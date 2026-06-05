# Hermes 4 套 task_router API 速查

> 動態整合時必查 — 各 router 公開 API 不同，呼叫前先確認。

## 1. FT DynamicTaskRouter（8,119 行）

**檔案**：`~/.hermes/skills/productivity/ft-team-agent/scripts/task_router_v2.py`

**公開方法**：
| 方法 | 簽名 | 用途 |
|------|------|------|
| `analyze` | `(task_text: str) -> Dict` | 分析任務，回傳 capabilities + workflow |
| `execute` | `(analysis: Dict) -> Dict` | 執行分析後的 workflow |
| `generate_reports` | `(result: Dict) -> tuple` | 生成 text + json 報告 |
| `run` | `(task: str, send_telegram: bool = True) -> Dict` | 端到端執行 |

**回傳結構**（`analyze`）：
```python
{
    "task": str,
    "task_type": str,                    # e.g. "體育博彩分析"
    "capabilities": [(cap_id, weight, reason), ...],  # tuples!
    "cap_ids": [str, ...],               # 純 ID 列表 ← 用這個
    "workflow": [str, ...],              # ["E50: 球隊狀態分析", ...]
    "confidence": float,
    "auto_created": bool,
}
```

**陷阱**：
- `capabilities` 是 tuples，**不要直接 iterate 字串化** — 用 `cap_ids` 純字串版
- `workflow` 元素是 `"E50: 球隊狀態分析"` 格式 — 拆 ID 用 `split(":", 1)[0]`
- 內部會 `print()` 大量輸出（🔍 📋 🔧 等 emoji）— 在 caller 端 redirect_stdout

## 2. StockRouter（1,359 行）

**檔案**：`~/.hermes/skills/productivity/stock-team-agent/scripts/task_router/stock_router.py`

**公開類**：
- `TaskType` — 任務類型 enum
  - `FULL_ANALYSIS`, `TECHNICAL_ONLY`, `FUNDAMENTAL_ONLY`, `RISK_ASSESSMENT`,
  - `SENTIMENT_ANALYSIS`, `VALUATION_ONLY`, `COMPARISON`, `PORTFOLIO_REVIEW`,
  - `REAL_TIME_ALERT`, `HISTORICAL_BACKTEST`
- `StockRouter(symbol: str = None, region: str = "hk")` — 主類

**典型方法簽名**（不同版本可能不同）：
- `route_task(task: str)` 或 `route(task: str)` 或 `process(task: str)`

**陷阱**：
- 方法名不固定，**先用 `hasattr` 探測**
- 內部 print 訊息少
- `region` 預設 `"hk"`

## 3. CodingTaskRouter（650 行）

**檔案**：`~/.hermes/skills/autonomous-ai-agents/coding-team-agent/scripts/train/task_router.py`

**公開枚舉**：
- `IntentCategory` — 22 種意圖
  - 開發：WEB_APP, MOBILE_APP, DESKTOP_APP, API_DESIGN, BACKEND_SERVICE, FRONTEND_COMPONENT, FULLSTACK_APP
  - 數據：DATABASE_DESIGN, DATA_PIPELINE, MACHINE_LEARNING
  - DevOps：DEVOPS_CICD, CONTAINERIZATION, INFRASTRUCTURE
  - 安全：SECURITY_AUDIT, SECURITY_FIX
  - 質量：REFACTOR, CODE_REVIEW, TEST_GENERATION, DEBUG_FIX
  - 文檔：DOCUMENTATION, README
  - 擴展：EXTENSION_RECOMMEND, TECH_CONSULT
  - 未知：UNKNOWN

**公開 dataclass**：
- `RoleRecommendation` — 角色推薦（id, name, weight, reason）
- `WorkflowStep` — 工作流步驟（step_num, phase, action, roles, description）
- `ComplexityAssessment` — 複雜度評估
- `RoutingResult` — 完整路由結果

**主類**：`CodingTaskRouter` — `route_task(task: str) -> RoutingResult`

**陷阱**：
- 大量 `print()` 輸出（`[Router] 分析請求...`）— caller 端 redirect_stdout
- 識別不到意圖時返回 `IntentCategory.UNKNOWN`
- 框架檢測需手動加分（React +0.9, FastAPI +0.85 等）

## 4. HermesTeam TaskRouter（1,424 行）

**檔案**：`~/.hermes/skills/productivity/hermes-team-agent/scripts/task_router.py`

**公開 dataclass**：
- `TaskProfile` — 任務配置檔
  ```python
  @dataclass
  class TaskProfile:
      type: str          # "stock_analysis" | "web_development" | ...
      name: str
      description: str
      keywords: List[str]
      icon: str
      agent_config: Dict[str, Any]
      capabilities: List[str]
      workflow: List[str]
      output_template: str
      notion_db_id: str = ""
  ```

**TASK_PROFILES** 預設 6 種：
- `stock_analysis`, `web_development`, `code_audit`, `paper_analysis`, `data_analysis`, `general`

**主類**：`TaskRouter(output_dir: Path = None)` — `identify_task_type(task: str) -> TaskProfile`

**陷阱**：
- 識別閾值是 `best_score < 1.5` 時 fallback 到 `general`（不是 regex 信心度）
- 用 `print(f"🔍 任務識別...")` 輸出 — caller 端 redirect_stdout
- 整合時**不直接呼叫**這個，**包裝成 UnifiedTaskRouter**（見 `hermes_team_unified.py`）

## 通用整合模式

```python
import importlib.util
import sys

def _load_router_class(router_path: Path, class_name: str):
    spec = importlib.util.spec_from_file_location("custom_name", str(router_path))
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules["custom_name"] = module
    spec.loader.exec_module(module)
    return getattr(module, class_name, None)
```

**Adapter 內呼叫模式**（避免 hard-fail）：
```python
raw = None
for method_name in ["route_task", "route", "analyze_task", "process", "dispatch"]:
    method = getattr(router_instance, method_name, None)
    if method and callable(method):
        try:
            raw = method(task_text)
            break
        except TypeError:
            continue
if raw is None:
    raw = {"detected": True, "router_class": router_instance.__class__.__name__}
```

## 維護注意事項

- 各 router 由不同人維護，API 可能變動
- 整合測試必須每次跑（`tests/test_basic.py` 的 [5] 檢查 hash）
- 新增 router 時先建立 `references/task-router-apis.md` 對應段落
