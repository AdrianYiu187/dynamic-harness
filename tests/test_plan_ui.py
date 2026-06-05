"""
test_plan_ui.py — Plan UI 終端視覺化測試（Phase 4）
====================================================

測試案例：
- test_assign_levels_no_deps
- test_assign_levels_chain
- test_assign_levels_diamond
- test_colorize_with_color
- test_colorize_without_color
- test_status_icon_for_all_states
- test_planui_render_empty_plan
- test_planui_render_linear_dag
- test_planui_render_diamond_dag
- test_planui_render_failed_phase
- test_planui_render_skipped_phase
- test_planui_legend_present
- test_load_plan_from_temp_db
- test_list_plans_from_temp_db
- test_cli_nonexistent_plan_exits_1
- test_cli_list_empty_db
- test_cli_watch_mode_terminates_on_completed

日期：2026-06-05
"""
from __future__ import annotations
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import List

_HERE = Path(__file__).resolve().parent
_DH_ROOT = _HERE.parent
sys.path.insert(0, str(_DH_ROOT))


# ============================================================
# Helper: 建構 test DB + mock plans
# ============================================================

def setup_temp_db():
    """建立臨時 DB，patch plan_ui + plan + persistence 三處 DEFAULT_DB_PATH"""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    test_db = Path(tmp.name)

    import plan_ui
    import plan
    import persistence
    plan_ui._default_db_path = lambda: str(test_db)
    plan.DEFAULT_DB_PATH = test_db
    persistence.DEFAULT_DB_PATH = test_db
    return test_db


def teardown_temp_db(db_path: Path):
    if db_path.exists():
        db_path.unlink()


def build_plan(plan_id: str, phases_data: List[dict], status: str = "running") -> "Plan":
    """從 dict 規格建構 Plan"""
    from plan import Plan, Phase
    phases = [
        Phase(
            id=p["id"], name=p["name"], sub_task=p.get("sub_task", "task"),
            force_domain=p.get("force_domain"),
            depends_on=p.get("depends_on", []),
            timeout_s=p.get("timeout_s", 60),
            status=p["status"],
            started_at=p.get("started_at"),
            completed_at=p.get("completed_at"),
            error=p.get("error"),
        )
        for p in phases_data
    ]
    return Plan(
        id=plan_id, task_text=f"Test plan {plan_id}", force_domain="stock",
        created_at=time.time(), script_source="# mock", status=status, phases=phases,
    )


def persist_plan(plan) -> None:
    """寫入 plan 到當前 test DB（用 plan.py 的 save_plan 介面）"""
    from plan import save_plan
    save_plan(plan)


# ============================================================
# [1] _assign_levels
# ============================================================

def test_assign_levels_no_deps():
    """無依賴 → 全部 L0"""
    print("\n[1] _assign_levels 無依賴")
    from plan_ui import _assign_levels
    plan = build_plan("p1", [
        {"id": 1, "name": "a", "status": "pending"},
        {"id": 2, "name": "b", "status": "pending"},
    ])
    levels = _assign_levels(plan.phases)
    assert levels == {1: 0, 2: 0}, f"expected {{1:0, 2:0}}, got {levels}"
    print("    ✓ L0 for no deps")


def test_assign_levels_chain():
    """線性依賴 → 依序升層"""
    print("\n[2] _assign_levels 線性 chain")
    from plan_ui import _assign_levels
    plan = build_plan("p2", [
        {"id": 1, "name": "a", "status": "pending"},
        {"id": 2, "name": "b", "status": "pending", "depends_on": [1]},
        {"id": 3, "name": "c", "status": "pending", "depends_on": [2]},
    ])
    levels = _assign_levels(plan.phases)
    assert levels == {1: 0, 2: 1, 3: 2}, f"expected chain, got {levels}"
    print("    ✓ chain 1→0, 2→1, 3→2")


def test_assign_levels_diamond():
    """diamond: 1 → 2,3 → 4"""
    print("\n[3] _assign_levels diamond")
    from plan_ui import _assign_levels
    plan = build_plan("p3", [
        {"id": 1, "name": "a", "status": "pending"},
        {"id": 2, "name": "b", "status": "pending", "depends_on": [1]},
        {"id": 3, "name": "c", "status": "pending", "depends_on": [1]},
        {"id": 4, "name": "d", "status": "pending", "depends_on": [2, 3]},
    ])
    levels = _assign_levels(plan.phases)
    assert levels == {1: 0, 2: 1, 3: 1, 4: 2}, f"expected diamond, got {levels}"
    print("    ✓ diamond layout 1:0, 2:1, 3:1, 4:2")


# ============================================================
# [4] _colorize
# ============================================================

def test_colorize_with_color():
    """use_color=True 應包含 ANSI escape"""
    print("\n[4] _colorize 含 ANSI")
    from plan_ui import _colorize
    out = _colorize("hello", "\033[32m", use_color=True)
    assert "\033[32m" in out
    assert "hello" in out
    assert "\033[0m" in out
    print("    ✓ ANSI escape codes present")


