# Plan-in-Code 架構設計（v2.0 規劃）

**日期**：2026-06-05
**作者**：MiniMax-M3
**狀態**：設計階段，待 P3-2 MVP 確認後實作
**預估總工作量**：8-12 天（P3-2: 2-3 天 / P3-3: 5-7 天）

---

## 1. 問題與動機

### 1.1 現狀（v1.5）

v1.5 的 `multi_route()` 雖然能拆分 + 並行，但**拆分策略是靜態的**：
- 啟發式：regex 連接詞切分
- LLM 拆分：固定 prompt

**侷限**：
- 對複雜任務（如「分析 A 公司財務 + 跟競爭對手 B 比較 + 寫投資建議報告」），靜態拆分不知道怎麼組織
- 每個 sub_task 的 force_domain、parallel、depends_on 都是固定的
- 沒有 stop condition 概念
- 沒有可恢復的 checkpoint
- 沒有 adversarial 驗證

### 1.2 目標

引入 **Plan-in-Code**（參考 Claude Code 2026-05-28 Dynamic Workflows）：
- LLM 為每個任務**即場生成** Python orchestration script
- Script 包含：phase 拆解、依賴關係、stop condition、model 選擇
- Plan 是可執行的 code，不只是文字
- 中途可暫停、可恢復、可審計
- 多 plan 互相對抗驗證到共識

### 1.3 非目標（v2.0 不做）

- 完全自動化的 self-improving planner
- 跨機器 federation plan
- 視覺化 plan editor

---

## 2. 核心概念

### 2.1 Plan = 生成的 Python script

```
用戶 task: "分析 A 公司 2025 表現並跟 B 公司比較 + 寫投資建議"
                ↓
[PlanGenerator] LLM call
                ↓
生成的 script:
  ```python
  from dynamic_harness import UnifiedRouter
  router = UnifiedRouter()
  
  # Phase 1: 並行收集兩家公司基礎資料
  p1a = router.route("A 公司 2025 財報", force_domain="stock")
  p1b = router.route("B 公司 2025 財報", force_domain="stock", parallel=True)
  
  # Phase 2: 計算關鍵比率（依賴 Phase 1）
  p2a = router.route("A 公司 PE ROE 計算", force_domain="stock", depends_on=[p1a])
  p2b = router.route("B 公司 PE ROE 計算", force_domain="stock", depends_on=[p1b])
  
  # Phase 3: 比較分析（依賴 Phase 2）
  p3 = router.route("A vs B 比較 + 投資建議", force_domain="stock", depends_on=[p2a, p2b])
  
  # Phase 4: 寫成報告（依賴 Phase 3）
  p4 = router.route("寫成 Markdown 報告", force_domain="hermes", depends_on=[p3])
  ```
```

### 2.2 差異化 vs 現有

| 維度 | v1.5 multi_route | v2.0 plan-in-code |
|------|------------------|-------------------|
| 拆分策略 | 靜態（regex + 固定 LLM prompt） | **動態**（每個任務生成獨特 script） |
| 依賴關係 | ❌ | ✅ DAG（depends_on） |
| Stop conditions | ❌ | ✅ 每 phase 獨立 |
| 可恢復 | ❌ | ✅ checkpoint + resume |
| Adversarial | ❌ | ✅ plan_v1 + critic → plan_v2 |
| 跨 session | ❌ | ✅ SQLite 持久化 |
| 觀察性 | metrics | metrics + per-plan traces |

---

## 3. 架構圖

```
用戶 task
    ↓
[PlanGenerator] ─── LLM call (M2.7)
    ↓
    Plan object (Python script)
    ↓
[StaticAnalyzer] — 拒絕危險 script（exec/import os/...）
    ↓
[PlanExecutor] ─── 執行 phases
    │   │
    │   ├─ Phase 1: 跑 router.route() → envelope_1
    │   │  ├─ 寫 checkpoint
    │   │  └─ 檢查 stop_condition
    │   │
    │   ├─ Phase 2: depends_on=[1]
    │   │  ...
    │   │
    │   └─ Phase N
    │
[PlanVerifier] — adversarial: 對抗驗證（v2.0 MVP 暫不做）
    │
    ↓
最終 envelope + plan summary
```

