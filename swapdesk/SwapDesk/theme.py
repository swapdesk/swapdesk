"""Visual theme and presentation helpers for SwapDesk.

Palette, radii, the per-OS font probe, the small widget-builder functions
(card / pill / buttons / entry / checkbox), the display-only formatters
(fmt / chunk_addr / now_iso / full_str) and the swap-status colour map. Kept
apart from app.py so the UI vocabulary lives in one place and the main module
stays about behaviour rather than styling."""
from __future__ import annotations

import contextlib
import sys
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import customtkinter as ctk

import providers as prov

BG = "#0b0d12"            # window background
SURFACE = "#12151c"       # tab body / scroll areas
CARD = "#171b23"          # card background
CARD_HOVER = "#1f2530"
BORDER = "#242a37"        # default 1px card border
FIELD_BG = "#0e1117"      # entry / dropdown background (inset look)
FIELD_BORDER = "#2a3141"
ACCENT = "#f7931a"        # bitcoin orange, brand accent
ACCENT_HOVER = "#dd830e"
ACCENT_SOFT = "#3a2a14"   # accent tint for badges/pills
GOOD = "#34d399"
GOOD_SOFT = "#123226"
BAD = "#ff6161"
BAD_SOFT = "#301418"
NEUTRAL_SOFT = "#232838"  # secondary-button surface
TEXT = "#f3f4f6"
MUTED = "#9aa2b1"
FAINT = "#5c6272"
RADIUS_LG = 18
RADIUS_MD = 14
RADIUS_SM = 10

def _pick_family() -> str:
    """Segoe UI only exists on Windows. Pick a native-feeling default per
    OS, but verify it's actually installed (tkinter silently substitutes an
    ugly fallback otherwise) before trusting it, and fall back to Tk's own
    default UI font (which always exists) if not."""
    if sys.platform == "win32":
        preferred = "Segoe UI"
    elif sys.platform == "darwin":
        preferred = "SF Pro Text"
    else:
        preferred = "Noto Sans"  # common on GNOME/most modern Linux desktops
    candidates = [preferred, "Helvetica Neue", "Ubuntu", "Cantarell",
                  "DejaVu Sans", "Helvetica", "Arial"]
    with contextlib.suppress(Exception):
        import tkinter.font as tkfont
        # Needs a Tk root to exist before font.families() works; CTk hasn't
        # made one yet at import time, so make a throwaway hidden one.
        _probe = ctk.CTk()
        _probe.withdraw()
        available = set(tkfont.families())
        _probe.destroy()
        for name in candidates:
            if name in available:
                return name
    return "TkDefaultFont"  # always present; safe universal fallback
FAMILY = _pick_family()

def F(size: int, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=FAMILY, size=size, weight=weight)

def now_iso() -> str:
    # Must stay parseable by datetime.fromisoformat() in config.prune_history,
    # that's the whole point of calling it "_iso". The previous version
    # ("%Y-%m-%d %H:%M UTC") looked ISO-ish but wasn't valid ISO-8601, which
    # silently made auto_clear_history_days a no-op (every entry failed to
    # parse and was kept). datetime.isoformat() on a tz-aware UTC datetime
    # produces e.g. "2026-07-25T14:42:00+00:00", which round-trips cleanly.
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

# Defence in depth against a value whose "f" expansion is enormous. _dec()
# bounds the exponent for every provider-supplied number and _parse_amount()
# bounds what the user can type, but these two functions are the last thing
# between a Decimal and the Tk layout engine, and they are called from the
# GUI thread. A value that reached here anyway must not be allowed to freeze
# the app while it renders.
_MAX_RENDER_EXP = 30


def _render_safe(d: Decimal) -> bool:
    """False when formatting `d` non-exponentially would produce an
    unreasonable amount of text."""
    try:
        return -_MAX_RENDER_EXP <= d.adjusted() <= _MAX_RENDER_EXP
    except (InvalidOperation, ValueError):
        return False


def full_str(d) -> str:
    """Full-precision plain-decimal string, no rounding: for values the
    user is about to COPY and paste into a wallet (e.g. the deposit
    amount). fmt() below rounds to `places` decimals for readability,
    which is fine for on-screen text but wrong for a clipboard value on a
    coin with more than 8 decimal places (ETH has 18): ROUND_HALF_UP could
    silently hand back a slightly different amount than the one actually
    quoted/agreed with the provider."""
    if d is None:
        return "0"
    try:
        d = Decimal(d)
    except (InvalidOperation, TypeError):
        return str(d)
    if not _render_safe(d):
        return "unreadable value"
    s = f"{d:f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"
