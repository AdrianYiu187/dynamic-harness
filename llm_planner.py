"""
LLM 驅動的 plan 規劃器 — 把高階任務描述轉成 phase DAG

設計：
  - 給定高階任務（如「分析曼聯 vs 車路士的赔率」）
  - 呼叫 minimax API（OpenAI-compatible）生成 JSON plan
  - 解析回 Plan 物件

P3-3.2: 第一個真正的 LLM-as-planner 實作
"""
from __future__ import annotations
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import requests

from plan import Plan, Phase, parse_script_to_plan

log = logging.getLogger(__name__)


# 從 ~/.hermes/.env 載入 MINIMAX_API_KEY（fallback）
def _load_minimax_key() -> Optional[str]:
    # 1. env var
    key = os.environ.get("MINIMAX_API_KEY")
    if key:
        return key
    # 2. ~/.hermes/.env
    env_path = Path.home() / ".hermes" / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                if line.startswith("MINIMAX_API_KEY="):
                    return line.split("=", 1)[1].strip()
    return None


# 預設可用 domains
DEFAULT_DOMAINS = ["ft", "stock", "hkstock", "code", "general"]


SYSTEM_PROMPT = """你是 Hermes Agent 的 plan planner，專門把高階任務拆解成可平行執行的 phase DAG。

# 規則

1. **拆解粒度**：2-6 個 phases 為佳。太多 = 噪音；太少 = 沒 DAG 價值
2. **平行度優先**：能並行的就用 `depends_on: []` 拉出來。沒依賴的絕不寫成 depends_on: [n]
3. **保守語意**：
   - 真的需要前一個結果才能做下一個，才放 depends_on
   - 共識/彙總 phase 必須 depends_on 所有上游
4. **domain 標註**：每個 phase 必須有 `force_domain`（ft/stock/hkstock/code/general/...）
5. **sub_task 必須是可被 router.route() 處理的任務文字**：用 query 語氣
6. **不確定就保守**：拿不準順序的 phases，就不設 depends_on（讓 router 決定）

# 輸出格式

純 JSON（不要 markdown code fence，不要解釋），schema：
```json
{
  "rationale": "一句話說明為什麼這樣拆",
  "phases": [
    {
      "id": 1,
      "name": "短動詞 (如: 查赔率)",
      "sub_task": "router 可處理的 query 文字",
      "force_domain": "ft|stock|hkstock|code|general",
      "depends_on": []
    }
  ]
}
```

# 範例

輸入：「分析曼聯 vs 車路士的赔率」
輸出：
```json
{
  "rationale": "拆成 source data collection + consensus",
  "phases": [
    {"id": 1, "name": "查赔率", "sub_task": "曼聯 vs 車路士 赔率", "force_domain": "ft", "depends_on": []},
    {"id": 2, "name": "查近況", "sub_task": "曼聯 車路士 傷兵 近況", "force_domain": "ft", "depends_on": []},
    {"id": 3, "name": "共識分析", "sub_task": "曼聯 vs 車路士 共識", "force_domain": "ft", "depends_on": [1, 2]}
  ]
}
```

# 失敗模式

- ❌ 全部 phases depends_on: [前一個] → 退化成 sequential
- ❌ 只輸出 markdown 沒有 JSON → 解析失敗
- ❌ phases 沒給 force_domain → 路由失敗
- ❌ depends_on 寫成 [n-1] 而非 [id] → 無法解析
"""


@dataclass
class PlannerConfig:
    model: str = "MiniMax-M2.7-highspeed"
    base_url: str = "https://api.minimax.io/v1"
    api_key: Optional[str] = None
    temperature: float = 0.2
    max_tokens: int = 2000
    timeout: int = 60
    max_retries: int = 2

    def __post_init__(self):
        if not self.api_key:
            self.api_key = _load_minimax_key()


