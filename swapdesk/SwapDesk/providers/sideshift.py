"""providers.sideshift: SideShift.ai (v2): variable-rate shifts."""
from __future__ import annotations

from typing import ClassVar

import requests

from .base import (
    ProviderError,
    ProviderNetworkError,
    Quote,
    Swap,
    SwapProvider,
    pick_native_network,
)
from .constants import (
    COINS,
    STATUS_COMPLETE,
    STATUS_CONFIRMING,
    STATUS_EXCHANGING,
    STATUS_EXPIRED,
    STATUS_NEEDS_ACTION,
    STATUS_REFUNDED,
    STATUS_REFUNDING,
    STATUS_SENDING,
    STATUS_UNKNOWN,
    STATUS_WAITING,
    _dec,
)


# SideShift.ai
class SideShift(SwapProvider):
    can_quote_without_config = True  # /pair is public; secret only needed to create
    name = "SideShift"
    site = "https://sideshift.ai/account"
    BASE = "https://sideshift.ai/api/v2"

    # SideShift shift status -> normalized status
    _MAP: ClassVar[dict[str, str]] = {
        "waiting": STATUS_WAITING,
        "pending": STATUS_CONFIRMING,
        "processing": STATUS_EXCHANGING,
        "review": STATUS_EXCHANGING,
        "settling": STATUS_SENDING,
        "settled": STATUS_COMPLETE,
        # Only "refunded" means the coins are back. "refunding" is the
        # transfer in flight, and "refund" is queued: mapping either to
        # STATUS_REFUNDED, which is terminal, stops polling and records a
        # refund that has not happened. "refund" is resolved in get_status()
        # because it depends on the shift itself.
        "refunding": STATUS_REFUNDING,
        "refunded": STATUS_REFUNDED,
        "expired": STATUS_EXPIRED,
        "multiple": STATUS_EXCHANGING,
    }

    def __init__(self, secret: str = "", affiliate_id: str = "", timeout: int = 20):
        super().__init__(timeout)
        self.secret = (secret or "").strip()
        self.affiliate_id = (affiliate_id or "").strip()
        # ticker -> SideShift network id. Seeded from the curated COINS table
        # so the app works before any fetch_coins() call; refresh_all_coins()
        # replaces/extends this with SideShift's own live /v2/coins listing.
        self.networks: dict[str, str] = {t: v[1] for t, v in COINS.items()}
        self.names: dict[str, str] = {t: v[0] for t, v in COINS.items()}

    def configured(self) -> bool:
        return bool(self.secret and self.affiliate_id)

    def _auth_headers(self) -> dict:
        # NOTE: x-user-ip is intentionally NOT set. That header is only for
        # requests made from an integration's SERVER on behalf of a user.
        # A self-hosted desktop app's requests originate from the user's own
        # IP, which is exactly what SideShift expects for a client integration.
        return {"x-sideshift-secret": self.secret, "Content-Type": "application/json"}

    def fetch_coins(self) -> dict:
        """Pull SideShift's full public coin catalogue (GET /v2/coins, no
        auth needed) and use it to REPLACE self.networks/self.names, so the
        app can offer every coin SideShift actually lists rather than just
        the hand-curated set.

        Each row carries its own authoritative "mainnet" field naming which
        of its listed networks is native -- confirmed live (e.g. BTC:
        networks ["bitcoin","liquid"], mainnet "bitcoin"; ETH: six networks
        including L2s, mainnet "ethereum"). That field is checked first and
        wins outright when present. Without it (a defensive fallback, not
        the expected path), the curated network id from constants.COINS is
        tried next via pick_native_network's `expected` parameter, and only
        as a last resort does a genuinely unlabelled multi-network coin fall
        back to the ticker-string-match heuristic. This matters because a
        plain ticker-match can't find the native row at all once a coin has
        several networks named as full chain words ("bitcoin"/"ethereum")
        rather than short tickers -- that used to make BOTH BTC and ETH
        "ambiguous" and drop them from SideShift entirely on every routine
        coin-list refresh, even though each one names its own native network
        explicitly. A multi-network coin with no usable signal at all is
        skipped rather than defaulted: this app has no per-coin network
        selector in the UI, so a wrong guess here means depositing to (or
        settling on) a chain the user never chose and can't see, which is how
        funds get sent across incompatible networks and lost. Returns
        {ticker: display_name} for the merged GUI list; raises on failure so
        callers can leave the curated defaults in place."""
        data = self._get(f"{self.BASE}/coins")
        if not isinstance(data, list):
            raise ProviderError(f"{self.name}: unexpected /coins response shape.")
        networks: dict[str, str] = {}
        names: dict[str, str] = {}
        skipped_ambiguous = 0
        for row in data:
            if not isinstance(row, dict):
                continue
            coin = str(row.get("coin") or "").strip()
            if not coin:
                continue
            ticker = coin.upper()
            nets = row.get("networks") or []
            if not nets:
                continue  # no networks listed at all, not the same as "ambiguous"
            mainnet = row.get("mainnet")
            expected = (str(mainnet).strip() if isinstance(mainnet, str) and mainnet.strip()
                       else COINS.get(ticker, (None, None, None, None))[1])
            native = pick_native_network(nets, coin, expected=expected)
            if native is None:
                skipped_ambiguous += 1
                continue
            networks[ticker] = native
            names[ticker] = str(row.get("name") or ticker)
        if not networks:
            raise ProviderError(f"{self.name}: /coins returned no usable coins.")
        self.networks = networks
        self.names = names
        self.skipped_ambiguous_coins = skipped_ambiguous
        return dict(self.names)

    def _pair_id(self, ticker: str) -> str:
        network = self.networks.get(ticker.upper())
        if not network:
            raise ProviderError(
                f"{self.name}: {ticker} isn't in {self.name}'s coin list.")
        return f"{ticker.lower()}-{network}"

    def get_quote(self, from_coin, to_coin, amount, destination: str = "") -> Quote:
        # /v2/pair is PUBLIC: works without a secret, great for comparison.
        # destination isn't used. A SideShift rate quote is independent of it.
        try:
            frm, to = self._pair_id(from_coin), self._pair_id(to_coin)
        except ProviderError as e:
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         error=str(e), unsupported=True)
        try:
            data = self._get(f"{self.BASE}/pair/{frm}/{to}",
                             params={"amount": str(amount)})
        except (ProviderError, requests.RequestException) as e:
            msg = str(e)
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         error=self._friendly_error(msg, from_coin, to_coin),
                         unsupported=self._pair_unsupported(msg))
        # Some errors come back 200 with an "error" field in the body
        # (disabled coin on an otherwise-valid pair).
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         error=self._friendly_error(
                             f"{self.name}: {msg}", from_coin, to_coin),
                         unsupported=self._pair_unsupported(str(msg)))
        rate = _dec(data.get("rate"))
        mn = _dec(data.get("min"))
        mx = _dec(data.get("max"))
        est = _dec(data.get("settleAmount"))
        if est is None and rate is not None:
            est = amount * rate
        return Quote(self.name, from_coin, to_coin, amount, est, rate,
                     min_amount=mn, max_amount=mx, raw=data)

    def _friendly_error(self, raw_msg: str, from_coin: str, to_coin: str) -> str:
        """Translate SideShift's raw API error text into a clearer message
        for coin-level outages (e.g. a coin disabled for deposits/settling)."""
        low = raw_msg.lower()
        if "deposit method is disabled" in low or "deposit coin is disabled" in low:
            return (f"{self.name}: {from_coin} deposits are currently disabled "
                    f"on SideShift (this happens per-coin, often for regulatory "
                    f"reasons). Try Trocador or ChangeNOW instead.")
        if "settle method is disabled" in low or "settle coin is disabled" in low:
            return (f"{self.name}: {to_coin} payouts are currently disabled "
                    f"on SideShift. Try Trocador or ChangeNOW instead.")
        return raw_msg

    @staticmethod
    def _pair_unsupported(raw_msg: str) -> bool:
        """True when `raw_msg` describes SideShift not offering this pair at
        all right now (a coin disabled for deposits/settling), as opposed to
        a transient/network/config problem. Several currently-curated coins
        (live-checked: XMR, ZEC, FIRO, DCR, ARRR, BEAM, KAS) return exactly
        this shape today rather than a clean pair-not-found. Distinguishing
        it lets the UI say "nobody offers this pair" instead of the harsher
        "something's actually broken" -- see Quote.unsupported's docstring.
        Only the pair-lookup call itself failing reaches here; a ticker not
        being in self.networks at all is already caught earlier in
        get_quote() via _pair_id(), which sets unsupported=True itself."""
        low = raw_msg.lower()
        return ("deposit method is disabled" in low or "deposit coin is disabled" in low
                or "settle method is disabled" in low or "settle coin is disabled" in low)

    def create_swap(self, from_coin, to_coin, amount, settle_address,
                    refund_address="") -> Swap:
        if not self.configured():
            raise ProviderError("SideShift needs an account secret + affiliate ID "
                                "(set them in Settings).")
        dep_net = self.networks.get(from_coin.upper())
        set_net = self.networks.get(to_coin.upper())
        if not dep_net or not set_net:
            raise ProviderError(
                f"{self.name}: {from_coin if not dep_net else to_coin} isn't "
                f"in {self.name}'s coin list.")
        body = {
            "affiliateId": self.affiliate_id,
            "settleAddress": settle_address,
            "depositCoin": from_coin.lower(),
            "settleCoin": to_coin.lower(),
            "depositNetwork": dep_net,
            "settleNetwork": set_net,
        }
        if refund_address:
            body["refundAddress"] = refund_address
        # Variable-rate shift: one call, 7-day window, rate locks on deposit.
        try:
            data = self._post(f"{self.BASE}/shifts/variable",
                              headers=self._auth_headers(), json=body,
                              retry_on=self.ORDER_CREATE_RETRY_STATUSES)
        except ProviderNetworkError as e:
            # Keep the type: the deposit window tells the user the order may
            # already exist only for a network failure.
            raise ProviderNetworkError(
                self._friendly_error(str(e), from_coin, to_coin),
                status_code=e.status_code, retry_after=e.retry_after) from e
        except (ProviderError, requests.RequestException) as e:
            raise ProviderError(self._friendly_error(str(e), from_coin, to_coin))
        if not isinstance(data, dict):
            raise ProviderError(
                f"{self.name}: unexpected non-object response from create-swap "
                f"endpoint. Do not send funds. Response type: {type(data).__name__}")
        order_id = data.get("id")
        deposit_address = data.get("depositAddress")
        deposit_address = self._checked_deposit_address(deposit_address)
        if not order_id:
            # Don't hand back a Swap with a blank/partial deposit address,
            # that's the one field that decides where real money goes.
            raise ProviderError(
                f"{self.name}: response was missing an order id or deposit "
                f"address. Do not send funds. Try again.")
        order_id = self._checked_order_id(order_id)
        return Swap(
            provider=self.name,
            order_id=order_id,
            from_coin=from_coin,
            to_coin=to_coin,
            deposit_address=deposit_address,
            deposit_memo=self._checked_memo(data.get("depositMemo")),
            send_amount=amount,
            deposit_min=_dec(data.get("depositMin")),
            deposit_max=_dec(data.get("depositMax")),
            estimated_receive=None,  # unknown until deposit for variable shifts
            settle_address=settle_address,
            expires_at=data.get("expiresAt"),
            status=self._map_status(data.get("status"), STATUS_WAITING),
            raw=data,
            # SideShift's own create-shift response echoes back the
            # settleAddress it recorded. Read it independently rather than
            # trusting the local variable we sent.
            settle_address_confirmed_by_provider=data.get("settleAddress"),
        )

    def get_status(self, order_id) -> str:
        data = self._get(f"{self.BASE}/shifts/{order_id}")
        state = data.get("status") if isinstance(data, dict) else None
        if str(state or "").lower() == "refund":
            # A refund address is optional on a shift. Without one SideShift
            # holds the refund and asks for an address on its own order page,
            # which only the user can supply.
            refund_to = data.get("refundAddress")
            if isinstance(refund_to, str) and refund_to.strip():
                return STATUS_REFUNDING
            return STATUS_NEEDS_ACTION
        return self._map_status(state, STATUS_UNKNOWN)


