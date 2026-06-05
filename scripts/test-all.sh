#!/usr/bin/env bash
# Dynamic Harness 完整測試套件
#
# 跑 tests/ 下所有測試，統計 pass/fail 數量
# 用法：bash scripts/test-all.sh
# 環境變數：VERBOSE=1 顯示每個測試名稱

set -e

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SKILL_DIR"

if [[ "$VERBOSE" == "1" ]]; then
    python3 -m pytest tests/ -v
else
    python3 -m pytest tests/ --tb=short
fi
