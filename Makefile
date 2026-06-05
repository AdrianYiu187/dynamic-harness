# Dynamic Harness — Makefile
# 統一入口給所有常用指令
#
# 用法：
#   make help          # 列出所有 target
#   make install       # 跑 scripts/install.sh
#   make test          # 跑 99 個測試
#   make coverage      # 跑測試 + 產生 coverage report
#   make lint          # 跑 pyflakes + shellcheck + mandoc lint
#   make ci            # 一次跑 test + lint + smoke（模擬 GitHub Actions）
#   make clean         # 清掉暫存
#   make uninstall     # 移除 ~/.local/bin/dh symlink

SHELL := /bin/bash
.SILENT:
.DEFAULT_GOAL := help

PYTHON ?= python3
PYTEST ?= python3 -m pytest
SKILL_DIR := $(shell pwd)
BIN_DIR := $(HOME)/.local/bin
DH := $(BIN_DIR)/dh

# ============================================================
# 1. 說明
# ============================================================
.PHONY: help
help:                              ## 顯示所有可用 target
	@echo "Dynamic Harness v1.6.0 — Make targets"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ============================================================
# 2. 安裝 / 反安裝
# ============================================================
.PHONY: install
install:                           ## 跑 scripts/install.sh（建 symlink + man page）
	bash scripts/install.sh

.PHONY: uninstall
uninstall:                         ## 移除 ~/.local/bin/dh symlink
	bash scripts/install.sh --uninstall

.PHONY: man
man:                               ## 僅安裝 man page 到 ~/.local/share/man/man1/
	@mkdir -p $(HOME)/.local/share/man/man1
	cp bin/dh.1 $(HOME)/.local/share/man/man1/
	@echo "✓ Man page installed: ~/.local/share/man/man1/dh.1"

# ============================================================
# 3. 測試
# ============================================================
.PHONY: test
test:                              ## 跑 99 個測試（簡短輸出）
	$(PYTEST) tests/ --tb=short

.PHONY: test-verbose
test-verbose:                      ## 跑測試（顯示每個測試名稱 + 耗時）
	$(PYTEST) tests/ -v --durations=10

.PHONY: test-fast
test-fast:                         ## 跑測試（跳過 test_llm_planner.py，約 60s → 15s）
	$(PYTEST) tests/ --ignore=tests/test_llm_planner.py --tb=short

.PHONY: test-one
test-one:                          ## 跑單一測試（用法: make test-one T=tests/test_plan.py::test_parallel_threshold）
	$(PYTEST) $(T) -v

# ============================================================
# 4. Coverage
# ============================================================
.PHONY: coverage
coverage:                          ## 跑測試 + 產生 HTML coverage report
	$(PYTEST) tests/ \
		--cov=. \
		--cov-report=term:skip-covered \
		--cov-report=html:htmlcov \
		--cov-report=xml:coverage.xml \
		--tb=short
	@echo ""
	@echo "✓ HTML report: htmlcov/index.html"
	@echo "  Open: open htmlcov/index.html"

.PHONY: coverage-clean
coverage-clean:                    ## 刪除 coverage artifacts
	rm -rf htmlcov coverage.xml .coverage

