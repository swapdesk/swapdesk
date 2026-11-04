#!/usr/bin/env bash
# Runs SwapDesk attached to this terminal so any Python error is visible
# live. Use this if swapdesk.sh / SwapDesk.command silently does nothing.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$HERE" || exit 1

VPY="$HERE/.venv/bin/python"
if [ ! -x "$VPY" ]; then
    echo "No virtual environment yet. Run ./scripts/dev/swapdesk.sh first to set it up."
    read -rp "Press Enter to close..." _
    exit 1
fi

echo "Starting SwapDesk in debug mode..."
echo "If it crashes, the full traceback appears below."
echo "-------------------------------------------------"
"$VPY" "$HERE/app.py"
code=$?
echo "-------------------------------------------------"
echo "App exited with code $code."
read -rp "Press Enter to close..." _
