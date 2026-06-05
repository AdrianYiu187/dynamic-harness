"""
coding_adapter.py — 委派給 CodingTeamAgent CodingTaskRouter
============================================================

目標：包裝 CodingTaskRouter 為 RouteEnvelope。
CodingTaskRouter 在 scripts/train/task_router.py（透過 scripts/task_router.py shim）。

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

log = logging.getLogger("dynamic_harness.coding")

CODING_ROUTER_PATH = Path.home() / ".hermes" / "skills" / "autonomous-ai-agents" / "coding-team-agent" / "scripts" / "train" / "task_router.py"


class CodingAdapter:
    domain = Domain.CODING
    
    def can_handle(self, task_text: str) -> float:
        text_lower = task_text.lower()
        hits = sum(1 for kw in DOMAIN_KEYWORDS[Domain.CODING.value] if kw.lower() in text_lower)
        if hits == 0:
            return 0.0
        return min(hits / 3.0, 1.0)
    
    def route(self, task_text: str) -> RouteEnvelope:
        try:
            router_instance = self._load_coding_router()
            if router_instance is None:
                return self._fallback_route(task_text, error="Coding router failed to load")
            
            raw = None
            for method_name in ["route_task", "route", "analyze_task", "classify_intent"]:
                method = getattr(router_instance, method_name, None)
                if method and callable(method):
                    try:
                        raw = method(task_text)
                        break
                    except TypeError:
                        continue
            
            if raw is None:
                # 用 detect_intent 或 classify_intent
                if hasattr(router_instance, "detect_intent"):
                    raw = router_instance.detect_intent(task_text)
            
            if raw is None:
                raw = {"detected": True, "router_class": router_instance.__class__.__name__}
            
            return self._wrap_envelope(task_text, raw)
        except Exception as e:
            log.exception("Coding adapter failed")
            return self._fallback_route(task_text, error=f"{type(e).__name__}: {e}")
    
    def _load_coding_router(self):
        if not CODING_ROUTER_PATH.exists():
            log.warning(f"Coding router not found at {CODING_ROUTER_PATH}")
            return None
        try:
            spec = importlib.util.spec_from_file_location(
                "coding_task_router", str(CODING_ROUTER_PATH)
            )
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules["coding_task_router"] = module
            spec.loader.exec_module(module)
            
            router_class = getattr(module, "CodingTaskRouter", None)
            if router_class is None:
                return None
            return router_class()
        except Exception as e:
            log.warning(f"Failed to import Coding router: {e}")
            return None
    
    def _wrap_envelope(self, task_text: str, raw) -> RouteEnvelope:
        capabilities: List[CapabilityRef] = []
        workflow: List[WorkflowStep] = []
        
        # Coding router 的 RoutingResult 有 .intent / .workflow / .complexity
        intent = getattr(raw, "intent", None)
        if intent:
            intent_name = getattr(intent, "name", str(intent))
            capabilities.append(CapabilityRef(
                id=str(intent_name),
                name=str(intent_name),
                domain=self.domain.value,
                confidence=getattr(raw, "intent_confidence", 1.0),
            ))
        
        for step in getattr(raw, "workflow", []) or []:
            if hasattr(step, "action"):
                workflow.append(WorkflowStep(
                    step=getattr(step, "step_num", len(workflow) + 1),
                    action=getattr(step, "action", ""),
                    role=str(getattr(step, "roles", [""])[0]) if getattr(step, "roles", None) else "",
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
            adapter_used="CodingAdapter",
        )
    
    def _fallback_route(self, task_text: str, error: str = None) -> RouteEnvelope:
        # 簡單意圖識別
        intent = "general"
        text_lower = task_text.lower()
        if any(k in text_lower for k in ["網頁", "web", "網站"]):
            intent = "WEB_APP"
        elif any(k in text_lower for k in ["api", "後端", "backend"]):
            intent = "API_DESIGN"
        elif any(k in text_lower for k in ["重構", "refactor"]):
            intent = "REFACTOR"
        elif any(k in text_lower for k in ["審計", "audit", "安全", "security"]):
            intent = "SECURITY_AUDIT"
        
        return RouteEnvelope(
            task_text=task_text,
            detected_domain=self.domain.value,
            domain_confidence=self.can_handle(task_text),
            capabilities=[CapabilityRef(id=intent, name=intent, domain=self.domain.value)],
            workflow=[],
            raw_result={"intent": intent, "mode": "fallback"},
            adapter_used="CodingAdapter",
            error=error,
        )
