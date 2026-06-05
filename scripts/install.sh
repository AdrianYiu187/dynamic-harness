#!/usr/bin/env bash
# Dynamic Harness 一鍵安裝腳本
#
# 功能：
# 1. 建立 ~/.local/bin/dh symlink → skill 的 bin/dh
# 2. 提示把 ~/.local/bin 加入 PATH
# 3. 跑一次 smoke test（顯示 --version + --ui-list）
#
# 用法：bash scripts/install.sh
# 反安裝：bash scripts/install.sh --uninstall

set -e

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BIN_DIR="${HOME}/.local/bin"
LINK_PATH="$BIN_DIR/dh"

# 反安裝
if [[ "$1" == "--uninstall" ]]; then
    if [[ -L "$LINK_PATH" ]]; then
        rm "$LINK_PATH"
        echo "✓ Removed $LINK_PATH"
    else
        echo "✗ $LINK_PATH not found (nothing to uninstall)"
    fi
    exit 0
fi

# 1. 確保 ~/.local/bin 存在
mkdir -p "$BIN_DIR"

# 2. 建立 symlink
if [[ -e "$LINK_PATH" && ! -L "$LINK_PATH" ]]; then
    echo "✗ $LINK_PATH already exists and is NOT a symlink. Aborting."
    echo "  Remove it manually or run: bash scripts/install.sh --uninstall"
    exit 1
fi
ln -sf "$SKILL_DIR/bin/dh" "$LINK_PATH"
echo "✓ Symlink created: $LINK_PATH → $SKILL_DIR/bin/dh"

# 3. 檢查 PATH
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo ""
    echo "⚠ $BIN_DIR is NOT in your PATH"
    echo "  Add to ~/.zshrc or ~/.bashrc:"
    echo ""
    echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo ""
fi

# 4. 可選：安裝 man page
if [[ -f "$SKILL_DIR/bin/dh.1" ]] && command -v mandoc >/dev/null 2>&1; then
    MANDIR="${HOME}/.local/share/man/man1"
    mkdir -p "$MANDIR"
    cp "$SKILL_DIR/bin/dh.1" "$MANDIR/dh.1"
    echo "✓ Man page installed: $MANDIR/dh.1"
    if [[ ":MANPATH:" != *":$(dirname $MANDIR):"* ]]; then
        echo "  To view: man dh  (after adding ~/.local/share/man to MANPATH)"
        echo "  Or:     mandoc -man $SKILL_DIR/bin/dh.1 | less"
    fi
fi

# 4. Smoke test
echo ""
echo "=== Smoke Test ==="
"$LINK_PATH" --version
echo ""
"$LINK_PATH" --ui-list 2>&1 | head -3

echo ""
echo "✓ Install complete. Try: dh --task \"hello world\""
