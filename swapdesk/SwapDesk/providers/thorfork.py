"""providers.thorfork: THORChain and Maya Protocol (decentralized, memo-based).

Fundamentally different from every other provider in this package: there's no
account, no API key, and no order created server-side. A quote is a live
simulation run by a network node against real pool depth; to execute, the user
sends the FROM coin directly to a protocol-owned vault address with a specific
memo attached (the memo IS the instruction: "swap to X, send output to Y").
The network's own bonded validators observe the deposit and route the swap.
Nothing to register for, nothing that can be frozen or KYC'd for an individual.

Two caveats this app surfaces to the user rather than hiding:

1. The inbound_address these networks hand back is a SHARED vault used by
   every depositor of that asset during that vault's lifetime, not a
   single-use address minted per order the way SideShift/ChangeNOW/Trocador
   do. There is therefore no order id to poll, and SwapDesk can't tell "your"
   deposit apart from anyone else's without your own deposit transaction
   hash, which this app never sees. get_status() says so instead of guessing.

2. The memo must arrive on-chain intact. On BTC/LTC/DOGE it rides in an
   OP_RETURN with roughly 80 bytes to spare, and a destination address can
   eat over half of that. A truncated memo produces a deposit the network can
   neither pay out nor refund, so both the request shape and the returned
   memo are size-checked before any address is shown.
"""
from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from typing import ClassVar
from urllib.parse import urlparse

import requests

from .base import ProviderError, ProviderNetworkError, Quote, Swap, SwapProvider
from .constants import (
    COINS,
    STATUS_UNKNOWN,
    STATUS_WAITING,
    _addr_in_memo_fields,
    _dec,
    _epoch_utc,
    _eta_minutes,
)


