#!/usr/bin/env python3
"""
plan_cli.py — Plan-in-Code 命令列（v2.0 MVP）
================================================

子命令：
- generate: 從手寫 script 生成 plan
- execute:  執行 plan
- resume:   從 checkpoint 恢復
- status:   顯示 plan 詳情
- list:     列出所有 plan
- traces:   顯示 trace log
- delete:   刪除 plan

日期：2026-06-05
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

# 把當前目錄加入 sys.path（讓 plan.py 可 import）
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))


def cmd_generate(args):
    from plan import parse_script_to_plan, StaticAnalyzer, save_plan
    
    # 讀取 script
    if args.script_file:
        script_source = Path(args.script_file).read_text(encoding="utf-8")
    elif args.script:
        script_source = args.script
    else:
        print("錯誤：必須提供 --script 或 --script-file", file=sys.stderr)
        return 1
    
    # 靜態分析
    analyzer = StaticAnalyzer()
    is_safe, reason = analyzer.analyze(script_source)
    if not is_safe:
        print(json.dumps({"error": "static_analysis_failed", "reason": reason}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    
    # 解析成 plan
    plan_obj = parse_script_to_plan(script_source, task_text=args.task, force_domain=args.force_domain)
    plan_obj.status = "validated"
    save_plan(plan_obj)
    
    # 印摘要
    print(json.dumps({
        "plan_id": plan_obj.id,
        "status": plan_obj.status,
        "task_text": plan_obj.task_text,
        "phase_count": len(plan_obj.phases),
        "phases": [
            {"id": p.id, "name": p.name, "sub_task": p.sub_task, "depends_on": p.depends_on, "force_domain": p.force_domain}
            for p in plan_obj.phases
        ],
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_execute(args):
    from plan import load_plan, PlanExecutor
    
    plan_obj = load_plan(args.plan_id)
    if not plan_obj:
        print(json.dumps({"error": "plan_not_found", "plan_id": args.plan_id}, ensure_ascii=False), file=sys.stderr)
        return 1
    
    executor = PlanExecutor(plan_obj, verbose=args.verbose)
    result = executor.execute()
    
    print(json.dumps({
        "plan_id": result.id,
        "status": result.status,
        "phases": [
            {"id": p.id, "status": p.status, "envelope_id": p.envelope_id, "error": p.error}
            for p in result.phases
        ],
    }, ensure_ascii=False, indent=2))
    return 0 if result.status == "completed" else 1


def cmd_resume(args):
    """Resume 等同 execute：會自動跳過已完成的 phase"""
    return cmd_execute(args)


def cmd_status(args):
    from plan import load_plan
    
    plan_obj = load_plan(args.plan_id)
    if not plan_obj:
        print(json.dumps({"error": "plan_not_found", "plan_id": args.plan_id}, ensure_ascii=False), file=sys.stderr)
        return 1
    
    print(json.dumps(plan_obj.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_list(args):
    from plan import list_plans
    
    plans = list_plans(status=args.status, limit=args.limit)
    print(json.dumps(plans, ensure_ascii=False, indent=2))
    return 0


def cmd_traces(args):
    from plan import get_plan_traces
    
    traces = get_plan_traces(args.plan_id)
    print(json.dumps(traces, ensure_ascii=False, indent=2))
    return 0


def cmd_delete(args):
    import persistence
    from plan import _connect
    
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM plans WHERE id = ?", (args.plan_id,))
        conn.commit()
        deleted = cursor.rowcount
    
    print(json.dumps({"plan_id": args.plan_id, "deleted": deleted}, ensure_ascii=False, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description="Plan-in-Code CLI v2.0 MVP")
    subparsers = parser.add_subparsers(dest="cmd", required=True)
    
    # generate
    p_gen = subparsers.add_parser("generate", help="從 script 生成 plan")
    p_gen.add_argument("--script", help="inline Python script")
    p_gen.add_argument("--script-file", help="從檔案讀取 script")
    p_gen.add_argument("--task", required=True, help="原始任務描述")
    p_gen.add_argument("--force-domain", help="預設 domain")
    p_gen.set_defaults(func=cmd_generate)
    
    # execute
    p_exec = subparsers.add_parser("execute", help="執行 plan")
    p_exec.add_argument("--plan-id", required=True)
    p_exec.add_argument("--verbose", action="store_true")
    p_exec.set_defaults(func=cmd_execute)
    
    # resume
    p_resume = subparsers.add_parser("resume", help="從 checkpoint 恢復")
    p_resume.add_argument("--plan-id", required=True)
    p_resume.add_argument("--verbose", action="store_true")
    p_resume.set_defaults(func=cmd_resume)
    
    # status
    p_status = subparsers.add_parser("status", help="顯示 plan 詳情")
    p_status.add_argument("--plan-id", required=True)
    p_status.set_defaults(func=cmd_status)
    
    # list
    p_list = subparsers.add_parser("list", help="列出所有 plan")
    p_list.add_argument("--status", help="過濾狀態")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.set_defaults(func=cmd_list)
    
    # traces
    p_traces = subparsers.add_parser("traces", help="顯示 trace log")
    p_traces.add_argument("--plan-id", required=True)
    p_traces.set_defaults(func=cmd_traces)
    
    # delete
    p_delete = subparsers.add_parser("delete", help="刪除 plan")
    p_delete.add_argument("--plan-id", required=True)
    p_delete.set_defaults(func=cmd_delete)
    
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
