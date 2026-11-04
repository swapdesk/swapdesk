#!/usr/bin/env bash
# swapdesk.sh - double-click (macOS, via SwapDesk.command) or run from a
# terminal (Linux/macOS) to launch SwapDesk.
#
# Mirrors SwapDesk.bat's behavior: finds Python 3, creates a local venv,
# installs hash-pinned dependencies on first run only, verifies the app
# imports, then launches with no attached console window.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$HERE" || exit 1

VENV="$HERE/.venv"
LOG="$HERE/setup_log.txt"
SENTINEL="$VENV/.deps_ok"
REQ="$HERE/requirements.txt"

echo "SwapDesk setup log  $(date)" > "$LOG"

case "$(uname -s)" in
    Darwin*) OS=macos ;;
    Linux*)  OS=linux ;;
    *)       OS=other ;;
esac

fail() {
    echo
    echo "[X] $1"
    echo
    [ -n "${2:-}" ] && echo "$2"
    echo
    echo "See $LOG for details."
    read -rp "Press Enter to close..." _
    exit 1
}

# locate Python 3.12+
#
# 3.12 is the single supported version across this project: the binaries are
# compiled on it and the Windows launcher installs it. The check used to
# accept 3.9, which was not just looser
# but wrong: requirements.txt pins requests and pillow versions that declare
# >=3.10, so a 3.9 interpreter passed this gate and then failed at the
# --require-hashes install with "no matching distribution", which reads as a
# broken download rather than an unsupported interpreter.
PYCMD=""
for cand in python3.12 python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
        if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
            PYCMD="$cand"
            break
        fi
    fi
done

if [ -z "$PYCMD" ]; then
    if [ "$OS" = macos ]; then
        fail "Python 3.12+ not found." \
"Install it with:  brew install python@3.12 python-tk@3.12
(or download from https://www.python.org/downloads/macos/)
If you don't have Homebrew: https://brew.sh"
    elif [ "$OS" = linux ]; then
        fail "Python 3.12+ not found." \
"Install it with your package manager, e.g.:
  Debian/Ubuntu:  sudo apt install python3.12 python3.12-venv python3-tk
  Fedora:         sudo dnf install python3.12 python3-tkinter
  Arch:           sudo pacman -S python tk"
    else
        fail "Python 3.12+ not found. Install it from https://www.python.org/downloads/"
    fi
fi

echo "Using Python: $PYCMD ($($PYCMD --version 2>&1))" >> "$LOG"

# confirm Tkinter is available (separate OS package on Linux)
if ! "$PYCMD" -c "import tkinter" >> "$LOG" 2>&1; then
    if [ "$OS" = linux ]; then
        fail "Tkinter isn't installed for $PYCMD." \
"Debian/Ubuntu:  sudo apt install python3-tk
Fedora:         sudo dnf install python3-tkinter
Arch:           sudo pacman -S tk
Then run this script again."
    else
        fail "Tkinter isn't available for $PYCMD (unusual for this OS. See $LOG)."
    fi
fi

# create virtual environment if missing
VPY="$VENV/bin/python"
if [ ! -x "$VPY" ]; then
    echo "Creating virtual environment ..."
    "$PYCMD" -m venv "$VENV" >> "$LOG" 2>&1
fi
if [ ! -x "$VPY" ]; then
    fail "Could not create the virtual environment." \
"On Debian/Ubuntu this usually means the venv module is missing:
  sudo apt install python3-venv"
fi

# The import line the checks below run. cryptography is in it because the
# master-password encryption is optional at RUNTIME (secretbox degrades to
# plaintext without it) but not optional in a correct install: leaving it
# out means a venv missing it verifies clean and the app opens in plaintext
# with a dialog blaming the build.
VERIFY_IMPORTS="import customtkinter, requests, qrcode, PIL, cryptography, providers, config, app"

# What the sentinel records. A bare "ok" marks that SOME install happened,
# not that it matches what this version needs, so a .venv left behind by an
# older copy satisfied it forever and the install was skipped. That is how
# an in-place upgrade to 0.1.20 landed without cryptography. Hashing
# requirements.txt into it means a dependency change invalidates it.
req_stamp() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$REQ" | cut -d' ' -f1
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$REQ" | cut -d' ' -f1
    else
        # No hasher (unusual, but don't strand the launcher over it): fall
        # back to size+mtime, which still changes when the file does.
        wc -c < "$REQ" | tr -d ' '
    fi
}
STAMP="$(req_stamp)"

# install dependencies (first run, and after requirements.txt changes)
FRESH_INSTALL=0
if [ ! -f "$SENTINEL" ] || [ "$(cat "$SENTINEL" 2>/dev/null)" != "$STAMP" ]; then
    FRESH_INSTALL=1
    echo "Installing dependencies (one-time, please wait) ..."
    "$VPY" -m pip install --upgrade pip >> "$LOG" 2>&1
    "$VPY" -m pip install --require-hashes -r "$REQ" >> "$LOG" 2>&1

    # verify the app imports BEFORE detaching. Only needed right after a
    # fresh install: this is the one time a missing DLL/Tk issue can exist,
    # and it's cheap here since we're already waiting on pip. On every
    # later launch the venv is known-good, so skip this (it would otherwise
    # mean importing the whole app twice on every single run).
    echo "Verifying install ..."
    if ! "$VPY" -c "$VERIFY_IMPORTS" >> "$LOG" 2>&1; then
        # One repair pass, matching SwapDesk.bat. A venv carried over from
        # an older install can be present but incomplete; a plain install
        # leaves an already-satisfied-looking wheel in place, force-reinstall
        # does not.
        echo "Verification failed - repairing dependencies ..."
        echo "--- repair pass ---" >> "$LOG"
        rm -f "$SENTINEL"
        "$VPY" -m pip install --force-reinstall --require-hashes -r "$REQ" >> "$LOG" 2>&1
        if ! "$VPY" -c "$VERIFY_IMPORTS" >> "$LOG" 2>&1; then
            echo "----- last lines of setup_log.txt -----"
            tail -n 20 "$LOG"
            fail "The app failed to import." "Full error is in $LOG"
        fi
    fi
    printf '%s' "$STAMP" > "$SENTINEL"
fi

# launch, detached from this terminal
echo "Launching SwapDesk ..."
nohup "$VPY" "$HERE/app.py" >> "$LOG" 2>&1 &
LAUNCH_PID=$!
disown

# steady-state fast-fail check: a crash on import (e.g. a venv corrupted
# after the fact) exits almost immediately. Give it a beat, and only pay
# for a diagnostic re-run in that unlikely case, not on every launch.
sleep 0.6
if [ "$FRESH_INSTALL" = 0 ] && ! kill -0 "$LAUNCH_PID" 2>/dev/null; then
    echo "Launch failed - checking why ..."
    if ! "$VPY" -c "$VERIFY_IMPORTS" >> "$LOG" 2>&1; then
        echo "----- last lines of setup_log.txt -----"
        tail -n 20 "$LOG"
        fail "The app failed to start." "Full error is in $LOG"
    fi
fi
exit 0
