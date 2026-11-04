"""Compile SwapDesk into a double-clickable binary.

    python3 build.py

PyInstaller can't cross-compile, so run this once per target OS. Output goes
to dist/<platform>/ (windows, macos, linux) so binaries collected from three
machines don't overwrite each other, the Linux and macOS ones are both just
called "SwapDesk".
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

DATA_FILES = ["VERSION"]


def platform_dir() -> str:
    """Folder name for this platform's build output.

    Each OS gets its own subfolder under dist/ (and build/) rather than every
    build dropping straight into dist/. PyInstaller can't cross-compile, so
    the three binaries are produced on three different machines and then
    collected in one place: with a flat dist/ the Linux and macOS binaries are
    both called "SwapDesk" and the second one to arrive silently replaces the
    first. Keeping them separated at the point of creation means that can't
    happen, and a local build for one OS no longer wipes out the artifacts of
    a previous build for another.
    """
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


DIST_DIR = ROOT / "dist" / platform_dir()
WORK_DIR = ROOT / "build" / platform_dir()

# Reverse-DNS bundle id. macOS uses it for preferences, keychain scoping and
# Gatekeeper; a bundle without one gets an identifier derived from the binary
# name, which collides with any other app that happens to be called SwapDesk.
BUNDLE_ID = "io.github.swapdesk.app"

# Per-format, and optional: none are in the repo. Drop one in next to
# build.py and it gets picked up.
ICONS = {"windows": "icon.ico", "macos": "icon.icns", "linux": "icon.png"}


# The Python this project is developed and exercised on. requirements.txt is
# hash-pinned with pip-compile --generate-hashes, which records hashes for
# EVERY wheel of each pinned version, so a newer interpreter installs the
# correct ABI wheels for itself and --require-hashes still verifies them.
# A newer Python is therefore not broken here, just not exercised: the note
# below exists so an unexpected version is visible in the build output, not
# to imply the build is wrong.
PINNED_PY = (3, 12)


def note_python_version() -> None:
    got = sys.version_info[:2]
    if got == PINNED_PY:
        return
    print(f"Note: building on Python {got[0]}.{got[1]}; this project is "
          f"developed on {PINNED_PY[0]}.{PINNED_PY[1]}.")
    print("      Dependencies are pinned per-version and install correctly on")
    print("      both. If the build misbehaves in a way you can't explain,")
    print(f"     trying {PINNED_PY[0]}.{PINNED_PY[1]} is one variable to rule out.")
    print()


def version_string() -> str:
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def version_tuple() -> tuple[int, int, int, int]:
    """VERSION as the 4-integer tuple Windows resources require.

    "1.2.3" or "1.2.3-alpha" -> (1, 2, 3, 0). The suffix is dropped because the Windows
    version resource has no field for it; it stays visible in the ProductVersion
    string below, which is free text.

    Each field is clamped to 65535: VS_FIXEDFILEINFO stores them as WORDs, so a
    larger number wraps rather than erroring, and the binary would then report a
    version unrelated to the one in VERSION. A leading "v" is tolerated because
    tags are written v0.1.20 and it is an easy thing to copy into the file; left
    unhandled it parses as major version 0.
    """
    head = version_string().split("-", 1)[0].split("+", 1)[0].lstrip("vV")
    parts = []
    for piece in head.split("."):
        try:
            parts.append(max(0, min(65535, int(piece))))
        except ValueError:
            parts.append(0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])  # type: ignore[return-value]


def write_windows_version_resource(path: Path) -> None:
    """Emit the version resource PyInstaller feeds to --version-file.

    Without this the .exe has a blank Details tab in Explorer's properties
    dialog, no company, no description, no version. That is one of the things
    SmartScreen weighs on an unsigned binary, and it is the first thing anyone
    checks when deciding whether a downloaded .exe is what it claims to be.

    The file is a Python expression that PyInstaller eval()s, so the version
    string is inserted with repr() rather than pasted in raw. VERSION is a
    repo-controlled file rather than untrusted input, but an apostrophe in it
    is enough to produce a resource that either fails to parse or parses into
    something other than what was written, and that is a poor way to find out.
    """
    v = version_tuple()
    ver = repr(version_string())
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={v},
    prodvers={v},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', 'SwapDesk'),
         StringStruct('FileDescription', 'SwapDesk non-custodial crypto swaps'),
         StringStruct('FileVersion', {ver}),
         StringStruct('InternalName', 'SwapDesk'),
         StringStruct('LegalCopyright', 'MIT Licensed'),
         StringStruct('OriginalFilename', 'SwapDesk.exe'),
         StringStruct('ProductName', 'SwapDesk'),
         StringStruct('ProductVersion', {ver})])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")


DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=SwapDesk
GenericName=Crypto Swap
Comment=Non-custodial cryptocurrency swaps
Exec=SwapDesk
Icon=swapdesk
Terminal=false
Categories=Office;Finance;
Keywords=crypto;swap;bitcoin;monero;exchange;
"""

