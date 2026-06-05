"""
Plan template library — 預定義可複用的 plan 範本

設計：
  - 5 個內建範本覆蓋常見場景（FT/股票/代碼/多市場/debug）
  - 用 trigger 詞 regex 自動選 template
  - 用 instantiate(task) 動態填入 sub_task

P3-3.4: 讓 LLM 不必每次從零生成 plan
"""
from __future__ import annotations
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from plan import Plan, Phase


# ==================== Template 結構 ====================

@dataclass
class PlanTemplate:
    """可複用的 plan 範本"""
    name: str                                  # e.g. "ft_match_analysis"
    description: str                           # 一句話說明
    trigger_patterns: List[str]                # regex patterns；任一 match 就用此 template
    required_domains: List[str]                # 此 template 用到哪些 domain
    phase_specs: List[Dict] = field(default_factory=list)
    # phase_spec schema: {name, sub_task_template, force_domain, depends_on}
    # sub_task_template 可用 {task} 變數
    rationale: str = ""
    
    def matches(self, task: str) -> bool:
        for pattern in self.trigger_patterns:
            if re.search(pattern, task, re.IGNORECASE):
                return True
        return False
    
    def instantiate(self, task: str) -> Plan:
        """用 task 填入 template，回 Plan"""
        phases = []
        for i, spec in enumerate(self.phase_specs, 1):
            sub = spec["sub_task_template"].format(task=task)
            phases.append(Phase(
                id=i,
                name=spec["name"],
                sub_task=sub,
                force_domain=spec.get("force_domain", "general"),
                depends_on=spec.get("depends_on", []),
            ))
        
        # 組 DSL script
        script_lines = [f"# Task: {task}", f"# Template: {self.name}", f"# {self.description}"]
        if self.rationale:
            script_lines.append(f"# Rationale: {self.rationale}")
        script_lines.append("")
        for p in phases:
            script_lines.append(f"# Phase {p.id}: {p.name}")
            if p.depends_on:
                script_lines.append(f"# depends_on: {p.depends_on}")
            script_lines.append(f'p{p.id} = router.route("{p.sub_task}", force_domain="{p.force_domain}")')
            script_lines.append("")
        
        return Plan(
            id=str(uuid.uuid4()),
            task_text=task,
            force_domain=None,
            created_at=time.time(),
            script_source="\n".join(script_lines),
            status="draft",
            metadata={
                "generated_by": "template",
                "template": self.name,
                "rationale": self.rationale,
                "phase_count": len(phases),
            },
            phases=phases,
        )


# ==================== 內建 templates ====================

# 1. FT 足球比賽分析
FT_MATCH_ANALYSIS = PlanTemplate(
    name="ft_match_analysis",
    description="足球比賽深度分析：赔率 + 傷兵 + 歷史 + 共識",
    trigger_patterns=[
        r"分析.*?赔率|赔率.*?分析",
        r"分析.*?vs|分析.*?對",
        r"足球.*?分析|[賽赛].*?分析",
        r"who.*?win|odds.*?analysis",
    ],
    required_domains=["ft"],
    rationale="數據收集（赔率/傷兵/歷史）可並行；共識需等所有數據",
    phase_specs=[
        {
            "name": "查赔率",
            "sub_task_template": "{task} 赔率",
            "force_domain": "ft",
            "depends_on": [],
        },
        {
            "name": "查傷兵近況",
            "sub_task_template": "{task} 傷兵 近況",
            "force_domain": "ft",
            "depends_on": [],
        },
        {
            "name": "查歷史對戰",
            "sub_task_template": "{task} 歷史 H2H",
            "force_domain": "ft",
            "depends_on": [],
        },
        {
            "name": "綜合共識",
            "sub_task_template": "{task} 綜合分析 共識",
            "force_domain": "ft",
            "depends_on": [1, 2, 3],
        },
    ],
)


# 2. 股票深度研究
STOCK_DEEP_RESEARCH = PlanTemplate(
    name="stock_deep_research",
    description="個股深度研究：技術 + 基本 + 新聞 + 評等",
    trigger_patterns=[
        r"深度分析.*?(股|個股|股票)",
        r"研究.*?股",
        r"invest.*?stock|analyze.*?stock",
        r"\d{4,6}.*?分析",  # 股票代號 + 分析
    ],
    required_domains=["stock"],
    rationale="技術/基本/新聞 三條獨立資料流可並行；評等需整合",
    phase_specs=[
        {
            "name": "技術面",
            "sub_task_template": "{task} 技術分析 K線 指標",
            "force_domain": "stock",
            "depends_on": [],
        },
        {
            "name": "基本面",
            "sub_task_template": "{task} 基本面 財報 估值",
            "force_domain": "stock",
            "depends_on": [],
        },
        {
            "name": "新聞面",
            "sub_task_template": "{task} 最新新聞 市場情緒",
            "force_domain": "stock",
            "depends_on": [],
        },
        {
            "name": "綜合評等",
            "sub_task_template": "{task} 綜合評等 投資建議",
            "force_domain": "stock",
            "depends_on": [1, 2, 3],
        },
    ],
)


