"""
persistence.py — Envelope SQLite 持久化
=========================================

用途：把 RouteEnvelope 存入 SQLite，便於 pipeline 追蹤、查詢歷史、除錯。

資料庫位置：~/.hermes/dynamic_harness_envelopes.db
- 跟隨 hermes_home 規範
- 單一檔案，方便備份
- 包含時間索引，便於範圍查詢

Schema：
    envelopes (
        id INTEGER PRIMARY KEY,
        ts REAL,                  -- Unix timestamp
        task_text TEXT,
        detected_domain TEXT,
        domain_confidence REAL,
        adapter_used TEXT,
        llm_judged INTEGER,       -- 0/1
        error TEXT,
        envelope_json TEXT,        -- 完整 to_dict() 結果
        capabilities_summary TEXT, -- "E50,E53,..." 摘要
        workflow_steps INTEGER
    )

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
from typing import List, Optional

_HERE = Path(__file__).resolve().parent
sys_path = str(_HERE)  # noqa: F841

log = logging.getLogger("dynamic_harness.persistence")

# 預設資料庫位置
DEFAULT_DB_PATH = Path.home() / ".hermes" / "dynamic_harness_envelopes.db"


# === Schema ===

# envelopes table (v1.0+)
ENVELOPES_SCHEMA = """
CREATE TABLE IF NOT EXISTS envelopes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    task_text TEXT NOT NULL,
    detected_domain TEXT,
    domain_confidence REAL,
    adapter_used TEXT,
    llm_judged INTEGER DEFAULT 0,
    error TEXT,
    envelope_json TEXT,
    capabilities_summary TEXT,
    workflow_steps INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_envelopes_ts ON envelopes(ts);
CREATE INDEX IF NOT EXISTS idx_envelopes_domain ON envelopes(detected_domain);
CREATE INDEX IF NOT EXISTS idx_envelopes_task ON envelopes(task_text);
"""

# envelope_cache table (v1.4+)
CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS envelope_cache (
    cache_key TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    task_text TEXT NOT NULL,
    force_domain TEXT,
    envelope_json TEXT NOT NULL,
    hit_count INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_cache_ts ON envelope_cache(ts);
"""

SCHEMA_SQL = ENVELOPES_SCHEMA + CACHE_SCHEMA


