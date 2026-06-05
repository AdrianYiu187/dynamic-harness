# MiniMax M2.7 API 整合模式

> 從 dynamic-harness v1.1.0 LLM 二次判斷開發萃取
> 適用：任何在 Hermes 內直接呼叫 MiniMax M2.7 (chat.completions) 的場景

## API 設定

```python
import json
import urllib.request
import os
from pathlib import Path

# 從 ~/.hermes/.env 讀
def _load_env():
    env_path = Path.home() / ".hermes" / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                if k.startswith("MINIMAX_") or k == "MINIMAX_API_KEY":
                    os.environ.setdefault(k, v)

_load_env()

# 標準設定
API_KEY = os.environ.get("MINIMAX_API_KEY", "")
BASE_URL = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.io/v1").rstrip("/")
MODEL = "MiniMax-M2.7-highspeed"  # 標準 model 名（M3 用 minimax-m3）
```

## ⚠️ 核心坑：M2.7 的 <think> 區塊

**M2.7 會在最終答案之前先輸出 `<think>...</think>` 區塊做內部推理。**

如果 `max_tokens` 太小，會被 thinking 區塊用光，**最終答案是空的**：

```python
# ❌ 這樣會失敗：max_tokens=20，thinking 區塊用光所有 token
raw = call_minimax(messages, max_tokens=20)
# → raw = "<think>\n用戶詢問的是...（未完成）"  ← 沒有最終答案
```

**正解**：

```python
# ✅ max_tokens 必須給到 500+（thinking 通常用 200-400 tokens）
raw = call_minimax(messages, max_tokens=500)
# → raw = "<think>\n用戶詢問的是...\n</think>\n\nft"  ← 答案在後面
```

**必須剝離 thinking 區塊後再解析答案**：

```python
import re

def _strip_thinking(text: str) -> str:
    """剝離 M2.7 的 <think>...</think> 區塊"""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    return text.strip()

# 使用
raw = call_minimax(messages, max_tokens=500)
clean = _strip_thinking(raw)
# clean 才是真正要解析的內容
```

**驗證已剝離**：
```python
assert "<think>" not in clean
assert "</think>" not in clean
```

## 完整客戶端模板

```python
import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

def call_minimax(
    messages: list,
    model: str = "MiniMax-M2.7-highspeed",
    max_tokens: int = 500,    # ⚠️ 至少 500，否則 thinking 用光
    timeout: int = 15,
) -> str | None:
    """呼叫 MiniMax M2.7 chat completion
    
    Returns:
        assistant 回覆文字（含 thinking 區塊）
        失敗時回傳 None
    """
    if not API_KEY:
        log.warning("MINIMAX_API_KEY not set")
        return None
    
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.0,  # 路由/分類任務建議 0
    }
    
    try:
        req = urllib.request.Request(
            f"{BASE_URL}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return content if content else None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        log.warning(f"MiniMax HTTP {e.code}: {body}")
        return None
    except urllib.error.URLError as e:
        log.warning(f"MiniMax URL error: {e}")
        return None
    except Exception as e:
        log.warning(f"MiniMax call failed: {type(e).__name__}: {e}")
        return None
```

## Prompt 設計模式

### 模式 1：嚴格枚舉分類（推薦）

```python
_SYSTEM = """你是任務路由助手。給定一段用戶任務描述，判斷它屬於哪個領域。

領域列表（嚴格遵守，不要新增）：
- ft: 足球博彩、赔率、盤口、賽事分析
- stock: 股票、股市、財報、估值、技術分析
- coding: 編程、網頁開發、API、代碼審計
- hermes: 通用研究、學術論文、數據分析
- general: 無法判斷或跨領域

輸出格式（嚴格遵守）：只回傳一個英文領域名稱，無其他文字、不要解釋。"""
```

**效果**：5/6 正確率（剩 1 個是 LLM 合理判斷為 general 的邊界案例）。

### 模式 2：JSON 數組回傳

```python
_SYSTEM = """你是任務拆分助手。給定一段用戶輸入，如果它包含多個獨立的子任務，
把它拆成多個獨立任務；如果是單一任務，回傳原任務。

輸出格式（嚴格遵守 JSON 數組，不要其他文字）：
- 單一任務：["原任務文字"]
- 多任務：["子任務1", "子任務2", ...]

每個子任務應保持原語言、保留關鍵實體（股票代碼、隊伍名、檔名等）。"""
```

