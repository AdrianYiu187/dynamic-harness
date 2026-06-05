"""
plan.py — Plan-in-Code 核心（v2.0 MVP）
==========================================

包含：
- Plan / Phase 資料模型
- StaticAnalyzer（AST 安全分析）
- PlanGenerator（手寫 script + LLM fallback）
- PlanExecutor（checkpoint + resume）

設計細節見 references/plan-in-code-architecture.md

日期：2026-06-05
"""
from __future__ import annotations
import ast
import logging
import re
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from persistence import _get_db_path, SCHEMA_SQL, DEFAULT_DB_PATH

log = logging.getLogger("dynamic_harness.plan")

_lock = threading.Lock()


# ============================================================
# 1. 資料模型
# ============================================================

@dataclass
class Phase:
    """Plan 中的單個 phase"""
    id: int
    name: str
    sub_task: str
    force_domain: Optional[str] = None
    depends_on: List[int] = field(default_factory=list)
    timeout_s: int = 60
    stop_condition: Optional[str] = None
    
    # 執行狀態（不入 LLM prompt，由 executor 填）
    status: str = "pending"           # pending / running / completed / failed / skipped
    envelope_id: Optional[int] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None
    parallel: bool = False  # v2.1: parallel mode 備用欄位（MVP 不啟用）
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, d: dict) -> "Phase":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Plan:
    """完整的 plan（生成的 orchestration）"""
    id: str                            # UUID
    task_text: str
    force_domain: Optional[str]
    created_at: float
    script_source: str                 # 生成的 Python source
    status: str = "draft"              # draft / validated / running / completed / failed / cancelled
    result_json: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    phases: List[Phase] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_text": self.task_text,
            "force_domain": self.force_domain,
            "created_at": self.created_at,
            "script_source": self.script_source,
            "status": self.status,
            "result_json": self.result_json,
            "metadata": self.metadata,
            "phases": [p.to_dict() for p in self.phases],
        }
    
    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        phases = [Phase.from_dict(p) for p in d.get("phases", [])]
        return cls(
            id=d["id"],
            task_text=d["task_text"],
            force_domain=d.get("force_domain"),
            created_at=d["created_at"],
            script_source=d["script_source"],
            status=d.get("status", "draft"),
            result_json=d.get("result_json"),
            metadata=d.get("metadata", {}),
            phases=phases,
        )


# ============================================================
# 2. 持久化
# ============================================================

@contextmanager
def _connect(db_path: Optional[Path] = None):
    path = _get_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        yield conn
    finally:
        conn.close()


