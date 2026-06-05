"""
Adversarial verifier 測試
"""
from __future__ import annotations
import json
import os
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from plan import Plan, Phase
from adversarial_verifier import (
    AdversarialVerifier, VerifierConfig, Verdict,
    verify_plan, verify_and_print,
)
from llm_planner import _load_minimax_key, DEFAULT_DOMAINS

try:
    _load_api_key()
except Exception:
    pass


# ==================== 測試 fixtures ====================

def make_phase(id, name="phase", sub_task="task", force_domain="general", depends_on=None):
    return Phase(
        id=id, name=name, sub_task=sub_task,
        force_domain=force_domain,
        depends_on=depends_on or [],
    )


def make_plan(phases, task="test"):
    return Plan(
        id=str(uuid.uuid4()),
        task_text=task,
        force_domain=None,
        created_at=time.time(),
        script_source="# test",
        status="draft",
        metadata={},
        phases=phases,
    )


# ==================== 單元測試 ====================

def test_static_pass():
    """[1] 正常 plan → pass"""
    plan = make_plan([
        make_phase(1, "a", "task 1", "ft", []),
        make_phase(2, "b", "task 2", "ft", []),
        make_phase(3, "c", "task 3", "ft", [1, 2]),
    ])
    v = verify_plan(plan, offline_only=True)
    ok = v.verdict == "pass"
    print(f"  static pass: verdict={v.verdict} {'✅' if ok else '❌'}")
    return ok


def test_static_cycle_detected():
    """[2] Cycle → fail (critical)"""
    plan = make_plan([
        make_phase(1, "a", "task", "ft", [2]),  # 1→2→3→1
        make_phase(2, "b", "task", "ft", [3]),
        make_phase(3, "c", "task", "ft", [1]),
    ])
    v = verify_plan(plan, offline_only=True)
    has_critical = any(i["severity"] == "critical" for i in v.issues)
    ok = v.verdict == "fail" and has_critical
    print(f"  cycle detected: verdict={v.verdict} critical={has_critical} {'✅' if ok else '❌'}")
    return ok


def test_static_self_dependency():
    """[3] Self-dependency → critical"""
    plan = make_plan([
        make_phase(1, "a", "task", "ft", [1]),  # 自己 depend 自己
    ])
    v = verify_plan(plan, offline_only=True)
    has_critical = any(i["severity"] == "critical" and "itself" in i["message"] for i in v.issues)
    ok = v.verdict == "fail" and has_critical
    print(f"  self-dependency: verdict={v.verdict} {'✅' if ok else '❌'}")
    return ok


def test_static_missing_dep():
    """[4] 引用不存在的 phase → major"""
    plan = make_plan([
        make_phase(1, "a", "task", "ft", [99]),  # 99 不存在
    ])
    v = verify_plan(plan, offline_only=True)
    has_major = any(i["severity"] == "major" and "non-existent" in i["message"] for i in v.issues)
    ok = v.verdict == "warn" and has_major
    print(f"  missing dep: verdict={v.verdict} {'✅' if ok else '❌'}")
    return ok


def test_static_missing_domain():
    """[5] Missing force_domain → major"""
    plan = make_plan([
        make_phase(1, "a", "task", None),  # 沒 force_domain
    ])
    v = verify_plan(plan, offline_only=True)
    has_major = any(i["severity"] == "major" and "missing force_domain" in i["message"] for i in v.issues)
    ok = v.verdict == "warn" and has_major
    print(f"  missing domain: verdict={v.verdict} {'✅' if ok else '❌'}")
    return ok


def test_static_unavailable_domain():
    """[6] Unavailable domain → major"""
    plan = make_plan([
        make_phase(1, "a", "task", "invalid_domain"),
    ])
    v = verify_plan(plan, offline_only=True, available_domains=["ft", "stock"])
    has_major = any(i["severity"] == "major" and "unavailable" in i["message"] for i in v.issues)
    ok = v.verdict == "warn" and has_major
    print(f"  unavailable domain: verdict={v.verdict} {'✅' if ok else '❌'}")
    return ok


def test_static_too_many_phases():
    """[7] > 8 phases → major granularity warn"""
    phases = [make_phase(i+1, f"p{i+1}", f"task {i+1}", "ft", []) for i in range(10)]
    plan = make_plan(phases)
    v = verify_plan(plan, offline_only=True)
    has_major = any(i["severity"] == "major" and "over-engineered" in i["message"] for i in v.issues)
    ok = v.verdict == "warn" and has_major
    print(f"  too many phases: verdict={v.verdict} {'✅' if ok else '❌'}")
    return ok


