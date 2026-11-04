"""Reusable compound widgets for SwapDesk (the searchable CoinPicker)."""
from __future__ import annotations

import customtkinter as ctk

from theme import *  # colours, fonts, entry(), POPULAR_COINS


class CoinPicker(ctk.CTkFrame):
    """A From/To coin selector.

    Labels read "TICKER (Name)" but get()/set() deal in bare tickers, so
    callers never see the display form. Exposes the CTkOptionMenu surface
    (get / set / configure(values=..., names=...)) the rest of the app uses.

    An empty value list is a real state, not a bug: coins unlock per
    provider, so a fresh install with nothing enabled has none. The menu
    goes disabled and says so rather than offering a coin no provider can
    route, and get() returns "" so callers never see a phantom ticker.
    """

    EMPTY_LABEL = "No coins unlocked"

    def __init__(self, master, values: list[str], names: dict[str, str],
                 command=None, initial: str = "", height: int = 40, **kw):
        super().__init__(master, fg_color="transparent")
        self._values: list[str] = list(values)
        self._names = names          # ticker -> display name, read live
        self._command = command
        self._value = self._first_offered(initial)

        self._var = ctk.StringVar(value=self._label_for(self._value))
        self._menu = ctk.CTkOptionMenu(
            self, variable=self._var, values=self._labels(),
            command=self._on_pick, anchor="w",
            fg_color=FIELD_BG, button_color=FIELD_BG,
            button_hover_color=CARD_HOVER, text_color=TEXT,
            # Set explicitly rather than inherited: the empty state renders
            # through this colour, and CustomTkinter's own default for it
            # comes from its bundled theme JSON, so a library upgrade could
            # silently restyle the one label that has to stay readable.
            text_color_disabled=MUTED,
            dropdown_fg_color=SURFACE, dropdown_hover_color=CARD_HOVER,
            dropdown_text_color=TEXT, corner_radius=RADIUS_SM,
            font=F(13), dropdown_font=F(13), height=height, **kw)
        if not self._values:
            self._menu.configure(state="disabled")
        self._menu.pack(fill="both", expand=True)

    # Labels carry the coin name for readability, so map both ways rather
    # than storing the label as the value: callers expect a bare ticker.
    def _first_offered(self, preferred: str = "") -> str:
        """`preferred` when it's offered, else the first coin that is,
        else "" when nothing is unlocked."""
        if preferred and preferred in self._values:
            return preferred
        ordered = [c for c in POPULAR_COINS if c in self._values]
        ordered += [c for c in sorted(self._values) if c not in ordered]
        return ordered[0] if ordered else ""

    def _label_for(self, ticker: str) -> str:
        if not ticker:
            return self.EMPTY_LABEL
        name = self._names.get(ticker, "")
        return f"{ticker} ({name})" if name else ticker

    def _labels(self) -> list[str]:
        ordered = [c for c in POPULAR_COINS if c in self._values]
        ordered += [c for c in sorted(self._values) if c not in ordered]
        return [self._label_for(c) for c in ordered] or [self.EMPTY_LABEL]

    def _ticker_for(self, label: str) -> str:
        if label == self.EMPTY_LABEL:
            return ""
        return (label or "").split(" (")[0].strip()

    def _on_pick(self, label: str):
        self._value = self._ticker_for(label)
        if self._command:
            self._command(self._value)

    # CTkOptionMenu-compatible interface
    def get(self) -> str:
        return self._value

    def set(self, value: str):
        # Never hold a ticker the picker doesn't offer: on_get_rate() reads
        # get() straight into a quote request, so a stale selection left
        # over from a wider list would quote a pair no enabled provider
        # routes.
        self._value = value if value in self._values else self._first_offered()
        self._var.set(self._label_for(self._value))

    def configure(self, values: list[str] | None = None,
                  names: dict[str, str] | None = None, **kw):
        if names is not None:
            self._names = names
        if values is not None:
            self._values = list(values)
        if values is not None or names is not None:
            self._menu.configure(values=self._labels())
            # A refresh can drop the selected coin (the user turned off the
            # only provider routing it, or removed its API key). Leaving it
            # selected would show a ticker the dropdown no longer offers and
            # quote a pair nothing can route, so fall back to the first
            # available, or to the empty state when there is none.
            if self._value not in self._values:
                self._value = self._first_offered()
            self._menu.configure(state="normal" if self._values else "disabled")
            self._var.set(self._label_for(self._value))
        if kw:
            self._menu.configure(**kw)

# Fixed by j2sec
