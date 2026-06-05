"""
unified_router.py — Dynamic Harness Meta-Dispatcher
=====================================================

設計目標：
- 跨 4 套 task_router 統一入口
- 自動判斷 domain，委派給對應 adapter
- 完全不修改 4 套現有 router
- v1.1 新增：LLM 二次判斷、--force-domain、multi_route()

使用方式：
    from dynamic_harness.unified_router import UnifiedRouter
    router = UnifiedRouter()
    result = router.route("分析 01810 股票走勢", force_domain="stock")
    
    # 多任務拆分
    results = router.multi_route("分析 01810 並做網頁儀表板")
    
    # CLI
    python -m dynamic_harness.unified_router --task "..."
    python -m dynamic_harness.unified_router --multi --task "..."

日期：2026-06-05
"""
from __future__ import annotations
import contextlib
import importlib
import importlib.util
import io
import logging
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Type

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from schemas import (
    Domain,
    DomainAdapter,
    RouteEnvelope,
    CapabilityRef,
    WorkflowStep,
    DOMAIN_KEYWORDS,
)
from llm_judge import (
    LLM_CONFIDENCE_THRESHOLD,
    llm_judge_domain,
    llm_split_tasks,
)

log = logging.getLogger("dynamic_harness")


# === Domain 自動判斷 ===

def detect_domain(task_text: str) -> tuple[Domain, float]:
    """根據關鍵字判斷最可能的 domain
    回傳：(domain, confidence)
    """
    text_lower = task_text.lower()
    scores: Dict[str, int] = {}
    
    for domain_value, keywords in DOMAIN_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw.lower() in text_lower)
        if count > 0:
            scores[domain_value] = count
    
    if not scores:
        return Domain.GENERAL, 0.0
    
    best_domain_value = max(scores, key=scores.get)  # type: ignore
    best_score = scores[best_domain_value]
    confidence = min(best_score / 5.0, 1.0)
    
    return Domain(best_domain_value), confidence


# === Adapter Loader ===

class AdapterRegistry:
    """管理所有已註冊的 adapter"""
    
    def __init__(self):
        self._adapters: Dict[Domain, DomainAdapter] = {}
        self._load_all()
    
    def _load_all(self):
        """動態載入 adapters/*.py 內所有 adapter class"""
        adapters_dir = _HERE / "adapters"
        if not adapters_dir.exists():
            log.warning(f"adapters directory not found: {adapters_dir}")
            return
        
        for adapter_file in sorted(adapters_dir.glob("*_adapter.py")):
            module_name = f"dynamic_harness.adapters.{adapter_file.stem}"
            try:
                spec = importlib.util.spec_from_file_location(module_name, adapter_file)
                if spec is None or spec.loader is None:
                    log.warning(f"Cannot load spec for {adapter_file}")
                    continue
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (isinstance(attr, type) 
                        and attr.__name__.endswith("Adapter")
                        and attr.__name__ != "DomainAdapter"
                        and hasattr(attr, 'domain')
                        and hasattr(attr, 'can_handle')
                        and hasattr(attr, 'route')):
                        try:
                            instance = attr()
                            self._adapters[instance.domain] = instance
                            log.info(f"Loaded adapter: {attr.__name__} → {instance.domain.value}")
                        except Exception as e:
                            log.warning(f"Failed to instantiate {attr.__name__}: {e}")
            except Exception as e:
                log.warning(f"Failed to load {adapter_file.name}: {e}")
    
    def get(self, domain: Domain) -> Optional[DomainAdapter]:
        return self._adapters.get(domain)
    
    def all(self) -> List[DomainAdapter]:
        return list(self._adapters.values())


# === 多任務拆分啟發式（給 multi_route 用）===

