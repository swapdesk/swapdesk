@echo off
REM build_windows.bat - build SwapDesk.exe on Windows.
REM
REM Usage:
REM   build_windows.bat
REM
REM Run from anywhere: it cd's to the source root itself. PyInstaller does not
REM cross-compile, so this produces a Windows .exe only.
setlocal
title SwapDesk build (Windows)

REM Two levels up: this script lives in scripts\windows\, build.py is at the root.
pushd "%~dp0..\.."

if not exist build.py (
    echo ERROR: build.py not found in %CD%
    popd
    pause
    exit /b 1
)

REM 3.12 is the version everything else in this project uses, including
REM the launcher's own floor. Checked
REM here rather than left to pip, which would otherwise fail mid-install
REM on a resolver error that never mentions the interpreter.
set "PY="
py -3.12 -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)" >nul 2>&1 && set "PY=py -3.12"
if not defined PY py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY (
    echo ERROR: SwapDesk builds on Python 3.12 or newer, and none was found.
    echo Install Python 3.12 from python.org and tick "Add python.exe to PATH".
    popd
    pause
    exit /b 1
)

REM Tk is what CustomTkinter draws on, and the python.org installer lets it
REM be unticked. Without it PyInstaller has nothing to collect and the .exe
REM dies on first launch instead of failing here, where the cause is still
REM obvious. build_linux.sh and build_macos.command both check this; this
REM script was the one that did not.
%PY% -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo ERROR: this Python has no working tkinter.
    echo Re-run the Python installer, choose Modify, and tick
    echo "tcl/tk and IDLE".
    popd
    pause
    exit /b 1
)

if not exist .venv (
    echo Creating virtual environment in .venv ...
    %PY% -m venv .venv || goto :failed
)

set "VPY=.venv\Scripts\python.exe"

echo.
echo Installing dependencies from requirements.txt ...
REM --require-hashes: the lockfile is hash-pinned, and a build is exactly when
REM a substituted dependency would do the most damage.
"%VPY%" -m pip install --upgrade pip >nul
"%VPY%" -m pip install --require-hashes -r requirements.txt || goto :failed

echo.
echo Installing PyInstaller ...
"%VPY%" -m pip install --require-hashes -r ..\requirements-build.txt || goto :failed

echo.
echo Running build.py %*
"%VPY%" build.py %* || goto :failed

REM build.py writes to dist\windows\ so binaries built on different machines
REM can be collected in one place without overwriting each other.
if not exist "dist\windows\SwapDesk.exe" (
    echo.
    echo WARNING: build.py reported success but dist\windows\SwapDesk.exe was not found.
    goto :failed
)

REM The .exe is NOT copied to the source root. dist\windows\ is the one
REM canonical build output: a second copy goes stale the moment you
REM rebuild without noticing, and "which one did I actually test" is a
REM bug worth designing out.
echo.
echo Build finished.
echo Compiled file: %CD%\dist\windows\SwapDesk.exe
echo Package:       %CD%\dist\windows
echo.
echo Note: the .exe is unsigned, so SmartScreen will warn on first run.
echo Users click "More info" then "Run anyway", or verify CHECKSUMS.sha256.
popd
pause
exit /b 0

:failed
echo.
echo BUILD FAILED. See the messages above.
popd
pause
exit /b 1
