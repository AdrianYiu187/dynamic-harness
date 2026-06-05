"""
Adversarial verifier — 對 LLM planner 生成的 plan 做獨立審核

設計：
  - Planner LLM（generator）拆任務
  - Verifier LLM（critic）獨立評估 plan 品質
  - 兩者分離 → 找出 generator 自我審核容易漏掉的問題

P3-3.3: Generator-Critic loop
"""
from __future__ import annotations
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests

from plan import Plan, Phase
from llm_planner import _load_minimax_key, DEFAULT_DOMAINS

log = logging.getLogger(__name__)


VERIFIER_SYSTEM_PROMPT = """你是 Hermes Agent 的 plan verifier（critic）。你的工作：獨立審核別的 LLM 生成的 plan，找出問題。

# 審核維度

1. **DAG 正確性**：cycle, 缺漏依賴, 不必要的依賴
2. **粒度**：phase 數是否合適（太少=沒拆, 太多=over-engineering）
3. **平行度**：可並行的 phase 是否真的並行（不該 depends_on）
4. **Domain 合理性**：每個 phase 的 force_domain 是否正確
5. **任務完整性**：給定高階任務，是否有 phase 漏掉（如共識、總結、verify）
6. **Sub-task 可執行性**：每個 phase 的 sub_task 文字是否可被 router.route() 處理
7. **命名**：phase name 是否清楚（短動詞 + 對象）

# 評分

- "pass": 沒問題，可直接執行
- "warn": 有次要問題，建議修改但不阻塞
- "fail": 有嚴重問題，必須重做

# 輸出格式

純 JSON（不要 markdown fence，不要解釋），schema：
```json
{
  "verdict": "pass|warn|fail",
  "confidence": 0.0-1.0,
  "issues": [
    {"severity": "critical|major|minor", "phase_id": 1, "category": "DAG|domain|granularity|...", "message": "..."}
  ],
  "suggestions": [
    "把 phase 3 和 phase 4 設成 depends_on: [] 讓它們並行"
  ],
  "summary": "一句話總結"
}
```
"""


@dataclass
class VerifierConfig:
    model: str = "MiniMax-M2.7-highspeed"
    base_url: str = "https://api.minimax.io/v1"
    api_key: Optional[str] = None
    temperature: float = 0.1
    max_tokens: int = 1500
    timeout: int = 60

    def __post_init__(self):
        if not self.api_key:
            self.api_key = _load_minimax_key()


@dataclass
class Verdict:
    """審核結果"""
    verdict: str  # pass / warn / fail
    confidence: float
    issues: List[Dict]
    suggestions: List[str]
    summary: str
    raw_response: Optional[str] = None  # 供 debug


