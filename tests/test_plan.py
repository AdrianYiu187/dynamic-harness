"""
test_plan.py — Plan-in-Code 測試（v2.0 MVP）
==============================================

測試案例：
- test_static_analyzer_blocks_dangerous
- test_static_analyzer_allows_legit
- test_parse_script_simple
- test_parse_script_with_deps
- test_plan_persistence
- test_plan_execution_end_to_end
- test_plan_resume_skip_completed
- test_plan_failure_short_circuit
- test_plan_dependency_order
- test_cli_generate_execute

日期：2026-06-05
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
_DH_ROOT = _HERE.parent  # tests/ 的上層 = dynamic-harness/
sys.path.insert(0, str(_DH_ROOT))


def setup_test_db():
    """建立臨時 DB 路徑，並 patch persistence 模組"""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    test_db = Path(tmp.name)
    
    import persistence
    persistence.DEFAULT_DB_PATH = test_db
    # 也 patch plan 模組裡的引用
    import plan
    plan.DEFAULT_DB_PATH = test_db
    
    return test_db


def teardown_test_db(db_path: Path):
    if db_path.exists():
        db_path.unlink()


# ==================== StaticAnalyzer ====================

def test_static_analyzer_blocks_dangerous():
    """[1] 危險 script 被拒絕"""
    print("\n[1] StaticAnalyzer 阻擋危險 script")
    from plan import StaticAnalyzer
    analyzer = StaticAnalyzer()
    
    dangerous = [
        'import os\nos.system("rm -rf /")',
        'import subprocess\nsubprocess.run(["ls"])',
        'eval("print(1)")',
        'exec("print(1)")',
        '__import__("os").system("rm")',
        'import shutil\nshutil.rmtree("/")',
    ]
    
    results = []
    for d in dangerous:
        is_safe, reason = analyzer.analyze(d)
        results.append((not is_safe, reason[:40]))
    
    ok = all(r[0] for r in results)
    for d, r in zip(dangerous, results):
        print(f"  {'✅' if r[0] else '❌'} {d[:50]!r} → {r[1]}")
    return ok


def test_static_analyzer_allows_legit():
    """[2] 合法 script 通過"""
    print("\n[2] StaticAnalyzer 允許合法 script")
    from plan import StaticAnalyzer
    analyzer = StaticAnalyzer()
    
    legit = [
        'from dynamic_harness import UnifiedRouter\nrouter = UnifiedRouter()\nrouter.route("test")',
        'router.route("test", force_domain="ft")',
        'p = router.route("test")\nq = p.adapter_used',
    ]
    
    results = [analyzer.analyze(s)[0] for s in legit]
    ok = all(results)
    for s, r in zip(legit, results):
        print(f"  {'✅' if r else '❌'} safe={r}: {s[:50]!r}")
    return ok


# ==================== parse_script_to_plan ====================

def test_parse_script_simple():
    """[3] 解析簡單 script（無注釋，從 route() 參數提取 force_domain）"""
    print("\n[3] 解析簡單 script")
    from plan import parse_script_to_plan
    
    script = '''
p1 = router.route("曼聯 赔率", force_domain="ft")
p2 = router.route("車路士 赔率", force_domain="ft")
'''
    plan_obj = parse_script_to_plan(script, "test task")
    
    ok = [
        len(plan_obj.phases) == 2,
        plan_obj.phases[0].sub_task == "曼聯 赔率",
        plan_obj.phases[0].force_domain == "ft",
        plan_obj.phases[1].sub_task == "車路士 赔率",
    ]
    print(f"  phases={len(plan_obj.phases)}")
    for p in plan_obj.phases:
        print(f"    [{p.id}] domain={p.force_domain} sub={p.sub_task!r}")
    return all(ok)


def test_parse_script_with_deps():
    """[4] 解析帶 depends_on 的 script"""
    print("\n[4] 解析 depends_on")
    from plan import parse_script_to_plan
    
    script = '''
# domain: ft
# depends_on: []
p1 = router.route("task 1", force_domain="ft")

# domain: ft
# depends_on: [1]
p2 = router.route("task 2", force_domain="ft")
'''
    plan_obj = parse_script_to_plan(script, "test")
    
    ok = [
        len(plan_obj.phases) == 2,
        plan_obj.phases[0].depends_on == [],
        plan_obj.phases[1].depends_on == [1],
    ]
    print(f"  p1.depends_on = {plan_obj.phases[0].depends_on}")
    print(f"  p2.depends_on = {plan_obj.phases[1].depends_on}")
    return all(ok)


# ==================== Plan persistence ====================

def test_plan_persistence():
    """[5] Plan 持久化 + 載入"""
    print("\n[5] Plan 持久化 + 載入")
    db = setup_test_db()
    try:
        from plan import parse_script_to_plan, save_plan, load_plan
        
        script = 'p1 = router.route("test", force_domain="ft")'
        plan_obj = parse_script_to_plan(script, "persist test")
        plan_id = plan_obj.id
        save_plan(plan_obj)
        
        loaded = load_plan(plan_id)
        ok = [
            loaded is not None,
            loaded.id == plan_id,
            loaded.task_text == "persist test",
            len(loaded.phases) == 1,
            loaded.phases[0].sub_task == "test",
        ]
        print(f"  loaded phases: {len(loaded.phases) if loaded else 0}")
        return all(ok)
    finally:
        teardown_test_db(db)


# ==================== Plan execution ====================

def test_plan_execution_end_to_end():
    """[6] Plan end-to-end 執行"""
    print("\n[6] Plan end-to-end")
    db = setup_test_db()
    try:
        from plan import parse_script_to_plan, save_plan, load_plan, PlanExecutor
        
        script = '''
# domain: ft
p1 = router.route("曼聯 對 車路士 赔率", force_domain="ft")

# domain: stock
p2 = router.route("分析 01810 股票", force_domain="stock")
'''
        plan_obj = parse_script_to_plan(script, "e2e test")
        save_plan(plan_obj)
        
        loaded = load_plan(plan_obj.id)
        executor = PlanExecutor(loaded)
        result = executor.execute()
        
        ok = [
            result.status == "completed",
            result.phases[0].status == "completed",
            result.phases[1].status == "completed",
        ]
        print(f"  status: {result.status}")
        for p in result.phases:
            print(f"    [{p.id}] {p.status} - {p.name}")
        return all(ok)
    finally:
        teardown_test_db(db)


def test_plan_resume_skip_completed():
    """[7] Resume 跳過已完成的 phase"""
    print("\n[7] Resume 跳過 completed")
    db = setup_test_db()
    try:
        from plan import (
            parse_script_to_plan, save_plan, load_plan, PlanExecutor,
            update_phase, update_plan_status,
        )
        
        script = '''
# domain: ft
p1 = router.route("曼聯 對 車路士 赔率", force_domain="ft")

# domain: ft
p2 = router.route("車路士 對 曼聯 赔率", force_domain="ft")
'''
        plan_obj = parse_script_to_plan(script, "resume test")
        save_plan(plan_obj)
        
        # 模擬 Phase 1 已完成
        plan_obj.phases[0].status = "completed"
        plan_obj.phases[0].envelope_id = 999
        update_phase(plan_obj.id, plan_obj.phases[0])
        update_plan_status(plan_obj.id, "running")
        
        # 載入 + 跑
        loaded = load_plan(plan_obj.id)
        executor = PlanExecutor(loaded)
        result = executor.execute()
        
        # 重新載入看 Phase 1 的 envelope_id 是否保留
        loaded2 = load_plan(plan_obj.id)
        
        ok = [
            result.status == "completed",
            result.phases[0].envelope_id == 999,  # 保留
            result.phases[1].status == "completed",
            loaded2.phases[0].envelope_id == 999,  # DB 也保留
        ]
        print(f"  Phase 1 envelope_id (應為 999): {result.phases[0].envelope_id}")
        print(f"  Phase 2 status: {result.phases[1].status}")
        return all(ok)
    finally:
        teardown_test_db(db)


def test_plan_failure_short_circuit():
    """[8] Phase 失敗時 plan 標 failed，下游 phase 仍 pending
    
    構造失敗：mock router.route() 回傳 env.error，驗證短路機制
    """
    print("\n[8] 失敗短路")
    db = setup_test_db()
    try:
        from plan import parse_script_to_plan, save_plan, load_plan, PlanExecutor
        from unittest.mock import MagicMock, patch
        
        script = '''
# domain: ft
# depends_on: []
p1 = router.route("", force_domain="ft")
# domain: ft
# depends_on: [1]
p2 = router.route("車路士 赔率", force_domain="ft")
'''
        plan_obj = parse_script_to_plan(script, "failure test")
        save_plan(plan_obj)
        
        # 載入 + mock router.route
        loaded2 = load_plan(plan_obj.id)
        mock_env = MagicMock()
        mock_env.error = "mocked error"
        mock_route = MagicMock(return_value=mock_env)
        
        with patch.object(
            PlanExecutor, 'router',
            new=property(lambda self: MagicMock(route=mock_route)),
        ):
            executor2 = PlanExecutor(loaded2)
            result2 = executor2.execute()
        
        ok = [
            result2.status == "failed",
            result2.phases[0].status == "failed",
            result2.phases[0].error == "mocked error",
            result2.phases[1].status == "skipped",  # 下游自動標 skipped
        ]
        print(f"  plan status: {result2.status}")
        print(f"  p1 status: {result2.phases[0].status}, error: {result2.phases[0].error}")
        print(f"  p2 status: {result2.phases[1].status}")
        return all(ok)
    finally:
        teardown_test_db(db)


def test_plan_dependency_order():
    """[9] 依 depends_on 排序"""
    print("\n[9] 依 depends_on 排序")
    db = setup_test_db()
    try:
        from plan import parse_script_to_plan, save_plan, PlanExecutor
        
        # Script 順序顛倒：先寫 p3（depends_on=[2,3]）再寫 p1
        # phases 編號是按出現順序：1=p3, 2=p1, 3=p2
        # p3 依賴 p1 和 p2 (即 phase 2 和 3)
        script = '''
# depends_on: [2, 3]
# domain: ft
p3 = router.route("task 3", force_domain="ft")

# depends_on: []
# domain: ft
p1 = router.route("task 1", force_domain="ft")

# depends_on: [2]
# domain: ft
p2 = router.route("task 2", force_domain="ft")
'''
        plan_obj = parse_script_to_plan(script, "order test")
        save_plan(plan_obj)
        
        # 印來源順序
        print(f"  source order: {[(p.id, p.sub_task) for p in plan_obj.phases]}")
        
        # Topological sort 應該得到 [2, 3, 1]（p1 先，p2 依賴 p1，p3 依賴 p1, p2）
        executor = PlanExecutor(plan_obj)
        ordered = executor._topological_sort(plan_obj.phases)
        ids = [p.id for p in ordered]
        print(f"  sorted IDs: {ids}")
        
        ok = ids == [2, 3, 1]
        return ok
    finally:
        teardown_test_db(db)


def test_cli_generate_execute():
    """[10] CLI: generate + execute"""
    print("\n[10] CLI generate + execute")
    db = setup_test_db()
    try:
        # 寫 script 到臨時檔
        script_file = Path(tempfile.mkstemp(suffix=".py")[1])
        script_file.write_text('''
# domain: ft
p1 = router.route("曼聯 對 車路士 赔率", force_domain="ft")
''')
        
        # 跑 CLI
        from plan_cli import main as cli_main
        
        # generate
        sys.argv = ["plan_cli", "generate", "--script-file", str(script_file), "--task", "cli test"]
        rc = cli_main()
        if rc != 0:
            print(f"  generate returned {rc}")
            return False
        
        # 拿 plan_id
        from plan import list_plans
        plans = list_plans()
        if not plans:
            return False
        plan_id = plans[0]["id"]
        
        # execute
        sys.argv = ["plan_cli", "execute", "--plan-id", plan_id]
        rc = cli_main()
        if rc != 0:
            print(f"  execute returned {rc}")
            return False
        
        # status
        sys.argv = ["plan_cli", "status", "--plan-id", plan_id]
        rc = cli_main()
        
        script_file.unlink()
        return rc == 0
    finally:
        teardown_test_db(db)


# ==================== P3-3.1: Parallel execution ====================

def test_parallel_phases_run_concurrently():
    """[11] 兩個獨立 phases 在同一 wave 並行"""
    print("\n[11] Parallel phases（同一 wave）")
    db = setup_test_db()
    try:
        from plan import (
            parse_script_to_plan, save_plan, load_plan, PlanExecutor, add_trace,
        )
        from unittest.mock import MagicMock, patch
        import time as time_mod
        
        # 兩個 phase 都 depends_on=[]，應在同一 wave
        script = '''
# domain: ft
p1 = router.route("曼聯 赔率", force_domain="ft")
# domain: ft
p2 = router.route("車路士 赔率", force_domain="ft")
'''
        plan_obj = parse_script_to_plan(script, "parallel test")
        save_plan(plan_obj)
        loaded = load_plan(plan_obj.id)
        
        # Mock router.route 模擬耗時操作
        mock_env = MagicMock()
        mock_env.error = None
        mock_env.to_dict = lambda: {"mock": True}
        
        call_log = []
        def slow_route(task, force_domain=None):
            call_log.append((time_mod.time(), task))
            time_mod.sleep(0.3)  # 模擬 0.3s 耗時
            return mock_env
        
        mock_router = MagicMock()
        mock_router.route = slow_route
        
        with patch.object(PlanExecutor, 'router', new=property(lambda self: mock_router)):
            executor = PlanExecutor(loaded)
            start = time_mod.time()
            result = executor.execute()
            elapsed = time_mod.time() - start
        
        # 兩 phase 都 completed，總時間 < 0.5s（若 sequential 會是 0.6s+）
        # 給 0.55s 容差
        is_parallel = elapsed < 0.55
        ok = [
            result.status == "completed",
            result.phases[0].status == "completed",
            result.phases[1].status == "completed",
            len(call_log) == 2,
            is_parallel,
        ]
        print(f"  elapsed: {elapsed:.2f}s (parallel < 0.55s, sequential ~0.6s)")
        print(f"  call_log: {len(call_log)} calls")
        print(f"  parallel: {'✅' if is_parallel else '❌'}")
        return all(ok)
    finally:
        teardown_test_db(db)


def test_dataflow_dependent_phases_serialized():
    """[12] 有依賴的 phases 必須依序"""
    print("\n[12] Dataflow dependent phases")
    db = setup_test_db()
    try:
        from plan import (
            parse_script_to_plan, save_plan, load_plan, PlanExecutor, add_trace,
        )
        from unittest.mock import MagicMock, patch
        import time as time_mod
        
        # p1 → p2 → p3 線性鏈
        script = '''
# domain: ft
# depends_on: []
p1 = router.route("task 1", force_domain="ft")
# domain: ft
# depends_on: [1]
p2 = router.route("task 2", force_domain="ft")
# domain: ft
# depends_on: [2]
p3 = router.route("task 3", force_domain="ft")
'''
        plan_obj = parse_script_to_plan(script, "dataflow test")
        save_plan(plan_obj)
        loaded = load_plan(plan_obj.id)
        
        mock_env = MagicMock()
        mock_env.error = None
        mock_env.to_dict = lambda: {"mock": True}
        
        execution_order = []
        execution_lock = threading.Lock()
        
        def track_route(task, force_domain=None):
            with execution_lock:
                execution_order.append((time_mod.time(), task))
            time_mod.sleep(0.2)
            return mock_env
        
        mock_router = MagicMock()
        mock_router.route = track_route
        
        with patch.object(PlanExecutor, 'router', new=property(lambda self: mock_router)):
            executor = PlanExecutor(loaded)
            result = executor.execute()
        
        # 驗證：執行順序必須是 p1 → p2 → p3
        tasks = [t for _, t in execution_order]
        ok = [
            result.status == "completed",
            tasks == ["task 1", "task 2", "task 3"],
        ]
        print(f"  order: {tasks}")
        return all(ok)
    finally:
        teardown_test_db(db)


def test_dataflow_diamond_dag():
    """[13] Diamond DAG: p1 → (p2, p3) → p4
    
    p2 和 p3 應在同一 wave，p4 在後一波
    """
    print("\n[13] Diamond DAG")
    db = setup_test_db()
    try:
        from plan import (
            parse_script_to_plan, save_plan, load_plan, PlanExecutor, add_trace,
        )
        from unittest.mock import MagicMock, patch
        import time as time_mod
        
        # Diamond: p1 是 source, p4 是 sink, p2/p3 是 middle
        script = '''
# depends_on: []
# domain: ft
p1 = router.route("source", force_domain="ft")
# depends_on: [1]
# domain: ft
p2 = router.route("left", force_domain="ft")
# depends_on: [1]
# domain: ft
p3 = router.route("right", force_domain="ft")
# depends_on: [2, 3]
# domain: ft
p4 = router.route("sink", force_domain="ft")
'''
        plan_obj = parse_script_to_plan(script, "diamond test")
        save_plan(plan_obj)
        loaded = load_plan(plan_obj.id)
        
        mock_env = MagicMock()
        mock_env.error = None
        mock_env.to_dict = lambda: {"mock": True}
        
        start_times = {}
        end_times = {}
        time_lock = threading.Lock()
        
        def track_route(task, force_domain=None):
            with time_lock:
                start_times[task] = time_mod.time()
            time_mod.sleep(0.2)
            with time_lock:
                end_times[task] = time_mod.time()
            return mock_env
        
        mock_router = MagicMock()
        mock_router.route = track_route
        
        with patch.object(PlanExecutor, 'router', new=property(lambda self: mock_router)):
            executor = PlanExecutor(loaded)
            result = executor.execute()
        
        # 驗證 p2 和 p3 確實 overlap（end of one > start of other）
        p2_overlaps_p3 = (
            end_times["left"] > start_times["right"] and
            end_times["right"] > start_times["left"]
        )
        # 驗證 p4 在 p2, p3 都完成後才開始
        p4_starts_after = (
            start_times["sink"] > end_times["left"] and
            start_times["sink"] > end_times["right"]
        )
        # 驗證 p1 在 p2, p3 之前完成
        p1_before_others = end_times["source"] < start_times["left"]
        
        ok = [
            result.status == "completed",
            p2_overlaps_p3,
            p4_starts_after,
            p1_before_others,
        ]
        print(f"  p2/p3 overlap: {'✅' if p2_overlaps_p3 else '❌'}")
        print(f"  p4 after p2/p3: {'✅' if p4_starts_after else '❌'}")
        print(f"  p1 before p2/p3: {'✅' if p1_before_others else '❌'}")
        return all(ok)
    finally:
        teardown_test_db(db)


def test_parallel_failure_skips_downstream():
    """[14] Parallel + failure: 失敗 phase 同 wave 的完成不被影響，下游 skipped"""
    print("\n[14] Parallel + failure")
    db = setup_test_db()
    try:
        from plan import (
            parse_script_to_plan, save_plan, load_plan, PlanExecutor,
        )
        from unittest.mock import MagicMock, patch
        
        # p1 失敗，p2 成功（兩者都 depends_on=[]，同一 wave），p3 依賴兩者 → skipped
        script = '''
# domain: ft
# depends_on: []
p1 = router.route("will fail", force_domain="ft")
# domain: ft
# depends_on: []
p2 = router.route("will succeed", force_domain="ft")
# domain: ft
# depends_on: [1, 2]
p3 = router.route("depends on both", force_domain="ft")
'''
        plan_obj = parse_script_to_plan(script, "parallel failure test")
        save_plan(plan_obj)
        loaded = load_plan(plan_obj.id)
        
        def selective_route(task, force_domain=None):
            mock_env = MagicMock()
            mock_env.error = "intentional fail" if "fail" in task else None
            mock_env.to_dict = lambda: {"task": task, "error": mock_env.error}
            return mock_env
        
        mock_router = MagicMock()
        mock_router.route = selective_route
        
        with patch.object(PlanExecutor, 'router', new=property(lambda self: mock_router)):
            executor = PlanExecutor(loaded)
            result = executor.execute()
        
        ok = [
            result.status == "failed",
            result.phases[0].status == "failed",   # p1
            result.phases[1].status == "completed",  # p2（不受 p1 影響）
            result.phases[2].status == "skipped",   # p3（p1 失敗 → skipped）
        ]
        print(f"  plan status: {result.status}")
        for p in result.phases:
            print(f"    [{p.id}] {p.status:10s} - {p.name}")
        return all(ok)
    finally:
        teardown_test_db(db)


def test_wave_traces_logged():
    """[15] Wave-level traces 記錄到 DB"""
    print("\n[15] Wave traces")
    db = setup_test_db()
    try:
        from plan import (
            parse_script_to_plan, save_plan, load_plan, PlanExecutor, get_plan_traces,
        )
        from unittest.mock import MagicMock, patch
        
        script = '''
# domain: ft
# depends_on: []
p1 = router.route("a", force_domain="ft")
# domain: ft
# depends_on: []
p2 = router.route("b", force_domain="ft")
# domain: ft
# depends_on: [1, 2]
p3 = router.route("c", force_domain="ft")
'''
        plan_obj = parse_script_to_plan(script, "trace test")
        save_plan(plan_obj)
        loaded = load_plan(plan_obj.id)
        
        mock_env = MagicMock()
        mock_env.error = None
        mock_env.to_dict = lambda: {}
        
        with patch.object(PlanExecutor, 'router', new=property(lambda self: MagicMock(route=MagicMock(return_value=mock_env)))):
            executor = PlanExecutor(loaded)
            result = executor.execute()
        
        traces = get_plan_traces(plan_obj.id)
        wave_starts = [t for t in traces if t["event"] == "wave_start"]
        
        # 應該有 2 個 wave：第一 wave 跑 p1, p2；第二 wave 跑 p3
        ok = [
            result.status == "completed",
            len(wave_starts) == 2,
        ]
        print(f"  wave_start events: {len(wave_starts)}")
        for w in wave_starts:
            print(f"    {w['message'][:80]}")
        return all(ok)
    finally:
        teardown_test_db(db)


# ==================== Main ====================

TESTS = [
    ("static_analyzer_blocks_dangerous", test_static_analyzer_blocks_dangerous),
    ("static_analyzer_allows_legit", test_static_analyzer_allows_legit),
    ("parse_script_simple", test_parse_script_simple),
    ("parse_script_with_deps", test_parse_script_with_deps),
    ("plan_persistence", test_plan_persistence),
    ("plan_execution_end_to_end", test_plan_execution_end_to_end),
    ("plan_resume_skip_completed", test_plan_resume_skip_completed),
    ("plan_failure_short_circuit", test_plan_failure_short_circuit),
    ("plan_dependency_order", test_plan_dependency_order),
    ("cli_generate_execute", test_cli_generate_execute),
    # P3-3.1: Parallel execution
    ("parallel_phases_run_concurrently", test_parallel_phases_run_concurrently),
    ("dataflow_dependent_phases_serialized", test_dataflow_dependent_phases_serialized),
    ("dataflow_diamond_dag", test_dataflow_diamond_dag),
    ("parallel_failure_skips_downstream", test_parallel_failure_skips_downstream),
    ("wave_traces_logged", test_wave_traces_logged),
]


def main():
    print("=" * 70)
    print("Plan-in-Code 測試（v2.0 MVP）")
    print("=" * 70)
    
    results = {}
    for name, fn in TESTS:
        try:
            results[name] = fn()
        except Exception as e:
            print(f"  ❌ EXCEPTION: {e}")
            import traceback
            traceback.print_exc()
            results[name] = False
    
    print("\n" + "=" * 70)
    print("測試結果總覽")
    print("=" * 70)
    for name, passed in results.items():
        mark = "✅" if passed else "❌"
        print(f"  {mark} {'PASS' if passed else 'FAIL':4s} {name}")
    
    total = sum(1 for v in results.values() if v)
    print(f"\n總計: {total}/{len(results)} 通過")
    return 0 if total == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