**解析方式**：
```python
raw = call_minimax(messages, max_tokens=500)
clean = _strip_thinking(raw)

# 找 [ 和 ] 之間的內容（容忍 LLM 在 JSON 前後加文字）
start = clean.find("[")
end = clean.rfind("]")
if start == -1 or end == -1:
    return None
json_str = clean[start:end+1]

try:
    result = json.loads(json_str)
    if isinstance(result, list) and all(isinstance(x, str) for x in result):
        return result
except json.JSONDecodeError:
    return None
```

**效果**：多任務能正確拆（例：「皇馬赔率 + 寫爬蟲」→ 2 個子任務），但邊界模糊的（例：「分析 01810 並做網頁儀表板」）LLM 會視為 1 個任務。

**雙策略解法**：先跑啟發式（regex 切分連接詞），失敗再用 LLM 補位（dynamic-harness `multi_route()` 採用此策略）。

## 性能 & 限制

| 項目 | 數值 |
|------|------|
| 簡單分類任務延遲 | 2-5 秒 |
| JSON 數組拆分任務延遲 | 3-8 秒 |
| 上下文視窗 | 204,800 tokens（M2.7） |
| M3 上下文視窗 | 1,000,000 tokens |
| Token 計費 | 看 .env 設定，預設用戶層級 |

## 何時該用 LLM 二次判斷

**不要無腦啟用**。判斷標準：

| 場景 | 是否用 LLM | 理由 |
|------|----------|------|
| Regex 信心度 ≥ 0.7 | ❌ 不必 | 多花 3-5 秒無實質幫助 |
| Regex 信心度 0.3-0.7 | ✅ 推薦 | 邊界情況，LLM 較準 |
| Regex 信心度 < 0.3 | ✅ 必須 | 完全沒頭緒，LLM 是唯一希望 |
| 任務極短（< 5 字） | ⚠ 視情況 | LLM 對極短文本也常誤判 |

**三層 fallback 設計**（dynamic-harness 採用）：
```
1. 強制指定（force_domain）       → 100% 信心度
2. Regex 自動判斷（信心度 ≥ 0.5） → regex 結果
3. Regex 信心度 < 0.5             → call LLM 二次判斷
   ├─ 成功                         → 信心度 0.8
   └─ 失敗                         → 用 regex 結果
```

## 已驗證的測試案例

```python
# 全部 5/6 正確（剩 1 個 LLM 合理判為 general）
test_cases = [
    ("曼聯 對 車路士 赔率", "ft"),
    ("分析 01810 股票", "stock"),
    ("寫一個 React 應用", "coding"),
    ("今天天氣如何", "general"),
    ("幫我看看小米最新消息", "stock"),  # LLM 判為 general（邊界）
    ("arXiv 找 transformer 論文", "hermes"),
]
```

## 常見錯誤

### 錯誤 1：忘記剝離 thinking 區塊
```python
# ❌ raw 包含 "<think>...</think>\n\nft"
if "ft" in raw.lower():  # True（因為 thinking 區塊可能提到 "ft"）
    return "ft"  # 誤判
```

### 錯誤 2：max_tokens 太小
```python
# ❌ max_tokens=20
raw = call_minimax(messages, max_tokens=20)
# raw 可能是 "<think>\n用戶詢問的是曼聯對車路"（截斷）
# 解析失敗
```

### 錯誤 3：忘記處理 HTTP 錯誤
```python
# ❌ 沒 try/except
raw = urllib.request.urlopen(req).read()
# 任何網路錯誤都會 crash
```

### 錯誤 4：忘記 .env 載入
```python
# ❌ 直接讀 os.environ
api_key = os.environ.get("MINIMAX_API_KEY")
# 若 .env 還沒被 shell 載入 → None
```

### 錯誤 5：model 名打錯
```python
# ❌ 大小寫敏感
model = "minimax-m2.7-highspeed"  # 404 Not Found
# ✅ 正確
model = "MiniMax-M2.7-highspeed"
```

## 與 hermes-agent 內建客戶端的差異

Hermes 內部已有 `agent/model_metadata.py` 和 provider 抽象（如 `minimax`、`minimax-cn`），但那些是給 `AIAgent` 用的（複雜的 provider routing、credential pool、fallback）。

**何時用 hermes-agent 內建**：要做完整對話、tool calling、串流輸出時。
**何時用本文件客戶端**：只要 1 次性 LLM 呼叫（分類、抽取、判定）時 — 輕量、零依賴、不污染 AIAgent 的 session 紀錄。

如果 dynamic-harness 之後要進化為完整 agent，應改用內建 provider。
