"""providers.trocador: Trocador.app: privacy exchange AGGREGATOR."""
from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

import requests

from .base import ProviderError, Quote, Swap, SwapProvider, group_native_rows
from .constants import (
    COINS,
    STATUS_COMPLETE,
    STATUS_CONFIRMING,
    STATUS_EXPIRED,
    STATUS_NEEDS_ACTION,
    STATUS_PARTIAL,
    STATUS_REFUNDED,
    STATUS_SENDING,
    STATUS_UNKNOWN,
    STATUS_WAITING,
    TROCADOR_NETWORK,
    _dec,
)


# Trocador.app: privacy-focused EXCHANGE AGGREGATOR
#
# One integration fans out to 20+ underlying exchanges (FixedFloat, ChangeNOW,
# MajesticBank, StealthEx, Godex, etc). Trocador itself is non-custodial: the
# chosen partner settles directly to the user's address.
#
# Auth: a single free "API-Key" header (copied from your Trocador profile;
# no KYC). Confirmed against Trocador's own docs (api.trocador.app) that
# every endpoint needs it -- a live, unauthenticated /new_rate call returns
# HTTP 401 "Missing API key" -- so quotes are not attempted without one; an
# unconfigured Trocador is skipped from comparison instead of shown as a
# noisy, always-failing error card (see SwapProvider.can_quote_without_config).
class Trocador(SwapProvider):
    can_quote_without_config = False
    name = "Trocador"
    site = "https://trocador.app/en/register/affiliate"
    # Trocador's own docs (api.trocador.app) state the endpoint as
    # https://api.trocador.app/. The trocador.app/api host this used to point
    # at isn't the documented one (it works today, apparently via a reverse-
    # proxy alias, but carries no support guarantee).
    BASE = "https://api.trocador.app"

    _MAP: ClassVar[dict[str, str]] = {
        "new": STATUS_WAITING,
        "anonpaynew": STATUS_WAITING,
        "waiting": STATUS_WAITING,
        "confirming": STATUS_CONFIRMING,
        "sending": STATUS_SENDING,
        "finished": STATUS_COMPLETE,
        # NOT STATUS_COMPLETE: a partial-payment deposit may still need
        # top-up on the exchange side. See STATUS_PARTIAL above.
        "paid partially": STATUS_PARTIAL,
        # Trocador documents both of these as "contact support", not as an
        # outcome, and its trade guarantee describes halted trades being
        # settled later, as a refund or as a completed trade. STATUS_FAILED is
        # terminal, so reporting them that way stopped polling before the
        # trade reached whatever it reached.
        "failed": STATUS_NEEDS_ACTION,
        "expired": STATUS_EXPIRED,
        "halted": STATUS_NEEDS_ACTION,
        "refunded": STATUS_REFUNDED,
    }

    _NOTES: ClassVar[dict[str, str]] = {
        "failed": ("Trocador reports a problem with this trade and asks users "
                   "to contact its support with the trade id. It can still "
                   "settle as refunded or finished, so SwapDesk keeps checking."),
        "halted": ("Trocador has halted this trade and asks users to contact "
                   "its support with the trade id. It can still settle as "
                   "refunded or finished, so SwapDesk keeps checking."),
    }

    def __init__(self, api_key: str = "", timeout: int = 25):
        super().__init__(timeout)
        self.api_key = (api_key or "").strip()
        # ticker -> Trocador network label. Seeded from the curated
        # TROCADOR_NETWORK table; refresh_all_coins() replaces/extends this
        # with Trocador's own live /coins listing.
        self.networks: dict[str, str] = dict(TROCADOR_NETWORK)
        self.names: dict[str, str] = {t: v[0] for t, v in COINS.items()}
        # ticker -> Decimal. /coins is the only endpoint that documents
        # minimum/maximum tradeable amounts (a static per-coin figure, not
        # tied to a specific quote); /new_rate and /new_trade's own Results
        # lists don't include either field. Populated by fetch_coins();
        # empty (get_quote/create_swap then report min/max as unknown,
        # rather than the wrong field names getting a value out of thin
        # air) until the first coin refresh has run.
        self.minimums: dict[str, Decimal] = {}
        self.maximums: dict[str, Decimal] = {}

    def configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict:
        h = {}
        if self.api_key:
            h["API-Key"] = self.api_key
        return h

    def fetch_coins(self) -> dict:
        """Pull Trocador's full coin catalogue (GET /coins) and use it to
        REPLACE self.networks/self.names/self.minimums/self.maximums, so the
        app can offer every coin Trocador lists rather than just the
        hand-curated set. Trocador lists one row per (ticker, network),
        where a ticker has several we ONLY keep it if one row's network
        equals the ticker's known-native id -- `expected`, from the curated
        TROCADOR_NETWORK table -- checked against the live rows rather than
        just trusted because it's the only (or the ticker-matching) row
        present; see pick_native_network's docstring in base.py for why that
        distinction matters. A ticker with no such native match (e.g. a
        token that only exists as USDT/ETH, USDT/TRX, USDT/BSC, no row
        native to "usdt") is skipped instead of taking "the first row seen":
        this app has no per-coin network selector, so an arbitrary pick here
        means a deposit/settle address on a chain the user never chose and
        can't see, a wrong-network transfer is typically unrecoverable.
        Returns {ticker: display_name}; raises on failure so callers can
        leave the curated defaults (plus the "Mainnet" guess for anything
        still unknown) in place."""
        data = self._get(f"{self.BASE}/coins", headers=self._headers())
        rows = data if isinstance(data, list) else data.get("coins") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ProviderError(f"{self.name}: unexpected /coins response shape.")
        native_by_ticker, skipped_ambiguous = group_native_rows(
            rows,
            ticker_of=lambda r: str(r.get("ticker") or r.get("symbol") or "").upper(),
            network_of=lambda r: str(r.get("network") or r.get("chain") or "").strip(),
            expected=TROCADOR_NETWORK)
        networks = {t: str(r.get("network") or r.get("chain"))
                   for t, r in native_by_ticker.items()}
        names = {t: str(r.get("name") or t) for t, r in native_by_ticker.items()}
        minimums: dict[str, Decimal] = {}
        maximums: dict[str, Decimal] = {}
        for t, r in native_by_ticker.items():
            mn, mx = _dec(r.get("minimum")), _dec(r.get("maximum"))
            if mn is not None:
                minimums[t] = mn
            if mx is not None:
                maximums[t] = mx
        if not networks:
            raise ProviderError(f"{self.name}: /coins returned no usable native coins.")
        self.networks = networks
        self.names = names
        self.minimums = minimums
        self.maximums = maximums
        self.skipped_ambiguous_coins = skipped_ambiguous
        return dict(self.names)

    def _net(self, ticker: str) -> str:
        """This ticker's Trocador network label, or raise.

        Deliberately no "Mainnet" default any more. self.networks is seeded
        from the curated TROCADOR_NETWORK table, which covers every ticker in
        constants.COINS, so the only way to reach this with an unknown ticker
        is for fetch_coins() to have dropped it -- and fetch_coins drops a
        ticker precisely when its known-native network is NOT among the live
        /coins rows (see group_native_rows/pick_native_network). Falling back
        to "Mainnet" there re-made the exact guess that refusal exists to
        avoid, on both the quote and the /new_trade path, for a coin this
        app has no per-coin network selector to correct with. Unavailable is
        the honest answer.
        """
        net = self.networks.get(ticker.upper())
        if not net:
            raise ProviderError(
                f"{self.name}: {ticker.upper()} isn't in {self.name}'s "
                f"current coin list, so there is no network to send it on. "
                f"Refresh the coin list or pick another provider.")
        return net

    def _rate_params(self, from_coin, to_coin, amount) -> dict:
        return {
            "ticker_from": from_coin.lower(),
            "network_from": self._net(from_coin),
            "ticker_to": to_coin.lower(),
            "network_to": self._net(to_coin),
            "amount_from": str(amount),
            "payment": "False",
        }

    def get_quote(self, from_coin, to_coin, amount, destination: str = "") -> Quote:
        # Built before the request so a ticker with no known network reports
        # as an unsupported PAIR (the calm "nobody offers this" card) rather
        # than as a provider failure, matching how every other provider
        # answers for a coin it doesn't list.
        try:
            rate_params = self._rate_params(from_coin, to_coin, amount)
        except ProviderError as e:
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         error=str(e), unsupported=True)
        try:
            data = self._get(f"{self.BASE}/new_rate", params=rate_params,
                             headers=self._headers())
        except (ProviderError, requests.RequestException) as e:
            msg = str(e)
            if not self.api_key and ("401" in msg or "403" in msg or "key" in msg.lower()):
                msg = ("Trocador needs a free API-Key (get one on your Trocador "
                       "profile, then add it in Settings).")
            return Quote(self.name, from_coin, to_coin, amount, None, None, error=msg)

        # Trocador returns the best deal at the top level plus a `quotes` list.
        est = _dec(data.get("amount_to"))
        rate = (est / amount) if (est is not None and amount) else None
        quotes = data.get("quotes") or []
        best_via = None
        if isinstance(quotes, list) and quotes:
            # pick the highest amount_to as "best"
            def amt(q):
                return _dec(q.get("amount_to")) or Decimal(-1)
            best = max(quotes, key=amt)
            if _dec(best.get("amount_to")) is not None and (est is None or amt(best) > est):
                est = _dec(best.get("amount_to"))
                rate = (est / amount) if amount else None
            best_via = best.get("provider") or data.get("provider")
        best_via = best_via or data.get("provider")
        return Quote(self.name, from_coin, to_coin, amount, est, rate,
                     # /new_rate's own Results list has no minimum/maximum
                     # field (only /coins documents those, as a static
                     # per-coin figure); read from the catalogue fetch_coins()
                     # already populated instead of a field this endpoint
                     # never actually returns.
                     min_amount=self.minimums.get(from_coin.upper()),
                     max_amount=self.maximums.get(from_coin.upper()),
                     via=best_via, raw=data)

    def create_swap(self, from_coin, to_coin, amount, settle_address,
                    refund_address="") -> Swap:
        if not self.api_key:
            raise ProviderError("Trocador needs a free API-Key (add it in Settings).")
        # /new_trade requires `provider` and `fixed` -- both documented as
        # Mandatory (api.trocador.app) -- and neither is knowable in advance:
        # the server picks the winning underlying exchange and that
        # exchange's rate type. Trocador's own documented flow is "generate
        # a rate, then trade THAT rate's id/provider/fixed", so a fresh rate
        # is fetched here rather than trusting a quote the caller might
        # still be holding from get_quote(), which can go stale between
        # "Get best rate" and "Create swap".
        #
        # Computed ONCE and reused for both calls, not recomputed via
        # self._net() a second time when building the /new_trade payload:
        # self.networks is replaced wholesale (no lock) by fetch_coins(),
        # which app.py runs on a background thread at startup and after any
        # Settings save. If that swap landed between these two requests,
        # recomputing self._net() for the payload could send /new_trade a
        # DIFFERENT network than the one /new_rate priced the locked-in
        # `id` against. Reusing the same dict makes that impossible: the
        # network sent to /new_trade is provably the one /new_rate used.
        rate_params = self._rate_params(from_coin, to_coin, amount)
        rate = self._get(f"{self.BASE}/new_rate", params=rate_params,
                         headers=self._headers())
        if not isinstance(rate, dict):
            raise ProviderError(
                f"{self.name}: unexpected non-object response from the rate "
                f"lookup. Do not send funds. Response type: {type(rate).__name__}")
        rate_id = rate.get("trade_id")
        rate_provider = rate.get("provider")
        rate_fixed = rate.get("fixed")
        if not rate_id or not rate_provider or rate_fixed is None:
            raise ProviderError(
                f"{self.name}: rate lookup didn't return a trade id, exchange, "
                f"and rate type -- new_trade needs all three to create a real "
                f"order. Do not send funds. Try again.")
        payload = {
            "id": rate_id,
            "ticker_from": rate_params["ticker_from"],
            "network_from": rate_params["network_from"],
            "ticker_to": rate_params["ticker_to"],
            "network_to": rate_params["network_to"],
            "amount_from": rate_params["amount_from"],
            "payment": rate_params["payment"],
            "address": settle_address,
            # Mandatory per docs whenever the destination coin uses a memo;
            # '0' is Trocador's own documented value for "no memo needed".
            # This app has no destination-memo field to plumb through yet
            # (see providers/base.py), which is fine for every coin
            # currently in constants.COINS -- none of them are memo-tagged
            # chains -- so '0' is always correct today.
            "address_memo": "0",
            "provider": rate_provider,
            "fixed": rate_fixed,
        }
        if refund_address:
            payload["refund"] = refund_address
            payload["refund_memo"] = "0"
        # CRITICAL: Use POST so the destination/refund addresses travel in
        # the request body, not the URL, GET params get logged by proxies,
        # reverse proxies, and web-server access logs. If Trocador's API
        # ever rejects POST here, this must be reverted AND a prominent
        # privacy warning shown in the UI when Trocador is selected.
        data = self._post(f"{self.BASE}/new_trade", json=payload,
                          headers=self._headers(),
                          retry_on=self.ORDER_CREATE_RETRY_STATUSES)
        if not isinstance(data, dict):
            raise ProviderError(
                f"{self.name}: unexpected non-object response from create-swap "
                f"endpoint. Do not send funds. Response type: {type(data).__name__}")
        # Field names vary slightly across Trocador versions; read defensively.
        # address_provider / address_user are the names the current API docs
        # use; the _from/_to spellings are older and kept as fallbacks.
        deposit = (data.get("address_provider") or data.get("address_provider_from")
                   or data.get("address_from") or data.get("payinAddress")
                   or data.get("deposit_address") or "")
        memo = (data.get("address_provider_memo")
                or data.get("address_provider_from_memo") or data.get("memo_from")
                or data.get("payinExtraId"))
        trade_id = (data.get("trade_id") or data.get("id") or "")
        deposit = self._checked_deposit_address(deposit)
        if not trade_id:
            # None of the known field names matched, rather than silently
            # creating a Swap with a blank deposit address (which the UI
            # would happily render a QR code for and invite a deposit to),
            # fail loudly. This has bitten trades before when Trocador
            # renamed a response field across versions.
            raise ProviderError(
                f"{self.name}: response was missing a deposit address or "
                f"trade id. Do not send funds. Try again.")
        trade_id = self._checked_order_id(trade_id)
        send_amount = _dec(data.get("amount_from"))
        return Swap(
            provider=self.name,
            order_id=trade_id,
            from_coin=from_coin,
            to_coin=to_coin,
            deposit_address=deposit,
            deposit_memo=self._checked_memo(memo),
            # `is not None`, not `or`: a provider-echoed 0 is a real (if odd)
            # value, not the same as "field missing/unparseable" -- `x or
            # amount` couldn't tell those apart, since Decimal("0") is falsy.
            send_amount=send_amount if send_amount is not None else amount,
            deposit_min=self.minimums.get(from_coin.upper()),
            deposit_max=self.maximums.get(from_coin.upper()),
            estimated_receive=_dec(data.get("amount_to")),
            settle_address=settle_address,
            # /new_trade's documented Results list has no expiration field at
            # all (unlike /trade's nested quotes.expiresAt, a separate
            # status-lookup endpoint this app doesn't poll just to learn it),
            # so this is honestly unknown rather than read from field names
            # Trocador never actually sends here.
            expires_at=None,
            status=self._map_status(data.get("status"), STATUS_WAITING),
            raw=data,
            # Field name for the echoed destination varies across Trocador
            # versions/partners like the deposit-address fields above; try
            # the known candidates. None (no candidate present) means "can't
            # independently verify for this response", not "confirmed ok",
            # callers must not treat a missing field as a pass.
            settle_address_confirmed_by_provider=(
                data.get("address_user") or data.get("address_provider_to")
                or data.get("address_to") or data.get("recipient_address")),
        )

    def get_status(self, order_id) -> str:
        data = self._get(f"{self.BASE}/trade", params={"id": order_id},
                         headers=self._headers())
        state = str(data.get("status") or "").lower() if isinstance(data, dict) else ""
        self.notes.set(order_id, self._NOTES.get(state))
        return self._map_status(state, STATUS_UNKNOWN)