class _ThorForkProvider(SwapProvider):
    """Shared logic for THORChain and any THORNode-API-compatible fork.

    Subclasses set: name, site, BASE (primary node API root), QUOTE_PATH,
    ASSET_MAP. Optionally set BASES (list) to add fallback nodes tried in
    order if the primary is unreachable. These are protocol-level public APIs
    with no auth, so any in-sync node answers identically; there's no reason a
    single blocked or down hostname should make the whole provider fail when
    another public node would work.
    """

    can_quote_without_config = True  # protocol-level, no account/key ever needed

    BASE = ""
    BASES: ClassVar[list] = []          # optional extra fallback node roots, tried after BASE
    QUOTE_PATH = "/thorchain/quote/swap"
    ASSET_MAP: ClassVar[dict[str, str]] = {}

    # Nine Realms' public THORNode gateway sits behind Cloudflare and requires
    # every request to carry an 'x-client-id' header identifying the caller;
    # unidentified traffic is served a bot-challenge instead of JSON. This is a
    # free-form app identifier, NOT a secret or API key. Other public nodes
    # ignore an unknown header, so sending it never hurts.
    CLIENT_ID = "swapdesk"

    # Max on-chain memo budget per source chain, in bytes. BTC/LTC/DOGE carry
    # the memo in an OP_RETURN, which is where the ~80-byte ceiling comes from;
    # account-model chains have far more room, up to THORChain's own network-
    # wide ceiling. Used both to pick a request shape that will fit and to
    # refuse a deposit whose memo the source chain physically cannot carry.
    #
    # THORChain's own memo docs (dev.thorchain.org/concepts/memos.html) state
    # a flat 250-byte memo ceiling network-wide -- "THORChain has a memo size
    # limit of 250 bytes. Any inbound tx sent with a larger memo will be
    # ignored" -- with the OP_RETURN 80-byte figure (and BCH's own ~220-byte
    # single-OP_RETURN limit) called out separately as an ADDITIONAL,
    # UTXO-chain-specific constraint that sits BELOW the 250-byte network cap,
    # never above it. ETH/AVAX/BSC previously used 1024 here with no doc
    # support -- found on review to contradict this same citation, which the
    # SOL/XMR rows already correctly apply -- so a memo between 250 and 1024
    # bytes on an EVM-sourced swap could pass this table's "will it fit" and
    # "SAFETY ABORT" checks and then be silently ignored by THORChain
    # validators. Every chain here is capped at 250, since no documented
    # THORChain source names a higher figure for any chain.
    MEMO_LIMITS: ClassVar[dict[str, int]] = {"BTC": 80, "LTC": 80, "DOGE": 80, "BCH": 220,
                   "ETH": 250, "AVAX": 250, "BSC": 250,
                   "SOL": 250, "XMR": 250}

    # Memo actions that mean "market swap" (dev.thorchain.org/concepts/memos:
    # SWAP, s, =). Anything else in the first field -- an add-liquidity "+",
    # a loan, a limit order "=<" -- is a different instruction that merely
    # happens to carry an address in the third field.
    SWAP_ACTIONS: ClassVar[frozenset[str]] = frozenset({"=", "SWAP", "S"})

    # Shortened asset names a node may write into the memo's asset field in
    # place of the full CHAIN.SYMBOL (the live quote endpoint does, e.g.
    # "=:e:0x..."). Only the fork whose table is documented fills this in; an
    # empty table means only the full asset id is accepted, which fails closed.
    SHORT_ASSETS: ClassVar[dict[str, str]] = {}

    # Source chains a deposit can be made from with what this app shows: a
    # plain transfer to the vault plus the memo in an OP_RETURN output, which
    # is what the quote's own notes describe for these UTXO chains. Every
    # other source is refused. EVM chains must call the router contract
    # (depositWithExpiry); an XMR deposit needs the memo in tx_extra, which
    # ordinary wallets cannot set, and is donated to the pool if it fails
    # without a refund address in that memo; SOL has no deposit procedure
    # this app can verify.
    MEMO_SOURCE_CHAINS: ClassVar[frozenset[str]] = frozenset(
        {"BTC", "LTC", "DOGE", "BCH", "DASH"})
    ROUTER_CHAINS: ClassVar[frozenset[str]] = frozenset(
        {"ETH", "AVAX", "BSC", "BASE", "ARB", "POL"})

    # Price protection requested on every create_swap, in basis points. The
    # node writes the resulting minimum output into the memo's LIM field.
    TOLERANCE_BPS = 300

    def __init__(self, timeout: int = 20):
        super().__init__(timeout)
        # ticker -> CHAIN.SYMBOL asset id. Seeded from the class's own curated
        # ASSET_MAP; fetch_coins() replaces this with whatever native L1 assets
        # currently have a live pool.
        self.assets: dict[str, str] = dict(self.ASSET_MAP)
        self.names: dict[str, str] = {
            t: v[0] for t, v in COINS.items() if t in self.ASSET_MAP}

    def configured(self) -> bool:
        # Protocol-level: no credentials to add, always usable.
        return True

    def _node_bases(self) -> list:
        bases = [self.BASE] + [b for b in self.BASES if b != self.BASE]
        return [b for b in bases if b]

    def _node_headers(self, base: str) -> dict:
        # Nine Realms *requires* x-client-id (without it its Cloudflare front
        # serves a bot-challenge instead of JSON) and Liquify uses it to grant
        # higher rate limits. Any other node, including Maya's, ignores an
        # unknown header, so Maya requests correctly send none.
        low = base.lower()
        if "ninerealms.com" in low or "liquify.com" in low:
            return {"x-client-id": self.CLIENT_ID}
        return {}

    def fetch_coins(self) -> dict:
        """Pull the network's live pool list and replace self.assets with every
        native L1 asset that currently has an Available pool.

        Deliberately restricted to plain "CHAIN.CHAIN" native assets: it skips
        synths ("CHAIN/CHAIN") and tokens ("ETH.USDC-0X..."). A token's asset
        id encodes an exact contract address, and getting that wrong from an
        automated parse is a real-funds mistake, not a cosmetic one. Raises on
        failure so callers leave the curated ASSET_MAP in place.
        """
        pools_path = self.QUOTE_PATH.rsplit("/quote/swap", 1)[0] + "/pools"
        last_err: Exception | None = None
        data = None
        for base in self._node_bases():
            try:
                data = self._get(f"{base}{pools_path}",
                                 headers=self._node_headers(base))
                break
            except (ProviderError, requests.RequestException) as e:
                last_err = e
                continue
        if data is None:
            raise last_err or ProviderError(
                f"{self.name}: no reachable node for /pools.")
        if not isinstance(data, list):
            raise ProviderError(f"{self.name}: unexpected /pools response shape.")
        assets: dict[str, str] = {}
        for row in data:
            if not isinstance(row, dict):
                continue
            if str(row.get("status", "")).lower() != "available":
                continue
            asset = str(row.get("asset") or "")
            chain, _, symbol = asset.partition(".")
            if not chain or not symbol or "-" in symbol or "/" in asset:
                continue  # tokens need a contract id; synths aren't native
            if chain != symbol:
                continue  # not a plain native asset (defensive)
            assets[chain] = asset
        if not assets:
            raise ProviderError(
                f"{self.name}: /pools returned no usable native assets.")
        self.assets = assets
        self.names = {t: COINS[t][0] if t in COINS else t for t in assets}
        return dict(self.names)

    def _asset(self, ticker: str) -> str | None:
        return self.assets.get(ticker.upper())

    def _unsupported(self, ticker: str) -> str:
        # self.assets, not ASSET_MAP: fetch_coins() replaces the curated seed
        # with the live pool list, so naming the seed would report the wrong
        # set to anyone whose install has refreshed.
        known = ", ".join(sorted(self.assets)) or "none loaded"
        return (f"{self.name}: {ticker} has no {self.name} pool; it only "
                f"routes {known} here.")

    def unsupported_source_reason(self, from_coin: str) -> str | None:
        """Why from_coin can't be sent through this provider, or None.

        Read by preflight as well as by get_quote/create_swap, so a route
        that cannot be funded safely is refused at every step.
        """
        chain = (self._asset(from_coin) or "").partition(".")[0].upper()
        if chain in self.MEMO_SOURCE_CHAINS:
            return None
        ticker = from_coin.upper()
        if chain in self.ROUTER_CHAINS:
            return (f"{self.name}: sending {ticker} requires calling the "
                    f"{self.name} router contract (depositWithExpiry), not a "
                    f"plain transfer to the vault address with a memo, and "
                    f"SwapDesk cannot build that transaction. Use another "
                    f"provider for {ticker} as the coin you send.")
        return (f"{self.name}: SwapDesk can only show a vault address and a "
                f"memo, and sending {ticker} that way is not a deposit "
                f"procedure it can verify is safe (the memo has to reach the "
                f"chain intact, with a refund address the network can use). "
                f"Use another provider for {ticker} as the coin you send.")

    def _memo_pays_asset(self, field: str, to_asset: str) -> bool:
        """True if a memo's asset field names to_asset, in full or shortened."""
        f = field.strip()
        if f.upper() == to_asset.upper():
            return True
        return self.SHORT_ASSETS.get(f.lower(), "").upper() == to_asset.upper()

    @staticmethod
    def _memo_limit_ok(field: str, expected_out: Decimal | None,
                       tolerance_bps: int) -> bool:
        """True if the memo's LIM/INTERVAL/QUANTITY field carries a minimum
        output no weaker than the tolerance that was requested.

        With liquidity_tolerance_bps set, the node writes
        floor(expected_amount_out * (10000 - bps) / 10000) as LIM (checked
        against live quotes). A missing, zero or lower LIM would let the swap
        fill at any price. LIM may use the documented shortened form NeX.
        """
        lim_text = field.partition("/")[0].strip().lower()
        mantissa, e, exp = lim_text.partition("e")
        if (not mantissa.isdigit() or (e and not exp.isdigit()) or len(mantissa) > 30
                or int(exp or 0) > 30):
            return False
        lim = int(mantissa) * 10 ** int(exp or 0)
        if lim <= 0 or expected_out is None or expected_out <= 0:
            return False
        floor = int(expected_out * (10000 - tolerance_bps) / 10000)
        # One base unit of slack for the node's own rounding.
        return lim >= floor - 1

    @staticmethod
    def _to_base_units(amount: Decimal) -> int:
        # These networks normalize every asset, even 1e18 ones like ETH, to
        # 1e8 base units. Truncate rather than round to never quote or send
        # more than the user actually typed.
        return int((amount * Decimal(10 ** 8)).to_integral_value(rounding=ROUND_DOWN))

    @classmethod
    def _memo_limit(cls, from_coin: str) -> int:
        return cls.MEMO_LIMITS.get((from_coin or "").upper(), 250)

    @staticmethod
    def _is_memo_len_error(text: str) -> bool:
        low = (text or "").lower()
        return "memo" in low and ("too long" in low or "long for" in low)

    def _quote_once(self, params: dict) -> tuple:
        """Try every node with one exact parameter set.

        Returns (data, node_errors, last_network_err, memo_too_long). `data` is
        None when no node produced a usable answer. Only transport-level
        failures are collected and skipped past; a genuine API rejection still
        raises immediately, with one exception: "memo too long" is reported via
        the flag instead, so the caller can retry with a smaller request rather
        than failing the whole swap.
        """
        node_errors: list = []
        last_network_err = None
        memo_too_long = False
        for base in self._node_bases():
            try:
                data = self._get(f"{base}{self.QUOTE_PATH}", params=params,
                                 headers=self._node_headers(base))
            except ProviderNetworkError as e:
                # Unreachable, rate-limited, or behind a bot-check. Record
                # which host and why, then fall through to the next one.
                # THORNode reports some request-validation failures (memo
                # length among them) with a 5xx, which lands here rather than
                # in the JSON-error branch below, so the memo check has to
                # happen in both places.
                host = urlparse(base).hostname or base
                # Strip the "<provider>: " prefix so the per-node line reads
                # "host -> detail" rather than repeating the provider name on
                # every entry. Kept in sync with the message built in _json().
                reason = str(e).split(": ", 1)[-1] if ": " in str(e) else str(e)
                if self._is_memo_len_error(reason):
                    memo_too_long = True
                node_errors.append(f"{host} -> {reason}")
                last_network_err = e
                continue
            if isinstance(data, dict) and data.get("error"):
                err_text = str(data["error"])
                if self._is_memo_len_error(err_text):
                    host = urlparse(base).hostname or base
                    memo_too_long = True
                    node_errors.append(f"{host} -> {err_text}")
                    continue
                raise ProviderError(f"{self.name}: {data['error']}")
            if not isinstance(data, dict):
                raise ProviderError(
                    f"{self.name}: unexpected non-object response from quote "
                    f"endpoint. Do not send funds. "
                    f"Response type: {type(data).__name__}")
            return data, node_errors, last_network_err, memo_too_long
        return None, node_errors, last_network_err, memo_too_long

    def _quote_swap(self, from_coin: str, to_coin: str, amount: Decimal,
                    destination: str = "", refund_address: str = "",
                    tolerance_bps: int | None = None,
                    _streaming: bool = True) -> dict:
        from_asset, to_asset = self._asset(from_coin), self._asset(to_coin)
        if not from_asset or not to_asset:
            raise ProviderError(
                self._unsupported(from_coin if not from_asset else to_coin))
        base_units = self._to_base_units(amount)
        if base_units <= 0:
            # Truncation to 1e8 base units can turn a positive dust amount
            # into 0, and sending amount=0 asks the node to price a swap the
            # user did not request. Nodes reject it today, but relying on
            # every node to keep doing so is not a guarantee worth taking on
            # a funds path. Refuse locally and say what the floor is.
            raise ProviderError(
                f"{self.name}: {amount} {from_coin.upper()} is below the "
                f"smallest amount this network can represent (1e-8). Use at "
                f"least 0.00000001.")
        base_params = {
            "from_asset": from_asset,
            "to_asset": to_asset,
            "amount": str(base_units),
        }
        if destination:
            base_params["destination"] = destination
        if tolerance_bps is not None:
            base_params["liquidity_tolerance_bps"] = str(tolerance_bps)

        # Degradation ladder for the memo budget. BTC's ~80-byte OP_RETURN is
        # the tightest of any chain routed here, and a 42-char bech32
        # destination eats over half of it, so a request that is perfectly
        # valid with an ETH source can be rejected outright with a BTC one.
        # Rather than failing the swap, shed the two optional memo-bearing
        # extras in the order that costs the user least:
        #
        #   1. streaming + refund_address: best rate, explicit refund target
        #   2. refund_address only: drops streaming (~0.5% worse rate)
        #   3. streaming only: drops the explicit refund target; the protocol
        #      still refunds, just to the sending address instead
        #   4. neither: smallest memo we can produce
        #
        # Rate is given up before the refund target is: a worse price is an
        # annoyance, a refund that goes somewhere unexpected is a lost coin.
        ladder = [(True, True), (False, True), (True, False), (False, False)]
        if not _streaming:                    # caller explicitly pinned this
            ladder = [(False, True), (False, False)]
        if not refund_address:                # nothing to shed on that axis
            ladder = [(st, False) for st, _rf in ladder]

        seen, variants = set(), []
        for v in ladder:
            if v not in seen:
                seen.add(v)
                variants.append(v)

        attempts: list = []
        transport_errors: list = []
        last_network_err = None
        hit_memo_limit = False
        for streaming, keep_refund in variants:
            params = dict(base_params)
            if streaming:
                params["streaming_interval"] = "1"
                params["streaming_quantity"] = "0"
            if keep_refund and refund_address:
                params["refund_address"] = refund_address

            data, errs, net_err, memo_long = self._quote_once(params)
            if data is not None:
                return data
            if net_err is not None:
                last_network_err = net_err
            if memo_long:
                hit_memo_limit = True
                attempts.append(
                    f"[streaming={'yes' if streaming else 'no'}, "
                    f"refund_address={'yes' if keep_refund and refund_address else 'no'}] "
                    + "; ".join(errs))
                continue          # shed another field and try a smaller memo
            transport_errors = errs   # nothing left to shed, report as-is
            break

        # Memo overflow wins over the transport error even when the node
        # reported it with a 5xx (which THORNode does, so it arrives as a
        # ProviderNetworkError). transport_errors is only populated when a
        # variant failed for a reason OTHER than memo length, so an empty list
        # here means every shape we tried overflowed, which is an actionable
        # request problem, not a connectivity one.
        if hit_memo_limit and not transport_errors:
            raise ProviderError(
                f"{self.name}: every request shape this app can build produces "
                f"a memo longer than {from_coin.upper()} can carry on-chain "
                f"(~{self._memo_limit(from_coin)} bytes), so there is no safe "
                f"way to route this swap. The destination address is the "
                f"largest part of the memo, a legacy {to_coin.upper()} "
                f"address will usually fit where a bech32 one does not. "
                f"Tried: " + " | ".join(attempts))

        # Every node failed. Surface each host and its reason so it's clear
        # whether this is a Cloudflare block, a rate-limit, or the hosts being
        # down, instead of a bare "HTTP 403" that hides which node and why.
        reported = transport_errors or attempts
        if last_network_err is None:
            raise ProviderError(f"{self.name}: quote failed: "
                                + " | ".join(reported or ["no nodes configured"]))
        bases = self._node_bases()
        if len(bases) > 1:
            raise ProviderNetworkError(
                f"{self.name}: all {len(bases)} nodes failed: "
                + "; ".join(reported))
        raise last_network_err

    def get_quote(self, from_coin, to_coin, amount, destination: str = "") -> Quote:
        if not self._asset(from_coin) or not self._asset(to_coin):
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         error=self._unsupported(
                             from_coin if not self._asset(from_coin) else to_coin),
                         unsupported=True)
        # Refused here too, not only in create_swap: a quote that can never be
        # executed would otherwise win "best rate" and be auto-selected.
        source_err = self.unsupported_source_reason(from_coin)
        if source_err:
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         error=source_err, unsupported=True)
        try:
            data = self._quote_swap(from_coin, to_coin, amount,
                                    destination=destination)
        except (ProviderError, requests.RequestException) as e:
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         error=str(e))
        out_base = (_dec(data.get("expected_amount_out_streaming"))
                    or _dec(data.get("expected_amount_out")))
        est = (out_base / Decimal(10 ** 8)) if out_base is not None else None
        rate = (est / amount) if (est is not None and amount) else None
        min_base = _dec(data.get("recommended_min_amount_in"))
        min_amt = (min_base / Decimal(10 ** 8)) if min_base is not None else None
        eta = _eta_minutes(data.get("total_swap_seconds"))
        return Quote(self.name, from_coin, to_coin, amount, est, rate,
                     min_amount=min_amt, eta_minutes=eta, raw=data)

    def create_swap(self, from_coin, to_coin, amount, settle_address,
                    refund_address="") -> Swap:
        # 3% default slippage guard vs. the simulated quote: protects the user
        # from a stale or moved market without being so tight that ordinary
        # pool movement between quote and deposit fails the swap.
        source_err = self.unsupported_source_reason(from_coin)
        if source_err:
            raise ProviderError(source_err)
        data = self._quote_swap(from_coin, to_coin, amount,
                                destination=settle_address,
                                refund_address=refund_address,
                                tolerance_bps=self.TOLERANCE_BPS)
        # A router in the answer means the node itself expects a contract
        # call for this inbound, whatever the local chain table says.
        if data.get("router"):
            raise ProviderError(
                f"{self.name}: the node says deposits of {from_coin.upper()} "
                f"must go through its router contract, which SwapDesk cannot "
                f"build. Do not send to the vault address directly.")
        inbound = data.get("inbound_address")
        memo = data.get("memo")
        inbound = self._checked_deposit_address(inbound)
        if not isinstance(memo, str) or not memo.strip():
            raise ProviderError(f"{self.name}: quote didn't include a deposit "
                                f"address/memo: try again.")
        # Checked as broadcast, not trimmed: whitespace or a control character
        # inside a field is not something the fields below may read past.
        if any(ch.isspace() or not ch.isprintable() for ch in memo):
            raise ProviderError(
                f"{self.name}: SAFETY ABORT: the memo returned by the node "
                f"contains whitespace or control characters. Do not use this "
                f"memo/deposit address. memo={memo!r}")
        # Last-line memo budget check. The node is supposed to enforce this
        # itself, but node versions differ and a gateway can be stale, and the
        # failure mode if a too-long memo slips through is that the user
        # broadcasts a real deposit carrying a truncated instruction, which the
        # network can then neither pay out nor refund. Refuse to hand out the
        # deposit address rather than risk that.
        memo_bytes = len(memo.encode("utf-8"))
        limit = self._memo_limit(from_coin)
        if memo_bytes > limit:
            raise ProviderError(
                f"{self.name}: SAFETY ABORT: the node returned a "
                f"{memo_bytes}-byte memo but {from_coin.upper()} can only "
                f"carry {limit} bytes on-chain. Sending this deposit would "
                f"broadcast a truncated instruction that can be neither paid "
                f"out nor refunded. Use a shorter destination address (a "
                f"legacy {to_coin.upper()} address rather than a bech32 one) "
                f"and try again. memo={memo!r}")
        # There's no separate "order" object here: the memo the node hands back
        # IS the destination instruction that gets broadcast on-chain. So the
        # memo naming the exact settle_address we asked for *is* the
        # independent verification. If it's wrong, sending funds with this
        # memo would not pay out where the user approved, and that's
        # unrecoverable once broadcast. Fail here rather than downstream.
        #
        # The layout is documented (dev.thorchain.org/concepts/memos, and the
        # worked quote example in THORChain's swap guide) as
        #   SWAP:ASSET:DESTADDR:LIM/INTERVAL/QUANTITY:AFFILIATE:FEE
        # with an optional refund target carried INSIDE the destination field
        # as DESTADDR/REFUNDADDR. Two things follow, and this check used to
        # get both wrong by scanning every ':'-delimited field for the
        # address instead of reading the one field that decides the payout:
        #
        #   1. A memo whose DESTADDR is someone else's but that happens to
        #      carry the approved address somewhere else (the affiliate
        #      field, or the refund half) passed as "verified". The payout
        #      position is the only one worth verifying, so read it.
        #   2. When a refund address was sent the node writes "dest/refund"
        #      into that field, which no whole-field compare can ever match
        #      -- so a correct memo aborted with a SAFETY ABORT claiming the
        #      destination was absent, on every THORChain/Maya swap whose
        #      refund target survived the memo-budget ladder above.
        memo_fields = memo.split(":")
        # The address check below only proves the memo pays SOMETHING to the
        # approved address; the first two fields decide what. An add-liquidity
        # or loan memo also carries an address in position three, and an EVM
        # address is equally valid on AVAX or BSC.
        if memo_fields[0].upper() not in self.SWAP_ACTIONS:
            raise ProviderError(
                f"{self.name}: SAFETY ABORT: the memo returned by the node is "
                f"not a swap instruction (action {memo_fields[0]!r}). Do not "
                f"use this memo/deposit address. memo={memo!r}")
        to_asset = self._asset(to_coin) or ""
        if len(memo_fields) < 2 or not self._memo_pays_asset(memo_fields[1], to_asset):
            raise ProviderError(
                f"{self.name}: SAFETY ABORT: the memo returned by the node "
                f"does not pay out {to_asset} (asset field "
                f"{memo_fields[1] if len(memo_fields) > 1 else ''!r}). Do not "
                f"use this memo/deposit address. memo={memo!r}")
        dest_field = memo_fields[2] if len(memo_fields) > 2 else ""
        memo_dest, _, memo_refund = dest_field.partition("/")
        if not _addr_in_memo_fields(settle_address, [memo_dest]):
            raise ProviderError(
                f"{self.name}: SAFETY ABORT: the memo returned by the node "
                f"does not name the destination address you approved "
                f"({settle_address!r}) as its payout address. Do not use this "
                f"memo/deposit address. memo={memo!r}")
        # The refund half of that field is a funds destination too: whatever
        # is written there is where the protocol returns the deposit if the
        # swap cannot complete. A refund target the user did not supply sends
        # a failed swap's coins to an address they never approved, so it is
        # refused on the same terms as a wrong payout address rather than
        # being left unchecked.
        if memo_refund and not _addr_in_memo_fields(refund_address or "",
                                                     [memo_refund]):
            raise ProviderError(
                f"{self.name}: SAFETY ABORT: the memo returned by the node "
                f"names {memo_refund!r} as the refund address, which is not "
                f"the one you entered ({refund_address or 'none'}). A failed "
                f"swap would return your deposit there. Do not use this "
                f"memo/deposit address. memo={memo!r}")
        # The price protection requested above has to survive into the memo.
        # The smaller of the two expected outputs sets the floor, so a node
        # that derives LIM from either one is not refused for it.
        outs = [d for d in (_dec(data.get("expected_amount_out")),
                            _dec(data.get("expected_amount_out_streaming")))
                if d is not None and d > 0]
        lim_field = memo_fields[3] if len(memo_fields) > 3 else ""
        if not self._memo_limit_ok(lim_field, min(outs) if outs else None,
                                   self.TOLERANCE_BPS):
            raise ProviderError(
                f"{self.name}: SAFETY ABORT: the memo returned by the node "
                f"does not carry the minimum output ({self.TOLERANCE_BPS / 100:g}% "
                f"price protection) that was requested (limit field "
                f"{lim_field!r}). Without it the swap could fill at any "
                f"price. Do not use this memo/deposit address. memo={memo!r}")
        out_base = (_dec(data.get("expected_amount_out_streaming"))
                    or _dec(data.get("expected_amount_out")))
        est = (out_base / Decimal(10 ** 8)) if out_base is not None else None
        min_base = _dec(data.get("recommended_min_amount_in"))
        min_amt = (min_base / Decimal(10 ** 8)) if min_base is not None else None
        expires_at = _epoch_utc(data.get("expiry"))
        swap = Swap(
            provider=self.name,
            # No server-side order exists. The memo you send IS the order.
            order_id=memo,
            from_coin=from_coin,
            to_coin=to_coin,
            deposit_address=inbound,
            deposit_memo=self._checked_memo(memo),
            send_amount=amount,
            deposit_min=min_amt,
            deposit_max=None,
            estimated_receive=est,
            settle_address=settle_address,
            expires_at=expires_at,
            status=STATUS_WAITING,
            raw=data,
            # Already fail-fast verified above: the memo contains this exact
            # address, so it's as independently-confirmed as this protocol
            # allows.
            settle_address_confirmed_by_provider=settle_address,
        )
        # Record whether the explicit refund address survived the memo-budget
        # ladder. When the memo will not fit the source chain, _quote_swap
        # sheds the refund target (rung 3) and the protocol falls back to
        # refunding the SENDING address -- the right thing to shed before
        # rate, but the user filled that field in and nothing told them it had
        # been dropped.
        #
        # Read from the memo, which is the instruction that actually gets
        # broadcast, not from a `refund_address` key in the quote response:
        # THORChain's documented quote schema has no such field, so that read
        # returned None every time and this was False on every swap -- which
        # is what the deposit window renders its red "Refund address was NOT
        # included" card on. None when no refund address was asked for at all,
        # since "not applicable" is not the same as "dropped" (the window only
        # warns on False).
        swap.refund_address_sent = bool(memo_refund) if refund_address else None
        return swap

    def get_status(self, order_id) -> str:
        # See the module docstring: there is no order id these networks know
        # about, and the shared vault address can't be attributed to one
        # user's swap without their deposit tx hash. Report unknown honestly
        # rather than polling an endpoint that can't answer this.
        return STATUS_UNKNOWN


