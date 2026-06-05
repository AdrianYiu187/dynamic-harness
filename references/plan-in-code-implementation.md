# Plan-in-Code 實作參考（P3-3，2026-06-05）

> 補充 SKILL.md 的 v2.0 路線圖：4 個新元件的實作細節、verifier 規則、template API。

## 1. `plan.py` — Plan + Phase + PlanExecutor

### 資料結構

```python
@dataclass
class Phase:
    id: int
    name: str
    sub_task: str
    force_domain: str = "general"
    depends_on: List[int] = field(default_factory=list)
    status: str = "pending"   # pending / running / completed / failed / skipped
    result: Optional[str] = None
    error: Optional[str] = None
    timeout_s: float = 300.0

@dataclass
class Plan:
    id: str
    task_text: str
    force_domain: Optional[str] = None
    created_at: float
    script_source: str
    status: str = "draft"
    metadata: Dict = field(default_factory=dict)
    phases: List[Phase] = field(default_factory=list)
```

### PlanExecutor — dataflow wave-based

**核心演算法**：
1. 找出所有 deps 已 satisfied 且未跑的 phase（ready set）
2. 用 `ThreadPoolExecutor` 同時跑（`max_workers=4`）
3. 等待全部完成
4. 若任一 failed → 標記所有未跑的下游為 `skipped`，結束
5. 回到步驟 1

**關鍵方法**：
- `_topological_sort(phases)` — DFS-based，避 cycle
- `_find_ready_phases()` — 過濾 `status == "pending"` 且所有 deps `status == "completed"`
- `_execute_phase(phase)` — 跑單一 phase，含 trace 記錄

**Resume 能力**：DB 中 `status=completed` 的 phase 自動跳過。

## 2. `llm_planner.py` — LLM-generated plan

透過 minimax M2.7 為 task 生成 DSL script，prompt 規範：
- 每個 phase 一行 `pN = router.route(...)`
- 強制 `force_domain`
- 標明 `depends_on=[...]`

`generate_plan_with_llm(task_text, force_domain=None, ...)` 回傳 `Plan`。

## 3. `adversarial_verifier.py` — 12 條 static rules

| # | Rule | 嚴重度 | 觸發 |
|---|------|--------|------|
| 1 | pass | - | 無問題 |
| 2 | cycle_detected | **critical** | DFS cycle 偵測 |
| 3 | self_dependency | fail | phase.depends_on 含自己 |
| 4 | missing_dep | warn | depends_on 指向不存在的 phase |
| 5 | missing_domain | warn | force_domain 為空字串 |
| 6 | unavailable_domain | warn | domain 不在 `available_domains` |
| 7 | too_many_phases | warn | phases > 10 |
| 8 | too_sequential | major | ≥80% phases 有 depends_on（**用 `>=`**） |
| 9 | short_sub_task | warn | sub_task < 5 chars |
| 10 | critical_skips_llm | fail | LLM-only 場景用了 fake adapter（confidence=1.0） |
| 11+ | LLM judge | - | 額外 call minimax，語意檢查任務覆蓋 |

**API**：
```python
from adversarial_verifier import verify_plan, Verdict

verdict = verify_plan(plan, offline_only=True)
verdict.verdict    # Verdict 物件（注意：.verdict 屬性是 str / Enum）
verdict.confidence # 0-1
verdict.issues     # List[Dict]
verdict.suggestions
```

**⚠️ Bug 預警**：本 session 結束時 `test_integration.py` 第 24 行用 `Verdict.PASS` / `Verdict.FAIL` 比較，但 `class Verdict` 不一定提供這些屬性。**修法**：先看 `class Verdict:` 完整定義，確認是 `Enum` / `@dataclass` / 普通 class。**或**用 `verdict.verdict in ("pass", "warn")` 字串比對（更安全）。

## 4. `template_library.py` — 5 個 starter templates

### 觸發詞表

| Template | Trigger patterns（regex, case-insensitive） |
|----------|---------------------------------------------|
| `ft_match_analysis` | `分析.*?赔率`, `赔率.*?分析`, `分析.*?vs`, `分析.*?對`, `足球.*?分析`, `[賽赛].*?分析`, `who.*?win`, `odds.*?analysis` |
| `stock_deep_research` | `深度分析.*?(股\|個股\|股票)`, `研究.*?股`, `invest.*?stock`, `analyze.*?stock`, `\d{4,6}.*?分析` |
| `code_refactor` | `重構`, `refactor`, `重寫.*?代碼`, `重寫.*?code`, `代碼.*?優化`, `優化.*?代碼` |
| `multi_market_compare` | `多市場`, `跨市場`, `multi.*?market`, `cross.*?market` |
| `investigation` | `調查`, `investigate`, `debug`, `為什麼.*?失敗`, `why.*?fail`, `找.*?原因`, `find.*?cause` |

**繁/簡中文用 `[賽赛]` 形式**避免漏觸發（已踩過坑）。

### API

