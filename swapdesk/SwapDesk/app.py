"""
SwapDesk: a 100% self-hosted, non-custodial crypto swap desktop app.

A native window (CustomTkinter). No browser, no Electron, no server you host.
It talks directly to Trocador, SideShift.ai, FixedFloat, 0x, ChangeNOW,
THORChain, Maya Protocol, Chainflip and StealthEX, and (for every provider
except 0x) asks them to settle the output coin straight to YOUR destination
address. SwapDesk never holds funds.

Run:  double-click scripts/dev/SwapDesk.bat   (or:  python app.py)
"""

from __future__ import annotations

# Crash logging is installed before this app's own modules, using only the
# stdlib. The imports below run code at module level (config resolves the
# per-user data directory as it loads), and a frozen build that raises there
# exits with no window, no console and no log. Installing the hook after
# those imports would miss exactly that case.
#
# The path logic below duplicates a little of config._app_dir() on purpose:
# importing config to decide where to log would defeat the point.
import os
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path


def _crash_log_path() -> Path:
    """Best-effort writable location for crash.log, stdlib only.

    Frozen builds try the folder holding the .exe first, so the log sits
    where a user will find it and gets included if they send the folder on.
    Falls back to the per-user data dir (matching config._app_dir()) when
    the install folder is read-only, e.g. under Program Files, then to the
    temp dir so a bad HOME still produces a log.
    """
    candidates = []
    try:
        if getattr(sys, "frozen", False):
            candidates.append(Path(sys.executable).resolve().parent)
            if sys.platform == "win32":
                base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
            elif sys.platform == "darwin":
                base = Path.home() / "Library" / "Application Support"
            else:
                base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
            candidates.append(Path(base) / "SwapDesk")
        else:
            candidates.append(Path(__file__).resolve().parent)
    except Exception:  # noqa: BLE001 - fall through to the temp dir
        pass
    candidates.append(Path(tempfile.gettempdir()))

    for target in candidates:
        try:
            target.mkdir(parents=True, exist_ok=True)
            # Prove it's actually writable rather than assuming: a folder can
            # exist and still reject writes (Program Files, read-only media).
            probe = target / ".swapdesk-write-test"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            return target / "crash.log"
        except Exception:  # noqa: BLE001 - try the next candidate
            continue
    return Path(tempfile.gettempdir()) / "SwapDesk-crash.log"


def _write_crash(exc_type, exc_value, exc_tb) -> None:
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    header = (f"\n{'=' * 70}\nSwapDesk crash {stamp}\n"
              f"frozen={bool(getattr(sys, 'frozen', False))} "
              f"platform={sys.platform} python={sys.version.split()[0]}\n"
              f"{'=' * 70}\n")
    try:
        with open(_crash_log_path(), "a", encoding="utf-8") as fh:
            fh.write(header + text)
    except Exception:  # noqa: BLE001 - logging must never mask the crash
        pass
    # Also print, so a console build or the run-debug scripts show it. Guarded
    # because a PyInstaller --windowed build has NO console: sys.stderr is
    # None there, and an unguarded .write() would raise inside the crash
    # handler itself, replacing the real traceback with an AttributeError.
    try:
        if sys.stderr is not None:
            sys.stderr.write(header + text)
    except Exception:  # noqa: BLE001 - same reason
        pass


def _excepthook(exc_type, exc_value, exc_tb):
    _write_crash(exc_type, exc_value, exc_tb)
    # Default hook writes to stderr, which is None in a windowed frozen
    # build; calling it there would raise on top of the crash we're
    # reporting. The log is already written by this point either way.
    if sys.stderr is not None:
        sys.__excepthook__(exc_type, exc_value, exc_tb)


sys.excepthook = _excepthook


def _enable_faulthandler() -> None:
    """Catch hard crashes, not just Python exceptions.

    A segfault / Windows access violation inside a C extension (Tk, the
    cryptography Rust bindings, cffi) kills the process outright: no
    exception is ever raised, so sys.excepthook, threading.excepthook and
    Tk's callback hook all see nothing and no traceback is written. That is
    exactly what "the window opened for a second then just closed" looks
    like, and it is the usual symptom of an extension module built for a
    different Python ABI than the interpreter running it.

    faulthandler installs OS-level signal handlers and dumps the Python
    stack of every thread at the moment of the fault, which is the only
    record that survives this class of failure. The file is kept open
    deliberately: faulthandler writes from a signal handler and cannot open
    one at fault time.
    """
    try:
        import faulthandler

        path = _crash_log_path().with_name("crash-fatal.log")
        fh = open(path, "a", buffering=1, encoding="utf-8")
        fh.write(f"\n--- session started {datetime.now(timezone.utc).isoformat(timespec='seconds')} ---\n")
        faulthandler.enable(file=fh, all_threads=True)
        globals()["_FAULT_FH"] = fh  # keep the handle alive for process life
    except Exception:  # noqa: BLE001 - diagnostics must never block startup
        pass


_enable_faulthandler()


def _thread_excepthook(args):
    """Background-thread crashes.

    sys.excepthook only ever sees the main thread. SwapDesk does its coin
    refresh, quote fetches and swap-status polling on worker threads, so the
    most likely place for a crash right after startup is a thread the main
    hook cannot see. SystemExit is skipped: it's how a worker is asked to
    stop, not a fault.
    """
    if args.exc_type is SystemExit:
        return
    _write_crash(args.exc_type, args.exc_value, args.exc_traceback)


