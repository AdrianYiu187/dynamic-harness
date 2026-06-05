"""
metrics.py — 觀察性 metrics（v1.5）
====================================

追蹤：
- 各 adapter 的 call 數、成功率、平均延遲
- LLM judge 觸發次數 / 命中率
- Cache hit rate
- Web search 觸發次數

存儲：SQLite（envelope_cache schema 共用）
時間範圍：可按日/月聚合查詢

日期：2026-06-05
"""
from __future__ import annotations
import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

from persistence import _get_db_path, SCHEMA_SQL

log = logging.getLogger("dynamic_harness.metrics")

_lock = threading.Lock()


# === Schema ===

METRICS_SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    metric_type TEXT NOT NULL,    -- "adapter_call" | "llm_judge" | "cache_hit" | "cache_miss" | "web_search"
    metric_key TEXT NOT NULL,      -- e.g. "FTAdapter" | "true" | "tavily"
    latency_ms REAL,                -- 延遲（ms），可選
    success INTEGER DEFAULT 1,      -- 1=成功 0=失敗
    extra TEXT                      -- JSON 額外資訊
);

CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(ts);
CREATE INDEX IF NOT EXISTS idx_metrics_type ON metrics(metric_type);
CREATE INDEX IF NOT EXISTS idx_metrics_key ON metrics(metric_key);
"""

# 把 METRICS_SCHEMA 注入 SCHEMA_SQL（避免重複定義）
if "metrics" not in SCHEMA_SQL:
    SCHEMA_SQL = SCHEMA_SQL + METRICS_SCHEMA


@contextmanager
def _connect(db_path: Optional[Path] = None):
    path = _get_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        yield conn
    finally:
        conn.close()


# === 記錄 ===

def record(
    metric_type: str,
    metric_key: str,
    latency_ms: Optional[float] = None,
    success: bool = True,
    extra: Optional[dict] = None,
    db_path: Optional[Path] = None,
) -> None:
    """記錄一筆 metric（thread-safe）

    Args:
        metric_type: 類型 ("adapter_call" | "llm_judge" | "cache_hit" | "cache_miss" | "web_search")
        metric_key: 識別（adapter name / 結果 / backend name）
        latency_ms: 延遲（毫秒）
        success: 成功與否
        extra: 額外資訊（會序列化成 JSON）
    """
    with _lock:
        try:
            with _connect(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO metrics (ts, metric_type, metric_key, latency_ms, success, extra)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        time.time(),
                        metric_type,
                        metric_key,
                        latency_ms,
                        1 if success else 0,
                        json.dumps(extra) if extra else None,
                    ),
                )
                conn.commit()
        except Exception as e:
            log.debug(f"metrics.record failed (non-fatal): {e}")


# === 查詢 ===

def get_summary(
    since_ts: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> dict:
    """取得整體 metrics 摘要

    Args:
        since_ts: 只統計此時間戳之後的記錄（None = 全部）
        db_path: 自訂資料庫路徑

    Returns:
        {
            "total_calls": int,
            "success_rate": float (0-1),
            "by_adapter": {name: {calls, success, avg_latency_ms}},
            "llm_judge": {triggered, hit, trigger_rate},
            "cache": {hits, misses, hit_rate},
            "web_search": {calls, by_backend},
        }
    """
    where = "WHERE 1=1"
    params: list = []
    if since_ts is not None:
        where += " AND ts >= ?"
        params.append(since_ts)
    
    with _connect(db_path) as conn:
        # 總量 + 成功率
        row = conn.execute(
            f"SELECT COUNT(*) as total, COALESCE(SUM(success), 0) as ok FROM metrics {where}",
            params,
        ).fetchone()
        total, ok = row[0], row[1]
        success_rate = ok / total if total > 0 else 0.0
        
        # 依 adapter 分組
        adapter_rows = conn.execute(
            f"""
            SELECT metric_key, COUNT(*) as calls,
                   COALESCE(AVG(latency_ms), 0) as avg_lat,
                   COALESCE(SUM(success), 0) as ok
            FROM metrics
            {where} AND metric_type = 'adapter_call'
            GROUP BY metric_key
            ORDER BY calls DESC
            """,
            params,
        ).fetchall()
        
        by_adapter = {}
        for name, calls, avg_lat, ok_count in adapter_rows:
            by_adapter[name] = {
                "calls": calls,
                "success": ok_count,
                "success_rate": ok_count / calls if calls > 0 else 0,
                "avg_latency_ms": round(avg_lat, 2),
            }
        
        # LLM judge
        llm_rows = conn.execute(
            f"""
            SELECT metric_key, COUNT(*) as n
            FROM metrics {where} AND metric_type = 'llm_judge'
            GROUP BY metric_key
            """,
            params,
        ).fetchall()
        llm_judge = {k: n for k, n in llm_rows}
        
        # Cache
        cache_rows = conn.execute(
            f"""
            SELECT metric_key, COUNT(*) as n
            FROM metrics {where} AND metric_type IN ('cache_hit', 'cache_miss')
            GROUP BY metric_key
            """,
            params,
        ).fetchall()
        cache_hits = 0
        cache_misses = 0
        for k, n in cache_rows:
            if k == "hit":
                cache_hits = n
            elif k == "miss":
                cache_misses = n
        cache_total = cache_hits + cache_misses
        cache = {
            "hit": cache_hits,
            "miss": cache_misses,
            "hit_rate": round(cache_hits / cache_total, 4) if cache_total > 0 else 0.0,
        }
        
        # Web search
        web_rows = conn.execute(
            f"""
            SELECT metric_key, COUNT(*) as n
            FROM metrics {where} AND metric_type = 'web_search'
            GROUP BY metric_key
            """,
            params,
        ).fetchall()
        web_search = {k: n for k, n in web_rows}
    
    return {
        "total_calls": total,
        "success_rate": round(success_rate, 4),
        "by_adapter": by_adapter,
        "llm_judge": llm_judge,
        "cache": cache,
        "web_search": web_search,
    }


def clear_metrics(
    before_ts: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> int:
    """清理 metrics（謹慎使用）

    Returns:
        刪除筆數
    """
    with _lock:
        with _connect(db_path) as conn:
            if before_ts is not None:
                cursor = conn.execute("DELETE FROM metrics WHERE ts < ?", (before_ts,))
            else:
                cursor = conn.execute("DELETE FROM metrics")
            conn.commit()
            return cursor.rowcount
