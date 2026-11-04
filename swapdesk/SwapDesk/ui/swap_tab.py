"""Swap tab: the quote form, results list and provider selection.

Split out of app.py as a mixin: every method runs bound to the SwapDesk
instance (self), so this class is never instantiated on its own."""
from __future__ import annotations

import concurrent.futures
import contextlib
import threading
from decimal import Decimal

import customtkinter as ctk

import providers as prov

from theme import *  # shared colours, fonts, formatters
from widgets import CoinPicker


class SwapTabMixin:
    def _eyebrow(self, parent, text):
        """Small uppercase section label, gives each block of the form a
        clear heading instead of one undifferentiated stack of fields."""
        return ctk.CTkLabel(parent, text=text.upper(), text_color=FAINT,
                            font=F(10, "bold"))

    def _build_swap_tab(self):
        t = self.tab_swap

        # Grid, not pack. The form is a fixed-height block and the results
        # panel is the only elastic row; with pack, a form taller than the
        # window simply squeezed results down to a few unusable pixels and
        # nothing could scroll it back. With grid + a single weighted row,
        # results always receives exactly the leftover space and never less
        # than its configured minimum.
        t.grid_columnconfigure(0, weight=1)
        t.grid_rowconfigure(3, weight=1)

        form = card(t)
        form.grid(row=0, column=0, sticky="ew", padx=4, pady=(6, 0))
        form.grid_columnconfigure(0, weight=1)
        form.grid_columnconfigure(1, weight=0)
        form.grid_columnconfigure(2, weight=1)

        self._eyebrow(form, "Pair").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=18, pady=(12, 4))

        ctk.CTkLabel(form, text="From", text_color=MUTED, font=F(12)).grid(
            row=1, column=0, sticky="w", padx=18, pady=(0, 3))
        ctk.CTkLabel(form, text="To", text_color=MUTED, font=F(12)).grid(
            row=1, column=2, sticky="w", padx=18, pady=(0, 3))

        self.from_coin = CoinPicker(
            form, values=self._coin_list(), names=self.coin_names,
            command=lambda _=None: self._reset_quotes(),
            initial="BTC", height=38)
        self.from_coin.grid(row=2, column=0, sticky="ew", padx=18)

        swap_dir_btn = ctk.CTkButton(
            form, text="⇄", width=36, height=36, corner_radius=999,
            fg_color=NEUTRAL_SOFT, hover_color=ACCENT_SOFT,
            text_color=MUTED, font=F(15, "bold"),
            command=self._swap_direction)
        swap_dir_btn.grid(row=2, column=1, padx=8)

        self.to_coin = CoinPicker(
            form, values=self._coin_list(), names=self.coin_names,
            command=lambda _=None: self._reset_quotes(),
            initial="XMR", height=38)
        self.to_coin.grid(row=2, column=2, sticky="ew", padx=18)

        coins_row = ctk.CTkFrame(form, fg_color="transparent")
        coins_row.grid(row=3, column=0, columnspan=3, sticky="ew",
                       padx=18, pady=(4, 0))
        # Reads as a fraction of the supported set deliberately: the count
        # is what the enabled providers unlock, and the denominator is the
        # ceiling, so a small number reads as "turn more on" rather than
        # "this app only does three coins".
        _unlocked = len(self._coin_list())
        self.coins_status = ctk.CTkLabel(
            coins_row,
            text=(f"{_unlocked} of {len(prov.COINS)} coins unlocked"
                  if _unlocked else
                  "No coins available: enable a provider (and add its key)"),
            text_color=FAINT, font=F(10))
        self.coins_status.pack(side="left")
        refresh_link = ctk.CTkLabel(coins_row, text="Refresh coin list ↻",
                                    text_color=ACCENT, font=F(10), cursor="hand2")
        refresh_link.pack(side="right")
        refresh_link.bind("<Button-1>", lambda _e: self.on_refresh_coins())

        ctk.CTkFrame(form, fg_color=BORDER, height=1).grid(
            row=4, column=0, columnspan=3, sticky="ew", padx=18, pady=(10, 0))

        # Amount and the destination address sit on one row. They are the two
        # fields every swap needs, they are short, and stacking them full-width
        # on a wide window wasted ~120px of height for no gain in legibility.
        self._eyebrow(form, "Amount & destination").grid(
            row=5, column=0, columnspan=3, sticky="w", padx=18, pady=(10, 4))
        ctk.CTkLabel(form, text="Amount to send", text_color=MUTED,
                     font=F(12)).grid(row=6, column=0, sticky="w",
                                      padx=18, pady=(0, 3))
        ctk.CTkLabel(form, text="Destination address (where you receive)",
                     text_color=MUTED, font=F(12)).grid(
            row=6, column=2, sticky="w", padx=18, pady=(0, 3))
        self.amount = entry(form, placeholder_text="0.0500", height=38)
        self.amount.grid(row=7, column=0, sticky="ew", padx=18)
        self.amount.bind("<KeyRelease>", lambda _e: self._reset_quotes())
        self.dest = entry(form, placeholder_text="your receiving wallet address",
                          height=38)
        self.dest.grid(row=7, column=2, sticky="ew", padx=18)

        # Label and placeholder start in the "optional" state and are
        # flipped to the "required" wording by _update_refund_field_label()
        # whenever the selected provider is one that actually enforces a
        # refund address (currently Chainflip) -- a static "optional" here
        # was misleading for that provider, since create_swap() hard-refuses
        # without one.
        self.refund_label = ctk.CTkLabel(
            form, text="Refund address (optional, if a swap fails)",
            text_color=MUTED, font=F(12))
        self.refund_label.grid(
            row=8, column=0, columnspan=3, sticky="w", padx=18, pady=(10, 3))
        self.refund = entry(form, placeholder_text="optional", height=38)
        self.refund.grid(row=9, column=0, columnspan=3, sticky="ew", padx=18,
                         pady=(0, 14))

        self.get_rate_btn = primary_button(t, "Get best rate", self.on_get_rate,
                                           height=44)
        self.get_rate_btn.grid(row=1, column=0, sticky="ew", padx=4, pady=(10, 6))

        self.status_line = ctk.CTkLabel(t, text="", text_color=MUTED,
                                        font=F(12), anchor="w", justify="left",
                                        wraplength=820)
        self.status_line.grid(row=2, column=0, sticky="ew", padx=10)

        # Quotes / results area: the one row that grows.
        self.results = ctk.CTkScrollableFrame(t, fg_color="transparent",
                                              height=260)
        self.results.grid(row=3, column=0, sticky="nsew", padx=4, pady=(6, 8))
        self._swap_form = form
        self._last_results_height = 260
        self._last_wrap = 820
        self._resize_job = None
        # Without this, a child that requests more height than the tab has
        # makes the tab grow to fit it, which pushes the lower rows outside
        # the window rather than clipping the child.
        t.grid_propagate(False)
        # CTkScrollableFrame's `height` is a fixed pixel value for its inner
        # canvas: sticky="nsew" alone does NOT make that canvas grow, so the
        # provider list stayed cramped no matter how big the window got.
        # Recompute and apply the real available height on every resize.
        t.bind("<Configure>", self._resize_results_panel)

    def _resize_results_panel(self, _event=None):
        """Debounced. Recomputes the results panel height for the current
        window size.

        Deliberately does NOT call update_idletasks(): doing that from inside
        a <Configure> handler re-enters layout while the window is still
        being resized, so the numbers read back are a mix of the old and new
        geometry. On restore-down that produced a results height computed
        from the maximised window, which then forced the tab taller than the
        window and pushed the quote rows off-screen entirely.
        """
        if self._resize_job is not None:
            with contextlib.suppress(Exception):
                self.after_cancel(self._resize_job)
        self._resize_job = self.after(60, self._apply_results_height)

    def _apply_results_height(self):
        self._resize_job = None
        with contextlib.suppress(Exception):
            tab_h = self.tab_swap.winfo_height()
            tab_w = self.tab_swap.winfo_width()
            if tab_h <= 1:
                return          # not mapped yet; a later <Configure> will do it
            fixed = (self._swap_form.winfo_height()
                     + self.get_rate_btn.winfo_height()
                     + self.status_line.winfo_height() + 46)
            avail = tab_h - fixed
            # Clamp on BOTH sides. The floor keeps the panel usable; the
            # ceiling is what was missing before, without it the panel could
            # request more height than the tab actually has, and grid would
            # then let the content overflow the window instead of clipping
            # the panel.
            avail = max(120, min(avail, max(120, tab_h - 60)))
            if abs(avail - self._last_results_height) > 8:
                self.results.configure(height=avail)
                self._last_results_height = avail
            # The status line wraps at a fixed width, so on a wide window it
            # broke into short lines and ate vertical space it didn't need.
            wrap = max(360, tab_w - 40)
            if abs(wrap - self._last_wrap) > 20:
                self.status_line.configure(wraplength=wrap)
                self._last_wrap = wrap

    def _proxy_in_use(self) -> bool:
        """True if provider requests are actually going through a proxy.

        Read from the live sessions rather than the config: in "auto" the
        config says auto whether or not a proxy was found, and it is the
        resolved state that explains a failure.
        """
        return any(getattr(p, "session", None) is not None and p.session.proxies
                   for p in getattr(self, "providers", {}).values())

    def _swap_direction(self):
        """Flip From/To in one click instead of re-picking both dropdowns.
        Comparing rates in both directions is a common action."""
        frm, to = self.from_coin.get(), self.to_coin.get()
        self.from_coin.set(to)
        self.to_coin.set(frm)
        self._reset_quotes()

    def _reset_quotes(self, *_):
        self.quotes.clear()
        self.selected_provider = None
        for w in self.results.winfo_children():
            w.destroy()

    def on_get_rate(self):
        if getattr(self, "_rate_fetching", False):
            return
        try:
            amount = self._parse_amount()
        except ValueError as e:
            self._set_status(str(e), BAD)
            return
        frm, to = self.from_coin.get(), self.to_coin.get()
        if not frm or not to:
            # Empty pickers: no enabled provider routes anything, so there
            # is no pair to quote. Point at the cause rather than letting
            # the request go out with blank tickers.
            self._set_status(
                "No coins are unlocked. Open Settings, enable a provider "
                "and add its API key if it needs one.", MUTED)
            return
        if frm == to:
            self._set_status("Choose two different coins.", BAD)
            return
        dest = self.dest.get().strip()

        self._rate_fetching = True
        self.get_rate_btn.configure(state="disabled", text="Fetching quotes…")
        self._set_status("Requesting quotes from providers…", MUTED)
        self._reset_quotes()

        def work():
            try:
                results = {}
                skipped = []
                to_query = []
                enabled = self.cfg.get("enabled_providers", {})
                for name, p in self.providers.items():
                    if not prov.provider_enabled(enabled, name):
                        continue  # off by default; user turns it on in Settings
                    if not p.configured() and not p.can_quote_without_config:
                        # Querying this provider would only ever produce a
                        # "add your API key" error, skip it instead of
                        # showing that as a red error card next to real
                        # quotes; note it once, quietly, below.
                        skipped.append(name)
                        continue
                    to_query.append((name, p))

                # Providers are independent HTTP calls to different hosts, so
                # querying them one at a time made the wait as long as the
                # SUM of every provider's latency (worst case: one timeout
                # per configured provider, back to back).
                # Running them concurrently instead bounds it to the SLOWEST
                # single provider, since each has its own requests.Session
                # and there's no shared state across providers to race on.
                def fetch_one(name, p):
                    try:
                        return name, p.get_quote(frm, to, amount, destination=dest)
                    except Exception as e:  # noqa: BLE001
                        return name, prov.Quote(name, frm, to, amount, None, None,
                                                error=str(e))

                if to_query:
                    with concurrent.futures.ThreadPoolExecutor(
                            max_workers=len(to_query)) as pool:
                        for name, quote in pool.map(
                                lambda np: fetch_one(*np), to_query):
                            results[name] = quote

                self._post(self._show_quotes, results, True, skipped)
            finally:
                self._rate_fetching = False

        threading.Thread(target=work, daemon=True).start()

    def _show_quotes(self, results: dict, apply_auto_select: bool = True,
                     skipped: list | None = None):
        self.get_rate_btn.configure(state="normal", text="Get best rate")
        if skipped is not None:
            self._skipped_providers = skipped
        skipped = getattr(self, "_skipped_providers", [])

        if apply_auto_select and results:
            # A fresh fetch landing after the pair on screen already changed.
            # _reset_quotes() (bound to both coin pickers) has already
            # cleared self.quotes/selected_provider for whatever pair is now
            # showing; applying these results anyway would silently
            # repopulate the display -- and self.selected_provider -- with
            # quotes for a pair that's no longer on screen. Same class of
            # race app.py's _apply_coin_refresh already guards against for a
            # coin-catalogue refresh landing mid-flight; get_rate_btn was
            # already reset above so this doesn't leave it stuck.
            any_q = next(iter(results.values()))
            if (any_q.from_coin, any_q.to_coin) != (
                    self.from_coin.get(), self.to_coin.get()):
                return
            # The amount half of the same race. _reset_quotes() is bound to
            # the amount field's <KeyRelease> as well as to both coin
            # pickers, so an amount edited while the fetch was in flight
            # clears the quotes and this callback used to put them straight
            # back -- rows reading "Send <old amount>" beside a field that
            # now says something else, and on_create_swap's min/max gate
            # checking the new amount against the old amount's quote. A
            # field that isn't a valid amount right now is treated as
            # changed, since these results cannot describe it either.
            try:
                if any_q.send_amount != self._parse_amount():
                    return
            except ValueError:
                return

        # _pick() calls back into this method just to re-render the
        # "Selected" highlight after a manual click. Rebuilding every row in
        # a CTkScrollableFrame resets its scroll position to the top, which
        # is disorienting if the user scrolled down to pick a lower-ranked
        # quote: capture and restore it whenever this isn't a fresh fetch.
        scroll_pos = None
        if not apply_auto_select:
            with contextlib.suppress(Exception):
                scroll_pos = self.results._parent_canvas.yview()[0]

        # Clear previous rows first. _pick() calls back into this method to
        # re-render the "Selected" highlight after a manual click, without
        # this, that re-render appended a second full copy of every quote
        # row (and a second Create-swap button) on top of the first instead
        # of replacing it, stacking further with every subsequent click.
        for w in self.results.winfo_children():
            w.destroy()
        # provider -> its "Use"/"Selected" button, rebuilt fresh each render.
        # _pick() restyles entries in place off this map instead of tearing
        # every quote row down again just to move one highlight.
        self._quote_row_buttons = {}
        self.quotes = results

        if skipped:
            self._skipped_row(skipped)
        ok = [q for q in results.values() if q.ok]
        others_ok = bool(ok)
        # Nothing was queried at all. Every provider is opt-in, so this is the
        # state a fresh install starts in, and it is NOT a connectivity
        # problem: falling through to "check your connection" below would send
        # a new user to debug a network that is working fine.
        if not results and not skipped:
            self._set_status(
                "No providers are switched on yet. Open Settings and enable "
                "at least one to compare rates.", MUTED)
            self._no_providers_row()
            return
        if not ok:
            errored = [q for q in results.values() if q.error]
            # If every configured provider came back saying "this pair isn't
            # something I route" (rather than a network/auth/config hiccup),
            # that's not a failure to explain away, it's simply a pair
            # nobody here offers. Say that plainly instead of pointing the
            # user at their connection, and skip the wall of per-provider
            # error cards; one calm, actionable line is more useful.
            all_unsupported = bool(errored) and all(q.unsupported for q in errored)
            if all_unsupported:
                any_q = next(iter(results.values()))
                frm, to = any_q.from_coin, any_q.to_coin
                self._set_status(
                    f"No current providers support {frm} → {to}. "
                    f"Please select a different pair.", MUTED)
                self._no_route_row(frm, to)
                return
            # Name the proxy when one is carrying the traffic. Some
            # providers block Tor exit nodes, so "check your connection" is
            # actively misleading there: the connection is fine and the
            # setting that fixes it is one the user may not know is on.
            # Only shown when every provider failed, since a single failure
            # among successes is not the proxy's doing.
            if self._proxy_in_use():
                self._set_status(
                    "No quotes available. Requests are going through your "
                    "proxy/Tor, and some providers block Tor exit nodes. "
                    "Settings > Privacy > \"No proxy\" to try directly.", BAD)
            else:
                self._set_status("No quotes available. Check your connection "
                                 "or coin/network support.", BAD)
            for q in errored:
                self._quote_error_row(q, others_ok=False)
            return

        best = max(ok, key=lambda q: q.estimated_receive)
        if apply_auto_select:
            # Only run auto-select on a genuinely fresh quote fetch. This is
            # also called by _pick() to re-render the selection highlight
            # after a manual "Use" click, re-running auto-select there would
            # immediately overwrite the user's manual choice back to the
            # best-rate provider, silently discarding their pick.
            auto = self.cfg.get("auto_select_best", True)
            self.selected_provider = best.provider if auto else None
            self._update_refund_field_label()
        self._set_status(
            f"Best rate: {best.provider} · you receive ≈ "
            f"{fmt(best.estimated_receive)} {best.to_coin}", GOOD)

        for q in sorted(results.values(),
                        key=lambda x: (x.estimated_receive or Decimal(-1)),
                        reverse=True):
            if q.ok:
                self._quote_row(q, is_best=(q.provider == best.provider))
            else:
                self._quote_error_row(q, others_ok=others_ok)

        self.create_btn = primary_button(self.results, "Create swap",
                                         self.on_create_swap, height=46)
        self.create_btn.pack(fill="x", pady=(14, 6))

        if scroll_pos is not None:
            self.after_idle(lambda pos=scroll_pos: self._restore_scroll(pos))

    def _restore_scroll(self, pos: float):
        with contextlib.suppress(Exception):
            self.results._parent_canvas.yview_moveto(pos)

    def _skipped_row(self, skipped: list):
        row = card(self.results, fg_color=CARD, border_color=BORDER, border_width=1)
        row.pack(fill="x", pady=(0, 5))
        names = ", ".join(skipped)
        ctk.CTkLabel(
            row,
            text=f"⏭  Skipped (no API key set): {names}. Add a key in "
                 f"Settings, or leave them off. SwapDesk works fine on "
                 f"SideShift/Trocador alone.",
            text_color=FAINT, font=F(11), wraplength=740, justify="left"
        ).pack(anchor="w", padx=16, pady=10)

    def _quote_row(self, q: prov.Quote, is_best: bool):
        row = card(self.results, border_color=ACCENT if is_best else BORDER,
                   border_width=2 if is_best else 1)
        row.pack(fill="x", pady=5)
        left = ctk.CTkFrame(row, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True, padx=16, pady=14)
        head_line = ctk.CTkFrame(left, fg_color="transparent")
        head_line.pack(anchor="w")
        head = q.provider
        if q.via:
            head += f"  (via {q.via})"
        ctk.CTkLabel(head_line, text=head, font=F(15, "bold"),
                     text_color=ACCENT if is_best else TEXT).pack(side="left")
        if is_best:
            pill(head_line, "★ BEST").pack(side="left", padx=(8, 0),
                                           ipadx=8, ipady=2)
        ctk.CTkLabel(left,
                     text=f"Send {fmt(q.send_amount)} {q.from_coin}  →  receive ≈ "
                          f"{fmt(q.estimated_receive)} {q.to_coin}",
                     text_color=MUTED, font=F(13)).pack(anchor="w", pady=(4, 0))
        # Show the rate with units and direction so it's unambiguous, a bare
        # number ("rate 34.2") doesn't say whether it's to/from, and reads as
        # nonsense for pairs with large or tiny ratios.
        if q.rate is not None:
            sub = f"rate: 1 {q.from_coin} ≈ {fmt(q.rate)} {q.to_coin}"
        else:
            sub = "rate: n/a"
        if q.min_amount is not None:
            sub += f"   · min {fmt(q.min_amount)} {q.from_coin}"
        ctk.CTkLabel(left, text=sub, text_color=FAINT,
                     font=F(11)).pack(anchor="w", pady=(3, 0))

        if self.selected_provider == q.provider:
            pick = secondary_button(row, "Selected", lambda p=q.provider: self._pick(p),
                                    width=90, fg_color=GOOD_SOFT, text_color=GOOD,
                                    hover_color=GOOD_SOFT)
        else:
            pick = secondary_button(row, "Use", lambda p=q.provider: self._pick(p),
                                    width=90)
        pick.pack(side="right", padx=16)
        # Track the button so a later _pick() can flip just this row's
        # highlight in place rather than rebuilding every quote row.
        self._quote_row_buttons[q.provider] = pick

    def _no_providers_row(self):
        """Shown when nothing is enabled yet. Informational, not an error:
        providers are opt-in, so this is the starting state."""
        row = card(self.results, fg_color=CARD, border_color=BORDER, border_width=1)
        row.pack(fill="x", pady=5)
        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=16)
        head = ctk.CTkFrame(inner, fg_color="transparent")
        head.pack(anchor="w", fill="x")
        ctk.CTkLabel(head, text="⚙", font=F(16), text_color=MUTED).pack(side="left")
        ctk.CTkLabel(head, text="No providers enabled", font=F(15, "bold"),
                     text_color=TEXT).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(
            inner,
            text=("Every provider ships switched off. Open Settings and tick "
                  "at least one: Trocador needs a single free API key and "
                  "covers the most pairs, THORChain and Maya need no account "
                  "at all."),
            text_color=MUTED, font=F(13), wraplength=740, justify="left"
        ).pack(anchor="w", pady=(6, 0))

    def _no_route_row(self, frm: str, to: str):
        """Calm, informational card (not an error) shown when every
        configured provider simply doesn't offer this coin pair, as
        opposed to something being broken, which stays styled as an error."""
        row = card(self.results, fg_color=CARD, border_color=BORDER, border_width=1)
        row.pack(fill="x", pady=5)
        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=16)
        head = ctk.CTkFrame(inner, fg_color="transparent")
        head.pack(anchor="w", fill="x")
        ctk.CTkLabel(head, text="✗", font=F(16), text_color=MUTED).pack(side="left")
        ctk.CTkLabel(head, text="No route for this pair", font=F(15, "bold"),
                     text_color=TEXT).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(
            inner,
            text=(f"There are no current providers for {frm} → {to}. "
                  f"Please select a different pair."),
            text_color=MUTED, font=F(13), wraplength=740, justify="left"
        ).pack(anchor="w", pady=(6, 0))

    def _quote_error_row(self, q: prov.Quote, others_ok: bool = False):
        row = card(self.results, fg_color=BAD_SOFT, border_color=BAD, border_width=1)
        row.pack(fill="x", pady=5)
        msg = q.error or ""
        text = msg if msg.startswith(f"{q.provider}: ") else f"{q.provider}: {msg}"
        # If this looks like a "your whole internet/HTTPS is down" message
        # but other providers just returned quotes fine, that diagnosis is
        # wrong: correct it instead of leaving a misleading claim on screen.
        looks_total = ("no internet connectivity" in text.lower()
                       or "blocking outbound https itself" in text.lower()
                       or "blocking outbound https entirely" in text.lower())
        if others_ok and looks_total:
            text += (" (Other providers just responded fine, so this isn't "
                     "your whole connection: it's specific to this host/"
                     "domain being blocked or down.)")
        ctk.CTkLabel(row, text=text,
                     text_color=BAD, font=F(12), wraplength=740, justify="left"
                     ).pack(anchor="w", padx=16, pady=12)

    def _pick(self, provider_name: str):
        # Moving the selection changes only two things on screen: the old
        # row's button reverts to "Use", the new one becomes "Selected".
        # Restyle those two buttons in place instead of destroying and
        # rebuilding every quote row (a full rebuild also reset the scroll
        # position, which is why the old path saved and restored it). Fall
        # back to a full re-render only if the target row isn't tracked,
        # which shouldn't happen once a fetch has rendered its rows.
        buttons = getattr(self, "_quote_row_buttons", {})
        if provider_name not in buttons:
            self.selected_provider = provider_name
            self._show_quotes(self.quotes, apply_auto_select=False)
            return
        previous = self.selected_provider
        self.selected_provider = provider_name
        if previous and previous != provider_name and previous in buttons:
            self._style_pick_button(buttons[previous], selected=False)
        self._style_pick_button(buttons[provider_name], selected=True)
        self._update_refund_field_label()

    def _update_refund_field_label(self):
        """Reflect whether the currently-selected provider actually
        requires a refund address, instead of the field always reading
        "optional" regardless of provider. Mirrors preflight's
        REQUIRES_REFUND_ADDRESS check, so the UI and the gate agree."""
        provider = getattr(self, "providers", {}).get(self.selected_provider)
        requires = bool(getattr(provider, "REQUIRES_REFUND_ADDRESS", False))
        if requires:
            self.refund_label.configure(
                text=f"Refund address (REQUIRED by {self.selected_provider})",
                text_color=BAD)
            self.refund.configure(placeholder_text="required for this provider")
        else:
            self.refund_label.configure(
                text="Refund address (optional, if a swap fails)",
                text_color=MUTED)
            self.refund.configure(placeholder_text="optional")

    @staticmethod
    def _style_pick_button(btn, selected: bool):
        """Flip a quote row's action button between the default "Use" look
        and the green "Selected" look, matching how _quote_row builds them
        so an in-place restyle is indistinguishable from a fresh render."""
        if selected:
            btn.configure(text="Selected", fg_color=GOOD_SOFT,
                          text_color=GOOD, hover_color=GOOD_SOFT)
        else:
            btn.configure(text="Use", fg_color=NEUTRAL_SOFT,
                          text_color=TEXT, hover_color=CARD_HOVER)

    def _section(self, parent, title, subtitle=None, badge=None,
                border_color=BORDER, badge_fg=ACCENT_SOFT, badge_text=ACCENT):
        """Build a consistent settings section card: colored border, title,
        optional pill badge next to the title, optional muted subtitle."""
        box = card(parent, border_color=border_color)
        box.pack(fill="x", pady=8, padx=4)
        head = ctk.CTkFrame(box, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(18, 0))
        ctk.CTkLabel(head, text=title, font=F(16, "bold"),
                     text_color=TEXT).pack(side="left")
        if badge:
            pill(head, badge, fg=badge_fg, fg_text=badge_text
                 ).pack(side="left", padx=(10, 0), ipadx=8, ipady=2)
        if subtitle:
            ctk.CTkLabel(box, text=subtitle, text_color=MUTED,
                         wraplength=780, justify="left", font=F(12)
                         ).pack(anchor="w", padx=18, pady=(8, 10))
        return box