_SPLIT_PATTERNS = [
    # 中文連接詞
    r'\s*並\s*[且]?\s*',          # 「並」「並且」
    r'\s*和\s*',                  # 「和」
    r'\s*同時\s*',                # 「同時」
    r'\s*還要\s*',                # 「還要」
    r'\s*以及\s*',                # 「以及」
    r'\s*及\s*',                  # 「及」
    r'\s*然後\s*',                # 「然後」
    r'\s*接著\s*',                # 「接著」
    r'\s*，\s*',                  # 「，」
    r'\s*,\s*',                   # 「,」
    r'\s*;\s*',                   # 「;」
    r'\s*；\s*',                  # 「；」
    r'\s*\+\s*',                  # 「+」
    r'\s*&\s*',                   # 「&」
]

_SPLIT_REGEX = re.compile('|'.join(_SPLIT_PATTERNS))


def heuristic_split(task_text: str) -> List[str]:
    """啟發式拆分：依連接詞切分

    Returns:
        子任務列表（過濾空字串）
    """
    parts = _SPLIT_REGEX.split(task_text)
    return [p.strip() for p in parts if p.strip() and len(p.strip()) > 1]


# === Module-level worker kept for future (subprocess 模式參考) ===

def _parallel_worker(idx: int, sub: str, state: dict) -> tuple:
    """子進程 worker（保留供未來 subprocess 模式參考）

    v1.4 註：multiprocessing.fork 在 macOS Python 3.9 會失敗
    因為 child 進程無法 re-import parent 用 importlib 載入的模組
    （如 coding_task_router 動態載入失敗）

    保留此函式供未來可能的 subprocess 模式參考。
    """
    return (idx, sub, None, "not implemented in v1.4")


# === Unified Router ===

