# Plan UI — Terminal 視覺化參考

> Phase 4 實作細節（2026-06-05）。Plan DAG 的 terminal 渲染、watch 模式、CLI 整合的具體做法與踩坑記錄。

---

## 1. 兩種視圖

### 1.1 DAG View（拓樸圖）
- 每行一個 level（L0 = root，遞增）
- 節點格式：`[id] name <icon>`
- 邊：垂直 `│` 從 parent level 指向 child level
- 限制：merge diamond 不做邊交叉最小化（接受「兩條 │ 都從下來」）

範例：
```
  L0: [1] collect A ✓   [2] collect B ✓
          │         │   
  L1: [3] calc A ✓   [4] calc B ◉
          │         │   
  L2: [5] compare ○
          │   
  L3: [6] write report ○
```

### 1.2 Phase Details View（分層詳情）
- 每個 phase 4 行：header / subtask / meta / error
- 連接線 `│` 串接（非最後 phase 都加）
- Meta 欄位順序：`domain | timeout | deps | took/running`

---

## 2. 狀態圖示與顏色

| Status | Icon | ANSI Color | Code |
|--------|------|-----------|------|
| pending | ○ | 灰 | `\033[90m` |
| running | ◉ | 黃 | `\033[33m` |
| completed | ✓ | 綠 | `\033[32m` |
| failed | ✗ | 紅 | `\033[31m` |
| skipped | ⊘ | 灰 | `\033[90m` |

實作位置：`plan_ui.py` 的 `STATUS_ICONS` / `STATUS_COLORS` 常數。

---

## 3. DAG Layout 演算法

### 3.1 Level Assignment
`level(p) = max(level(d) for d in p.depends_on) + 1`，root = 0。

**遞迴寫法**（`plan_ui._assign_levels`）避免重複計算：
```python
def get_level(phase):
    if phase.id in levels: return levels[phase.id]
    if not phase.depends_on:
        levels[phase.id] = 0
        return 0
    parent_levels = [get_level(phase_map[d]) for d in phase.depends_on if d in phase_map]
    levels[phase.id] = max(parent_levels) + 1
    return levels[phase.id]
```

對循環依賴：會陷入無限遞迴。生產環境應先呼叫 `StaticAnalyzer` 確認 plan 通過驗證再 render（plan.py 的 `execute()` 內已有保證）。

### 3.2 Group by Level
簡單分桶：`_group_by_level(phases, levels)` → `[[level0 phases], [level1 phases], ...]`，每個 bucket 內保留原順序。**不做 column assignment 優化**（edge crossing minimization）— 對 5-10 phases 的 plan 已經夠用。

---

## 4. Watch 模式

`_watch_plan(plan_id, use_color, interval)`：
- 用 `\033[2J\033[H` 清屏（ANSI CSI 序列）
- 每 `interval` 秒 re-render 一次
- 終止條件：`plan.status ∈ {completed, failed, cancelled}` 時 exit
- 捕捉 `KeyboardInterrupt` 優雅退出

**整合進 unified_router.py**：`--ui <id> --live [--interval 2.0]`，**必須同時加 `--live` 跟 `--interval` flag 到 unified_router.py**（見 §6）。

---

## 5. CLI Flag 對應表

| plan_ui.py 旗標 | unified_router.py 旗標 | 說明 |
|----------------|----------------------|------|
| `<plan_id>` | `--ui <plan_id>` | 渲染單個 plan |
| `--list` | `--ui-list` | 列出所有 plans |
| `--live` | `--live` | Watch 模式（unified_router 加） |
| `--interval N` | `--interval N` | Watch 間隔（unified_router 加） |
| `--no-color` | `--no-color` | 關 ANSI（unified_router 加） |

---

## 6. Pitfall：CLI Flag 必須在 umbrella 與 sub-module 都加

**症狀**：在 `plan_ui.py` 加了新 flag，但只在 `unified_router.py` 內呼叫 plan_ui 時忘記在 argparse 加 forwarding flag。sub-process 測試報 `unrecognized arguments`。

**P4 session 踩了 2 次**：
1. 加 `--no-color` 到 `plan_ui.main()` → 沒加到 `unified_router` → test 16 炸
2. 加 `--live` / `--interval` 到 `plan_ui.main()` → 沒加到 `unified_router` → test 17 炸