try:
    import threading as _threading_for_hook

    _threading_for_hook.excepthook = _thread_excepthook
except Exception:  # noqa: BLE001 - hook is best-effort, never fatal
    pass

# Import-time failures don't go through sys.excepthook in every frozen
# bootstrap, so wrap the app's own imports explicitly too.
try:
    import contextlib
    import copy
    import threading
    import tkinter
    import tkinter.messagebox  # submodule: not bound by `import tkinter` alone
    from decimal import Decimal, InvalidOperation

    import customtkinter as ctk

    import config as cfg
    import providers as prov
except Exception:  # any import failure, logged then re-raised
    _write_crash(*sys.exc_info())
    raise

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Design system: one palette / type-scale / radius-scale used everywhere
# so every screen reads as one coherent product instead of a pile of
# ad-hoc widgets.

try:
    from theme import *  # palette, fonts, formatters, helpers
    from ui import DepositMixin, HistoryTabMixin, SettingsTabMixin, SwapTabMixin
except Exception:  # same reason as the import block above
    _write_crash(*sys.exc_info())
    raise

try:
    # cfg.RESOURCE_DIR, not __file__: in a frozen build the VERSION file is
    # unpacked into PyInstaller's temp dir, not sitting beside the binary.
    APP_VERSION = (cfg.RESOURCE_DIR / "VERSION").read_text(
        encoding="utf-8", errors="ignore")[:32].strip()
except Exception:  # noqa: BLE001 - version file read can fail in ways
    # (missing, encoding, permissions) that don't matter: any failure here
    # falls back to the same "dev" string, so there's nothing narrower to
    # gain by splitting the catch.
    APP_VERSION = "dev"

CURATED_COIN_NAMES: dict[str, str] = {t: v[0] for t, v in prov.COINS.items()}

# Skip the startup coin refresh when the cache is younger than this. Manual
# refreshes (adding a key, the Settings button) ignore it and always go out.
_COIN_REFRESH_MAX_AGE = 12 * 3600