# Docs that ship beside the binary. Someone who downloaded a compiled build
# and never saw the repo still needs the security note and the licence.
# Shipped beside the binary. README, CHANGELOG, LICENSE and SECURITY live at the repo
# root so GitHub picks them up and there is one copy of each; VERSION is the
# app's own. package() looks in the app folder first, then one level up.
PACKAGE_DOCS = ["README.md", "CHANGELOG.md", "LICENSE", "VERSION", "SECURITY.md"]

# Console-visible launcher per OS, for a window that vanishes on startup.
# Generated, not copied from scripts/: those bootstrap a venv and run
# app.py, which a binary download has no source tree for.
DEBUG_LAUNCHERS = {
    # A --windowed .exe has NO console, so it cannot print here no matter how
    # this script is launched: cmd.exe gets the exit code and nothing else.
    # Saying "runs it with the console attached" was simply false, and it left
    # a crashing build with no visible diagnosis at all. The exe's own crash
    # handler writes %APPDATA%\SwapDesk\crash.log, so show THAT instead, which
    # is the thing actually worth reading.
    "windows": ("SEE-ERRORS.bat", (
        "@echo off\r\n"
        "REM Runs SwapDesk.exe, then shows the crash log if one was written.\r\n"
        "REM A windowed build has no console of its own, so the log is the\r\n"
        "REM only place a startup error can appear.\r\n"
        "title SwapDesk (debug)\r\n"
        "cd /d \"%~dp0\"\r\n"
        "SwapDesk.exe\r\n"
        # Captured before anything else runs, for the same reason the POSIX
        # launchers capture $? immediately.
        "set rc=%ERRORLEVEL%\r\n"
        "echo.\r\n"
        "echo SwapDesk exited with code %rc%.\r\n"
        "echo.\r\n"
        # Beside the exe first: that is where the app now writes, and where
        # a user zipping the folder will actually capture it.
        "set \"FATAL=%~dp0crash-fatal.log\"\r\n"
        "if not exist \"%FATAL%\" set \"FATAL=%APPDATA%\\SwapDesk\\crash-fatal.log\"\r\n"
        "if exist \"%FATAL%\" (\r\n"
        "  echo ---------- %FATAL% ----------\r\n"
        "  type \"%FATAL%\"\r\n"
        "  echo ---------------------------------------------\r\n"
        ")\r\n"
        "set \"LOG=%~dp0crash.log\"\r\n"
        "if not exist \"%LOG%\" set \"LOG=%APPDATA%\\SwapDesk\\crash.log\"\r\n"
        "if not exist \"%LOG%\" set \"LOG=%TEMP%\\SwapDesk-crash.log\"\r\n"
        "if exist \"%LOG%\" (\r\n"
        "  echo ---------- %LOG% ----------\r\n"
        "  type \"%LOG%\"\r\n"
        "  echo ---------------------------------------------\r\n"
        "  echo Send the text above when reporting this.\r\n"
        ") else (\r\n"
        "  echo No crash log was written.\r\n"
        "  echo That usually means it failed before Python started\r\n"
        "  echo (missing runtime, antivirus, or a blocked temp folder)\r\n"
        "  echo rather than inside the app. Rebuild with:\r\n"
        "  echo     python build.py --console\r\n"
        "  echo and run the exe again to see the loader error.\r\n"
        ")\r\n"
        "pause\r\n"
    )),
    "macos": ("Run-Debug.command", (
        "#!/bin/bash\n"
        "# Double-click to run SwapDesk with output visible in Terminal.\n"
        "cd \"$(dirname \"$0\")\" || exit 1\n"
        "./SwapDesk\n"
        # Captured immediately. The blank echo below would otherwise reset $?
        # and the script would cheerfully report success on every crash.
        "rc=$?\n"
        "echo\n"
        "echo \"SwapDesk exited with code $rc.\"\n"
        "read -r -p \"Press Return to close.\" _\n"
    )),
    "linux": ("run-debug.sh", (
        "#!/bin/sh\n"
        "# Run SwapDesk from a terminal with output visible.\n"
        "cd \"$(dirname \"$0\")\" || exit 1\n"
        "./SwapDesk\n"
        # Captured immediately; the blank echo below resets $?.
        "rc=$?\n"
        "echo\n"
        "echo \"SwapDesk exited with code $rc.\"\n"
    )),
}