def test_colorize_without_color():
    """use_color=False 應為純文字"""
    print("\n[5] _colorize 不含 ANSI")
    from plan_ui import _colorize
    out = _colorize("hello", "\033[32m", use_color=False)
    assert out == "hello", f"expected 'hello', got '{out}'"
    print("    ✓ no ANSI escape")


# ============================================================
# [6] _status_icon
# ============================================================

def test_status_icon_for_all_states():
    """5 種狀態都有對應圖示"""
    print("\n[6] _status_icon 5 states")
    from plan_ui import _status_icon, STATUS_ICONS
    for status, expected in STATUS_ICONS.items():
        assert _status_icon(status) == expected
    assert _status_icon("unknown") == "?", "unknown → ?"
    print(f"    ✓ {len(STATUS_ICONS)} states mapped")


# ============================================================
# [7] PlanUI render
# ============================================================

def test_planui_render_empty_plan():
    """空 phases 仍可 render（不 crash）"""
    print("\n[7] PlanUI 空 plan")
    from plan_ui import PlanUI, PlanUIConfig
    plan = build_plan("empty", [], status="draft")
    ui = PlanUI(plan, PlanUIConfig(use_color=False))
    out = ui.render()
    assert "empty" in out
    assert "DAG View" in out
    print("    ✓ empty plan renders")


def test_planui_render_linear_dag():
    """線性 chain 渲染（1 → 2 → 3 三層）"""
    print("\n[8] PlanUI 線性 DAG")
    from plan_ui import PlanUI, PlanUIConfig
    plan = build_plan("linear", [
        {"id": 1, "name": "step1", "status": "completed",
         "started_at": time.time()-5, "completed_at": time.time()-3},
        {"id": 2, "name": "step2", "status": "completed",
         "depends_on": [1],
         "started_at": time.time()-3, "completed_at": time.time()-2},
        {"id": 3, "name": "step3", "status": "running",
         "depends_on": [2],
         "started_at": time.time()-1},
    ], status="running")
    ui = PlanUI(plan, PlanUIConfig(use_color=False))
    out = ui.render()
    assert "L0:" in out, "missing L0"
    assert "L1:" in out, "missing L1"
    assert "L2:" in out, "missing L2"
    assert "step1" in out
    assert "step2" in out
    assert "step3" in out
    assert "deps: [2]" in out, "phase3 dep annotation missing"
    assert "deps: [1]" in out, "phase2 dep annotation missing"
    print("    ✓ linear chain L0→L1→L2 rendered")


def test_planui_render_diamond_dag():
    """diamond 結構渲染"""
    print("\n[9] PlanUI diamond DAG")
    from plan_ui import PlanUI, PlanUIConfig
    plan = build_plan("dia", [
        {"id": 1, "name": "root", "status": "completed"},
        {"id": 2, "name": "left", "status": "completed", "depends_on": [1]},
        {"id": 3, "name": "right", "status": "completed", "depends_on": [1]},
        {"id": 4, "name": "merge", "status": "pending", "depends_on": [2, 3]},
    ])
    ui = PlanUI(plan, PlanUIConfig(use_color=False))
    out = ui.render()
    assert "deps: [2, 3]" in out
    print("    ✓ diamond merge point rendered")


def test_planui_render_failed_phase():
    """failed 狀態顯示 error 訊息"""
    print("\n[10] PlanUI failed phase")
    from plan_ui import PlanUI, PlanUIConfig
    plan = build_plan("fail", [
        {"id": 1, "name": "x", "status": "failed", "error": "API timeout"},
    ], status="failed")
    ui = PlanUI(plan, PlanUIConfig(use_color=False))
    out = ui.render()
    assert "FAILED" in out
    assert "API timeout" in out
    print("    ✓ failed + error displayed")


def test_planui_render_skipped_phase():
    """skipped 狀態顯示"""
    print("\n[11] PlanUI skipped phase")
    from plan_ui import PlanUI, PlanUIConfig
    plan = build_plan("skip", [
        {"id": 1, "name": "x", "status": "skipped"},
    ], status="failed")
    ui = PlanUI(plan, PlanUIConfig(use_color=False))
    out = ui.render()
    assert "SKIPPED" in out
    assert "⊘" in out
    print("    ✓ skipped + ⊘ displayed")


def test_planui_legend_present():
    """Legend 區塊存在"""
    print("\n[12] PlanUI legend")
    from plan_ui import PlanUI, PlanUIConfig
    plan = build_plan("leg", [{"id": 1, "name": "x", "status": "pending"}])
    ui = PlanUI(plan, PlanUIConfig(use_color=False))
    out = ui.render()
    assert "Legend" in out
    for s in ["pending", "running", "completed", "failed", "skipped"]:
        assert s in out, f"legend missing {s}"
    print("    ✓ legend lists all 5 states")


# ============================================================
# [13] load_plan / list_plans
# ============================================================

