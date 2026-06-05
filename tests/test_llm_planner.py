"""
LLM planner 測試
- 整合測試會真打 LLM（需 MINIMAX_API_KEY）
- 單元測試 mock LLM response
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))


def _load_api_key():
    """從 .env 載入 key（測試用）"""
    env_path = Path.home() / ".hermes" / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "MINIMAX_API_KEY" in line and "=" in line and not line.strip().startswith("#"):
                os.environ.setdefault("MINIMAX_API_KEY", line.split("=", 1)[1].strip())


_load_api_key()

# ==================== 單元測試（mock LLM） ====================

def test_parse_json_pure():
    """[1] Parse pure JSON"""
    from llm_planner import LLMPlanner
    p = LLMPlanner.__new__(LLMPlanner)
    p.config = MagicMock()
    
    text = '{"rationale": "test", "phases": [{"id": 1, "name": "a", "sub_task": "x", "force_domain": "ft", "depends_on": []}]}'
    result = p._parse_plan_json(text)
    
    ok = result is not None and result["phases"][0]["name"] == "a"
    print(f"  pure JSON: {'✅' if ok else '❌'}")
    return ok


def test_parse_json_markdown_fence():
    """[2] Parse JSON wrapped in ```json ... ```"""
    from llm_planner import LLMPlanner
    p = LLMPlanner.__new__(LLMPlanner)
    p.config = MagicMock()
    
    text = '''Here's the plan:
```json
{"rationale": "test", "phases": [{"id": 1, "name": "a", "sub_task": "x", "force_domain": "ft", "depends_on": []}]}
```
That's it.'''
    result = p._parse_plan_json(text)
    
    ok = result is not None and len(result["phases"]) == 1
    print(f"  markdown fence: {'✅' if ok else '❌'}")
    return ok


def test_parse_json_natural_text():
    """[3] Parse JSON embedded in natural language"""
    from llm_planner import LLMPlanner
    p = LLMPlanner.__new__(LLMPlanner)
    p.config = MagicMock()
    
    text = '''I think we should split it as follows:
{"phases": [{"id": 1, "name": "research", "sub_task": "查赔率", "force_domain": "ft", "depends_on": []}]}
This way the LLM is data-driven.'''
    result = p._parse_plan_json(text)
    
    ok = result is not None and result["phases"][0]["name"] == "research"
    print(f"  natural text: {'✅' if ok else '❌'}")
    return ok


def test_parse_json_invalid():
    """[4] Invalid JSON returns None"""
    from llm_planner import LLMPlanner
    p = LLMPlanner.__new__(LLMPlanner)
    p.config = MagicMock()
    
    text = "no json here at all"
    result = p._parse_plan_json(text)
    
    ok = result is None
    print(f"  invalid text returns None: {'✅' if ok else '❌'}")
    return ok


def test_validate_and_fix_duplicate_ids():
    """[5] Validate + fix duplicate IDs"""
    from llm_planner import LLMPlanner
    p = LLMPlanner.__new__(LLMPlanner)
    p.config = MagicMock()
    
    plan_dict = {
        "phases": [
            {"id": 1, "name": "a", "sub_task": "x", "force_domain": "ft", "depends_on": []},
            {"id": 1, "name": "b", "sub_task": "y", "force_domain": "ft", "depends_on": [1]},  # duplicate
        ]
    }
    fixed = p._validate_and_fix(plan_dict)
    ids = [ph["id"] for ph in fixed["phases"]]
    
    ok = ids == [1, 2]  # 第二個自動編為 2
    print(f"  duplicate IDs fixed: {ids} {'✅' if ok else '❌'}")
    return ok


def test_validate_and_fix_missing_domain():
    """[6] Validate + add missing force_domain"""
    from llm_planner import LLMPlanner
    p = LLMPlanner.__new__(LLMPlanner)
    p.config = MagicMock()
    
    plan_dict = {
        "phases": [
            {"id": 1, "name": "a", "sub_task": "x", "depends_on": []},  # 沒 force_domain
        ]
    }
    fixed = p._validate_and_fix(plan_dict)
    
    ok = fixed["phases"][0]["force_domain"] == "general"
    print(f"  missing domain → general: {'✅' if ok else '❌'}")
    return ok


def test_validate_and_fix_invalid_deps():
    """[7] Validate + drop invalid depends_on"""
    from llm_planner import LLMPlanner
    p = LLMPlanner.__new__(LLMPlanner)
    p.config = MagicMock()
    
    plan_dict = {
        "phases": [
            {"id": 1, "name": "a", "sub_task": "x", "force_domain": "ft", "depends_on": []},
            {"id": 2, "name": "b", "sub_task": "y", "force_domain": "ft", "depends_on": [1, 99, 100]},  # 99, 100 不存在
            {"id": 3, "name": "c", "sub_task": "z", "force_domain": "ft", "depends_on": [3]},  # self-ref
        ]
    }
    fixed = p._validate_and_fix(plan_dict)
    p2_deps = fixed["phases"][1]["depends_on"]
    p3_deps = fixed["phases"][2]["depends_on"]
    
    ok = p2_deps == [1] and p3_deps == []
    print(f"  invalid deps dropped: p2={p2_deps}, p3={p3_deps} {'✅' if ok else '❌'}")
    return ok


def test_dict_to_plan_creates_dsl_script():
    """[8] _dict_to_plan 產生合法 DSL script"""
    from llm_planner import LLMPlanner, PlannerConfig
    p = LLMPlanner.__new__(LLMPlanner)
    p.config = PlannerConfig()
    
    plan_dict = {
        "rationale": "test",
        "phases": [
            {"id": 1, "name": "查赔率", "sub_task": "曼聯 赔率", "force_domain": "ft", "depends_on": []},
            {"id": 2, "name": "共識", "sub_task": "曼聯 共識", "force_domain": "ft", "depends_on": [1]},
        ]
    }
    plan = p._dict_to_plan(plan_dict, "test task")
    
    checks = [
        plan.task_text == "test task",
        len(plan.phases) == 2,
        "router.route" in plan.script_source,
        "# Rationale: test" in plan.script_source,
        "# depends_on: [1]" in plan.script_source,
        plan.metadata.get("generated_by") == "llm_planner",
        plan.metadata.get("rationale") == "test",
    ]
    ok = all(checks)
    print(f"  DSL script generated: {'✅' if ok else '❌'}")
    if not ok:
        for i, c in enumerate(checks):
            if not c:
                print(f"    ❌ check {i} failed")
        print(f"  script_source:\n{plan.script_source}")
    return ok


def test_generate_plan_with_mock():
    """[9] generate_plan() 完整 flow（mock LLM response）"""
    from llm_planner import LLMPlanner
    
    planner = LLMPlanner.__new__(LLMPlanner)
    planner.config = MagicMock()
    planner.config.max_retries = 1
    
    # Mock LLM response
    mock_response = json.dumps({
        "rationale": "test plan",
        "phases": [
            {"id": 1, "name": "a", "sub_task": "task 1", "force_domain": "ft", "depends_on": []},
            {"id": 2, "name": "b", "sub_task": "task 2", "force_domain": "ft", "depends_on": []},
            {"id": 3, "name": "c", "sub_task": "task 3", "force_domain": "ft", "depends_on": [1, 2]},
        ]
    })
    planner._call_llm = MagicMock(return_value=mock_response)
    
    plan = planner.generate_plan("test task")
    
    ok = (
        plan.task_text == "test task"
        and len(plan.phases) == 3
        and plan.phases[2].depends_on == [1, 2]
    )
    print(f"  generate_plan with mock: {'✅' if ok else '❌'}")
    return ok


# ==================== 整合測試（真打 LLM） ====================

def _has_api_key():
    return bool(os.environ.get("MINIMAX_API_KEY"))


def test_integration_ft_plan():
    """[10] 整合測試：FT 足球分析（真打 LLM）"""
    if not _has_api_key():
        print("  ⏭️  SKIP (no MINIMAX_API_KEY)")
        return True
    
    from llm_planner import generate_plan_from_task
    
    plan = generate_plan_from_task("分析曼聯 vs 車路士的赔率")
    
    ok = (
        len(plan.phases) >= 2
        and all(p.force_domain for p in plan.phases)
        and plan.metadata.get("rationale")
    )
    print(f"  integrated FT plan: {len(plan.phases)} phases")
    print(f"  rationale: {plan.metadata.get('rationale', '')[:60]}...")
    return ok


def test_integration_stock_plan():
    """[11] 整合測試：股票分析（真打 LLM）"""
    if not _has_api_key():
        print("  ⏭️  SKIP (no MINIMAX_API_KEY)")
        return True
    
    from llm_planner import generate_plan_from_task
    
    plan = generate_plan_from_task(
        "深度分析台積電 2330 的投資價值",
        available_domains=["stock", "general"],
    )
    
    # 至少要有 2 個 phases，至少一個用 stock domain
    has_stock = any(p.force_domain == "stock" for p in plan.phases)
    ok = len(plan.phases) >= 2 and has_stock
    print(f"  integrated stock plan: {len(plan.phases)} phases (has stock: {has_stock})")
    return ok


def test_integration_simple_task():
    """[12] 整合測試：簡單單一查詢（真打 LLM）"""
    if not _has_api_key():
        print("  ⏭️  SKIP (no MINIMAX_API_KEY)")
        return True
    
    from llm_planner import generate_plan_from_task
    
    # 即使 LLM 想多加 phases，至少 1 個
    plan = generate_plan_from_task("查 BTC 現價")
    
    ok = len(plan.phases) >= 1
    print(f"  integrated simple plan: {len(plan.phases)} phases")
    for p in plan.phases:
        print(f"    [{p.id}] {p.name} ({p.force_domain}): {p.sub_task}")
    return ok


# ==================== Main ====================

TESTS = [
    # 單元
    ("parse_json_pure", test_parse_json_pure),
    ("parse_json_markdown_fence", test_parse_json_markdown_fence),
    ("parse_json_natural_text", test_parse_json_natural_text),
    ("parse_json_invalid", test_parse_json_invalid),
    ("validate_and_fix_duplicate_ids", test_validate_and_fix_duplicate_ids),
    ("validate_and_fix_missing_domain", test_validate_and_fix_missing_domain),
    ("validate_and_fix_invalid_deps", test_validate_and_fix_invalid_deps),
    ("dict_to_plan_creates_dsl_script", test_dict_to_plan_creates_dsl_script),
    ("generate_plan_with_mock", test_generate_plan_with_mock),
    # 整合（真打 LLM）
    ("integration_ft_plan", test_integration_ft_plan),
    ("integration_stock_plan", test_integration_stock_plan),
    ("integration_simple_task", test_integration_simple_task),
]


def main():
    print("=" * 70)
    print("LLM Planner 測試")
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
    
    print()
    print("=" * 70)
    print(f"總計: {passed}/{total} 通過")
    print("=" * 70)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