---

## 4. 元件設計

### 4.1 PlanGenerator

**職責**：用 LLM 為任務生成 orchestration script

```python
class PlanGenerator:
    def __init__(self, llm_client=None):
        self.llm = llm_client or call_minimax
    
    def generate(self, task_text: str, force_domain: Optional[str] = None) -> Plan:
        """生成 plan
        1. 構造 prompt（含 system instructions + 框架範本 + 任務）
        2. Call LLM
        3. 解析 LLM 回覆（提取 Python code block）
        4. 靜態分析（拒絕危險）
        5. 解析成 Plan 物件
        """
        pass
    
    def _build_prompt(self, task_text: str, force_domain: Optional[str]) -> str:
        """構造 prompt（含 system + 範本 + 規範）"""
        pass
```

**LLM Prompt 設計**（v1 — 簡單版）：
```
你是 plan generator。給定用戶任務，生成 Python orchestration script。

可用工具：
- from dynamic_harness import UnifiedRouter
- router = UnifiedRouter()
- router.route(task_text, force_domain=..., parallel=...)  # 回傳 RouteEnvelope

要求：
1. 拆成 2-5 個 phases（每個 phase 一個獨立的子任務）
2. 用 force_domain 指定 domain
3. 設 depends_on=[phase_id] 表達依賴
4. 寫成可執行的 Python script
5. 包含 if __name__ == "__main__": 入口
6. 最多 100 行

範例：
{template_example}

任務：{task_text}

請生成 Python script（只回傳 code block，不要其他文字）：
```

### 4.2 Plan 資料模型

```python
@dataclass
class Plan:
    id: str                       # UUID
    task_text: str
    force_domain: Optional[str]
    created_at: float
    script_source: str            # 生成的 Python source
    status: str                   # "draft" | "validated" | "running" | "completed" | "failed" | "cancelled"
    result_json: Optional[dict]   # 最終結果
    metadata: dict                # LLM model, prompt tokens, etc.
    phases: List[Phase] = field(default_factory=list)
    
    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, d: dict) -> "Plan": ...


@dataclass
class Phase:
    id: int                       # 1, 2, 3, ...
    name: str                     # "收集 A 公司財報"
    sub_task: str                 # 傳給 router.route() 的 task_text
    force_domain: Optional[str]
    depends_on: List[int]         # [1, 2]
    parallel: bool = False
    timeout_s: int = 60
    stop_condition: Optional[str] = None  # Python expression
    status: str = "pending"       # "pending" | "running" | "completed" | "failed" | "skipped"
    envelope_id: Optional[int] = None      # FK to envelopes table
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None
```

### 4.3 StaticAnalyzer

**職責**：靜態分析生成的 script，拒絕危險操作

```python
class StaticAnalyzer:
    FORBIDDEN_CALLS = {
        "os.system", "subprocess", "shutil.rmtree", "open(", 
        "__import__", "eval", "exec", "compile",
    }
    ALLOWED_IMPORTS = {
        "dynamic_harness", "dynamic_harness.unified_router",
        "dynamic_harness.persistence", "dynamic_harness.metrics",
        # 不允許 os, sys, subprocess, requests
    }
    
    def analyze(self, source: str) -> tuple[bool, str]:
        """回傳 (is_safe, reason)"""
        tree = ast.parse(source)
        for node in ast.walk(tree):
            # 檢查 import
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                ...
            # 檢查危險函式呼叫
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in self.FORBIDDEN_CALLS:
                    return False, f"Forbidden call: {node.func.id}"
        return True, "ok"
```

### 4.4 PlanExecutor

**職責**：執行 plan，依序/並行跑 phases，寫 checkpoint