class SwapDesk(ctk.CTk, SwapTabMixin, DepositMixin, SettingsTabMixin,
               HistoryTabMixin):
    def report_callback_exception(self, exc_type, exc_value, exc_tb):
        """Tk swallows callback exceptions by default.

        Every background result reaches the GUI through self.after(), and Tk
        routes an exception raised inside one of those callbacks here rather
        than to sys.excepthook. Without this override, the single most likely
        crash path in the app, a worker's result blowing up while being
        rendered, would leave no trace anywhere.
        """
        _write_crash(exc_type, exc_value, exc_tb)
        with contextlib.suppress(Exception):
            tkinter.messagebox.showerror(
                "SwapDesk - unexpected error",
                f"{exc_type.__name__}: {exc_value}\n\n"
                f"Details were written to:\n{_crash_log_path()}")

    def __init__(self):
        super().__init__()
        self.title(f"SwapDesk {APP_VERSION} · non-custodial swaps")
        # Clamped to the screen: 900x860 is taller than a 1366x768 laptop can
        # show, and the window opened with its lower half under the taskbar.
        _w, _h = self._fit_to_screen(self, 900, 860)
        self.geometry(f"{_w}x{_h}")
        # Form (~275) + button + status + the results panel's 120px floor
        # + header/tab chrome. Below this the quote rows get clipped, so this
        # is the floor, but it is itself clamped: a minsize taller than the
        # screen is worse than a slightly cramped layout, because the user
        # cannot shrink the window to reach what's off-screen.
        self.minsize(*self._fit_to_screen(self, 820, 700))
        self.configure(fg_color=BG)

        # Gate config access on the master password BEFORE any load: an
        # encrypted config.enc must be unlocked first, and a genuine first
        # run is offered encryption up front. Both are modal and block here.
        self._unlock_or_setup_config()
        try:
            self.cfg = cfg.load_config()
        except cfg.ConfigLocked as ex:
            # The config was readable but its contents wouldn't parse, so
            # config.py refused rather than handing back defaults that the
            # next save would write over the real file. Say so and stop; a
            # traceback here reads as a crash and invites the user to delete
            # the file, which is the one thing that loses the keys for good.
            self.withdraw()
            tkinter.messagebox.showerror("SwapDesk - config unreadable", str(ex))
            self.destroy()
            raise SystemExit(1) from ex
        priv = self.cfg.setdefault("privacy", {})
        cfg.prune_history(priv.get("auto_clear_history_days", 0))
        self._pending_proxy_error = None
        self.providers = {p.name: p for p in self._build_providers_safe(self.cfg)}
        self._dns_fallback_hosts: list[str] = []
        prov.set_dns_fallback_notifier(self._on_dns_fallback)
        self.quotes: dict[str, prov.Quote] = {}
        self.selected_provider: str | None = None
        # ticker -> display name, currently offered in the From/To dropdowns.
        # Starts as the curated fallback UNION the last-saved coin cache, so a
        # returning user's dropdowns are fully populated on the first frame
        # rather than sitting on the curated set until the startup background
        # refresh lands. on_refresh_coins() then re-expands it from the
        # providers themselves. Curated names win for curated tickers (same
        # precedence as refresh_all_coins); the cache only contributes the
        # extra tickers providers had listed last time.
        self.coin_names: dict[str, str] = dict(CURATED_COIN_NAMES)
        # Through registry_only(), same ceiling refresh_all_coins() applies.
        # coins.json is written by whatever build last ran, so a cache left
        # by a build that merged provider catalogues verbatim would put
        # those tickers back into the pickers on launch, before the first
        # refresh has a chance to overwrite the file.
        _cached_coins = prov.registry_only(cfg.load_coins_cache())
        for _t, _n in _cached_coins.items():
            self.coin_names.setdefault(_t, _n)
        self._coins_from_cache = bool(_cached_coins)

        self._build_header()
        self._history_dirty = False
        self.tabs = ctk.CTkTabview(
            self, fg_color=SURFACE, corner_radius=RADIUS_LG,
            segmented_button_fg_color=CARD,
            segmented_button_selected_color=ACCENT,
            segmented_button_selected_hover_color=ACCENT_HOVER,
            segmented_button_unselected_color=CARD,
            segmented_button_unselected_hover_color=CARD_HOVER,
            text_color=MUTED, text_color_disabled=FAINT,
            border_width=1, border_color=BORDER,
            command=self._on_tab_changed)
        self.tabs.pack(fill="both", expand=True, padx=18, pady=(0, 14))
        self.tab_swap = self.tabs.add("Swap")
        self.tab_settings = self.tabs.add("Settings")
        self.tab_history = self.tabs.add("History")
        self._build_swap_tab()
        self._build_settings_tab()
        self._build_history_tab()
        self._refresh_credential_banner()
        self._check_permission_warning()
        if self._pending_proxy_error:
            # Privacy fail-open guard. build_providers() fails CLOSED when a
            # proxy is requested but unusable; _build_providers_safe rebuilt
            # on DIRECT (clearnet) connections so the app can still open, but
            # firing the coin refresh now would leak a request over clearnet
            # before the user knows their Tor/SOCKS routing is off. Defer ALL
            # network activity until the user explicitly chooses to continue
            # on a direct connection or quit.
            self.after(150, self._handle_proxy_failopen)
        else:
            # Pull each provider's own coin list in the background so the
            # dropdowns fill in with everything providers actually support,
            # without delaying window startup. Safe to fail silently here,
            # the curated fallback set is already showing.
            self.on_refresh_coins(initial=True)

    def _build_providers_safe(self, config: dict) -> list:
        try:
            return prov.build_providers(config)
        except prov.ProviderError as e:
            # Proxy misconfigured (e.g. PySocks missing). Fall back to direct
            # connections rather than bricking the app, but build that from a
            # COPY: mutating the live config here left the Settings checkbox
            # rendering unticked, so the next unrelated save wrote the
            # downgrade to disk and the user's routing preference vanished
            # without ever being turned off deliberately.
            direct_cfg = copy.deepcopy(config)
            direct_cfg.setdefault("privacy", {})["proxy_mode"] = "off"
            self._pending_proxy_error = str(e)
            return prov.build_providers(direct_cfg)

    # Master-password gate
    def _unlock_or_setup_config(self):
        """Run before the first config load.

        - Encrypted config.enc present -> mandatory unlock (or quit).
        - Crypto available and the user hasn't opted out -> offer to set a
          master password. This covers a genuine first run AND an existing
          plaintext config, so keys already sitting unencrypted on disk still
          get offered protection instead of the offer being a one-shot that a
          returning user never sees again. "Not now" persists an opt-out (in
          the plaintext config) so it isn't asked on every launch.
        - Crypto unavailable on a genuine first run -> say so, don't skip in
          silence. A frozen build that failed to bundle cryptography would
          otherwise open straight into plaintext with no sign the password
          step was ever meant to run, which reads as "it never asked me".
        """
        if cfg.encryption_unexpectedly_missing():
            # The marker says this install had a master password; the
            # encrypted store is gone. Either it was deleted/moved, or this is
            # not the data directory this install was using. Opening straight
            # into a fresh plaintext config here is exactly the silent
            # downgrade the marker exists to catch, and the user would then
            # re-enter live API keys into it.
            self._warn_encryption_missing()
        if cfg.is_config_encrypted():
            self._setup_after_reset = False
            self._prompt_master_unlock()
            # Sequential, not nested: the unlock dialog has fully closed and
            # its grab released before this second dialog is built.
            if self._setup_after_reset and cfg.crypto_available():
                self._prompt_master_setup()
            return
        if not cfg.crypto_available():
            if not cfg.config_exists():
                self._warn_crypto_unavailable()
            return
        if not cfg.master_password_prompt_dismissed():
            self._prompt_master_setup()

    # Vertical room a desktop typically reserves for taskbar/dock/menu bar.
    # winfo_screenheight() reports the raw panel height, not the usable work
    # area, so a window sized to it lands partly under the taskbar with its
    # buttons out of reach.
    _CHROME_H = 80
    _CHROME_W = 40

    def _fit_to_screen(self, win, w: int, h: int) -> tuple[int, int]:
        """Clamp a requested size to what the screen can actually show.

        Fixed pixel sizes were chosen on a large display: 900x860 for the
        main window, 760 tall for the deposit window. A 1366x768 laptop,
        still one of the most common panels in use, has roughly 730px of
        usable height, so those windows opened taller than the screen with
        their lower controls unreachable. Clamping costs nothing on a big
        display and makes the app usable on a small one.
        """
        try:
            sw = max(320, win.winfo_screenwidth() - self._CHROME_W)
            sh = max(320, win.winfo_screenheight() - self._CHROME_H)
        except Exception:  # noqa: BLE001 - fall back to the requested size
            return w, h
        return min(w, sw), min(h, sh)

    def _center_on_screen(self, win, w: int, h: int):
        win.update_idletasks()
        w, h = self._fit_to_screen(win, w, h)
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")

    def _prompt_master_unlock(self):
        # Modal, blocking: the app cannot read its config until this resolves.
        # The root's window state is deliberately left alone here.
        # CustomTkinter runs its own withdraw/deiconify cycle on Windows to
        # repaint the title bar, and interleaving our own state changes with
        # it during __init__ leaves the window in an inconsistent state. The
        # modal grab below is what blocks interaction; hiding the root was
        # never doing that work.
        state = {"unlocked": False}
        win = ctk.CTkToplevel(self)
        win.title("Unlock SwapDesk")
        win.configure(fg_color=BG)
        self._center_on_screen(win, 460, 300)
        win.transient(self)
        win.grab_set()
        ctk.CTkLabel(win, text="\U0001f512  Enter your master password",
                     font=F(16, "bold"), text_color=TEXT).pack(pady=(24, 6), padx=20)
        ctk.CTkLabel(win, text="Your provider keys are encrypted. Enter the "
                     "master password you set to unlock them.",
                     text_color=MUTED, font=F(12), wraplength=400,
                     justify="left").pack(padx=20, pady=(0, 12))
        pw = entry(win, placeholder_text="master password", show="\u2022")
        pw.pack(fill="x", padx=20)
        pw.focus_set()
        err = ctk.CTkLabel(win, text="", text_color=BAD, font=F(12),
                           wraplength=400, justify="left")
        err.pack(padx=20, pady=(8, 0))

        def do_unlock(*_a):
            val = pw.get()
            if not val:
                return
            try:
                ok = cfg.unlock(val)
            except Exception as ex:  # noqa: BLE001 - malformed/foreign envelope
                err.configure(text=f"Could not read the encrypted config: {ex}")
                return
            if ok:
                state["unlocked"] = True
                win.destroy()
            else:
                err.configure(text="Incorrect master password. Try again.")
                pw.delete(0, "end")
                pw.focus_set()

        def do_quit():
            win.destroy()

        def do_forgot():
            # Deliberately a second, typed confirmation rather than a plain
            # yes/no: this discards the provider keys permanently and there
            # is no undo. The password is the key and is never stored, so
            # there is genuinely nothing to recover, only a clean start.
            confirm = ctk.CTkToplevel(win)
            confirm.title("Reset SwapDesk")
            confirm.configure(fg_color=BG)
            self._center_on_screen(confirm, 480, 380)
            confirm.transient(win)
            confirm.grab_set()
            ctk.CTkLabel(confirm, text="Start over without the password?",
                         font=F(16, "bold"), text_color=TEXT
                         ).pack(pady=(22, 8), padx=20)
            ctk.CTkLabel(
                confirm,
                text=("Your master password is the encryption key and is "
                      "never stored anywhere, so there is no way to recover "
                      "it or the keys it protects.\n\n"
                      "Continuing DELETES your saved provider API keys and "
                      "returns SwapDesk to a first run. You will re-enter "
                      "your keys in Settings.\n\n"
                      "Your swap history is NOT deleted."),
                text_color=MUTED, font=F(12), wraplength=420,
                justify="left").pack(padx=20, pady=(0, 12))
            ctk.CTkLabel(confirm, text="Type RESET to confirm:",
                         text_color=MUTED, font=F(12)).pack(padx=20)
            word = entry(confirm, placeholder_text="RESET")
            word.pack(fill="x", padx=20, pady=(6, 4))
            word.focus_set()
            werr = ctk.CTkLabel(confirm, text="", text_color=BAD, font=F(11))
            werr.pack(padx=20)

            def do_reset(*_a):
                if word.get().strip().upper() != "RESET":
                    werr.configure(text="Type RESET exactly to confirm.")
                    return
                cfg.reset_encrypted_config()
                confirm.destroy()
                # Treat this as a successful entry into the app: the config
                # is now unencrypted-and-empty, which is the same state a
                # genuine first run starts from.
                state["unlocked"] = True
                state["was_reset"] = True
                win.destroy()

            cbtns = ctk.CTkFrame(confirm, fg_color="transparent")
            cbtns.pack(fill="x", padx=20, pady=16)
            secondary_button(cbtns, "Cancel", confirm.destroy, height=40).pack(
                side="left", expand=True, fill="x", padx=(0, 4))
            primary_button(cbtns, "Delete keys and restart", do_reset,
                           height=40).pack(side="left", expand=True,
                                           fill="x", padx=(4, 0))
            word.bind("<Return>", do_reset)

        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=(18, 4))
        secondary_button(btns, "Quit", do_quit, height=40).pack(
            side="left", expand=True, fill="x", padx=(0, 4))
        primary_button(btns, "Unlock", do_unlock, height=40).pack(
            side="left", expand=True, fill="x", padx=(4, 0))
        # Low-emphasis on purpose: this is the destructive escape hatch, not
        # a peer of Unlock. Someone who has genuinely lost the password needs
        # it to exist; nobody should reach for it by accident.
        secondary_button(win, "Forgot password", do_forgot, height=32,
                         font=F(11)).pack(padx=20, pady=(0, 16))
        pw.bind("<Return>", do_unlock)
        win.protocol("WM_DELETE_WINDOW", do_quit)
        self.wait_window(win)
        if not state["unlocked"]:
            # User closed/quit without unlocking: there's nothing to run.
            self.destroy()
            raise SystemExit(0)
        if state.get("was_reset"):
            # Keys were discarded, so the config is plaintext and empty.
            # Offer encryption again rather than leaving it unprotected.
            #
            # Set as a flag for the caller rather than calling
            # _prompt_master_setup() here: that would nest a second modal
            # Toplevel inside this one's wait_window, and on Windows
            # CustomTkinter styles a new Toplevel's title bar via a ctypes
            # DwmSetWindowAttribute call scheduled with after(). Running
            # that against a parent being torn down kills the process with
            # no Python traceback.
            cfg.set_master_password_prompt_dismissed(False)
            self._setup_after_reset = True

    def _prompt_master_setup(self):
        # See _prompt_master_unlock: the root's window state is left alone.
        win = ctk.CTkToplevel(self)
        win.title("Protect your API keys")
        win.configure(fg_color=BG)
        self._center_on_screen(win, 500, 430)
        win.transient(self)
        win.grab_set()
        ctk.CTkLabel(win, text="\U0001f512  Set a master password",
                     font=F(17, "bold"), text_color=TEXT).pack(pady=(22, 6), padx=22)
        ctk.CTkLabel(win, text="SwapDesk can encrypt the provider API keys you "
                     "enter so they're unreadable on disk without this "
                     "password (recommended). You can also do this later in "
                     "Settings, or skip it for a headless/plaintext setup.",
                     text_color=MUTED, font=F(12), wraplength=440,
                     justify="left").pack(padx=22, pady=(0, 14))
        p1 = entry(win, placeholder_text="master password (min 8 characters)",
                   show="\u2022")
        p1.pack(fill="x", padx=22, pady=(0, 8))
        p1.focus_set()
        p2 = entry(win, placeholder_text="confirm master password", show="\u2022")
        p2.pack(fill="x", padx=22)
        ctk.CTkLabel(win, text="There is no recovery if you forget it: it isn't "
                     "stored anywhere. Forgetting it means re-entering your "
                     "provider keys, not losing funds (these are affiliate "
                     "keys, not wallet keys).",
                     text_color=FAINT, font=F(11), wraplength=440,
                     justify="left").pack(padx=22, pady=(10, 0))
        err = ctk.CTkLabel(win, text="", text_color=BAD, font=F(12),
                           wraplength=440, justify="left")
        err.pack(padx=22, pady=(6, 0))

        def do_set(*_a):
            a, b = p1.get(), p2.get()
            if len(a) < 8:
                err.configure(text="Use at least 8 characters.")
                return
            if a != b:
                err.configure(text="The two passwords don't match.")
                return
            try:
                cfg.set_master_password(a)
            except Exception as ex:  # noqa: BLE001
                err.configure(text=f"Could not enable encryption: {ex}")
                return
            win.destroy()

        def do_skip():
            # Persist the choice so a plaintext config isn't offered encryption
            # on every launch. The gate reads this back via
            # master_password_prompt_dismissed().
            try:
                cfg.set_master_password_prompt_dismissed()
            except cfg.ConfigLocked:
                # config.json exists but won't parse. Nothing can be recorded
                # in it; close the dialog so __init__ reaches its own
                # "config unreadable" stop instead of leaving a modal that
                # cannot be dismissed.
                pass
            win.destroy()

        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack(fill="x", padx=22, pady=18)
        secondary_button(btns, "Not now", do_skip, height=40).pack(
            side="left", expand=True, fill="x", padx=(0, 4))
        primary_button(btns, "Encrypt my keys", do_set, height=40).pack(
            side="left", expand=True, fill="x", padx=(4, 0))
        p2.bind("<Return>", do_set)
        win.protocol("WM_DELETE_WINDOW", do_skip)
        self.wait_window(win)

    def _warn_encryption_missing(self):
        """Modal shown when the encryption marker outlives config.enc.

        Deliberately not a silent banner: the two explanations are "you moved
        or restored a data folder" and "something removed your encrypted
        store", and the second one wants the user to stop before typing any
        credentials back in.
        """
        win = ctk.CTkToplevel(self)
        win.title("Encrypted config missing")
        self._center_on_screen(win, 520, 360)
        win.configure(fg_color=BG)
        win.transient(self)
        win.grab_set()
        ctk.CTkLabel(win, text="\u26a0  Your encrypted config is missing",
                     font=F(16, "bold"), text_color=BAD).pack(pady=(22, 8))
        ctk.CTkLabel(
            win,
            text=("This install previously had a master password, so your "
                  "provider keys were stored encrypted in config.enc. That "
                  "file is no longer there.\n\n"
                  "If you moved, restored or copied your data folder, that "
                  "explains it: point the app at the original folder to get "
                  "your keys back.\n\n"
                  "If you did not, something removed it. Do not re-enter your "
                  "API keys until you know which of the two happened. "
                  "Continuing will start from an empty, UNENCRYPTED config."),
            text_color=MUTED, font=F(12), wraplength=450,
            justify="left").pack(padx=24, pady=(0, 14))
        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(pady=(0, 18))

        def _quit_app():
            win.destroy()
            self.destroy()
            raise SystemExit(0)

        def _continue():
            # Clear the marker so this is asked once, not on every launch.
            cfg._set_encryption_marker(False)
            win.destroy()

        secondary_button(row, "Quit", _quit_app, height=40).pack(
            side="left", padx=6)
        primary_button(row, "Continue unencrypted", _continue,
                       height=40).pack(side="left", padx=6)
        win.protocol("WM_DELETE_WINDOW", _quit_app)
        self.wait_window(win)

    def _warn_crypto_unavailable(self):
        """First run, but the crypto backend didn't load so encryption can't be
        offered. Show it rather than starting in plaintext without a word: a
        broken build should look broken, not like the app chose to skip the
        password step."""
        # See _prompt_master_unlock: the root's window state is left alone.
        win = ctk.CTkToplevel(self)
        win.title("Encryption unavailable")
        win.configure(fg_color=BG)
        self._center_on_screen(win, 500, 320)
        win.transient(self)
        win.grab_set()
        ctk.CTkLabel(win, text="\U000026a0  Encryption unavailable",
                     font=F(17, "bold"), text_color=TEXT).pack(pady=(22, 6), padx=22)
        ctk.CTkLabel(win, text="SwapDesk couldn't load its encryption backend, so "
                     "it can't protect your API keys with a master password in "
                     "this build. Any keys you enter will be stored in plaintext.",
                     text_color=MUTED, font=F(12), wraplength=440,
                     justify="left").pack(padx=22, pady=(0, 10))
        ctk.CTkLabel(win, text="This almost always means the 'cryptography' "
                     "library wasn't bundled into the build. Rebuilding with "
                     "build.py on Python 3.12 usually fixes it.",
                     text_color=FAINT, font=F(11), wraplength=440,
                     justify="left").pack(padx=22, pady=(0, 4))
        primary_button(win, "Continue in plaintext", win.destroy, height=40).pack(
            padx=22, pady=18)
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        self.wait_window(win)

    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=18, pady=(18, 12))

        mark = ctk.CTkLabel(bar, text="⇄", width=38, height=38,
                             fg_color=ACCENT, text_color="#12130f",
                             corner_radius=RADIUS_SM, font=F(18, "bold"))
        mark.pack(side="left", padx=(0, 12))

        titles = ctk.CTkFrame(bar, fg_color="transparent")
        titles.pack(side="left")
        ctk.CTkLabel(titles, text="SwapDesk", font=F(24, "bold"),
                     text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(titles, text="non-custodial · self-hosted",
                     text_color=FAINT, font=F(12)).pack(anchor="w")

        self.cred_banner = pill(bar, "", fg=ACCENT_SOFT, fg_text=ACCENT)
        self.cred_banner.pack(side="right", ipadx=12, ipady=5)

        # Always-visible routing state. "Auto" only helps if the user can
        # tell which way it resolved, otherwise it is the silent-clearnet
        # failure with a friendlier config key. Shows what is actually in
        # effect, not what was requested.
        self.route_pill = pill(bar, "", fg=NEUTRAL_SOFT, fg_text=MUTED)
        self.route_pill.pack(side="right", ipadx=12, ipady=5, padx=(0, 8))

        # Everything below is built ONCE, as part of the header. It used to
        # sit inside _refresh_route_pill() -- indented one level too far --
        # which is called again after every Settings save: each save packed
        # another 1px divider (after the tabs, since pack appends) and
        # replaced self.proxy_banner/self.dns_banner with fresh, unpacked
        # widgets while the _*_banner_shown flags describing the old ones
        # stayed as they were. A privacy banner can only do its job if the
        # widget the flag refers to is the one on screen.
        ctk.CTkFrame(self, fg_color=BORDER, height=1).pack(
            fill="x", padx=18, pady=(0, 4))

        # Persistent, full-width privacy banner. Created hidden; shown by
        # _show_proxy_banner() whenever the app is running on a DIRECT
        # connection despite the user having requested proxy/Tor routing, so
        # a clearnet fallback can never go silently unnoticed.
        self.proxy_banner = ctk.CTkFrame(
            self, fg_color=BAD_SOFT, corner_radius=RADIUS_SM,
            border_width=1, border_color=BAD)
        self.proxy_banner_label = ctk.CTkLabel(
            self.proxy_banner, text="", text_color=BAD, font=F(12, "bold"),
            wraplength=820, justify="left")
        self.proxy_banner_label.pack(side="left", padx=14, pady=8,
                                     fill="x", expand=True)

        # Separate banner for DNS-over-HTTPS fallback notices: distinct
        # concern from the proxy/clearnet warning above (this one fires
        # per-hostname, can accumulate several lines, and isn't necessarily
        # bad news, just something the zero-trust-minded user asked to be
        # told about rather than have happen silently).
        self.dns_banner = ctk.CTkFrame(
            self, fg_color=ACCENT_SOFT, corner_radius=RADIUS_SM,
            border_width=1, border_color=ACCENT)
        self.dns_banner_label = ctk.CTkLabel(
            self.dns_banner, text="", text_color=ACCENT, font=F(12, "bold"),
            wraplength=820, justify="left")
        self.dns_banner_label.pack(side="left", padx=14, pady=8,
                                   fill="x", expand=True)

        self._refresh_route_pill()

    def _refresh_route_pill(self):
        """Update the header pill from the sessions that actually exist."""
        proxied = any(getattr(p, "session", None) is not None
                      and p.session.proxies
                      for p in getattr(self, "providers", {}).values())
        if proxied:
            self.route_pill.configure(text="Proxy on", fg_color=GOOD_SOFT,
                                      text_color=GOOD)
        else:
            self.route_pill.configure(text="Direct", fg_color=NEUTRAL_SOFT,
                                      text_color=MUTED)

    def _show_proxy_banner(self, msg: str):
        self.proxy_banner_label.configure(text=msg)
        if not getattr(self, "_proxy_banner_shown", False):
            # Sit directly under the header, above the tabs.
            self.proxy_banner.pack(fill="x", padx=18, pady=(2, 2),
                                   before=self.tabs)
            self._proxy_banner_shown = True

    def _hide_proxy_banner(self):
        if getattr(self, "_proxy_banner_shown", False):
            self.proxy_banner.pack_forget()
            self._proxy_banner_shown = False

    def _post(self, fn, *args):
        """Schedule `fn(*args)` on the GUI thread from a worker thread.

        Every network call in this app runs on a background thread and hands
        its result back with .after(). If the user closes the window while
        one is still in flight (a coin refresh at startup, a quote fetch, a
        status poll, or a swap creation), the interpreter is already tearing
        Tk down by the time the thread returns, and a bare self.after()
        raises RuntimeError or TclError from inside the worker. Nothing
        catches it there, so it lands on stderr as a traceback: alarming
        after a swap, and on Windows it is the last thing written before the
        console vanishes. There is no useful work left to do at that point,
        so drop the callback quietly.
        """
        try:
            if not self.winfo_exists():
                return
            self.after(0, fn, *args)
        except (RuntimeError, tkinter.TclError):
            pass

    def _on_dns_fallback(self, hostname: str, doh_errors: list):
        """Registered with providers.set_dns_fallback_notifier(). Called
        from a background request thread (quote fetching runs on a
        ThreadPoolExecutor), so this only ever schedules GUI work via
        .after() rather than touching Tk widgets directly."""
        self._post(self._show_dns_fallback_banner, hostname)

    def _show_dns_fallback_banner(self, hostname: str):
        hosts = getattr(self, "_dns_fallback_hosts", None)
        if hosts is None:
            hosts = []
            self._dns_fallback_hosts = hosts
        if hostname not in hosts:
            hosts.append(hostname)
        self.dns_banner_label.configure(
            text="SECURE DNS: DNS-over-HTTPS couldn't resolve " +
                 ", ".join(hosts) + ", so SwapDesk used your network's own "
                 "DNS for " + ("this host" if len(hosts) == 1 else "these hosts") +
                 " instead. Turn on Secure DNS mode in Settings > DNS to "
                 "stop instead of falling back.")
        if not getattr(self, "_dns_banner_shown", False):
            self.dns_banner.pack(fill="x", padx=18, pady=(2, 2),
                                 before=self.tabs)
            self._dns_banner_shown = True

    def _hide_dns_fallback_banner(self):
        if getattr(self, "_dns_banner_shown", False):
            self.dns_banner.pack_forget()
            self._dns_banner_shown = False
        self._dns_fallback_hosts = []

    def _handle_proxy_failopen(self):
        msg = (self._pending_proxy_error
               or "Proxy/Tor routing was requested but could not be enabled.")
        self._show_proxy_banner(
            "PRIVACY WARNING: running on a DIRECT (clearnet) connection that "
            "exposes your real IP to swap providers. " + msg)

        win = ctk.CTkToplevel(self)
        win.title("Proxy could not be enabled")
        self._center_on_screen(win, 470, 340)
        win.configure(fg_color=BG)
        win.transient(self)
        win.grab_set()
        ctk.CTkLabel(win, text="⚠  Proxy/Tor routing is OFF",
                     font=F(16, "bold"), text_color=BAD).pack(pady=(20, 8), padx=18)
        ctk.CTkLabel(
            win,
            text=(msg + "\n\nYou asked SwapDesk to route its network traffic "
                  "through a proxy/Tor, but that isn't available right now. "
                  "Continuing will use a DIRECT connection that reveals your "
                  "real IP to the swap providers.\n\nNothing has contacted the "
                  "network yet."),
            text_color=TEXT, font=F(12), wraplength=420,
            justify="left").pack(padx=18, pady=(0, 14))
        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack(fill="x", padx=18, pady=(0, 18))

        def quit_app():
            win.destroy()
            self.destroy()

        def continue_direct():
            win.destroy()
            # User explicitly accepted a clearnet connection, only now is it
            # safe to make the deferred first network call.
            self.on_refresh_coins(initial=True)

        secondary_button(btns, "Quit", quit_app, height=40
                         ).pack(side="left", expand=True, fill="x", padx=(0, 4))
        primary_button(btns, "Continue on direct connection", continue_direct,
                       height=40).pack(side="left", expand=True, fill="x", padx=(4, 0))

    def _active_providers(self) -> list:
        """Providers that are switched on AND actually usable.

        `configured()` is the second half deliberately: a provider toggled
        on but missing its API key can't quote or create a swap, so listing
        the coins it would route only produces pairs that fail at the
        quote step.
        """
        enabled_cfg = self.cfg.get("enabled_providers", {})
        return [p for p in self.providers.values()
                if prov.provider_enabled(enabled_cfg, p.name)
                and (p.configured() or p.can_quote_without_config)]

    def _coin_list(self) -> list[str]:
        """Tickers the currently usable providers can actually route.

        Strictly the union of what each usable provider routes, intersected
        with the curated registry. One provider on means that provider's
        coins and nothing else; every provider on with every key wired means
        the whole registry. Nothing widens it past the registry, and nothing
        widens it past what the enabled set can actually do.

        Returns an EMPTY list when no provider is usable. It used to fall
        back to the full registry here, on the reasoning that empty pickers
        look broken. They don't: the results panel already renders a
        "No providers enabled" card explaining the state, whereas offering
        fifteen coins that nothing can quote invited the user to pick a pair
        and get a failure back as the only feedback. An unusable provider
        (switched on, key missing) hit the same fallback and so widened the
        pickers instead of contributing its own coins.
        """
        routable: set[str] = set()
        for p in self._active_providers():
            routable |= set(self._provider_tickers(p))
        # Intersect with the known-names map so the label lookup always
        # resolves, and so a provider's odd internal ticker can't leak in.
        return sorted(t for t in routable if t in self.coin_names)

    @staticmethod
    def _provider_tickers(p) -> set:
        """Tickers one provider routes, across the differing shapes each
        implementation uses to record that."""
        for attr in ("networks", "currencies", "codes", "assets", "symbols"):
            table = getattr(p, attr, None)
            if isinstance(table, dict) and table:
                return set(table)
        tokens = getattr(prov, "EVM_TOKENS", None)
        if isinstance(p, prov.ZeroExDEX) and tokens:
            return set(tokens)
        return set()

    def on_refresh_coins(self, initial: bool = False):
        """Ask every configured provider for its own coin list (background
        thread) and merge the results into the From/To dropdowns. Safe to
        call repeatedly: e.g. after adding an API key in Settings, since a
        provider needing auth (ChangeNOW, FixedFloat) can't be queried for
        its coin list before that."""
        if initial and self._coins_from_cache:
            age = cfg.coins_cache_age_seconds()
            if age is not None and age < _COIN_REFRESH_MAX_AGE:
                # Recent cache is already filling the dropdowns; don't spend a
                # launch-time network round trip re-fetching a list that
                # rarely changes. An older cache or a manual refresh still goes.
                return
        if hasattr(self, "coins_status") and not initial:
            self.coins_status.configure(text="Refreshing coin list…")

        def work():
            # ENABLED providers only. Passing every constructed provider
            # would contact services the user never switched on, firing
            # outbound requests at launch while the UI says nothing is
            # enabled. That is a privacy leak, not just extra traffic.
            enabled_cfg = self.cfg.get("enabled_providers", {})
            active = [p for p in self.providers.values()
                      if prov.provider_enabled(enabled_cfg, p.name)]
            if not active:
                # Nothing to ask. Post an empty result so the status label is
                # still updated rather than left saying "Refreshing...".
                self._post(self._apply_coin_refresh, dict(self.coin_names), [])
                return
            merged, results = prov.refresh_all_coins(active)
            self._post(self._apply_coin_refresh, merged, results)

        threading.Thread(target=work, daemon=True).start()

    def _apply_coin_refresh(self, merged: dict, results: list):
        self.coin_names = merged
        # Persist for the next cold start so the dropdowns come up fully
        # populated before the first background refresh returns.
        cfg.save_coins_cache(merged)
        coins = self._coin_list()
        prev_from, prev_to = self.from_coin.get(), self.to_coin.get()
        self.from_coin.configure(values=coins, names=self.coin_names)
        self.to_coin.configure(values=coins, names=self.coin_names)
        # Keep the current selection when the refreshed list still offers it,
        # otherwise fall back to something that IS offered rather than
        # leaving a stale ticker selected. coins can legitimately be empty
        # (no usable provider), so nothing is indexed without checking it
        # first; CoinPicker renders its own placeholder in that case.
        if coins:
            self.from_coin.set(prev_from if prev_from in coins else
                               ("BTC" if "BTC" in coins else coins[0]))
            self.to_coin.set(prev_to if prev_to in coins and prev_to != prev_from
                             else next((c for c in coins
                                        if c != self.from_coin.get()), coins[0]))
        # A refresh can move the selection out from under a set of quotes
        # that were fetched for the OLD pair (disabling the only provider
        # routing DASH drops it, and the picker falls back to BTC). The
        # quotes dict and selected_provider survive that, so on_create_swap
        # would go on to check the amount against the previous pair's
        # min/max while sending the NEW pair to the provider, and the
        # results panel would keep showing rows for a pair that is no
        # longer selected. Drop them whenever the pair actually moved.
        if (self.from_coin.get(), self.to_coin.get()) != (prev_from, prev_to):
            self._reset_quotes()

        failed = [r.provider for r in results if not r.ok]
        text = (f"{len(coins)} of {len(CURATED_COIN_NAMES)} coins unlocked"
                if coins else
                "No coins available: enable a provider (and add its key)")
        if failed:
            text += f" · {', '.join(failed)} kept its existing list (not reachable/configured)"
        skipped_total = sum(r.skipped_ambiguous for r in results if r.ok)
        if skipped_total:
            text += (f" · {skipped_total} multi-network coin(s) left out "
                     f"(no single safe network to default to)")
        if hasattr(self, "coins_status"):
            self.coins_status.configure(text=text)

    def _on_tab_changed(self):
        if self.tabs.get() == "History" and self._history_dirty:
            self._refresh_history(force=True)

    def _parse_amount(self) -> Decimal:
        raw = self.amount.get().strip()
        if not raw:
            raise ValueError("Enter an amount to send.")
        try:
            d = Decimal(raw)
        except InvalidOperation:
            raise ValueError("Amount isn't a valid number.")
        if not d.is_finite() or d <= 0:
            raise ValueError("Amount must be a positive, finite number.")
        # Same bound providers.constants._dec applies to provider-supplied
        # numbers. Decimal("1e999999999") is finite and positive, and would
        # freeze the GUI thread the moment anything rendered it.
        if not (-30 <= d.adjusted() <= 30):
            raise ValueError("Amount is out of range.")
        return d

    def _set_status(self, text: str, color: str = MUTED):
        self.status_line.configure(text=text, text_color=color)



def main():
    app = SwapDesk()
    app.mainloop()


if __name__ == "__main__":
    main()
