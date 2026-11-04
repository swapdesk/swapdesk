"""providers.chainflip: Chainflip via Broker-as-a-Service (BaaS).

Chainflip is a decentralized cross-chain protocol: swaps are executed by its
own validator set, not by a company holding your coins. BaaS is a hosted
broker in front of it, so unlike THORChain/Maya this one DOES need an API
key (your broker key), but the swap itself is still non-custodial and the
deposit address is a per-swap channel rather than a shared vault.

Two things that differ from every other provider here:

1. A refund address is MANDATORY, not optional. Chainflip requires a
   minimum accepted price on every swap; if the market moves past it the
   source funds are returned to the refund address. Without one there is
   nowhere to send a failed swap, so create_swap refuses rather than
   silently dropping the protection.

2. The swap-creation response does not echo the destination address back.
   The status endpoint does, but only once BaaS has polled Chainflip for the
   new channel, so an immediate follow-up call usually 404s. create_swap
   retries briefly; if the answer still isn't there the swap opens as
   unverified, and the deposit window keeps comparing the destination on
   every status poll until one arrives (see DESTINATION_ECHO_DEFERRED).

3. There is no "refunded" state on the wire. A fill-or-kill refund arrives
   as state "completed" with a refund egress and no swap egress, so the
   outcome is read from which egress legs are present, not from the state
   string alone (see _classify).

API reference: https://docs.chainflip-broker.io
"""
from __future__ import annotations

import re
import threading
import time
from decimal import Decimal
from typing import ClassVar

import requests

from .base import ProviderError, ProviderNetworkError, Quote, Swap, SwapProvider
from .constants import (
    STATUS_COMPLETE,
    STATUS_CONFIRMING,
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_REFUNDED,
    STATUS_REFUNDING,
    STATUS_SENDING,
    STATUS_UNKNOWN,
    STATUS_WAITING,
    _dec,
    _eta_minutes,
)

# A transaction reference shown to the user. Hex hashes, base58 signatures
# and bech32-style ids all fit; anything else is dropped rather than put in
# a label.
_TX_REF_RE = re.compile(r"^[A-Za-z0-9:_-]{8,128}$")
_ASSET_ID_RE = re.compile(r"^[a-z0-9]{1,16}\.[a-z0-9]{1,16}$")


