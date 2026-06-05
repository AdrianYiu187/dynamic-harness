# 測試執行 checklist（2026-06-05 P4-5 後建立）

> 配合 Pitfall #19（pytest warning 過濾）+ Pitfall #20（測試數字別憑記憶）。
> 每次 SKILL.md 要寫「N/N ✅」或更新變更歷史前，跑一次這個 checklist。

## 1. 跑 pytest 看實際結果

```bash
cd ~/.hermes/skills/hermes-agent/dynamic-harness
python3 -m pytest tests/ --collect-only -q 2>&1 | tail -1   # 收集測試
python3 -m pytest tests/ --tb=no 2>&1 | tail -5            # 實際跑（注意 timeout）
```

**為何一次跑一個 file**：`tests/` 整包跑會超過 60s foreground timeout；分檔跑每個 < 25s。
**為何用 `--tb=no`**：縮短輸出（PASSED 已經足夠，不需要看 traceback）。
**為何忽略那 1 個 urllib3/LibreSSL warning**：環境問題（macOS 內建 Python 用 LibreSSL），非測試本身。

## 2. 把實際 N/N 對到 SKILL.md 各處

需要更新的位置（每次都檢查全部）：
- [ ] `## Phase X` 表格的「測試結果」欄
- [ ] 「**全部測試套件**」段落（列出每個檔案 N/N）
- [ ] 「**總計**」行的 N/N
- [ ] 「## 變更歷史」表格
- [ ] 「## 檔案結構」的 `tests/` 區塊（每個檔案 N tests）

## 3. 反例：P4-5 漏算

| 寫法 | 問題 |
|------|------|
| 「`tests/test_plan.py` 15/15 + `tests/test_basic.py` 16/16 + ...」 | test_basic.py 已經是 **18/18**（多 2 個），test_llm_planner.py 整個漏列 |
| 「總計 85/85 ✅」 | 實際 99/99 ✅（漏算 test_llm_planner.py 12 個） |

**教訓**：列出 test file 之前先 `pytest --collect-only` 確認有哪些檔案，**不要從 SKILL.md 上一版的「我記得有這些」推導**。

## 4. pytest.ini 過濾設定（已 commit）

```ini
[pytest]
filterwarnings =
    ignore::pytest.PytestReturnNotNoneWarning
```

放在 `~/.hermes/skills/hermes-agent/dynamic-harness/pytest.ini`。
**理由**：99 個測試用 `return` 風格，pytest 8+ 會丟 99 個 warning；
重寫測試違反 Rule 2/3，用設定檔一行過濾乾淨。

## 5. 數字演進記錄

| 日期 | 測試數 | 變更 | commit |
|------|--------|------|--------|
| 2026-06-05 | 16/16 | v1.5 初始 test_basic.py | - |
| 2026-06-05 | 64/64 | P3-3 + plan / verifier / template / integration | 387baa2 |
| 2026-06-05 | 99/99 | P4-5 + plan_ui 17 + 修正 test_basic.py 18 + 加 pytest.ini 過濾 | 512bb81 |

## 6. 給未來 Phase 5+ 的提醒

如果新增 phase 後測試數有變動：
1. 跑 `pytest --collect-only` 確認新檔案已收錄
2. 跑 `pytest --tb=no` 確認全綠
3. 更新本文件「數字演進記錄」
4. SKILL.md 5 個位置（見 §2）全部同步
5. 確認 `git diff SKILL.md` 沒有「總計 N/M」和分項加總不一致
