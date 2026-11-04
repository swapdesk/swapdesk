"""providers.changenow: ChangeNOW (v2): standard/floating flow."""
from __future__ import annotations

from contextlib import suppress
from typing import ClassVar

import requests

from .base import ProviderError, Quote, Swap, SwapProvider, group_native_rows
from .constants import (
    COINS,
    STATUS_COMPLETE,
    STATUS_CONFIRMING,
    STATUS_EXCHANGING,
    STATUS_EXPIRED,
    STATUS_FAILED,
    STATUS_NEEDS_ACTION,
    STATUS_REFUNDED,
    STATUS_SENDING,
    STATUS_UNKNOWN,
    STATUS_WAITING,
    _dec,
)


# ChangeNOW
class ChangeNow(SwapProvider):
    name = "ChangeNOW"
    site = "https://changenow.io/affiliate"
    BASE = "https://api.changenow.io/v2"

    _MAP: ClassVar[dict[str, str]] = {
        "new": STATUS_WAITING,
        "waiting": STATUS_WAITING,
        "confirming": STATUS_CONFIRMING,
        "exchanging": STATUS_EXCHANGING,
        "sending": STATUS_SENDING,
        "finished": STATUS_COMPLETE,
        "refunded": STATUS_REFUNDED,
        "failed": STATUS_FAILED,
        "expired": STATUS_EXPIRED,
        # Not a chain confirmation: ChangeNOW documents "verifying" as an
        # exchange it has stopped and flagged, which stays stopped until the
        # user completes the verification it links them to.
        "verifying": STATUS_NEEDS_ACTION,
    }

    _NOTES: ClassVar[dict[str, str]] = {
        "verifying": ("ChangeNOW has flagged this exchange and asks the user "
                      "to verify through the link it provides, which is valid "
                      "for three days. It continues once that passes."),
    }

    def __init__(self, api_key: str = "", timeout: int = 20):
        super().__init__(timeout)
        self.api_key = (api_key or "").strip()
        # ticker -> (changenow currency code, changenow network id). Seeded
        # from the curated COINS table; refresh_all_coins() replaces/extends
        # this with ChangeNOW's own live currency listing.
        self.currencies: dict[str, tuple[str, str]] = {
            t: (v[2], v[3]) for t, v in COINS.items()
        }
        self.names: dict[str, str] = {t: v[0] for t, v in COINS.items()}

    def configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["x-changenow-api-key"] = self.api_key
        return h

    def fetch_coins(self) -> dict:
        """Pull ChangeNOW's full currency catalogue (GET /v2/exchange/
        currencies) and use it to REPLACE self.currencies/self.names, so the
        app can offer every active coin ChangeNOW lists rather than just the
        hand-curated set. Needs the API key like every other v2 endpoint.
        ChangeNOW lists one row per (ticker, network) pair; where a ticker
        has several rows we ONLY keep it if one of those rows' network
        equals the ticker itself (the native-chain convention, e.g.
        BTC/btc), the one case where picking "the" network for that
        ticker isn't a guess. A ticker whose rows are all network-suffixed
        variants (e.g. a token that only exists as USDT-erc20/USDT-trc20/
        USDT-bsc, no row with network=="usdt") is skipped rather than
        falling back to isDefault or "the first active row": this app has
        no per-coin network selector, so defaulting to an arbitrary chain
        here means a deposit/settle address on a chain the user never
        picked and can't see. A wrong-network transfer is typically
        unrecoverable. Fiat and inactive rows are skipped. Returns
        {ticker: display_name}; raises on failure so callers can leave the
        curated defaults in place."""
        if not self.api_key:
            raise ProviderError(f"{self.name}: add an API key in Settings to "
                                f"fetch its coin list.")
        data = self._get(f"{self.BASE}/exchange/currencies",
                         params={"active": "true"}, headers=self._headers())
        if not isinstance(data, list):
            raise ProviderError(f"{self.name}: unexpected currencies response shape.")
        rows = [r for r in data if isinstance(r, dict) and not r.get("isFiat")]
        # Curated per-ticker expected network (constants.COINS' changenow_network
        # field, index 3). Passed through to pick_native_network so a ticker
        # whose only currently-listed row is a non-native/bridged variant (e.g.
        # FIRO briefly listed with a single "bsc" row, no native "firo" row at
        # all -- confirmed against ChangeNOW's live /currencies) is treated as
        # unavailable rather than silently trusted just because it's the only
        # option. See pick_native_network's docstring in base.py.
        expected = {t: v[3] for t, v in COINS.items()}
        native_by_ticker, skipped_ambiguous = group_native_rows(
            rows,
            ticker_of=lambda r: str(r.get("ticker") or "").upper(),
            network_of=lambda r: str(r.get("network") or "").strip(),
            expected=expected)
        currencies = {t: (str(r.get("ticker")), str(r.get("network")))
                     for t, r in native_by_ticker.items()}
        names = {t: str(r.get("name") or t) for t, r in native_by_ticker.items()}
        if not currencies:
            raise ProviderError(f"{self.name}: currencies list returned no usable native coins.")
        self.currencies = currencies
        self.names = names
        self.skipped_ambiguous_coins = skipped_ambiguous
        return dict(self.names)

    def _params(self, from_coin, to_coin) -> dict:
        f = self.currencies.get(from_coin.upper())
        t = self.currencies.get(to_coin.upper())
        if not f or not t:
            raise ProviderError(
                f"{self.name}: {from_coin if not f else to_coin} isn't in "
                f"{self.name}'s coin list.")
        f_cur, f_net = f
        t_cur, t_net = t
        return {
            "fromCurrency": f_cur, "toCurrency": t_cur,
            "fromNetwork": f_net, "toNetwork": t_net,
        }

    def get_quote(self, from_coin, to_coin, amount, destination: str = "") -> Quote:
        # ChangeNOW's v2 endpoints require the API key; without it the server
        # returns an HTML auth page. Fail fast with a clear, actionable note.
        if not self.api_key:
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         error="Add your ChangeNOW API key in Settings to fetch "
                               "quotes.")
        try:
            p = self._params(from_coin, to_coin)
        except ProviderError as e:
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         error=str(e), unsupported=True)
        p.update({"fromAmount": str(amount), "flow": "standard", "type": "direct"})
        try:
            data = self._get(f"{self.BASE}/exchange/estimated-amount",
                             params=p, headers=self._headers())
        except (ProviderError, requests.RequestException) as e:
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         error=str(e))
        est = _dec(data.get("toAmount"))
        rate = (est / amount) if (est is not None and amount) else None
        # Best-effort minimum (separate endpoint; ignore failures).
        mn = None
        with suppress(Exception):
            mp = self._params(from_coin, to_coin)
            mp["flow"] = "standard"
            md = self._get(f"{self.BASE}/exchange/min-amount",
                           params=mp, headers=self._headers())
            mn = _dec(md.get("minAmount"))
        # ChangeNOW reports transactionSpeedForecast as a range string
        # ("10-60"), which doesn't fit eta_minutes. Left unset rather than
        # guessed at; the raw value is still available in raw.
        return Quote(self.name, from_coin, to_coin, amount, est, rate,
                     min_amount=mn, eta_minutes=None, raw=data)

    def create_swap(self, from_coin, to_coin, amount, settle_address,
                    refund_address="") -> Swap:
        if not self.configured():
            raise ProviderError("ChangeNOW needs an API key (set it in Settings).")
        body = self._params(from_coin, to_coin)
        body.update({
            "fromAmount": str(amount),
            "toAmount": "",
            "address": settle_address,
            "refundAddress": refund_address,
            "flow": "standard",
            "type": "direct",
        })
        data = self._post(f"{self.BASE}/exchange",
                          headers=self._headers(), json=body,
                          retry_on=self.ORDER_CREATE_RETRY_STATUSES)
        if not isinstance(data, dict):
            raise ProviderError(
                f"{self.name}: unexpected non-object response from create-swap "
                f"endpoint. Do not send funds. Response type: {type(data).__name__}")
        order_id = data.get("id")
        deposit_address = data.get("payinAddress")
        deposit_address = self._checked_deposit_address(deposit_address)
        if not order_id:
            raise ProviderError(
                f"{self.name}: response was missing an order id or deposit "
                f"address. Do not send funds. Try again.")
        order_id = self._checked_order_id(order_id)
        send_amount = _dec(data.get("fromAmount"))
        return Swap(
            provider=self.name,
            order_id=order_id,
            from_coin=from_coin,
            to_coin=to_coin,
            deposit_address=deposit_address,
            deposit_memo=self._checked_memo(data.get("payinExtraId")),
            # `is not None`, not `or`: a provider-echoed 0 is a real (if odd)
            # value, not the same as "field missing/unparseable" -- `x or
            # amount` couldn't tell those apart, since Decimal("0") is falsy.
            send_amount=send_amount if send_amount is not None else amount,
            deposit_min=None,
            deposit_max=None,
            estimated_receive=_dec(data.get("toAmount")),
            settle_address=settle_address,
            expires_at=None,
            status=self._map_status(data.get("status"), STATUS_WAITING),
            raw=data,
            # ChangeNOW echoes back the destination as "payoutAddress".
            settle_address_confirmed_by_provider=data.get("payoutAddress"),
        )

    def get_status(self, order_id) -> str:
        data = self._get(f"{self.BASE}/exchange/by-id",
                         params={"id": order_id}, headers=self._headers())
        state = str(data.get("status") or "").lower() if isinstance(data, dict) else ""
        self.notes.set(order_id, self._NOTES.get(state))
        return self._map_status(state, STATUS_UNKNOWN)


