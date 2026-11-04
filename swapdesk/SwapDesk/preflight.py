"""
preflight.py: a mandatory safety gate in front of create_swap().

Why this exists
----------------
Reading the code proves it builds the *right* request given a canned
response. It can't catch a live-API surprise, a typo in an address you
paste, or a moment of "I'll just trust the UI" before funds move. The one
failure mode that matters here, a wrong-chain send or a mangled memo, is
typically unrecoverable, not a bug you patch and refund. This module is the
last independent check before that happens, and it is deliberately NOT part
of the providers package: it must reason
about the request using information create_swap() doesn't have access to
(what you originally typed, what's in history, what the quote said a moment
ago) rather than just trusting the same variables create_swap() already
trusts.

What it checks, and why each one is here
-----------------------------------------
1. Address format sanity (config.address_looks_valid), catches paste
   errors/wrong-coin addresses before they reach a provider.
2. Coin/network pair is actually supported by the chosen provider, a
   provider that silently no-ops or errors deep in create_swap is better
   caught here, before you've committed to depositing.
3. Echo-back confirmation: prints the EXACT outbound settle_address and
   refund_address for you to visually diff against what you meant to type,
   because a variable getting silently reused/overwritten upstream (in your
   own calling code, not this app) would otherwise be invisible.
4. Address-reuse warning: flags if this destination was used before
   (privacy leak on transparent chains; also worth a second look in case
   it's not the address you meant to use this time).
5. Dry-run mode by default, get_quote() only, never create_swap(), unless
   you pass execute=True AND type the literal confirmation phrase. No
   flag-only bypass; the confirmation string must be typed interactively
   (or passed explicitly with an unambiguous CLI flag) so a script can't
   accidentally set execute=True and fire for real.
6. Post-creation verification: after a real swap is created, re-checks
   the destination against Swap.settle_address_confirmed_by_provider,
   which each provider populates from something it independently read
   back out of its own API response (SideShift's settleAddress,
   ChangeNOW's payoutAddress, FixedFloat's to.address).
   This is deliberately NOT a comparison against Swap.settle_address:
   that field is just the local variable echoed straight back through
   every provider's create_swap(), so comparing it to itself can never
   catch anything. When a provider's response has no independently-
   readable field, the check degrades to an explicit warning rather than
   a silent pass.

This is a safety NET, not a safety GUARANTEE. It cannot verify that the
provider's live API is behaving as documented, and it cannot verify your
clipboard/typing was correct. It can only verify internal consistency and
known-format sanity. Independent audit and small real-fund dogfooding are
still the only ways to close the remaining gaps.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from decimal import Decimal

import config
import providers as p

CONFIRM_PHRASE = "SEND REAL FUNDS"

import re as _re

_EVM_ADDR = _re.compile(r"^0x[0-9a-fA-F]{40}$")


def _addr_equal(a: str, b: str) -> bool:
    """Compare two destination addresses for the post-create echo check.

    EVM addresses (0x + 40 hex) are compared case-INSENSITIVELY: EIP-55
    checksum casing is cosmetic. The same address may be typed lowercase
    by the user and echoed back checksummed by the provider (SideShift/
    ChangeNOW routinely re-checksum), and a byte-exact compare would then
    fire a false "SAFETY ABORT" on a perfectly correct address, blocking
    legitimate ETH/USDC swaps and training users to distrust the very check
    meant to catch real tampering. For everything else (base58/bech32) case
    IS significant (a different case is a genuinely different address) so
    those stay exact."""
    if a is None or b is None:
        return a == b
    # Surrounding whitespace is stripped for every address type, not just
    # EVM. No address format treats a leading or trailing space as part of
    # the address, so a provider echoing "addr " is echoing the same
    # address; comparing it byte-exact would fire the false SAFETY ABORT
    # this function exists to avoid. Stripping cannot hide real tampering:
    # a different address is still different once trimmed.
    a, b = a.strip(), b.strip()
    if _EVM_ADDR.match(a) and _EVM_ADDR.match(b):
        return a.lower() == b.lower()
    return a == b


@dataclass
class PreflightResult:
    ok: bool
    checks: list = field(default_factory=list)   # list of (label, passed, detail)
    warnings: list = field(default_factory=list)

    def add(self, label: str, passed: bool, detail: str = ""):
        self.checks.append((label, passed, detail))
        if not passed:
            self.ok = False

    def warn(self, msg: str):
        self.warnings.append(msg)

    def report(self) -> str:
        lines = []
        for label, passed, detail in self.checks:
            mark = "PASS" if passed else "FAIL"
            lines.append(f"  [{mark}] {label}" + (f": {detail}" if detail else ""))
        for w in self.warnings:
            lines.append(f"  [WARN] {w}")
        lines.append("")
        lines.append("RESULT: " + ("all checks passed" if self.ok else "BLOCKED, fix failures above"))
        return "\n".join(lines)


def _warn_on_reuse_enabled() -> bool:
    """Settings > Privacy > "Warn if a destination address was used in a
    previous swap". Read from disk rather than passed in, because
    run_preflight() is called from both the GUI (which holds a live config
    dict) and the CLI (which doesn't), and reading it here keeps the two
    paths identical. A read failure defaults to True: this is a privacy
    warning, so the safe direction is to show it."""
    try:
        return bool(config.load_config().get("privacy", {})
                    .get("warn_on_address_reuse", True))
    except Exception:  # noqa: BLE001 - an unreadable/corrupt config must not
        # take down the preflight gate; fall back to warning.
        return True


def run_preflight(provider: p.SwapProvider, from_coin: str, to_coin: str,
                   amount: Decimal, settle_address: str,
                   refund_address: str = "") -> PreflightResult:
    """Independent verification pass. Does NOT call create_swap. Safe to
    call as many times as you want."""
    r = PreflightResult(ok=True)

    from_coin, to_coin = from_coin.upper(), to_coin.upper()

    # 1. Address format sanity for the DESTINATION (to_coin). This is the
    #    field that decides where funds land, so it gets the strictest check.
    valid = config.address_looks_valid(to_coin, settle_address)
    r.add(f"settle_address format looks like a valid {to_coin} address",
          valid, settle_address if not valid else "")
    # A coin with no curated regex (SOL has a decode check; anything else only
    # gets address_looks_valid's near-no-op length>=16 fallback) has almost no
    # protection against a truncated/wrong-coin paste on the one field that
    # decides where funds land. A warning here would let such a coin be
    # swapped to with essentially no format validation, so it is a hard FAIL
    # unless you've explicitly vouched for this exact address before (address
    # book), which is the one signal that the address is actually yours
    # rather than merely long enough.
    has_curated_check = config.has_curated_pattern(to_coin)
    if not has_curated_check:
        # is_confirmed_address, NOT bool(is_in_address_book(...)): the latter
        # returns the saved LABEL, which is "" (falsy) for an address saved
        # without one. That made this hard FAIL unclearable by the very
        # action its own failure text tells the user to take.
        confirmed_before = config.is_confirmed_address(to_coin, settle_address)
        r.add(f"{to_coin} has no built-in address format check. Destination "
              f"must be a saved, previously-confirmed address before sending",
              confirmed_before,
              "" if confirmed_before else
              f"{to_coin} isn't in the curated format list, so this app can't "
              f"meaningfully validate the address shape. Add it to your address "
              f"book with config.add_to_address_book() only after verifying it "
              f"character-by-character against your wallet, then retry.")

    # 2. Refund address, if given, should look like a FROM_COIN address
    #    (refunds return the FROM coin, not the TO coin, mixing these up
    #    is a classic copy-paste mistake).
    if refund_address:
        rvalid = config.address_looks_valid(from_coin, refund_address)
        r.add(f"refund_address format looks like a valid {from_coin} address",
              rvalid, refund_address if not rvalid else "")

    # 3. settle_address and refund_address must not be identical, a common
    #    accidental double-paste that would send BOTH the swap output and
    #    any refund to the same string, which is only correct by coincidence.
    if refund_address and _addr_equal(settle_address, refund_address):
        r.add("settle_address and refund_address are different values",
              False, "both fields contain the identical address")
    elif refund_address:
        r.add("settle_address and refund_address are different values", True)

    # 4. Provider actually supports this pair, ask it directly rather than
    #    assuming; this mirrors what create_swap will check but surfaces it
    #    BEFORE you've typed the confirmation phrase.
    supported = _pair_support_state(provider, from_coin, to_coin) == "yes"
    r.add(f"{provider.name} supports {from_coin} -> {to_coin}",
          supported,
          "" if supported else "pair not in this provider's coin/asset table")

    # 4b. A pair can be routed and still not be fundable: THORChain/Maya
    #     deposits from some chains need a transaction this app cannot build.
    source_reason = getattr(provider, "unsupported_source_reason", None)
    if callable(source_reason):
        reason = source_reason(from_coin)
        if reason:
            r.add(f"{provider.name} can take {from_coin} as the coin you send",
                  False, reason)

    # 5. Provider is configured (has credentials), a clearer failure than
    #    whatever create_swap would raise.
    r.add(f"{provider.name} is configured (API key/secret present)",
          provider.configured())

    # 6. Address-reuse warning (not a failure, informational). Gated on the
    #    Settings > Privacy checkbox of the same name; read here so the
    #    toggle actually governs the warning. Default True, to match
    #    DEFAULT_CONFIG, so an existing
    #    config that predates the control keeps warning.
    if _warn_on_reuse_enabled():
        prior = config.previously_used_destination(settle_address)
        if prior:
            r.warn(f"This destination address was used before "
                   f"(provider={prior.get('provider')}, order={prior.get('order_id')}). "
                   f"Reusing addresses links transactions together on transparent "
                   f"chains (BTC/LTC/DOGE/etc), fine if intentional.")

    # 6a-ii. Zcash shielded destinations. The address is well-formed and this
    # app has no way to ask a provider "can you pay out to zs1/u1?", so this
    # is a warning, not a failure: the answer varies per provider and per
    # route, and blocking it would push a privacy user back to a transparent
    # address, which defeats the point of choosing Zcash.
    if to_coin.upper() == "ZEC" and settle_address[:2].lower() in ("zs", "u1"):
        r.warn("This is a SHIELDED Zcash address. Not every provider can "
               "settle to one; some only pay out to transparent t1/t3 "
               "addresses, and a swap that can't pay out has to be refunded "
               "rather than completed. Confirm on the provider's own site "
               "that shielded payout is supported before sending, or use a "
               "t-address if you're unsure.")

    # 6a-iii. Pirate Chain and Zcash share the Sapling 'zs1' address format,
    # so neither coin's pattern can tell one from the other. Anyone holding
    # both is a paste away from sending ARRR to a ZEC address or the reverse,
    # and that is unrecoverable. A regex cannot help, so say it plainly.
    if (to_coin.upper() in ("ARRR", "ZEC")
            and settle_address[:3].lower() == "zs1"):
        other = "Zcash" if to_coin.upper() == "ARRR" else "Pirate Chain"
        r.warn(f"This is a Sapling 'zs1' address, and {other} uses the exact "
               f"same format. SwapDesk cannot tell a {to_coin.upper()} "
               f"address apart from a {other} one, so check you copied it "
               f"from your {to_coin.upper()} wallet. Sending to the wrong "
               f"chain's address is unrecoverable.")

    # 6a-iiib. Legacy Base58 addresses whose version byte several supported
    # chains share. A '3...' P2SH address is valid on BTC, LTC and BCH alike
    # (all three used version 0x05), and a '1...' P2PKH one on BTC and BCH, so
    # the address itself carries nothing that says which chain it belongs to.
    # This is the same shape of hazard as the ARRR/ZEC 'zs1' collision above
    # and gets the same treatment: the address stays VALID (it is), and the
    # user is told exactly what it could be confused with plus the modern
    # format that removes the doubt. Failing it would bounce correct
    # addresses; staying silent would let a deposit land on the wrong chain.
    also_valid_on = config.legacy_address_ambiguity(to_coin, settle_address)
    if also_valid_on:
        def _join(items, conj):
            if len(items) == 1:
                return items[0]
            return ", ".join(items[:-1]) + f" {conj} " + items[-1]
        others = _join(also_valid_on, "and")
        others_or = _join(also_valid_on, "or")
        hint = config.unambiguous_format_hint(to_coin)
        r.warn(f"AMBIGUOUS ADDRESS: this is a legacy Base58 address, and the "
               f"exact same string is an equally valid address on {others}. "
               f"Nothing in the address itself says which chain it belongs "
               f"to, so SwapDesk cannot check that it is really a {to_coin} "
               f"address. Confirm you copied it from your {to_coin} wallet "
               f"and not from {others_or}. Sending to the wrong chain is "
               f"unrecoverable."
               + (f" Using {hint} instead removes the ambiguity entirely, "
                  f"because the chain is encoded in the address."
                  if hint else ""))

    # 6a-iii. Providers that can demand identity verification.
    #
    # Surfaced here, at the point funds are about to be committed, because
    # it contradicts the reason most people are using this app. A provider
    # that flags a swap can hold the payout pending documents, and by then
    # the deposit is already on-chain and the user's only options are to
    # comply or to argue for a refund. Better to say so while the decision
    # is still free.
    if getattr(provider, "KYC_ON_FLAGGED", False):
        r.warn(f"{provider.name} does not require KYC as a rule, but reserves "
               f"the right to ask for identity verification on swaps it "
               f"flags, which in practice means large or unusual amounts. If "
               f"that happens the payout is held until you comply or agree a "
               f"refund, and your deposit is already sent by then. Use a "
               f"provider without that exception if it matters to you, or "
               f"keep the amount small.")

    # 6a-iv. Providers that require a refund address to create a swap at
    # all (currently just Chainflip: it enforces a minimum accepted price
    # and needs somewhere to return the deposit if the market moves past
    # it). create_swap() already refuses without one, but that only
    # surfaces after the user has typed the confirm phrase; catching it
    # here is the whole point of a pre-flight gate.
    if getattr(provider, "REQUIRES_REFUND_ADDRESS", False) and not refund_address:
        r.add(f"{provider.name} requires a refund_address", False,
              f"{provider.name} enforces a minimum accepted price on every "
              f"swap and refunds the deposit if the market moves past it, "
              f"so it needs a refund address on the {from_coin} chain before "
              f"it will create a swap at all.")

    # 6a-iv-c. Providers that accept a refund address and do not send it.
    if refund_address and getattr(provider, "IGNORES_REFUND_ADDRESS", False):
        r.warn(f"{provider.name} does not accept a refund address at order "
               f"creation, so the one you entered is NOT sent. If this swap "
               f"has to be refunded it goes back to the address you send "
               f"from, which is a problem if that is an exchange withdrawal "
               f"address you do not control. Send from a wallet you own, or "
               f"pick a provider that takes a refund address.")

    # 6a-v. Providers whose API sends the key/addresses via URL query
    # parameters rather than a POST body or header, so they can end up in a
    # reverse-proxy or web-server access log en route (currently Chainflip;
    # see its _params() for why this hasn't simply been switched to match
    # every other provider's approach).
    if getattr(provider, "LOGS_SENSITIVE_DATA_IN_URL", False):
        r.warn(f"{provider.name} sends its API key and your swap addresses "
               f"as URL query parameters rather than in a POST body, so any "
               f"reverse proxy or web server between you and {provider.name} "
               f"can log them. This is a documented constraint of "
               f"{provider.name}'s API (no POST or header-auth option is "
               f"offered), not something this app can avoid the way it "
               f"does for its other providers.")

    # 6b. Address-book check: mitigates the "correctly-typed WRONG address"
    # failure mode. Format validity can't distinguish a typo'd-but-valid
    # address from your real one; a prior explicit confirmation can. This is
    # a WARNING not a FAIL (first-time addresses are legitimate) but it's
    # the strongest signal available that "have you actually verified this
    # is yours before" instead of just "is it shaped correctly".
    # Presence decides; the label is only used to make the PASS line more
    # useful when there is one. An address saved with no label is still a
    # confirmed address (see config.is_confirmed_address).
    if config.is_confirmed_address(to_coin, settle_address):
        label = config.is_in_address_book(to_coin, settle_address)
        r.add("settle_address matches a saved, previously-confirmed address"
              + (f" ({label!r})" if label else " (no label saved)"), True)
    else:
        r.warn(f"settle_address has NOT been confirmed before (not in your "
               f"address book). If this is the first time you're sending to "
               f"this {to_coin} address, verify it character-by-character "
               f"against its original source (your wallet app, not a copy "
               f"from chat/email) before proceeding. Use "
               f"config.add_to_address_book() to save it once you've done so.")

    # 7. Sanity on amount.
    r.add("amount is a positive number", amount is not None and amount > 0,
          str(amount))

    return r


def _pair_support_state(provider: p.SwapProvider, from_coin: str, to_coin: str) -> str:
    """Return "yes" / "no" for whether the provider routes this pair,
    without a network call."""
    if isinstance(provider, p.SideShift):
        return "yes" if (from_coin in provider.networks and to_coin in provider.networks) else "no"
    if isinstance(provider, p.ChangeNow):
        return "yes" if (from_coin in provider.currencies and to_coin in provider.currencies) else "no"
    if isinstance(provider, p.Trocador):
        # A plain yes/no like every other provider now that Trocador's _net()
        # refuses a ticker outside its network table instead of defaulting it
        # to "Mainnet". Absence used to be inconclusive precisely because of
        # that guess; with the guess gone, create_swap would refuse, so a
        # warning-only "unknown" would be softer than the truth.
        return "yes" if (from_coin in provider.networks
                         and to_coin in provider.networks) else "no"
    if isinstance(provider, p.FixedFloat):
        return "yes" if (from_coin in provider.codes and to_coin in provider.codes) else "no"
    if isinstance(provider, p.ZeroExDEX):
        return "yes" if (from_coin in p.EVM_TOKENS and to_coin in p.EVM_TOKENS) else "no"
    if isinstance(provider, p.StealthEX):
        return "yes" if (from_coin in provider.symbols and to_coin in provider.symbols) else "no"
    if isinstance(provider, p.Chainflip):
        return "yes" if (from_coin in provider.assets and to_coin in provider.assets) else "no"
    if isinstance(provider, p._ThorForkProvider):
        # These express support as a pool table (ticker -> CHAIN.SYMBOL)
        # rather than a network map. Without this branch they fell through to
        # the "no" below and every THORChain/Maya swap was blocked at the
        # gate, since the fallback assumes an unrecognised provider routes
        # nothing.
        return "yes" if (from_coin in provider.assets and to_coin in provider.assets) else "no"
    return "no"


# How far the provider's stated deposit amount may drift from what the user
# approved before it is treated as wrong rather than as rounding. Providers
# legitimately adjust the last decimal places (satoshi rounding, fee
# netting); 2% is far wider than any of that and far narrower than a
# misplaced decimal point, which is the failure this catches.
AMOUNT_DRIFT_TOLERANCE = Decimal("0.02")


def verify_provider_deposit_amount(provider_name: str, swap: p.Swap,
                                   approved_amount) -> tuple[str, str]:
    """Check the deposit amount the user is about to send.

    Several providers report the deposit figure in their own create-swap
    response, and SwapDesk shows that figure rather than the requested one
    because the provider may have rounded it. That means a wrong value in
    that field becomes the number on the screen the user pays. A response
    carrying 1.0 for a 0.01 request would have the deposit window instruct
    a 100x overpayment, and nothing else in the flow compares the two.

    Returns (status, message) with status one of:
      "ok"          within tolerance of what was approved
      "unverified"  no usable amount to compare
      "mismatch"    outside tolerance; do not send
    """
    stated = swap.send_amount
    if stated is None or approved_amount is None:
        return "unverified", (
            f"{provider_name} did not state a deposit amount, so the amount "
            f"on screen could not be checked against what you approved.")
    if stated <= 0:
        return "mismatch", (
            f"SAFETY ABORT: {provider_name} reported a deposit amount of "
            f"{stated}, which cannot be correct. Do NOT send funds.")
    drift = abs(stated - approved_amount) / approved_amount
    if drift <= AMOUNT_DRIFT_TOLERANCE:
        return "ok", ""
    return "mismatch", (
        f"SAFETY ABORT: you approved sending {approved_amount} "
        f"{swap.from_coin}, but {provider_name} is asking for {stated} "
        f"{swap.from_coin}. Sending the amount shown would move "
        f"{'more' if stated > approved_amount else 'less'} than you "
        f"intended. Do NOT send funds; start the swap again.")


def verify_provider_confirmed_destination(provider_name: str, swap: p.Swap,
                                          approved_settle_address: str):
    """Pure (no printing, no exceptions) version of the post-creation
    destination check, so the GUI can use the exact same logic as the CLI
    instead of re-implementing it. Returns (status, message) where status
    is one of "ok" / "mismatch" / "unverified". Callers decide what to do
    with each: guarded_create_swap() below raises on "mismatch"; the GUI
    aborts the swap the same way rather than displaying it."""
    confirmed = swap.settle_address_confirmed_by_provider
    if confirmed is not None and not isinstance(confirmed, str):
        return ("mismatch",
                (f"SAFETY ABORT: {provider_name} reported the destination as "
                 f"a {type(confirmed).__name__}, not an address, so it cannot "
                 f"be checked against the one you approved. Do NOT send funds "
                 f"to the deposit address shown."))
    if confirmed is None:
        return ("unverified",
                (f"{provider_name}'s response doesn't expose a field this "
                 f"app can use to independently confirm the destination it "
                 f"recorded. This swap's destination is UNVERIFIED beyond "
                 f"what you typed. Double-check the destination shown for "
                 f"this order on {provider_name}'s own site/app before "
                 f"depositing."))
    if not _addr_equal(confirmed, approved_settle_address):
        return ("mismatch",
                (f"SAFETY ABORT: {provider_name} independently reported the "
                 f"destination as {confirmed!r} but you approved "
                 f"{approved_settle_address!r}. These must match exactly. "
                 f"Do NOT send funds to the deposit address shown. This "
                 f"indicates something altered the destination between "
                 f"approval and order creation."))
    return ("ok", f"{provider_name} independently confirmed the destination.")


def guarded_create_swap(provider: p.SwapProvider, from_coin: str, to_coin: str,
                        amount: Decimal, settle_address: str,
                        refund_address: str = "", execute: bool = False,
                        confirm_phrase: str | None = None) -> p.Swap | None:
    """The only sanctioned way to call create_swap from this safety module.

    - Always runs preflight first and prints the report.
    - If preflight fails, refuses to proceed regardless of `execute`.
    - If execute is False (default), stops after a dry-run quote, no
      create_swap call is made, no deposit address is generated, nothing
      that could be mistaken for "the swap is happening" occurs.
    - If execute is True, ALSO requires confirm_phrase to exactly equal
      CONFIRM_PHRASE ("SEND REAL FUNDS"). This is intentionally not a
      boolean-only gate: a script that flips execute=True by accident
      (e.g. a stray default, a copy-pasted call) still can't fire without
      the literal phrase being present in the call, which is much harder
      to trigger by accident than a bare flag.
    - After a real swap is created, re-verifies the returned
      Swap.settle_address against what you approved and refuses to return
      the swap silently if they don't match (raises instead. This would
      indicate the provider or an intermediate step altered it).
    """
    print(f"\n=== PREFLIGHT: {provider.name} {from_coin.upper()} -> "
          f"{to_coin.upper()}, amount={amount} ===")
    result = run_preflight(provider, from_coin, to_coin, amount,
                           settle_address, refund_address)
    print(result.report())

    if not result.ok:
        print("\nBLOCKED: preflight failed, create_swap will NOT be called.")
        return None

    print(f"\nDestination you approved (settle_address): {settle_address}")
    if refund_address:
        print(f"Refund address you approved:               {refund_address}")

    # Dry run: show the live quote, but never create an order.
    quote = provider.get_quote(from_coin, to_coin, amount, destination=settle_address)
    if quote.ok:
        print(f"\nLive quote: {amount} {from_coin.upper()} -> "
              f"~{quote.estimated_receive} {to_coin.upper()} "
              f"(rate {quote.rate})")
    else:
        print(f"\nLive quote FAILED: {quote.error}")
        print("BLOCKED: not creating a swap against a pair the provider "
              "itself just rejected for a quote.")
        return None

    if not execute:
        print("\nDRY RUN complete: no order created, no deposit address "
              "generated. Pass execute=True + the confirm phrase to "
              "actually create the swap.")
        return None

    if confirm_phrase != CONFIRM_PHRASE:
        print(f"\nBLOCKED: execute=True requires confirm_phrase to be "
              f"exactly {CONFIRM_PHRASE!r}. Refusing to create a real order.")
        return None

    print("\nConfirmed. Creating real swap order now...")
    swap = provider.create_swap(from_coin, to_coin, amount, settle_address, refund_address)

    # Post-creation independent re-verification, via the same helper the GUI
    # uses, see its docstring for why this checks
    # settle_address_confirmed_by_provider rather than swap.settle_address.
    status, message = verify_provider_confirmed_destination(
        provider.name, swap, settle_address)
    if status == "mismatch":
        raise p.ProviderError(message)
    elif status == "unverified":
        print(f"\nWARNING: {message}")

    print(f"\nSwap created: order_id={swap.order_id}")
    if swap.expires_at:
        print(f"Deposit window expires: {swap.expires_at}. Do not fund "
              f"this address after that time; get a fresh quote instead.")

    if not _confirm_deposit_address(swap, interactive=sys.stdin.isatty()):
        # The swap exists at the provider, but the address on this screen
        # could not be confirmed, so this function refuses to hand back a
        # Swap the caller would treat as ready to fund. The order id is
        # printed so the user can still find it on the provider's own site,
        # which is the only place the real deposit address can be re-read.
        raise p.ProviderError(
            f"Deposit address for order {swap.order_id} could not be "
            f"confirmed on screen. Do NOT send funds using the address "
            f"printed above. Look the order up on {provider.name}'s own "
            f"site to read the deposit address from the source, or let the "
            f"order expire and start again.")

    # Memo hard-stop: if this swap requires a memo, force an explicit typed
    # acknowledgment before returning the swap. Mitigates the single most
    # common real-world loss mode for memo/tag-based coins, sending from
    # a wallet/exchange UI that has no memo field, or simply
    # forgetting to paste it. A silently-printed memo is too easy to miss;
    # this makes skipping it a deliberate act, not an accident.
    if swap.deposit_memo:
        print(f"\n{'!' * 70}")
        print("MEMO/TAG REQUIRED: funds sent WITHOUT this memo are "
              "typically UNRECOVERABLE:")
        print(f"\n    {swap.deposit_memo}\n")
        print(f"{'!' * 70}")
        if sys.stdin.isatty():
            ack = input("Type CONFIRM to acknowledge you will include this "
                        "exact memo with your deposit: ").strip()
            if ack != "CONFIRM":
                # Refuses, rather than printing a caution and returning the
                # swap anyway. A gate whose own text calls the memo mandatory
                # has to be able to fail, exactly like the deposit-address
                # retype above it. The order exists at the provider, so its id
                # goes out with the refusal.
                raise p.ProviderError(
                    f"Memo not acknowledged. The swap order exists at "
                    f"{swap.provider} (order id: {swap.order_id}) but this "
                    f"tool will not hand back a swap whose memo requirement "
                    f"has not been confirmed. Look the order up on the "
                    f"provider's own site, or let it expire and start again.")
        else:
            # Non-interactive: nothing can be acknowledged, so say so rather
            # than letting a scripted run pass silently through a gate that
            # exists to make skipping the memo a deliberate act.
            print("NOT INTERACTIVE: memo acknowledgment skipped. This swap "
                  "REQUIRES the memo above; nothing here has verified that "
                  "you will include it.")

    print(f"\nConfirmed settle_address (funds land here): {swap.settle_address}")
    print("\nDouble-check the deposit address above against the provider's "
          "own site/app before sending anything.")
    return swap


def _confirm_deposit_address(swap: p.Swap, interactive: bool = True) -> bool:
    """Mitigates deposit-address corruption between the provider response and
    your sending wallet (clipboard manager, QR scan error, render bug):
    prints the deposit address chunked for easier visual diffing, and, when
    running interactively, requires you to retype the last 6 characters
    before continuing. This does not verify the address is correct (only the
    provider's own site can do that); it only verifies what's ON YOUR SCREEN
    matches what you're about to act on, which is a different and cheaper
    check worth doing anyway.

    Returns True if the deposit address passed both checks, False otherwise.
    The return value is the enforcement: this function used to print a
    MISMATCH warning and return None on every path, so the caller carried on
    and handed back the swap regardless. A gate whose docstring says it
    "requires" something has to be able to fail.
    """
    addr = swap.deposit_address
    # Format check on the DEPOSIT address, matching what the GUI does before it
    # will render a QR or unlock the copy buttons (ui/deposit.py). The CLI had
    # no equivalent, so a malformed address from a provider reached the user
    # here with nothing between them but the retype prompt below.
    if not config.address_looks_valid(swap.from_coin, addr):
        print(f"\nSAFETY ABORT: {swap.provider} returned a deposit address "
              f"that is not a well-formed {swap.from_coin.upper()} address: "
              f"{addr!r}. Do NOT send funds to it. This is a provider-side "
              f"problem; the swap has been created but must not be funded.")
        return False
    chunks = [addr[i:i + 4] for i in range(0, len(addr), 4)]
    print(f"\nDeposit address (send {swap.from_coin.upper()} here), "
          f"chunked for easier visual verification:")
    print("  " + " ".join(chunks))
    print(f"Full: {addr}")
    if interactive:
        tail = addr[-6:]
        typed = input("Retype the LAST 6 CHARACTERS of the address above "
                      "to confirm it displayed correctly on your screen: ").strip()
        if typed != tail:
            print(f"MISMATCH: you typed {typed!r}, address ends in {tail!r}. "
                 f"Re-read the address above carefully before sending. Do "
                 f"not proceed until these match.")
            return False
    return True


# CLI
def _build_provider(name: str) -> p.SwapProvider:
    cfg = config.load_config()
    providers = {pr.name.lower().split()[0]: pr for pr in p.build_providers(cfg)}
    key = name.lower()
    for pname, pr in providers.items():
        if pname.startswith(key) or key.startswith(pname):
            return pr
    raise SystemExit(f"Unknown provider {name!r}. Options: "
                     f"{', '.join(sorted(providers))}")


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="Pre-flight safety check for SwapDesk swaps. Defaults "
                    "to dry-run (quote only, no order created).")
    try:
        version = (config.RESOURCE_DIR / "VERSION").read_text(
            encoding="utf-8", errors="ignore").strip() or "unknown"
    except OSError:
        version = "unknown"
    ap.add_argument("--version", action="version",
                    version=f"SwapDesk preflight {version}")
    ap.add_argument("--provider", required=True,
                    help="sideshift | changenow | trocador | fixedfloat | 0x | "
                         "thorchain | maya | chainflip | stealthex")
    ap.add_argument("--from", dest="from_coin", required=True)
    ap.add_argument("--to", dest="to_coin", required=True)
    ap.add_argument("--amount", required=True, type=Decimal)
    ap.add_argument("--settle", required=True, help="destination address (yours)")
    ap.add_argument("--refund", default="", help="refund address (optional)")
    ap.add_argument("--execute", action="store_true",
                    help="Actually create the swap (default: dry-run quote only).")
    args = ap.parse_args()

    # If the config is encrypted, unlock it before anything reads it
    # (_build_provider -> config.load_config() would otherwise raise
    # ConfigLocked).
    if not config.prompt_and_unlock_cli():
        raise SystemExit("Could not unlock the encrypted config; aborting.")

    provider = _build_provider(args.provider)

    confirm = None
    if args.execute:
        print(f"\nYou are about to create a REAL swap that will generate a "
              f"real deposit address. Type exactly: {CONFIRM_PHRASE}")
        confirm = input("> ").strip()

    swap = guarded_create_swap(provider, args.from_coin, args.to_coin, args.amount,
                               args.settle, args.refund, execute=args.execute,
                               confirm_phrase=confirm)

    # Exit status has to reflect the verdict. This is the safety gate, and it
    # printed BLOCKED and then exited 0, so anything driving it from a script
    # ("preflight ... && send-the-funds") read a refusal as approval.
    #
    # A dry run that passes returns None too (by design: no order is created),
    # so "swap is None" alone can't distinguish refused from fine. The checks
    # that decide the verdict are pure and cheap, so re-running them here is
    # cheaper than threading state back out of guarded_create_swap and keeps
    # that function's contract unchanged for the GUI's sake.
    verdict = run_preflight(provider, args.from_coin, args.to_coin,
                            args.amount, args.settle, args.refund)
    if not verdict.ok:
        raise SystemExit(1)
    if args.execute and swap is None:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
