# Parallel 模式線程限制（v1.3 已知問題 + v1.4 嘗試記錄）

> 在 dynamic-harness 嘗試用 ThreadPoolExecutor / asyncio.gather / multiprocessing 實作並行時遇到的問題。

## 問題描述

呼叫 `router.multi_route(parallel=True)` 兩次時，**第二次呼叫會卡住**或下游 router 的 print 會 leak 到主 stdout（v1.3）。
呼叫 `router.multi_route(parallel=True)` 時，**child 進程直接崩潰**（v1.4 multiprocessing 嘗試）。

## 根本原因

### 原因 1：downstream router 用 C-level print

FT/Coding/HermesTeam 4 個 router 內部都有 `print()` 呼叫：

```python
# CodingTaskRouter 內部
print(f"[Router] 分析請求：{task_text}...")
print(f"[Router] 識別意圖：{intent}")
```

Python 的 `print()` 透過 `sys.stdout` 這個 Python-level 物件。但**某些 C extension 或經過 C 層的 I/O 路徑會繞過 `sys.stdout`，直接寫到 fd 1**（stdout 檔案描述符）。

### 原因 2：thread 的 `sys.stdout` 是共享的

```python
# 主 thread
sys.stdout = StringIO()  # redirect

# 子 thread（透過 to_thread）
print("hello")  # 在子 thread 看到的 sys.stdout 還是主 thread 的 StringIO（共享）
```

但**子 thread 用 fork 啟動時會複製 fd** — 子 thread 的 fd 1 還是指向原本的 terminal，`print()` 經過 C 層會寫到那邊。

### 原因 3：asyncio.run 在 nested 場景失敗

```python
def _multi_route_parallel(self, ...):
    asyncio.run(self._async_impl())  # 第一次 OK
    # 第二次呼叫 → asyncio.run 試圖在已有 loop 的 thread 跑 → RuntimeError 或 hang
```

### 原因 4（v1.4 新發現）：macOS Python 3.9 multiprocessing + importlib 動態載入不相容

Child 進程 fork 後，`sys.modules` 雖然保留 parent 的 modules，但模組內部 `from xxx import yyy` 的解析行為可能不一致。我們的 adapter 是用 `importlib.util.spec_from_file_location("custom_name", path)` 載入的，child 進程的 module 物件內部狀態可能損壞。

**錯誤訊息**：
```
ModuleNotFoundError: No module named 'coding_task_router'
BrokenProcessPool: A process in the process pool was terminated abruptly
```

## 實驗記錄（v1.3）

| 模式 | 結果 |
|------|------|
| `asyncio.gather` + `asyncio.to_thread` | 第二次 hang |
| `ThreadPoolExecutor` + per-thread `redirect_stdout` | 第二次 hang + print leak |
| `subprocess` 跑獨立 process | 乾淨但太重（每次 ~500ms 啟動） |
| Sequential（預設） | 永遠 OK |

## 實驗記錄（v1.4）— multiprocessing 嘗試

**目標**：繞過 threading 的 stdout 共享問題，用 `multiprocessing.ProcessPoolExecutor` 跑獨立進程。

### 失敗 1：Local function 不可 pickle
```python
def _multi_route_parallel(self, ...):
    with ProcessPoolExecutor(...) as executor:
        executor.submit(self._worker, ...)  # AttributeError
        # Can't pickle local object 'UnifiedRouter._multi_route_parallel.<locals>._worker'
```
**解法**：把 `_worker` 提到 module-level。⚠️ 但這只是先決條件。

### 失敗 2：Child 進程找不到 importlib 載入的模組
```python
# Parent 已透過 importlib 載入 coding_task_router
spec = importlib.util.spec_from_file_location("coding_task_router", path)
module = importlib.util.module_from_spec(spec)
sys.modules["coding_task_router"] = module  # ← 註冊到 parent 的 sys.modules
spec.loader.exec_module(module)
# coding_task_router 已用，且呼叫成功

# 但 child 進程（fork 出來）找不到
# 在 child 進程中呼叫 _worker 時：
#   if "coding_task_router" in sys.modules:  # False！child 是新進程
#       mod = sys.modules["coding_task_router"]
#   else:
#       spec = importlib.util.spec_from_file_location(...)  # 重新載入
#       mod = importlib.util.module_from_spec(spec)
#       spec.loader.exec_module(mod)  # 失敗！
```
**錯誤訊息**：
```
ModuleNotFoundError: No module named 'coding_task_router'
BrokenProcessPool: A process in the process pool was terminated abruptly
```

