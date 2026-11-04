"""Swap creation, the deposit view, QR, clipboard and status polling.

Split out of app.py as a mixin: every method runs bound to the SwapDesk
instance (self), so this class is never instantiated on its own."""
from __future__ import annotations

import contextlib
import threading
from datetime import datetime, timezone
from io import BytesIO

import customtkinter as ctk

import config as cfg
import preflight
import providers as prov

from theme import *  # shared colours, fonts, formatters


def _expiry_passed(expires_at) -> bool:
    """True only when `expires_at` is a deadline this app can read AND it has
    already passed.

    Anything unreadable is treated as "not expired". Two of the formats that
    reach Swap.expires_at are written by this app (Chainflip's channel
    lifetime, FixedFloat's and THORChain/Maya's epoch fields, all rendered as
    "%Y-%m-%d %H:%M:%S UTC") and the rest are provider strings passed through
    verbatim, so guessing at an unrecognised one would be worse than staying
    quiet: a wrong "expired" on a live order is its own bad advice.
    """
    if not isinstance(expires_at, str) or not expires_at.strip():
        return False
    text = expires_at.strip()
    parsed = None
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S UTC").replace(
            tzinfo=timezone.utc)
    except ValueError:
        with contextlib.suppress(ValueError):
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed is None:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) > parsed


