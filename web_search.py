"""
web_search.py — 統一 web search 介面（支援 Tavily + Firecrawl）
================================================================

Tavily：免費額度 1000 次/月，速度快，適合搜尋摘要
Firecrawl：爬取網頁內容，適合需要全文的任務

兩個都讀 ~/.hermes/.env：
- TAVILY_API_KEY
- FIRECRAWL_API_KEY

日期：2026-06-05
"""
from __future__ import annotations
import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

_HERE = Path(__file__).resolve().parent


# === 載入 .env（修新版：strip \n）===

def _load_env_once() -> None:
    """從 ~/.hermes/.env 載入 API keys（每個 session 一次）"""
    global _env_loaded
    if _env_loaded:
        return
    
    env_path = Path.home() / ".hermes" / ".env"
    if env_path.exists():
        try:
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        if k.startswith("TAVILY") or k.startswith("FIRECRAWL"):
                            os.environ[k] = v.strip()  # 確保去除 \n
        except Exception as e:
            log.debug(f"Failed to load .env: {e}")
    
    _env_loaded = True


_env_loaded = False

log = logging.getLogger("dynamic_harness.web_search")


# === 觸發判斷（哪些任務需要即時查）===

REAL_TIME_KEYWORDS = [
    # 中文
    "今天", "最新", "現在", "剛才", "昨天", "明天", "這週", "這周",
    "本週", "本月", "今年", "新聞", "股價", "匯率", "天氣", "比分",
    "實時", "即时", "即时", "當前", "目前",
    # 英文
    "today", "latest", "now", "current", "recent", "news", "weather",
    "stock price", "live", "right now", "this week", "this month",
]

import re
# 數字模式（如日期、價位）
NUMERIC_PATTERNS = re.compile(
    r'\b(20\d{2}[-/年]\d{1,2}[-/月]?\d{0,2})\b'  # 2024-01-15
    r'|\b(0[0-9]{4})\b'  # HK stock code 01810
    r'|\b([A-Z]{2,5})\b'  # ticker TSLA, AAPL
)


def needs_real_time_data(task_text: str) -> bool:
    """判斷任務是否需要即時資料"""
    text_lower = task_text.lower()
    
    # 關鍵字觸發
    for kw in REAL_TIME_KEYWORDS:
        if kw in text_lower:
            return True
    
    # 數字模式觸發（日期/股票代碼/股票代號）
    if NUMERIC_PATTERNS.search(task_text):
        # 但要避免數學計算場景
        if any(c in task_text for c in ["計算", "等於", "等於多少", "等於?", "=", "+", "×"]):
            return False
        return True
    
    return False


# === Tavily ===

def tavily_search(query: str, max_results: int = 3, timeout: int = 10) -> Optional[List[Dict]]:
    """用 Tavily 搜尋

    Returns:
        List of {title, url, content} 或 None（失敗時）
    """
    _load_env_once()
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        return None
    
    url = "https://api.tavily.com/search"
    payload = {
        "query": query,
        "max_results": max_results,
        "include_answer": True,  # 直接給 LLM 摘要
        "search_depth": "basic",
    }
    
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            results = data.get("results", [])
            answer = data.get("answer", "")
            # 整合 answer + 結果
            if answer:
                results.insert(0, {
                    "title": "[Tavily 摘要]",
                    "url": "",
                    "content": answer,
                    "_is_summary": True,
                })
            return results
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        log.warning(f"Tavily HTTP {e.code}: {body}")
        return None
    except Exception as e:
        log.warning(f"Tavily error: {type(e).__name__}: {e}")
        return None


# === Firecrawl ===

def firecrawl_search(query: str, max_results: int = 2, timeout: int = 15) -> Optional[List[Dict]]:
    """用 Firecrawl 搜尋（v1 API）

    Returns:
        List of {title, url, content} 或 None（失敗時）
    """
    _load_env_once()
    api_key = os.environ.get("FIRECRAWL_API_KEY", "")
    if not api_key or "your-" in api_key or len(api_key) < 10:
        return None
    
    url = "https://api.firecrawl.dev/v1/search"
    payload = {
        "query": query,
        "limit": max_results,
    }
    
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            # Firecrawl 回傳格式
            data_items = data.get("data", []) or data.get("results", [])
            results = []
            for item in data_items[:max_results]:
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "content": item.get("description", item.get("markdown", ""))[:500],
                })
            return results
    except Exception as e:
        log.warning(f"Firecrawl error: {type(e).__name__}: {e}")
        return None


# === 統一搜尋（自動選擇 backend）===

def web_search(query: str, prefer: str = "tavily", max_results: int = 3) -> List[Dict]:
    """統一 web search 入口

    Args:
        query: 搜尋查詢
        prefer: 偏好 backend（"tavily" | "firecrawl"）
        max_results: 最多回傳結果數

    Returns:
        結果列表（可能為空）
    """
    results: List[Dict] = []
    
    if prefer == "tavily":
        tavily_result = tavily_search(query, max_results=max_results)
        if tavily_result:
            results.extend(tavily_result)
        # Tavily 失敗 → 試 Firecrawl
        if not results:
            firecrawl_result = firecrawl_search(query, max_results=max_results)
            if firecrawl_result:
                results.extend(firecrawl_result)
    else:
        # 偏好 Firecrawl
        firecrawl_result = firecrawl_search(query, max_results=max_results)
        if firecrawl_result:
            results.extend(firecrawl_result)
        if not results:
            tavily_result = tavily_search(query, max_results=max_results)
            if tavily_result:
                results.extend(tavily_result)
    
    return results


# === Format 結果供 LLM 使用 ===

def format_results_for_llm(results: List[Dict], max_len: int = 2000) -> str:
    """把搜尋結果格式化成 LLM prompt 友善的字串"""
    if not results:
        return "（無搜尋結果）"
    
    parts = []
    total_len = 0
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        url = r.get("url", "")
        content = r.get("content", "")
        if r.get("_is_summary"):
            # Tavily 摘要
            piece = f"[摘要]\n{content}\n"
        else:
            piece = f"[{i}] {title}\nURL: {url}\n{content}\n"
        
        if total_len + len(piece) > max_len:
            parts.append(f"...（還有 {len(results) - i + 1} 筆結果省略）")
            break
        parts.append(piece)
        total_len += len(piece)
    
    return "\n".join(parts)
