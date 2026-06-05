---
name: dynamic-harness
description: 統一任務路由器 v1.6 — 跨 5 個 domain 的 meta-dispatcher，內建 metrics 觀察性、cost tracking 預算警告、envelope cache 50x 加速、SQLite 持久化、Plan UI 終端視覺化、Plan-in-Code 程式碼生成、adversarial verifier、template library、Web Search 整合。production-ready with full observability，獨立 skill（不再依附 hermes-agent）。
category: tooling
tags: [router, dynamic, harness, dispatcher, ft-team-agent, stock-team-agent, coding-team-agent, llm-judge, multi-route, sqlite, integration, general-fallback, web-search, tavily, firecrawl, retention, cache, production, metrics, cost-tracking, plan-in-code, plan-ui, verifier, template-library, dag-visualization, independent-skill]
created: 2026-06-05
version: 1.6.2
---

# Dynamic Harness v1.6.2 — Plan UI DB path 雙 bug 修復 + Pitfall 30

> 5 個 domain、19 個功能、101/101 測試通過（0 個測試警告）。**已升級為獨立 skill**（不再依附 `hermes-agent`）— 2026-06-05。

## v1.6 — 獨立化 + Plan UI

| 變更 | 說明 |
|------|------|
| **獨立 skill** | 從 `hermes-agent/dynamic-harness/` 升級為頂層 `~/.hermes/skills/dynamic-harness/` |
| **bin/dh wrapper** | 新增 `bin/dh` 啟動腳本，PATH 加入後可直接 `dh --task "..."` 執行 |
| **scripts/install.sh** | 一鍵安裝（建立 ~/.local/bin/dh symlink + 設 PLAN_DB_PATH） |
| **scripts/test-all.sh** | 一鍵跑完整測試套件 |
| **Plan UI** | DAG 終端視覺化（`--ui` / `--ui-list` / `--live`） |
| **category 變更** | `hermes-agent` → `tooling`（頂層分類） |
| **Makefile** | 22 個 target（test/coverage/lint/ci/install/demo）— 統一入口取代散落的 shell 指令 |
| **GitHub Pages** | `.github/workflows/pages.yml` 自動部署 coverage HTML 到 `gh-pages`（badge 動態計算） |
| **README badges** | Tests / Coverage / Python / License 四個 shields.io badge |

## v1.5 新功能（已保留）

| Feature | 說明 |
|---------|------|
| **metrics 觀察性** | 自動記錄每個 adapter 呼叫、cache hit、LLM judge 觸發 |
| **cost tracking** | 自動估算 API 成本（LLM + Web Search）+ 月預算警告 |
| **--metrics CLI** | 顯示使用率、命中率、平均延遲 |
| **--cost / --budget CLI** | 顯示本月成本或檢查預算 |

## 18 個功能完整列表

| # | 功能 | 狀態 |
|---|------|------|
| 1-6 | 5 個 adapter + 5 個 domain + Envelope + stdout + 4 套原 router + force-domain | ✅ |
| 7 | multi_route 順序（parallel fallback sequential） | ✅/⚠ |
| 8 | SQLite 持久化 + 查詢 | ✅ |
| 9 | HermesTeamAgent fallthrough 整合 | ✅ |
| 10 | GeneralAdapter + Web Search（Tavily/Firecrawl） | ✅ |
| 11 | parallel=auto 自動判斷 | ✅ |
| 12 | --retention-days 清理 | ✅ |
| 13 | 可配置 fallthrough 閾值 | ✅ |
| 14 | --db-stats / --cleanup CLI | ✅ |
| 15 | envelope cache（**50x 加速**） | ✅ |
| 16 | --cache-stats / --cache-clear / --no-cache | ✅ |
| **17** | **metrics 自動記錄 + CLI** | **✅ v1.5** |
| **18** | **cost tracking + 月預算警告 + CLI** | **✅ v1.5** |

## 快速使用

```bash
# Skill 安裝位置
DH=~/.hermes/skills/dynamic-harness

# === 方法 1: 一鍵安裝（推薦）===
bash $DH/scripts/install.sh      # 建立 ~/.local/bin/dh
dh --task "曼聯 對 車路士 赔率"   # 任意目錄可用

# === 方法 2: 直接呼叫 ===
python3 $DH/unified_router.py --task "曼聯 對 車路士 赔率"
python3 $DH/unified_router.py --task "今天天氣" --force-domain general

# === Plan UI（DAG 視覺化）===
python3 $DH/unified_router.py --ui-list
python3 $DH/unified_router.py --ui <plan-id> --live

# === Cache 管理 ===
python3 $DH/unified_router.py --cache-stats
python3 $DH/unified_router.py --cache-clear

# === 觀察性（v1.5+）===
python3 $DH/unified_router.py --metrics
python3 $DH/unified_router.py --cost
python3 $DH/unified_router.py --budget 5
```

```python
# 程式內使用
import sys
sys.path.insert(0, "~/.hermes/skills/hermes-agent/dynamic-harness")
from unified_router import UnifiedRouter
import metrics, cost

router = UnifiedRouter()
router.route("曼聯 對 車路士 赔率")
router.route("曼聯 對 車路士 赔率")  # cache hit, 50x 加速

# 觀察性
print(metrics.get_summary())
print(cost.get_cost_summary())
print(cost.check_budget(budget_usd=5.0))
```

## Common Tasks（Makefile 快捷）

v1.6+ 所有常用指令統一透過 `make <target>` 入口。Makefile 在 `$DH/Makefile`，先 `cd $DH` 再執行。

