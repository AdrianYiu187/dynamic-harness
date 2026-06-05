"""
hermes_team_adapter.py — 委派給 HermesTeamAgent 通用 task_router
================================================================

這個是兜底 adapter，涵蓋通用研究、論文分析、數據分析等
（非足球博彩、非股票、非純編碼的任務）。

注意：HermesTeamAgent 通用版的 task_router 內含 27 種體育博彩任務，
本 adapter 主要用於「體育/學術/通用」等任務。

日期：2026-06-05
"""
from __future__ import annotations
import importlib.util
import logging
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

log = logging.getLogger("dynamic_harness.hermes_team")

HERMES_TEAM_ROUTER_PATH = Path.home() / ".hermes" / "skills" / "productivity" / "hermes-team-agent" / "scripts" / "task_router.py"


class HermesTeamAdapter:
    domain = Domain.HERMES
    
    def can_handle(self, task_text: str) -> float:
        # Hermes 通用版是兜底，confidence 較低
        text_lower = task_text.lower()
        # 先看其他 domain 是否有更高信心
        for other_domain in [Domain.FT.value, Domain.STOCK.value, Domain.CODING.value]:
            for kw in DOMAIN_KEYWORDS[other_domain]:
                if kw.lower() in text_lower:
                    return 0.0  # 別跟專業 domain 搶
        
        hits = sum(1 for kw in DOMAIN_KEYWORDS[Domain.HERMES.value] if kw.lower() in text_lower)
        if hits == 0:
            return 0.1  # 通用 fallback
        return min(hits / 3.0, 0.7)  # 上限 0.7，避免搶專業任務
    
    def route(self, task_text: str) -> RouteEnvelope:
        try:
            router_instance = self._load_router()
            if router_instance is None:
                return self._fallback_route(task_text, error="HermesTeam router failed to load")
            
            task_type = None
            for method_name in ["identify_task_type", "route_task", "route"]:
                method = getattr(router_instance, method_name, None)
                if method and callable(method):
                    try:
                        result = method(task_text)
                        if hasattr(result, "type"):
                            task_type = result
                        elif isinstance(result, dict):
                            task_type = result.get("type")
                        else:
                            task_type = result
                        break
                    except TypeError:
                        continue
            
            return self._wrap_envelope(task_text, task_type)
        except Exception as e:
            log.exception("HermesTeam adapter failed")
            return self._fallback_route(task_text, error=f"{type(e).__name__}: {e}")
    
    def _load_router(self):
        if not HERMES_TEAM_ROUTER_PATH.exists():
            log.warning(f"HermesTeam router not found at {HERMES_TEAM_ROUTER_PATH}")
            return None
        try:
            spec = importlib.util.spec_from_file_location(
                "hermes_team_task_router", str(HERMES_TEAM_ROUTER_PATH)
            )
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules["hermes_team_task_router"] = module
            spec.loader.exec_module(module)
            
            router_class = getattr(module, "TaskRouter", None)
            if router_class is None:
                return None
            return router_class()
        except Exception as e:
            log.warning(f"Failed to import HermesTeam router: {e}")
            return None
    
    def _wrap_envelope(self, task_text: str, task_type) -> RouteEnvelope:
        capabilities: List[CapabilityRef] = []
        workflow: List[WorkflowStep] = []
        
        if task_type is None:
            return self._fallback_route(task_text)
        
        if hasattr(task_type, "type"):
            # TaskProfile 物件
            capabilities.append(CapabilityRef(
                id=getattr(task_type, "type", "unknown"),
                name=getattr(task_type, "name", str(task_type)),
                domain=self.domain.value,
            ))
            for idx, step in enumerate(getattr(task_type, "workflow", []) or []):
                workflow.append(WorkflowStep(
                    step=idx + 1,
                    action=step,
                    role=step,
                    domain=self.domain.value,
                ))
        elif isinstance(task_type, str):
            capabilities.append(CapabilityRef(
                id=task_type, name=task_type, domain=self.domain.value,
            ))
        
        return RouteEnvelope(
            task_text=task_text,
            detected_domain=self.domain.value,
            domain_confidence=self.can_handle(task_text),
            capabilities=capabilities,
            workflow=workflow,
            raw_result=task_type,
            adapter_used="HermesTeamAdapter",
        )
    
    def _fallback_route(self, task_text: str, error: str = None) -> RouteEnvelope:
        return RouteEnvelope(
            task_text=task_text,
            detected_domain=self.domain.value,
            domain_confidence=0.1,
            capabilities=[CapabilityRef(id="general", name="通用助手", domain=self.domain.value)],
            workflow=[WorkflowStep(step=1, action="理解任務", role="assistant", domain=self.domain.value)],
            raw_result={"mode": "fallback"},
            adapter_used="HermesTeamAdapter",
            error=error,
        )
