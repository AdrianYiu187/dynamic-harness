"""
stock_adapter.py — 委派給 Stock_Team_Agent StockRouter
========================================================

目標：完全不改動 StockRouter，包裝為 RouteEnvelope。
策略與 ft_adapter.py 相同。

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

log = logging.getLogger("dynamic_harness.stock")

STOCK_ROUTER_PATH = Path.home() / ".hermes" / "skills" / "productivity" / "stock-team-agent" / "scripts" / "task_router" / "stock_router.py"


class StockAdapter:
    domain = Domain.STOCK
    
    # 港股代碼常見前綴
    HK_STOCK_PATTERN = re.compile(r'\b(0[0-9]{4})\b')  # e.g. 01810, 00700
    
    def can_handle(self, task_text: str) -> float:
        text_lower = task_text.lower()
        hits = sum(1 for kw in DOMAIN_KEYWORDS[Domain.STOCK.value] if kw.lower() in text_lower)
        # 港股代碼加分
        if self.HK_STOCK_PATTERN.search(task_text):
            hits += 2
        if hits == 0:
            return 0.0
        return min(hits / 4.0, 1.0)
    
    def route(self, task_text: str) -> RouteEnvelope:
        try:
            router_instance = self._load_stock_router()
            if router_instance is None:
                return self._fallback_route(task_text, error="Stock router failed to load")
            
            # 嘗試 extract symbol
            symbol = self._extract_symbol(task_text)
            if hasattr(router_instance, 'symbol') and symbol:
                router_instance.symbol = symbol
            
            # 找路由方法（不同版本可能叫 route / route_task / dispatch）
            raw = None
            for method_name in ["route_task", "route", "dispatch", "process"]:
                method = getattr(router_instance, method_name, None)
                if method and callable(method):
                    try:
                        raw = method(task_text)
                        break
                    except TypeError:
                        # 方法簽名不同
                        continue
            
            if raw is None:
                raw = {"detected": True, "router_class": router_instance.__class__.__name__}
            
            return self._wrap_envelope(task_text, raw, symbol)
        except Exception as e:
            log.exception("Stock adapter failed")
            return self._fallback_route(task_text, error=f"{type(e).__name__}: {e}")
    
    def _extract_symbol(self, task_text: str) -> str:
        m = self.HK_STOCK_PATTERN.search(task_text)
        if m:
            return m.group(1)
        return ""
    
    def _load_stock_router(self):
        if not STOCK_ROUTER_PATH.exists():
            log.warning(f"Stock router not found at {STOCK_ROUTER_PATH}")
            return None
        try:
            spec = importlib.util.spec_from_file_location(
                "stock_router", str(STOCK_ROUTER_PATH)
            )
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules["stock_router"] = module
            spec.loader.exec_module(module)
            
            router_class = getattr(module, "StockRouter", None)
            if router_class is None:
                return None
            return router_class()
        except Exception as e:
            log.warning(f"Failed to import Stock router: {e}")
            return None
    
    def _wrap_envelope(self, task_text: str, raw, symbol: str) -> RouteEnvelope:
        capabilities: List[CapabilityRef] = []
        if symbol:
            capabilities.append(CapabilityRef(
                id=f"S-symbol:{symbol}",
                name=f"分析 {symbol}",
                domain=self.domain.value,
            ))
        return RouteEnvelope(
            task_text=task_text,
            detected_domain=self.domain.value,
            domain_confidence=self.can_handle(task_text),
            capabilities=capabilities,
            workflow=[],
            raw_result=raw,
            adapter_used="StockAdapter",
        )
    
    def _fallback_route(self, task_text: str, error: str = None) -> RouteEnvelope:
        symbol = self._extract_symbol(task_text)
        return RouteEnvelope(
            task_text=task_text,
            detected_domain=self.domain.value,
            domain_confidence=self.can_handle(task_text),
            capabilities=[CapabilityRef(
                id=f"S-symbol:{symbol}" if symbol else "S-unknown",
                name=f"分析 {symbol}" if symbol else "未指定股票",
                domain=self.domain.value,
            )],
            workflow=[],
            raw_result={"symbol": symbol, "mode": "fallback"},
            adapter_used="StockAdapter",
            error=error,
        )
