# Hermes 內 4 套 task_router 完整盤點

> 寫於 2026-06-05（dynamic-harness v1.0.0 開發期間）
> 用於：任何時候要在 Hermes 內加新領域 adapter、判斷要不要覆用現有 router、評估整合難度

## 總覽

| Router | 技能路徑 | 腳本路徑 | 行數 | 領域 |
|--------|----------|----------|------|------|
| **FT DynamicTaskRouter** | `productivity/ft-team-agent` | `scripts/task_router_v2.py` | 8,119 | 足球博彩 |
| **FT 重構套件** | `productivity/ft-team-agent` | `scripts/task_router/` (4 檔) | ~200 (stub) | 足球博彩（同上，模組化中） |
| **Stock StockRouter** | `productivity/stock-team-agent` | `scripts/task_router/stock_router.py` | 1,359 | 股票分析 |
| **Coding CodingTaskRouter** | `autonomous-ai-agents/coding-team-agent` | `scripts/train/task_router.py` | 650 | 編碼開發 |
| **HermesTeam TaskRouter** | `productivity/hermes-team-agent` | `scripts/task_router.py` | 1,424 | 通用 27 種體育博彩 + 學術 + 通用 |

## 各 router 公開介面

### FT DynamicTaskRouter（task_router_v2.py）

```python
class DynamicTaskRouter:
    def analyze(task_text: str) -> Dict          # ★ 路由主入口，回傳 Dict
    def execute(analysis: Dict) -> Dict         # 執行 workflow
    def generate_reports(result: Dict) -> tuple # (text_report, json_report)
    def run(task: str, send_telegram: bool = True) -> Dict  # 一鍵執行
```

**analyze() 回傳結構**：
```python
{
    "task": str,
    "task_type": str,                          # e.g. "體育博彩分析"
    "capabilities": [(cap_id, weight, reason), ...],  # tuple 列表！
    "cap_ids": [str, ...],                     # 純 ID 列表（envelope 用這個）
    "workflow": [str, ...],                    # e.g. ["E50: 球隊狀態分析", ...]
    "confidence": float,
    "auto_created": bool,
}
```

**⚠️ 坑**：沒有 `route_task()`，呼叫會 `AttributeError`。用 `analyze()` 才對。

### Stock StockRouter（stock_router.py）

```python
class StockRouter:
    def __init__(self, symbol: str = None, region: str = "hk"): ...
    # 沒有統一 route_task，需自己讀取 symbol 後呼叫對應 handler
```

**TaskType enum**（10 種）：
- `FULL_ANALYSIS`, `TECHNICAL_ONLY`, `FUNDAMENTAL_ONLY`, `RISK_ASSESSMENT`
- `SENTIMENT_ANALYSIS`, `VALUATION_ONLY`, `COMPARISON`, `PORTFOLIO_REVIEW`
- `REAL_TIME_ALERT`, `HISTORICAL_BACKTEST`

**⚠️ 坑**：StockRouter 沒有統一路由方法，需要先 `__init__(symbol=...)` 後呼叫個別 handler。

### Coding CodingTaskRouter（train/task_router.py）

```python
class CodingTaskRouter:
    def route_task(task_text: str) -> RoutingResult  # ★ 路由主入口
```

**RoutingResult 結構**：
```python
@dataclass
class RoutingResult:
    intent: IntentCategory          # 22 種 enum（WEB_APP, API_DESIGN, ...）
    intent_confidence: float
    primary_role: str
    supporting_roles: list[RoleRecommendation]
    workflow: list[WorkflowStep]    # WorkflowStep(step_num, phase, action, roles, description)
    complexity: ComplexityAssessment
    suggested_commands: list[str]
    project_type: str
```

### HermesTeam TaskRouter（task_router.py）

```python
class TaskRouter:
    def identify_task_type(task_text: str) -> TaskProfile  # ★ 路由主入口
```

**TaskProfile 結構**（27 種 type）：
```python
@dataclass
class TaskProfile:
    type: str                       # e.g. "stock_analysis", "web_development", "code_audit"
    name: str                       # 顯示名稱
    description: str
    keywords: List[str]
    icon: str
    agent_config: Dict[str, Any]
    capabilities: List[str]         # E1, E2, ... 形式
    workflow: List[str]             # 步驟名稱
    output_template: str
    notion_db_id: str
```

**⚠️ 坑**：此 router 內含 27 種 task profile（多為體育博彩），但與 FT v2 的 179 個 E* capability 重疊 — 選用時需決定走哪條路。

## 載入策略對照

| Router | 載入方式 | 失敗行為 |
|--------|---------|---------|
| FT | `importlib.util` 動態 | 失敗 → regex fallback（識別隊伍） |
| Stock | `importlib.util` 動態 | 失敗 → 簡單 envelope（只塞 symbol） |
| Coding | `importlib.util` 動態 | 失敗 → regex 識別 intent enum |
| HermesTeam | `importlib.util` 動態 | 失敗 → 通用 envelope |

## 4 套都有的問題

1. **都有 print() 污染** — FT/Coding/HermesTeam 都在 `analyze`/`route_task` 過程中 print 大量 debug 訊息
2. **公開介面不統一** — 4 套 router 的入口方法名都不同
3. **capability 抽象層次不同** — FT 是 E1-E179 細粒度，HermesTeam 是 27 種粗粒度 task type
4. **無 LLM 二次判斷** — 都靠 regex，信心度低時容易錯

## 整合新領域的決策樹

```
要整合的新領域有現成 router？
├── 是 → 寫 adapter (DynamicHarness/adapters/<x>_adapter.py)
│       ├── API 是 dict → 解析 cap_ids + workflow
│       ├── API 是 dataclass → 用 getattr 取欄位
│       └── API 是 enum → 轉 CapabilityRef(id=enum.name)
└── 否 → 從頭寫一個新 router（用 FT v2 的 CapabilityRegistry 為樣板）
```
