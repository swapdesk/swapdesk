@echo off
setlocal EnableExtensions
title SwapDesk launcher

REM  SwapDesk.bat - double-click to run. Lives in scripts\; the app itself
REM  (app.py, requirements.txt) is one directory up, in the repo root.
REM  Writes setup_log.txt and stays open (pause) on any failure.

set "PYVER=3.12.7"
set "PYMD5=b51e0889be50c55fbdd809f4ad587120"
pushd "%~dp0..\.."
set "APPDIR=%CD%\"
popd
cd /d "%APPDIR%"

REM pick a venv location that fits inside MAX_PATH
REM Windows caps most file APIs at 260 characters, and a virtualenv buries
REM its payload deep: ".venv\Lib\site-packages\customtkinter\windows\widgets\
REM appearance_mode\appearance_mode_tracker.py" is ~96 characters on its own,
REM and Pillow's _imaging .pyd is similar. So if this folder already sits
REM more than ~130 characters deep (a Downloads path inside a roaming profile
REM will), a local .venv cannot physically be created or loaded, pip appears
REM to succeed and then imports die with "DLL load failed ... The filename or
REM extension is too long". Put the venv under LOCALAPPDATA instead, which is
REM short and stable. The app itself still runs from this folder; only the
REM dependencies move, and they are generic, so one venv can serve any copy.
set "VENV=%APPDIR%.venv"
if not "%APPDIR:~130,1%"=="" set "VENV=%LOCALAPPDATA%\SwapDesk\venv"
if /i not "%VENV%"=="%APPDIR%.venv" md "%LOCALAPPDATA%\SwapDesk" 2>nul

set "VPY=%VENV%\Scripts\python.exe"
set "VPYW=%VENV%\Scripts\pythonw.exe"
set "SENTINEL=%VENV%\.deps_ok"
set "LOG=%APPDIR%setup_log.txt"
set "PYINSTALLER=%TEMP%\python-%PYVER%-amd64.exe"

echo SwapDesk setup log  %DATE% %TIME%> "%LOG%"
echo App folder: %APPDIR%>> "%LOG%"
echo Venv folder: %VENV%>> "%LOG%"

REM locate a REAL Python 3.12+
REM The 'py' launcher is never the Microsoft Store stub, so prefer it, and
REM ask it for 3.12 by name before falling back to whatever -3 resolves to.
REM The version test matters: these probes used to accept any 3.x, but
REM requirements.txt pins wheels that declare >=3.10, so an older
REM interpreter got past this and then failed at the --require-hashes
REM install with "no matching distribution", which reads like a broken
REM download rather than an unsupported Python. 3.12 is the version the
REM compiled binaries and %PYVER% below both use.
set "PYCMD="
py -3.12 -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)" >nul 2>&1 && set "PYCMD=py -3.12"
if not defined PYCMD py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)" >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD call :try_python
if not defined PYCMD goto :getpython

:havepython
echo Using Python: %PYCMD%>> "%LOG%"
%PYCMD% --version >> "%LOG%" 2>&1

REM create virtual environment if missing
if not exist "%VPY%" (
    echo Creating virtual environment ...
    %PYCMD% -m venv "%VENV%" >> "%LOG%" 2>&1
)
if not exist "%VPY%" goto :fail_venv

REM install dependencies (first run, and after requirements.txt changes)
REM The sentinel records a hash of requirements.txt, not a bare "ok". A
REM plain marker says only that SOME install happened: the venv under
REM LOCALAPPDATA is shared between installs, so one left by an older copy
REM satisfied it forever and the install below was skipped, which is how an
REM in-place upgrade could land without a newly added dependency. If the
REM hash can't be computed, STAMP is empty and we install rather than skip.
REM Hash via a temp file rather than a for /f capture: the path has to
REM reach Python without nesting double quotes inside the for's own
REM quoting, which cmd.exe mangles. Passing it through the environment
REM lets the -c body use single quotes and stay one plain argument.
set "REQFILE=%APPDIR%requirements.txt"
set "STAMPFILE=%VENV%\.reqstamp"
"%VPY%" -c "import hashlib,os;print(hashlib.sha256(open(os.environ['REQFILE'],'rb').read()).hexdigest())" > "%STAMPFILE%" 2>nul
set "STAMP="
if exist "%STAMPFILE%" set /p STAMP=<"%STAMPFILE%"
set "SEEN="
if exist "%SENTINEL%" set /p SEEN=<"%SENTINEL%"
if defined STAMP if "%SEEN%"=="%STAMP%" goto :launch

echo Installing dependencies ^(one-time, please wait^) ...
"%VPY%" -m pip install --upgrade pip >> "%LOG%" 2>&1
"%VPY%" -m pip install --require-hashes -r "%APPDIR%requirements.txt" >> "%LOG%" 2>&1

REM verify the app imports BEFORE detaching. Only needed right after a
REM fresh install/repair: this is the one point a missing DLL or a stale
REM shared venv would otherwise fail later, in the GUI, where the
REM traceback is invisible. Once the sentinel exists this is skipped, so
REM a normal launch doesn't import the whole app twice every time.
echo Verifying install ...
"%VPY%" -c "import customtkinter, requests, qrcode, PIL, cryptography, providers, config, app" >> "%LOG%" 2>&1
if not errorlevel 1 goto :verified

