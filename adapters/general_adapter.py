"""
general_adapter.py — 通用 fallback adapter（v1.3 with Web Search）
====================================================================

處理 LLM 或 regex 判為 "general" 的任務。
策略：先判斷是否需要即時資料，是的話先 web_search，再用 LLM 整合回答。

v1.3 增強：
- 接 Tavily / Firecrawl 取即時資料
- 對「今天天氣」「最新新聞」「現在股價」這類問題給出真實答案
- 無 API key 時退回 LLM 純知識回答

日期：2026-06-05
"""
from __future__ import annotations
import logging
import sys
from pathlib import Path
from typing import List

_HERE = Path(__file__).resolve().parent
_DH_ROOT = _HERE.parent
sys.path.insert(0, str(_DH_ROOT))

from schemas import Domain, RouteEnvelope, CapabilityRef, WorkflowStep
from llm_judge import call_minimax, _strip_thinking
from web_search import web_search, format_results_for_llm, needs_real_time_data

log = logging.getLogger("dynamic_harness.general")


# === System prompts ===

_GENERAL_SYSTEM = """你是一個誠實的助手。給定用戶的問題，給出簡潔、準確的回答。
規則：
1. 如果搜尋結果中有明確答案，引用並整理
2. 如果沒有搜尋結果且你真的知道，給出簡短回答（2-5 句）
3. 如果你不確定，明確說「我不確定」並建議用戶查證
4. 不要假裝是專業領域的專家
5. 用用戶使用的語言回答（中文問題用中文，英文問題用英文）"""

_GENERAL_OFFLINE_SYSTEM = """你是一個誠實的助手。給定用戶的問題，給出簡潔、準確的回答。
規則：
1. 如果你真的知道答案，給出簡短回答（2-5 句）
2. 如果你不確定，明確說「我不確定」並建議用戶查證
3. 不要假裝是專業領域的專家
4. 用用戶使用的語言回答（中文問題用中文，英文問題用英文）
5. 你的知識有截止日期，無法取得即時資訊"""


def _ask_general_llm(task_text: str, search_context: str = "") -> str:
    """用 LLM 回答 general 任務（可選 search context）"""
    system = _GENERAL_SYSTEM if search_context else _GENERAL_OFFLINE_SYSTEM
    
    user_msg = task_text
    if search_context:
        user_msg = f"{task_text}\n\n【搜尋結果】\n{search_context}\n\n請整合搜尋結果回答。"
    
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]
    raw = call_minimax(messages, max_tokens=500)
    if raw is None:
        return None  # type: ignore
    return _strip_thinking(raw).strip()


class GeneralAdapter:
    """通用 fallback adapter — 處理 LLM 判為 general 的任務

    v1.3 流程：
    1. 判斷是否需要即時資料（needs_real_time_data）
    2. 是的話 → 嘗試 web_search (Tavily/Firecrawl)
    3. 把搜尋結果 + 用戶問題一起丟給 LLM 整合
    4. 若無 web search → 直接 LLM 回答
    """
    domain = Domain.GENERAL
    
    def can_handle(self, task_text: str) -> float:
        return 1.0
    
    def route(self, task_text: str) -> RouteEnvelope:
        try:
            search_used = False
            search_context = ""
            
            # 1. 判斷是否需要即時資料
            if needs_real_time_data(task_text):
                log.info(f"Task needs real-time data, attempting web search: {task_text[:60]}")
                results = web_search(task_text, prefer="tavily", max_results=3)
                if results:
                    search_used = True
                    search_context = format_results_for_llm(results, max_len=2000)
                    # 記錄 metrics + cost（v1.5+）
                    try:
                        import sys as _sys
                        _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
                        import metrics, cost
                        # 判斷實際使用的 backend（tavily / firecrawl）
                        backend = "tavily" if any("tavily" in str(r.get("url", "")).lower() or r.get("_is_summary") for r in results) else "firecrawl"
                        # 簡化：先看 results 內容判斷，預設 tavily
                        backend = "tavily"
                        metrics.record(
                            metric_type="web_search",
                            metric_key=backend,
                            success=True,
                            extra={"results_count": len(results)},
                        )
                        cost.record_cost(backend, "web_search")
                    except Exception:
                        pass
            
            # 2. 調 LLM 回答
            answer = _ask_general_llm(task_text, search_context)
            
            if answer is None:
                # LLM 不可用
                answer = self._fallback_message(task_text, search_used)
            
            return RouteEnvelope(
                task_text=task_text,
                detected_domain=self.domain.value,
                domain_confidence=0.5,
                capabilities=[
                    CapabilityRef(id="general_chat", name="通用對話", domain=self.domain.value),
                ],
                workflow=[
                    WorkflowStep(
                        step=1,
                        action="web_search" if search_used else "llm_only",
                        role="general_chat",
                        domain=self.domain.value,
                        raw={"search_used": search_used, "results_count": len(search_context.split("[摘要]")) - 1 + len(search_context.split("\n[")) - 1 if search_context else 0},
                    ),
                ],
                raw_result={
                    "answer": answer,
                    "method": "llm_with_search" if search_used else "llm_only",
                    "search_used": search_used,
                },
                adapter_used="GeneralAdapter",
            )
        except Exception as e:
            log.exception("GeneralAdapter failed")
            return RouteEnvelope(
                task_text=task_text,
                detected_domain=self.domain.value,
                domain_confidence=0.0,
                capabilities=[],
                workflow=[],
                raw_result=None,
                adapter_used="GeneralAdapter",
                error=f"{type(e).__name__}: {e}",
            )
    
    def _fallback_message(self, task_text: str, search_used: bool) -> str:
        """LLM 不可用時的 fallback"""
        if search_used:
            return f"已嘗試為你搜尋「{task_text}」的即時資料，但 LLM 服務暫時不可用整合搜尋結果。\n建議：直接查看搜尋結果。"
        return (
            f"無法判斷此任務屬於哪個專業領域，且 LLM 服務暫時不可用。建議：\n"
            f"1. 重新描述任務（加入領域關鍵字）\n"
            f"2. 或用 --force-domain 指定領域"
        )