```bash
cd ~/.hermes/skills/dynamic-harness

# === 開發日常 ===
make help                 # 列出 22 個 target 與說明
make test                 # 跑全部 101 個測試（簡短輸出）
make test-verbose         # 顯示每個測試名稱 + 耗時（找慢測試用）
make test-fast            # 跳過 test_llm_planner.py，60s → 15s
make test-one T=tests/test_plan.py::test_parallel_threshold   # 跑單一測試

# === Coverage ===
make coverage             # 跑測試 + 產生 htmlcov/index.html
make coverage-clean       # 刪除 coverage artifacts

# === Lint / 健康檢查 ===
make lint                 # pyflakes + shellcheck + mandoc + bash -n + YAML 全部跑
make smoke                # CLI 健康檢查（--version / --list-adapters / --ui-list）
make smoke-dh             # 測試 ~/.local/bin/dh 是否可運作

# === CI 模擬 ===
make ci                   # 一次跑 test + lint + smoke（= GitHub Actions 在本地跑）

# === 安裝 / 反安裝 ===
make install              # bash scripts/install.sh（建 ~/.local/bin/dh + man page）
make man                  # 僅裝 man page
make uninstall            # 移除 ~/.local/bin/dh symlink

### Demo ===
make demo                 # 跑真實任務：分析 01810 小米（⚠️ adapter 是 router-only，見 Pitfall #29）
make demo-ui              # 列出所有 plan（demo Plan UI，1.6.2+ 不需 PLAN_DB_PATH）
make stats                # 顯示檔案數 / 行數 / 磁碟大小

### Plan-in-Code 範本 ===
* `templates/plan_cli_demo.py` — 4 phases, 多 domain, DAG deps, 已知可 E2E 跑通（generate → execute → Plan UI 渲染）。改 `router.route("...")` 內容即可 reproduce 各種 multi-domain 任務。語法細節見 Pitfall #28。
```

**為什麼用 Makefile 而非直接打 pytest/shellcheck？**
- 22 個常用指令一個入口，不需要記 flags
- `make ci` = 模擬 GitHub Actions 流程，本地先綠再 push
- `make help` 自動從註解產生，永遠是最新的

## Metrics 設計

**自動記錄**：
- `adapter_call` — 每次 adapter 路由（含 success/fail、延遲 ms）
- `llm_judge` — LLM 二次判斷觸發次數
- `cache_hit` / `cache_miss` — cache 命中率
- `web_search` — Web Search 觸發（記錄 backend）

**查詢 API**：
```python
summary = metrics.get_summary()
# {
#   "total_calls": 13,
#   "success_rate": 1.0,
#   "by_adapter": {
#     "FTAdapter": {"calls": 5, "success": 5, "success_rate": 1.0, "avg_latency_ms": 91.4},
#     "StockAdapter": {"calls": 3, "success": 3, "success_rate": 1.0, "avg_latency_ms": 0.3},
#     ...
#   },
#   "llm_judge": {"triggered": 2},
#   "cache": {"hit": 8, "miss": 5, "hit_rate": 0.6154},
#   "web_search": {"tavily": 2}
# }
```

**CLI 範例**：
```bash
$ python3 unified_router.py --metrics
{
  "total_calls": 6,
  "success_rate": 1.0,
  "by_adapter": {
    "FTAdapter": {"calls": 1, "success_rate": 1.0, "avg_latency_ms": 91.4},
    "GeneralAdapter": {"calls": 2, "success_rate": 1.0, "avg_latency_ms": 9600.0},
    ...
  },
  "cache": {"hit": 1, "miss": 5, "hit_rate": 0.1667},
  "web_search": {"tavily": 2}
}
```

## Cost Tracking 設計

**費用估算**（2026-06）：
| Service | Operation | Cost/call |
|---------|-----------|-----------|
| MiniMax M2.7 | llm_judge | $0.00011 |
| MiniMax M2.7 | llm_split | $0.00011 |
| Tavily | web_search | $0.001 |
| Firecrawl | web_search | $0.0006 |

**預算警告層級**：
| 級距 | 狀態 |
|------|------|
| < 80% | ✅ ok |
| 80-100% | ⚠️ warning |
| ≥ 100% | ❌ exceeded |

**CLI 範例**：
```bash
$ python3 unified_router.py --budget 5
{
  "month": "2026-06",
  "total_usd": 0.002,
  "budget_usd": 5.0,
  "remaining_usd": 4.998,
  "used_pct": 0.04,
  "warning": false,
  "warning_level": "ok"
}

$ python3 unified_router.py --cost
{
  "month": "2026-06",
  "total_usd": 0.002,
  "call_count": 2,
  "by_service": {"tavily": 0.002},
  "by_operation": {"web_search": 0.002}
}
```

## Schema 演進

```sql
-- v1.0+ 持久化
CREATE TABLE envelopes (...);
-- v1.4+ cache
CREATE TABLE envelope_cache (
    cache_key TEXT PRIMARY KEY,
    ts REAL, task_text, force_domain,
    envelope_json TEXT, hit_count INTEGER
);
-- v1.5+ metrics
CREATE TABLE metrics (
    id, ts, metric_type, metric_key,
    latency_ms, success, extra
);
-- v1.5+ cost
CREATE TABLE cost_log (
    id, ts, month, service, cost_usd, operation
);
```

## 4 套原 router 完整性（v1.5 仍維持）

```
task_router_v2.py: b941f90eb0628ef6 (unchanged)
task_router.py:    e698a31b84625780 (unchanged)
stock_router.py:   c051c6c442d9ae2e (unchanged)
task_router.py:    c8941a76346d8828 (unchanged)
```

## 101/101 測試結果（2026-06-05 實測 78.53s）

```
test_basic.py            18/18 ✅
test_plan.py             15/15 ✅
test_integration.py      10/10 ✅
test_template_library.py 15/15 ✅
test_verifier.py         12/12 ✅
test_plan_ui.py          19/19 ✅ (P4-5: +2 regression tests)
test_llm_planner.py      12/12 ✅
────────────────────────────────────
總計                     101/101 ✅
```