```python
class PlanExecutor:
    def __init__(self, plan: Plan, db_path: Optional[Path] = None):
        self.plan = plan
        self.router = UnifiedRouter()  # 共用 v1.5 邏輯
        self.db = db_path or DEFAULT_DB_PATH
    
    def execute(self) -> Plan:
        """執行 plan
        1. 依 topological order 跑 phases
        2. 每個 phase 跑完寫 SQLite checkpoint
        3. 失敗時保留狀態，可 resume
        4. 全部完成回傳 Plan（status="completed"）
        """
        # 1. 計算 topological order
        ordered_phases = self._topological_sort(self.plan.phases)
        
        # 2. 依序執行
        for phase in ordered_phases:
            if phase.status == "completed":
                continue  # 跳過已完成的（resume 場景）
            self._execute_phase(phase)
        
        return self.plan
    
    def _execute_phase(self, phase: Phase):
        """執行單個 phase"""
        phase.status = "running"
        self._save_phase(phase)
        
        try:
            # 等所有 depends_on 完成
            for dep_id in phase.depends_on:
                dep = self._get_phase(dep_id)
                if dep.status != "completed":
                    raise RuntimeError(f"Dependency phase {dep_id} not completed")
            
            # 執行
            env = self.router.route(
                phase.sub_task,
                force_domain=phase.force_domain,
                parallel=phase.parallel,
            )
            
            # 寫 envelope 到 envelopes table
            envelope_id = save_envelope(env, db_path=self.db)
            phase.envelope_id = envelope_id
            phase.status = "completed" if not env.error else "failed"
            phase.error = env.error
            phase.completed_at = time.time()
            self._save_phase(phase)
            
        except Exception as e:
            phase.status = "failed"
            phase.error = str(e)
            phase.completed_at = time.time()
            self._save_phase(phase)
    
    def resume(self, plan_id: str) -> Plan:
        """從 checkpoint 恢復"""
        plan = self._load_plan(plan_id)
        return self.execute()  # 會自動跳過已完成的 phases
```

### 4.5 PlanVerifier (v2.0 MVP 暫不做)

**職責**：adversarial 驗證

```
1. PlanGenerator 生成 plan_v1
2. PlanCritic (用 LLM 二次判斷) 批評 plan_v1：
   "此 plan 缺漏：沒考慮 X 風險、phase 5 應在 phase 3 之後"
3. PlanGenerator 根據批評重生成 plan_v2
4. 重複直到 critic 同意或 max_iterations=3
5. 儲存最終 plan
```

**v2.0 MVP 跳過**，因為：
- 雙 LLM call 慢
- 5/6 LLM judge 已夠好
- 留到 v2.1

---

## 5. 資料持久化

### 5.1 Schema（共用 SQLite）

```sql
-- v2.0 新增 plans 表
CREATE TABLE plans (
    id TEXT PRIMARY KEY,           -- UUID
    task_text TEXT NOT NULL,
    force_domain TEXT,
    created_at REAL NOT NULL,
    script_source TEXT NOT NULL,
    status TEXT NOT NULL,           -- "draft" | "validated" | "running" | "completed" | "failed" | "cancelled"
    result_json TEXT,
    metadata_json TEXT
);

-- v2.0 新增 plan_phases 表
CREATE TABLE plan_phases (
    plan_id TEXT NOT NULL,
    phase_id INTEGER NOT NULL,
    name TEXT,
    sub_task TEXT,
    force_domain TEXT,
    depends_on TEXT,                -- JSON: [1, 2]
    parallel INTEGER DEFAULT 0,
    timeout_s INTEGER DEFAULT 60,
    stop_condition TEXT,
    status TEXT DEFAULT 'pending',
    envelope_id INTEGER,            -- FK to envelopes.id
    started_at REAL,
    completed_at REAL,
    error TEXT,
    PRIMARY KEY (plan_id, phase_id),
    FOREIGN KEY (plan_id) REFERENCES plans(id) ON DELETE CASCADE
);

-- v2.0 新增 plan_traces（per-phase log）
CREATE TABLE plan_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT,
    phase_id INTEGER,
    ts REAL,
    event TEXT,                     -- "phase_start" | "phase_complete" | "checkpoint" | "error"
    message TEXT
);
```

