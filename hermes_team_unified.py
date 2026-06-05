"""
hermes_team_unified.py — HermesTeamAgent + Dynamic Harness 整合
================================================================

用途：讓 HermesTeamAgent 主線 CLI 自動 fallthrough 到 Dynamic Harness。
完全不改動現有 task_router.py（1,424 行），用包裝方式整合。

行為：
1. 先用現有 TaskRouter.identify_task_type() 識別任務
2. 若識別為 "general"（信心度低）→ 自動 fallthrough 到 Dynamic Harness
3. Dynamic Harness 自動判斷 domain 並委派給專業 adapter
4. 輸出統一格式

使用：
    # 直接呼叫
    from hermes_team_unified import UnifiedTaskRouter
    router = UnifiedTaskRouter()
    result = router.route("曼聯 對 車路士 赔率")  # 自動 ft
    
    # CLI
    python3 hermes_team_unified.py --task "分析 01810"
    python3 hermes_team_unified.py --task "..." --no-fallback  # 強制只用原版
    python3 hermes_team_unified.py --task "..." --save  # 存 SQLite

日期：2026-06-05
"""
from __future__ import annotations
import logging
import sys
from pathlib import Path
from typing import Optional

# 確保可 import dynamic_harness
DH_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(DH_ROOT))

# 動態載入 HermesTeamAgent 的 task_router
HTA_DIR = Path.home() / ".hermes" / "skills" / "productivity" / "hermes-team-agent" / "scripts"
HTA_ROUTER_PATH = HTA_DIR / "task_router.py"

# Dynamic Harness 模組
from unified_router import UnifiedRouter
from schemas import RouteEnvelope
import persistence

log = logging.getLogger("hermes_team_unified")


# === 載入 HermesTeamAgent TaskRouter（不執行其 main）===

def _load_hta_router():
    """動態載入 HermesTeamAgent 的 task_router.py 並取 TaskRouter class

    找不到時 graceful degrade，回傳 (None, None) — 整合路由器仍可運作
    （HTA 是 optional 增強，沒有它也能 fallback 到 Dynamic Harness）
    """
    import importlib.util
    if not HTA_ROUTER_PATH.exists():
        log.info("HTA TaskRouter not found at %s — running without it", HTA_ROUTER_PATH)
        return None, None
    try:
        spec = importlib.util.spec_from_file_location("hta_task_router", str(HTA_ROUTER_PATH))
        if spec is None or spec.loader is None:
            return None, None
        module = importlib.util.module_from_spec(spec)
        sys.modules["hta_task_router"] = module
        spec.loader.exec_module(module)
        return module.TaskRouter, module.TASK_PROFILES
    except (FileNotFoundError, ImportError, AttributeError) as e:
        log.warning("Failed to load HTA TaskRouter: %s — running without it", e)
        return None, None


# === 整合路由器 ===