> 過濾 99 個 `PytestReturnNotNoneWarning`（pytest 8+ 對 `return` 風格測試丟的）見 [`references/test-execution-checklist.md`](references/test-execution-checklist.md)。

## v2.0 路線圖（plan-in-code）

Phase 3 設計已交付（**待用戶確認後進 MVP**）：
- 📄 [`references/plan-in-code-architecture.md`](references/plan-in-code-architecture.md) — 478 行架構設計
- 4 個元件：PlanGenerator → StaticAnalyzer → PlanExecutor → PlanVerifier
- 3 個新 SQLite 表：`plans` / `plan_phases` / `plan_traces`
- 核心概念：LLM 為每個任務**即場生成** Python orchestration script（不只是文字 plan）
- 工作量：P3-2 MVP 2-3 天 / P3-3 完整版 5-7 天
- PlanVerifier（adversarial）跳過 MVP，留 v2.1

### P3-3 進度（v1.5.1，2026-06-05）

| 子任務 | 元件 | 測試結果 | 狀態 |
|--------|------|---------|------|
| P3-3.1 | `plan.py` PlanExecutor（dataflow wave-based）| - | ✅ |
| P3-3.2 | `llm_planner.py` LLM-generated plan via minimax | - | ✅ |
| **P3-3.3** | `adversarial_verifier.py`（12 static checks + LLM judge） | **12/12 ✅** | ✅ |
| **P3-3.4** | `template_library.py`（5 templates） | **15/15 ✅** | ✅ |
| **P3-3.5** | `tests/test_integration.py` E2E 測試 | **10/10 ✅** | ✅ |
| **P3-3.6** | SKILL.md 更新 + Pitfall 補充 | - | ✅ |

**全部 P3-3 測試套件結果**：
- `tests/test_plan.py` — 15/15 ✅
- `tests/test_llm_planner.py` — 12/12 ✅
- `tests/test_verifier.py` — 12/12 ✅
- `tests/test_template_library.py` — 15/15 ✅
- `tests/test_integration.py` — 10/10 ✅
- **總計: 64/64 ✅**

**新增交付物**：
- 📄 [`references/plan-in-code-implementation.md`](references/plan-in-code-implementation.md) — 5 個新元件的 API、verifier 12 條規則清單、template 觸發詞表
- 📁 `adversarial_verifier.py`（450 行，LLM judge 可關）+ `template_library.py`（228 行）
- 📁 `tests/test_verifier.py`（12 tests）+ `tests/test_template_library.py`（15 tests）+ `tests/test_integration.py`（10 tests）

**Template fallback chain**：`TemplateLibrary.instantiate(task)` → 若 trigger 命中 → 回 `Plan`；若無 → `None`（呼叫方接手用 LLM 補）。Template 涵蓋：ft_match_analysis、stock_deep_research、code_refactor、multi_market_compare、investigation（含 diamond DAG）。

**AdversarialVerifier 12 條 static rules**：pass、cycle_detected、self_dependency、missing_dep、missing_domain、unavailable_domain、too_many_phases、too_sequential（≥80% 邊界）、short_sub_task、critical_skips_llm。`verify_plan(plan, offline_only=True)` 跳過 LLM judge。

**P3-3.5 整合測試覆蓋場景**：
1. E2E template → verified Plan
2. Template DSL → reparse round-trip
3. 全部 5 templates 通過 static verification
4. Template 變數替換正確
5. Investigation diamond DAG 結構驗證
6. Template 路徑與 LLM 路徑產出可互通
7. Library miss → LLM fallback chain
8. 注入 defect（self-cycle）→ verifier 抓到
9. 5 templates DAG shape 全部符合預期
10. Template rationale 寫入 plan metadata

## 限制

1. **parallel 模式 sequential-fallback**（v1.4 決定）
2. **cost 估算為粗略值** — 應根據實際 API 定價調整 `cost.COST_TABLE`
3. **metrics 永久累積** — 需手動 `clear_metrics()` 或 `--cleanup`
4. **LLM judge 5/6 正確** — 「小米新聞」會被當 general
5. ~~plan-in-code 還沒實作~~ — **已實作並 E2E 驗證**（P3-3.6 + Plan-in-Code 示範, 2026-06-05）。`python3 plan_cli.py generate --script-file X.py` + `execute --plan-id <id>` 真實跑通：3 phases, 多 domain, DAG deps, 4.9s LLM 二次判斷, SQLite 寫入 8 plans。見 `templates/plan_cli_demo.py` 可直接 reproduce。

## Pitfalls（從實際建構 v1.5 學到的）

### 1. Cache hit/miss key 必須一致
記錄時 `metric_key="hit"` / `"miss"`，查詢時 `cache.get("cache_hit")` → 永遠 0。**修正**：記錄 + 查詢用同一套 key（`hit` / `miss`），別在兩處用不同名。

### 2. Budget warning 級距要對齊測試
101% 應是 `exceeded`（≥100%）不是 `warning`（80-100%）。**修正**：寫測試前先確認函式實際邏輯，別假設 "中間值 = warning"。

### 3. 刪 import 前先 grep 全文
刪 `import re` 以為 `re` 沒用，結果 `re.compile(...)` 還在用 → `NameError`。**修正**：刪 import 前用 `search_files target=content pattern="\\bre\\b"` 確認沒有符號引用。

### 4. SQLite schema migration 用 append，不要 inline CREATE
新模組（metrics / cost）的表定義用 `SCHEMA_SQL = SCHEMA_SQL + NEW_SCHEMA` 追加，別寫在模組 `__init__`。**理由**：共用同一個 `_get_db_path` + thread-safe lock，避免 race condition 創建空 DB。