def test_load_plan_from_temp_db():
    """round-trip: persist → load"""
    print("\n[13] load_plan round-trip")
    db = setup_temp_db()
    try:
        from plan_ui import load_plan
        from plan import save_plan
        plan = build_plan("rt-1", [
            {"id": 1, "name": "a", "status": "completed"},
            {"id": 2, "name": "b", "status": "pending", "depends_on": [1]},
        ], status="running")
        save_plan(plan)
        loaded = load_plan("rt-1")
        assert loaded is not None, "load returned None"
        assert loaded.id == "rt-1"
        assert loaded.task_text == plan.task_text
        assert len(loaded.phases) == 2
        assert loaded.phases[1].depends_on == [1]
        print("    ✓ persist + load round-trip")
    finally:
        teardown_temp_db(db)


def test_list_plans_from_temp_db():
    """list_plans 從 temp DB 撈資料"""
    print("\n[14] list_plans")
    db = setup_temp_db()
    try:
        from plan_ui import list_plans
        from plan import save_plan
        for i in range(3):
            save_plan(build_plan(f"lp-{i}", [{"id": 1, "name": "x", "status": "pending"}]))
        result = list_plans(limit=10)
        assert len(result) == 3, f"expected 3 plans, got {len(result)}"
        ids = {r[0] for r in result}
        assert ids == {"lp-0", "lp-1", "lp-2"}
        print(f"    ✓ listed {len(result)} plans")
    finally:
        teardown_temp_db(db)


# ============================================================
# [15] CLI
# ============================================================

def test_cli_nonexistent_plan_exits_1():
    """--ui <不存在的 id> → exit code 1"""
    print("\n[15] CLI --ui nonexistent exit 1")
    import subprocess
    result = subprocess.run(
        [sys.executable, "unified_router.py", "--ui", "ghost-plan"],
        cwd=str(_DH_ROOT), capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 1, f"expected exit 1, got {result.returncode}"
    assert "not found" in result.stdout.lower() or "not found" in result.stderr.lower()
    print(f"    ✓ exit 1, msg: {result.stdout.strip()[:60]}")


def test_cli_list_empty_db():
    """--ui-list 在空 DB 應 graceful（exit 0）"""
    print("\n[16] CLI --ui-list empty DB")
    import subprocess
    db = setup_temp_db()
    try:
        # 先 save_plan 觸發 schema init（sub-process 用 PLAN_DB_PATH 拿到同個 DB）
        from plan import save_plan
        save_plan(build_plan("seed", [{"id": 1, "name": "x", "status": "pending"}]))

        result = subprocess.run(
            [sys.executable, "unified_router.py", "--ui-list", "--no-color"],
            cwd=str(_DH_ROOT), capture_output=True, text=True, timeout=15,
            env={**os.environ, "PLAN_DB_PATH": str(db)},
        )
        assert result.returncode == 0, (
            f"expected 0, got {result.returncode}\n"
            f"stdout: {result.stdout[:300]}\nstderr: {result.stderr[:300]}"
        )
        assert "seed" in result.stdout
        print(f"    ✓ --ui-list empty DB after schema init, exit 0")
    finally:
        teardown_temp_db(db)


def test_cli_watch_mode_terminates_on_completed():
    """--live 在 completed plan 應自動 exit"""
    print("\n[17] CLI --live watch 模式")
    db = setup_temp_db()
    try:
        from plan import save_plan
        plan = build_plan("watch-1", [
            {"id": 1, "name": "done", "status": "completed",
             "started_at": time.time()-5, "completed_at": time.time()-3},
        ], status="completed")
        save_plan(plan)

        import subprocess
        result = subprocess.run(
            [sys.executable, "unified_router.py", "--ui", "watch-1", "--live",
             "--interval", "0.5", "--no-color"],
            cwd=str(_DH_ROOT), capture_output=True, text=True, timeout=10,
            env={**os.environ, "PLAN_DB_PATH": str(db)},
        )
        # watch 模式應在 plan completed 時自動 exit
        assert "DAG" in result.stdout, f"no DAG in output: {result.stdout[:200]}"
        assert "Plan completed" in result.stdout or "completed" in result.stdout.lower()
        assert result.returncode == 0, (
            f"expected 0, got {result.returncode}\n"
            f"stdout: {result.stdout[:300]}\nstderr: {result.stderr[:300]}"
        )
        print(f"    ✓ watch auto-terminated on completed, exit 0")
    finally:
        teardown_temp_db(db)


# ============================================================
# Main runner
# ============================================================

def main():
    tests = [
        test_assign_levels_no_deps,
        test_assign_levels_chain,
        test_assign_levels_diamond,
        test_colorize_with_color,
        test_colorize_without_color,
        test_status_icon_for_all_states,
        test_planui_render_empty_plan,
        test_planui_render_linear_dag,
        test_planui_render_diamond_dag,
        test_planui_render_failed_phase,
        test_planui_render_skipped_phase,
        test_planui_legend_present,
        test_load_plan_from_temp_db,
        test_list_plans_from_temp_db,
        test_cli_nonexistent_plan_exits_1,
        test_cli_list_empty_db,
        test_cli_watch_mode_terminates_on_completed,
    ]
    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"    ✗ FAILED: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n=== Plan UI Tests: {passed}/{passed+failed} passed ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