STATUS_COLORS = {
    prov.STATUS_COMPLETE: GOOD,
    prov.STATUS_REFUNDED: ACCENT,
    prov.STATUS_REFUNDING: ACCENT,
    prov.STATUS_FAILED: BAD,
    prov.STATUS_EXPIRED: BAD,
    prov.STATUS_PARTIAL: BAD,
    prov.STATUS_NEEDS_ACTION: BAD,
}

def chunk_addr(addr: str, size: int = 4) -> str:
    """Group an address into space-separated blocks for easier visual
    diffing against the source (mirrors preflight.py's CLI chunking). For
    DISPLAY only. Never copy this form: the spaces are not part of the
    address."""
    if not addr:
        return ""
    return " ".join(addr[i:i + size] for i in range(0, len(addr), size))

def fmt(d, places: int = 8) -> str:
    if d is None:
        return "n/a"
    try:
        d = Decimal(d)
    except (InvalidOperation, TypeError):
        return str(d)
    # Round to `places` decimals, then render as a PLAIN fixed-point string
    # with trailing zeros trimmed. Do NOT use Decimal.normalize() here: it
    # re-expresses values in scientific notation (1200 -> "1.2E+3",
    # 0.000000012 -> "1E-8", a BTC->DOGE rate -> "4E+5"), which is exactly the
    # "rate/amount looks like gibberish" problem in the UI. The 'f' format is
    # always non-exponential; we trim the trailing zeros ourselves.
    try:
        d = d.quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        pass  # too many integer digits to quantize; render as-is below
    if not _render_safe(d):
        return "unreadable value"
    s = f"{d:f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"

def card(parent, border_color=BORDER, **kw):
    defaults = {"fg_color": CARD, "corner_radius": RADIUS_LG,
                "border_width": 1, "border_color": border_color}
    defaults.update(kw)
    return ctk.CTkFrame(parent, **defaults)

def pill(parent, text, fg=ACCENT_SOFT, fg_text=ACCENT):
    return ctk.CTkLabel(parent, text=text, fg_color=fg, text_color=fg_text,
                         corner_radius=999, font=F(11, "bold"))

def primary_button(parent, text, command, height=44, **kw):
    defaults = {"fg_color": ACCENT, "hover_color": ACCENT_HOVER,
                "text_color": "#12130f", "corner_radius": RADIUS_SM,
                "font": F(14, "bold"), "height": height}
    defaults.update(kw)
    return ctk.CTkButton(parent, text=text, command=command, **defaults)

def secondary_button(parent, text, command, height=36, **kw):
    defaults = {"fg_color": NEUTRAL_SOFT, "hover_color": CARD_HOVER,
                "text_color": TEXT, "corner_radius": RADIUS_SM,
                "font": F(13), "height": height}
    defaults.update(kw)
    return ctk.CTkButton(parent, text=text, command=command, **defaults)

def entry(parent, **kw):
    defaults = {"fg_color": FIELD_BG, "border_color": FIELD_BORDER,
                "border_width": 1, "corner_radius": RADIUS_SM,
                "text_color": TEXT, "placeholder_text_color": FAINT,
                "font": F(13), "height": 38}
    defaults.update(kw)
    return ctk.CTkEntry(parent, **defaults)

def checkbox(parent, text, variable, **kw):
    defaults = {"fg_color": ACCENT, "hover_color": ACCENT_HOVER,
                "border_color": FIELD_BORDER, "checkmark_color": "#12130f",
                "text_color": TEXT, "font": F(13)}
    defaults.update(kw)
    return ctk.CTkCheckBox(parent, text=text, variable=variable, **defaults)

# Sort order for the coin pickers: these float to the top, everything else
# follows alphabetically. Every entry must exist in providers.constants.COINS
# or it silently never appears.
POPULAR_COINS = ["BTC", "ETH", "XMR", "USDC", "LTC", "SOL", "DOGE", "DASH", "ZEC"]

# Fixed by j2sec
