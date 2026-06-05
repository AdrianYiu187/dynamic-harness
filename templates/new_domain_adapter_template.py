"""
new_domain_adapter_template.py — 新領域 adapter 範本

複製此檔為 adapters/<your_domain>_adapter.py，然後：
1. 修改 Domain.YOUR_DOMAIN
2. 填入 YOUR_KEYWORDS
3. 實作 3 個方法
4. 加測試到 tests/test_basic.py
"""
from __future__ import annotations
import importlib.util
import logging
import sys
from pathlib import Path
from typing import List

# 確保可 import schemas
_HERE = Path(__file__).resolve().parent
_DH_ROOT = _HERE.parent
sys.path.insert(0, str(_DH_ROOT))

from schemas import (
    Domain, RouteEnvelope, CapabilityRef, WorkflowStep,
    DOMAIN_KEYWORDS,
)

log = logging.getLogger("dynamic_harness.<your_domain>")


# 1. 設定下游 router 路徑
YOUR_ROUTER_PATH = Path.home() / "path" / "to" / "your_router.py"


class YourDomainAdapter:
    """委派給 <Your Router>"""
    
    # 2. 設定 domain
    domain = Domain.YOUR_DOMAIN  # 需先在 schemas.py 的 Domain enum 加入
    
    # 3. 你的領域關鍵字
    YOUR_KEYWORDS = DOMAIN_KEYWORDS[Domain.YOUR_DOMAIN.value] + [
        # 補充自定義關鍵字
    ]
    
    def can_handle(self, task_text: str) -> float:
        """信心度評分：命中數 / 3.0 封頂 1.0"""
        text_lower = task_text.lower()
        hits = sum(1 for kw in self.YOUR_KEYWORDS if kw.lower() in text_lower)
        if hits == 0:
            return 0.0
        return min(hits / 3.0, 1.0)
    
    def route(self, task_text: str) -> RouteEnvelope:
        try:
            router_instance = self._load_router()
            if router_instance is None:
                return self._fallback_route(task_text, error="Your router failed to load")
            
            # 4. 呼叫 router 的真實介面（先用 scripts/discover_router_api.py 探測）
            raw = router_instance.your_method(task_text)
            return self._wrap_envelope(task_text, raw)
        except Exception as e:
            log.exception("Your adapter failed")
            return self._fallback_route(task_text, error=f"{type(e).__name__}: {e}")
    
    def _load_router(self):
        """動態載入下游 router（參考 references/adapter-pattern-techniques.md）"""
        if not YOUR_ROUTER_PATH.exists():
            log.warning(f"Your router not found at {YOUR_ROUTER_PATH}")
            return None
        try:
            spec = importlib.util.spec_from_file_location(
                "your_module_name", str(YOUR_ROUTER_PATH)
            )
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules["your_module_name"] = module
            spec.loader.exec_module(module)
            
            # 5. 換成你 router 的真實 class 名
            router_class = getattr(module, "YourRouterClass", None)
            if router_class is None:
                return None
            return router_class()
        except Exception as e:
            log.warning(f"Failed to import Your router: {e}")
            return None
    
    def _wrap_envelope(self, task_text: str, raw) -> RouteEnvelope:
        """包裝下游結果為 RouteEnvelope"""
        capabilities: List[CapabilityRef] = []
        workflow: List[WorkflowStep] = []
        
        # 6. 解析 raw（依你 router 的真實結構）
        if isinstance(raw, dict):
            for cap_id in raw.get("cap_ids", raw.get("capabilities", [])):
                if isinstance(cap_id, str):
                    capabilities.append(CapabilityRef(
                        id=cap_id, name=cap_id, domain=self.domain.value,
                    ))
            
            for idx, step in enumerate(raw.get("workflow", [])):
                if isinstance(step, str):
                    workflow.append(WorkflowStep(
                        step=idx + 1, action=step, role=step, domain=self.domain.value,
                    ))
        
        return RouteEnvelope(
            task_text=task_text,
            detected_domain=self.domain.value,
            domain_confidence=self.can_handle(task_text),
            capabilities=capabilities,
            workflow=workflow,
            raw_result=raw,
            adapter_used="YourDomainAdapter",
        )
    
    def _fallback_route(self, task_text: str, error: str = None) -> RouteEnvelope:
        """下游 router 不可用時的最小 envelope"""
        return RouteEnvelope(
            task_text=task_text,
            detected_domain=self.domain.value,
            domain_confidence=self.can_handle(task_text),
            capabilities=[CapabilityRef(id="unknown", name="未知", domain=self.domain.value)],
            workflow=[],
            raw_result={"mode": "fallback"},
            adapter_used="YourDomainAdapter",
            error=error,
        )