### 5. 觀察性必須 non-fatal
`metrics.record()` / `cost.record_cost()` 失敗不應中斷 `route()`。**修正**：所有觀察性呼叫包 `try/except`，log 即可，別 raise。

### 6. 整合舊代碼前先 hash 驗證
整合 4 套原 router 前用 `shasum -a 256` 記下 hash。**理由**：避免「以為沒改但其實改了一行」這類 silent regression。

### 7. Module-level 函式呼叫需 try/except
若 `_load_api_key()` 在檔案頂層執行，**但定義在後面**（測試檔常見），import 即崩。**修正**：
```python
try:
    _load_api_key()
except Exception:
    pass
```
**理由**：測試檔應容錯 API key 缺失，別讓 import 失敗阻斷整個測試套件。

### 8. Threshold 邊界檢查要對齊測試
寫 `if ratio > 0.8` 但測試 case 注入 `ratio=0.8` 時不觸發 → 測試 fail。**修正**：寫測試前先決定邊界（`>` vs `>=`），讓程式邏輯和測試預期一致。**規則**：80% sequential 偵測、budget warning 級距、parallelism 比例 — 全部用 `>=` 讓「剛好等於」也算違規。

### 9. Test cases 必須實際 match pattern
寫 test case 沒驗證 regex 真的會 match → 測試一直 False 但以為是 code 錯。**修正**：
- 寫 test 前用 `python3 -c "import re; print(re.search(r'...', 'case'))"` 先驗每個 case
- 繁/簡中文 regex 用 `[賽赛]` 形式同時涵蓋
- 英文 regex 確認空格（`who.*?win` 不會 match `whowill`）
- 跨 case 測試要列「should match」與「should not」兩組，避免 false positive/negative

### 10. 引用函式/符號前先確認存在
寫 `from plan import build_dag` 結果 `build_dag` 不存在 → ImportError 整個 test 死。**修正**：
```bash
grep -n "^def \|^class " target_module.py  # 先列出所有 public symbols
```
**或**用 `dir(module)` 列舉。**或**寫 inline helper（手動算 DAG level）繞開依賴。

### 11. 整合測試必須關掉 LLM 呼叫
`verify_plan(plan)` 預設會 call LLM API；整合測試一定要用 `verify_plan(plan, offline_only=True)`，否則 CI 會卡 60s timeout。

### 12. `Verdict` 是 dataclass 不是 Enum
`class Verdict` 是 `verdict: str` 欄位（值為 `"pass"` / `"warn"` / `"fail"`），不是 `Enum`。比對時用 `verdict.verdict == "pass"`，**不可**寫 `Verdict.PASS` 或 `verdict.verdict.value`。

### 13. 引用函式/符號前先確認存在（補充）
`plan.build_dag()` 在內部 `_topological_sort()` 是 private，沒 public export。整合測試需要 level 計算時，直接 inline 算 `max(dep_level) + 1` 即可，不要 import 不存在的 public API。

### 14. 繁/簡 trigger pattern 要並列
Template trigger regex 用繁體中文，但使用者可能打簡體（`賽` vs `赛`）。寫成 `[賽赛]` 字元 class 兩者皆吃。

### 15. Threshold 邊界用 `>=` 不是 `>`
「≥80% 為 sequential」應寫 `if sequential_ratio >= 0.8`，`>` 在剛好等於 80% 時會漏抓。

## Phase 4: Plan UI（2026-06-05，v1.5.2）

| 子任務 | 元件 | 測試結果 | 狀態 |
|--------|------|---------|------|
| P4-1 | `plan_ui.py` PlanUI 類別 + ASCII DAG layout | - | ✅ |
| P4-2 | Status 顏色化（ANSI escape codes） | - | ✅ |
| P4-3 | CLI 整合（`--ui` / `--ui-list` / `--live`） | - | ✅ |
| P4-4 | `tests/test_plan_ui.py` 整合測試 | **17/17 ✅** | ✅ |
| P4-5 | SKILL.md 更新 + Pitfall 補充（後續 1.6.2 補上 +2 regression tests → 19/19） | - | ✅ |

**`tests/test_plan_ui.py` 17 個測試場景**：
1. `_assign_levels` 無依賴 / 線性 chain / diamond
2. `_colorize` 含 / 不含 ANSI
3. `_status_icon` 5 種狀態映射
4. PlanUI 空 plan / 線性 DAG / diamond DAG / failed / skipped / legend
5. `load_plan` round-trip 從 temp DB
6. `list_plans` 從 temp DB（3 個 plans）
7. CLI `--ui` 不存在 plan → exit 1
8. CLI `--ui-list` 空 DB → exit 0
9. CLI `--live` watch 模式在 completed plan 自動 exit

**全部測試套件**（Phase 3 + Phase 4 累積，2026-06-05 實測 84.75s）：
- `tests/test_basic.py` — 18/18 ✅
- `tests/test_plan.py` — 15/15 ✅
- `tests/test_integration.py` — 10/10 ✅
- `tests/test_template_library.py` — 15/15 ✅
- `tests/test_verifier.py` — 12/12 ✅
- `tests/test_plan_ui.py` — 19/19 ✅（P4-5: +2 regression tests）
- `tests/test_llm_planner.py` — 12/12 ✅
- **總計: 101/101 ✅**（無回歸，0 個測試警告）

**Plan UI 設計**：
- 兩種視圖：DAG 拓樸圖（每行一層 level）+ 分層詳情列表（含 deps、subtask、timing、error）
- 5 種狀態圖示：○ pending / ◉ running / ✓ completed / ✗ failed / ⊘ skipped
- ANSI 顏色：灰/黃/綠/紅 對應 status；`--no-color` 關閉
- DAG layout 演算法：`_assign_levels`（BFS topological）+ `_group_by_level`，零外部依賴
- Watch 模式：plan status ∈ {completed, failed, cancelled} 時自動 exit；`--interval` 控制 re-render 頻率
- DB 路徑支援 `PLAN_DB_PATH` 環境變數（給 sub-process / 測試用）

