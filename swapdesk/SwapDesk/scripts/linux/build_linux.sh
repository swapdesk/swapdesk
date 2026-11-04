#!/usr/bin/env bash
# build_linux.sh - build SwapDesk on Linux.
#
# Usage:
#   ./build_linux.sh
#
# Run from anywhere: it cd's to the source root (two levels up) itself.
# PyInstaller does not cross-compile: this produces a Linux binary only,
# runnable on other Linux machines of the same CPU architecture (and
# generally a similar-or-newer glibc) as the machine that built it.

set -euo pipefail
# Two levels up: this script lives in scripts/linux/, build.py is at the root.
cd "$(dirname "$0")/../.."

if [ ! -f build.py ]; then
    echo "ERROR: build.py not found in $(pwd)"
    echo "Run this script from the SwapDesk source folder."
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found on PATH."
    echo "Install it via your distro's package manager, e.g.:"
    echo "  Debian/Ubuntu: sudo apt install python3 python3-venv python3-tk"
    echo "  Fedora:        sudo dnf install python3 python3-tkinter"
    echo "  Arch:          sudo pacman -S python tk"
    exit 1
fi

# 3.12 is the version everything else in this project uses, including the
# launcher's own floor. Checked here rather
# than left to pip, which would otherwise fail mid-install on a resolver
# error that doesn't mention the interpreter.
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
    echo "ERROR: SwapDesk builds on Python 3.12 or newer."
    echo "Found: $(python3 --version 2>&1)"
    echo "  Debian/Ubuntu: sudo apt install python3.12 python3.12-venv python3-tk"
    exit 1
fi

if ! python3 -c "import tkinter" >/dev/null 2>&1; then
    echo "ERROR: python3's tkinter module is missing."
    echo "CustomTkinter (SwapDesk's GUI) needs it, and it's usually a separate"
    echo "OS package, not something pip installs. Install it with:"
    echo "  Debian/Ubuntu: sudo apt install python3-tk"
    echo "  Fedora:        sudo dnf install python3-tkinter"
    echo "  Arch:          sudo pacman -S tk"
    exit 1
fi

echo "Using interpreter: $(command -v python3)"
python3 --version

if [ ! -d .venv ]; then
    echo "Creating virtual environment in .venv ..."
    python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo
echo "Installing dependencies from requirements.txt ..."
python -m pip install --upgrade pip
# --require-hashes, matching build_macos.command and build_windows.bat. This
# is the path that produces a shipped binary, so it is the one place the
# pinning is worth the most, and it was the only build script without it.
python -m pip install --require-hashes -r requirements.txt

echo
echo "Installing PyInstaller ..."
python -m pip install --require-hashes -r ../requirements-build.txt

echo
echo "Running build.py $*"
python build.py "$@"

# build.py writes to dist/<platform>/ so binaries built on different
# machines can be collected in one place without the Linux and macOS ones
# (both plain "SwapDesk") overwriting each other.
BUILT=dist/linux/SwapDesk

if [ ! -f "$BUILT" ]; then
    echo
    echo "WARNING: build.py reported success but $BUILT was not found."
    exit 1
fi

# The binary is NOT copied to the source root. dist/<platform>/ is the one
# canonical build output: a second copy goes stale the moment you rebuild
# without noticing, and "which one did I actually test" is a bug worth
# designing out.
echo
echo "Build finished."
echo "Compiled file: $BUILT"
echo "Package:       $(pwd)/dist/linux"
echo
echo "Note: this only runs on Linux machines with a compatible glibc"
echo "(same distro/version or newer is safest) and the same CPU"
echo "architecture as this machine. It's unsigned, so nothing like"
echo "Gatekeeper applies, but sending it over a channel that strips the"
echo "executable bit (some zip tools, some chat apps) means the recipient"
echo "will need to run 'chmod +x SwapDesk' themselves before it'll launch."
