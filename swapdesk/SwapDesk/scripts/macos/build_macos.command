#!/usr/bin/env bash
# build_macos.command - build SwapDesk on macOS.
#
# Usage:
#   ./build_macos.command
#
# Run from anywhere, or double-click in Finder: it cd's to the source root
# itself. PyInstaller does not cross-compile, so this produces a macOS build
# only, and one that runs on the same architecture as the machine that built
# it (an Apple Silicon build will not run on an Intel Mac).

set -euo pipefail
# Two levels up: this script lives in scripts/macos/, build.py is at the root.
cd "$(dirname "$0")/../.."

if [ ! -f build.py ]; then
    echo "ERROR: build.py not found in $(pwd)"
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found on PATH."
    echo "Install it from python.org, or with Homebrew:"
    echo "  brew install python@3.12 python-tk@3.12"
    exit 1
fi

# 3.12 is the version everything else in this project uses, including the
# launcher's own floor. Checked here rather
# than left to pip, which would otherwise fail mid-install on a resolver
# error that doesn't mention the interpreter.
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
    echo "ERROR: SwapDesk builds on Python 3.12 or newer."
    echo "Found: $(python3 --version 2>&1)"
    echo "  brew install python@3.12 python-tk@3.12"
    exit 1
fi

# python.org and Homebrew Pythons ship Tk; the system python3 at
# /usr/bin/python3 does not have a usable one. Failing here with a clear
# message beats a build that succeeds and then dies on first launch.
if ! python3 -c "import tkinter" >/dev/null 2>&1; then
    echo "ERROR: this python3 has no working tkinter."
    echo "  brew install python-tk@3.12"
    echo "or use the python.org installer, which bundles Tk."
    exit 1
fi

if [ ! -d .venv ]; then
    echo "Creating virtual environment in .venv ..."
    python3 -m venv .venv
fi

VPY=".venv/bin/python"

echo
echo "Installing dependencies from requirements.txt ..."
# --require-hashes: the lockfile is hash-pinned, and a build is exactly when a
# substituted dependency would do the most damage.
"$VPY" -m pip install --upgrade pip >/dev/null
"$VPY" -m pip install --require-hashes -r requirements.txt

echo
echo "Installing PyInstaller ..."
"$VPY" -m pip install --require-hashes -r ../requirements-build.txt

echo
echo "Running build.py $*"
"$VPY" build.py "$@"

# build.py writes to dist/macos/ so binaries built on different machines can
# be collected in one place without the Linux and macOS ones (both plain
# "SwapDesk") overwriting each other.
BUILT=dist/macos/SwapDesk

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
echo "Package:       $(pwd)/dist/macos"
echo
echo "Note: the build is unsigned and not notarised, so Gatekeeper will block"
echo "it on first launch. Users right-click then Open, or run:"
echo "  xattr -dr com.apple.quarantine dist/macos/SwapDesk"