def save_plan(plan: Plan, db_path: Optional[Path] = None) -> None:
    """儲存 plan + 全部 phases（覆蓋）"""
    import json
    with _lock:
        with _connect(db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO plans
                (id, task_text, force_domain, created_at, script_source, status, result_json, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.id,
                    plan.task_text,
                    plan.force_domain,
                    plan.created_at,
                    plan.script_source,
                    plan.status,
                    plan.result_json,
                    json.dumps(plan.metadata),
                ),
            )
            # 刪除舊 phases
            conn.execute("DELETE FROM plan_phases WHERE plan_id = ?", (plan.id,))
            # 寫入新 phases
            for p in plan.phases:
                conn.execute(
                    """
                    INSERT INTO plan_phases
                    (plan_id, phase_id, name, sub_task, force_domain, depends_on, parallel, timeout_s,
                     stop_condition, status, envelope_id, started_at, completed_at, error)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan.id, p.id, p.name, p.sub_task, p.force_domain,
                        json.dumps(p.depends_on), 1 if p.parallel else 0, p.timeout_s,
                        p.stop_condition, p.status, p.envelope_id,
                        p.started_at, p.completed_at, p.error,
                    ),
                )
            conn.commit()


def update_plan_status(plan_id: str, status: str, result_json: Optional[str] = None,
                       db_path: Optional[Path] = None) -> None:
    """更新 plan 整體狀態"""
    with _lock:
        with _connect(db_path) as conn:
            if result_json is not None:
                conn.execute(
                    "UPDATE plans SET status = ?, result_json = ? WHERE id = ?",
                    (status, result_json, plan_id),
                )
            else:
                conn.execute(
                    "UPDATE plans SET status = ? WHERE id = ?",
                    (status, plan_id),
                )
            conn.commit()


def update_phase(plan_id: str, phase: Phase, db_path: Optional[Path] = None) -> None:
    """更新單個 phase"""
    with _lock:
        with _connect(db_path) as conn:
            conn.execute(
                """
                UPDATE plan_phases SET
                    status = ?, envelope_id = ?, started_at = ?, completed_at = ?, error = ?
                WHERE plan_id = ? AND phase_id = ?
                """,
                (phase.status, phase.envelope_id, phase.started_at, phase.completed_at,
                 phase.error, plan_id, phase.id),
            )
            conn.commit()


def add_trace(plan_id: str, phase_id: Optional[int], event: str, message: str,
              db_path: Optional[Path] = None) -> None:
    """新增一筆 trace"""
    with _lock:
        with _connect(db_path) as conn:
            conn.execute(
                "INSERT INTO plan_traces (plan_id, phase_id, ts, event, message) VALUES (?, ?, ?, ?, ?)",
                (plan_id, phase_id, time.time(), event, message),
            )
            conn.commit()


def load_plan(plan_id: str, db_path: Optional[Path] = None) -> Optional[Plan]:
    """從 SQLite 載入 plan"""
    import json
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if not row:
            return None
        plan = Plan(
            id=row[0],
            task_text=row[1],
            force_domain=row[2],
            created_at=row[3],
            script_source=row[4],
            status=row[5],
            result_json=row[6],
            metadata=json.loads(row[7]) if row[7] else {},
        )
        phase_rows = conn.execute(
            "SELECT * FROM plan_phases WHERE plan_id = ? ORDER BY phase_id",
            (plan_id,),
        ).fetchall()
        for pr in phase_rows:
            plan.phases.append(Phase(
                id=pr[1],
                name=pr[2] or "",
                sub_task=pr[3] or "",
                force_domain=pr[4],
                depends_on=json.loads(pr[5]) if pr[5] else [],
                parallel=bool(pr[6]),
                timeout_s=pr[7] or 60,
                stop_condition=pr[8],
                status=pr[9] or "pending",
                envelope_id=pr[10],
                started_at=pr[11],
                completed_at=pr[12],
                error=pr[13],
            ))
    return plan


def list_plans(status: Optional[str] = None, limit: int = 50,
               db_path: Optional[Path] = None) -> List[dict]:
    """列出 plans（不載入完整 phase 資料）"""
    import json
    where = "WHERE 1=1"
    params: list = []
    if status:
        where += " AND status = ?"
        params.append(status)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT id, task_text, force_domain, created_at, status,
                   (SELECT COUNT(*) FROM plan_phases WHERE plan_id = plans.id) as phase_count,
                   (SELECT COUNT(*) FROM plan_phases WHERE plan_id = plans.id AND status = 'completed') as completed_count
            FROM plans
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    return [
        {
            "id": r[0],
            "task_text": r[1],
            "force_domain": r[2],
            "created_at": r[3],
            "status": r[4],
            "phase_count": r[5],
            "completed_count": r[6],
        }
        for r in rows
    ]


def get_plan_traces(plan_id: str, db_path: Optional[Path] = None) -> List[dict]:
    """取得 plan 的所有 trace 事件"""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT phase_id, ts, event, message FROM plan_traces WHERE plan_id = ? ORDER BY id",
            (plan_id,),
        ).fetchall()
    return [
        {"phase_id": r[0], "ts": r[1], "event": r[2], "message": r[3]}
        for r in rows
    ]


# ============================================================
# 3. StaticAnalyzer — AST 安全分析
# ============================================================

class StaticAnalyzer:
    """靜態分析生成的 script，拒絕危險操作"""
    
    FORBIDDEN_NAMES = {
        # 危險函式
        "exec", "eval", "compile", "__import__",
        # 檔案刪除
        "os.remove", "os.unlink", "os.rmdir", "shutil.rmtree",
        # 系統指令
        "os.system", "subprocess.run", "subprocess.call", "subprocess.Popen",
    }
    
    FORBIDDEN_ATTRS = {
        # os/shutil 危險模組屬性
        "system", "remove", "unlink", "rmdir", "rmtree", "popen", "call",
    }
    
    ALLOWED_IMPORT_MODULES = {
        "dynamic_harness", "dynamic_harness.unified_router",
        "dynamic_harness.persistence", "dynamic_harness.metrics",
        "dynamic_harness.cost", "dynamic_harness.web_search",
        "dynamic_harness.llm_judge", "dynamic_harness.plan",
        "dynamic_harness.adapters.ft_adapter",
        "dynamic_harness.adapters.stock_adapter",
        "dynamic_harness.adapters.coding_adapter",
        "dynamic_harness.adapters.general_adapter",
        "dynamic_harness.adapters.hermes_team_adapter",
    }
    
    def analyze(self, source: str) -> Tuple[bool, str]:
        """回傳 (is_safe, reason)
        
        is_safe=True 表示可執行；False 表示拒絕並附 reason
        """
        if not source or not source.strip():
            return False, "Empty source"
        
        # 限制長度（避免 LLM 失控）
        if len(source.splitlines()) > 100:
            return False, f"Source too long ({len(source.splitlines())} lines > 100)"
        
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            return False, f"Syntax error: {e}"
        
        for node in ast.walk(tree):
            # 1. 檢查 import
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod = alias.name
                    if not any(mod == a or mod.startswith(a + ".") for a in self.ALLOWED_IMPORT_MODULES):
                        return False, f"Disallowed import: {mod}"
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if not any(mod == a or mod.startswith(a + ".") for a in self.ALLOWED_IMPORT_MODULES):
                    return False, f"Disallowed import from: {mod}"
            
            # 2. 檢查危險函式呼叫
            if isinstance(node, ast.Call):
                # 2a. 直接呼叫：exec(...), eval(...)
                if isinstance(node.func, ast.Name):
                    if node.func.id in {"exec", "eval", "compile"}:
                        return False, f"Forbidden call: {node.func.id}"
                
                # 2b. 屬性呼叫：os.system(...), shutil.rmtree(...)
                if isinstance(node.func, ast.Attribute):
                    full_name = self._get_attr_name(node.func)
                    if full_name in self.FORBIDDEN_NAMES:
                        return False, f"Forbidden call: {full_name}"
                    if node.func.attr in self.FORBIDDEN_ATTRS:
                        return False, f"Forbidden attribute: {node.func.attr}"
        
        return True, "ok"
    
    def _get_attr_name(self, node: ast.Attribute) -> str:
        """取得屬性的完整名稱（如 os.system）"""
        parts = [node.attr]
        cur = node.value
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))


# ============================================================
# 4. PlanGenerator — 手寫 script + LLM fallback
# ============================================================

SYSTEM_PROMPT = """你是一個 plan generator。給定用戶任務，生成 Python orchestration script。

可用工具（已 import 過，可直接用）：
- router = UnifiedRouter()  # 已建立
- router.route(task_text, force_domain=..., parallel=...)  # 回傳 RouteEnvelope

要求：
1. 拆成 2-5 個 phases（每個 phase 一個獨立的子任務）
2. 用 force_domain 指定 domain（不知道就留空）
3. 設 depends_on=[phase_id] 表達依賴關係
4. 寫成可執行的 Python script
5. 包含 if __name__ == "__main__": 入口
6. 最多 80 行

可用 domain 值：ft / stock / coding / hermes / general
不要 import 其他模組。
不要使用 eval / exec / os / subprocess。

回應格式：只回傳 Python code block，不要其他文字。"""


def parse_script_to_plan(script_source: str, task_text: str,
                         force_domain: Optional[str] = None) -> Plan:
    """從 Python script 解析出 Plan 物件（簡化版：找 router.route() 呼叫 + 注釋推導 phases）
    
    解析規則：
    - 找到所有 router.route(...) 呼叫，每個視為一個 phase
    - depends_on：從 # depends_on: [1, 2] 注釋讀取
    - force_domain：從 # domain: stock 注釋讀取
    - parallel：從 # parallel: True 注釋讀取
    """
    # 嘗試提取 router.route() 呼叫 + 上方注釋
    lines = script_source.splitlines()
    phases: List[Phase] = []
    current_comments: List[str] = []
    phase_id = 0
    
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            current_comments.append(stripped)
            continue
        if not stripped or stripped.startswith("import") or stripped.startswith("from"):
            current_comments = []
            continue
        
        # 找 router.route() 呼叫
        m = re.search(r"router\.route\s*\(\s*['\"](.+?)['\"]", line)
        if m:
            phase_id += 1
            sub_task = m.group(1)
            
            # 從注釋推導
            force = force_domain
            depends_on: List[int] = []
            parallel = False
            name = f"Phase {phase_id}: {sub_task[:30]}"
            timeout_s = 60
            
            for c in current_comments:
                if m2 := re.search(r"domain:\s*(\w+)", c):
                    force = m2.group(1)
                if m2 := re.search(r"depends_on:\s*\[([^\]]*)\]", c):
                    try:
                        depends_on = [int(x.strip()) for x in m2.group(1).split(",") if x.strip()]
                    except ValueError:
                        depends_on = []
                if "parallel" in c.lower() and "true" in c.lower():
                    parallel = True
                if m2 := re.search(r"name:\s*(.+)", c):
                    name = m2.group(1).strip()
                if m2 := re.search(r"timeout:\s*(\d+)", c):
                    timeout_s = int(m2.group(1))
            
            # Fallback：從 router.route() 的 force_domain 參數提取
            if force is None:
                if m3 := re.search(r'force_domain\s*=\s*["\'](\w+)["\']', line):
                    force = m3.group(1)
            
            phases.append(Phase(
                id=phase_id,
                name=name,
                sub_task=sub_task,
                force_domain=force,
                depends_on=depends_on,
                parallel=parallel,
                timeout_s=timeout_s,
            ))
            current_comments = []
    
    return Plan(
        id=str(uuid.uuid4()),
        task_text=task_text,
        force_domain=force_domain,
        created_at=time.time(),
        script_source=script_source,
        status="draft",
        metadata={"generated_by": "manual", "phase_count": len(phases)},
        phases=phases,
    )


def generate_plan_with_llm(task_text: str, force_domain: Optional[str] = None,
                            model: str = "MiniMax-M2.7-highspeed") -> Plan:
    """用 LLM 生成 plan（v2.0 MVP：若 LLM 不可用則拋例外）
    
    用戶可選擇：
    1. 手寫 script → 傳給 parse_script_to_plan
    2. LLM 生成 → 用本函式
    """
    try:
        # 嘗試呼叫 minimax API（透過 hermes_tools）
        from hermes_tools import web_search
        # 這裡用 web_search 純粹是為了 import minimax 客戶端
        # 實際上應該直接呼叫 minimax API
        raise NotImplementedError(
            "LLM 規劃需要 minimax API 客戶端。請先用 plan_generate_manual() 手寫 script，"
            "或安裝 minimax SDK 後實作。"
        )
    except ImportError:
        raise NotImplementedError("minimax SDK 不可用")


# ============================================================
# 5. PlanExecutor
# ============================================================

class PlanExecutor:
    """執行 plan，支援 checkpoint + resume"""
    
    def __init__(self, plan: Plan, db_path: Optional[Path] = None,
                 verbose: bool = False):
        self.plan = plan
        self.db_path = db_path
        self.verbose = verbose
        self._router = None  # lazy init
    
    @property
    def router(self):
        if self._router is None:
            from unified_router import UnifiedRouter
            self._router = UnifiedRouter(verbose=False, enable_cache=True)
        return self._router
    
    def _topological_sort(self, phases: List[Phase]) -> List[Phase]:
        """依 depends_on 排序 phases"""
        phase_map = {p.id: p for p in phases}
        visited = set()
        result = []
        
        def visit(phase: Phase):
            if phase.id in visited:
                return
            visited.add(phase.id)
            for dep_id in phase.depends_on:
                if dep_id in phase_map:
                    visit(phase_map[dep_id])
            result.append(phase)
        
        for p in phases:
            visit(p)
        return result
    
    def execute(self) -> Plan:
        """執行 plan（會跳過 status=completed 的 phase，支援 resume + 自動 parallel）
        
        採用 dataflow model：
        1. 找出所有 deps 已 satisfied 且未跑的 phase（ready set）
        2. 用 ThreadPoolExecutor 同時跑（max_workers=4）
        3. 等待全部完成
        4. 若任一 failed → 標記所有未跑的下游為 skipped，結束
        5. 回到步驟 1
        """
        add_trace(self.plan.id, None, "execute_start", f"Plan has {len(self.plan.phases)} phases", self.db_path)
        self.plan.status = "running"
        update_plan_status(self.plan.id, "running", db_path=self.db_path)
        
        max_workers = 4
        total_runs = 0
        
        while True:
            # 1. 找 ready phases
            ready = self._find_ready_phases()
            if not ready:
                # 沒 ready 的，看是否還有 pending
                pending = [p for p in self.plan.phases if p.status == "pending"]
                if pending:
                    # 還有 pending 但都沒 deps satisfied → 失敗
                    for p in pending:
                        p.status = "skipped"
                        p.error = "Dependencies failed or unreachable"
                        p.completed_at = time.time()
                        update_phase(self.plan.id, p, self.db_path)
                        add_trace(self.plan.id, p.id, "phase_skip", p.error, self.db_path)
                    self.plan.status = "failed"
                    update_plan_status(self.plan.id, "failed", result_json=self._serialize_result(), db_path=self.db_path)
                    add_trace(self.plan.id, None, "execute_failed", "Pending phases skipped due to failed deps", self.db_path)
                else:
                    self.plan.status = "completed"
                    update_plan_status(self.plan.id, "completed", result_json=self._serialize_result(), db_path=self.db_path)
                    add_trace(self.plan.id, None, "execute_complete", f"All phases completed ({total_runs} runs)", self.db_path)
                break
            
            # 2. 跑 ready phases（parallel）
            add_trace(
                self.plan.id, None, "wave_start",
                f"Running {len(ready)} phase(s) in parallel: {[p.id for p in ready]}",
                self.db_path,
            )
            
            with ThreadPoolExecutor(max_workers=min(max_workers, len(ready))) as pool:
                futures = {pool.submit(self._execute_phase, p): p for p in ready}
                for fut in as_completed(futures):
                    phase = futures[fut]
                    try:
                        fut.result()
                    except Exception as e:
                        phase.status = "failed"
                        phase.error = f"{type(e).__name__}: {e}"
                        update_phase(self.plan.id, phase, self.db_path)
                        log.exception(f"Phase {phase.id} crashed")
            
            total_runs += len(ready)
            
            # 3. 檢查是否有 failed（短路）
            if any(p.status == "failed" for p in ready):
                failed_ids = [p.id for p in ready if p.status == "failed"]
                add_trace(
                    self.plan.id, None, "wave_failed",
                    f"Phases failed: {failed_ids}, marking downstream as skipped",
                    self.db_path,
                )
                # 標記所有 pending 為 skipped
                for p in self.plan.phases:
                    if p.status == "pending":
                        p.status = "skipped"
                        p.error = "Upstream phase failed"
                        p.completed_at = time.time()
                        update_phase(self.plan.id, p, self.db_path)
                self.plan.status = "failed"
                update_plan_status(self.plan.id, "failed", result_json=self._serialize_result(), db_path=self.db_path)
                add_trace(self.plan.id, None, "execute_failed", f"Failed phases: {failed_ids}", self.db_path)
                return self.plan
        
        return self.plan
    
    def _find_ready_phases(self) -> List[Phase]:
        """找出所有 deps 已 satisfied 且未跑的 phase"""
        phase_map = {p.id: p for p in self.plan.phases}
        ready = []
        for p in self.plan.phases:
            if p.status != "pending":
                continue
            deps_ok = True
            for dep_id in p.depends_on:
                dep = phase_map.get(dep_id)
                if dep is None or dep.status != "completed":
                    deps_ok = False
                    break
            if deps_ok:
                ready.append(p)
        return ready
    
    def _execute_phase(self, phase: Phase) -> None:
        """執行單個 phase（thread-safe）"""
        phase.status = "running"
        phase.started_at = time.time()
        update_phase(self.plan.id, phase, self.db_path)
        add_trace(self.plan.id, phase.id, "phase_start", phase.sub_task, self.db_path)
        
        if self.verbose:
            print(f"  [Phase {phase.id}] {phase.name}: {phase.sub_task}")
        
        # 執行
        try:
            env = self.router.route(
                phase.sub_task,
                force_domain=phase.force_domain,
            )
            
            phase.completed_at = time.time()
            if env.error:
                phase.status = "failed"
                phase.error = env.error
            else:
                phase.status = "completed"
                # 嘗試存 envelope_id（如有 envelopes table）
                phase.envelope_id = self._save_envelope(env, phase)
            
            update_phase(self.plan.id, phase, self.db_path)
            add_trace(self.plan.id, phase.id, "phase_complete", f"Status: {phase.status}", self.db_path)
        
        except Exception as e:
            phase.status = "failed"
            phase.error = f"{type(e).__name__}: {e}"
            phase.completed_at = time.time()
            update_phase(self.plan.id, phase, self.db_path)
            add_trace(self.plan.id, phase.id, "phase_error", str(e), self.db_path)
            log.exception(f"Phase {phase.id} failed")
    
    def _save_envelope(self, env, phase: Phase) -> Optional[int]:
        """存 envelope 到 SQLite，回傳 ID"""
        try:
            import json
            from persistence import _get_db_path
            import sqlite3
            with sqlite3.connect(str(_get_db_path(self.db_path)), timeout=5.0) as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO envelopes
                    (ts, task_text, force_domain, envelope_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        time.time(),
                        phase.sub_task,
                        phase.force_domain,
                        json.dumps(env.to_dict() if hasattr(env, "to_dict") else dict(env), ensure_ascii=False),
                    ),
                )
                conn.commit()
                return cursor.lastrowid
        except Exception as e:
            log.debug(f"save_envelope failed: {e}")
            return None
    
    def _serialize_result(self) -> str:
        """序列化 plan 結果"""
        import json
        return json.dumps(
            {
                "phases": [
                    {
                        "id": p.id, "name": p.name, "status": p.status,
                        "envelope_id": p.envelope_id, "error": p.error,
                    }
                    for p in self.plan.phases
                ]
            },
            ensure_ascii=False,
        )