def fail(msg: str) -> None:
    print(f"\nBUILD ABORTED: {msg}", file=sys.stderr)
    sys.exit(1)


# Never imported by SwapDesk; see the exclusion note in build(). Kept as a
# named list so the reason for each is reviewable rather than buried in a
# command line.
EXCLUDED_MODULES = [
    # Packaging machinery. Present only because other packages inspect their
    # own metadata at import time; nothing here calls it.
    "setuptools",
    "pkg_resources",
    "distutils",
    # Process-based parallelism. This app is threaded (ThreadPoolExecutor);
    # concurrent.futures drags multiprocessing in for the process pool half
    # that is never used.
    "multiprocessing",
    # Developer/test tooling that has no place in a shipped binary.
    "unittest",
    "doctest",
    "pydoc",
    "pdb",
    "lib2to3",
    # Tk's own test suite, bundled by some Tk builds.
    "tkinter.test",
    "test",
]


def build(console: bool, onedir: bool) -> None:
    sep = ";" if sys.platform == "win32" else ":"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", "SwapDesk",
        "--onedir" if onedir else "--onefile",
        "--console" if console else "--windowed",
        # Strip asserts and set bytecode optimisation; the app has no runtime
        # asserts so nothing behavioural changes, the PYZ just gets smaller.
        "--optimize", "1",
        # --workpath moves too: the intermediate tree is also named after
        # the app, so a shared one collides across platforms.
        "--distpath", str(DIST_DIR),
        "--workpath", str(WORK_DIR),
        # Keeps the generated .spec (absolute paths, shipped by mistake in
        # 0.1.17) out of the source tree entirely.
        "--specpath", str(WORK_DIR),
    ]
    for data in DATA_FILES:
        cmd += ["--add-data", f"{ROOT / data}{sep}."]
    # cryptography's AES-GCM and scrypt live in a compiled backend. When it
    # isn't fully collected the app can't encrypt the config and falls back to
    # plaintext, so the master-password prompt never appears in the exe even
    # though it works from source. Collect it explicitly rather than trusting
    # the build machine's Python to be one the default hook handles cleanly.
    cmd += ["--collect-all", "cryptography"]

    # socks (PySocks) is reached only through urllib3's contrib importer at
    # the moment a SOCKS proxy is first used, so static analysis never sees
    # it and a frozen build silently ships without it. The app then fails
    # closed with "PySocks isn't installed", advice a user of a compiled
    # binary cannot act on. Named explicitly so Tor routing works in the
    # binary, not just from source.
    cmd += ["--hidden-import", "socks",
            "--hidden-import", "urllib3.contrib.socks"]

    # Modules PyInstaller pulls in transitively that this app never imports.
    # Excluding them is safe because nothing references them; if that
    # changes, the build fails at import rather than shipping a broken
    # binary.
    cmd += [arg for mod in EXCLUDED_MODULES for arg in ("--exclude-module", mod)]

    # UPX is explicitly off. It shrinks the binary but rewrites the
    # executable in a way heuristic antivirus flags, and an unsigned crypto
    # app that AV quarantines on download is worse off than a larger one
    # that runs. Being explicit also stops a UPX that happens to be on the
    # build machine's PATH from silently changing the output.
    cmd.append("--noupx")

    # Strip symbol tables from the bundled binaries. Not on Windows: PyInstaller
    # warns against it there and it can corrupt the PE.
    if sys.platform != "win32":
        cmd.append("--strip")

    WORK_DIR.mkdir(parents=True, exist_ok=True)

    # Per-OS packaging metadata. Each of these is what separates a binary that
    # looks like a real application from one that looks like something a build
    # script dropped on disk.
    icon = ROOT / ICONS[platform_dir()]
    if sys.platform == "win32":
        res = WORK_DIR / "version_info.txt"
        write_windows_version_resource(res)
        cmd += ["--version-file", str(res)]
        if icon.exists():
            cmd += ["--icon", str(icon)]
    elif sys.platform == "darwin":
        cmd += ["--osx-bundle-identifier", BUNDLE_ID]
        if icon.exists():
            cmd += ["--icon", str(icon)]
    elif icon.exists():
        # Linux has no icon slot in the ELF itself; the .desktop entry written
        # during packaging is what a desktop environment actually reads.
        pass

    cmd.append(str(ROOT / "app.py"))

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    print(" ".join(cmd))
    if subprocess.run(cmd, cwd=ROOT, check=False).returncode != 0:
        fail("PyInstaller failed")


