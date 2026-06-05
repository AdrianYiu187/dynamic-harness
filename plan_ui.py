"""
Plan UI — Terminal 視覺化 Plan DAG 與 phase 狀態

設計目標：
- 零外部依賴（stdlib + ANSI escape）
- 兩種視圖：DAG 拓樸圖 + 分層詳情列表
- CLI: --list / <plan-id> / --live <plan-id> / --no-color
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# 嘗試從 plan.py 匯入（同一目錄）
try:
    from plan import Plan, Phase
except ImportError:
    Plan = None  # type: ignore
    Phase = None  # type: ignore


# ============================================================
# 1. 狀態圖示與顏色
# ============================================================

# 狀態對應圖示
STATUS_ICONS = {
    "pending":   "○",
    "running":   "◉",
    "completed": "✓",
    "failed":    "✗",
    "skipped":   "⊘",
}

# 狀態對應 ANSI 顏色
STATUS_COLORS = {
    "pending":   "\033[90m",  # 灰
    "running":   "\033[33m",  # 黃
    "completed": "\033[32m",  # 綠
    "failed":    "\033[31m",  # 紅
    "skipped":   "\033[90m",  # 灰
}
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"


def _colorize(text: str, color: str, use_color: bool = True) -> str:
    """包 ANSI 顏色（可關閉）"""
    if not use_color or not color:
        return text
    return f"{color}{text}{RESET}"


def _status_icon(status: str) -> str:
    return STATUS_ICONS.get(status, "?")


def _status_color(status: str) -> str:
    return STATUS_COLORS.get(status, "")


# ============================================================
# 2. DAG Layout — Level assignment
# ============================================================

def _assign_levels(phases: List) -> Dict[int, int]:
    """計算每個 phase 的拓樸層級（0-based，root = 0）
    
    level(p) = max(level(d) for d in p.depends_on) + 1
    對循環依賴：fallback 為 0
    """
    phase_map = {p.id: p for p in phases}
    levels: Dict[int, int] = {}
    
    def get_level(phase) -> int:
        if phase.id in levels:
            return levels[phase.id]
        if not phase.depends_on:
            levels[phase.id] = 0
            return 0
        # 防止循環依賴：用 depth counter
        parent_levels = []
        for dep_id in phase.depends_on:
            if dep_id in phase_map:
                if dep_id not in levels:
                    levels[dep_id] = get_level(phase_map[dep_id])
                parent_levels.append(levels[dep_id])
        if not parent_levels:
            levels[phase.id] = 0
        else:
            levels[phase.id] = max(parent_levels) + 1
        return levels[phase.id]
    
    for p in phases:
        get_level(p)
    
    return levels


def _group_by_level(phases: List, levels: Dict[int, int]) -> List[List]:
    """按 level 分組，回傳 [[level0 phases], [level1 phases], ...]"""
    if not levels:
        return [list(phases)]
    max_level = max(levels.values())
    grouped = [[] for _ in range(max_level + 1)]
    for p in phases:
        grouped[levels.get(p.id, 0)].append(p)
    return grouped


# ============================================================
# 3. PlanUI 主類別
# ============================================================

@dataclass
class PlanUIConfig:
    """UI 顯示配置"""
    use_color: bool = True
    width: int = 100
    show_subtask: bool = True
    show_timing: bool = True


class PlanUI:
    """渲染 Plan 為 terminal 友善的 ASCII UI"""
    
    def __init__(self, plan, config: Optional[PlanUIConfig] = None):
        if Plan is not None and not isinstance(plan, Plan):
            raise TypeError(f"plan must be Plan instance, got {type(plan)}")
        self.plan = plan
        self.config = config or PlanUIConfig()
        self.levels = _assign_levels(plan.phases)
        self.grouped = _group_by_level(plan.phases, self.levels)
    
    # --- Header ---
    
    def render_header(self) -> str:
        """Plan 基本資訊"""
        p = self.plan
        # 進度統計
        total = len(p.phases)
        done = sum(1 for ph in p.phases if ph.status == "completed")
        failed = sum(1 for ph in p.phases if ph.status == "failed")
        running = sum(1 for ph in p.phases if ph.status == "running")
        
        status_color = _status_color(p.status) if p.status in STATUS_COLORS else ""
        status_str = _colorize(f"  {p.status.upper()}  ", status_color, self.config.use_color)
        
        lines = [
            "═" * self.config.width,
            _colorize(f"  Plan: {p.id}", BOLD, self.config.use_color),
            f"  Task: {p.task_text}",
            f"  Domain: {p.force_domain or 'auto'} | "
            f"Status: {status_str} | "
            f"Progress: {done}/{total} completed, {running} running, {failed} failed",
            "═" * self.config.width,
        ]
        return "\n".join(lines)
    
    # --- DAG view ---
    
    def render_dag(self) -> str:
        """水平 DAG 視圖：每行一個 level，節點用方框，邊用 │ 連接"""
        lines = ["", _colorize("  DAG View:", BOLD, self.config.use_color)]
        
        for level_idx, layer in enumerate(self.grouped):
            # 節點行
            node_parts = []
            for ph in layer:
                node_parts.append(self._render_dag_node(ph))
            lines.append(f"  L{level_idx}: " + "   ".join(node_parts))
            
            # 邊行（除了最後一層）
            if level_idx < len(self.grouped) - 1:
                edge_parts = []
                for ph in layer:
                    # 找哪些 child 在下一層
                    children_in_next = [
                        c for c in self.plan.phases
                        if c.depends_on and ph.id in c.depends_on
                        and self.levels.get(c.id, 0) == level_idx + 1
                    ]
                    if children_in_next:
                        edge_parts.append("   │   ")
                    else:
                        edge_parts.append("       ")
                lines.append("       " + "   ".join(edge_parts))
        
        return "\n".join(lines)
    
    def _render_dag_node(self, phase) -> str:
        """渲染單個 DAG 節點：[id] name (icon)"""
        icon = _status_icon(phase.status)
        color = _status_color(phase.status)
        icon_colored = _colorize(icon, color, self.config.use_color)
        return _colorize(f"[{phase.id}]", BOLD, self.config.use_color) + \
               f" {phase.name[:20]} {icon_colored}"
    
    # --- Phase list view ---
    
    def render_phase_list(self) -> str:
        """分層詳情列表"""
        lines = ["", _colorize("  Phase Details:", BOLD, self.config.use_color)]
        
        for level_idx, layer in enumerate(self.grouped):
            lines.append("")
            for ph in layer:
                lines.append(self._render_phase_detail(ph))
                # 連接線
                if ph is not layer[-1] or level_idx < len(self.grouped) - 1:
                    lines.append("  │")
        
        return "\n".join(lines)
    
    def _render_phase_detail(self, phase) -> str:
        """單個 phase 詳細資訊"""
        icon = _status_icon(phase.status)
        color = _status_color(phase.status)
        icon_colored = _colorize(icon, color, self.config.use_color)
        status_colored = _colorize(phase.status.upper(), color, self.config.use_color)
        
        # 第一行：ID + 名稱 + 狀態
        first_line = (
            f"  ├─ "
            f"{_colorize(f'[{phase.id}]', BOLD, self.config.use_color)} "
            f"{phase.name} "
            f"{icon_colored} {status_colored}"
        )
        
        # 第二行：subtask（截短）
        lines = [first_line]
        if self.config.show_subtask and phase.sub_task:
            subtask = phase.sub_task[:80] + ("..." if len(phase.sub_task) > 80 else "")
            lines.append(f"  │  " + _colorize(f"subtask: {subtask}", DIM, self.config.use_color))
        
        # 第三行：meta（domain + timeout + deps + timing）
        meta_parts = [f"domain: {phase.force_domain or 'auto'}"]
        if phase.timeout_s:
            meta_parts.append(f"timeout: {phase.timeout_s}s")
        if phase.depends_on:
            meta_parts.append(f"deps: [{', '.join(map(str, phase.depends_on))}]")
        if self.config.show_timing:
            if phase.started_at and phase.completed_at:
                dur = phase.completed_at - phase.started_at
                meta_parts.append(f"took: {dur:.1f}s")
            elif phase.started_at:
                dur = time.time() - phase.started_at
                meta_parts.append(f"running: {dur:.1f}s")
        lines.append(f"  │  " + _colorize(" | ".join(meta_parts), DIM, self.config.use_color))
        
        # 第四行：error（如果 failed）
        if phase.error:
            err = phase.error[:80] + ("..." if len(phase.error) > 80 else "")
            lines.append(f"  │  " + _colorize(f"error: {err}", "\033[31m", self.config.use_color))
        
        return "\n".join(lines)
    
    # --- Legend ---
    
    def render_legend(self) -> str:
        """狀態圖示說明"""
        lines = ["", _colorize("  Legend:", DIM, self.config.use_color)]
        for status, icon in STATUS_ICONS.items():
            color = _status_color(status)
            icon_colored = _colorize(icon, color, self.config.use_color)
            lines.append(f"    {icon_colored} = {status}")
        return "\n".join(lines)
    
    # --- Full render ---
    
    def render(self) -> str:
        """完整 plan 視圖"""
        parts = [
            self.render_header(),
            self.render_dag(),
            self.render_phase_list(),
            self.render_legend(),
            "",
        ]
        return "\n".join(parts)
    
    def __str__(self) -> str:
        return self.render()


# ============================================================
# 4. Plan 載入（從 SQLite）
# ============================================================

def _default_db_path() -> str:
    """預設 DB 路徑（與 plan.py 同步）
    
    優先順序：
    1. PLAN_DB_PATH 環境變數（給 sub-process / 測試用）
    2. skill_dir/plan_registry.db（與 plan.py 同步）
    """
    env_path = os.environ.get("PLAN_DB_PATH")
    if env_path:
        return env_path
    skill_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(skill_dir, "plan_registry.db")


def load_plan(plan_id: str, db_path: Optional[str] = None) -> Optional[Plan]:
    """從 SQLite 載入 plan"""
    if Plan is None:
        raise ImportError("plan module not available")
    db_path = db_path or _default_db_path()
    if not os.path.exists(db_path):
        return None
    
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, task_text, force_domain, created_at, script_source, status, result_json, metadata_json "
            "FROM plans WHERE id = ?",
            (plan_id,),
        ).fetchone()
        if not row:
            return None

        plan_id, task_text, force_domain, created_at, script_source, status, result_json, metadata_json = row
        import json
        meta = json.loads(metadata_json) if metadata_json else {}
        
        # 載入 phases
        phase_rows = conn.execute(
            "SELECT phase_id, name, sub_task, force_domain, depends_on, timeout_s, stop_condition, "
            "status, envelope_id, started_at, completed_at, error, parallel "
            "FROM plan_phases WHERE plan_id = ? ORDER BY phase_id",
            (plan_id,),
        ).fetchall()
        
        phases = []
        for prow in phase_rows:
            (pid, name, sub_task, pforce_domain, depends_on_json, timeout_s, stop_condition,
             pstatus, envelope_id, started_at, completed_at, error, parallel) = prow
            deps = json.loads(depends_on_json) if depends_on_json else []
            phases.append(Phase(
                id=pid, name=name, sub_task=sub_task, force_domain=pforce_domain,
                depends_on=deps, timeout_s=timeout_s, stop_condition=stop_condition,
                status=pstatus, envelope_id=envelope_id,
                started_at=started_at, completed_at=completed_at,
                error=error, parallel=bool(parallel),
            ))
        
        return Plan(
            id=plan_id, task_text=task_text, force_domain=force_domain,
            created_at=created_at, script_source=script_source,
            status=status, result_json=result_json, metadata=meta, phases=phases,
        )
    finally:
        conn.close()


def list_plans(db_path: Optional[str] = None, limit: int = 20) -> List[Tuple[str, str, str, int]]:
    """列出 DB 中所有 plan：(id, task, status, phase_count)"""
    db_path = db_path or _default_db_path()
    if not os.path.exists(db_path):
        return []

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT p.id, p.task_text, p.status, COUNT(ph.phase_id) "
            "FROM plans p LEFT JOIN plan_phases ph ON p.id = ph.plan_id "
            "GROUP BY p.id ORDER BY p.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [(r[0], r[1], r[2], r[3]) for r in rows]
    finally:
        conn.close()


# ============================================================
# 5. CLI
# ============================================================

def _render_list(limit: int, use_color: bool):
    """渲染 plan 列表"""
    plans = list_plans(limit=limit)
    if not plans:
        print("No plans found in DB.")
        return
    
    width = 100
    print("═" * width)
    print(_colorize(f"  {len(plans)} recent plans", BOLD, use_color))
    print("═" * width)
    print(f"  {'PLAN ID':<20} {'STATUS':<12} {'PHASES':<8} TASK")
    print("  " + "-" * (width - 2))
    for pid, task, status, count in plans:
        color = _status_color(status)
        status_colored = _colorize(status, color, use_color)
        task_short = task[:55] + ("..." if len(task) > 55 else "")
        print(f"  {pid:<20} {status_colored:<22} {count:<8} {task_short}")


def _render_plan(plan_id: str, use_color: bool):
    """渲染單個 plan"""
    plan = load_plan(plan_id)
    if not plan:
        print(f"Plan {plan_id} not found in DB.", file=sys.stderr)
        sys.exit(1)
    ui = PlanUI(plan, PlanUIConfig(use_color=use_color))
    print(ui.render())


def _watch_plan(plan_id: str, use_color: bool, interval: float):
    """Watch 模式：每 N 秒重渲染"""
    print(f"Watching plan {plan_id} (Ctrl+C to exit, interval={interval}s)")
    try:
        while True:
            # 清除螢幕
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.flush()
            
            plan = load_plan(plan_id)
            if not plan:
                print(f"Plan {plan_id} not found.")
                return
            ui = PlanUI(plan, PlanUIConfig(use_color=use_color))
            print(ui.render())
            
            # 終止條件：plan 已完成/失敗/取消
            if plan.status in ("completed", "failed", "cancelled"):
                print(_colorize(f"\n  Plan {plan.status}. Exiting watch.", DIM, use_color))
                return
            
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nWatch stopped.")


def main():
    parser = argparse.ArgumentParser(
        description="Plan UI — Terminal 視覺化 Plan DAG 與 phase 狀態"
    )
    parser.add_argument("plan_id", nargs="?", help="Plan ID 來 render 單個 plan")
    parser.add_argument("--list", action="store_true", help="列出所有 plan")
    parser.add_argument("--limit", type=int, default=20, help="--list 顯示數量（default: 20）")
    parser.add_argument("--live", action="store_true", help="Watch 模式（持續 re-render）")
    parser.add_argument("--interval", type=float, default=2.0, help="Watch 間隔秒數（default: 2）")
    parser.add_argument("--no-color", action="store_true", help="關閉 ANSI 顏色")
    
    args = parser.parse_args()
    use_color = not args.no_color and sys.stdout.isatty()
    
    if args.list:
        _render_list(args.limit, use_color)
    elif args.plan_id and args.live:
        _watch_plan(args.plan_id, use_color, args.interval)
    elif args.plan_id:
        _render_plan(args.plan_id, use_color)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
