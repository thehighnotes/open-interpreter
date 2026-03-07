#!/bin/bash
# Open Interpreter — Hub Tools Bootstrap
#
# One-command install for hub or node:
#   Hub:  curl -sL <url>/bootstrap.sh | bash
#   Node: curl -sL <url>/bootstrap.sh | bash -s node
#
# Environment variables:
#   OI_REPO  — git URL to clone (default: thehighnotes fork)
#   OI_DIR   — install directory (default: ~/projects/open-interpreter)

set -e

REPO_URL="${OI_REPO:-https://github.com/thehighnotes/open-interpreter.git}"
INSTALL_DIR="${OI_DIR:-$HOME/projects/open-interpreter}"
MODE="${1:-hub}"

echo ""
echo "  Open Interpreter + Hub Tools"
echo ""

# ── Prerequisites ──

missing=""
command -v git >/dev/null 2>&1 || missing="$missing git"
command -v python3 >/dev/null 2>&1 || missing="$missing python3"

if [ -n "$missing" ]; then
    echo "  Missing required tools:$missing"
    echo "  Install them first, then re-run this script."
    exit 1
fi

# Find pip
if command -v pip3 >/dev/null 2>&1; then
    PIP=pip3
elif python3 -m pip --version >/dev/null 2>&1; then
    PIP="python3 -m pip"
else
    echo "  pip not found. Install python3-pip first."
    exit 1
fi

# ── Clone or update ──

if [ -d "$INSTALL_DIR/.git" ]; then
    echo "  Updating existing installation at $INSTALL_DIR..."
    git -C "$INSTALL_DIR" fetch origin --quiet
    git -C "$INSTALL_DIR" pull --ff-only origin main 2>/dev/null || {
        echo "  Pull failed — you may have local changes. Run manually:"
        echo "    cd $INSTALL_DIR && git pull"
        exit 1
    }
    echo "  Updated."
else
    echo "  Cloning to $INSTALL_DIR..."
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --quiet "$REPO_URL" "$INSTALL_DIR"
    echo "  Cloned."
fi

# ── Install OI ──

echo "  Installing Open Interpreter (editable mode)..."
cd "$INSTALL_DIR"
$PIP install -e . --quiet 2>&1 | tail -1
echo "  Installed."

# ── Run setup wizard ──

echo ""
if [ "$MODE" = "node" ]; then
    python3 tools/hub/install.py --node
else
    python3 tools/hub/install.py
fi