```python
from template_library import (
    TemplateLibrary, instantiate_template,
    FT_MATCH_ANALYSIS, STOCK_DEEP_RESEARCH,
    CODE_REFACTOR, MULTI_MARKET_COMPARE, INVESTIGATION,
)

lib = TemplateLibrary()  # 5 個內建 templates
plan = lib.instantiate("分析曼聯 vs 車路士的赔率")
# → Plan with metadata={"generated_by": "template", "template": "ft_match_analysis", ...}

# 強制指定 template
plan = lib.instantiate("foo", template_name="stock_deep_research")

# 列出所有 templates
for info in lib.list_templates():
    print(info["name"], info["phase_count"])
```

### Template 結構

```python
@dataclass
class PlanTemplate:
    name: str
    description: str
    trigger_patterns: List[str]       # 任一 match 就觸發
    required_domains: List[str]
    phase_specs: List[Dict]           # {name, sub_task_template, force_domain, depends_on}
    rationale: str = ""
    # sub_task_template 可用 {task} 變數
```

### 各 template 的 DAG shape

| Template | phases | level 0 (parallel) | 依賴 |
|----------|--------|------------------|------|
| ft_match_analysis | 4 | 3 | p4 deps [1,2,3] |
| stock_deep_research | 4 | 3 | p4 deps [1,2,3] |
| code_refactor | 4 | 2 | p3 deps [1,2] → p4 deps [3] |
| multi_market_compare | 3 | 3 | 無依賴（全並行） |
| investigation | 5 | 1 | p2 deps [1] → p3,p4 deps [2] → p5 deps [3,4]（**diamond**） |

## 5. DSL 互通性驗證

Template 路徑和 LLM 路徑產出的 Plan 都能被 `parse_script_to_plan()` round-trip：

```python
plan = FT_MATCH_ANALYSIS.instantiate("曼聯 vs 車路士")
reparsed = parse_script_to_plan(plan.script_source, task_text=plan.task_text)
assert reparsed.phases[3].depends_on == [1, 2, 3]
```

## 6. P3-3.5 整合測試修復記錄（2026-06-05，已全部修完）

`tests/test_integration.py` 從 5/10 修到 **10/10 ✅**。三個 bug 一次解決：

1. **`Verdict.PASS/FAIL` 屬性不存在** — `class Verdict` 是 `@dataclass` 而非 `Enum`，欄位是 `verdict: str`。**修法**：把 `Verdict.PASS` 改成字串 `"pass"`、`.value` 拿掉。比對用 `verdict.verdict == "pass"`。
2. **`from plan import build_dag` import 殘留** — `build_dag` 不存在（`_topological_sort()` 是 private）。**修法**：刪 line 95 的 import，inline 算 DAG level 即可。
3. **`test_verification_catches_introduced_defects`** 注入 cycle 用同一個 plan 多個 phases 互相覆蓋。**修法**：明確先建好 defect plan，再驗證。

**sed 替換指令**（保留為 cookbook）：
```bash
# Verdict.PASS → "pass", .WARN → "warn", .FAIL → "fail"
sed -i '' 's/Verdict\.PASS/"pass"/g; s/Verdict\.WARN/"warn"/g; s/Verdict\.FAIL/"fail"/g' tests/test_integration.py
# .verdict.value → .verdict（移除 .value 屬性）
sed -i '' 's/\.verdict\.value/\.verdict/g' tests/test_integration.py
```

## 7. 9 個新 test pitfalls（已收錄到 SKILL.md）

| # | Pitfall | 觸發場景 |
|---|---------|---------|
| 7 | Module-level 函式呼叫需 try/except | `_load_api_key()` 在檔案頂層但定義在後面 → import 即崩 |
| 8 | Threshold 邊界用 `>=` | `ratio > 0.8` 在 `ratio == 0.8` 時漏抓 |
| 9 | Test cases 必須實際 match pattern | 寫 test 沒驗 regex 真的 match → False 但以為 code 錯 |
| 10 | 引用符號前 `grep -n "^def \|^class "` 確認存在 | `from plan import build_dag` 結果不存在 |
| 11 | 整合測試必須 `offline_only=True` 跳過 LLM | `verify_plan()` 預設 call LLM → CI 60s timeout |
| 12 | `Verdict` 是 dataclass 不是 Enum | 用 `Verdict.PASS` 或 `.value` → AttributeError |
| 13 | `build_dag()` 是私有，inline 算 level | 不要 import 不存在的 public API |
| 14 | 繁/簡 trigger pattern 要並列 | `[賽赛]` 形式兩者皆吃，別只寫繁體 |
| 15 | 邊界條件用 `>=` 不是 `>` | 「≥80% 為 sequential」要 `sequential_ratio >= 0.8` |

**通用規則**（跨多個 pitfall 提煉）：

- **LLM-wrapped code 測試前必找 offline kwarg**：先看函式簽名找正確 kwarg（`offline_only` / `use_llm` / `skip_llm`），別猜。
- **判斷類型先看完整定義**：`@dataclass` / `Enum` / 普通 class 對應不同使用方式，先 `read_file` 看 line 79 附近。
- **Threshold 寫法 + 測試 case 一起設計**：寫 code 前先想好邊界（`>` vs `>=`），測試 case 要涵蓋「剛好等於」的情境。
