"""
Plan template library 測試
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from template_library import (
    PlanTemplate, TemplateLibrary, get_library,
    instantiate_template, FT_MATCH_ANALYSIS, STOCK_DEEP_RESEARCH,
    CODE_REFACTOR, MULTI_MARKET_COMPARE, INVESTIGATION,
)


# ==================== Template 單元測試 ====================

def test_ft_template_matches():
    """[1] FT template 觸發"""
    tpl = FT_MATCH_ANALYSIS
    
    cases_should_match = [
        "分析曼聯 vs 車路士的赔率",
        "賽前分析",
        "who will win",
    ]
    cases_should_not = [
        "寫個 hello world",
        "查 BTC 現價",
    ]
    
    ok = all(tpl.matches(t) for t in cases_should_match)
    ok = ok and all(not tpl.matches(t) for t in cases_should_not)
    print(f"  FT template match: {ok} {'✅' if ok else '❌'}")
    return ok


def test_stock_template_matches():
    """[2] Stock template 觸發"""
    tpl = STOCK_DEEP_RESEARCH
    
    cases = [
        "深度分析台積電股票",
        "研究個股 2330",
        "analyze stock AAPL",
        "2330 股票分析",
    ]
    ok = all(tpl.matches(t) for t in cases)
    print(f"  Stock template match: {ok} {'✅' if ok else '❌'}")
    return ok


def test_code_template_matches():
    """[3] Code refactor template 觸發"""
    tpl = CODE_REFACTOR
    
    cases = [
        "重構 plan.py",
        "refactor executor",
        "代碼優化",
        "重寫 code 模組",
    ]
    ok = all(tpl.matches(t) for t in cases)
    print(f"  Code template match: {ok} {'✅' if ok else '❌'}")
    return ok


def test_investigation_template_matches():
    """[4] Investigation template 觸發"""
    tpl = INVESTIGATION
    
    cases = [
        "調查為什麼 test 失敗",
        "debug 系統崩潰",
        "找 OOM 原因",
    ]
    ok = all(tpl.matches(t) for t in cases)
    print(f"  Investigation template match: {ok} {'✅' if ok else '❌'}")
    return ok


def test_template_instantiate_creates_plan():
    """[5] Template 實例化產生合法 Plan"""
    tpl = FT_MATCH_ANALYSIS
    plan = tpl.instantiate("曼聯 vs 車路士")
    
    checks = [
        plan.task_text == "曼聯 vs 車路士",
        len(plan.phases) == 4,
        plan.phases[0].depends_on == [],
        plan.phases[1].depends_on == [],
        plan.phases[2].depends_on == [],
        plan.phases[3].depends_on == [1, 2, 3],  # 共識 depends on 三個 source
        "曼聯 vs 車路士" in plan.phases[0].sub_task,
        "router.route" in plan.script_source,
        plan.metadata.get("template") == "ft_match_analysis",
    ]
    ok = all(checks)
    print(f"  FT instantiate: {len(plan.phases)} phases, deps[3]={plan.phases[3].depends_on} {'✅' if ok else '❌'}")
    if not ok:
        for i, c in enumerate(checks):
            if not c:
                print(f"    ❌ check {i} failed")
    return ok


def test_template_instantiate_stock():
    """[6] Stock template 實例化"""
    plan = STOCK_DEEP_RESEARCH.instantiate("2330 台積電")
    
    checks = [
        len(plan.phases) == 4,
        plan.phases[0].name == "技術面",
        plan.phases[1].name == "基本面",
        plan.phases[2].name == "新聞面",
        plan.phases[3].name == "綜合評等",
        plan.phases[3].depends_on == [1, 2, 3],
        "2330 台積電" in plan.phases[0].sub_task,
    ]
    ok = all(checks)
    print(f"  Stock instantiate: {'✅' if ok else '❌'}")
    return ok


def test_template_instantiate_investigation():
    """[7] Investigation template 實例化（含 diamond DAG）"""
    plan = INVESTIGATION.instantiate("API timeout")
    
    # 結構: 1 → 2 → (3, 4) → 5
    checks = [
        len(plan.phases) == 5,
        plan.phases[0].depends_on == [],
        plan.phases[1].depends_on == [1],
        plan.phases[2].depends_on == [2],  # 假設 A
        plan.phases[3].depends_on == [2],  # 假設 B (與 A 並行)
        plan.phases[4].depends_on == [3, 4],  # 結論
    ]
    ok = all(checks)
    print(f"  Investigation instantiate: deps={[p.depends_on for p in plan.phases]} {'✅' if ok else '❌'}")
    return ok


# ==================== Library 測試 ====================

def test_library_finds_first_match():
    """[8] Library 自動 match 第一個 hit"""
    lib = TemplateLibrary()
    
    plan = lib.instantiate("分析曼聯 vs 車路士的赔率")
    
    checks = [
        plan is not None,
        plan.metadata.get("template") == "ft_match_analysis",
    ]
    ok = all(checks)
    print(f"  Library auto-match FT: template={plan.metadata.get('template') if plan else None} {'✅' if ok else '❌'}")
    return ok


def test_library_no_match_returns_none():
    """[9] 無 match 時回 None"""
    lib = TemplateLibrary()
    plan = lib.instantiate("講個笑話")
    
    ok = plan is None
    print(f"  Library no match: {plan} {'✅' if ok else '❌'}")
    return ok


def test_library_named_template():
    """[10] Library 用指定 template 名稱"""
    lib = TemplateLibrary()
    
    # 即使 task 不觸發 stock，指定也應該 work
    plan = lib.instantiate("foo bar", template_name="stock_deep_research")
    
    checks = [
        plan is not None,
        plan.metadata.get("template") == "stock_deep_research",
    ]
    ok = all(checks)
    print(f"  Library named: template={plan.metadata.get('template') if plan else None} {'✅' if ok else '❌'}")
    return ok


def test_library_list_templates():
    """[11] List 全部 5 個 template"""
    lib = TemplateLibrary()
    templates = lib.list_templates()
    
    checks = [
        len(templates) == 5,
        {t["name"] for t in templates} == {
            "ft_match_analysis", "stock_deep_research",
            "code_refactor", "multi_market_compare", "investigation",
        },
    ]
    ok = all(checks)
    print(f"  Library list: {len(templates)} templates {'✅' if ok else '❌'}")
    return ok


def test_library_finds_all_matches():
    """[12] 找出所有 match 的 templates"""
    lib = TemplateLibrary()
    
    # "debug 為什麼失敗" 可能同時 match investigation + code_refactor
    matches = lib.find_all_matches("debug 為什麼 refactor 失敗")
    names = [m.name for m in matches]
    
    ok = len(matches) >= 2  # 至少 debug + refactor
    print(f"  Library find-all: {names} {'✅' if ok else '❌'}")
    return ok


def test_custom_templates():
    """[13] 自訂 template"""
    custom = PlanTemplate(
        name="custom_test",
        description="test",
        trigger_patterns=[r"hello.*?test"],
        required_domains=["general"],
        phase_specs=[
            {"name": "step 1", "sub_task_template": "{task} 1", "force_domain": "general", "depends_on": []},
            {"name": "step 2", "sub_task_template": "{task} 2", "force_domain": "general", "depends_on": [1]},
        ],
    )
    lib = TemplateLibrary([custom])  # 只用 custom
    plan = lib.instantiate("hello test")
    
    ok = plan is not None and plan.metadata.get("template") == "custom_test" and len(plan.phases) == 2
    print(f"  Custom template: phases={len(plan.phases) if plan else 0} {'✅' if ok else '❌'}")
    return ok


# ==================== DSL script 驗證 ====================

def test_template_dsl_runs_through_parse_script():
    """[14] Template 生成的 DSL script 真的可被 parse_script_to_plan 解析"""
    from plan import parse_script_to_plan
    
    plan = FT_MATCH_ANALYSIS.instantiate("曼聯 vs 車路士")
    
    # parse 回來應該得到等價的 plan
    reparsed = parse_script_to_plan(plan.script_source, task_text=plan.task_text)
    
    checks = [
        reparsed is not None,
        len(reparsed.phases) == len(plan.phases),
        reparsed.phases[0].sub_task == plan.phases[0].sub_task,
        reparsed.phases[3].depends_on == [1, 2, 3],
    ]
    ok = all(checks)
    print(f"  DSL reparse: {len(reparsed.phases)} phases {'✅' if ok else '❌'}")
    return ok


def test_default_singleton():
    """[15] 預設 singleton library"""
    lib1 = get_library()
    lib2 = get_library()
    
    ok = lib1 is lib2 and len(lib1.templates) == 5
    print(f"  Default singleton: {len(lib1.templates)} templates {'✅' if ok else '❌'}")
    return ok


# ==================== Main ====================

TESTS = [
    ("ft_template_matches", test_ft_template_matches),
    ("stock_template_matches", test_stock_template_matches),
    ("code_template_matches", test_code_template_matches),
    ("investigation_template_matches", test_investigation_template_matches),
    ("template_instantiate_creates_plan", test_template_instantiate_creates_plan),
    ("template_instantiate_stock", test_template_instantiate_stock),
    ("template_instantiate_investigation", test_template_instantiate_investigation),
    ("library_finds_first_match", test_library_finds_first_match),
    ("library_no_match_returns_none", test_library_no_match_returns_none),
    ("library_named_template", test_library_named_template),
    ("library_list_templates", test_library_list_templates),
    ("library_finds_all_matches", test_library_finds_all_matches),
    ("custom_templates", test_custom_templates),
    ("template_dsl_runs_through_parse_script", test_template_dsl_runs_through_parse_script),
    ("default_singleton", test_default_singleton),
]


def main():
    print("=" * 70)
    print("Plan Template Library 測試")
    print("=" * 70)
    
    passed = 0
    total = len(TESTS)
    for name, fn in TESTS:
        print(f"\n[{name}]")
        try:
            if fn():
                passed += 1
        except Exception as e:
            print(f"  ❌ EXCEPTION: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
    
    print()
    print("=" * 70)
    print(f"總計: {passed}/{total} 通過")
    print("=" * 70)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