# === Plan-in-Code Schema（v2.0）===
PLAN_SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    task_text TEXT NOT NULL,
    force_domain TEXT,
    created_at REAL NOT NULL,
    script_source TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS plan_phases (
    plan_id TEXT NOT NULL,
    phase_id INTEGER NOT NULL,
    name TEXT,
    sub_task TEXT,
    force_domain TEXT,
    depends_on TEXT,
    parallel INTEGER DEFAULT 0,
    timeout_s INTEGER DEFAULT 60,
    stop_condition TEXT,
    status TEXT DEFAULT 'pending',
    envelope_id INTEGER,
    started_at REAL,
    completed_at REAL,
    error TEXT,
    PRIMARY KEY (plan_id, phase_id),
    FOREIGN KEY (plan_id) REFERENCES plans(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS plan_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT,
    phase_id INTEGER,
    ts REAL,
    event TEXT,
    message TEXT
);

CREATE INDEX IF NOT EXISTS idx_plans_status ON plans(status);
CREATE INDEX IF NOT EXISTS idx_plan_phases_plan ON plan_phases(plan_id);
CREATE INDEX IF NOT EXISTS idx_plan_traces_plan ON plan_traces(plan_id);
"""

if "plans" not in SCHEMA_SQL:
    SCHEMA_SQL = SCHEMA_SQL + PLAN_SCHEMA


def _get_db_path(custom_path: Optional[Path] = None) -> Path:
    if custom_path:
        return Path(custom_path)
    return DEFAULT_DB_PATH


@contextmanager
def _connect(db_path: Path = None, timeout: float = 5.0):
    """Context manager：自動 commit/close"""
    path = _get_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=timeout)
    try:
        # 確保 schema 存在
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        yield conn
    finally:
        conn.close()


# === 寫入 ===

def save_envelope(envelope, db_path: Optional[Path] = None) -> int:
    """儲存 envelope 到 SQLite，回傳新 row id

    Args:
        envelope: RouteEnvelope instance
        db_path: 自訂資料庫路徑（測試用）

    Returns:
        新插入的 row id
    """
    d = envelope.to_dict()
    
    # 判斷是否經過 LLM 二次判斷
    llm_judged = 0
    if isinstance(envelope.raw_result, dict):
        llm_judged = 1 if envelope.raw_result.get("_llm_judged") else 0
    
    # capabilities 摘要（e.g. "E50,E53,E54,..."）
    caps_summary = ",".join(c.id for c in envelope.capabilities[:20]) if envelope.capabilities else ""
    
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO envelopes (
                ts, task_text, detected_domain, domain_confidence,
                adapter_used, llm_judged, error, envelope_json,
                capabilities_summary, workflow_steps
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                envelope.task_text,
                envelope.detected_domain,
                envelope.domain_confidence,
                envelope.adapter_used,
                llm_judged,
                envelope.error,
                json.dumps(d, ensure_ascii=False),
                caps_summary,
                len(envelope.workflow),
            )
        )
        conn.commit()
        return cursor.lastrowid


# === 查詢 ===

def query_envelopes(
    domain: Optional[str] = None,
    task_pattern: Optional[str] = None,
    since_ts: Optional[float] = None,
    limit: int = 50,
    db_path: Optional[Path] = None,
) -> List[dict]:
    """查詢 envelope 歷史

    Args:
        domain: 過濾特定 domain
        task_pattern: task_text 模糊匹配
        since_ts: 只回傳此時間戳之後的記錄
        limit: 最多回傳幾筆
        db_path: 自訂資料庫路徑
    """
    sql = "SELECT * FROM envelopes WHERE 1=1"
    params: list = []
    
    if domain:
        sql += " AND detected_domain = ?"
        params.append(domain)
    
    if task_pattern:
        sql += " AND task_text LIKE ?"
        params.append(f"%{task_pattern}%")
    
    if since_ts is not None:
        sql += " AND ts >= ?"
        params.append(since_ts)
    
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    
    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]


def get_envelope(envelope_id: int, db_path: Optional[Path] = None) -> Optional[dict]:
    """取單筆 envelope"""
    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM envelopes WHERE id = ?", (envelope_id,)).fetchone()
        return dict(row) if row else None


def count_envelopes(db_path: Optional[Path] = None) -> dict:
    """統計資訊"""
    with _connect(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM envelopes").fetchone()[0]
        by_domain = conn.execute(
            "SELECT detected_domain, COUNT(*) as n FROM envelopes GROUP BY detected_domain"
        ).fetchall()
        recent = conn.execute(
            "SELECT MAX(ts) FROM envelopes"
        ).fetchone()[0]
        return {
            "total": total,
            "by_domain": {r[0]: r[1] for r in by_domain},
            "latest_ts": recent,
        }


def clear_envelopes(before_ts: Optional[float] = None, db_path: Optional[Path] = None) -> int:
    """清除 envelope（謹慎使用）

    Args:
        before_ts: 清除此時間戳之前的記錄
        db_path: 自訂資料庫路徑

    Returns:
        刪除的筆數
    """
    with _connect(db_path) as conn:
        if before_ts is not None:
            cursor = conn.execute("DELETE FROM envelopes WHERE ts < ?", (before_ts,))
        else:
            cursor = conn.execute("DELETE FROM envelopes")
        conn.commit()
        return cursor.rowcount


def cleanup_old_envelopes(retention_days: int = 30, db_path: Optional[Path] = None) -> int:
    """清理超過保留天數的 envelope

    Args:
        retention_days: 保留天數（預設 30 天）
        db_path: 自訂資料庫路徑

    Returns:
        刪除的筆數
    """
    if retention_days <= 0:
        raise ValueError("retention_days must be positive")
    
    cutoff_ts = time.time() - (retention_days * 86400)
    return clear_envelopes(before_ts=cutoff_ts, db_path=db_path)


def vacuum_database(db_path: Optional[Path] = None) -> None:
    """VACUUM 資料庫，回收空間"""
    with _connect(db_path) as conn:
        conn.execute("VACUUM")
        conn.commit()


# === Envelope Cache（v1.4）===

def _compute_cache_key(task_text: str, force_domain: Optional[str] = None) -> str:
    """計算 cache key（SHA-256）"""
    import hashlib
    raw = f"{task_text}|{force_domain or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def cache_get(
    task_text: str,
    force_domain: Optional[str] = None,
    ttl_seconds: int = 86400,
    db_path: Optional[Path] = None,
) -> Optional[dict]:
    """從 cache 取得 envelope dict

    Args:
        task_text: 任務文字
        force_domain: 強制指定的 domain（影響 cache key）
        ttl_seconds: TTL（預設 24 小時 = 86400s）
        db_path: 自訂資料庫路徑

    Returns:
        命中時回傳 dict（envelope.to_dict() 結果）；未命中或過期回傳 None
    """
    cache_key = _compute_cache_key(task_text, force_domain)
    
    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT ts, envelope_json FROM envelope_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        
        if row is None:
            return None
        
        # 檢查 TTL
        age = time.time() - row["ts"]
        if age > ttl_seconds:
            # 過期 — 刪除
            conn.execute("DELETE FROM envelope_cache WHERE cache_key = ?", (cache_key,))
            conn.commit()
            return None
        
        # 命中 — 增加 hit count
        conn.execute(
            "UPDATE envelope_cache SET hit_count = hit_count + 1 WHERE cache_key = ?",
            (cache_key,),
        )
        conn.commit()
        
        return json.loads(row["envelope_json"])


def cache_put(
    task_text: str,
    envelope,
    force_domain: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> str:
    """寫入 envelope 到 cache

    Returns:
        cache_key
    """
    cache_key = _compute_cache_key(task_text, force_domain)
    d = envelope.to_dict() if hasattr(envelope, "to_dict") else envelope
    
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO envelope_cache 
            (cache_key, ts, task_text, force_domain, envelope_json, hit_count)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (
                cache_key,
                time.time(),
                task_text,
                force_domain,
                json.dumps(d, ensure_ascii=False),
            ),
        )
        conn.commit()
    
    return cache_key


def cache_clear(
    before_ts: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> int:
    """清空 cache

    Returns:
        刪除筆數
    """
    with _connect(db_path) as conn:
        if before_ts is not None:
            cursor = conn.execute("DELETE FROM envelope_cache WHERE ts < ?", (before_ts,))
        else:
            cursor = conn.execute("DELETE FROM envelope_cache")
        conn.commit()
        return cursor.rowcount


def cache_stats(db_path: Optional[Path] = None) -> dict:
    """cache 統計資訊"""
    with _connect(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM envelope_cache").fetchone()[0]
        total_hits = conn.execute("SELECT COALESCE(SUM(hit_count), 0) FROM envelope_cache").fetchone()[0]
        if total > 0:
            latest_ts = conn.execute("SELECT MAX(ts) FROM envelope_cache").fetchone()[0]
            oldest_ts = conn.execute("SELECT MIN(ts) FROM envelope_cache").fetchone()[0]
            top_keys = conn.execute(
                "SELECT task_text, hit_count FROM envelope_cache ORDER BY hit_count DESC LIMIT 5"
            ).fetchall()
        else:
            latest_ts = oldest_ts = 0
            top_keys = []
        
        return {
            "total_entries": total,
            "total_hits": total_hits,
            "latest_ts": latest_ts,
            "oldest_ts": oldest_ts,
            "top_hits": [{"task_text": r[0][:60], "hits": r[1]} for r in top_keys],
        }


# === 確保 thread-safe ===

_lock = threading.Lock()


def save_envelope_safe(envelope, db_path: Optional[Path] = None) -> int:
    """thread-safe 版本（適用於並行 multi_route）"""
    with _lock:
        return save_envelope(envelope, db_path)