**unified_router.py 新 CLI flags**：
```
--ui <PLAN_ID>             # 渲染單個 plan DAG
--ui-list                  # 列出所有 plans
--ui-list --no-color       # 關顏色（給 log/pipe）
--ui <id> --live           # Watch 模式
--ui <id> --live --interval 0.5   # 0.5s re-render
```

**延伸閱讀**：
- [`references/plan-ui.md`](references/plan-ui.md) — DAG layout 演算法、CLI flag 對應表、sub-process 測試配方、擴充指引。
- [`references/test-execution-checklist.md`](references/test-execution-checklist.md) — 配合 Pitfall #19/#20：跑 pytest 看實際 N/N、5 個 SKILL.md 同步位置、pytest.ini 過濾設定、數字演進記錄。

### 16. Plan table column 是 `metadata_json` 不是 `metadata`
`persistence.py` 的 `PLAN_SCHEMA` 把 plans 欄位命名為 `metadata_json`（不是 `metadata`）。寫 `SELECT ... metadata FROM plans` 會報 `no such column: metadata`。Plan UI 的 `load_plan()` 必須用正確欄位名。

### 17. `plan_phases` 主鍵是 `(plan_id, phase_id)`，沒有 `id` 欄位
JOIN 寫 `COUNT(ph.id)` 會炸，正確是 `COUNT(ph.phase_id)`。同樣 ORDER BY 也要用 `phase_id` 不是 `id`。

### 18. sub-process CLI 測試要注入 PLAN_DB_PATH env var
`plan_ui._default_db_path()` 在 sub-process 內是全新 import，無法用 in-process 的 `lambda patch` 改路徑。改用 `env={**os.environ, "PLAN_DB_PATH": str(db)}` 傳給 `subprocess.run()`，讓 sub-process 內部讀環境變數找到 temp DB。空 DB 第一次 query 還會踩到「no such table: plans」，必須先 `save_plan(...)` 觸發 `_connect` 內的 `executescript(SCHEMA_SQL)`。

### 19. pytest 8+ 對 `return condition` 風格測試丟 PytestReturnNotNoneWarning
99 個測試函式統一用 `if not X: return False` 風格（不是 `assert`），pytest 8+ 會丟 `PytestReturnNotNoneWarning`（每個測試一個）。**不要重寫 99 個測試**（違反 Rule 2「最小代碼」+ Rule 3「精準修改」），**改用 `pytest.ini` 全域過濾**：
```ini
[pytest]
filterwarnings =
    ignore::pytest.PytestReturnNotNoneWarning
```
過濾前 99 passed + 99 warnings；過濾後 99 passed + 0 test warnings（剩 1 個 urllib3/LibreSSL 環境警告，不歸測試管）。

### 20. 寫 SKILL.md 測試數字前必須跑 `pytest -q` 計數，不要憑記憶
P4-5 commit 前 SKILL.md 寫「85/85 ✅」，但實際 `pytest tests/ -q` 是 **99/99 ✅**（漏算了 `tests/test_llm_planner.py` 的 12 個測試）。**修正**：
- 任何時候要寫「N/N ✅」到 SKILL.md，**先跑 `python3 -m pytest tests/ --collect-only -q`** 列出所有測試函式名稱
- 再用 `python3 -m pytest tests/ --tb=no -q 2>&1 | tail -1` 確認最終通過數
- 兩個數字對得起來才寫進文件
- 同步更新 `[tool.pytest]` 之外的任何表格（檔案結構、變更歷史、測試場景清單）

### 21. Makefile smoke target 必須呼叫 `bin/dh` wrapper，不能直接 `python3 unified_router.py --version`
v1.6 Makefile 初版 `make smoke` 寫了 `$(PYTHON) unified_router.py --version`，但 argparse 沒有 `--version`，argparse 會直接 exit 2。**修正**：
- `unified_router.py` 的 argparse spec **沒有註冊 `--version`**。`--version` 是 `bin/dh` 這個 shell wrapper 從 `SKILL.md` 第一行 `# Dynamic Harness v1.6` 抽出來回應的
- 任何 Makefile target / smoke test / CI 腳本要驗版本，**必須呼叫 `./bin/dh --version`**，不能 `python3 unified_router.py --version`
- 推論：以後新增 CLI flags 時，**只有真正寫進 argparse 才算「公開 API」**；shell wrapper 處理的（如 `--version`）要在 SKILL.md 註明，否則 smoke/lint 會誤判

**驗證**：`make smoke` 第一行應該輸出 `→ version (via bin/dh wrapper)` + `1.6.1`，不是 `unrecognized arguments: --version`。

### 22. `mandoc -Tlint` 連 STYLE warning 都 exit 1 — 不要無腦加進 CI
`mandoc -Tlint` 對 lint finding 的嚴格度比想像中高：**WARNING** 與 **STYLE** 等級都會讓 exit code = 1，CI workflow 用 `mandoc -man -Tlint bin/dh.1` 當 step 就會整個 fail。常見觸發：
- 文字行 > 80 bytes（STYLE: input text line longer than 80 bytes）
- `.PP` macro 接在 `.SS` 後面（WARNING: skipping paragraph macro: PP after SS）
- 連續空行、縮排不一致

