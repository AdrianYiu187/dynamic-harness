# M2.7 Thinking Block 解析模式

## 問題

呼叫 `MiniMax-M2.7-highspeed` 模型時，回應**總是**先回 `<think>...</think>` 思考區塊，再給實際答案。例如：

```
<think>
用戶詢問的是曼聯對車路士的赔率...
</think>

ft
```

如果直接解析整個回應會失敗：
- `ft` 這種單字答案夾在 thinking 後面
- 有時 thinking 區塊用完所有 token，沒有答案

## 規則

1. **`max_tokens` 必須 ≥ 500** — M2.7 的 thinking 會消耗大量 token，`max_tokens=20` 只回得到 thinking
2. **解析前先 strip thinking 區塊**：
   ```python
   import re
   text = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
   ```
3. **匹配答案時容忍 thinking 殘留** — fallback 用 `re.search` 而不是 `==` 比對

## 實作範例

`dynamic-harness/llm_judge.py` 內的 `_strip_thinking()`：

```python
def _strip_thinking(text: str) -> str:
    import re
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    return text.strip()
```

## 驗證（2026-06-05 實測）

| 設定 | 結果 |
|------|------|
| `max_tokens=20` | 只回 `<think>` 區塊，無答案 |
| `max_tokens=500` | thinking + 答案，strip 後正確 |
| 沒 strip | 解析失敗（domain 找不到） |
| strip 後 | 5/6 答案正確（1 個 LLM 判斷為 general） |

## 推廣應用

任何呼叫 M2.7 的場景都應用此模式：
- HermesTeamAgent 共識引擎（已有部分處理）
- Stock Coding FT 的 LLM 二次判斷
- 任何未來新加的 LLM 整合