class THORChain(_ThorForkProvider):
    name = "THORChain"
    site = "https://thorchain.org"
    # Node list reflects THORChain's current dev-docs guidance rather than the
    # old xchainjs defaults:
    #   1. gateway.liquify.com/chain/thorchain_api: the endpoint THORChain's
    #      dev docs now point integrators at; Liquify's current public gateway.
    #   2. thornode.thorchain.liquify.com: Liquify's older host, still live.
    #   3. thornode.ninerealms.com: Nine Realms' host, still used in
    #      THORChain's own docs examples. Last in the list because it has been
    #      unreliable to resolve at times, not because it's known bad.
    # *.thorswap.net was dropped: it's now private to THORSwap's app.
    # Order = try order. All are THORNode-API-compatible; Liquify hosts take an
    # x-client-id header (added automatically) to lift the per-IP daily limit.
    # If every THORChain node is blocked on your network, THORChain simply
    # drops out of the results. To force a specific node, put it first here or
    # point BASE at your own THORNode.
    BASE = "https://gateway.liquify.com/chain/thorchain_api"
    BASES: ClassVar[list] = [
        "https://gateway.liquify.com/chain/thorchain_api",
        "https://thornode.thorchain.liquify.com",
        "https://thornode.ninerealms.com",
    ]
    # ticker -> THORChain asset notation (CHAIN.SYMBOL). This is only a seed:
    # fetch_coins() replaces it with the live /pools list, so a pool added or
    # removed upstream is picked up without editing this. Listing a coin that
    # has no pool would give a confusing API failure instead of a clear "not
    # supported", so keep it to assets known to be live.
    #
    # DASH is absent: it has no THORChain pool. Maya below routes it.
    ASSET_MAP: ClassVar[dict[str, str]] = {
        "BTC":  "BTC.BTC",
        "LTC":  "LTC.LTC",
        "ETH":  "ETH.ETH",
        "DOGE": "DOGE.DOGE",
        "BCH":  "BCH.BCH",
        "SOL":  "SOL.SOL",
        # Monero went live on THORChain mainnet in 2026. Notation follows the
        # native CHAIN.SYMBOL convention every other entry here uses; if it
        # ever disagrees with the network, the /pools refresh corrects it.
        "XMR":  "XMR.XMR",
    }
    # dev.thorchain.org/concepts/memo-length-reduction.html, "Shortened Asset
    # Names". The live quote endpoint writes these (e.g. "=:e:0x...").
    SHORT_ASSETS: ClassVar[dict[str, str]] = {
        "r": "THOR.RUNE", "rune": "THOR.RUNE", "a": "AVAX.AVAX", "b": "BTC.BTC",
        "c": "BCH.BCH", "d": "DOGE.DOGE", "e": "ETH.ETH", "f": "BASE.ETH",
        "g": "GAIA.ATOM", "l": "LTC.LTC", "p": "POL.POL", "s": "BSC.BNB",
        "tr": "TRON.TRX", "x": "XRP.XRP",
    }