class Chainflip(SwapProvider):
    name = "Chainflip"
    site = "https://chainflip-broker.io"

    # Read by preflight (same pattern as KYC_ON_FLAGGED): True means
    # create_swap hard-refuses without a refund_address, so preflight should
    # catch a missing one before the user reaches the confirm step rather
    # than letting create_swap be the first thing to say so.
    REQUIRES_REFUND_ADDRESS = True

    # Read by preflight, same pattern as KYC_ON_FLAGGED: True means this
    # provider's credential and swap addresses travel in the URL query
    # string rather than a POST body or header. VERIFIED against
    # Chainflip's own "Starting a Swap" API reference: /swap has no
    # documented POST or header-auth alternative, so this is a real API
    # constraint, not a missed fix (see _params() below). Disclosed rather
    # than left implicit, since it's a real difference in what a reverse
    # proxy or web-server access log can see.
    LOGS_SENSITIVE_DATA_IN_URL = True

    # Read by the deposit window: the destination echo is not available when
    # the swap is created (BaaS answers /status-by-id with 404 until it has
    # polled Chainflip for the new channel), so an "unverified" result at
    # creation is re-checked against destination_echo() on every poll. A
    # mismatch found that way locks the window the same way one found at
    # creation would have refused it.
    DESTINATION_ECHO_DEFERRED = True

    # Mainnet BaaS host. The docs' worked examples all use the testnet host
    # (perseverance.chainflip-broker.io); this is the same service without
    # that prefix, matching the BaaS product site. Point BASE at the
    # perseverance host to test against testnet.
    BASE = "https://chainflip-broker.io"

    # ticker -> Chainflip asset id ("chain.asset").
    #
    # Deliberately only assets whose mapping is unambiguous. Chainflip lists
    # the same ticker on several chains (usdc.eth, usdc.arb, usdc.sol, and
    # eth.eth vs eth.arb), and picking one for the user is the kind of guess
    # that sends funds to the wrong network. USDC is pinned to Ethereum
    # mainnet because that is what USDC means everywhere else in this app
    # (see EVM_TOKENS in zerox.py); the Arbitrum and Solana variants are
    # left out rather than guessed between.
    ASSETS: ClassVar[dict[str, str]] = {
        "BTC":  "btc.btc",
        "ETH":  "eth.eth",
        "SOL":  "sol.sol",
        "USDC": "usdc.eth",
    }

    # Chainflip's in-flight states. "completed" and "failed" are absent on
    # purpose: neither says what happened to the deposit on its own, so
    # _classify decides them from the egress legs.
    _MAP: ClassVar[dict[str, str]] = {
        "waiting": STATUS_WAITING,
        "receiving": STATUS_CONFIRMING,
        "swapping": STATUS_CONFIRMING,
        "sending": STATUS_SENDING,
        "sent": STATUS_SENDING,
    }

    # Price tolerance behind the minimum-price floor, in percent. Chainflip
    # refunds rather than completing a swap that breaches it, so this bounds
    # what the user receives; it is not a fee.
    #
    # Each quote carries a recommended tolerance for its pair, size and
    # current liquidity. The floor uses that figure, but never less than
    # SLIPPAGE_PCT, and a quote recommending more than MAX_SLIPPAGE_PCT is
    # not offered at all. A wider tolerance only turns refunds into fills at
    # a worse price, and past a point that is a cost the user should not
    # carry without choosing it. A thin pair is better skipped than filled
    # 10% under the rate on screen.
    SLIPPAGE_PCT = Decimal("2.5")
    MAX_SLIPPAGE_PCT = Decimal("5")

    # Waits, in seconds, between attempts to read the destination back after
    # /swap. BaaS needs one round trip to Chainflip before /status-by-id can
    # answer for a new channel.
    # No retry starts after DEST_ECHO_BUDGET_SECONDS, so a slow or hanging
    # host adds at most one request timeout to "Creating..." beyond that.
    DEST_ECHO_RETRY_DELAYS: ClassVar[tuple[float, ...]] = (2, 3, 5)

    # /swap opens a deposit channel, and BaaS serves it as a GET, so the
    # shared retry-on-5xx rule would apply to it. BaaS documents 503 as the
    # temporary condition to retry with backoff; 408 and 429 mean the request
    # was not processed. After a 500, 502 or 504 the channel may already be
    # open, and a retry would open a second one.
    SWAP_RETRY_STATUSES: ClassVar[frozenset[int]] = frozenset({408, 429, 503})

    # Chainflip documents deposit channels as open for 24 hours, after which
    # the network may no longer recognise a late deposit. /swap does return
    # the exact expiry, but as a block height on the source chain
    # (sourceExpiryBlock), which this app has no clock for. The documented
    # window, counted from when the channel was opened, is shown instead.
    CHANNEL_LIFETIME_SECONDS = 24 * 60 * 60
    DEST_ECHO_BUDGET_SECONDS = 15

    # How long Chainflip keeps retrying before refunding, in blocks. One
    # block is 6 seconds, so 150 blocks is ~15 minutes: long enough to ride
    # out ordinary volatility, short enough that a stuck swap refunds the
    # same day.
    RETRY_BLOCKS = 150

    # How long a quote stays usable as the basis for the slippage floor.
    # Long enough to cover reading the rate and confirming the swap, short
    # enough that the floor still reflects a live market.
    QUOTE_TTL_SECONDS = 120

    def __init__(self, api_key: str = "", timeout: int = 20):
        super().__init__(timeout)
        self.api_key = (api_key or "").strip()
        self.assets: dict[str, str] = dict(self.ASSETS)
        self.names: dict[str, str] = {}
        # From /assets, keyed by ticker and asset id respectively. Until
        # fetch_coins() has run these are empty: no minimum is stated and
        # every mapped asset is usable in both directions.
        self.minimums: dict[str, Decimal] = {}
        self.directions: dict[str, str] = {}
        # Last quote, so the swap's minimum price and displayed receive
        # figure come from the numbers the user actually approved. Guarded by
        # a lock: quotes are fetched on worker threads, one per provider.
        self._last_quote: tuple | None = None
        self._quote_lock = threading.Lock()
        # Per order: the destination BaaS last reported, and the payout /
        # refund facts from the last poll. Written by the polling thread,
        # read by the GUI thread.
        self._dest_echo: dict[str, str] = {}
        self._state_lock = threading.Lock()

    def configured(self) -> bool:
        return bool(self.api_key)

    def _params(self, **kw) -> dict:
        # VERIFIED hard API constraint, not an oversight: Chainflip's BaaS
        # /swap endpoint only accepts apiKey, destinationAddress and
        # refundAddress as URL query parameters -- confirmed against
        # Chainflip's own "Starting a Swap" API reference
        # (docs.chainflip-broker.io/features/start-swap/), whose own worked
        # curl example sends every one of these as a query string:
        #   curl --location '.../swap?apikey=...&destinationAddress=...
        #                    &refundAddress=...&minimumPrice=...'
        # No POST body form and no header-based auth are documented for
        # this endpoint. That's the opposite of trocador.py's create_swap()
        # (deliberately POST, for the same class of data, because GET
        # params get logged by proxies/web servers) -- the difference here
        # is real, not a missed fix. Disclosed to the user via
        # LOGS_SENSITIVE_DATA_IN_URL / preflight rather than silently
        # matched to Trocador's approach, since there is nothing to switch
        # to.
        p = {"apiKey": self.api_key}
        p.update({k: v for k, v in kw.items() if v is not None})
        return p

    def _json(self, r: requests.Response):
        """BaaS reports errors as RFC 7807 problem documents. The reason is in
        "detail" for a refusal such as an amount below the minimum, and in
        "errors" as field to messages for a rejected request. The shared
        parser reads only "error" and "message" and would show the whole
        body. Status handling matches the shared parser's."""
        if r.status_code >= 400:
            try:
                data = r.json()
            except ValueError:
                data = None
            reason = self._problem_reason(data)
            if reason and "error" not in data and "message" not in data:
                text = f"{self.name}: HTTP {r.status_code}: {reason}"
                if r.status_code == 401:
                    text += "  (check your API key)"
                elif r.status_code == 403:
                    # Chainflip's own error-code docs define 403 as
                    # "route_disabled": this pair isn't enabled for the
                    # account, a dashboard permission setting, not a bad key.
                    text += ("  (this route isn't enabled for your API key -- "
                             "check the allowed pairs in your Chainflip "
                             "broker dashboard, not the key itself)")
                if r.status_code in self._GATEWAY_STATUS:
                    raise ProviderNetworkError(text, status_code=r.status_code,
                                               retry_after=self._parse_retry_after(r))
                raise ProviderError(text)
        return super()._json(r)

    @staticmethod
    def _problem_reason(data) -> str | None:
        """The human part of an RFC 7807 body, or None if there isn't one."""
        if not isinstance(data, dict):
            return None
        parts: list[str] = []
        detail = data.get("detail")
        errors = data.get("errors")
        if isinstance(detail, str) and detail.strip():
            # A refusal states its reason here; "errors" then only repeats the
            # figure the sentence already gives.
            parts.append(detail)
        elif isinstance(errors, dict):
            for field, messages in list(errors.items())[:3]:
                if isinstance(messages, str):
                    messages = [messages]
                if isinstance(messages, list) and messages:
                    first = str(messages[0])
                    parts.append(f"{field}: {first}" if field else first)
        if not parts:
            title = data.get("title")
            if isinstance(title, str) and title.strip():
                parts.append(title)
        if not parts:
            return None
        reason = " ".join(" ".join(parts).split())
        return reason[:300] + "..." if len(reason) > 300 else reason

    def _asset(self, ticker: str) -> str | None:
        return self.assets.get(ticker.upper())

    def _unsupported(self, ticker: str) -> str:
        known = ", ".join(sorted(self.assets)) or "none loaded"
        return (f"{self.name}: {ticker} isn't routed here ({known}). "
                f"Multi-network assets are left out rather than guessed at.")

    def _route(self, from_coin: str, to_coin: str) -> tuple[str, str, None] | tuple[None, None, str]:
        """(source id, destination id, None), or (None, None, reason).

        Honours the direction /assets reports: an asset marked "ingress" can
        only be swapped from, "egress" only to. Without that a one-way asset
        fails at quote time with nothing to say why.
        """
        src, dst = self._asset(from_coin), self._asset(to_coin)
        if not src or not dst:
            return None, None, self._unsupported(from_coin if not src else to_coin)
        if self.directions.get(src, "both") == "egress":
            return None, None, (f"{self.name}: {from_coin.upper()} can only be "
                                f"received through Chainflip right now, not sent.")
        if self.directions.get(dst, "both") == "ingress":
            return None, None, (f"{self.name}: {to_coin.upper()} can only be "
                                f"sent through Chainflip right now, not received.")
        return src, dst, None

    def fetch_coins(self) -> dict:
        """Refresh names, availability, direction and minimums from /assets.

        The ticker -> asset-id map is never grown from the live list: /assets
        returns every network variant, and collapsing those to a bare ticker
        is exactly the guess ASSETS exists to avoid. The live list can only
        narrow it. An asset that is missing, or not enabled, is dropped until
        the next refresh, since BaaS hides assets it will not serve.

        A list that names none of the ASSETS ids is treated as a response
        this code does not understand, not as "nothing is served", and raises
        so the built-in map stays in place.
        """
        data = self._get(f"{self.BASE}/assets", params=self._params())
        rows = data.get("assets") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise ProviderError(f"{self.name}: unexpected /assets response shape.")
        live: dict[str, dict] = {}
        for row in rows:
            if isinstance(row, dict) and row.get("id"):
                live[str(row["id"])] = row
        if not any(a in live for a in self.ASSETS.values()):
            raise ProviderError(f"{self.name}: /assets listed none of the assets "
                                f"this app routes; keeping the built-in list.")
        assets: dict[str, str] = {}
        names: dict[str, str] = {}
        minimums: dict[str, Decimal] = {}
        directions: dict[str, str] = {}
        for ticker, asset_id in self.ASSETS.items():
            row = live.get(asset_id)
            if row is None or row.get("enabled", True) is not True:
                continue
            direction = str(row.get("direction") or "both").lower()
            if direction not in ("both", "ingress", "egress"):
                continue
            assets[ticker] = asset_id
            directions[asset_id] = direction
            names[ticker] = str(row.get("name") or row.get("ticker") or "")
            minimum = _dec(row.get("minimalAmount"))
            if minimum is not None and minimum > 0:
                minimums[ticker] = minimum
        self.directions = directions
        self.minimums = minimums
        self.assets = assets
        self.names = names
        return dict(self.names)

    def get_quote(self, from_coin, to_coin, amount, destination: str = "") -> Quote:
        src, dst, reason = self._route(from_coin, to_coin)
        if reason:
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         error=reason, unsupported=True)
        if not self.configured():
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         error=f"{self.name}: needs a BaaS API key (set it in Settings).")
        minimum = self.minimums.get(from_coin.upper())
        try:
            data = self._get(f"{self.BASE}/quotes",
                             params=self._params(sourceAsset=src,
                                                 destinationAsset=dst,
                                                 amount=str(amount)))
        except (ProviderError, requests.RequestException) as e:
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         min_amount=minimum, error=str(e))

        quote = self._pick_regular_quote(data)
        if quote is None:
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         min_amount=minimum,
                         error=f"{self.name}: no instant quote for this pair/amount.")
        # estimatedPrice is a rate net of the network, broker and liquidity
        # fees but gross of the two chain fees (deposit and payout gas), so
        # amount * estimatedPrice overstates the payout by those fees: under
        # 1 bps on a large trade, several percent on a small one.
        # egressAmount is the payout net of every fee, and is what the user
        # receives. estimatedPrice stays the rate, because that is what the
        # minimum-price floor is measured against.
        price = _dec(quote.get("estimatedPrice"))
        est = _dec(quote.get("egressAmount"))
        if price is None or price <= 0 or est is None or est <= 0:
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         min_amount=minimum,
                         error=f"{self.name}: quote is missing its rate or payout amount.")
        tolerance, reason = self._tolerance(quote)
        if reason:
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         min_amount=minimum, error=reason)
        eta = _eta_minutes(quote.get("estimatedDurationSeconds"))
        with self._quote_lock:
            self._last_quote = (from_coin.upper(), to_coin.upper(), amount,
                                price, est, tolerance, time.monotonic())
        return Quote(self.name, from_coin, to_coin, amount, est, price,
                     min_amount=minimum, eta_minutes=eta, raw=quote)

    def _tolerance(self, quote: dict) -> tuple[Decimal, None] | tuple[None, str]:
        """(tolerance percent, None), or (None, reason to skip this quote)."""
        recommended = _dec(quote.get("recommendedSlippageTolerancePercent"))
        if recommended is None or recommended < 0:
            return self.SLIPPAGE_PCT, None
        if recommended > self.MAX_SLIPPAGE_PCT:
            return None, (
                f"{self.name}: liquidity on this pair is thin right now "
                f"(Chainflip recommends allowing the price to move "
                f"{recommended.normalize():f}%). SwapDesk allows at most "
                f"{self.MAX_SLIPPAGE_PCT}%, so this route is skipped. A smaller "
                f"amount or another provider may do better.")
        return max(self.SLIPPAGE_PCT, recommended), None

    @staticmethod
    def _pick_regular_quote(data):
        """Pick the instant ('regular') quote from the /quotes array.

        The endpoint also returns 'dca' quotes, which split a swap into
        timed chunks. Those price better but take much longer and behave
        differently on slippage, so an unattended pick of one would be a
        surprise. Only a row that says it is regular is used. There is no
        fallback to whatever row came first: create_swap always opens a
        regular swap, so a floor taken from a DCA price is a floor the swap
        may never reach, and a row with no type is not known to be regular.
        BaaS promises no ordering and no filtering of this array.
        """
        rows = data if isinstance(data, list) else [data]
        for q in rows:
            if not isinstance(q, dict):
                continue
            if str(q.get("type") or "").lower() != "regular":
                continue
            # BaaS aggregates providers and stamps each quote's "platform";
            # /swap routes to chainflip unless told otherwise. A floor taken
            # from another platform's price would not describe the swap that
            # is actually opened. No platform field means a response from
            # before the aggregator existed, which was Chainflip's.
            if str(q.get("platform") or "chainflip").lower() != "chainflip":
                continue
            return q
        return None

    def create_swap(self, from_coin, to_coin, amount, settle_address,
                    refund_address="") -> Swap:
        src, dst, reason = self._route(from_coin, to_coin)
        if reason:
            raise ProviderError(reason)
        if not self.configured():
            raise ProviderError(f"{self.name}: needs a BaaS API key "
                                f"(set it in Settings).")
        if not refund_address:
            raise ProviderError(
                f"{self.name}: a refund address on the {from_coin.upper()} "
                f"chain is required. Chainflip enforces a minimum price on "
                f"every swap and returns the deposit if the market moves "
                f"past it, so without a refund address there is nowhere for "
                f"a failed swap to go.")

        # The floor is derived from the rate the user was shown and
        # approved, not from a rate fetched here. Re-quoting at this point
        # would move the floor with the market: if the price fell between
        # the quote and the confirmation, a fresh floor would sit below it
        # and the swap would complete at the worse rate instead of being
        # refunded. Anchoring to the approved rate means an adverse move
        # trips the protection, which is the direction the user asked for.
        #
        # The floor is taken from estimatedPrice, never from the payout
        # figure divided by the amount: that ratio is net of the chain fees,
        # so a floor built on it would sit lower than intended by exactly
        # those fees.
        approved = self._approved_quote(from_coin, to_coin, amount)
        if approved is None:
            raise ProviderError(
                f"{self.name}: no recent quote for {from_coin.upper()} -> "
                f"{to_coin.upper()} at this amount, so the minimum price "
                f"that protects you from slippage can't be set from a rate "
                f"you approved. Get a fresh rate and try again.")
        rate, est_receive, tolerance = approved
        min_price = (rate * (Decimal(100) - tolerance) / Decimal(100))

        opened_at = time.time()
        data = self._get(f"{self.BASE}/swap",
                         params=self._params(
                             sourceAsset=src, destinationAsset=dst,
                             destinationAddress=settle_address,
                             refundAddress=refund_address,
                             minimumPrice=f"{min_price:f}",
                             retryDurationInBlocks=str(self.RETRY_BLOCKS)),
                         retry_on=self.SWAP_RETRY_STATUSES)
        if not isinstance(data, dict):
            raise ProviderError(f"{self.name}: unexpected /swap response shape. "
                                f"Do not send funds.")
        deposit = data.get("address")
        swap_id = data.get("id")
        deposit = self._checked_deposit_address(deposit)
        swap_id = self._checked_order_id(swap_id)
        return Swap(
            provider=self.name,
            order_id=swap_id,
            from_coin=from_coin,
            to_coin=to_coin,
            deposit_address=str(deposit),
            deposit_memo=None,      # deposit channel, no memo/tag involved
            send_amount=amount,
            deposit_min=self.minimums.get(from_coin.upper()),
            deposit_max=None,
            estimated_receive=est_receive,
            settle_address=settle_address,
            expires_at=time.strftime(
                "%Y-%m-%d %H:%M:%S UTC",
                time.gmtime(opened_at + self.CHANNEL_LIFETIME_SECONDS)),
            status=STATUS_WAITING,
            raw=data,
            settle_address_confirmed_by_provider=self._confirmed_destination(swap_id),
        )

    def _approved_quote(self, from_coin, to_coin, amount
                        ) -> tuple[Decimal, Decimal, Decimal] | None:
        """(rate, payout, tolerance) from the most recent matching quote, if
        still fresh.

        Matched on pair AND amount because Chainflip's price is
        amount-dependent, so a rate quoted for a different size is not the
        one the user approved. Returns None when there is no such quote or
        it has aged out, which the caller turns into "get a fresh rate"
        rather than silently substituting a new price.
        """
        with self._quote_lock:
            last = self._last_quote
        if not last:
            return None
        frm, to, amt, rate, payout, tolerance, at = last
        if frm != from_coin.upper() or to != to_coin.upper() or amt != amount:
            return None
        if (time.monotonic() - at) > self.QUOTE_TTL_SECONDS:
            return None
        return rate, payout, tolerance

    def _confirmed_destination(self, swap_id) -> str | None:
        """Read the destination Chainflip recorded, for the echo check.

        /swap doesn't return it, so this is a second call, retried a few
        times because BaaS answers 404 until it has polled Chainflip for the
        new channel. Returns None if it still can't answer, which callers
        treat as 'unverified' rather than 'ok'; the deposit window then keeps
        checking on each status poll. Never raises: an exception here would
        abort a swap that is actually fine.
        """
        deadline = time.monotonic() + self.DEST_ECHO_BUDGET_SECONDS
        for delay in (0, *self.DEST_ECHO_RETRY_DELAYS):
            if delay:
                if time.monotonic() + delay > deadline:
                    break
                time.sleep(delay)
            if self._fetch_status(swap_id) is not None:
                echoed = self.destination_echo(swap_id)
                if echoed is not None:
                    return echoed
        return None

    def destination_echo(self, order_id) -> str | None:
        """The destination BaaS reported for this order on the most recent
        status read, or None. Read by the deposit window for the deferred
        destination check."""
        with self._state_lock:
            return self._dest_echo.get(str(order_id))

    def _fetch_status(self, order_id) -> dict | None:
        """The 'status' object for an order, or None if it can't be read.
        Records the reported destination as a side effect."""
        try:
            data = self._get(f"{self.BASE}/status-by-id",
                             params=self._params(swapId=str(order_id)))
        except (ProviderError, requests.RequestException):
            return None
        status = data.get("status") if isinstance(data, dict) else None
        if not isinstance(status, dict):
            return None
        dest = status.get("destinationAddress")
        if isinstance(dest, str) and dest.strip():
            # Capped because it can reach a label via the mismatch message.
            # No real address is this long, so truncation cannot turn a
            # mismatch into a match.
            with self._state_lock:
                self._dest_echo[str(order_id)] = dest.strip()[:200]
        return status

    def get_status(self, order_id) -> str:
        status = self._fetch_status(order_id)
        if status is None:
            return STATUS_UNKNOWN
        return self._classify(str(order_id), status)

    @staticmethod
    def _leg(value) -> dict | None:
        """Normalise one egress leg, or None if the leg is absent.

        settled follows BaaS's own rule: a leg is still moving while it has
        neither a witnessedAt time nor a transaction reference.
        """
        if not isinstance(value, dict):
            return None
        raw_ref = value.get("transactionReference")
        ref = raw_ref.strip() if isinstance(raw_ref, str) else ""
        return {
            "amount": _dec(value.get("amount")),
            "ref": ref if _TX_REF_RE.match(ref) else None,
            "settled": value.get("witnessedAt") is not None or bool(ref),
        }

    def _ticker_for(self, asset_id) -> str:
        if not isinstance(asset_id, str) or not _ASSET_ID_RE.match(asset_id):
            return ""
        for ticker, known in self.ASSETS.items():
            if known == asset_id:
                return ticker
        return asset_id.upper()

    def _classify(self, order_id: str, status: dict) -> str:
        """Map a status object to a normalised status.

        A fill-or-kill refund is reported as state "completed" with a refund
        leg and no swap leg; a partial fill as "completed" with both, which
        is reported as partial so it is never read as a plain success. A
        "failed" swap whose refund has been scheduled but not yet witnessed
        is still moving, and stays non-terminal until it lands.

        An unsettled leg is treated the same way under "completed" as it is
        under "failed": _leg() defines a leg with neither a witnessedAt time
        nor a transaction reference as still moving, and reporting one as a
        terminal Complete/Refunded contradicted that -- it stops polling and
        writes "Done. The coins were sent to your address" to history with
        nothing on-chain to point at. Every captured response carries one of
        the two fields on a finished leg, so this only bites when BaaS
        reports the state before the egress lands; the next poll resolves it.
        """
        state = str(status.get("state") or "").lower()
        payout = self._leg(status.get("swapEgress"))
        refund = self._leg(status.get("refundEgress"))

        if state == "completed":
            if payout and refund:
                result = STATUS_PARTIAL
            elif refund:
                result = STATUS_REFUNDED if refund["settled"] else STATUS_REFUNDING
            elif payout:
                result = STATUS_COMPLETE if payout["settled"] else STATUS_SENDING
            else:
                # Finished, but with nothing to say where the coins went.
                # Not reported as Complete without a payout to point at.
                result = STATUS_UNKNOWN
        elif state == "failed":
            if refund is None and payout:
                # Contradictory: failed, yet a payout leg. Not terminal until
                # the provider's own record settles which one happened.
                result = STATUS_UNKNOWN
            elif refund is None:
                result = STATUS_FAILED
            elif refund["settled"]:
                result = STATUS_REFUNDED
            else:
                result = STATUS_REFUNDING
        else:
            result = self._map_status(state, STATUS_UNKNOWN)

        notes = []
        if payout:
            notes.append(self._leg_note("Payout", payout, status.get("destinationAsset")))
        if refund:
            notes.append(self._leg_note("Refund", refund, status.get("sourceAsset")))
        self.notes.set(order_id, "\n".join(notes) if notes else None)
        return result

    def _leg_note(self, label: str, leg: dict, asset_id) -> str:
        text = label
        if leg["amount"] is not None:
            ticker = self._ticker_for(asset_id)
            text += f": {leg['amount'].normalize():f} {ticker}".rstrip()
        if leg["ref"]:
            text += f", tx {leg['ref']}"
        elif not leg["settled"]:
            text += ", not on-chain yet"
        return text
