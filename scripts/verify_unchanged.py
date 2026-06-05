"""
verify_unchanged.py — 驗證包裝層沒改動到下游原始碼

用途：定期檢查 dynamic-harness 涉及的 4 套原始 router 仍然 hash 未變。
可以在 CI 跑，也可以手動檢查。

使用方式：
    python3 verify_unchanged.py
    python3 verify_unchanged.py --strict  # 任何 hash 不符立即 exit 1
"""
import argparse
import hashlib
import sys
from pathlib import Path


# v1.0.0 鎖定的 hash 與 mtime（從 2026-06-05 測試時取得）
LOCKED_HASHES = {
    "task_router_v2.py": {
        "path": "~/.hermes/skills/productivity/ft-team-agent/scripts/task_router_v2.py",
        "expected_hash": "b941f90eb0628ef6",
        "domain": "ft",
    },
    "task_router.py (hermes-team)": {
        "path": "~/.hermes/skills/productivity/hermes-team-agent/scripts/task_router.py",
        "expected_hash": "e698a31b84625780",
        "domain": "hermes",
    },
    "stock_router.py": {
        "path": "~/.hermes/skills/productivity/stock-team-agent/scripts/task_router/stock_router.py",
        "expected_hash": "c051c6c442d9ae2e",
        "domain": "stock",
    },
    "task_router.py (coding)": {
        "path": "~/.hermes/skills/autonomous-ai-agents/coding-team-agent/scripts/train/task_router.py",
        "expected_hash": "c8941a76346d8828",
        "domain": "coding",
    },
}


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()[:16]


def verify(strict: bool = False):
    print("🔍 驗證 4 套下游 router 完整性\n")
    
    all_ok = True
    for name, info in LOCKED_HASHES.items():
        path = Path(info["path"]).expanduser()
        
        if not path.exists():
            print(f"⚠ {name:35s} 檔案不存在: {path}")
            all_ok = False
            continue
        
        actual = hash_file(path)
        expected = info["expected_hash"]
        mtime = path.stat().st_mtime
        
        if actual == expected:
            print(f"✓ {name:35s} hash={actual} (unchanged)")
        else:
            print(f"✗ {name:35s} hash={actual} EXPECTED={expected} (MODIFIED!)")
            all_ok = False
    
    print()
    if all_ok:
        print("✅ 全部 4 套 router 仍然 hash 未變")
        return 0
    else:
        print("❌ 部分 router 已被改動")
        if strict:
            return 1
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="任何不符立即 exit 1")
    args = parser.parse_args()
    sys.exit(verify(strict=args.strict))


if __name__ == "__main__":
    main()