class MayaProtocol(_ThorForkProvider):
    # Maya is a THORChain fork with a byte-for-byte compatible quote API, so it
    # needs nothing beyond the shared base class: just its own node root, quote
    # path (/mayachain/ instead of /thorchain/) and pool list. Maya's node is
    # not operated by Nine Realms, so it needs no x-client-id header, and
    # _node_headers only adds one for ninerealms/liquify hosts.
    name = "Maya Protocol"
    site = "https://www.mayaprotocol.com"
    BASE = "https://mayanode.mayachain.info"
    # Maya's own docs point every integrator at this single official public
    # node; there isn't a widely-run second public Mayanode to list as a
    # verified fallback, so BASE stands alone rather than inventing hosts that
    # might not answer. The multi-node fallback logic still applies to
    # THORChain, which does have several.
    BASES: ClassVar[list] = ["https://mayanode.mayachain.info"]
    QUOTE_PATH = "/mayachain/quote/swap"
    # Maya normalizes every asset to 1e8 base units exactly like THORChain, so
    # the base class's _to_base_units is correct as-is.
    #
    # Restricted to coins with a live native Maya pool: BTC, ETH and DASH,
    # which is the only DASH route of the two protocols here. Maya
    # also pools ERC-20 USDC, but like THORChain that requires the full
    # "ETH.USDC-0X<contract>" asset id where exact checksum and casing matter,
    # so it's intentionally left out rather than risk a subtly-wrong mapping.
    # LTC/DOGE/BCH/SOL/XMR are absent because Maya has no pool for them; they
    # stay on the THORChain provider, which does.
    ASSET_MAP: ClassVar[dict[str, str]] = {
        "BTC":  "BTC.BTC",
        "ETH":  "ETH.ETH",
        "DASH": "DASH.DASH",
    }
    # SHORT_ASSETS is deliberately left empty: Maya's shortened names are not
    # taken from THORChain's table, so only a full asset id in the memo is
    # accepted. A shortened one aborts the swap rather than being guessed at.
