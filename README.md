# Dynamic Harness

[![Tests](https://github.com/adrian/dynamic-harness/actions/workflows/test.yml/badge.svg)](https://github.com/adrian/dynamic-harness/actions/workflows/test.yml)
[![Coverage](https://img.shields.io/badge/coverage-99%25-brightgreen)](https://adrian.github.io/dynamic-harness/landing.html)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> 統一任務路由器 — 跨 5 個 domain 的 meta-dispatcher，production-ready with full observability。

**獨立 skill**（不再依附 `hermes-agent`）— v1.6.0 / 2026-06-05

[![Tests](https://img.shields.io/badge/tests-99%2F99-brightgreen)](tests/)
[![Coverage](https://img.shields.io/badge/coverage-77%25-yellow)](tests/)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](bin/dh)
[![License](https://img.shields.io/badge/license-internal-lightgrey)](#授權)

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

## CI

`.github/workflows/test.yml` 提供 GitHub Actions：

| Job | 內容 |
|-----|------|
| **test** | 矩陣測試：Ubuntu + macOS × Python 3.9 / 3.10 / 3.11 / 3.12（共 8 組合），跑 99 個測試 + CLI smoke test + shell 語法檢查 + man page lint |
| **lint** | pyflakes（語法檢查）+ shellcheck（shell script 品質） |
| **coverage** | 產生 coverage.xml → 上傳 Codecov + 存 HTML 報告 artifact 7 天 |

**啟用步驟**（推上 GitHub 後自動運作）：
1. Push 到 GitHub
2. （可選）到 https://codecov.io 連結 repo 拿 token
3. 設 repo secret `CODECOV_TOKEN`（coverage job 會自動使用）

**本地模擬 CI 步驟**：
```bash
python -m pytest tests/ -v --tb=short --durations=10
python unified_router.py --version && python unified_router.py --ui-list
bash -n bin/dh && bash -n scripts/*.sh
mandoc -man -Tlint bin/dh.1   # 需先 brew install mandoc
```

## 詳細文件

- [`SKILL.md`](SKILL.md) — 完整功能描述 + 18 個 pitfalls
- [`bin/dh.1`](bin/dh.1) — man page（groff/mandoc 格式）
- [`references/`](references/) — 12 份設計文件（plan-in-code、plan-ui、verifier、template library 等）
- [`tests/`](tests/) — 99 個整合測試

## Man Page

```bash
# 安裝（透過 install.sh 自動）
bash scripts/install.sh   # 自動 cp bin/dh.1 → ~/.local/share/man/man1/

# 查看
man dh
mandoc -man bin/dh.1 | less   # 跳過安裝直接看
```

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