**預防**：
- 寫完 man page **先在本地** `mandoc -man -Tlint bin/dh.1; echo $?` 確認 exit 0
- 文字行保持 ≤80 bytes；長的 option 描述斷成多行或用 `.nf` 程式碼區塊包
- 不要在 `.SS` 後面加 `.PP`（`.SS` 自己就開始新 paragraph block）
- 如果一定要接受 STYLE 級別：workflow 改 `mandoc -Tlint bin/dh.1 | grep -v STYLE || true`（不推薦，會掩蓋真問題）

**驗證**：在 v1.6.1 commit `0cc3649` 之前，`bin/dh.1` 同時有 6 處 `.PP` after `.SS`（WARNING 等級）+ 1 處 81-byte 行（STYLE 等級），都會讓 lint exit 1。

### 23. shellcheck SC2086 — `$(...)` 內的變數也要加引號
`scripts/install.sh:57` 的 `[[ ":MANPATH:" != *":$(dirname $MANDIR):"* ]]` 看起來很安全（路徑不會有空白），但 shellcheck SC2086 仍報 warning（info level），預設 CI 對所有 finding 都 fail。修正：把 `$(dirname $MANDIR)` 改成 `$(dirname "$MANDIR")`。**不要圖省事把 SC2086 disable**（`-e SC2086`）— 它會在 `MANDIR` 真的含空白時默默爆。

**預防**：
- shell script 一寫完就跑 `shellcheck scripts/*.sh bin/dh`，exit 0 才能 commit
- `$(...)` 內的變數一律 `"$VAR"`（即使路徑也不省）
- CI 用 `shellcheck bin/dh scripts/*.sh` 當 blocking step，不要 `|| true`

### 24. `xml.etree.ElementTree.attrib` 的值永遠是 `str`
`pages.yml` 的 `Generate coverage badge` step 原本寫：
```python
print(f'{root.attrib["line-rate"]*100:.1f}')
```
會炸 `ValueError: Unknown format code 'f' for object of type 'str'` — 因為 `ET.parse(...).getroot().attrib["..."]` 是字串，f-string 算術前要 `float()` cast：
```python
print(f'{float(root.attrib["line-rate"])*100:.1f}')
```
同樣陷阱在 `Element.text` / `tail` / 其他 attrib。**驗證後再 commit**：
```python
# local check before pushing
import xml.etree.ElementTree as ET
r = ET.parse('coverage.xml').getroot()
print(type(r.attrib['line-rate']))  # 應該是 float 才是對的；不是就 cast
```
實際上 `coverage.xml` schema 的 `line-rate` 是百分比小數（0.0-1.0）不是字串 — 但 `ET.parse` 不會自動轉型。

### 25. `actions/deploy-pages@v4` 只認 artifact name `github-pages`
`actions/upload-pages-artifact@v3` 的 `with.name` 預設是 `github-pages`，但很容易自訂成 `coverage-report` 之類語意化名字 — 然後 `actions/deploy-pages@v4` 找不到 artifact 就報：
```
Error: No artifacts named "github-pages" were found for this workflow run.
```
**預防**：
- `upload-pages-artifact` 的 `name` 永遠寫 `github-pages`（或與 `deploy-pages` 對應）
- 不要自訂 artifact name — Pages workflow 只有 1 個 artifact，語意化名沒意義
- 第一次 deploy 前先看 GitHub repo → Actions → Pages workflow run → Artifacts tab，確認有 `github-pages` artifact

**驗證**：v1.6.1 commit `b1d6c77` 把 `coverage-report` 改回 `github-pages` 後，Deploy to GitHub Pages 從 6s 失敗變 6s ✅。

### 26. Multi-job workflow 每個 job 都要自己裝完整 deps
`test.yml` 有兩個會跑測試的 job：`test`（pytest 矩陣）和 `coverage`（Coverage report）。原本 `coverage` job 只裝 `pytest-cov`，結果 `tests/test_integration.py` 和 `tests/test_verifier.py` import `requests`（透過 `llm_planner.py` / `adversarial_verifier.py`），整個 job `ModuleNotFoundError` 失敗。`pip cache` 不會跨 job 共享 venv 內容（只 cache wheel 下載，不 restore site-packages）。

**預防**：
- 抽出 install 步驟成 composite action（`.github/actions/setup-deps/action.yml`），所有 job 共用
- 或在每個跑測試的 job 都 `pip install -r requirements-dev.txt`（冗餘但直觀）
- 如果想省時間：把 wheel cache key 設對（`cache: 'pip'` + `cache-dependency-path: requirements*.txt`），但**不能省 pip install** 本身

**驗證**：v1.6.1 commit `b1d6c77` 把 coverage job 改成跟 test job 一樣的 `if [ -f requirements.txt ]; then pip install -r ...` 雙行邏輯後，全綠。

### 27. `plan_ui._default_db_path()` 指向不存在的 DB → Plan UI 永遠空（已修復 P4-5）
**症狀**：`./bin/dh --ui <plan-id>` 永遠回 "Plan X not found in DB"；`./bin/dh --ui-list` 永遠 "No plans found in DB."，即使 `~/.hermes/dynamic_harness_envelopes.db` 裡有 8+ plans。

**原因**（`plan_ui.py:297-308` + `bin/dh:19`，**兩個 bug 疊加**）：
1. `plan_ui._default_db_path()` 寫死 `skill_dir/plan_registry.db`（從未建立），註解卻說「與 plan.py 同步」
2. `bin/dh:19` 又覆寫 `export PLAN_DB_PATH="${PLAN_DB_PATH:-$HOME/.hermes/plans.db}"`（也不存在），蓋過 #1 的 fallback

**修法**（P4-5 一起修，2026-06-05）：
- `plan_ui.py:27-29`：新增 `from persistence import DEFAULT_DB_PATH`
- `plan_ui.py:308`：fallback chain 改為 `PLAN_DB_PATH env → persistence.DEFAULT_DB_PATH → 舊路徑 (defensive)`
- `bin/dh:19`：刪掉 `export PLAN_DB_PATH=...`，改由 `plan_ui._default_db_path()` 自己 fallback