def package() -> None:
    """Turn dist/<platform>/ from a bare binary into something shippable.

    Adds the docs a binary-only user would otherwise never see, a
    console-attached launcher for when the window won't open, and a
    CHECKSUMS.sha256 covering everything else in the folder. The checksums go
    last and hash the folder as it will actually be handed over, not the
    source tree it came from.
    """
    for rel in PACKAGE_DOCS:
        src = ROOT / rel
        if not src.exists():
            src = ROOT.parent / rel
        if not src.exists():
            fail(f"{rel} is missing; the package would ship without it.")
        shutil.copy2(src, DIST_DIR / Path(rel).name)

    name, body = DEBUG_LAUNCHERS[platform_dir()]
    launcher = DIST_DIR / name
    # newline="" so the CRLF written into the .bat above survives on a POSIX
    # machine; cmd.exe mis-parses a multi-line .bat with bare LF endings.
    launcher.write_text(body, encoding="utf-8", newline="")
    if not name.endswith(".bat"):
        launcher.chmod(0o755)

    if platform_dir() == "linux":
        # Desktop environments read this, not the ELF: there is no icon slot
        # in the binary itself the way there is in a PE or an .app bundle.
        entry = DIST_DIR / "swapdesk.desktop"
        entry.write_text(DESKTOP_ENTRY, encoding="utf-8")
        entry.chmod(0o644)

    icon = ROOT / ICONS[platform_dir()]
    if platform_dir() == "linux" and icon.exists():
        # Named to match the .desktop Icon= key, not the source filename.
        shutil.copy2(icon, DIST_DIR / "swapdesk.png")

    sums = DIST_DIR / "CHECKSUMS.sha256"
    lines = []
    for path in sorted(p for p in DIST_DIR.rglob("*") if p.is_file()):
        if path == sums:
            continue
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        lines.append(f"{h.hexdigest()}  {path.relative_to(DIST_DIR).as_posix()}\n")
    sums.write_text("".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--console", action="store_true",
                    help="keep a console window (for debugging a build)")
    ap.add_argument("--onedir", action="store_true",
                    help="build a folder (SwapDesk/ with the exe + libs "
                         "alongside) instead of a single --onefile exe. "
                         "--onefile re-extracts itself to a temp dir on "
                         "EVERY launch, which is usually the single "
                         "biggest contributor to slow startup; --onedir "
                         "has none of that overhead after the first run, "
                         "at the cost of shipping a folder instead of one "
                         "file to hand someone.")
    args = ap.parse_args()

    note_python_version()

    if shutil.which("pyinstaller") is None:
        try:
            import PyInstaller  # noqa: F401
        except ImportError:
            fail("PyInstaller not installed: pip install pyinstaller")

    build(console=args.console, onedir=args.onedir)

    package()

    out = DIST_DIR
    print(f"\nOK  SwapDesk {version_string()} packaged in {out}")
    for item in sorted(out.rglob("*")) if out.exists() else []:
        if item.is_file():
            print(f"    {item.relative_to(out).as_posix()}")


if __name__ == "__main__":
    main()