class UnifiedRouter:
    """Dynamic Harness 統一入口
    
    v1.1 功能：
    - 自動 domain 判斷（regex）
    - 信心度 < 0.5 時自動 call LLM 二次判斷
    - 支援 force_domain 強制指定
    - 支援 multi_route() 一句話拆多個 domain
    """
    
    def __init__(
        self,
        verbose: bool = False,
        enable_llm_judge: bool = True,
        enable_cache: bool = True,
        cache_ttl_seconds: int = 86400,
    ):
        """初始化 UnifiedRouter

        Args:
            verbose: 詳細輸出
            enable_llm_judge: 是否啟用 LLM 二次判斷（信心度 < 0.5 時觸發）
                              設為 False 可離線運行
            enable_cache: 是否啟用 envelope cache（v1.4+）
                         命中時直接回傳，不重複執行 adapter
            cache_ttl_seconds: cache TTL（預設 24h = 86400s）
        """
        self.verbose = verbose
        self.enable_llm_judge = enable_llm_judge
        self.enable_cache = enable_cache
        self.cache_ttl_seconds = cache_ttl_seconds
        self.registry = AdapterRegistry()
        if verbose:
            print(f"[UnifiedRouter] Loaded {len(self.registry.all())} adapters:")
            for adapter in self.registry.all():
                print(f"  - {adapter.__class__.__name__} → {adapter.domain.value}")
            print(f"[UnifiedRouter] Cache: {'enabled' if enable_cache else 'disabled'} (TTL={cache_ttl_seconds}s)")
    
    def route(
        self,
        task_text: str,
        force_domain: Optional[str] = None,
    ) -> RouteEnvelope:
        """路由單一任務

        Args:
            task_text: 任務描述
            force_domain: 強制指定 domain（覆蓋自動判斷）
                          可選值: "ft" | "stock" | "coding" | "hermes" | "general"
        """
        if not task_text or not task_text.strip():
            return RouteEnvelope(
                task_text=task_text,
                detected_domain=Domain.GENERAL.value,
                domain_confidence=0.0,
                capabilities=[],
                workflow=[],
                raw_result=None,
                adapter_used="none",
                error="Empty task text",
            )

        # 0. Cache 檢查（v1.4+）
        if self.enable_cache:
            try:
                import persistence, metrics
                cached_dict = persistence.cache_get(
                    task_text,
                    force_domain=force_domain,
                    ttl_seconds=self.cache_ttl_seconds,
                )
                if cached_dict is not None:
                    if self.verbose:
                        print(f"[UnifiedRouter] Cache HIT for: {task_text[:50]!r}")
                    # 記錄 cache hit metric
                    try:
                        metrics.record(metric_type="cache_hit", metric_key="hit")
                    except Exception:
                        pass
                    # 從 dict 重建 envelope（標記 _from_cache=True）
                    cached_dict = dict(cached_dict)
                    cached_dict.setdefault("raw_result", {})
                    if isinstance(cached_dict["raw_result"], dict):
                        cached_dict["raw_result"]["_from_cache"] = True
                    return self._dict_to_envelope(cached_dict)
                else:
                    # 記錄 cache miss metric
                    try:
                        metrics.record(metric_type="cache_miss", metric_key="miss")
                    except Exception:
                        pass
            except Exception as e:
                log.debug(f"Cache check failed (non-fatal): {e}")

        # 1. 判斷 domain（regex → LLM 二次判斷）
        domain, confidence, llm_used = self._detect_domain_with_fallback(
            task_text, force_domain
        )
        if self.verbose:
            llm_tag = " (LLM)" if llm_used else ""
            print(f"[UnifiedRouter] Detected domain: {domain.value} (confidence={confidence:.2f}){llm_tag}")

        # 1.5 記錄 LLM judge metrics（v1.5+）
        if llm_used:
            try:
                import metrics, cost
                metrics.record(
                    metric_type="llm_judge",
                    metric_key="triggered",
                    success=True,
                )
                cost.record_cost("minimax", "llm_judge")
            except Exception:
                pass

        # 2. 找對應 adapter
        adapter = self.registry.get(domain)
        if adapter is None:
            # Fallback：找一個可以處理的（can_handle > 0.3）
            for candidate in self.registry.all():
                cand_conf = candidate.can_handle(task_text)
                if cand_conf > 0.3:
                    adapter = candidate
                    confidence = cand_conf
                    if self.verbose:
                        print(f"[UnifiedRouter] Fallback to: {candidate.__class__.__name__} ({cand_conf:.2f})")
                    break

        if adapter is None:
            return RouteEnvelope(
                task_text=task_text,
                detected_domain=domain.value,
                domain_confidence=confidence,
                capabilities=[],
                workflow=[],
                raw_result=None,
                adapter_used="none",
                error=f"No adapter can handle domain={domain.value}",
            )

        # 3. 委派
        adapter_start_ts = time.time()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                envelope = adapter.route(task_text)
            adapter_latency_ms = (time.time() - adapter_start_ts) * 1000

            # 標記 LLM 二次判斷使用
            if llm_used:
                envelope.domain_confidence = confidence
                # 在 raw_result 加註記
                if isinstance(envelope.raw_result, dict):
                    envelope.raw_result["_llm_judged"] = True

            if self.verbose:
                print(f"[UnifiedRouter] Routed via: {envelope.adapter_used} ({adapter_latency_ms:.1f}ms)")

            # 3.5 記錄 metrics（v1.5+）
            try:
                import metrics
                metrics.record(
                    metric_type="adapter_call",
                    metric_key=envelope.adapter_used,
                    latency_ms=adapter_latency_ms,
                    success=not bool(envelope.error),
                )
            except Exception:
                pass

            # 4. 寫入 cache
            if self.enable_cache:
                try:
                    import persistence
                    persistence.cache_put(task_text, envelope, force_domain=force_domain)
                except Exception as e:
                    log.debug(f"Cache write failed (non-fatal): {e}")

            return envelope
        except Exception as e:
            adapter_latency_ms = (time.time() - adapter_start_ts) * 1000
            log.exception(f"Adapter {adapter.__class__.__name__} failed")
            # 記錄失敗 metric
            try:
                import metrics
                metrics.record(
                    metric_type="adapter_call",
                    metric_key=adapter.__class__.__name__,
                    latency_ms=adapter_latency_ms,
                    success=False,
                    extra={"error": str(e)},
                )
            except Exception:
                pass
            return RouteEnvelope(
                task_text=task_text,
                detected_domain=domain.value,
                domain_confidence=confidence,
                capabilities=[],
                workflow=[],
                raw_result=None,
                adapter_used=adapter.__class__.__name__,
                error=f"{type(e).__name__}: {e}",
            )

    def _dict_to_envelope(self, d: dict) -> RouteEnvelope:
        """從 dict 重建 RouteEnvelope"""
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
    
    def _detect_domain_with_fallback(
        self, task_text: str, force_domain: Optional[str]
    ) -> tuple[Domain, float, bool]:
        """判斷 domain，必要時用 LLM 二次判斷

        Returns:
            (domain, confidence, llm_used)
        """
        # 1. 強制指定優先
        if force_domain:
            try:
                domain = Domain(force_domain)
                return (domain, 1.0, False)  # 強制指定 = 100% 信心
            except ValueError:
                log.warning(f"Invalid force_domain={force_domain}, falling back to auto")
        
        # 2. Regex 自動判斷
        domain, confidence = detect_domain(task_text)
        
        # 3. 信心度足夠就直接用
        if confidence >= LLM_CONFIDENCE_THRESHOLD:
            return (domain, confidence, False)
        
        # 4. 信心度低於閾值，call LLM 二次判斷
        if self.enable_llm_judge:
            llm_result = llm_judge_domain(task_text)
            if llm_result is not None:
                llm_domain, llm_conf = llm_result
                if self.verbose:
                    print(f"[UnifiedRouter] LLM 二次判斷: {llm_domain.value} (conf={llm_conf})")
                return (llm_domain, llm_conf, True)
            if self.verbose:
                print("[UnifiedRouter] LLM 二次判斷失敗，使用 regex 結果")
        
        return (domain, confidence, False)
    
    def multi_route(
        self,
        task_text: str,
        prefer_llm_split: bool = True,
        parallel: bool = False,
        parallel_threshold: float = 0.05,
    ) -> List[RouteEnvelope]:
        """一句話拆多個 domain 並分別路由

        Args:
            task_text: 包含多個子任務的描述
            prefer_llm_split: 優先用 LLM 拆分（False 時只用啟發式）
            parallel: 是否並行執行
                     - True: 強制並行
                     - False (default): 強制順序
                     - "auto": 自動判斷 — 若任務量大於 parallel_threshold 用並行
            parallel_threshold: 「auto」模式下，單次路由耗時門檻（秒）
                              預設 0.05s（50ms）— 簡單任務用順序，LLM/重任務用並行

        流程：
        1. 先跑啟發式（regex 切分連接詞）— 快速、離線、零成本
        2. 若啟發式找到 ≥ 2 個子任務 → 用啟發式
        3. 否則若 prefer_llm_split → 用 LLM 二次判斷
        4. LLM 也失敗或只有 1 個 → 視為單一任務
        5. 對每個子任務呼叫 self.route()（sequential / parallel / auto）
        """
        if not task_text or not task_text.strip():
            return []
        
        # 1. 啟發式先跑（便宜、可靠）
        heuristic_parts = heuristic_split(task_text)
        sub_tasks: List[str] = []
        split_method = "heuristic"
        
        if len(heuristic_parts) >= 2:
            sub_tasks = heuristic_parts
            if self.verbose:
                print(f"[UnifiedRouter] 啟發式拆分 ({len(sub_tasks)} 個子任務):")
                for i, t in enumerate(sub_tasks, 1):
                    print(f"  {i}. {t}")
        elif prefer_llm_split and self.enable_llm_judge:
            # 2. 啟發式只找到 1 個或 0 個 → LLM 補位
            llm_result = llm_split_tasks(task_text)
            if llm_result and len(llm_result) >= 2:
                sub_tasks = llm_result
                split_method = "llm"
                if self.verbose:
                    print(f"[UnifiedRouter] LLM 拆分 ({len(sub_tasks)} 個子任務):")
                    for i, t in enumerate(sub_tasks, 1):
                        print(f"  {i}. {t}")
        
        # 3. 最終只找到 1 個（或 0 個）→ 視為單一任務
        if len(sub_tasks) <= 1:
            if not sub_tasks:
                sub_tasks = [task_text]
            return [self.route(sub_tasks[0])]
        
        # 4. 判斷是否並行
        actual_parallel = self._should_parallel(
            sub_tasks, parallel, parallel_threshold
        )
        
        if self.verbose:
            mode = "parallel" if actual_parallel else "sequential"
            print(f"[UnifiedRouter] 執行模式: {mode} (parallel={parallel}, threshold={parallel_threshold}s)")
        
        # 5. 對每個子任務 route
        if actual_parallel:
            return self._multi_route_parallel(sub_tasks, task_text, split_method)
        return self._multi_route_sequential(sub_tasks, task_text, split_method)
    
    def _should_parallel(
        self,
        sub_tasks: List[str],
        parallel: bool,
        parallel_threshold: float,
    ) -> bool:
        """判斷是否該並行執行

        規則：
        1. parallel=True/False：明確指定
        2. parallel="auto"：根據估算決定
           - 啟發式：sub_task 數量 ≥ 3 → 並行
           - 實測：跑第一個 sub_task 計時，> threshold → 並行
        """
        if parallel is True:
            return True
        if parallel is False:
            return False
        
        # parallel == "auto"
        # 啟發式：≥ 3 個子任務 → 並行
        if len(sub_tasks) >= 3:
            return True
        
        # 實測：跑第一個 sub_task
        try:
            import time
            t0 = time.time()
            self.route(sub_tasks[0])
            elapsed = time.time() - t0
            if self.verbose:
                print(f"[UnifiedRouter] 探測耗時: {elapsed:.3f}s × {len(sub_tasks)} = {elapsed * len(sub_tasks):.3f}s")
            # 若單次耗時 > threshold，或預估總耗時 > 0.15s → 並行
            return elapsed > parallel_threshold or elapsed * len(sub_tasks) > 0.15
        except Exception:
            return False
    
    def _multi_route_sequential(
        self,
        sub_tasks: List[str],
        parent_task: str,
        split_method: str,
    ) -> List[RouteEnvelope]:
        """順序執行多任務"""
        results: List[RouteEnvelope] = []
        for sub in sub_tasks:
            envelope = self.route(sub)
            envelope.raw_result = envelope.raw_result or {}
            if isinstance(envelope.raw_result, dict):
                envelope.raw_result["_parent_task"] = parent_task
                envelope.raw_result["_split_method"] = split_method
            results.append(envelope)
        return results
    
    async def _multi_route_async(
        self,
        sub_tasks: List[str],
        parent_task: str,
        split_method: str,
    ) -> List[RouteEnvelope]:
        """異步並行執行多任務

        使用 asyncio.to_thread 把 sync 的 self.route 包裝成 awaitable
        這樣可以在 async context 中並行呼叫
        """
        import asyncio

        async def route_one(sub: str):
            return await asyncio.to_thread(self.route, sub)

        # 同時啟動所有子任務
        envelopes = await asyncio.gather(
            *[route_one(sub) for sub in sub_tasks],
            return_exceptions=True,
        )

        results: List[RouteEnvelope] = []
        for sub, env_or_exc in zip(sub_tasks, envelopes):
            if isinstance(env_or_exc, Exception):
                # 失敗時建一個 error envelope
                from schemas import RouteEnvelope, WorkflowStep
                err_env = RouteEnvelope(
                    task_text=sub,
                    detected_domain="unknown",
                    domain_confidence=0.0,
                    capabilities=[],
                    workflow=[],
                    raw_result=None,
                    adapter_used="none",
                    error=f"{type(env_or_exc).__name__}: {env_or_exc}",
                )
                results.append(err_env)
            else:
                env_or_exc.raw_result = env_or_exc.raw_result or {}
                if isinstance(env_or_exc.raw_result, dict):
                    env_or_exc.raw_result["_parent_task"] = parent_task
                    env_or_exc.raw_result["_split_method"] = split_method
                results.append(env_or_exc)
        return results

    def _multi_route_parallel(
        self,
        sub_tasks: List[str],
        parent_task: str,
        split_method: str,
    ) -> List[RouteEnvelope]:
        """同步入口：並行（v1.4 改回 sequential 備援）

        v1.4 決定：multiprocessing 在 macOS Python 3.9 有 fork 問題
        (downstream router 動態載入的 importlib 模組在 child 進程中
        找不到，導致 BrokenProcessPool)

        替代方案：
        1. 直接用 sequential（仍標記 _parallel=True 讓 caller 知道意圖）
        2. 未來：subprocess 模式（每個子任務跑獨立 Python process）

        因此本方法實作上等同 sequential，但保留 parallel API
        方便未來升級。文件會明確標示這個限制。
        """
        # 標記：實際上 sequential，但 caller 的意圖保留
        if self.verbose:
            print(f"[UnifiedRouter] parallel mode is sequential-fallback in v1.4 (multiprocessing disabled)")
        return self._multi_route_sequential(sub_tasks, parent_task, split_method)
    
    def list_adapters(self) -> List[Dict[str, str]]:
        """列出所有已載入的 adapter"""
        return [
            {
                "class": adapter.__class__.__name__,
                "domain": adapter.domain.value,
                "module": adapter.__class__.__module__,
            }
            for adapter in self.registry.all()
        ]