# 3. 代碼重構
CODE_REFACTOR = PlanTemplate(
    name="code_refactor",
    description="代碼重構：理解 + 測試 + 重構 + 驗證",
    trigger_patterns=[
        r"重構|refactor",
        r"重寫.*?代碼|重寫.*?code",
        r"代碼.*?優化|優化.*?代碼",
    ],
    required_domains=["code"],
    rationale="理解/測試可先並行；重構需理解完成；驗證需重構完成",
    phase_specs=[
        {
            "name": "理解代碼",
            "sub_task_template": "{task} 代碼理解 結構",
            "force_domain": "code",
            "depends_on": [],
        },
        {
            "name": "建立測試",
            "sub_task_template": "{task} 測試覆蓋 baseline",
            "force_domain": "code",
            "depends_on": [],
        },
        {
            "name": "重構代碼",
            "sub_task_template": "{task} 重構實作",
            "force_domain": "code",
            "depends_on": [1, 2],
        },
        {
            "name": "驗證測試",
            "sub_task_template": "{task} 測試驗證",
            "force_domain": "code",
            "depends_on": [3],
        },
    ],
)


# 4. 多市場比較
MULTI_MARKET_COMPARE = PlanTemplate(
    name="multi_market_compare",
    description="多市場並行：FT + 股票 + Crypto（無依賴）",
    trigger_patterns=[
        r"多市場|跨市場",
        r"multi.*?market|cross.*?market",
    ],
    required_domains=["ft", "stock", "general"],
    rationale="多個獨立市場的查詢，彼此無依賴，全部並行",
    phase_specs=[
        {
            "name": "足球市場",
            "sub_task_template": "{task} 足球",
            "force_domain": "ft",
            "depends_on": [],
        },
        {
            "name": "股票市場",
            "sub_task_template": "{task} 股票",
            "force_domain": "stock",
            "depends_on": [],
        },
        {
            "name": "Crypto 市場",
            "sub_task_template": "{task} 加密貨幣",
            "force_domain": "general",
            "depends_on": [],
        },
    ],
)


# 5. Investigation / Debug
INVESTIGATION = PlanTemplate(
    name="investigation",
    description="調查/除錯：症狀 + 重現 + 假設 + 驗證",
    trigger_patterns=[
        r"調查|investigate|debug",
        r"為什麼.*?失敗|why.*?fail",
        r"找.*?原因|find.*?cause",
    ],
    required_domains=["code", "general"],
    rationale="症狀記錄可獨立；重現需症狀；多個假設可並行驗證",
    phase_specs=[
        {
            "name": "記錄症狀",
            "sub_task_template": "{task} 症狀記錄 現象",
            "force_domain": "general",
            "depends_on": [],
        },
        {
            "name": "重現問題",
            "sub_task_template": "{task} 重現步驟 最小案例",
            "force_domain": "code",
            "depends_on": [1],
        },
        {
            "name": "假設 A",
            "sub_task_template": "{task} 假設 排查方向 1",
            "force_domain": "code",
            "depends_on": [2],
        },
        {
            "name": "假設 B",
            "sub_task_template": "{task} 假設 排查方向 2",
            "force_domain": "code",
            "depends_on": [2],
        },
        {
            "name": "結論",
            "sub_task_template": "{task} 根因 結論 修法",
            "force_domain": "code",
            "depends_on": [3, 4],
        },
    ],
)


# ==================== Library ====================

class TemplateLibrary:
    """範本庫 + 自動匹配"""
    
    DEFAULT_TEMPLATES = [
        FT_MATCH_ANALYSIS,
        STOCK_DEEP_RESEARCH,
        CODE_REFACTOR,
        MULTI_MARKET_COMPARE,
        INVESTIGATION,
    ]
    
    def __init__(self, templates: Optional[List[PlanTemplate]] = None):
        self.templates = templates or list(self.DEFAULT_TEMPLATES)
    
    def find_match(self, task: str) -> Optional[PlanTemplate]:
        """找第一個 match 的 template"""
        for tpl in self.templates:
            if tpl.matches(task):
                return tpl
        return None
    
    def find_all_matches(self, task: str) -> List[PlanTemplate]:
        """找所有 match 的 template"""
        return [tpl for tpl in self.templates if tpl.matches(task)]
    
    def instantiate(self, task: str, template_name: Optional[str] = None) -> Optional[Plan]:
        """用指定 template（或自動 match）生成 Plan
        
        Returns:
            Plan if matched, None if no match
        """
        if template_name:
            tpl = next((t for t in self.templates if t.name == template_name), None)
        else:
            tpl = self.find_match(task)
        
        if tpl is None:
            return None
        return tpl.instantiate(task)
    
    def list_templates(self) -> List[Dict]:
        """列出所有 template 的 metadata"""
        return [
            {
                "name": t.name,
                "description": t.description,
                "trigger_patterns": t.trigger_patterns,
                "required_domains": t.required_domains,
                "phase_count": len(t.phase_specs),
            }
            for t in self.templates
        ]


# ==================== 便利函式 ====================

_default_library = TemplateLibrary()


def get_library() -> TemplateLibrary:
    """Get default library singleton"""
    return _default_library


def instantiate_template(task: str, template_name: Optional[str] = None) -> Optional[Plan]:
    """便利函式：從 task 拿 Plan（若無 match 回 None）"""
    return _default_library.instantiate(task, template_name=template_name)