class LLMPlanner:
    """LLM-based plan generator"""
    
    def __init__(self, config: Optional[PlannerConfig] = None):
        self.config = config or PlannerConfig()
        if not self.config.api_key:
            raise RuntimeError(
                "MINIMAX_API_KEY not found. Set env var or add to ~/.hermes/.env"
            )
    
    def generate_plan(
        self,
        task: str,
        available_domains: Optional[List[str]] = None,
        context: Optional[str] = None,
    ) -> Plan:
        """把高階任務轉成 Plan object
        
        Args:
            task: 高階任務描述（自然語言）
            available_domains: 可用 domain list（會給 LLM 當 hint）
            context: 額外 context（如「這是給 FT Team Agent 用的」）
        
        Returns:
            Plan object（已填好 phases 和 id）
        
        Raises:
            RuntimeError: 若 LLM 無法生成有效 plan（parse 失敗 2 次）
        """
        domains = available_domains or DEFAULT_DOMAINS
        plan_dict = None
        last_error = None
        
        for attempt in range(self.config.max_retries + 1):
            try:
                response_text = self._call_llm(task, domains, context, attempt)
                plan_dict = self._parse_plan_json(response_text)
                if plan_dict and plan_dict.get("phases"):
                    break
            except Exception as e:
                last_error = e
                log.warning(f"LLM planner attempt {attempt+1} failed: {e}")
        
        if not plan_dict or not plan_dict.get("phases"):
            raise RuntimeError(
                f"LLM planner failed after {self.config.max_retries + 1} attempts. "
                f"Last error: {last_error}"
            )
        
        # 驗證
        plan_dict = self._validate_and_fix(plan_dict)
        
        # 轉成 Plan 物件
        return self._dict_to_plan(plan_dict, task)
    
    def _call_llm(
        self,
        task: str,
        domains: List[str],
        context: Optional[str],
        attempt: int,
    ) -> str:
        """呼叫 minimax API"""
        user_prompt = f"# 任務\n{task}\n\n# 可用 domains\n{', '.join(domains)}"
        if context:
            user_prompt += f"\n\n# Context\n{context}"
        if attempt > 0:
            user_prompt += f"\n\n# 注意\n你前一次的輸出無法解析為 JSON。請只輸出合法 JSON（不要 markdown，不要解釋），schema 見 system prompt。"
        
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        
        resp = requests.post(
            f"{self.config.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    
    def _parse_plan_json(self, text: str) -> Optional[Dict]:
        """從 LLM 輸出抽取 JSON（可能含 markdown fence 或前導說明）"""
        text = text.strip()
        
        # 嘗試 1: 整段就是 JSON
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        
        # 嘗試 2: 從 ```json ... ``` 抽取
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        
        # 嘗試 3: 找第一個 { 到最後一個 } 的平衡區段
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
        
        log.warning(f"Failed to parse JSON from LLM output: {text[:200]}")
        return None
    
    def _validate_and_fix(self, plan_dict: Dict) -> Dict:
        """驗證 + 自動修復常見 LLM 錯誤"""
        phases = plan_dict.get("phases", [])
        if not phases:
            raise ValueError("phases empty")
        
        seen_ids = set()
        for p in phases:
            # 確保 id 唯一
            pid = p.get("id")
            if pid is None or pid in seen_ids:
                p["id"] = max(seen_ids, default=0) + 1
            seen_ids.add(p["id"])
            
            # 確保 force_domain（fallback to general）
            if not p.get("force_domain"):
                p["force_domain"] = "general"
            
            # 確保 depends_on 是 list
            if "depends_on" not in p:
                p["depends_on"] = []
            if not isinstance(p["depends_on"], list):
                try:
                    p["depends_on"] = list(p["depends_on"])
                except Exception:
                    p["depends_on"] = []
            
            # 確保 name
            if not p.get("name"):
                p["name"] = f"Phase {p['id']}"
            
            # 確保 sub_task
            if not p.get("sub_task"):
                p["sub_task"] = p.get("name", f"Phase {p['id']}")
        
        # 過濾掉引用不存在 id 的 depends_on
        for p in phases:
            valid_deps = [d for d in p["depends_on"] if d in seen_ids and d != p["id"]]
            p["depends_on"] = valid_deps
        
        return plan_dict
    
    def _dict_to_plan(self, plan_dict: Dict, task: str) -> Plan:
        """把 LLM 輸出的 dict 轉成 Plan object（含 DSL script_source）"""
        phases = []
        for p in plan_dict["phases"]:
            phase = Phase(
                id=p["id"],
                name=p["name"],
                sub_task=p["sub_task"],
                force_domain=p.get("force_domain", "general"),
                depends_on=p.get("depends_on", []),
            )
            phases.append(phase)
        
        # 從 phases 生成 DSL script（給 script_source 用）
        script_lines = [f"# Task: {task}"]
        rationale = plan_dict.get("rationale", "")
        if rationale:
            script_lines.append(f"# Rationale: {rationale}")
        script_lines.append("")
        for p in phases:
            script_lines.append(f"# Phase {p.id}: {p.name}")
            script_lines.append(f"# domain: {p.force_domain}")
            if p.depends_on:
                script_lines.append(f"# depends_on: {p.depends_on}")
            script_lines.append(
                f'p{p.id} = router.route("{p.sub_task}", force_domain="{p.force_domain}")'
            )
            script_lines.append("")
        script_source = "\n".join(script_lines)
        
        return Plan(
            id=str(uuid.uuid4()),
            task_text=task,
            force_domain=None,
            created_at=time.time(),
            script_source=script_source,
            status="draft",
            metadata={
                "generated_by": "llm_planner",
                "model": self.config.model,
                "rationale": rationale,
                "phase_count": len(phases),
            },
            phases=phases,
        )


# ==================== 便利函式 ====================

def generate_plan_from_task(
    task: str,
    available_domains: Optional[List[str]] = None,
    config: Optional[PlannerConfig] = None,
) -> Plan:
    """便利函式：直接從 task 拿 Plan"""
    planner = LLMPlanner(config=config)
    return planner.generate_plan(task, available_domains=available_domains)


def generate_plan_script_from_task(
    task: str,
    available_domains: Optional[List[str]] = None,
    config: Optional[PlannerConfig] = None,
) -> str:
    """便利函式：從 task 拿 plan script 字串（DSL 格式）"""
    plan = generate_plan_from_task(task, available_domains, config)
    return plan.script_source