# === CLI 入口 ===

def main():
    import argparse
    import json
    
    parser = argparse.ArgumentParser(
        description="Dynamic Harness — 統一任務路由器（跨 FT/Stock/Coding/HermesTeam）"
    )
    parser.add_argument("--task", "-t", help="任務描述")
    parser.add_argument("--verbose", "-v", action="store_true", help="顯示詳細流程")
    parser.add_argument("--list-adapters", action="store_true", help="列出所有已載入的 adapter")
    parser.add_argument(
        "--force-domain", "-d",
        choices=[d.value for d in Domain],
        help="強制指定 domain（覆蓋自動判斷）",
    )
    parser.add_argument(
        "--multi", "-m",
        action="store_true",
        help="多任務模式：自動拆分子任務並分別路由",
    )
    parser.add_argument(
        "--parallel", "-p",
        const="force",
        nargs="?",
        choices=["force", "auto", "never"],
        default=None,
        help="並行模式：force（強制並行）/ auto（自動判斷）/ never（順序）",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="關閉 LLM 二次判斷（純離線模式）",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="關閉 envelope cache",
    )
    parser.add_argument(
        "--cache-stats",
        action="store_true",
        help="顯示 envelope cache 統計",
    )
    parser.add_argument(
        "--cache-clear",
        action="store_true",
        help="清空 envelope cache",
    )
    parser.add_argument(
        "--cleanup",
        type=int,
        metavar="DAYS",
        help="清理超過 N 天的 envelope（從 SQLite 刪除）",
    )
    parser.add_argument(
        "--db-stats",
        action="store_true",
        help="顯示 SQLite 資料庫統計",
    )
    parser.add_argument(
        "--metrics",
        action="store_true",
        help="顯示 metrics 摘要（adapter 呼叫、cache hit rate、LLM judge 觸發次數）",
    )
    parser.add_argument(
        "--cost",
        action="store_true",
        help="顯示本月 API 成本",
    )
    parser.add_argument(
        "--budget",
        type=float,
        metavar="USD",
        help="檢查月預算（例：--budget 5.0 = 5 USD）",
    )
    parser.add_argument(
        "--ui",
        metavar="PLAN_ID",
        help="渲染 plan DAG 視覺化（plan UI）",
    )
    parser.add_argument(
        "--ui-list",
        action="store_true",
        help="列出所有 plan（plan UI 列表模式）",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="關閉 plan UI 顏色（給 log/pipe 用）",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Watch 模式：持續 re-render plan UI 直到 plan 完成",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        metavar="SECS",
        help="Watch 模式 re-render 間隔秒數（default: 2.0）",
    )
    args = parser.parse_args()
    
    if not args.task and not args.list_adapters and args.cleanup is None and not args.db_stats and not args.cache_stats and not args.cache_clear and not args.metrics and not args.cost and args.budget is None and not args.ui and not args.ui_list:
        parser.error("必須提供 --task、--list-adapters、--cleanup、--db-stats、--cache-stats、--cache-clear、--metrics、--cost、--budget、--ui 或 --ui-list")

    # --ui / --ui-list 模式（不需要 router，立即返回）
    if args.ui_list:
        from plan_ui import _render_list, list_plans as _list_plans
        use_color = (not args.no_color) and sys.stdout.isatty()
        _render_list(limit=20, use_color=use_color)
        return
    if args.ui:
        from plan_ui import _render_plan, _watch_plan
        use_color = (not args.no_color) and sys.stdout.isatty()
        if args.live:
            _watch_plan(args.ui, use_color, args.interval)
        else:
            _render_plan(args.ui, use_color)
        return

    router = UnifiedRouter(
        verbose=args.verbose,
        enable_llm_judge=not args.no_llm,
        enable_cache=not args.no_cache,
    )

    # --cache-stats 模式
    if args.cache_stats:
        from persistence import cache_stats, DEFAULT_DB_PATH
        stats = cache_stats()
        stats["db_path"] = str(DEFAULT_DB_PATH)
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return

    # --cache-clear 模式
    if args.cache_clear:
        from persistence import cache_clear, DEFAULT_DB_PATH
        deleted = cache_clear()
        print(json.dumps({
            "action": "cache_clear",
            "deleted_count": deleted,
            "db_path": str(DEFAULT_DB_PATH),
        }, ensure_ascii=False, indent=2))
        return

    # --cleanup 模式
    if args.cleanup is not None:
        from persistence import cleanup_old_envelopes, DEFAULT_DB_PATH
        deleted = cleanup_old_envelopes(retention_days=args.cleanup)
        print(json.dumps({
            "action": "cleanup",
            "retention_days": args.cleanup,
            "deleted_count": deleted,
            "db_path": str(DEFAULT_DB_PATH),
        }, ensure_ascii=False, indent=2))
        return
    
    # --db-stats 模式
    if args.db_stats:
        from persistence import count_envelopes, DEFAULT_DB_PATH
        stats = count_envelopes()
        stats["db_path"] = str(DEFAULT_DB_PATH)
        import os
        stats["db_size_bytes"] = os.path.getsize(DEFAULT_DB_PATH) if DEFAULT_DB_PATH.exists() else 0
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return

    # --metrics 模式（v1.5+）
    if args.metrics:
        import metrics
        summary = metrics.get_summary()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    # --cost 模式（v1.5+）
    if args.cost:
        import cost
        summary = cost.get_cost_summary()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    # --budget 模式（v1.5+）
    if args.budget is not None:
        import cost
        result = cost.check_budget(budget_usd=args.budget)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["warning"]:
            print()
            print(cost.format_warning(result))
        return
    
    if args.list_adapters:
        print(json.dumps(router.list_adapters(), ensure_ascii=False, indent=2))
        return
    
    if args.multi:
        # --parallel 參數：None=順序, "force"=強制並行, "auto"=自動, "never"=順序
        if args.parallel == "force":
            parallel_mode = True
        elif args.parallel == "auto":
            parallel_mode = "auto"
        else:  # None 或 "never"
            parallel_mode = False
        
        results = router.multi_route(args.task, parallel=parallel_mode)
        output = [r.to_dict() for r in results]
        print(json.dumps(output, ensure_ascii=False, indent=2))
        # 任一失敗就 exit 1
        if any(r.error for r in results):
            sys.exit(1)
    else:
        result = router.route(args.task, force_domain=args.force_domain)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        if result.error:
            sys.exit(1)


if __name__ == "__main__":
    main()
