"""History tab: past swaps and their live status.

Split out of app.py as a mixin: every method runs bound to the SwapDesk
instance (self), so this class is never instantiated on its own."""
from __future__ import annotations

import customtkinter as ctk

import config as cfg
import providers as prov

from theme import *  # shared colours, fonts, formatters


class HistoryTabMixin:
    def _build_history_tab(self):
        self.history_wrap = ctk.CTkScrollableFrame(self.tab_history,
                                                   fg_color="transparent")
        self.history_wrap.pack(fill="both", expand=True)
        # Defer rendering until the History tab is visible.
        self._history_dirty = True

    def _refresh_history(self, force: bool = False):
        # Avoid rebuilding hidden history widgets on every status poll.
        if not force and self.tabs.get() != "History":
            self._history_dirty = True
            return
        self._history_dirty = False
        for w in self.history_wrap.winfo_children():
            w.destroy()
        hist = cfg.load_history()
        if not hist:
            ctk.CTkLabel(self.history_wrap, text="No swaps yet.",
                         text_color=FAINT, font=F(13)).pack(pady=24)
            return
        for e in hist:
            if not isinstance(e, dict):
                continue
            row = card(self.history_wrap)
            row.pack(fill="x", pady=5, padx=4)
            frm, to = e.get('from', '?'), e.get('to', '?')
            top = f"{frm} → {to}    {e.get('send', '')} {frm}"
            ctk.CTkLabel(row, text=top, font=F(14, "bold"), text_color=TEXT
                         ).pack(anchor="w", padx=16, pady=(12, 0))
            # Resolve retired spellings so an old history entry keeps
            # its colour instead of silently falling back to grey.
            status = prov.normalize_status(e.get('status', ''))
            meta_color = STATUS_COLORS.get(status, MUTED)
            ctk.CTkLabel(row, text=f"{e.get('provider', '?')} · {e.get('time', '?')}",
                         text_color=MUTED, font=F(11)).pack(anchor="w", padx=16,
                                                            pady=(4, 0))
            if status:
                pill(row, status, fg=CARD_HOVER, fg_text=meta_color
                     ).pack(anchor="w", padx=16, pady=(6, 0), ipadx=8, ipady=2)
            ctk.CTkLabel(row, text=f"order {e.get('order_id', '?')}",
                         text_color=FAINT, font=F(10)).pack(anchor="w", padx=16,
                                                            pady=(6, 12))

# Fixed by j2sec
