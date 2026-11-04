#!/usr/bin/env bash
# Double-click entry point for macOS Finder. Finder can only double-click
# executable .command files (not plain .sh), so this just hands off to the
# real launcher. Keep this window open on failure so errors are readable.
# The POSIX launcher is shared with Linux and sits beside this file.
# This file exists separately because Finder will only double-click an
# executable .command, not a .sh.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$HERE/swapdesk.sh"
status=$?
if [ $status -ne 0 ]; then
    read -rp "SwapDesk failed to start (see setup_log.txt). Press Enter to close..." _
fi
exit $status
