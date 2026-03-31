#!/usr/bin/env bash
set -euo pipefail

SCRIPT_NAME="claude-budget-daemon.py"
PLIST_NAME="com.phenly.budget-daemon.plist"
INSTALL_DIR="$HOME/scripts"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
LABEL="com.phenly.budget-daemon"

echo "==> llm-budget-daemon installer"
echo ""

# Python 3.9+
PYTHON=$(command -v python3 || true)
if [ -z "$PYTHON" ]; then
  echo "ERROR: python3 not found. Install Python 3.9+ and try again."
  exit 1
fi
PY_VERSION=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$("$PYTHON" -c "import sys; print(sys.version_info.major)")
PY_MINOR=$("$PYTHON" -c "import sys; print(sys.version_info.minor)")
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 9 ]; }; then
  echo "ERROR: Python 3.9+ required (found $PY_VERSION)."
  exit 1
fi
echo "  python3: $PY_VERSION ($PYTHON)"

# Dependencies
echo "  installing pexpect and pyte..."
"$PYTHON" -m pip install --quiet pexpect pyte

# Install script
mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_NAME" "$INSTALL_DIR/$SCRIPT_NAME"
echo "  installed: $INSTALL_DIR/$SCRIPT_NAME"

# Install plist
mkdir -p "$LAUNCH_AGENTS_DIR"
cp "$PLIST_NAME" "$LAUNCH_AGENTS_DIR/$PLIST_NAME"
echo "  installed: $LAUNCH_AGENTS_DIR/$PLIST_NAME"

# Load launchd agent (unload first if already loaded, to pick up any plist changes)
if launchctl list | grep -q "$LABEL" 2>/dev/null; then
  echo "  unloading existing daemon..."
  launchctl unload "$LAUNCH_AGENTS_DIR/$PLIST_NAME" 2>/dev/null || true
fi
launchctl load "$LAUNCH_AGENTS_DIR/$PLIST_NAME"
echo "  launchd agent loaded: $LABEL"

# Smoke test
echo ""
echo "==> Running smoke test (--once --debug)..."
"$PYTHON" "$INSTALL_DIR/$SCRIPT_NAME" --once --debug
echo ""
echo "==> Done. Budget files are written to ~/.claude/budget/"
echo "    Logs: ~/Library/Logs/budget-daemon.log"