class AdversarialVerifier:
    """對 plan 做獨立審核"""

    def __init__(self, config: Optional[VerifierConfig] = None):
        self.config = config or VerifierConfig()
        if not self.config.api_key:
            # Lazy check: verifier 可以在沒有 API key 時建立
            # - offline_only=True 的 verify() 不會打到 LLM
            # - offline_only=False 時 _llm_verify() 才會 raise
            # 這樣 CI 沒設 MINIMAX_API_KEY 也能跑 static check 測試
            import logging as _log
            _log.getLogger(__name__).warning(
                "MINIMAX_API_KEY not found — verifier will work offline-only. "
                "Set MINIMAX_API_KEY in env to enable LLM semantic checks."
            )
    
    def verify(
        self,
        plan: Plan,
        task: Optional[str] = None,
        available_domains: Optional[List[str]] = None,
        # 跳過 LLM，只跑 deterministic check
        offline_only: bool = False,
    ) -> Verdict:
        """審核 plan，回傳 Verdict
        
        Args:
            plan: 要審核的 plan
            task: 高階任務（給 verifier 當 context）
            available_domains: 可用 domain 列表
            offline_only: True → 跳過 LLM，只跑本地 static checks
        
        Returns:
            Verdict
        """
        # 1. 本地 static checks（一定會跑）
        static_issues = self._static_checks(plan, available_domains)
        
        # 2. 若 offline 或 static 已有 critical → 不打 LLM，直接 fail
        has_critical = any(i["severity"] == "critical" for i in static_issues)
        if offline_only or has_critical:
            return self._build_verdict_from_static(static_issues, plan)
        
        # 3. 呼叫 LLM 做 semantic check
        try:
            llm_verdict = self._llm_verify(plan, task, available_domains)
            # 合併 static + LLM
            return self._merge_verdicts(static_issues, llm_verdict)
        except Exception as e:
            log.warning(f"LLM verifier failed, falling back to static only: {e}")
            return self._build_verdict_from_static(static_issues, plan)
    
    # ==================== Static checks ====================
    
    def _static_checks(
        self,
        plan: Plan,
        available_domains: Optional[List[str]] = None,
    ) -> List[Dict]:
        """本地 deterministic 檢查（不需 LLM）"""
        issues = []
        domains = set(available_domains or DEFAULT_DOMAINS)
        
        # 1. Cycle 檢測
        if self._has_cycle(plan.phases):
            issues.append({
                "severity": "critical",
                "category": "DAG",
                "message": "Plan has circular dependency — must be acyclic",
            })
        
        # 2. depends_on 引用存在的 phase
        phase_ids = {p.id for p in plan.phases}
        for p in plan.phases:
            for dep in p.depends_on:
                if dep not in phase_ids:
                    issues.append({
                        "severity": "major",
                        "category": "DAG",
                        "phase_id": p.id,
                        "message": f"Phase {p.id} depends on non-existent phase {dep}",
                    })
                if dep == p.id:
                    issues.append({
                        "severity": "critical",
                        "category": "DAG",
                        "phase_id": p.id,
                        "message": f"Phase {p.id} depends on itself",
                    })
        
        # 3. force_domain 必填且在可用 list
        for p in plan.phases:
            if not p.force_domain:
                issues.append({
                    "severity": "major",
                    "category": "domain",
                    "phase_id": p.id,
                    "message": f"Phase {p.id} missing force_domain",
                })
            elif p.force_domain not in domains:
                issues.append({
                    "severity": "major",
                    "category": "domain",
                    "phase_id": p.id,
                    "message": f"Phase {p.id} uses unavailable domain '{p.force_domain}'",
                })
        
        # 4. Phase 數量
        n = len(plan.phases)
        if n == 0:
            issues.append({
                "severity": "critical",
                "category": "granularity",
                "message": "Plan has no phases",
            })
        elif n == 1:
            issues.append({
                "severity": "minor",
                "category": "granularity",
                "message": "Plan has only 1 phase — no DAG value, consider splitting",
            })
        elif n > 8:
            issues.append({
                "severity": "major",
                "category": "granularity",
                "message": f"Plan has {n} phases — may be over-engineered",
            })
        
        # 5. 平行度檢查：若 80% phases 都 depends_on 某個前驅 → warn
        if n >= 3:
            has_dep_count = sum(1 for p in plan.phases if p.depends_on)
            sequential_ratio = has_dep_count / n
            if sequential_ratio >= 0.8:
                issues.append({
                    "severity": "major",
                    "category": "parallelism",
                    "message": f"{int(sequential_ratio*100)}% of phases have dependencies — likely missing parallelization opportunities",
                })
        
        # 6. Sub-task 不能太短或太長
        for p in plan.phases:
            sub = p.sub_task.strip()
            if len(sub) < 2:
                issues.append({
                    "severity": "major",
                    "category": "executable",
                    "phase_id": p.id,
                    "message": f"Phase {p.id} sub_task too short: '{sub}'",
                })
            elif len(sub) > 200:
                issues.append({
                    "severity": "minor",
                    "category": "executable",
                    "phase_id": p.id,
                    "message": f"Phase {p.id} sub_task very long ({len(sub)} chars) — may be hard to route",
                })
        
        # 7. 孤兒 phase：有 phase depends_on 但整個 plan 沒 phase 有完成 (DEPRECATED，忽略)
        
        return issues
    
    def _has_cycle(self, phases: List[Phase]) -> bool:
        """DFS 檢測 cycle"""
        graph = {p.id: list(p.depends_on) for p in phases}
        
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {p.id: WHITE for p in phases}
        
        def dfs(node):
            color[node] = GRAY
            for dep in graph.get(node, []):
                if dep not in color:
                    continue
                if color[dep] == GRAY:
                    return True
                if color[dep] == WHITE and dfs(dep):
                    return True
            color[node] = BLACK
            return False
        
        for p in phases:
            if color[p.id] == WHITE and dfs(p.id):
                return True
        return False
    
    # ==================== LLM verify ====================
    
    def _llm_verify(
        self,
        plan: Plan,
        task: Optional[str],
        available_domains: Optional[List[str]],
    ) -> Verdict:
        """呼叫 LLM 審核 plan（semantic check）"""
        # 準備 prompt
        plan_summary = {
            "id": plan.id,
            "task": task or plan.task_text,
            "available_domains": available_domains or DEFAULT_DOMAINS,
            "phases": [
                {
                    "id": p.id,
                    "name": p.name,
                    "sub_task": p.sub_task,
                    "force_domain": p.force_domain,
                    "depends_on": p.depends_on,
                }
                for p in plan.phases
            ],
        }
        
        user_prompt = (
            f"# 高階任務\n{task or plan.task_text}\n\n"
            f"# 生成的 plan\n```json\n{json.dumps(plan_summary, ensure_ascii=False, indent=2)}\n```\n\n"
            f"# 請審核\n"
            f"依照 system prompt 的評分標準，回傳 JSON verdict。"
        )
        
        resp = requests.post(
            f"{self.config.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": VERIFIER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens,
            },
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        
        # Parse
        verdict_dict = self._parse_json(text)
        if not verdict_dict:
            raise RuntimeError(f"Failed to parse verifier response: {text[:200]}")
        
        return Verdict(
            verdict=verdict_dict.get("verdict", "warn"),
            confidence=float(verdict_dict.get("confidence", 0.5)),
            issues=verdict_dict.get("issues", []),
            suggestions=verdict_dict.get("suggestions", []),
            summary=verdict_dict.get("summary", ""),
            raw_response=text,
        )
    
    def _parse_json(self, text: str) -> Optional[Dict]:
        """從 LLM 輸出抽 JSON"""
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        
        start = text.find("{")
        if start >= 0:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i+1])
                        except json.JSONDecodeError:
                            break
        return None
    
    # ==================== Merge ====================
    
    def _merge_verdicts(self, static: List[Dict], llm: Verdict) -> Verdict:
        """合併 static + LLM verdict（取最嚴重的）"""
        all_issues = list(static) + list(llm.issues)
        
        # 最高 severity wins
        has_critical = any(i.get("severity") == "critical" for i in all_issues)
        has_major = any(i.get("severity") == "major" for i in all_issues)
        
        if has_critical or llm.verdict == "fail":
            final = "fail"
        elif has_major or llm.verdict == "warn":
            final = "warn"
        else:
            final = "pass"
        
        return Verdict(
            verdict=final,
            confidence=llm.confidence,
            issues=all_issues,
            suggestions=llm.suggestions,
            summary=llm.summary,
            raw_response=llm.raw_response,
        )
    
    def _build_verdict_from_static(self, static: List[Dict], plan: Plan) -> Verdict:
        """純 static 結果的 Verdict"""
        has_critical = any(i.get("severity") == "critical" for i in static)
        has_major = any(i.get("severity") == "major" for i in static)
        
        if has_critical:
            verdict = "fail"
        elif has_major:
            verdict = "warn"
        else:
            verdict = "pass"
        
        # 從 issues 組 summary
        summary = (
            f"Static check: {len(static)} issue(s) — "
            f"{sum(1 for i in static if i.get('severity') == 'critical')} critical, "
            f"{sum(1 for i in static if i.get('severity') == 'major')} major, "
            f"{sum(1 for i in static if i.get('severity') == 'minor')} minor"
        )
        
        return Verdict(
            verdict=verdict,
            confidence=1.0,  # static 是確定的
            issues=static,
            suggestions=[],
            summary=summary,
            raw_response=None,
        )