REM One repair pass. The venv under LOCALAPPDATA is shared between installs,
REM so a stale one can satisfy the sentinel while missing what THIS version
REM needs. Reinstall against the current requirements.txt and re-verify once.
echo Verification failed - repairing dependencies ...
echo --- repair pass --->> "%LOG%"
del "%SENTINEL%" >nul 2>&1
"%VPY%" -m pip install --upgrade pip >> "%LOG%" 2>&1
"%VPY%" -m pip install --force-reinstall --require-hashes -r "%APPDIR%requirements.txt" >> "%LOG%" 2>&1
"%VPY%" -c "import customtkinter, requests, qrcode, PIL, cryptography, providers, config, app" >> "%LOG%" 2>&1
if errorlevel 1 goto :fail_import

:verified
(echo %STAMP%)> "%SENTINEL%"

:launch
REM launch with no console window
echo Launching SwapDesk ...
start "" "%VPYW%" "%APPDIR%app.py"
goto :done

REM ================= subroutine: test 'python' =======================
:try_python
REM Store stub prints nothing and fails this, so PYCMD stays empty. The
REM printed value doubles as the version gate: anything below 3.12 prints
REM nothing and leaves PYCMD unset, which routes to the installer below.
for /f "delims=" %%V in ('python -c "import sys; print(42 if sys.version_info >= (3,12) else 0)" 2^>nul') do if "%%V"=="42" set "PYCMD=python"
goto :eof

REM ================= download + install Python =======================
:getpython
echo.
echo Python was not found ^(or only the Microsoft Store stub is present^).
echo Downloading Python %PYVER% ... one-time setup.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-amd64.exe' -OutFile '%PYINSTALLER%' } catch { Write-Host $_; exit 1 }" >> "%LOG%" 2>&1
if not exist "%PYINSTALLER%" goto :fail_download

REM verify the download before running it
REM python.org only publishes MD5 for this file (see the release page),
REM this catches a corrupted/tampered download, it is NOT a substitute for
REM a real signature check. For stronger assurance, verify the .sigstore
REM bundle python.org publishes alongside each installer instead.
REM ---- SECURITY gate: verify the installer is validly code-signed by the
REM Python Software Foundation. Unlike a pinned hash this needs no constant
REM and a MITM-tampered installer cannot forge it; the MD5 check below is
REM kept only as a fast corruption check, NOT relied on for security
REM (MD5 is collision-broken).
echo Verifying installer signature ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { $s = Get-AuthenticodeSignature -LiteralPath '%PYINSTALLER%'; if ($s.Status -ne 'Valid') { Write-Host ('signature status: ' + $s.Status); exit 2 }; if ($s.SignerCertificate.Subject -notmatch 'Python Software Foundation') { Write-Host ('unexpected signer: ' + $s.SignerCertificate.Subject); exit 3 }; exit 0 } catch { Write-Host $_; exit 4 }" >> "%LOG%" 2>&1
if errorlevel 1 goto :fail_sig
echo Verifying installer checksum ...
for /f "skip=1 tokens=1" %%H in ('certutil -hashfile "%PYINSTALLER%" MD5 ^| findstr /r "^[0-9a-fA-F]*$"') do set "PYGOTMD5=%%H"
if /i not "%PYGOTMD5%"=="%PYMD5%" goto :fail_hash
echo Installing Python %PYVER% ...
"%PYINSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_tcltk=1 Include_launcher=1 >> "%LOG%" 2>&1
del "%PYINSTALLER%" >nul 2>&1
py -3 -c "import sys" >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD goto :fail_nopy
goto :havepython

REM ========================= handlers ================================
:fail_download
echo.
echo [X] Could not download Python ^(a firewall/AV/EDR may be blocking it^).
echo     Install from https://www.python.org/downloads/windows/ and TICK
echo     "Add python.exe to PATH", then run SwapDesk.bat again.
goto :halt

:fail_sig
echo.
echo [X] The downloaded Python installer is not validly signed by the Python
echo     Software Foundation ^(signature invalid, missing, or wrong signer^).
echo     This can mean a tampered or man-in-the-middled download. Deleting it
echo     - do NOT run it manually. Get Python directly from
echo     https://www.python.org/downloads/windows/ instead.
del "%PYINSTALLER%" >nul 2>&1
goto :halt

:fail_hash
echo.
echo [X] Downloaded installer's checksum didn't match the expected value.
echo     Deleting it. Do NOT run it manually. This can mean a bad/partial
echo     download, or a tampered file. Delete "%PYINSTALLER%" if it remains,
echo     re-run this script, and if it fails again get Python directly from
echo     https://www.python.org/downloads/windows/ instead.
del "%PYINSTALLER%" >nul 2>&1
goto :halt

:fail_nopy
echo.
echo [X] Python still not detected. Open a NEW window and run:  py --version
goto :halt

:fail_venv
echo.
echo [X] Could not create the virtual environment. See setup_log.txt.
echo     If this folder is inside OneDrive, or very deep in your user profile,
echo     move it to C:\SwapDesk and retry. Tried to build it at:
echo     %VENV%
goto :halt

:fail_import
echo.
echo [X] The app failed to import. Exact error is at the bottom of setup_log.txt
echo     ^(most common: Python installed without Tcl/Tk^).
echo.
echo     If the error mentions "filename or extension is too long" or a DLL
echo     that failed to load, this folder is too deep for Windows. Move it to
echo     C:\SwapDesk and run again. Current folder:
echo     %APPDIR%
echo.
echo ----- last lines of setup_log.txt -----
powershell -NoProfile -Command "Get-Content '%LOG%' -Tail 15" 2>nul
goto :halt

:halt
echo.
echo (Window kept open so you can read the message above.)
pause
goto :done

:done
endlocal
exit /b