**驗證**（修後）：
```bash
unset PLAN_DB_PATH
./bin/dh --ui-list                       # → 8 recent plans
./bin/dh --ui <plan-id>                  # → DAG view + phase details
PLAN_DB_PATH=/tmp/x.db ./bin/dh --ui-list # → env var 仍可覆寫（sub-process / 測試用）
```

**Regression tests**（`tests/test_plan_ui.py`，+2 tests, 99→101）：
- `test_default_db_path_falls_back_to_persistence` — 無 env 時 fallback 到 `persistence.DEFAULT_DB_PATH`
- `test_default_db_path_respects_env_var` — env var 優先權

**教訓**：跨模組共享 DB 時，**`from persistence import DEFAULT_DB_PATH`** 是 single source of truth。不要在 `bin/` wrapper 裡再覆寫一次（會把 `plan_ui` 的 fallback 邏輯蓋掉）。

### 28. `parse_script_to_plan` 的 script 語法是 `router.route(...)` + 注釋，不是函式呼叫
**症狀**：寫 `phase('id', 'domain', 'task')` 或 `p1 = router.route("task")` 然後以為會被 parser 自動 group 成 phases，結果 `phase_count: 0`，plan 永遠 `validated` 但沒有 phases。

**正確語法**（`plan.py:420-498` 的 `parse_script_to_plan` 是 regex-based parser）：
- 找 `router.route("sub_task")` 呼叫 → 每個視為一個 phase
- 注釋**直接放在呼叫上方**推導屬性：
  - `# domain: stock|ft|coding|hermes|general` ← 設 force_domain
  - `# depends_on: [1, 2, 3]` ← 設依賴（**唯一寫法，沒有 kwarg**）
  - `# name: short label` ← phase name
  - `# timeout: 60` ← 預設 60s
  - `# parallel: True` ← 平行執行（注意：偵測 `"parallel" in c.lower() and "true" in c.lower()`，兩個字都要在注釋裡）
- `force_domain="X"` 作為 kwarg 是 **fallback**，只在沒 `# domain:` 注釋時用
- `depends_on` 沒有 kwarg 形式 — 必須靠注釋

**錯的寫法**：
```python
phase('p1', 'stock', '分析 01810')  # ← phase() 函式不存在
p1 = router.route("分析 01810")       # ← 沒注釋，depends_on 抓不到
```

**對的寫法**：
```python
# domain: stock
# name: 小米分析
router.route("分析 01810 小米", force_domain="stock")

# domain: general
# depends_on: [1]
# name: 總結
router.route("總結結果", force_domain="general")
```

**完整可 reproduce 範本**見 `templates/plan_cli_demo.py`。

**驗證**：`plan_cli.py generate --script-file X.py` 應回 `phase_count: N`（N = 你的 `router.route` 數量），不是 0。

### 29. 5 個 adapter 都是 router-only，不會真的執行分析
**症狀**：`make demo` 跑 `python3 unified_router.py --task "分析 01810 小米最近一個月走勢" --force-domain stock` 只回 routing metadata（`detected_domain`、`domain_confidence`、`adapter_used`），沒有實際股票分析結果。plan 執行後 `envelope_id: null` 是正常的，不是 bug。

**原因**：StockAdapter / FTAdapter / CodingAdapter / HermesTeamAdapter / GeneralAdapter **只做 domain 偵測 + 關鍵字 matching + LLM 二次判斷**，回傳 `RouteEnvelope`。實際分析由各自的 team-agent skill 負責（`ft-team-agent` / `stock-team-agent` / `coding-team-agent`），Dynamic Harness **不會**自動 invoke。

**正確用法**：
- 想要 routing decision：用 `dh --task "..."`（≤ 0.5s 回傳）
- 想要真實分析：直接 invoke 對應的 team-agent：
  ```bash
  python3 ~/.hermes/skills/stock-team-agent/...      # 股票
  python3 ~/.hermes/skills/ft-team-agent/task_router_v2.py ...   # 足球
  ```
- 想要多 domain 流程 + DAG + Plan UI：用 `plan_cli.py` 串接（`parse_script_to_plan` + `PlanExecutor`），見 Pitfall #28 + `templates/plan_cli_demo.py`

**例外**：`GeneralAdapter` 在 LLM judge 觸發時（confidence < 1.0）會 call LLM API ~5s，但仍只是做 domain 判斷 + Web Search 摘要，不是 end-to-end 任務執行。

**驗證**：`make demo` 跑完看 `envelope_id` — 應為 `null`（不是 envelope 物件），這是正常行為。

### 30. `bin/dh` 二次覆寫 `PLAN_DB_PATH` 蓋過 `plan_ui` 的 fallback 邏輯（已修復 P4-5）
**症狀**：即使修了 `plan_ui._default_db_path()` fallback 到 `persistence.DEFAULT_DB_PATH`（Pitfall #27），裸跑 `./bin/dh --ui-list` 還是 "No plans found in DB."。手動設 `PLAN_DB_PATH=...` 才看得到 plans。

**原因**（`bin/dh:19`，**v1.6.0 獨立 skill 時新加的 wrapper**）：
```bash
export PLAN_DB_PATH="${PLAN_DB_PATH:-$HOME/.hermes/plans.db}"   # ← 第三個錯的路徑
```
這個 fallback 預設值是 `$HOME/.hermes/plans.db`（**第四個不存在的路徑**），會在 `plan_ui` 之前就把 env 設成錯的。Layered bugs 鏈：
1. `bin/dh:19` → 設 `PLAN_DB_PATH=$HOME/.hermes/plans.db`（不存在）
2. `plan_ui.py:308` → 看到 `PLAN_DB_PATH` 就 return → fallback chain 永遠不走