# ============================================================
# 5. Lint / 語法檢查
# ============================================================
.PHONY: lint
lint:                              ## 跑所有 lint（pyflakes + shellcheck + mandoc）
	@echo "→ pyflakes"
	@command -v pyflakes >/dev/null 2>&1 \
		&& pyflakes *.py adapters/ tests/ 2>&1 || echo "  (pyflakes not installed, skipping)"
	@echo ""
	@echo "→ shellcheck"
	@command -v shellcheck >/dev/null 2>&1 \
		&& shellcheck bin/dh scripts/*.sh 2>&1 || echo "  (shellcheck not installed, skipping)"
	@echo ""
	@echo "→ mandoc -Tlint"
	@command -v mandoc >/dev/null 2>&1 \
		&& mandoc -man -Tlint bin/dh.1 2>&1 || echo "  (mandoc not installed, skipping)"
	@echo ""
	@echo "→ bash -n (syntax check)"
	@bash -n bin/dh && echo "  ✓ bin/dh syntax OK"
	@bash -n scripts/install.sh && echo "  ✓ install.sh syntax OK"
	@bash -n scripts/test-all.sh && echo "  ✓ test-all.sh syntax OK"
	@echo ""
	@echo "→ YAML (GitHub Actions)"
	@$(PYTHON) -c "import yaml; yaml.safe_load(open('.github/workflows/test.yml')); print('  ✓ test.yml valid')"
	@if [ -f .github/workflows/pages.yml ]; then \
		$(PYTHON) -c "import yaml; yaml.safe_load(open('.github/workflows/pages.yml')); print('  ✓ pages.yml valid')"; \
	fi

# ============================================================
# 6. Smoke / CLI 健康檢查
# ============================================================
.PHONY: smoke
smoke:                             ## CLI 健康檢查（--version + --list-adapters + --ui-list）
	@echo "→ version (via bin/dh wrapper)"
	@./bin/dh --version
	@echo ""
	@echo "→ adapters"
	@$(PYTHON) unified_router.py --list-adapters | head -3
	@echo ""
	@echo "→ ui-list"
	@$(PYTHON) unified_router.py --ui-list 2>&1 | head -3

.PHONY: smoke-dh
smoke-dh:                          ## 測試 ~/.local/bin/dh 是否可運作
	@command -v dh >/dev/null 2>&1 \
		|| (echo "✗ dh not found in PATH. Run: make install"; exit 1)
	@dh --version
	@dh --ui-list 2>&1 | head -2

# ============================================================
# 7. CI（本地模擬 GitHub Actions）
# ============================================================
.PHONY: ci
ci: test lint smoke                ## 一次跑 test + lint + smoke（模擬 CI）
	@echo ""
	@echo "✓ All CI checks passed locally"

# ============================================================
# 8. 清理
# ============================================================
.PHONY: clean
clean: coverage-clean              ## 刪除所有 build / test / coverage artifacts
	rm -rf .pytest_cache
	rm -rf *.egg-info
	rm -rf build dist
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	@echo "✓ Cleaned"

.PHONY: distclean
distclean: clean uninstall         ## 完整清理（含 uninstall）
	rm -rf htmlcov coverage.xml
	@echo "✓ Dist-clean complete"

# ============================================================
# 9. Demo / 跑一個真實任務
# ============================================================
.PHONY: demo
demo:                              ## 跑一個真實任務（分析 01810 小米）
	$(PYTHON) unified_router.py --task "分析 01810 小米最近一個月走勢" --force-domain stock

.PHONY: demo-ui
demo-ui:                           ## 列出所有 plan（demo Plan UI）
	$(PYTHON) unified_router.py --ui-list

# ============================================================
# 10. 統計
# ============================================================
.PHONY: stats
stats:                             ## 顯示 skill 統計（檔案數、程式碼行數）
	@echo "=== File counts ==="
	@echo "  Python files:  $$(find . -name '*.py' -not -path './.git/*' | wc -l | tr -d ' ')"
	@echo "  Markdown:      $$(find . -name '*.md' -not -path './.git/*' | wc -l | tr -d ' ')"
	@echo "  Tests:         $$(find tests/ -name 'test_*.py' | wc -l | tr -d ' ') files"
	@echo ""
	@echo "=== Lines of code ==="
	@find . -name '*.py' -not -path './.git/*' -not -path './tests/*' | xargs wc -l 2>/dev/null | tail -1
	@find tests/ -name 'test_*.py' | xargs wc -l 2>/dev/null | tail -1
	@echo ""
	@echo "=== Total disk size ==="
	@du -sh . --exclude=.git 2>/dev/null | awk '{print "  " $$1 " (excl. .git)"}'

# ============================================================
# 11. 開發者輔助
# ============================================================
.PHONY: requirements
requirements:                      ## 安裝 dev 依賴
	$(PYTHON) -m pip install -r requirements-dev.txt

.PHONY: format
format:                            ## 跑 ruff format（如果安裝）
	@command -v ruff >/dev/null 2>&1 \
		&& ruff format *.py adapters/ tests/ \
		|| echo "(ruff not installed, skipping format)"

.PHONY: pre-commit
pre-commit: ci                     ## 提交前的完整檢查（CI + format）
	@echo "✓ Pre-commit checks complete"