# ==================== 便利函式 ====================

def verify_plan(
    plan: Plan,
    task: Optional[str] = None,
    available_domains: Optional[List[str]] = None,
    offline_only: bool = False,
) -> Verdict:
    """便利函式：審核 plan"""
    verifier = AdversarialVerifier()
    return verifier.verify(plan, task=task, available_domains=available_domains, offline_only=offline_only)


def verify_and_print(plan: Plan, task: Optional[str] = None, **kwargs) -> Verdict:
    """便利函式：審核 + 漂亮 print"""
    verdict = verify_plan(plan, task=task, **kwargs)
    print(f"\n{'='*60}")
    print(f"  Verdict: {verdict.verdict.upper()} (confidence: {verdict.confidence:.0%})")
    print(f"  Summary: {verdict.summary}")
    print(f"{'='*60}")
    if verdict.issues:
        print(f"\n  Issues ({len(verdict.issues)}):")
        for i, iss in enumerate(verdict.issues, 1):
            sev = iss.get("severity", "minor").upper()
            cat = iss.get("category", "")
            pid = iss.get("phase_id", "-")
            print(f"    {i}. [{sev}/{cat}] phase {pid}: {iss.get('message', '')}")
    if verdict.suggestions:
        print(f"\n  Suggestions:")
        for s in verdict.suggestions:
            print(f"    • {s}")
    return verdict
