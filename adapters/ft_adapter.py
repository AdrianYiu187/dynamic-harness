"""
ft_adapter.py — 委派給 FT_Team_Agent task_router_v2.py
======================================================

目標：完全不改動 task_router_v2.py（8,119 行），
      只在頂層包裝為 RouteEnvelope。

策略：
- 用 importlib.util 動態載入 task_router_v2.py
- 從 DynamicTaskRouter 拿 routing 結果
- 包成 RouteEnvelope
- 失敗時 fallback 到關鍵字判斷 + minimal envelope

日期：2026-06-05
"""
from __future__ import annotations
import importlib.util
import logging
import re
import sys
from pathlib import Path
from typing import List

_HERE = Path(__file__).resolve().parent
_DH_ROOT = _HERE.parent
sys.path.insert(0, str(_DH_ROOT))

from schemas import (
    Domain, RouteEnvelope, CapabilityRef, WorkflowStep,
    DOMAIN_KEYWORDS,
)

log = logging.getLogger("dynamic_harness.ft")

FT_ROUTER_PATH = Path.home() / ".hermes" / "skills" / "productivity" / "ft-team-agent" / "scripts" / "task_router_v2.py"


class FTAdapter:
    """委派給 FT_Team_Agent DynamicTaskRouter"""
    domain = Domain.FT
    
    # FT 專屬關鍵字（比通用 DOMAIN_KEYWORDS 更精準）
    FT_FOOTBALL_KEYWORDS = DOMAIN_KEYWORDS[Domain.FT.value] + [
        "曼聯", "車路士", "利物浦", "曼城", "阿仙奴", "熱刺",
        "皇馬", "巴塞", "馬德里", "拜仁", "祖雲達斯",
        "AC米蘭", "國際米蘭", "巴黎", "多蒙特",
    ]
    
    def can_handle(self, task_text: str) -> float:
        text_lower = task_text.lower()
        hits = sum(1 for kw in self.FT_FOOTBALL_KEYWORDS if kw.lower() in text_lower)
        if hits == 0:
            return 0.0
        return min(hits / 3.0, 1.0)
    
    def route(self, task_text: str) -> RouteEnvelope:
        # 嘗試 import 真正的 FT router
        try:
            router_instance = self._load_ft_router()
            if router_instance is None:
                return self._fallback_route(task_text, error="FT router failed to load")
            
            # 呼叫 DynamicTaskRouter.analyze (FT v2 公開介面)
            # 其他公開方法: execute(analysis), generate_reports(result), run(task, send_telegram)
            # stdout suppression 在 unified_router 層做
            raw = router_instance.analyze(task_text)
            return self._wrap_envelope(task_text, raw)
        
        except Exception as e:
            log.exception("FT adapter failed")
            return self._fallback_route(task_text, error=f"{type(e).__name__}: {e}")
    
    def _load_ft_router(self):
        """動態載入 FT task_router_v2.py"""
        if not FT_ROUTER_PATH.exists():
            log.warning(f"FT router not found at {FT_ROUTER_PATH}")
            return None
        
        try:
            spec = importlib.util.spec_from_file_location(
                "ft_task_router_v2", str(FT_ROUTER_PATH)
            )
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules["ft_task_router_v2"] = module
            spec.loader.exec_module(module)
            
            # 找 DynamicTaskRouter class
            router_class = getattr(module, "DynamicTaskRouter", None)
            if router_class is None:
                log.warning("DynamicTaskRouter class not found in task_router_v2.py")
                return None
            
            return router_class()
        except Exception as e:
            log.warning(f"Failed to import FT router: {e}")
            return None
    
    def _wrap_envelope(self, task_text: str, raw: dict) -> RouteEnvelope:
        """包裝 FT 結果為 RouteEnvelope"""
        capabilities: List[CapabilityRef] = []
        workflow: List[WorkflowStep] = []
        
        # FT v2 結果通常是 dict，可能含 'cap_ids' / 'workflow' / 'task_type'
        # 結構範例：
        #   capabilities: [(id, weight, reason), ...]  ← tuple 列表
        #   cap_ids: [str, ...]                         ← 純 ID 列表
        #   workflow: [str, ...]                        ← "E50: 球隊狀態分析" 格式
        if isinstance(raw, dict):
            # 先用 cap_ids（純字串）
            for cap_id in raw.get("cap_ids", []) or []:
                if isinstance(cap_id, str):
                    capabilities.append(CapabilityRef(
                        id=cap_id,
                        name=cap_id,
                        domain=self.domain.value,
                    ))
            # 若無 cap_ids，從 capabilities tuples 提取
            if not capabilities:
                for item in raw.get("capabilities", []) or []:
                    if isinstance(item, (list, tuple)) and len(item) >= 1:
                        cap_id = str(item[0])
                        capabilities.append(CapabilityRef(
                            id=cap_id,
                            name=cap_id,
                            domain=self.domain.value,
                            confidence=float(item[1]) if len(item) >= 2 and isinstance(item[1], (int, float)) else 1.0,
                        ))
            
            for idx, step in enumerate(raw.get("workflow", []) or []):
                if isinstance(step, str):
                    # FT 格式："E50: 球隊狀態分析" — 拆出 ID
                    parts = step.split(":", 1)
                    cap_id = parts[0].strip() if parts else step
                    workflow.append(WorkflowStep(
                        step=idx + 1,
                        action=step,
                        role=cap_id,
                        domain=self.domain.value,
                    ))
                elif isinstance(step, dict):
                    workflow.append(WorkflowStep(
                        step=idx + 1,
                        action=step.get("action", str(step)),
                        role=step.get("role", ""),
                        domain=self.domain.value,
                        raw=step,
                    ))
        
        return RouteEnvelope(
            task_text=task_text,
            detected_domain=self.domain.value,
            domain_confidence=self.can_handle(task_text),
            capabilities=capabilities,
            workflow=workflow,
            raw_result=raw,
            adapter_used="FTAdapter",
        )
    
    def _fallback_route(self, task_text: str, error: str = None) -> RouteEnvelope:
        """當 FT router 不可用時的 fallback — 用 regex 識別最少資訊"""
        # 識別隊伍（簡單中英匹配）
        teams = re.findall(
            r'(曼聯|車路士|利物浦|曼城|阿仙奴|熱刺|皇馬|巴塞|拜仁|祖雲達斯|'
            r'AC米蘭|巴黎|多蒙特|曼切斯特|切爾西|阿森納|托特納姆|皇家馬德里|'
            r'巴塞隆納|尤文圖斯|多特蒙德|Man United|Chelsea|Liverpool|'
            r'Man City|Arsenal|Tottenham|Real Madrid|Barcelona|Bayern|Juventus)',
            task_text, re.IGNORECASE
        )
        
        return RouteEnvelope(
            task_text=task_text,
            detected_domain=self.domain.value,
            domain_confidence=self.can_handle(task_text),
            capabilities=[CapabilityRef(id="E50", name="球隊狀態分析師", domain=self.domain.value)],
            workflow=[
                WorkflowStep(
                    step=1, action="識別球隊", role="E50",
                    domain=self.domain.value,
                    raw={"teams_detected": list(set(teams))}
                )
            ],
            raw_result={"teams_detected": list(set(teams)), "mode": "fallback"},
            adapter_used="FTAdapter",
            error=error,
        )