**修復模式**（3 處都要加）：
```python
# 1. plan_ui.py
parser.add_argument("--no-color", action="store_true", ...)
def main():
    args = parser.parse_args()
    use_color = (not args.no_color) and sys.stdout.isatty()
    ...

# 2. unified_router.py
parser.add_argument("--no-color", action="store_true", ...)
if args.ui or args.ui_list:
    from plan_ui import ...
    use_color = (not args.no_color) and sys.stdout.isatty()
    ...

# 3. test 必須 sub-process 跑 unified_router（in-process patch 不會生效）
subprocess.run([..., "--no-color"], env={**os.environ, "PLAN_DB_PATH": str(db)})
```

**預防**：寫新 CLI flag 時，**先在 sub-process 測試內跑一次 unified_router.py**，確認所有 flag 都有 forwarding，再寫 in-process 測試。

---

## 7. sub-process 測試 + Temp DB 配方

```python
import subprocess, os, tempfile
from pathlib import Path

db = Path(tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)

# In-process: patch 3 處 DB path
import plan, plan_ui, persistence
plan.DEFAULT_DB_PATH = db
plan_ui._default_db_path = lambda: str(db)
persistence.DEFAULT_DB_PATH = db

# 觸發 schema init（sub-process 不會跑 in-process patch）
from plan import save_plan
save_plan(build_plan("seed", [{"id": 1, "name": "x", "status": "pending"}]))

# Sub-process 必須傳 PLAN_DB_PATH env var
result = subprocess.run(
    [sys.executable, "unified_router.py", "--ui-list", "--no-color"],
    capture_output=True, text=True, timeout=15,
    env={**os.environ, "PLAN_DB_PATH": str(db)},
)
assert result.returncode == 0

# Cleanup
db.unlink()
```

**為什麼需要 save_plan 觸發 schema**：`persistence._connect()` 內 `conn.executescript(SCHEMA_SQL)` 只在第一次 `INSERT`/`UPDATE` 時跑。`SELECT` 不會跑 schema，會報 `no such table: plans`。

---

## 8. 完整 Render 範例

輸入：6-phase plan（mock，狀態混合）
```
Phase 1: collect A     completed   (2.0s)
Phase 2: collect B     completed   (2.0s)
Phase 3: calc A        completed   (2.0s)  deps: [1]
Phase 4: calc B        running     (3.0s so far)  deps: [2]
Phase 5: compare       pending                 deps: [3, 4]
Phase 6: write report  pending                 deps: [5]
```

輸出（彩色版）：
```
════════════════════════════════════════════════════════════════════════════════════════════
  Plan: test-plan-001
  Task: A 公司 vs B 公司 比較分析
  Domain: stock | Status:   RUNNING   | Progress: 3/6 completed, 1 running, 0 failed
════════════════════════════════════════════════════════════════════════════════════════════

  DAG View:
  L0: [1] collect A ✓   [2] collect B ✓
          │         │   
  L1: [3] calc A ✓   [4] calc B ◉
          │         │   
  L2: [5] compare ○
          │   
  L3: [6] write report ○

  Phase Details:

  ├─ [1] collect A ✓ COMPLETED
  │  subtask: A 公司 2025 財報
  │  domain: stock | timeout: 60s | took: 2.0s
  │
  ├─ [2] collect B ✓ COMPLETED
  ...
```

完整原始碼：`plan_ui.py`（~330 行）。

---

## 9. 擴充指引

### 9.1 加新 status
1. 在 `Phase.status` 字串定義（plan.py 已支援任意 string）
2. `plan_ui.STATUS_ICONS` 加圖示
3. `plan_ui.STATUS_COLORS` 加 ANSI color
4. 若要 legend 列出，在 `render_legend()` 已自動 iterate STATUS_ICONS

### 9.2 加新 meta 欄位
編輯 `PlanUI._render_phase_detail()` 的 `meta_parts` list。

### 9.3 改 DAG layout
- 想要 edge crossing 優化：實作 Sugiyama barycenter heuristic（plan 不大可能 > 20 phases，不建議先做）
- 想要水平 vs 垂直：目前是垂直（每行一層 level），改水平要重寫 `render_dag()`

### 9.4 Watch 模式加 hook
`_watch_plan` 在每次 re-render 之前/之後可加：
- 寫 log 到檔案
- 觸發 webhook（plan completed 時）
- 跨 session 廣播（plan-in-code v2.1 規劃）
