# Adapter 模式：5 個可重用技術

> 從 dynamic-harness v1.0.0 開發萃取
> 用途：任何「包裝現有代碼、不修改它、統一介面」的任務

## 技術 1：importlib 動態載入

**問題**：直接 `import task_router_v2` 會觸發該模組所有頂層程式碼（包含 SQLite init、env 載入、可能還有 side effect）。

**解法**：
```python
import importlib.util
import sys
from pathlib import Path

def _load_ft_router():
    spec = importlib.util.spec_from_file_location(
        "ft_task_router_v2",  # sys.modules 的虛擬 key
        str(FT_ROUTER_PATH)   # 絕對路徑
    )
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules["ft_task_router_v2"] = module  # 必須註冊才能讓遞迴 import 解析
    spec.loader.exec_module(module)
    return module.DynamicTaskRouter()
```

**⚠️ 坑**：
- 必須 `sys.modules[虛擬key] = module`，否則該模組內部 `from x import y` 會失敗
- 例外一定要 try/except（依賴套件可能缺失）
- 回傳 `None` 而非 raise，讓 caller 決定 fallback 策略

## 技術 2：Protocol + runtime_checkable

**問題**：4 個 adapter 各自實作，沒有介面合約，未來新加的 adapter 可能漏掉方法。

**解法**：
```python
from typing import Protocol, runtime_checkable
from schemas import Domain, RouteEnvelope

@runtime_checkable
class DomainAdapter(Protocol):
    domain: Domain
    
    def can_handle(self, task_text: str) -> float: ...
    def route(self, task_text: str) -> RouteEnvelope: ...
```

**用法**：
```python
def load_adapter(cls):
    if not isinstance(cls(), DomainAdapter):
        raise TypeError(f"{cls.__name__} doesn't implement DomainAdapter")
```

**⚠️ 坑**：`runtime_checkable` 只檢查方法名存在，不檢查簽名。要嚴格驗證還是要 `inspect.signature`。

## 技術 3：contextlib.redirect_stdout 抑制下游污染

**問題**：FT/Coding/HermesTeam router 都在 `analyze()` 過程中 print 大量中文/emoji debug 訊息，污染 envelope JSON 輸出。

**解法**（在 unified_router 層統一做）：
```python
import contextlib
import io

with contextlib.redirect_stdout(io.StringIO()):
    envelope = adapter.route(task_text)
# 印 envelope JSON 時乾淨
```

**驗證**（test 4）：
```python
import io, sys
captured = io.StringIO()
old = sys.stdout
sys.stdout = captured
router.route("曼聯 對 車路士 赔率分析")
sys.stdout = old
assert "🔍" not in captured.getvalue()  # 沒被污染
```

**⚠️ 坑**：
- 抑制 stdout 不影響 logging（logging 走 stderr）
- 若下游用 `print(..., file=sys.stderr)` 要另外處理
- `raw_result` 仍保留，必要時可解析

## 技術 4：SHA256 + mtime 非侵入驗證

**問題**：宣稱「沒改動到原始 router」，口說無憑。

**解法**（在 test 5 實作）：
```python
import hashlib
from pathlib import Path
from datetime import datetime

def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        h.update(f.read())
    return h.hexdigest()[:16]

SKILL_CREATION = datetime(2026, 6, 5).timestamp()

for path in target_files:
    mtime = path.stat().st_mtime
    assert mtime < SKILL_CREATION, f"{path} was modified after skill creation!"
    print(f"hash={hash_file(path)} mtime={mtime:.0f} (unchanged)")
```

**已知 hash**（v1.0.0 鎖定）：

| 檔案 | SHA256 (前 16) | mtime |
|------|----------------|-------|
| `task_router_v2.py` (FT) | `b941f90eb0628ef6` | 1780369993 (2026-06-02) |
| `task_router.py` (HermesTeam) | `e698a31b84625780` | 1776688278 (2026-04-20) |
| `stock_router.py` | `c051c6c442d9ae2e` | 1778637698 (2026-05-13) |
| `task_router.py` (Coding) | `c8941a76346d8828` | 1777912940 (2026-05-05) |

**⚠️ 坑**：
- mtime 在 git checkout 時可能會變（解法：改用 git commit hash）
- 大檔案（>10MB）計算 SHA256 慢，可只 hash 前 1MB

## 技術 5：capability 信心度評分

**問題**：單純關鍵字計數無法區分「強烈信號」與「巧合」。

**解法**：
```python
def can_handle(self, task_text: str) -> float:
    text_lower = task_text.lower()
    hits = sum(1 for kw in self.KEYWORDS if kw.lower() in text_lower)
    if hits == 0:
        return 0.0
    return min(hits / 3.0, 1.0)  # 3 個命中 = 100% 信心
```

**避免搶任務的設計**：
```python
# Hermes 通用版：上限 0.7，避免搶專業 domain
def can_handle(self, task_text: str) -> float:
    for other_domain in [Domain.FT.value, Domain.STOCK.value, Domain.CODING.value]:
        for kw in DOMAIN_KEYWORDS[other_domain]:
            if kw.lower() in task_text:
                return 0.0  # 別跟專業 domain 搶
    # 否則給低分
    return min(hits / 3.0, 0.7)
```

**⚠️ 坑**：
- 多語言任務（中英混雜）信心度會被稀釋
- 同領域關鍵字加權不一樣時，純計數會誤判
- 解法：未來加 LLM 二次判斷（call MiniMax M2.7）對信心度 < 0.5 的任務做確認

## 附：API 探測腳本

`scripts/discover_router_api.py` 可用於自動探測任一 router 的公開介面：
```bash
python3 scripts/discover_router_api.py ~/.hermes/skills/productivity/ft-team-agent/scripts/task_router_v2.py
# 輸出：DynamicTaskRouter 的所有 public methods 與 signatures
```