### 5.2 檢查點機制

每個 phase 完成 → 寫 `plan_phases` + `plan_traces` + `envelopes` 3 個 row。
Resume 時只需 query 該 plan 的 phases，status=completed 的跳過。

---

## 6. 安全考量

| 風險 | 防護 |
|------|------|
| LLM 生成危險 script | StaticAnalyzer 黑名單 + 靜態 AST 分析 |
| Script 卡死 | 每 phase 設 `timeout_s` |
| 資源耗盡 | 限制 max phases = 10 |
| 刪除檔案 | 禁止 `os.remove`、`shutil.rmtree` |
| 網路外洩 | 只允許 dynamic_harness 模組呼叫 |

---

## 7. CLI 設計

```bash
# 生成 plan
python3 $DH/plan_cli.py generate --task "分析 A 公司 2025 表現並跟 B 公司比較"
# → 印出生成的 plan + phases 摘要，寫入 SQLite

# 執行 plan
python3 $DH/plan_cli.py execute --plan-id <uuid>
# → 跑所有 phases，寫 checkpoint

# Resume 中斷的 plan
python3 $DH/plan_cli.py resume --plan-id <uuid>
# → 從斷點繼續

# 查詢 plan
python3 $DH/plan_cli.py status --plan-id <uuid>
# → 顯示每個 phase 的 status

# 列出所有 plan
python3 $DH/plan_cli.py list
```

---

## 8. 與 v1.5 整合

- Plan 內部呼叫 `router.route()` → 自動享 envelope cache、metrics、cost tracking
- `plan_traces` 寫入後可由 metrics query
- Plan failure 自動記錄到 cost_log（failed_phase_count × estimate）

**單一依賴鏈**：
```
PlanGenerator → LLM call
              ↓
              Plan object → PlanExecutor
                                    ↓
                                    router.route() (v1.5)
                                          ↓
                                          Adapter → envelope → cache/metrics/cost
```

---

## 9. 測試策略

| 測試 | 驗證 |
|------|------|
| test_plan_generation | LLM 真的生成了 valid plan |
| test_static_analyzer | 危險 script 被拒絕 |
| test_plan_execution | 真實任務 end-to-end 跑通 |
| test_plan_resume | 中斷後恢復，phase 跳過已完成的 |
| test_plan_failure | 一個 phase 失敗，整個 plan 標 failed |
| test_topological_order | 依賴關係正確排序 |

---

## 10. 工作量評估

| Sub-phase | 交付物 | 工作量 |
|-----------|--------|--------|
| **P3-1 架構**（本文件） | 設計文檔 + 資料模型 + schema | ✅ 1-2 天 |
| **P3-2 MVP** | PlanGenerator + StaticAnalyzer + PlanExecutor + 1 個真實用例 + CLI | 2-3 天 |
| **P3-3 完整** | PlanVerifier（adversarial）+ 跨 session + Plan UI 摘要 | 5-7 天 |
| **總計** | | **8-12 天** |

---

## 11. 風險與緩解

| 風險 | 機率 | 影響 | 緩解 |
|------|------|------|------|
| LLM 生成的 plan 品質差 | 中 | 高 | 範本 + few-shot + 靜態分析過濾危險 |
| 執行太慢 | 中 | 中 | max phases = 10、每 phase timeout = 60s |
| LLM judge 5/6 正確 | 已存在 | 中 | plan 失敗 fallback 到 v1.5 multi_route |
| Schema migration 衝突 | 低 | 高 | 新表獨立，不影響 v1.5 既有 tables |

---

## 12. 後續可擴充（v2.1+）

- [ ] PlanVerifier（adversarial 驗證）
- [ ] Plan UI（網頁或 terminal 視覺化）
- [ ] Plan template library（常用任務的預製 plan）
- [ ] 跨 session plan sharing
- [ ] Plan diff / merge（類似 git）

---

**P3-1 架構設計完成** — 待用戶確認後進 P3-2 MVP。
