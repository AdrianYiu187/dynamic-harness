"""
P3-3 整合測試：template → LLM-generate (offline) → verify → execute (mock)
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from plan import Plan, Phase, parse_script_to_plan
from template_library import instantiate_template, get_library
from adversarial_verifier import verify_plan, Verdict


# ==================== 整合測試 ====================

def test_end_to_end_template_to_verified_plan():
    """[1] Template → verify → 通過"""
    plan = instantiate_template("分析曼聯 vs 車路士的赔率")
    assert plan is not None
    
    verdict = verify_plan(plan, offline_only=True)
    
    checks = [
        plan.metadata.get("template") == "ft_match_analysis",
        len(plan.phases) == 4,
        verdict.verdict in ("pass", "warn"),
    ]
    ok = all(checks)
    print(f"  E2E template→verify: {len(plan.phases)} phases, verdict={verdict.verdict} {'✅' if ok else '❌'}")
    return ok


def test_end_to_end_template_to_executable_plan():
    """[2] Template 生成的 Plan 可走 round-trip"""
    plan = instantiate_template("debug 系統崩潰")
    assert plan is not None
    
    # Re-parse
    reparsed = parse_script_to_plan(plan.script_source, task_text=plan.task_text)
    
    checks = [
        reparsed is not None,
        len(reparsed.phases) == len(plan.phases),
        reparsed.phases[0].sub_task == plan.phases[0].sub_task,
    ]
    ok = all(checks)
    print(f"  E2E template→DSL→reparse: {len(reparsed.phases)} phases {'✅' if ok else '❌'}")
    return ok


def test_all_5_templates_pass_verification():
    """[3] 5 個 template 都至少 WARN 以上"""
    lib = get_library()
    
    results = []
    for tpl_info in lib.list_templates():
        tpl = next(t for t in lib.templates if t.name == tpl_info["name"])
        
        # 用對應的 task
        sample_tasks = {
            "ft_match_analysis": "分析曼聯 vs 車路士的赔率",
            "stock_deep_research": "深度分析 2330 個股",
            "code_refactor": "重構 plan.py 模組",
            "multi_market_compare": "多市場分析",
            "investigation": "debug 為什麼失敗",
        }
        plan = tpl.instantiate(sample_tasks[tpl.name])
        verdict = verify_plan(plan, offline_only=True)
        results.append((tpl.name, verdict.verdict))
    
    # 全部要非 fail（其實有些 investigation template 有 5 階段 sequential 80% 邊界，可能會 warn）
    all_ok = all(v != "fail" for _, v in results)
    print(f"  All 5 templates: {results}")
    print(f"  All non-FAIL: {all_ok} {'✅' if all_ok else '❌'}")
    return all_ok


def test_stock_template_uses_correct_substitutions():
    """[4] Stock template 變數替換正確"""
    plan = instantiate_template("深度分析 2330 個股", template_name="stock_deep_research")
    
    # 第一階段 sub_task 應含 2330
    has_var = "2330" in plan.phases[0].sub_task
    
    ok = has_var
    print(f"  Stock variable substitution: '2330' in sub_task: {has_var} {'✅' if ok else '❌'}")
    return ok


def test_investigation_template_has_diamond_dag():
    """[5] Investigation template 為 diamond DAG，可被 dataflow executor 識別"""
    plan = instantiate_template("debug 為什麼失敗", template_name="investigation")
    
    # 手動算 level（沒有 public build_dag）
    levels = {}
    def level_of(p):
        if p.id in levels: return levels[p.id]
        if not p.depends_on: levels[p.id] = 0; return 0
        max_dep = max(level_of(next(q for q in plan.phases if q.id == d)) for d in p.depends_on)
        levels[p.id] = max_dep + 1
        return levels[p.id]
    for p in plan.phases: level_of(p)
    dag = levels
    print(dag)
    
    # 應有 5 個 node
    has_5_nodes = len(dag) == 5
    
    # 5 階段依賴: 1, 2, 3&4, 5 → topological order 1→2→{3,4}→5
    # Level 0: phase 1
    # Level 1: phase 2
    # Level 2: phase 3, 4 (parallel)
    # Level 3: phase 5
    level_0 = [n for n, lvl in dag.items() if lvl == 0]
    level_1 = [n for n, lvl in dag.items() if lvl == 1]
    level_2 = [n for n, lvl in dag.items() if lvl == 2]
    level_3 = [n for n, lvl in dag.items() if lvl == 3]
    
    checks = [
        has_5_nodes,
        level_0 == [1],
        level_1 == [2],
        sorted(level_2) == [3, 4],  # parallel
        level_3 == [5],
    ]
    ok = all(checks)
    print(f"  Investigation DAG levels: {[(n, dag[n]) for n in sorted(dag)]} {'✅' if ok else '❌'}")
    return ok


def test_template_vs_llm_path_compatibility():
    """[6] Template 路徑與 LLM 路徑產出可互通"""
    # Template 出的 plan
    tpl_plan = instantiate_template("分析曼聯 vs 車路士的赔率")
    
    # LLM 出的 plan (模擬)
    llm_dsl = """
