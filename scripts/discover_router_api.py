"""
discover_router_api.py — 探測任一 Python router 模組的公開介面

用途：在包裝一個未知 router 之前，先探測它的 public methods 與 signatures。
避免「猜 API 名字」這個最常見的坑（例：FT 沒有 route_task()，是 analyze()）。

使用方式：
    python3 discover_router_api.py <path_to_router.py>

範例：
    python3 discover_router_api.py \
      ~/.hermes/skills/productivity/ft-team-agent/scripts/task_router_v2.py
"""
import importlib.util
import inspect
import sys
from pathlib import Path


def discover(path: str):
    p = Path(path).expanduser()
    if not p.exists():
        print(f"❌ 檔案不存在: {p}")
        sys.exit(1)
    
    print(f"🔍 探測 {p.name}\n")
    
    # 動態載入
    spec = importlib.util.spec_from_file_location("discovered_module", str(p))
    if spec is None or spec.loader is None:
        print(f"❌ 無法建立 spec")
        sys.exit(1)
    module = importlib.util.module_from_spec(spec)
    
    try:
        # 抑制載入時的 print 污染
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            spec.loader.exec_module(module)
    except Exception as e:
        print(f"⚠️ 載入時錯誤（可能缺少依賴）: {type(e).__name__}: {e}")
        print("   繼續探測...\n")
    
    # 找出所有看起來像 router 的 class
    router_classes = []
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if not isinstance(attr, type):
            continue
        if attr.__name__ in ("object", "dict", "list", "tuple", "Dataclass"):
            continue
        # 啟發式：class 名包含 Router、Handler、Engine、Orchestrator
        if any(kw in attr.__name__ for kw in ["Router", "Handler", "Engine", "Orchestrator", "Dispatcher"]):
            router_classes.append(attr)
    
    if not router_classes:
        print("⚠️ 沒找到看起來像 router 的 class")
        print("   列出所有 top-level class：")
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, type) and not attr.__name__.startswith("_"):
                print(f"     - {attr.__name__}")
        return
    
    for cls in router_classes:
        print(f"📦 Class: {cls.__name__}")
        print(f"   模組: {cls.__module__}")
        print(f"   Bases: {[b.__name__ for b in cls.__bases__]}")
        print()
        
        # 列出所有 public methods
        methods = []
        for name in dir(cls):
            if name.startswith("_") and not name.startswith("__init__"):
                continue
            attr = getattr(cls, name, None)
            if not callable(attr):
                continue
            try:
                sig = str(inspect.signature(attr))
            except (ValueError, TypeError):
                sig = "(?)"
            methods.append((name, sig, attr))
        
        # 分類
        constructors = [m for m in methods if m[0] == "__init__"]
        public_methods = [m for m in methods if not m[0].startswith("_")]
        
        if constructors:
            print(f"   構造方法:")
            for name, sig, _ in constructors:
                print(f"     {name}{sig}")
        
        if public_methods:
            print(f"   Public methods ({len(public_methods)}):")
            for name, sig, _ in public_methods:
                # 標記看起來像「入口」的方法
                marker = "★" if any(kw in name.lower() for kw in ["route", "dispatch", "analyze", "process", "run", "classify", "detect"]) else " "
                print(f"     {marker} {name}{sig}")
        
        # 列出 dataclass 欄位
        if hasattr(cls, "__dataclass_fields__"):
            print(f"   Dataclass 欄位:")
            for field_name, field in cls.__dataclass_fields__.items():
                print(f"     {field_name}: {field.type}")
        
        print()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    path = sys.argv[1]
    discover(path)


if __name__ == "__main__":
    main()
