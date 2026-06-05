# Dynamic Harness

> 統一任務路由器 — 跨 5 個 domain 的 meta-dispatcher，production-ready with full observability。

**獨立 skill**（不再依附 `hermes-agent`）— v1.6.0 / 2026-06-05

## 30 秒上手

```bash
# 安裝（建立 ~/.local/bin/dh symlink）
bash scripts/install.sh

# 跑任務
dh --task "分析 01810 小米最近一個月走勢"

# 看 plan 狀態
dh --ui-list
dh --ui <plan-id> --live
```

## 功能

| 模組 | 功能 |
|------|------|
| **5 個 adapter** | ft / stock / coding / hermes-team / general |
| **Plan UI** | 終端 DAG 視覺化（ASCII + 顏色 + watch 模式） |
| **Plan-in-Code** | Plan 程式碼生成 + 自動執行 + adversarial verifier |
| **Multi-Route** | 自動拆子任務、平行執行、共識判斷 |
| **Metrics** | adapter 呼叫、cache hit、LLM judge 觸發全記錄 |
| **Cost Tracking** | 自動估算 API 成本 + 月預算警告 |
| **Envelope Cache** | 50x 加速（task 描述 fingerprint cache） |
| **SQLite Persistence** | plans / phases / metrics 全持久化 |
| **Web Search** | Tavily + Firecrawl 整合（General adapter） |
| **Template Library** | 預建 ft/stock/coding 模板 + 自訂 DSL |

## 測試

```bash
bash scripts/test-all.sh           # 99/99 測試
VERBOSE=1 bash scripts/test-all.sh # 顯示每個測試名稱
```

## 詳細文件

- [`SKILL.md`](SKILL.md) — 完整功能描述 + 18 個 pitfalls
- [`references/`](references/) — 12 份設計文件（plan-in-code、plan-ui、verifier、template library 等）
- [`tests/`](tests/) — 99 個整合測試

## 依賴

零外部依賴 — 全部用 Python 3.9+ stdlib。

可選：
- `pytest`（測試）
- `tavily-python` / `firecrawl-sdk`（Web Search adapter，沒安裝會 fallback 到 duckduckgo）

## 反安裝

```bash
bash scripts/install.sh --uninstall
```

## 授權

內部使用。
