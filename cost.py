"""
cost.py — API 成本追蹤（v1.5）
=================================

追蹤每個 API 呼叫的成本（LLM、Web Search），並可設定月預算上限。

費用估算（2026-06）：
- Tavily: $0.001 per search（超過 1000/月付費）
- Firecrawl: $0.0006 per page
- MiniMax M2.7: $0.0001 per 1K input tokens, $0.0003 per 1K output（估算）
  假設平均一次 judge 500 input + 200 output = $0.00011

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
from typing import Dict, Optional

from persistence import _get_db_path, SCHEMA_SQL

log = logging.getLogger("dynamic_harness.cost")

_lock = threading.Lock()


# === Schema ===

COST_SCHEMA = """
CREATE TABLE IF NOT EXISTS cost_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    month TEXT NOT NULL,            -- "2026-06" 便於月聚合
    service TEXT NOT NULL,          -- "minimax" | "tavily" | "firecrawl"
    cost_usd REAL NOT NULL,
    operation TEXT,                 -- "llm_judge" | "llm_split" | "web_search"
    extra TEXT
);

CREATE INDEX IF NOT EXISTS idx_cost_month ON cost_log(month);
CREATE INDEX IF NOT EXISTS idx_cost_service ON cost_log(service);
"""

if "cost_log" not in SCHEMA_SQL:
    SCHEMA_SQL = SCHEMA_SQL + COST_SCHEMA


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


# === 費用表（USD per call，粗略估算）===

# 來源：各 provider 公開 pricing（2026-06）
COST_TABLE = {
    "minimax": {
        # 假設 500 input + 200 output tokens/次
        "llm_judge": 0.00011,
        "llm_split": 0.00011,
    },
    "tavily": {
        "web_search": 0.001,
    },
    "firecrawl": {
        "web_search": 0.0006,
    },
}


def estimate_cost(service: str, operation: str) -> float:
    """估算單次操作的成本（USD）"""
    return COST_TABLE.get(service, {}).get(operation, 0.0)


# === 記錄 ===

def record_cost(
    service: str,
    operation: str,
    cost_usd: Optional[float] = None,
    extra: Optional[dict] = None,
    db_path: Optional[Path] = None,
) -> float:
    """記錄一筆成本

    Args:
        service: "minimax" | "tavily" | "firecrawl"
        operation: "llm_judge" | "llm_split" | "web_search"
        cost_usd: 自訂成本（None = 從 COST_TABLE 估算）
        extra: 額外資訊

    Returns:
        記錄的成本（USD）
    """
    if cost_usd is None:
        cost_usd = estimate_cost(service, operation)
    
    # 從 timestamp 取 YYYY-MM
    ts = time.time()
    month = time.strftime("%Y-%m", time.localtime(ts))
    
    with _lock:
        try:
            with _connect(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO cost_log (ts, month, service, cost_usd, operation, extra)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts,
                        month,
                        service,
                        cost_usd,
                        operation,
                        json.dumps(extra) if extra else None,
                    ),
                )
                conn.commit()
        except Exception as e:
            log.debug(f"cost.record failed (non-fatal): {e}")
    
    return cost_usd


# === 查詢 ===

def get_cost_summary(
    month: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> dict:
    """取得成本摘要

    Args:
        month: "2026-06"，None = 本月
        db_path: 自訂資料庫

    Returns:
        {
            "month": str,
            "total_usd": float,
            "by_service": {service: float},
            "by_operation": {operation: float},
            "call_count": int,
        }
    """
    if month is None:
        month = time.strftime("%Y-%m", time.localtime())
    
    with _connect(db_path) as conn:
        # 總計
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) FROM cost_log WHERE month = ?",
            (month,),
        ).fetchone()
        total, count = row[0], row[1]
        
        # by service
        svc_rows = conn.execute(
            "SELECT service, SUM(cost_usd) FROM cost_log WHERE month = ? GROUP BY service",
            (month,),
        ).fetchall()
        by_service = {s: round(c, 6) for s, c in svc_rows}
        
        # by operation
        op_rows = conn.execute(
            "SELECT operation, SUM(cost_usd) FROM cost_log WHERE month = ? GROUP BY operation",
            (month,),
        ).fetchall()
        by_operation = {o: round(c, 6) for o, c in op_rows}
    
    return {
        "month": month,
        "total_usd": round(total, 6),
        "call_count": count,
        "by_service": by_service,
        "by_operation": by_operation,
    }


def check_budget(
    budget_usd: float = 5.0,
    month: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> dict:
    """檢查月預算

    Returns:
        {
            "month": str,
            "budget_usd": float,
            "spent_usd": float,
            "remaining_usd": float,
            "used_pct": float (0-100),
            "warning": bool,
            "warning_level": "ok" | "warning" | "exceeded",
        }
    """
    summary = get_cost_summary(month=month, db_path=db_path)
    spent = summary["total_usd"]
    remaining = budget_usd - spent
    used_pct = (spent / budget_usd * 100) if budget_usd > 0 else 0
    
    if used_pct >= 100:
        level = "exceeded"
        warning = True
    elif used_pct >= 80:
        level = "warning"
        warning = True
    else:
        level = "ok"
        warning = False
    
    return {
        **summary,
        "budget_usd": budget_usd,
        "remaining_usd": round(remaining, 6),
        "used_pct": round(used_pct, 2),
        "warning": warning,
        "warning_level": level,
    }


# === CLI 輸出輔助 ===

def format_warning(budget_result: dict) -> str:
    """格式化預算警告訊息"""
    level = budget_result["warning_level"]
    spent = budget_result["total_usd"]
    budget = budget_result["budget_usd"]
    pct = budget_result["used_pct"]
    if level == "ok":
        return f"✅ 預算正常：{pct:.1f}% ({spent:.4f}/{budget} USD)"
    elif level == "warning":
        return f"⚠️ 預算警告：{pct:.1f}% ({spent:.4f}/{budget} USD)"
    else:
        return f"❌ 預算超支：{pct:.1f}% ({spent:.4f}/{budget} USD)"
