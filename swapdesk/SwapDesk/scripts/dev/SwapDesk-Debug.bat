@echo off
setlocal EnableExtensions
title SwapDesk (debug)

REM Runs SwapDesk with a VISIBLE console so any Python error is shown live.
REM Use this if SwapDesk.bat silently does nothing. Lives in scripts\; the
REM app itself is one directory up, in the repo root.

REM Mirror SwapDesk.bat's venv location logic: deep folders get their venv
REM under LOCALAPPDATA, because a local .venv would exceed MAX_PATH.
pushd "%~dp0..\.."
set "APPDIR=%CD%\"
popd
cd /d "%APPDIR%"
set "VENV=%APPDIR%.venv"
if not "%APPDIR:~130,1%"=="" set "VENV=%LOCALAPPDATA%\SwapDesk\venv"

if not exist "%VENV%\Scripts\python.exe" (
    echo No virtual environment yet. Run SwapDesk.bat first to set it up.
    echo Looked in: %VENV%
    echo.
    pause
    exit /b 1
)

echo Starting SwapDesk in debug mode...
echo If it crashes, the full traceback appears below.
echo -------------------------------------------------
"%VENV%\Scripts\python.exe" "%APPDIR%app.py"
set rc=%ERRORLEVEL%
echo -------------------------------------------------
echo App exited with code %rc%.
echo.
REM Running from source, the crash handler writes crash.log beside
REM app.py. Shown here because a Tk callback or worker-thread crash
REM can kill the window without printing anything above.
if exist "%APPDIR%crash.log" (
  echo ---------- %APPDIR%crash.log ----------
  type "%APPDIR%crash.log"
  echo -------------------------------------------------
  echo Send the text above when reporting this.
)
pause