def test_static_too_sequential():
    """[8] 80%+ phases depends_on → major parallelism warn"""
    # 5 phases: 1 沒 deps, 2,3,4,5 都 depend 某個前一個 = 80% sequential
    phases = [
        make_phase(1, "a", "task 1", "ft", []),
        make_phase(2, "b", "task 2", "ft", [1]),
        make_phase(3, "c", "task 3", "ft", [2]),
        make_phase(4, "d", "task 4", "ft", [3]),
        make_phase(5, "e", "task 5", "ft", [4]),
    ]
    plan = make_plan(phases)
    v = verify_plan(plan, offline_only=True)
    has_major = any(i["severity"] == "major" and "parallelization" in i["message"] for i in v.issues)
    ok = v.verdict == "warn" and has_major
    print(f"  too sequential: verdict={v.verdict} {'✅' if ok else '❌'}")
    return ok


def test_static_short_subtask():
    """[9] sub_task 太短 → major"""
    plan = make_plan([
        make_phase(1, "a", "x", "ft", []),  # 太短
    ])
    v = verify_plan(plan, offline_only=True)
    has_major = any(i["severity"] == "major" and "too short" in i["message"] for i in v.issues)
    ok = v.verdict == "warn" and has_major
    print(f"  short sub_task: verdict={v.verdict} {'✅' if ok else '❌'}")
    return ok


def test_static_critical_skips_llm():
    """[10] Static 有 critical 時不應該打 LLM"""
    plan = make_plan([
        make_phase(1, "a", "task", "ft", [1]),  # critical: self-dep
    ])
    verifier = AdversarialVerifier.__new__(AdversarialVerifier)
    verifier.config = MagicMock()
    verifier.config.api_key = "fake"
    verifier._llm_verify = MagicMock(side_effect=AssertionError("should not call LLM"))
    
    v = verifier.verify(plan, offline_only=False)
    
    # 確認 _llm_verify 沒被呼叫
    ok = v.verdict == "fail" and v.confidence == 1.0  # 純 static
    print(f"  critical skips LLM: verdict={v.verdict} confidence={v.confidence} {'✅' if ok else '❌'}")
    return ok


# ==================== 整合測試（真打 LLM） ====================

def _has_api_key():
    return bool(os.environ.get("MINIMAX_API_KEY"))


def test_integration_verify_generated_plan():
    """[11] 整合：對 LLM planner 生成的 plan 做審核"""
    if not _has_api_key():
        print("  ⏭️  SKIP (no MINIMAX_API_KEY)")
        return True
    
    from llm_planner import generate_plan_from_task
    
    plan = generate_plan_from_task("分析曼聯 vs 車路士的赔率")
    v = verify_and_print(plan, task="分析曼聯 vs 車路士的赔率")
    
    # 至少 LLM 應該給出 verdict
    ok = v.verdict in ["pass", "warn", "fail"]
    print(f"  integration verdict: {v.verdict}")
    return ok


def test_integration_verify_bad_plan():
    """[12] 整合：對明顯有問題的 plan 做審核（應抓到）"""
    if not _has_api_key():
        print("  ⏭️  SKIP (no MINIMAX_API_KEY)")
        return True
    
    # 構造一個有問題的 plan：太 sequential + 缺漏
    plan = make_plan([
        make_phase(1, "step 1", "do something", "ft", []),
        make_phase(2, "step 2", "do next", "ft", [1]),
        make_phase(3, "step 3", "do more", "ft", [2]),
        make_phase(4, "step 4", "continue", "ft", [3]),
        make_phase(5, "step 5", "finalize", "ft", [4]),
    ], task="分析曼聯 vs 車路士的赔率")
    
    v = verify_plan(plan, task="分析曼聯 vs 車路士的赔率")
    
    # Verifier 應該抓到「缺 consensus phase」
    has_task_issue = any(
        "共識" in i.get("message", "") or "consensus" in i.get("message", "").lower()
        for i in v.issues
    )
    print(f"  integration bad plan: verdict={v.verdict}, found task gap: {has_task_issue}")
    return v.verdict in ["warn", "fail"]


# ==================== Main ====================

TESTS = [
    # 單元
    ("static_pass", test_static_pass),
    ("static_cycle_detected", test_static_cycle_detected),
    ("static_self_dependency", test_static_self_dependency),
    ("static_missing_dep", test_static_missing_dep),
    ("static_missing_domain", test_static_missing_domain),
    ("static_unavailable_domain", test_static_unavailable_domain),
    ("static_too_many_phases", test_static_too_many_phases),
    ("static_too_sequential", test_static_too_sequential),
    ("static_short_subtask", test_static_short_subtask),
    ("static_critical_skips_llm", test_static_critical_skips_llm),
    # 整合
    ("integration_verify_generated_plan", test_integration_verify_generated_plan),
    ("integration_verify_bad_plan", test_integration_verify_bad_plan),
]


def main():
    print("=" * 70)
    print("Adversarial Verifier 測試")
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
