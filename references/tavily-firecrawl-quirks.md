# Tavily / Firecrawl Web Search 整合 — 已踩過的坑

> 從 dynamic-harness v1.3 GeneralAdapter 開發萃取
> 適用：任何在 Hermes 內整合 Tavily / Firecrawl 兩個付費 web search API 的場景

## API Key 載入的 .env 換行字元坑

**問題**：寫在 `~/.hermes/.env` 裡的 `TAVILY_API_KEY=tvly-xxx` 末尾若帶 `\n`，呼叫時會得到 **403 Invalid API Key**。

```python
# ❌ 沒 strip
import os
api_key = os.environ.get("TAVILY_API_KEY", "")
# 實際值: "tvly-xxx\n"  ← 注意尾巴的換行

headers = {"Authorization": f"Bearer {api_key}"}
# 送出時: "Bearer tvly-xxx\n"  ← API 端 hash 比對失敗 → 403
```

**解法**：載入 .env 時 `.strip()` 兩端。

```python
# ✅ 正確寫法
def _load_env_var(name):
    val = os.environ.get(name, "")
    return val.strip() if val else val

api_key = _load_env_var("TAVILY_API_KEY")
```

或者在 `load_dotenv()` 階段用 `dotenv` 套件（它會自動 strip）。

**驗證**：
```python
assert "\n" not in api_key
assert "\r" not in api_key
print(f"key length: {len(api_key)} (should be exactly 32-40 chars for Tavily)")
```

## Tavily 配額與限制

| 項目 | 數值 |
|------|------|
| 免費層 | 1000 次/月 |
| 速率限制 | 100 次/分鐘（Free） / 1000 次/分鐘（Paid） |
| 單次回傳結果數 | `max_results` 參數（預設 5） |
| 是否支援中文 | ✅ 支援（搜尋結果會混合中英） |
| 是否需要 credit card | 免費層**不需要** |

**API 端點**：`https://api.tavily.com/search`（POST）

**最小呼叫範例**：
```python
import json
import urllib.request

def tavily_search(query: str, api_key: str, max_results: int = 5) -> list[dict]:
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",  # "basic" | "advanced"（advanced 較慢但更精準）
    }
    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
        return data.get("results", [])
```

## Firecrawl 作為 Tavily 的 fallback

**使用場景**：Tavily 配額用完 或 結果不夠深，需要爬特定 URL。

**端點**：`https://api.firecrawl.dev/v0/scrape`（POST）

**最小呼叫範例**：
```python
def firecrawl_scrape(url: str, api_key: str) -> dict | None:
    payload = {
        "url": url,
        "pageOptions": {"onlyMainContent": True},
    }
    req = urllib.request.Request(
        "https://api.firecrawl.dev/v0/scrape",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key.strip()}",  # ← 一樣要 strip
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 402:  # Payment Required
            return None  # 配額用完
        raise
```

## 觸發條件：什麼時候用 web search？

**不該用**：
- 寫詩、創意寫作、純閒聊
- 程式碼問題（有 Hermes 內建工具更好）
- 已知答案的常識問題

**該用**：
- 「今天有什麼新聞」「X 隊最新傷兵」「蘋果股價現在多少」
- 任何需要**即時/最新**資訊的問題
- 一般搜尋引擎能查到的問題

**關鍵詞清單（正則觸發）**：
```python
WEB_SEARCH_TRIGGERS = [
    r"今天|最新|近期|剛剛|剛出|最近",   # 中文時間詞
    r"新聞|消息|報導",                  # 新聞類
    r"赔率|盤口|比分|賽程|轉會|傷兵",  # 體育類
    r"股價|市值|財報|公告|配息|除淨",  # 金融類
    r"是什麼|怎麼用|怎麼做|介紹",       # 知識類
    r"weather|news|stock price|score",  # 英文
]
```

**三層 fallback 設計**：
```
1. 強制指定（force_domain=general + --with-web-search）  → 必走 web search
2. 自動觸發（regex 命中上述任一 pattern）                 → 走 web search
3. 都沒命中                                                → 純 LLM 回應
```

## 結果整合到 envelope

**不要把整個 web search 結果塞進 envelope** — 太大，污染快取。

**正確做法**：
```python
def _envelope_with_web_search(self, task_text: str, web_results: list[dict]) -> dict:
    # 把 web 結果摘要成純文字（< 500 字）
    summary = "\n".join(
        f"• [{r.get('title', '?')}] {r.get('content', '')[:120]}"
        for r in web_results[:3]
    )
    return {
        "domain": "general",
        "task": task_text,
        "capability_id": "general.web_search",
        "result": {
            "summary": summary,
            "sources": [{"title": r.get("title"), "url": r.get("url")} for r in web_results[:3]],
            "raw_results_count": len(web_results),
        },
        "confidence": 0.7,
        "source": "general_adapter.web_search",
    }
```

## 常見錯誤

### 錯誤 1：把 web 結果直接 print 給用戶
```python
# ❌ 太冗長
for r in web_results:
    print(f"來源: {r['url']}\n內容: {r['content']}\n\n")
# 用戶看到 10 個連結 + 5000 字原始內容 → 不友善
```

### 錯誤 2：沒處理 4xx 錯誤
```python
# ❌ 沒 try/except
resp = urllib.request.urlopen(req)
# 配額用完 402 / API key 錯 403 → 直接 crash
```

### 錯誤 3：忘記設 timeout
```python
# ❌ 預設會 hang
resp = urllib.request.urlopen(req)  # 沒有 timeout
# 對方伺服器慢，整個 route 卡住
```

### 錯誤 4：用 `requests` 套件（環境可能沒裝）
```python
# ⚠️ dynamic-harness 偏好 urllib（零依賴）
import requests
resp = requests.post(url, json=payload, timeout=15)
# 萬一環境只有 stdlib 就 GG
```

## 與 Hermes 內建 web 工具的差異

Hermes 已有 `web_search` tool（在 `web` toolset）— 用 firecrawl / 第三方。

**何時用 Hermes 內建**：
- Agent loop 內（tool calling）
- 需要 LLM 自動決定何時搜尋
- 想用 Hermes 內建的 cache / rate limit

**何時用本文件 client**：
- 路由判定階段（一次搜尋、不需要 LLM 介入）
- 想完全控制請求格式與回應解析
- 想把搜尋結果塞進統一 envelope

## 整合新領域的決策樹

```
任務需要最新/即時資訊？
├── 否 → 純 LLM / 純 adapter 路由
└── 是
    ├── 領域是 stock / ft / coding → 用各 domain 的 API
    ├── 領域是 general / 無法判定
    │   ├── Tavily 配額還有 → 用 Tavily（便宜、快速）
    │   ├── Tavily 配額用完 → fallback Firecrawl
    │   └── 兩個都沒配 → fallback 純 LLM（會 hallucination，需警告）
    └── 領域是 hermes（學術）→ 用 arXiv / PubMed 等專門 API
```

## 維護注意事項

- Tavily 免費配額每月重置，月初第一週容易爆量
- Firecrawl 計價是按 page 算，scraping 大量 URL 前先估算
- 兩個 API 都不適合做大規模 batch，請用 queue + retry
- 若兩個都配額用完，dynamic-harness 會自動 fallback 純 LLM（envelope 標 `source: general_adapter.llm_only`）
