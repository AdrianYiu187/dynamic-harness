# templates/plan_cli_demo.py
# ─────────────────────────────────────────────────────────────────────
# Plan-in-Code 已知可運行的多 phase 範本（2026-06-05 E2E 驗證）
#
# 用法：
#   python3 plan_cli.py generate --task "..." --script-file plan_cli_demo.py
#   python3 plan_cli.py execute  --plan-id <id-from-generate>
#   ./bin/dh --ui <plan-id>     # 渲染 DAG（v1.6.2+ 不需 PLAN_DB_PATH env var）
#
# Parser 規則（plan.py:parse_script_to_plan, regex-based）：
#   - 找 router.route("sub_task") 呼叫 → 每個 = 1 phase
#   - 注釋直接放在呼叫上方：
#       # domain: stock|ft|coding|hermes|general
#       # depends_on: [1, 2, 3]      ← 注意：這是 depends_on 唯一寫法
#       # name: short label
#       # timeout: 60                ← 秒
#       # parallel: True             ← 兩個字都要在注釋裡
#   - force_domain="X" kwarg 是 fallback（只在沒 # domain: 注釋時用）
#
# ⚠️ Dynamic Harness 的 adapter 是 router-only，不會做真實分析。
#    真正分析要直接 invoke ft-team-agent / stock-team-agent。
#    見 SKILL.md Pitfall #29。
# ─────────────────────────────────────────────────────────────────────


# === Phase 1: 並行股票分析 — 個股 A ===
# domain: stock
# name: 小米 01810 走勢
router.route("分析 01810 小米最近一個月走勢", force_domain="stock")


# === Phase 2: 並行股票分析 — 個股 B（與 Phase 1 平行）===
# domain: stock
# name: 騰訊 00700 走勢
router.route("分析 00700 騰訊最近一個月走勢", force_domain="stock")


# === Phase 3: 並行足球分析（展示多 domain）===
# domain: ft
# name: 曼城 vs 利物浦
router.route("分析 曼城 vs 利物浦 上一場比賽數據", force_domain="ft")


# === Phase 4: 綜合比較（等 1, 2, 3 都完成）===
# domain: general
# depends_on: [1, 2, 3]
# name: 綜合結論
# timeout: 120
router.route("綜合小米、騰訊走勢差異，並對比曼城利物浦數據", force_domain="general")