class UnifiedTaskRouter:
    """整合 HermesTeamAgent + Dynamic Harness

    流程：
    1. 嘗試本地 TaskRouter.identify_task_type()
    2. 若為 "general" 類型（信心度低）→ 自動 fallthrough 到 Dynamic Harness
    3. 統一輸出 RouteEnvelope
    """
    
    LOCAL_LOW_CONFIDENCE_TYPES = {"general"}  # 視為低信心度的 task type
    
    def __init__(
        self,
        verbose: bool = False,
        enable_llm_judge: bool = True,
        enable_fallback: bool = True,
        save_to_db: bool = False,
        fallthrough_confidence_threshold: float = 0.3,
    ):
        """初始化整合路由器

        Args:
            verbose: 詳細輸出
            enable_llm_judge: Dynamic Harness 是否啟用 LLM 二次判斷
            enable_fallback: 是否啟用「local → unified」fallthrough
            save_to_db: 是否把 envelope 存入 SQLite
            fallthrough_confidence_threshold: 本地信心度低於此值就 fallthrough
                預設 0.3 — 比「type=='general'」更寬鬆
        """
        self.verbose = verbose
        self.enable_fallback = enable_fallback
        self.save_to_db = save_to_db
        self.fallthrough_confidence_threshold = fallthrough_confidence_threshold
        
        # 載入 HermesTeamAgent 本地 router
        self.HTA_TaskRouter, self.HTA_TASK_PROFILES = _load_hta_router()
        if self.HTA_TaskRouter is None:
            log.warning(f"Failed to load HermesTeamAgent TaskRouter from {HTA_ROUTER_PATH}")
        
        # Dynamic Harness
        self.unified = UnifiedRouter(verbose=verbose, enable_llm_judge=enable_llm_judge)
        
        if verbose:
            print(f"[UnifiedTaskRouter] Initialized")
            print(f"  HermesTeamAgent: {'loaded' if self.HTA_TaskRouter else 'failed'}")
            print(f"  Dynamic Harness: {len(self.unified.list_adapters())} adapters")
            print(f"  Fallthrough: {'enabled' if enable_fallback else 'disabled'}")
            print(f"  SQLite: {'enabled' if save_to_db else 'disabled'}")
    
    def route(self, task_text: str, force_fallback: bool = False) -> dict:
        """路由單一任務

        Args:
            task_text: 任務描述
            force_fallback: 強制走 Dynamic Harness（跳過本地）

        Returns:
            統一 dict：{
                "source": "hta" | "unified",
                "local_type": str,         # 本地識別的 task type
                "envelope": dict,          # 統一 envelope
                "fallthrough_triggered": bool,
                "fallthrough_reason": str,
            }
        """
        if not task_text or not task_text.strip():
            return {
                "source": "none",
                "local_type": None,
                "envelope": None,
                "fallthrough_triggered": False,
                "fallthrough_reason": "Empty task",
            }
        
        # 1. 本地識別
        local_type = None
        local_confidence = 0.0
        if self.HTA_TaskRouter is not None and not force_fallback:
            try:
                hta_router = self.HTA_TaskRouter()
                profile = hta_router.identify_task_type(task_text)
                local_type = profile.type
                # 從 profile 推信心度（heuristic：capabilities 數量）
                local_confidence = min(len(profile.capabilities) / 3.0, 1.0) if profile.capabilities else 0.0
                if self.verbose:
                    print(f"[Local] type={local_type} caps={len(profile.capabilities)} conf={local_confidence:.2f}")
            except Exception as e:
                log.warning(f"Local routing failed: {e}")
                local_type = "general"
                local_confidence = 0.0
        
        # 2. 判斷是否需要 fallthrough
        fallthrough = False
        fallthrough_reason = ""
        
        if force_fallback:
            fallthrough = True
            fallthrough_reason = "force_fallback=True"
        elif not self.enable_fallback:
            fallthrough = False
            fallthrough_reason = "fallthrough disabled"
        elif local_type in self.LOCAL_LOW_CONFIDENCE_TYPES:
            fallthrough = True
            fallthrough_reason = f"local type='{local_type}' (low confidence)"
        elif local_confidence < self.fallthrough_confidence_threshold:
            fallthrough = True
            fallthrough_reason = f"local confidence {local_confidence:.2f} < {self.fallthrough_confidence_threshold}"
        
        if self.verbose and fallthrough:
            print(f"[UnifiedTaskRouter] Fallthrough: {fallthrough_reason}")
        
        # 3. 委派
        if fallthrough:
            envelope = self.unified.route(task_text)
            source = "unified"
        else:
            # 本地處理：包成本地 envelope
            envelope = self._wrap_local(task_text, local_type)
            source = "hta"
        
        # 4. 存 SQLite
        if self.save_to_db and envelope:
            try:
                # 從 dict 重建 envelope 物件以便存檔
                from dataclasses import asdict
                env_obj = self._dict_to_envelope(envelope) if isinstance(envelope, dict) else envelope
                eid = persistence.save_envelope_safe(env_obj)
                if self.verbose:
                    print(f"[UnifiedTaskRouter] Saved envelope #{eid}")
            except Exception as e:
                log.warning(f"Save failed: {e}")
        
        # 5. 回傳
        env_dict = envelope if isinstance(envelope, dict) else envelope.to_dict()
        return {
            "source": source,
            "local_type": local_type,
            "envelope": env_dict,
            "fallthrough_triggered": fallthrough,
            "fallthrough_reason": fallthrough_reason,
        }
    
    def _wrap_local(self, task_text: str, local_type: str) -> RouteEnvelope:
        """包裝本地識別結果為 RouteEnvelope"""
        if self.HTA_TaskRouter is None or local_type not in self.HTA_TASK_PROFILES:
            # 最終 fallback — 用 Dynamic Harness
            return self.unified.route(task_text)
        
        profile = self.HTA_TASK_PROFILES[local_type]
        from schemas import CapabilityRef, WorkflowStep
        return RouteEnvelope(
            task_text=task_text,
            detected_domain="hermes",  # 本地 router 全部視為 hermes 域
            domain_confidence=min(len(profile.capabilities) / 3.0, 1.0),
            capabilities=[
                CapabilityRef(id=c, name=c, domain="hermes")
                for c in profile.capabilities[:20]
            ],
            workflow=[
                WorkflowStep(step=i+1, action=step, role=step, domain="hermes")
                for i, step in enumerate(profile.workflow)
            ],
            raw_result={"local_type": local_type, "profile": profile.name, "method": "hta_local"},
            adapter_used="HermesTeamLocalAdapter",
        )
    
    def _dict_to_envelope(self, d: dict) -> RouteEnvelope:
        """從 dict 重建 RouteEnvelope（給 save_envelope 用）"""
        from schemas import CapabilityRef, WorkflowStep
        return RouteEnvelope(
            task_text=d.get("task_text", ""),
            detected_domain=d.get("detected_domain", ""),
            domain_confidence=d.get("domain_confidence", 0.0),
            capabilities=[
                CapabilityRef(
                    id=c.get("id", ""),
                    name=c.get("name", ""),
                    domain=c.get("domain", ""),
                    confidence=c.get("confidence", 1.0),
                )
                for c in d.get("capabilities", [])
            ],
            workflow=[
                WorkflowStep(
                    step=w.get("step", i+1),
                    action=w.get("action", ""),
                    role=w.get("role", ""),
                    domain=w.get("domain", ""),
                )
                for i, w in enumerate(d.get("workflow", []))
            ],
            raw_result=d.get("raw_result"),
            adapter_used=d.get("adapter_used", "unknown"),
            error=d.get("error"),
        )


