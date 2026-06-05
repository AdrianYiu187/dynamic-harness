"""
llm_judge.py — LLM 二次判斷（信心度低時使用）
================================================

用途：當 regex 判斷信心度 < 0.5 時，呼叫 MiniMax M2.7 確認 domain。
理由：M2.7 對於「模糊任務描述」的判斷比純 regex 強。
失敗時：fallback 到原 regex 結果，不阻斷流程。

環境要求：
- 環境變數 MINIMAX_API_KEY（必填）
- 環境變數 MINIMAX_BASE_URL（選填，預設 https://api.minimax.io/v1）

日期：2026-06-05
"""
from __future__ import annotations
import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

from schemas import Domain

log = logging.getLogger("dynamic_harness.llm_judge")


# 從 ~/.hermes/.env 讀 MiniMax 配置
def _load_env() -> None:
    """載入 .env 中的 MiniMax 配置"""
    env_path = Path.home() / ".hermes" / ".env"
    if not env_path.exists():
        return
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    if k.startswith("MINIMAX_") or k == "MINIMAX_API_KEY":
                        os.environ.setdefault(k, v)
    except Exception as e:
        log.debug(f"Failed to load .env: {e}")


_load_env()


# === Prompt 模板 ===

_JUDGE_SYSTEM = """你是任務路由助手。給定一段用戶任務描述，判斷它屬於哪個領域。

領域列表（嚴格遵守，不要新增）：
- ft: 足球博彩、赔率、盤口、賽事分析
- stock: 股票、股市、財報、估值、技術分析
- coding: 編程、網頁開發、API、代碼審計、bug 修復
- hermes: 通用研究、學術論文、數據分析、機器學習
- general: 無法判斷或跨領域

輸出格式（嚴格遵守）：只回傳一個英文領域名稱，無其他文字、不要解釋。"""


def _build_user_prompt(task_text: str) -> str:
    return f"任務：{task_text}\n\n領域："


# === LLM 呼叫 ===

def call_minimax(
    messages: list,
    model: str = "MiniMax-M2.7-highspeed",
    max_tokens: int = 20,
    timeout: int = 15,
) -> Optional[str]:
    """呼叫 MiniMax M2.7 chat completion

    Returns:
        assistant 回覆文字；失敗時回傳 None
    """
    api_key = os.environ.get("MINIMAX_API_KEY", "")
    base_url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.io/v1").rstrip("/")

    if not api_key:
        log.warning("MINIMAX_API_KEY not set, LLM judge disabled")
        return None

    url = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.0,
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
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return content.strip() if content else None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        log.warning(f"MiniMax HTTP {e.code}: {body}")
        return None
    except urllib.error.URLError as e:
        log.warning(f"MiniMax URL error: {e}")
        return None
    except Exception as e:
        log.warning(f"MiniMax call failed: {type(e).__name__}: {e}")
        return None


# === 二次判斷主邏輯 ===

# 信心度閾值（低於此值就 call LLM）
LLM_CONFIDENCE_THRESHOLD = 0.5


def llm_judge_domain(task_text: str) -> Optional[Tuple[Domain, float]]:
    """用 LLM 判斷 task 的 domain

    Returns:
        (Domain, confidence) — confidence 固定 0.8（標記是 LLM 判斷的）
        失敗時回傳 None
    """
    if not task_text or not task_text.strip():
        return None

    messages = [
        {"role": "system", "content": _JUDGE_SYSTEM},
        {"role": "user", "content": _build_user_prompt(task_text)},
    ]
    # M2.7 會先出 <think> 區塊再給答案，max_tokens 需要給到 500
    raw = call_minimax(messages, max_tokens=500)
    if raw is None:
        return None

    # 解析 LLM 回覆（剝離 <think>...</think> 區塊）
    raw_clean = _strip_thinking(raw)
    raw_lower = raw_clean.lower().strip().rstrip(".,!?;:")
    
    # 嘗試匹配
    for domain in Domain:
        if domain.value in raw_lower or domain.name.lower() in raw_lower:
            return (domain, 0.8)  # LLM 判斷的信心度標記 0.8

    log.warning(f"LLM returned unrecognized domain: {raw_clean!r}")
    return None


def _strip_thinking(text: str) -> str:
    """剝離 M2.7 的 <think>...</think> 區塊"""
    import re
    # 移除所有 <think>...</think> 區塊
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    return text.strip()


# === 多任務拆分（給 multi_route 用）===

_SPLIT_SYSTEM = """你是任務拆分助手。給定一段用戶輸入，如果它包含多個獨立的子任務，
把它拆成多個獨立任務；如果是單一任務，回傳原任務。

輸出格式（嚴格遵守 JSON 數組，不要其他文字）：
- 單一任務：["原任務文字"]
- 多任務：["子任務1", "子任務2", ...]

每個子任務應保持原語言、保留關鍵實體（股票代碼、隊伍名、檔名等）。"""


def llm_split_tasks(task_text: str, max_subtasks: int = 5) -> Optional[list[str]]:
    """用 LLM 把一句話拆成多個子任務

    Returns:
        子任務列表；失敗時回傳 None
    """
    if not task_text or not task_text.strip():
        return None

    messages = [
        {"role": "system", "content": _SPLIT_SYSTEM},
        {"role": "user", "content": f"輸入：{task_text}\n\nJSON 數組："},
    ]
    # M2.7 會先出 <think> 區塊再給答案，max_tokens 需要給到 500
    raw = call_minimax(messages, max_tokens=500)
    if raw is None:
        return None

    # 剝離 <think>...</think> 區塊
    raw = _strip_thinking(raw)
    raw = raw.strip()
    
    # 嘗試解析 JSON
    # 找到 [ 和 ] 之間的內容
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1:
        log.warning(f"LLM split returned non-JSON: {raw!r}")
        return None

    json_str = raw[start:end+1]
    try:
        result = json.loads(json_str)
        if isinstance(result, list) and all(isinstance(x, str) for x in result):
            return result[:max_subtasks]
        return None
    except json.JSONDecodeError as e:
        log.warning(f"LLM split JSON parse failed: {e} in {json_str!r}")
        return None
