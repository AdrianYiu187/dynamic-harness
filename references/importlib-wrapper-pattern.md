# 不動現有系統的整合模式 — importlib + 頂層包裝

> 場景：你要新增功能但不能（或不該）改現有程式碼。dynamic-harness 用此模式整合 4 套現有 task_router。

## 何時用

- 現有檔案很大（1,000+ 行），動它風險高
- 現有檔案由他人維護，改了會衝突
- 需要給現有功能加 wrapper（metrics、retry、fallback、observability）
- 想要 A/B test 兩種行為而不想改原碼

## 核心原則

1. **絕不修改現有檔案** — 即使「只加一行」也禁止
2. **用 importlib 動態載入** — 不污染 sys.path 也不影響 import 快取
3. **頂層包裝** — 寫新的 `wrapper_xxx.py` 對外提供介面
4. **驗證未變** — 在測試裡跑 hash + mtime 檢查

## importlib 載入模式

```python
import importlib.util
import sys
from pathlib import Path

def _load_external_class(router_path: Path, class_name: str):
    """動態載入外部模組的類別，不污染 sys.path"""
    if not router_path.exists():
        return None
    
    spec = importlib.util.spec_from_file_location(
        "custom_unique_name",   # 避免跟 sys.modules 衝突
        str(router_path),
    )
    if spec is None or spec.loader is None:
        return None
    
    module = importlib.util.module_from_spec(spec)
    sys.modules["custom_unique_name"] = module  # 讓內部 import 找得到
    spec.loader.exec_module(module)
    
    return getattr(module, class_name, None)
```

**關鍵點**：
- `spec_from_file_location(name, path)` 給模組一個唯一名稱
- 必須註冊到 `sys.modules` — 否則模組內部的 `from xxx import yyy` 會失敗
- `getattr(module, class_name, None)` 用 `None` 預設值，避免 AttributeError

## 頂層包裝模式

```python
# hermes_team_unified.py 簡化版
from unified_router import UnifiedRouter
from schemas import RouteEnvelope

def _load_hta_router():
    TaskRouter, TASK_PROFILES = None, None
    spec = importlib.util.spec_from_file_location(
        "hta_task_router",
        str(Path.home() / ".hermes/skills/productivity/hermes-team-agent/scripts/task_router.py"),
    )
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        sys.modules["hta_task_router"] = module
        spec.loader.exec_module(module)
        TaskRouter, TASK_PROFILES = module.TaskRouter, module.TASK_PROFILES
    return TaskRouter, TASK_PROFILES

class UnifiedTaskRouter:
    """整合 HermesTeamAgent + Dynamic Harness"""
    LOCAL_LOW_CONFIDENCE_TYPES = {"general"}
    
    def __init__(self, ...):
        self.HTA_TaskRouter, self.HTA_TASK_PROFILES = _load_hta_router()
        self.unified = UnifiedRouter(...)
    
    def route(self, task_text, force_fallback=False):
        # 1. 試本地
        local_type = None
        if self.HTA_TaskRouter and not force_fallback:
            try:
                profile = self.HTA_TaskRouter().identify_task_type(task_text)
                local_type = profile.type
            except Exception:
                pass
        
        # 2. 判斷是否 fallback
        fallthrough = (force_fallback 
                      or local_type in self.LOCAL_LOW_CONFIDENCE_TYPES
                      or local_confidence < self.threshold)
        
        # 3. 委派
        if fallthrough:
            return self.unified.route(task_text)
        else:
            return self._wrap_local(task_text, local_type)
```

## 驗證未變的測試

```python
import hashlib
from datetime import datetime

def test_existing_routers_unchanged():
    targets = [
        Path.home() / ".hermes/skills/productivity/ft-team-agent/scripts/task_router_v2.py",
        Path.home() / ".hermes/skills/productivity/hermes-team-agent/scripts/task_router.py",
        # ... 其他
    ]
    threshold = datetime(2026, 6, 5).timestamp()  # skill 建立時間
    for p in targets:
        assert p.stat().st_mtime < threshold, f"{p.name} was modified!"
```

**為什麼用 mtime 不用 hash**：
- hash 對內容改動敏感，但 mtime 對「是否被這個 session 改過」也敏感
- mtime < 創建時間 = 沒被改（簡單且足夠）
- hash 需要先記錄基線，mtime 直接看時間戳

## 常見陷阱

### 陷阱 1：sys.modules 名稱衝突
如果兩個 importlib 載入都用 `"custom"` 名字，第二次會拿到第一次的模組（已快取）。
**解法**：用唯一名稱（路徑 hash 或 `f"{path.stem}_{id(path)}"`）

### 陷阱 2：downstream import 找不到
`spec_from_file_location` 載入的模組內若有 `from foo import bar`，`foo` 會用 `sys.path` 找。
**解法**：
- 把 `sys.path` 加入模組所在目錄
- 或先 `import foo` 觸發正常 import
- 或用 unique name 註冊到 `sys.modules`（已驗證有效）

### 陷阱 3：print 污染 stdout
被包裝的程式內部常 `print()` 大量輸出，污染 wrapper 的 JSON 輸出。
**解法**：在 wrapper 端 `with contextlib.redirect_stdout(io.StringIO()):`

### 陷阱 4：adapter 內部重複 importlib 載入
每次 `route()` 呼叫都重新 import 一次 FT router 8119 行的檔案。
**影響**：首次慢，後續走 import cache 快。
**解法**：在 `__init__` 時載入一次並 cache。

## 驗證成功的關鍵指標

- ✅ 現有檔案 mtime + hash 全部未變
- ✅ 整合測試通過（既有功能仍可用）
- ✅ Wrapper 對外提供統一介面（envelope）
- ✅ 失敗時 graceful fallback（不 crash）