**根本原因**：
- macOS Python 3.9 `fork` 模式下，child 進程的 `sys.modules` 包含 parent 的 modules，**但 modules 的 `__file__` 屬性可能指向原始路徑，且 modules 內部 `from xxx import yyy` 的解析行為不一致**
- 我們的 adapter 是用 `importlib.util.spec_from_file_location("custom_name", path)` 載入的，child 進程 fork 後雖然 `sys.modules` 還有這個名字，但 module 物件內部狀態可能損壞
- 重試 `importlib.util.spec_from_file_location` 重新執行 module → 模組頂層程式碼失敗（依賴鏈缺失）

**真正可行解法**：subprocess 模式（每個子任務跑獨立 Python process）— 但每次 ~500ms 啟動成本太高。

### v1.4 最終決策
**Parallel 模式 fallback 為 sequential**，但保留 API 介面讓未來替換（不破壞呼叫端）。

```python
def _multi_route_parallel(self, sub_tasks, parent_task, split_method):
    if self.verbose:
        print(f"[UnifiedRouter] parallel mode is sequential-fallback in v1.4 (multiprocessing disabled)")
    return self._multi_route_sequential(sub_tasks, parent_task, split_method)
```

**何時重訪**：
- 4 個 router 重構成不 print 後（用 logging 取代 print）
- Python 升級到 3.12+（threading 行為改善）
- 改用完全獨立的 `subprocess` 方案（每個子任務 spawn 一個 Python process）

## 現階段解法

**1. Sequential 為預設**（`parallel=False`）— 安全可靠
**2. 並行只對自定義 adapter 推薦** — 那些不 print 的 adapter
**3. 文件標註限制**（已在 SKILL.md）

## 推薦替代方案（給確實需要並行的場景）

### 方案 A：subprocess 模式

```python
import subprocess
import json

def _route_via_subprocess(sub_task: str) -> dict:
    result = subprocess.run(
        ["python3", str(DH / "unified_router.py"), "--task", sub_task, "--no-llm"],
        capture_output=True, text=True, timeout=30,
    )
    return json.loads(result.stdout)
```

優點：完全隔離、stdout 乾淨
缺點：每次 ~500ms 啟動

### 方案 B：multiprocessing 模式

```python
from multiprocessing import Pool

def route_parallel(sub_tasks):
    with Pool(len(sub_tasks)) as pool:
        results = pool.map(_route_sync, sub_tasks)
    return results
```

優點：隔離、可平行
缺點：要序列化整個 envelope

### 方案 C：完全自管 output 緩衝

```python
import sys
from io import StringIO
from contextlib import redirect_stdout, redirect_stderr

def _isolated_route(self, sub):
    saved = (sys.stdout, sys.stderr)
    sys.stdout, sys.stderr = StringIO(), StringIO()
    try:
        return self.route(sub)
    finally:
        sys.stdout, sys.stderr = saved
```

在主 thread（不是 thread pool）跑多個 isolated route，
用 `concurrent.futures.ThreadPoolExecutor` 但每個 task 內部自己 isolate。

**注意**：這只在 thread 是 daemon 或短暫時才安全，跨 thread 的 sys.stdout 共享問題仍在。

## 為何堅持不在 v1.3 / v1.4 解掉

按 Rule 2（最小代碼）— 預設 sequential 已能滿足 90% 場景。
Python 3.9 線程 + macOS multiprocessing 問題的解法都涉及複雜的隔離機制，**風險大於收益**。
如果未來用 Python 3.12+ 或 fork 出獨立 process，可重新評估。

## 何時重訪

- 任何用戶回報「parallel 模式在我的環境壞掉」時
- 升級 Python 3.12+ 後可重新測試
- 4 個 router 重構為不 print 後可重新評估