class DepositMixin:
    def on_create_swap(self):
        if not self.selected_provider:
            self._set_status("Pick a provider first (or enable auto-select).", BAD)
            return
        p = self.providers[self.selected_provider]
        if not p.configured():
            self._set_status(f"{p.name} isn't configured. Add credentials in "
                             f"Settings.", BAD)
            self.tabs.set("Settings")
            return

        # Catch amounts outside the provider's own min/max BEFORE hitting the
        # API, using the limits it already returned with the quote, avoids
        # a round-trip just to get back the same "too low/high" rejection.
        q = self.quotes.get(self.selected_provider)
        if q and q.ok:
            try:
                amt_check = self._parse_amount()
            except ValueError:
                amt_check = None
            if amt_check is not None:
                if q.min_amount is not None and amt_check < q.min_amount:
                    self._set_status(
                        f"{p.name}: {fmt(amt_check)} {q.from_coin} is below the "
                        f"minimum of {fmt(q.min_amount)} {q.from_coin} for this "
                        f"pair.", BAD)
                    return
                if q.max_amount is not None and amt_check > q.max_amount:
                    self._set_status(
                        f"{p.name}: {fmt(amt_check)} {q.from_coin} is above the "
                        f"maximum of {fmt(q.max_amount)} {q.from_coin} for this "
                        f"pair.", BAD)
                    return

        frm, to = self.from_coin.get(), self.to_coin.get()
        dest = self.dest.get().strip()
        refund = self.refund.get().strip()
        if not cfg.address_looks_valid(to, dest):
            self._set_status(f"That destination doesn't look like a valid {to} "
                             f"address. Double-check before sending funds.", BAD)
            return
        if refund and not cfg.address_looks_valid(frm, refund):
            self._set_status(f"Refund address doesn't look like a valid {frm} "
                             f"address.", BAD)
            return
        try:
            amount = self._parse_amount()
        except ValueError as e:
            self._set_status(str(e), BAD)
            return

        # Full preflight gate: the same module/checks the CLI (preflight.py)
        # uses, not a re-implemented subset. run_preflight() itself is local
        # and makes no network calls, so it runs synchronously here; the
        # live dry-run quote that the CLI gates on runs inside the modal
        # below, on a background thread, and holds the Confirm button
        # disabled until it comes back clean. The typed confirm phrase is
        # then required before any real create_swap() call, mirroring the
        # CLI's execute=True + confirm_phrase gate.
        result = preflight.run_preflight(p, frm, to, amount, dest, refund)
        self._show_preflight_dialog(p, frm, to, amount, dest, refund, result)

    @staticmethod
    def _only_blocker_is_address_book(result, to_coin: str) -> bool:
        """True when every failed check is the has_curated_pattern one.

        Matched on the same condition that produced the check rather than on
        its label text, so rewording the check can't silently switch this
        off. Any other failure (bad format, unsupported pair, missing
        credentials) is not clearable by vouching for an address.
        """
        if cfg.has_curated_pattern(to_coin):
            return False
        failed = [c for c in result.checks if not c[1]]
        if len(failed) != 1:
            return False
        # Identify the check rather than trusting the count. Matching on the
        # condition that produced it (an uncurated destination coin) plus the
        # label's stable prefix means a second, unrelated single failure --
        # an unconfigured provider, say -- cannot be cleared by vouching for
        # an address, which is what a bare count would have allowed.
        return failed[0][0].startswith(f"{to_coin} has no built-in address")

    def _confirm_address_and_retry(self, win, p, frm, to, amount, dest, refund):
        """Save `dest` to the address book, then re-run preflight.

        Gated on retyping the last 6 characters, the same check the CLI uses
        on a deposit address in _confirm_deposit_address. A plain OK button
        here would be a one-click bypass of the gate; the point is to make
        the user look at the address once, deliberately, before this app
        will treat it as vouched for.
        """
        confirm = ctk.CTkToplevel(self)
        confirm.title("Confirm destination address")
        self._center_on_screen(confirm, 470, 400)
        confirm.configure(fg_color=BG)
        confirm.transient(win)
        confirm.grab_set()

        ctk.CTkLabel(confirm, text=f"Confirm this {to} address",
                     font=F(16, "bold"), text_color=TEXT).pack(pady=(20, 8), padx=18)
        ctk.CTkLabel(
            confirm,
            text=(f"SwapDesk has no built-in format check for {to}, so it "
                  f"cannot tell a correct address from a truncated or "
                  f"wrong-coin one. Check it against your wallet, character "
                  f"by character, before confirming. A wrong address here is "
                  f"typically unrecoverable."),
            text_color=MUTED, font=F(12), wraplength=410,
            justify="left").pack(padx=18, pady=(0, 10))
        ctk.CTkLabel(confirm, text=chunk_addr(dest), text_color=TEXT,
                     font=F(12), wraplength=410,
                     justify="left").pack(padx=18, pady=(0, 10))

        tail = dest[-6:]
        ctk.CTkLabel(confirm, text="Retype the LAST 6 CHARACTERS to confirm:",
                     text_color=MUTED, font=F(12)).pack(padx=18)
        tail_entry = entry(confirm, placeholder_text=tail)
        tail_entry.pack(fill="x", padx=18, pady=(6, 4))
        tail_entry.focus_set()
        err = ctk.CTkLabel(confirm, text="", text_color=BAD, font=F(11),
                           wraplength=410, justify="left")
        err.pack(padx=18)

        def save(*_a):
            if tail_entry.get().strip() != tail:
                err.configure(text="That doesn't match the last 6 characters "
                                   "above. Re-read the address before "
                                   "confirming.")
                return
            cfg.add_to_address_book(to, dest)
            confirm.destroy()
            win.destroy()
            # Re-run the gate rather than assuming it now passes: the saved
            # address clears this one check, and the rest still have to.
            self.on_create_swap()

        btns = ctk.CTkFrame(confirm, fg_color="transparent")
        btns.pack(fill="x", padx=18, pady=16)
        secondary_button(btns, "Cancel", confirm.destroy, height=40).pack(
            side="left", expand=True, fill="x", padx=(0, 4))
        primary_button(btns, "Confirm and save", save, height=40).pack(
            side="left", expand=True, fill="x", padx=(4, 0))
        tail_entry.bind("<Return>", save)

    def _show_preflight_dialog(self, p, frm, to, amount, dest, refund, result):
        win = ctk.CTkToplevel(self)
        win.title("Preflight")
        self._center_on_screen(win, 480, 620)
        win.configure(fg_color=BG)
        win.transient(self)
        win.grab_set()

        ctk.CTkLabel(win, text=f"Preflight: {p.name} {frm} → {to}",
                     font=F(16, "bold"), text_color=TEXT).pack(pady=(18, 10), padx=18)

        report = card(win, border_color=(BORDER if result.ok else BAD),
                     border_width=(1 if result.ok else 2))
        report.pack(fill="both", expand=True, padx=18, pady=(0, 10))
        inner = ctk.CTkScrollableFrame(report, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=10, pady=10)
        for label, passed, detail in result.checks:
            mark = "✓" if passed else "✗"
            color = GOOD if passed else BAD
            text = f"{mark}  {label}" + (f", {detail}" if detail else "")
            ctk.CTkLabel(inner, text=text, text_color=color, font=F(12),
                        wraplength=400, justify="left", anchor="w"
                        ).pack(fill="x", pady=2)
        for warn in result.warnings:
            ctk.CTkLabel(inner, text=f"⚠  {warn}", text_color=ACCENT, font=F(12),
                        wraplength=400, justify="left", anchor="w"
                        ).pack(fill="x", pady=(6, 2))

        if not result.ok:
            ctk.CTkLabel(win, text="BLOCKED: fix the failed checks above "
                                   "before creating this swap.",
                        text_color=BAD, font=F(13, "bold"),
                        wraplength=410, justify="center").pack(pady=(0, 10), padx=18)
            # The uncurated-coin check is the one failure the user can clear
            # from here rather than by editing the form. It fails because
            # config.py has no format pattern for this ticker, so the only
            # evidence the address is really theirs is an explicit prior
            # confirmation. Without this button the remediation text named
            # config.add_to_address_book(), which nothing in the GUI calls,
            # so every coin outside the curated set was a dead end while the
            # dropdowns kept offering hundreds of them.
            if self._only_blocker_is_address_book(result, to):
                secondary_button(
                    win, "Confirm this address and retry",
                    lambda: self._confirm_address_and_retry(
                        win, p, frm, to, amount, dest, refund),
                    height=38).pack(fill="x", padx=18, pady=(0, 6))
            secondary_button(win, "Close", win.destroy, height=38
                             ).pack(fill="x", padx=18, pady=(0, 18))
            return

        # Live dry-run quote, the GUI equivalent of the CLI's gate in
        # guarded_create_swap(): "not creating a swap against a pair the
        # provider itself just rejected for a quote". Everything above this
        # point is a LOCAL check against tables and files, none of it can
        # see that a coin went into maintenance, hit a reserve limit or was
        # disabled since the quote card on the Swap tab was rendered. It
        # also settles the one check preflight deliberately leaves open:
        # Trocador defaults unknown coins to a network guess, so
        # _pair_support_state() reports "unknown" and warns that the live
        # quote is the real test. Without this that test never ran here.
        # Confirm stays disabled until it comes back clean.
        live_lbl = ctk.CTkLabel(win, text="Live check: asking "
                                          f"{p.name} for a fresh quote…",
                                text_color=MUTED, font=F(12), wraplength=410,
                                justify="center")
        live_lbl.pack(padx=18, pady=(4, 2))

        ctk.CTkLabel(win, text=f"Type {preflight.CONFIRM_PHRASE!r} to create "
                                f"a REAL swap and generate a deposit address.",
                    text_color=MUTED, font=F(12), wraplength=410,
                    justify="center").pack(padx=18, pady=(4, 6))
        phrase_entry = entry(win, placeholder_text=preflight.CONFIRM_PHRASE)
        phrase_entry.pack(fill="x", padx=18, pady=(0, 6))
        err_lbl = ctk.CTkLabel(win, text="", text_color=BAD, font=F(11))
        err_lbl.pack(padx=18)

        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack(fill="x", padx=18, pady=(6, 18))
        secondary_button(btns, "Cancel", win.destroy, height=40
                         ).pack(side="left", expand=True, fill="x", padx=(0, 4))

        def on_confirm():
            if phrase_entry.get().strip() != preflight.CONFIRM_PHRASE:
                err_lbl.configure(text=f"Must type exactly: {preflight.CONFIRM_PHRASE}")
                return
            win.destroy()
            self._create_swap_now(p, frm, to, amount, dest, refund)

        confirm_btn = primary_button(btns, "Checking live quote…", on_confirm,
                                     height=40)
        confirm_btn.configure(state="disabled")
        confirm_btn.pack(side="left", expand=True, fill="x", padx=(4, 0))

        def apply_dry_run(quote, failure: str | None):
            # Scheduled back onto the GUI thread; the user may have hit
            # Cancel while the request was in flight, in which case every
            # widget below is already destroyed.
            with contextlib.suppress(Exception):
                if not win.winfo_exists():
                    return
                if failure is not None:
                    live_lbl.configure(
                        text=f"BLOCKED, live quote failed: {failure}\n\n"
                             f"Not creating a swap against a pair "
                             f"{p.name} just rejected for a quote. Close "
                             f"this, re-run Get best rate, and try again "
                             f"or pick another provider.",
                        text_color=BAD)
                    confirm_btn.configure(text="Blocked by live check",
                                          state="disabled")
                    return
                live_lbl.configure(
                    text=f"Live quote OK: {fmt(amount)} {frm} → "
                         f"≈ {fmt(quote.estimated_receive)} {to}",
                    text_color=GOOD)
                confirm_btn.configure(text="Confirm & create swap",
                                      state="normal")

        def dry_run():
            try:
                q = p.get_quote(frm, to, amount, destination=dest)
            except Exception as e:  # noqa: BLE001 - any provider/network
                # failure here is a reason to BLOCK, not to crash the
                # dialog, and there's no single narrower type across five
                # provider implementations.
                self._post(apply_dry_run, None, str(e))
                return
            self._post(apply_dry_run, q, None if q.ok else (q.error or
                                                            "no quote returned"))

        threading.Thread(target=dry_run, daemon=True).start()

    def _create_swap_now(self, p, frm, to, amount, dest, refund):
        self.create_btn.configure(state="disabled", text="Creating…")

        def work():
            try:
                swap = p.create_swap(frm, to, amount, dest, refund)
                # Independent post-creation check. See preflight.py's
                # verify_provider_confirmed_destination docstring. A
                # "mismatch" aborts exactly like the CLI's SAFETY ABORT:
                # no deposit window is ever shown for it.
                status, message = preflight.verify_provider_confirmed_destination(
                    swap.provider, swap, dest)
                if status == "mismatch":
                    # The order id goes out with the refusal. The swap exists
                    # at the provider by this point, so aborting without it
                    # left the user with an order they had no handle on.
                    raise prov.ProviderError(
                        f"{message}\n\nThe order DOES exist at "
                        f"{swap.provider} (order id: {swap.order_id}). Do not "
                        f"fund it; look it up on the provider's own site if "
                        f"you need to.")
                # The deposit figure shown is the provider's, not the one
                # typed in, because providers round it. That makes the field
                # worth checking: it is the number the user pays.
                amt_status, amt_message = preflight.verify_provider_deposit_amount(
                    swap.provider, swap, amount)
                if amt_status == "mismatch":
                    raise prov.ProviderError(
                        f"{amt_message}\n\nThe order DOES exist at "
                        f"{swap.provider} (order id: {swap.order_id}). Do not "
                        f"fund it; look it up on the provider's own site if "
                        f"you need to.")
                self._post(self._show_deposit, swap, status, message)
            except prov.ProviderNetworkError as e:
                # The request may have reached the provider and opened an
                # order whose response was lost; nothing here can tell.
                # THORChain/Maya create nothing server-side.
                self._post(self._create_failed, str(e),
                           not isinstance(p, prov._ThorForkProvider))
            except Exception as e:  # noqa: BLE001
                self._post(self._create_failed, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _safe_configure_create_btn(self, **kw):
        # self.create_btn gets replaced (and the old one destroyed) if the
        # user fetches new quotes while a create-swap request is still
        # in-flight on a background thread. Guard the configure() call so a
        # stale widget reference can't throw and swallow whatever runs after
        # it, which would swallow the actual error message in _create_failed.
        with contextlib.suppress(Exception):
            if self.create_btn.winfo_exists():
                self.create_btn.configure(**kw)

    def _create_failed(self, msg: str, order_may_exist: bool = False):
        # Network-shaped failures while proxied are worth attributing: some
        # providers block Tor exit nodes, and the user may not know a proxy
        # is in use at all. Not appended to provider-side refusals (bad
        # address, unsupported pair), where blaming the proxy would send
        # them to the wrong setting.
        low = msg.lower()
        looks_network = any(w in low for w in
                            ("timed out", "timeout", "connection", "unreachable",
                             "refused", "resolve", "proxy", "403", "forbidden"))
        if looks_network and self._proxy_in_use():
            msg = (f"{msg}\n\nRequests are going through your proxy/Tor. Some "
                   f"providers block Tor exit nodes; Settings > Privacy > "
                   f"\"No proxy\" will try directly.")
        if order_may_exist:
            msg = (f"{msg}\n\nThe connection failed while the order was being "
                   f"created, so it may exist at the provider even though no "
                   f"deposit address reached SwapDesk. Nothing from this "
                   f"attempt should be funded. Creating the swap again opens "
                   f"a new order; an unfunded one simply expires.")
        self._set_status(f"Could not create swap: {msg}", BAD)
        self._safe_configure_create_btn(state="normal", text="Create swap")

    def _show_deposit(self, swap: prov.Swap, destination_check_status: str = "unverified",
                      destination_check_message: str = ""):
        # Defaults to the CAUTIOUS state, not "ok": the one caller today
        # (_create_swap_now) always passes a real value from
        # preflight.verify_provider_confirmed_destination(), so this default
        # is never exercised in practice, but a safety-relevant default
        # should fail closed. "ok" here would have meant a future caller
        # that forgets this argument gets a fully-unverified swap rendered
        # as independently confirmed -- QR shown immediately, no warning
        # card -- exactly backwards from what this parameter is for.
        try:
            self._build_deposit_window(swap, destination_check_status,
                                       destination_check_message)
        except Exception as exc:
            # Deliberately broad, and re-raised below. The swap exists at the
            # provider by now, so no rendering failure may leave the user
            # without the order id. See _deposit_render_failed.
            self._deposit_render_failed(swap, exc)
            # Re-raised on purpose: this runs inside a self.after() callback,
            # so SwapDesk.report_callback_exception still writes crash.log and
            # shows the error dialog. The difference is that the order id has
            # already reached the status line by the time it does.
            raise

    def _build_deposit_window(self, swap: prov.Swap,
                              destination_check_status: str = "unverified",
                              destination_check_message: str = ""):
        # Same fail-closed reasoning as _show_deposit's default above, which
        # is this method's only caller: a future direct caller that forgets
        # this argument should get the cautious "unverified" rendering
        # (QR withheld, warning card shown), never a silent "ok".
        cfg.append_history({
            "time": now_iso(),
            "provider": swap.provider,
            "order_id": swap.order_id,
            "from": swap.from_coin, "to": swap.to_coin,
            # Exact, not rounded: this is the permanent record of what was
            # sent, and it should match what the deposit window displayed.
            "send": full_str(swap.send_amount),
            "deposit_address": swap.deposit_address,
            "destination": swap.settle_address,
            "status": swap.status,
        })
        self._refresh_history()

        win = ctk.CTkToplevel(self)
        win.title(f"Deposit: {swap.provider}")
        self._center_on_screen(win, 480, 760)
        win.configure(fg_color=BG)
        win.transient(self)
        win.grab_set()

        # Clipboard-clear timers armed from THIS window. Scoped here, not on
        # the instance, so closing one deposit window doesn't cancel the
        # pending clears belonging to another still-open one.
        win_timers: list = []

        ctk.CTkLabel(win, text=f"Send {swap.from_coin} to complete your swap",
                     font=F(17, "bold"), text_color=TEXT).pack(pady=(20, 4))
        ctk.CTkLabel(win, text=f"via {swap.provider}", text_color=MUTED,
                     font=F(12)).pack()

        # Re-validate the DEPOSIT address the provider handed back, using the
        # same format check already applied to the user-typed destination
        # address. Everything above this point only confirmed the address
        # the USER entered looks right; it never checked what the provider
        # actually sent back as the address to FUND. A provider bug, a
        # man-in-the-middle on the API call, or a malformed response could
        # in principle hand back something that isn't a valid from_coin
        # address at all: and without this check, the QR/copy buttons below
        # would present it exactly the same as a good one. This can't catch
        # a well-formed-but-wrong address (that's a different, unsolvable-
        # client-side problem), only a malformed one, but a malformed one
        # is exactly the kind of silent provider-side bug that has caused
        # real losses elsewhere.
        deposit_addr_ok = cfg.address_looks_valid(swap.from_coin, swap.deposit_address)
        if not deposit_addr_ok:
            danger = card(win, fg_color=BAD_SOFT, border_color=BAD, border_width=2)
            danger.pack(fill="x", padx=18, pady=(10, 4))
            ctk.CTkLabel(
                danger,
                text=("⚠ STOP, DO NOT SEND FUNDS\n\n"
                      f"The deposit address {swap.provider} returned doesn't "
                      f"look like a valid {swap.from_coin} address. This "
                      f"could be a provider-side bug or a corrupted "
                      f"response: sending funds to it risks losing them "
                      f"permanently.\n\nDouble-check this address on "
                      f"{swap.provider}'s own site/app before sending "
                      f"anything, or try a different provider."),
                text_color=BAD, font=F(13, "bold"),
                wraplength=410, justify="center").pack(padx=14, pady=14)

        # Destination check state for this window. For a provider that can
        # only report the destination after creation, polling fills it in
        # later (_apply_destination_echo); "blocked" is set if that later
        # report disagrees with what was approved.
        maker = self.providers.get(swap.provider)
        dest_ctx = {"status": destination_check_status, "blocked": False,
                    "card": None, "label": None, "sync": None, "qr": None,
                    "qr_slot": None, "qr_pending_card": None, "qr_eligible": False,
                    "deferred": bool(getattr(maker, "DESTINATION_ECHO_DEFERRED", False)),
                    # Past the provider's deposit deadline: set here if the
                    # window opens late, or by _lock_if_expired() on a poll.
                    "expired": _expiry_passed(swap.expires_at)}
        if destination_check_status == "unverified":
            unver = card(win, fg_color=BAD_SOFT, border_color=BAD, border_width=1)
            unver.pack(fill="x", padx=18, pady=(10, 4))
            unver_text = (f"⚠ Destination not independently verified\n\n"
                          f"{destination_check_message}")
            if dest_ctx["deferred"]:
                unver_text += (f"\n\n{swap.provider} reports the destination "
                               f"only after the swap is set up. SwapDesk keeps "
                               f"asking and updates this box when it does.")
            unver_lbl = ctk.CTkLabel(
                unver, text=unver_text,
                text_color=BAD, font=F(12, "bold"),
                wraplength=410, justify="center")
            unver_lbl.pack(padx=14, pady=12)
            dest_ctx["card"], dest_ctx["label"] = unver, unver_lbl

        # The refund address the user typed was not sent (THORChain/Maya
        # memo-budget ladder). Said here because the Swap tab still shows the
        # field they filled in, and a refund going to the sending address is
        # only safe if that address is one they control.
        if getattr(swap, "refund_address_sent", None) is False:
            refund_card = card(win, fg_color=BAD_SOFT, border_color=BAD,
                               border_width=1)
            refund_card.pack(fill="x", padx=18, pady=(10, 4))
            ctk.CTkLabel(
                refund_card,
                text=("Refund address was NOT included\n\n"
                      "This swap's memo would not fit on the "
                      f"{swap.from_coin} chain with a refund address in it, "
                      "so it was sent without one. If the swap has to be "
                      "refunded it goes back to the address you send FROM. "
                      "Only fund this from a wallet you control."),
                text_color=BAD, font=F(12, "bold"),
                wraplength=410, justify="center").pack(padx=14, pady=12)

        # QR suppression. Three things withhold it, each because scanning is
        # the fastest path in this window and the one representation that
        # bypasses every acknowledgment gate below it:
        #  1. a memo/tag is required (a plain-address QR can't carry it) --
        #     a wallet scans the address, sends WITHOUT the memo, and the
        #     deposit is typically unrecoverable;
        #  2. the deposit address itself failed this app's format check;
        #  3. the destination hasn't been independently verified yet (a
        #     deferred-echo provider, e.g. Chainflip, whose BaaS API can't
        #     confirm the channel's destination until after creation, still
        #     polling for it). A QR here was previously scannable the
        #     instant the window opened, before this app had confirmed the
        #     deposit channel actually goes to the address the user
        #     approved -- exactly the MITM/provider-bug scenario the
        #     verification card above exists to catch, sidestepped entirely
        #     by not touching the (correctly gated) copy button.
        # Cases 1-2 suppress the QR for good (they can't resolve later in
        # this window); case 3 is temporary: _apply_destination_echo() below
        # renders the QR retroactively the moment a later poll confirms a
        # match, and leaves it withheld for good on a confirmed mismatch.
        qr_blocked_for_good = (bool(swap.deposit_memo) or not deposit_addr_ok
                               or dest_ctx["expired"])
        qr_blocked_pending_verify = (not qr_blocked_for_good
                                     and dest_ctx["status"] == "unverified")
        qr_slot = ctk.CTkFrame(win, fg_color="transparent")
        qr_slot.pack(fill="x")
        dest_ctx["qr_slot"] = qr_slot
        dest_ctx["qr_eligible"] = not qr_blocked_for_good
        if qr_blocked_for_good:
            no_qr = card(qr_slot, fg_color=BAD_SOFT, border_color=BAD, border_width=1)
            no_qr.pack(fill="x", padx=18, pady=(10, 4))
            ctk.CTkLabel(
                no_qr,
                text=("QR code hidden on purpose\n\n"
                      "This swap needs a MEMO/tag (shown below), and a QR of "
                      "just the address can't include it. Scanning it would "
                      "send without the memo, which is typically "
                      "unrecoverable. Send manually: copy BOTH the deposit "
                      "address and the memo into your wallet."
                      if swap.deposit_memo and not dest_ctx["expired"] else
                      "QR code hidden: this order's deposit deadline has "
                      f"passed ({swap.expires_at}). Do not fund it; start a "
                      "new swap instead."
                      if dest_ctx["expired"] else
                      "QR code hidden on purpose\n\n"
                      "The deposit address above did not pass this app's "
                      "format check for " + swap.from_coin + ", so no QR is "
                      "offered for it. Scanning is the fastest way to fund an "
                      "address and this is not an address to fund. Verify it "
                      "on the provider's own site before doing anything with "
                      "it."),
                text_color=BAD, font=F(12, "bold"),
                wraplength=410, justify="center").pack(padx=14, pady=12)
        elif qr_blocked_pending_verify:
            pending = card(qr_slot, fg_color=BAD_SOFT, border_color=BAD, border_width=1)
            pending.pack(fill="x", padx=18, pady=(10, 4))
            # "Updates automatically" is only true for a DEFERRED-echo
            # provider (currently only Chainflip): only that path is polled
            # for a later confirmation (_start_polling above, gated on
            # dest_ctx["deferred"]). For a non-deferred provider whose
            # response simply omitted every echo field this app knows how to
            # read (possible today for Trocador, whose destination-echo field
            # name varies by version/partner -- see trocador.py's
            # settle_address_confirmed_by_provider comment), the status stays
            # "unverified" for the rest of the window's life: nothing ever
            # re-checks it. Promising an automatic update that structurally
            # cannot happen would leave the user waiting on a confirmation
            # that is never coming, so the two cases get different text.
            pending_text = (
                "QR code withheld until the destination is verified\n\n"
                f"{swap.provider} hasn't independently confirmed the deposit "
                "channel's destination yet (see the box above). Scanning a "
                "QR skips every check in this window, so it's only shown "
                "once that confirmation lands. This updates automatically "
                "-- no need to reopen the window."
                if dest_ctx["deferred"] else
                "QR code withheld: destination not independently confirmed\n\n"
                f"{swap.provider}'s response didn't include anything this "
                "app can use to independently confirm the deposit channel's "
                "destination (see the box above), and unlike some providers "
                "it won't arrive on a later check either -- this box will "
                "not resolve on its own. The deposit address above still "
                "passed this app's format check, and this is the destination "
                "you approved when creating the swap, but verify it on "
                f"{swap.provider}'s own site before sending if you can. Use "
                "the copy button instead of a QR.")
            ctk.CTkLabel(
                pending, text=pending_text,
                text_color=BAD, font=F(12, "bold"),
                wraplength=410, justify="center").pack(padx=14, pady=12)
            dest_ctx["qr_pending_card"] = pending
        else:
            self._render_qr_into(qr_slot, swap, dest_ctx)

        # full_str, not fmt: this is the figure the user pays. fmt() rounds to
        # 8 decimals with ROUND_HALF_UP, so on an 18-decimal coin the screen
        # showed a different number from the one the "Copy amount" button puts
        # on the clipboard, and anyone typing what they read sent the wrong
        # amount. full_str()'s own docstring already made this argument for the
        # clipboard path; the display path is the same argument.
        amt = full_str(swap.send_amount)
        has_min = swap.deposit_min is not None
        has_max = swap.deposit_max is not None
        # Only claim a range when the provider actually gave us both ends of
        # one. Trocador reports neither, so the old
        # "(any amount between X and Y works)" line was inferred from two
        # None-checks rather than from anything the provider stated.
        if has_min and has_max:
            rng = f"any amount between {fmt(swap.deposit_min)} and {fmt(swap.deposit_max)} works"
            send_txt = f"Send {amt} {swap.from_coin}\n({rng})"
        elif has_min:
            send_txt = (f"Send {amt} {swap.from_coin}\n"
                        f"(minimum {fmt(swap.deposit_min)} {swap.from_coin})")
        elif has_max:
            send_txt = (f"Send {amt} {swap.from_coin}\n"
                        f"(maximum {fmt(swap.deposit_max)} {swap.from_coin})")
        else:
            send_txt = f"Send exactly {amt} {swap.from_coin}"
        ctk.CTkLabel(win, text=send_txt, font=F(15, "bold"),
                     text_color=ACCENT, justify="center").pack(pady=(0, 10))

        # The min/max checked before "Create swap" came from the earlier
        # displayed quote; this swap's OWN min/max (just returned by the
        # provider) is the authoritative figure that decides whether the
        # deposit actually gets swapped or gets stuck/lost. Re-check against
        # that before the user sends anything.
        out_of_range = (
            swap.send_amount is not None and (
                (swap.deposit_min is not None and swap.send_amount < swap.deposit_min) or
                (swap.deposit_max is not None and swap.send_amount > swap.deposit_max)
            )
        )
        if out_of_range:
            warn_box = card(win, fg_color=BAD_SOFT, border_color=BAD, border_width=1)
            warn_box.pack(fill="x", padx=18, pady=(0, 10))
            ctk.CTkLabel(
                warn_box,
                text="⚠ This amount now falls outside the range above. Sending "
                     "it as-is risks a stuck or lost swap, update the amount "
                     "or contact the provider before depositing.",
                text_color=BAD, font=F(12, "bold"),
                wraplength=410, justify="center").pack(padx=14, pady=10)

        details = card(win)
        details.pack(fill="x", padx=18, pady=(2, 10))
        # The address row's copy button is gated like the big one below, and
        # is not offered at all when the address failed validation: the STOP
        # banner above is the whole answer in that case, and handing over a
        # one-click copy of an address the app has just called malformed is
        # the one thing this window must not do.
        gated_copy_buttons = []
        addr_copy_btn = self._kv(
            details, "Deposit address", swap.deposit_address,
            copy=deposit_addr_ok, timers=win_timers, gated=True)
        if addr_copy_btn is not None:
            gated_copy_buttons.append(addr_copy_btn)
        # Chunked rendering of the SAME address, for character-by-character
        # visual verification against the provider's own site (the GUI
        # equivalent of the CLI's chunked-display + retype check). Display
        # only: the copy button above copies the un-chunked address.
        chunk_row = ctk.CTkFrame(details, fg_color="transparent")
        chunk_row.pack(fill="x", padx=16, pady=(0, 4))
        ctk.CTkLabel(chunk_row, text=chunk_addr(swap.deposit_address),
                     text_color=MUTED, font=F(11), wraplength=400,
                     justify="left").pack(anchor="w")
        if swap.deposit_memo:
            memo_copy_btn = self._kv(
                details, "Deposit MEMO/tag (required!)", swap.deposit_memo,
                copy=True, warn=True, timers=win_timers, gated=True)
            if memo_copy_btn is not None:
                gated_copy_buttons.append(memo_copy_btn)
        if swap.estimated_receive is not None:
            self._kv(details, "Estimated receive",
                     f"{fmt(swap.estimated_receive)} {swap.to_coin}")
        self._kv(details, "Destination", swap.settle_address)
        if swap.expires_at:
            # Warn-styled, like the memo row. The CLI prints "Do not fund this
            # address after that time; get a fresh quote instead" next to the
            # same value; rendering it here as an ordinary grey detail row
            # made the deadline read as metadata rather than as an
            # instruction. Funding an expired order is a support ticket at
            # best.
            self._kv(details, "Expires (do not fund after this time)",
                     swap.expires_at, warn=True)
        self._kv(details, "Order ID", swap.order_id, copy=True, last=True,
                 timers=win_timers)

        # Memo hard-stop, GUI equivalent of preflight.py's typed CONFIRM
        # acknowledgment: funds sent without this memo are typically
        # unrecoverable, so the action buttons below stay disabled until
        # the user explicitly ticks that they've seen and will include it.
        # Scoped as a local var (not self.), since more than one deposit
        # window can be open at once.
        memo_ack_var = ctk.BooleanVar(value=not bool(swap.deposit_memo))
        if swap.deposit_memo:
            ack_row = card(win, fg_color=BAD_SOFT, border_color=BAD, border_width=1)
            ack_row.pack(fill="x", padx=18, pady=(0, 8))
            checkbox(ack_row,
                    "I will include the EXACT memo/tag above with my deposit "
                    "(funds sent without it are typically unrecoverable)",
                    memo_ack_var, text_color=BAD, font=F(12, "bold")
                    ).pack(padx=12, pady=10, anchor="w")

        # On-screen verification gate (GUI equivalent of the CLI's retype-
        # last-6 check): require an explicit tick that the deposit address on
        # screen was checked against the provider's own site before the copy
        # buttons unlock. A clipboard manager, QR/render glitch, or shoulder-
        # swapped address is exactly what this forces a human to catch.
        #
        # Only offered when the returned deposit address passes the format
        # check. When it doesn't, the STOP banner above is the whole answer
        # and there is nothing here worth acknowledging: ticking a box to
        # unlock "copy" on an address the app has just said is malformed is
        # the one path this window should not offer.
        addr_ack_var = ctk.BooleanVar(value=False)
        if deposit_addr_ok:
            addr_ack_row = card(win, border_color=BORDER, border_width=1)
            addr_ack_row.pack(fill="x", padx=18, pady=(0, 8))
            checkbox(addr_ack_row,
                    "I've verified the deposit address above (chunked form) "
                    "character-by-character against the provider's own site/app",
                    addr_ack_var, text_color=TEXT, font=F(12)
                    ).pack(padx=12, pady=10, anchor="w")

        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack(fill="x", padx=18, pady=6)
        copy_deposit_btn = primary_button(
            btns, "Copy deposit address",
            lambda: self._copy(swap.deposit_address, win_timers), height=38)
        copy_deposit_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))
        copy_amount_btn = secondary_button(
            btns, "Copy amount",
            lambda: self._copy(full_str(swap.send_amount), win_timers), height=38)
        copy_amount_btn.pack(side="left", expand=True, fill="x", padx=(4, 0))

        def _sync_buttons(*_a):
            # Both gates must be satisfied: the memo acknowledgment (auto-true
            # when there's no memo) AND the on-screen address verification.
            # A destination mismatch reported after creation overrides both.
            ready = (memo_ack_var.get() and addr_ack_var.get()
                     and not dest_ctx["blocked"] and not dest_ctx["expired"])
            state = "normal" if ready else "disabled"
            copy_deposit_btn.configure(state=state)
            copy_amount_btn.configure(state=state)
            # Every per-row copy button that carries a value out of this
            # window is driven by the same gate, so there is no second,
            # ungated route to the address or the memo.
            for _b in gated_copy_buttons:
                with contextlib.suppress(Exception):
                    _b.configure(state=state)
        memo_ack_var.trace_add("write", _sync_buttons)
        addr_ack_var.trace_add("write", _sync_buttons)
        dest_ctx["sync"] = _sync_buttons
        _sync_buttons()

        status_lbl = ctk.CTkLabel(win, text=f"Status: {swap.status}",
                                  font=F(15, "bold"), text_color=TEXT)
        status_lbl.pack(pady=(16, 4))
        hint = ctk.CTkLabel(win, text="Waiting for your deposit to appear on-chain…",
                            text_color=MUTED, font=F(12), wraplength=410)
        hint.pack()

        self._safe_configure_create_btn(state="normal", text="Create swap")
        if deposit_addr_ok:
            self._set_status(f"Swap created with {swap.provider}. Fund the "
                             f"deposit address shown.", GOOD)
        else:
            self._set_status(f"⚠ {swap.provider} returned a deposit address "
                             f"that doesn't look valid for {swap.from_coin}. "
                             f"Do not send funds until you've verified it "
                             f"independently.", BAD)
        self._start_polling(swap, status_lbl, hint, win, win_timers, dest_ctx)

    def _deposit_render_failed(self, swap, exc):
        """Last-resort surface for an exception raised while building the
        deposit window.

        The swap already exists at the provider and the history row is
        already written by the time the window is built, so an exception
        here used to leave a half-drawn, unpollable window, a Create button
        stuck on "Creating...", and no indication that a real order is
        outstanding. Whatever else fails, the order id has to reach the user:
        it is the only handle they have on funds the provider is now holding
        a channel open for.
        """
        with contextlib.suppress(Exception):
            self._safe_configure_create_btn(state="normal", text="Create swap")
        with contextlib.suppress(Exception):
            self._set_status(
                f"Swap WAS created with {swap.provider} (order id: "
                f"{swap.order_id}) but this window could not be drawn "
                f"({type(exc).__name__}). Do not send anything until you have "
                f"looked the order up on {swap.provider}'s own site. It is "
                f"also saved in your History tab.", BAD)

    def _start_polling(self, swap, status_lbl, hint, win, win_timers,
                       dest_ctx=None):
        # Each swap/window gets its OWN stop event. A shared one would mean
        # closing any deposit window silently kills polling for every other
        # swap still in flight.
        stop_event = threading.Event()
        last_status = swap.status  # updated only on a successful poll

        def loop():
            nonlocal last_status
            # .get(), not [], and resolved before the loop: a missing name
            # here raised KeyError on this thread, which killed polling
            # before the first request. The window then sat on its
            # creation-time status forever, which reads as a stuck swap
            # rather than as a lookup that failed. A name can be missing
            # when a swap outlives the provider that made it, e.g. reopening
            # one created by a provider a later build no longer ships.
            p = self.providers.get(swap.provider)
            if p is None:
                self._post(self._update_status, swap, prov.STATUS_UNKNOWN,
                           status_lbl, hint)
                return
            while not stop_event.is_set():
                try:
                    st = p.get_status(swap.order_id)
                    last_status = st
                except Exception:  # noqa: BLE001 - background polling
                    # thread; any provider/network failure (timeout, bad
                    # JSON, unexpected status shape) must not kill the loop,
                    # and there's no single narrower type to catch across
                    # every provider's get_status implementation.
                    #
                    # Transient error: report the last status we actually
                    # confirmed, not the swap's original creation-time
                    # status (which never updates and would look like the
                    # swap regressed to "Waiting" on a single flaky poll).
                    st = last_status
                self._post(self._update_status, swap, st, status_lbl, hint,
                           dest_ctx)
                # Deferred destination check: compare as soon as the
                # provider reports one. Read here, applied on the Tk thread.
                if (dest_ctx is not None and dest_ctx["deferred"]
                        and dest_ctx["status"] == "unverified"):
                    echoed = getattr(p, "destination_echo", lambda _oid: None)(
                        swap.order_id)
                    if echoed is not None:
                        self._post(self._apply_destination_echo, swap,
                                   echoed, dest_ctx)
                if st in prov.TERMINAL_STATUSES:
                    break
                stop_event.wait(15)

        # stop polling only for THIS window when it's closed, and cancel the
        # clipboard-clear timers armed from it so they can't fire later and
        # wipe clipboard contents unrelated to this swap. Only
        # this window's timers: another open deposit window's pending
        # clears have to survive this one closing.
        win.protocol("WM_DELETE_WINDOW",
                     lambda: (stop_event.set(),
                              self._cancel_clipboard_timers(win_timers),
                              win.destroy()))
        threading.Thread(target=loop, daemon=True).start()

    def _update_status(self, swap, st, status_lbl, hint, dest_ctx=None):
        color = STATUS_COLORS.get(st, TEXT)
        if dest_ctx is not None:
            self._lock_if_expired(swap, dest_ctx)
        # This runs via self.after(), so a poll result can land AFTER the user
        # has closed the deposit window and its widgets were destroyed.
        # Touching a destroyed widget raises (a noisy stderr traceback via
        # Tk's callback handler); guard it. The history update below still
        # runs so the last polled status is persisted regardless.
        widgets_alive = True
        try:
            widgets_alive = bool(status_lbl.winfo_exists() and hint.winfo_exists())
        except Exception:  # noqa: BLE001 - a destroyed-widget error must
            # flip widgets_alive to False (the opposite of the pre-try
            # default), so contextlib.suppress isn't equivalent here; Tk
            # doesn't document a narrower exception for a torn-down widget.
            widgets_alive = False
        if widgets_alive:
            status_lbl.configure(text=f"Status: {st}", text_color=color)
            messages = {
                prov.STATUS_WAITING: "Waiting for your deposit to appear on-chain…",
                prov.STATUS_CONFIRMING: "Deposit seen: waiting for confirmations…",
                prov.STATUS_EXCHANGING: "Exchanging your coins…",
                prov.STATUS_SENDING: "Sending the output to your destination address…",
                prov.STATUS_COMPLETE: "Done. The coins were sent to your address.",
                prov.STATUS_REFUNDED: "The swap was refunded to your refund address.",
                prov.STATUS_REFUNDING: (
                    "The swap did not go through. Your deposit is being sent "
                    "back to your refund address; SwapDesk keeps checking "
                    "until the refund is on-chain."),
                prov.STATUS_FAILED: "The swap failed. Check the provider dashboard.",
                prov.STATUS_EXPIRED: "The order expired without a deposit.",
                prov.STATUS_PARTIAL: (
                    "Only part of the expected deposit arrived. This trade is "
                    "NOT finished. Open your order on the provider's site "
                    f"(order id: {swap.order_id}) to see whether it needs a "
                    "top-up or a refund. SwapDesk will keep checking here too."),
                prov.STATUS_NEEDS_ACTION: (
                    f"{swap.provider} has paused this swap and needs something "
                    f"from you on its own site, order id: {swap.order_id}. Your "
                    f"funds aren't lost, but nothing here moves until that is "
                    f"done. SwapDesk will keep checking and update once it is."),
                prov.STATUS_UNKNOWN: "SwapDesk can't auto-track this one.",
            }
            text = messages.get(st, "")
            # The deposit deadline is shown as an instruction ("do not fund
            # after this time") in the details card above, and then nothing
            # ever checked it against the clock: a window left open past it
            # kept saying "Waiting for your deposit to appear on-chain",
            # which reads as "still fine to send". Chainflip is the sharp
            # case -- the deadline is computed locally from its documented
            # 24-hour channel lifetime and BaaS keeps reporting `waiting`
            # afterwards, so the provider status never contradicts it.
            if st not in prov.TERMINAL_STATUSES and _expiry_passed(swap.expires_at):
                expired_note = (
                    f"The deposit window closed at {swap.expires_at}. Do not "
                    f"send funds now: start a new swap instead. If you have "
                    f"already sent, look this order up on {swap.provider}'s "
                    f"own site (order id: {swap.order_id}).")
                text = f"{expired_note}\n\n{text}" if text else expired_note
            p = self.providers.get(swap.provider)
            detail = None
            if p is not None:
                with contextlib.suppress(Exception):
                    detail = p.status_detail(swap.order_id)
            if detail:
                text = f"{text}\n\n{detail}" if text else detail
            hint.configure(text=text)
        # Locked read-modify-write: a raw read/write loses an update when
        # two poll threads land at the same time.
        # Only redraw the history list when the stored status actually
        # moved. Polling reports the same status most ticks, and a redraw
        # tears down and rebuilds every row in the list.
        if cfg.update_history_status(swap.order_id, st):
            self._refresh_history()

    def _lock_if_expired(self, swap, dest_ctx):
        """Withdraw the QR and the copy buttons once the deposit deadline has
        passed; the status text already says not to send. Runs on the Tk
        thread, once. For THORChain/Maya the deadline is the quote expiry,
        after which the vault may rotate."""
        if dest_ctx.get("expired_locked") or not (
                dest_ctx.get("expired") or _expiry_passed(swap.expires_at)):
            return
        dest_ctx["expired"] = dest_ctx["expired_locked"] = True
        if dest_ctx.get("qr") is not None:
            with contextlib.suppress(Exception):
                dest_ctx["qr"].destroy()
            dest_ctx["qr"] = None
        if dest_ctx.get("sync") is not None:
            with contextlib.suppress(Exception):
                dest_ctx["sync"]()

    def _apply_destination_echo(self, swap, echoed, dest_ctx):
        """Settle a deferred destination check (runs on the Tk thread).

        Same comparison as at creation. A match turns the unverified card
        into a confirmation; a mismatch locks every copy button in the
        window for good and says so, because the order now exists with a
        destination the user did not approve.
        """
        if dest_ctx["status"] != "unverified":
            return
        swap.settle_address_confirmed_by_provider = echoed
        status, message = preflight.verify_provider_confirmed_destination(
            swap.provider, swap, swap.settle_address)
        if status == "unverified":
            return
        dest_ctx["status"] = status
        if status == "mismatch":
            dest_ctx["blocked"] = True
            if dest_ctx["sync"] is not None:
                with contextlib.suppress(Exception):
                    dest_ctx["sync"]()
            # The QR is not behind the acknowledgment gate, so it goes too.
            if dest_ctx["qr"] is not None:
                with contextlib.suppress(Exception):
                    dest_ctx["qr"].destroy()
                dest_ctx["qr"] = None
            text = (f"⚠ STOP, DO NOT SEND FUNDS\n\n{message}\n\nOrder id: "
                    f"{swap.order_id}. If you already sent, contact "
                    f"{swap.provider} with that id.")
            self._set_status(f"{swap.provider} reported a different destination "
                             f"than the one you approved. Do not send funds.", BAD)
            colors = {"fg_color": BAD_SOFT, "border_color": BAD, "border_width": 2}
            text_color = BAD
        else:
            text = f"✓ {message}"
            colors = {"fg_color": GOOD_SOFT, "border_color": GOOD}
            text_color = GOOD
            # The QR was withheld pending exactly this confirmation (see the
            # QR-suppression block in _build_deposit_window). Render it now,
            # replacing the "withheld" placeholder, the same way the copy
            # buttons unlock via _sync_buttons() above -- this is the other
            # half of that gate, not an optional extra.
            pending = dest_ctx.get("qr_pending_card")
            slot = dest_ctx.get("qr_slot")
            if (dest_ctx.get("qr_eligible") and dest_ctx.get("qr") is None
                    and slot is not None and not dest_ctx.get("expired")):
                with contextlib.suppress(Exception):
                    if pending is not None and pending.winfo_exists():
                        pending.destroy()
                dest_ctx["qr_pending_card"] = None
                with contextlib.suppress(Exception):
                    if slot.winfo_exists():
                        self._render_qr_into(slot, swap, dest_ctx)
        with contextlib.suppress(Exception):
            if dest_ctx["card"] is not None and dest_ctx["card"].winfo_exists():
                dest_ctx["card"].configure(**colors)
                dest_ctx["label"].configure(text=text, text_color=text_color)

    def _open_site(self, url: str):
        """Put a provider URL on the clipboard. Deliberately does NOT open it.

        webbrowser.open() would launch the SYSTEM DEFAULT browser, which has
        no way to honor the app's SOCKS5/Tor setting. For an app whose whole
        point is that its traffic can be routed
        through Tor, a button that silently sends a request from the user's real
        IP, with their default browser's full fingerprint and cookie jar, to a
        site that is about to be handed their API key, is the single loudest
        deanonymising action in the UI. It also correlates "this person runs
        SwapDesk" with "this browser profile" at the provider, permanently.

        Warning and then opening it anyway would only report what had already
        happened. Handing over the URL costs one paste and leaves the decision
        (Tor Browser? a throwaway profile? not at all?) where it belongs.

        Nothing else in the codebase launches an external process for a URL,
        and nothing should: this is the one entry point, so the guarantee holds
        as long as it does.
        """
        self._copy(url)
        # settings_msg, not _set_status: every caller is a provider link on the
        # Settings tab, and status_line lives on the Swap tab, so the
        # confirmation landed on a label the user could not see and the button
        # read as dead.
        msg = f"Link copied, open it in the browser of your choice: {url}"
        if getattr(self, "settings_msg", None) is not None:
            self.settings_msg.configure(text=msg, text_color=ACCENT)
        else:
            self._set_status(msg, ACCENT)

    def _render_qr_into(self, parent, swap: prov.Swap, dest_ctx: dict):
        """Build the deposit-address QR into `parent` and remember its frame
        on dest_ctx["qr"] (so a later confirmed mismatch can tear it down).

        Shared by the initial render (a non-deferred provider, or a deferred
        one whose destination was already confirmed at creation time) and by
        _apply_destination_echo below (a deferred-echo provider's later poll
        landing a confirmed match, rendering the QR retroactively). Any
        failure degrades to "no QR", never a broken window on a funds screen
        -- see the DataOverflowError note this mirrors from the previous
        single-call-site version of this code.
        """
        qr_img = None
        try:
            qr_img = self._make_qr(swap.deposit_address)
        except Exception as qr_err:  # noqa: BLE001 - any failure here
            # (capacity, a missing optional dependency, an imaging error)
            # must degrade to "no QR", never to a broken window.
            warn_card = card(parent, fg_color=BAD_SOFT, border_color=BAD,
                             border_width=1)
            warn_card.pack(fill="x", padx=18, pady=(10, 4))
            ctk.CTkLabel(
                warn_card,
                text=("QR code could not be generated (" +
                      type(qr_err).__name__ + "). Use the copy button "
                      "below instead, and verify the address on the "
                      "provider's own site."),
                text_color=BAD, font=F(12), wraplength=410,
                justify="center").pack(padx=14, pady=12)
            return
        win = parent.winfo_toplevel()
        win._qr_img_ref = qr_img   # window-scoped ref; freed on close
        qr_frame = ctk.CTkFrame(parent, fg_color="#ffffff",
                                corner_radius=RADIUS_MD)
        qr_frame.pack(pady=16)
        ctk.CTkLabel(qr_frame, image=qr_img, text="").pack(padx=14, pady=14)
        dest_ctx["qr"] = qr_frame

    def _make_qr(self, data: str):
        # Imported here, not at module load: qrcode/PIL are only needed once
        # a deposit address exists to render, not on every app launch.
        import qrcode
        from PIL import Image
        img = qrcode.make(data)
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        pil = Image.open(buf)
        # Caller must keep a reference for the lifetime of the widget showing
        # it (else Tk GCs the image). We attach it to the deposit window (see
        # _show_deposit) so it's freed when that window closes, instead of a
        # process-lifetime list that grew by one image per swap forever.
        return ctk.CTkImage(light_image=pil, dark_image=pil, size=(220, 220))

    def _kv(self, parent, key, value, copy=False, warn=False, wrap=400,
            last=False, timers=None, gated=False):
        """One key/value row, optionally with its own small copy button.

        `gated` marks that button as belonging to the acknowledgment gate:
        it is created disabled and the caller must register the returned
        widget with _sync_buttons(). The two large action buttons at the
        bottom of the deposit window were gated from the start, but these
        per-row buttons were not, so the memo and address-verification
        acknowledgments could both be walked around by clicking the small
        `copy` beside the address instead of the big one below it. Any row
        carrying a value that must not leave this window before the user has
        acknowledged something has to pass gated=True.
        """
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(fill="x", padx=16, pady=(10, 0 if last else 10))
        ctk.CTkLabel(f, text=key, text_color=BAD if warn else FAINT,
                     font=F(11, "bold" if warn else "normal")).pack(anchor="w")
        line = ctk.CTkFrame(f, fg_color="transparent")
        line.pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(line, text=value, wraplength=wrap, justify="left",
                     text_color=TEXT, font=F(12)).pack(side="left", anchor="w")
        btn = None
        if copy:
            btn = secondary_button(line, "copy",
                                   lambda: self._copy(value, timers),
                                   width=56, height=24, font=F(10))
            if gated:
                btn.configure(state="disabled")
            btn.pack(side="right")
        return btn

    def _copy(self, text: str, timers: list | None = None):
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status("Copied to clipboard. (auto-clears in 45s)", GOOD)
        # Don't leave deposit addresses/memos sitting in the clipboard
        # indefinitely: many clipboard managers (incl. Windows 11's) persist
        # history across reboots, which is a bigger, longer-lived leak than
        # anything SwapDesk's own file permissions can control. Only clear
        # if the clipboard still holds exactly what we put there, so we
        # don't stomp something the user copied from elsewhere in the
        # meantime.
        after_id = self.after(45_000, lambda: self._maybe_clear_clipboard(text))
        # `timers` is the owning deposit window's own list. Several deposit
        # windows can be open at once; cancelling per-instance would kill
        # timers armed by the others and leave their addresses in the
        # clipboard indefinitely, the exact leak this exists to prevent.
        # Copies with no owning
        # window (the Settings provider links) fall back to the instance
        # list, which nothing cancels early.
        if timers is None:
            timers = self._clipboard_after_ids = getattr(
                self, "_clipboard_after_ids", [])
        timers.append(after_id)
        return after_id

    def _cancel_clipboard_timers(self, timers: list | None = None):
        """Cancel pending clipboard-clear timers. Called when a deposit
        window closes early so a stale timer can't fire later and clear
        clipboard contents the user has since copied for something else.

        Cancels only `timers` when given, so one window closing leaves other
        open deposit windows' timers armed.
        """
        if timers is None:
            timers = getattr(self, "_clipboard_after_ids", [])
        for after_id in timers:
            with contextlib.suppress(Exception):
                self.after_cancel(after_id)
        timers.clear()

    def _maybe_clear_clipboard(self, expected: str):
        with contextlib.suppress(Exception):  # clipboard empty or unreadable
            if self.clipboard_get() == expected:
                self.clipboard_clear()
