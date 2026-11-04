# Which file do I run?

Find your platform, pick the row that matches what you want to do. Nothing
else in here is a choice you have to make.

## Just run SwapDesk (no compiling)

| Platform | File |
|----------|------|
| Windows | `dev\SwapDesk.bat` - double-click it |
| macOS | `dev/SwapDesk.command` - double-click it |
| Linux | `dev/swapdesk.sh` - run it from a terminal |

First run takes a few minutes: it creates a private virtual environment next
to the source and installs the dependencies. After that it starts straight
away. You do **not** need to compile anything to use the app this way.

## Compile a standalone binary

One file per platform, and that file is the whole build:

| Platform | File | Produces |
|----------|------|----------|
| Windows | `windows\build_windows.bat` | `dist\windows\SwapDesk.exe` |
| macOS | `macos/build_macos.command` | `dist/macos/SwapDesk` |
| Linux | `linux/build_linux.sh` | `dist/linux/SwapDesk` |

## It won't start / the window vanishes

These are the same launchers with the console left open, so you can read the
error instead of watching the window disappear:

| Platform | File |
|----------|------|
| Windows | `dev\SwapDesk-Debug.bat` |
| macOS / Linux | `dev/swapdesk-debug.sh` |

## Why five files in `dev/`?

Because three platforms need different things, and each file does exactly one
job. There is no redundancy here, nothing in this folder duplicates anything
else:

| File | What it is |
|------|------------|
| `SwapDesk.bat` | Windows launcher |
| `SwapDesk-Debug.bat` | the same, console attached, for a startup failure |
| `swapdesk.sh` | Linux and macOS launcher (the real one) |
| `swapdesk-debug.sh` | the same, console attached |
| `SwapDesk.command` | macOS Finder shim; hands straight off to `swapdesk.sh` |

`SwapDesk.command` exists as a separate file only because macOS Finder will
double-click an executable `.command` but not a `.sh`. It is four lines long
and contains no logic of its own.

Everything in `dev/` is for running from source. If you only want the
application, use a compiled binary from the Releases page and ignore this
folder entirely.