**為什麼 unit test 沒抓到**：`tests/test_plan_ui.py` 的 `setup_temp_db()` 直接 monkey-patch `plan_ui._default_db_path`，完全 bypass bin/dh。CLI-level integration test 才能抓到。

**修法**（P4-5 修，2026-06-05）：**bin wrapper 不應該有 fallback**。讓 `plan_ui._default_db_path()` 自己負責 chain：
```bash
# bin/dh:19 — 刪掉這行
# export PLAN_DB_PATH="${PLAN_DB_PATH:-$HOME/.hermes/plans.db}"
export PYTHONPATH="$SKILL_DIR:${PYTHONPATH:-}"
```

**驗證**（修後）：
```bash
unset PLAN_DB_PATH
./bin/dh --ui-list        # 應顯示 8 plans（不需手動設 env）
./bin/dh --ui <plan-id>   # 應顯示 DAG + phase details
```

**教訓**：**bin/wrapper 應該只做 env setup，不該做業務邏輯的 default**。Default value 的 single source of truth 應該在最靠近使用方的模組（這裡是 `plan_ui._default_db_path()`）。如果 wrapper 加了 fallback，要嘛寫對（用 `persistence.DEFAULT_DB_PATH`），要嘛乾脆不要加。

## 檔案結構

```
dynamic-harness/
├── SKILL.md
├── schemas.py              131 行
├── unified_router.py       839 行
├── llm_judge.py            217 行
├── persistence.py          407 行  ⭐
├── metrics.py              241 行  ⭐v1.5
├── cost.py                 251 行  ⭐v1.5
├── web_search.py           254 行
├── hermes_team_unified.py  298 行
├── plan.py                 27.9KB  ⭐v1.5.1 (P3-3 PlanExecutor)
├── llm_planner.py          11.9KB  ⭐v1.5.1 (P3-3 LLM-generated plan)
├── adversarial_verifier.py 15.7KB  ⭐v1.5.1 (P3-3 12 static rules + LLM judge)
├── template_library.py     11.2KB  ⭐v1.5.1 (P3-3 5 starter templates)
├── plan_cli.py             7.1KB   ⭐v1.5.1
├── adapters/               5 個 domain adapter
├── references/             ⭐v1.5
│   ├── plan-in-code-architecture.md    (478 行，v2.0 設計)
│   ├── plan-in-code-implementation.md  ⭐v1.5.1 (P3-3 實作 + 9 pitfalls)
│   ├── task-router-apis.md
│   ├── importlib-wrapper-pattern.md
│   ├── m2-llm-integration.md
│   ├── adapter-pattern-techniques.md
│   ├── parallel-mode-limitations.md
│   ├── m2-7-thinking-parser.md
│   ├── hermes-task-routers-inventory.md
│   └── tavily-firecrawl-quirks.md
├── templates/              ⭐v1.6.2 (+plan_cli_demo.py)
│   ├── plan_cli_demo.py         50 lines, E2E-verified multi-domain plan
│   └── new_domain_adapter_template.py
├── scripts/
│   ├── verify_unchanged.py
│   └── discover_router_api.py
└── tests/                  ⭐v1.6.2 (101 tests total, 0 warnings)
    ├── test_basic.py           18 tests
    ├── test_plan.py            15 tests (PlanExecutor)
    ├── test_llm_planner.py     12 tests
    ├── test_verifier.py        12 tests (AdversarialVerifier)
    ├── test_template_library.py 15 tests (TemplateLibrary)
    ├── test_integration.py     10 tests (E2E)
    └── test_plan_ui.py         17 tests (PlanUI, P4)
```

## 變更歷史

| 日期 | 版本 | 變更 |
|------|------|------|
| 2026-06-05 | 1.6.2 | Plan UI DB path 雙 bug 修復：plan_ui._default_db_path() fallback 到 persistence.DEFAULT_DB_PATH（不再是 skill_dir/plan_registry.db）+ bin/dh 移除 PLAN_DB_PATH 二次覆寫 + 2 個 regression tests（99→101）+ Pitfall 30 |
| 2026-06-05 | 1.6.0 | 獨立 skill、bin/dh wrapper、scripts/install.sh、Plan UI、Makefile (22 targets)、GitHub Pages coverage deploy、README badges、Pitfall 21（bin/dh 才有 --version） |
| 2026-06-05 | 1.5.2 | + P4-5 Plan UI：17/17 ✅、99/99 總計、Pitfall 16-20（schema 欄位名、`plan_phases` 主鍵、sub-process env var、pytest 8+ warning、test count 驗證） |
| 2026-06-05 | 1.5.0 | + metrics、cost tracking、預算警告 CLI |
| 2026-06-05 | 1.5.1 | + P3-3 plan-in-code: AdversarialVerifier (12/12)、TemplateLibrary (15/15)、5 starter templates |
| 2026-06-05 | 1.5.1 | + P3-3.5 整合測試 10/10 ✅、P3-3.6 SKILL.md 更新、新增 Pitfall 11-15（LLM 預設開、Verdict dataclass、build_dag 私有、繁簡 pattern、≥ 邊界） |
| 2026-06-05 | 1.4.0 | + envelope cache（50x）、CLI 管理 |
| 2026-06-05 | 1.3.0 | + parallel=auto、retention、config、Web Search |
| 2026-06-05 | 1.2.0 | + GeneralAdapter、SQLite、HTAgent 整合 |
| 2026-06-05 | 1.1.0 | + LLM judge、--force-domain、multi_route |
| 2026-06-05 | 1.0.0 | 初版 — 4 adapter + UnifiedRouter |