# === CLI 入口 ===

def main():
    import argparse
    import json
    
    parser = argparse.ArgumentParser(
        description="HermesTeamAgent + Dynamic Harness 整合路由器",
    )
    parser.add_argument("--task", "-t", required=True, help="任務描述")
    parser.add_argument("--verbose", "-v", action="store_true", help="詳細輸出")
    parser.add_argument("--no-llm", action="store_true", help="關閉 LLM 二次判斷")
    parser.add_argument("--no-fallback", action="store_true", help="關閉 local→unified fallthrough")
    parser.add_argument("--force-fallback", action="store_true", help="強制走 Dynamic Harness")
    parser.add_argument("--save", action="store_true", help="儲存 envelope 到 SQLite")
    parser.add_argument(
        "--fallthrough-threshold",
        type=float,
        default=0.3,
        help="本地信心度低於此值就 fallthrough 到 unified（預設 0.3）",
    )
    args = parser.parse_args()
    
    router = UnifiedTaskRouter(
        verbose=args.verbose,
        enable_llm_judge=not args.no_llm,
        enable_fallback=not args.no_fallback,
        save_to_db=args.save,
        fallthrough_confidence_threshold=args.fallthrough_threshold,
    )
    
    result = router.route(args.task, force_fallback=args.force_fallback)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