# Task: 分析曼聯 vs 車路士的赔率
p1 = router.route("分析曼聯 vs 車路士的赔率 赔率", force_domain="ft")
p2 = router.route("分析曼聯 vs 車路士的赔率 傷兵", force_domain="ft")
p3 = router.route("分析曼聯 vs 車路士的赔率 H2H", force_domain="ft")
p4 = router.route("分析曼聯 vs 車路士的赔率 綜合", force_domain="ft", depends_on=[1, 2, 3])
"""
    llm_plan = parse_script_to_plan(llm_dsl, task_text="分析曼聯 vs 車路士的赔率")
    
    # 兩個都應能被 verify
    tpl_verdict = verify_plan(tpl_plan, offline_only=True)
    llm_verdict = verify_plan(llm_plan, offline_only=True)
    
    checks = [
        tpl_verdict.verdict != "fail",
        llm_verdict.verdict != "fail",
        len(tpl_plan.phases) == len(llm_plan.phases) == 4,
    ]
    ok = all(checks)
    print(f"  Template/LLM parity: tpl={tpl_verdict.verdict} llm={llm_verdict.verdict} {'✅' if ok else '❌'}")
    return ok


def test_fallback_chain():
    """[7] 完整 fallback chain: Library → LLM → None"""
    lib = get_library()
    
    # 1. Library match
    p1 = lib.instantiate("分析曼聯 vs 車路士的赔率")
    assert p1 is not None and p1.metadata.get("generated_by") == "template"
    
    # 2. Library miss → 需要 LLM
    p2 = lib.instantiate("幫我寫一首關於星空的詩")
    assert p2 is None  # library miss
    
    # 3. 在實際系統中這裡會 call LLM; 這裡只 mock
    fallback_plan = Plan(
        id="fallback-1",
        task_text="幫我寫一首關於星空的詩",
        force_domain="general",
        created_at=time.time(),
        script_source='# mock',
        status="draft",
        phases=[Phase(id=1, name="寫詩", sub_task="幫我寫一首關於星空的詩", force_domain="general", depends_on=[])],
        metadata={"generated_by": "llm"},
    )
    
    checks = [
        p1.metadata.get("generated_by") == "template",
        p2 is None,
        fallback_plan.metadata.get("generated_by") == "llm",
    ]
    ok = all(checks)
    print(f"  Fallback chain: template={p1.metadata.get('generated_by')}, miss={'ok' if p2 is None else 'fail'}, llm_fallback={fallback_plan.metadata.get('generated_by')} {'✅' if ok else '❌'}")
    return ok


def test_verification_catches_introduced_defects():
    """[8] 在好的 plan 注入 defect，verifier 應能抓到"""
    # 先有個好 plan
    plan = instantiate_template("分析曼聯 vs 車路士的赔率")
    
    clean_verdict = verify_plan(plan, offline_only=True)
    
    # 注入 cycle
    plan.phases[0].depends_on = [4]  # phase 1 depends on phase 4 (creates cycle 1→4→3→1)
    plan.phases[3].depends_on = [1, 2, 3]  # 仍 depends_on 3
    
    # 3 depends on 2, 2 depends on 1, 1 depends on 4, 4 depends on 3 → cycle
    plan.phases[1].depends_on = [2]  # 維持
    plan.phases[2].depends_on = [1]  # 2 改 depends on 1
    
    # 1 → 4 → 3 → 1 (cycle via 3)
    # 1.depends_on = [4]
    # 2.depends_on = [1]
    # 3.depends_on = [2]
    # 4.depends_on = [3]  (need to break original [1,2,3] to avoid requiring all)
    plan.phases[3].depends_on = [3]  # self-cycle
    
    # Self-cycle
    plan.phases[3].depends_on = [3]
    
    # 重新檢查
    bad_verdict = verify_plan(plan, offline_only=True)
    
    checks = [
        clean_verdict.verdict != "fail",
        bad_verdict.verdict == "fail",
    ]
    ok = all(checks)
    print(f"  Defect injection: clean={clean_verdict.verdict} bad={bad_verdict.verdict} {'✅' if ok else '❌'}")
    return ok


def test_phase_count_in_template_variants():
    """[9] 5 個 template 的 phase 數量與 DAG 並行性符合預期"""
    lib = get_library()
    
    expected = {
        "ft_match_analysis": (4, 3),  # 4 phases, 3 in parallel at level 0
        "stock_deep_research": (4, 3),
        "code_refactor": (4, 2),  # phase 1 + 2 in parallel
        "multi_market_compare": (3, 3),  # all parallel
        "investigation": (5, 1),  # 1, 2 sequential, then 3,4 parallel
    }
    
    all_ok = True
    for tpl in lib.templates:
        if tpl.name not in expected:
            continue
        exp_count, exp_parallel = expected[tpl.name]
        actual_count = len(tpl.phase_specs)
        # Count phase 1's depends_on
        actual_parallel = sum(1 for p in tpl.phase_specs if not p.get("depends_on"))
        
        ok = (actual_count == exp_count) and (actual_parallel == exp_parallel)
        if not ok:
            print(f"    ❌ {tpl.name}: expected ({exp_count}, {exp_parallel}), got ({actual_count}, {actual_parallel})")
            all_ok = False
    
    print(f"  All 5 templates DAG shape: {all_ok} {'✅' if all_ok else '❌'}")
    return all_ok


def test_rationale_propagation():
    """[10] Template 的 rationale 寫入 plan metadata"""
    plan = instantiate_template("分析曼聯 vs 車路士的赔率")
    
    has_rationale = bool(plan.metadata.get("rationale"))
    
    ok = has_rationale
    print(f"  Rationale propagation: '{plan.metadata.get('rationale', '')[:50]}...' {'✅' if ok else '❌'}")
    return ok


# ==================== Main ====================

TESTS = [
    ("end_to_end_template_to_verified_plan", test_end_to_end_template_to_verified_plan),
    ("end_to_end_template_to_executable_plan", test_end_to_end_template_to_executable_plan),
    ("all_5_templates_pass_verification", test_all_5_templates_pass_verification),
    ("stock_template_uses_correct_substitutions", test_stock_template_uses_correct_substitutions),
    ("investigation_template_has_diamond_dag", test_investigation_template_has_diamond_dag),
    ("template_vs_llm_path_compatibility", test_template_vs_llm_path_compatibility),
    ("fallback_chain", test_fallback_chain),
    ("verification_catches_introduced_defects", test_verification_catches_introduced_defects),
    ("phase_count_in_template_variants", test_phase_count_in_template_variants),
    ("rationale_propagation", test_rationale_propagation),
]


def main():
    print("=" * 70)
    print("P3-3 Integration Tests")
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
