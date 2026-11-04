"""providers.stealthex: StealthEX instant exchange.

Non-custodial and account-free in the ordinary case: no registration, funds
move wallet to wallet, and the integrator only needs an API key.

The caveat this provider carries, and the reason it is flagged in the UI
rather than presented like the others: StealthEX reserves the right to ask
for identity verification when a swap is flagged, which in practice means
large or otherwise unusual amounts. Their own description is "without
mandatory KYC or entering any private data, except the cases when
transactions are marked as suspicious". That is a real exception to this
app's premise, and a user who picked SwapDesk to avoid handing over
documents should be told before they commit funds, not after the deposit is
already on-chain and the payout is held.

`KYC_ON_FLAGGED` below drives that disclosure. Settings shows it next to the
toggle, and preflight raises it as a warning on every StealthEX swap.

API: https://api.stealthex.io (v2). The key goes in the X-SX-API-KEY header:
StealthEX supports it as an api_key query parameter too, and recommends the
header so the key does not end up in logs and proxy history.
"""
from __future__ import annotations

from typing import ClassVar

import requests

from .base import ProviderError, ProviderNetworkError, Quote, Swap, SwapProvider
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


class StealthEX(SwapProvider):
    name = "StealthEX"
    site = "https://stealthex.io/partners/api/"

    BASE = "https://api.stealthex.io/api/v2"

    # Read by the Settings section and by preflight. True means: this
    # provider can require identity verification on a swap it flags, so the
    # no-KYC premise does not hold unconditionally.
    KYC_ON_FLAGGED = True

    # ticker -> StealthEX currency symbol. StealthEX uses plain lowercase
    # tickers rather than chain-qualified ids, so this is a direct mapping
    # for the coins this app already knows. fetch_coins() replaces it with
    # the live /currency list.
    #
    # USDC is left out on purpose, matching FixedFloat's _CODES: unlike
    # every other provider in this app, StealthEX's create_swap/get_quote
    # send no network field alongside "usdc", and this app has never
    # confirmed against StealthEX's live /currency catalogue that a bare
    # "usdc" is unambiguously the Ethereum-mainnet USDC every other part of
    # the app (and the COINS registry) assumes. Until that's verified, offer
    # it via StealthEX's native-chain coins only and exclude USDC here, the
    # same way FixedFloat does for the same reason.
    SYMBOLS: ClassVar[dict[str, str]] = {t: t.lower() for t in COINS if t != "USDC"}

    _MAP: ClassVar[dict[str, str]] = {
        "waiting": STATUS_WAITING,
        "confirming": STATUS_CONFIRMING,
        "exchanging": STATUS_EXCHANGING,
        "sending": STATUS_SENDING,
        "finished": STATUS_COMPLETE,
        "refunded": STATUS_REFUNDED,
        # Their own term for "we want documents before this proceeds". The
        # user has to deal with it on StealthEX's side, so it is reported as
        # needing their input rather than as untrackable.
        "verifying": STATUS_NEEDS_ACTION,
        # StealthEX's current OpenAPI spec documents 9 exchange statuses, not
        # 8: "expired" is one of them (the exchange's deposit window closed
        # without a deposit arriving). Without this entry it fell through to
        # STATUS_UNKNOWN, which isn't terminal, so an expired exchange polled
        # forever and never told the user it was over.
        "expired": STATUS_EXPIRED,
        # "failed" is resolved in get_status(): StealthEX returns the deposit
        # to refund_address from this state, so it is the start of a refund
        # rather than the end of the exchange.
    }


    _NOTES: ClassVar[dict[str, str]] = {
        "verifying": ("StealthEX asks for verification when a wallet is "
                      "flagged or an exchange looks suspicious to its "
                      "liquidity provider. Once it passes, StealthEX either "
                      "completes the exchange or refunds it."),
        "failed-refunding": ("The exchange did not go through. StealthEX "
                             "returns the deposit to the refund address on "
                             "the exchange, so SwapDesk keeps checking until "
                             "it reports the refund."),
        "failed-no-address": ("The exchange did not go through and it carries "
                              "no refund address, so StealthEX has nowhere to "
                              "return the deposit to without being told."),
    }

    def __init__(self, api_key: str = "", timeout: int = 20):
        super().__init__(timeout)
        self.api_key = (api_key or "").strip()
        self.symbols: dict[str, str] = dict(self.SYMBOLS)
        self.names: dict[str, str] = {t: v[0] for t, v in COINS.items()}

    def configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict:
        # Documented alternative to the api_key query parameter, recommended
        # by StealthEX so the key stays out of request logs.
        return {"X-SX-API-KEY": self.api_key}

    @staticmethod
    def _params(**kw) -> dict:
        return {k: v for k, v in kw.items() if v is not None}

    def _json(self, r: requests.Response):
        """StealthEX reports errors as {"err": {"kind", "details"}}, which the
        shared parser does not read, so a refused request would show the whole
        body. Status handling matches the shared parser's."""
        if r.status_code >= 400:
            try:
                data = r.json()
            except ValueError:
                data = None
            err = data.get("err") if isinstance(data, dict) else None
            if isinstance(err, dict):
                reason = " ".join(str(err.get("details") or err.get("kind") or "").split())
                if reason:
                    text = f"{self.name}: HTTP {r.status_code}: {reason[:300]}"
                    if r.status_code in (401, 403):
                        text += "  (check your API key)"
                    if r.status_code in self._GATEWAY_STATUS:
                        raise ProviderNetworkError(text, status_code=r.status_code,
                                                   retry_after=self._parse_retry_after(r))
                    raise ProviderError(text)
        return super()._json(r)

    def _sym(self, ticker: str) -> str | None:
        return self.symbols.get(ticker.upper())

    def _unsupported(self, ticker: str) -> str:
        known = ", ".join(sorted(self.symbols)) or "none loaded"
        return f"{self.name}: {ticker} isn't in its enabled set here ({known})."

    def fetch_coins(self) -> dict:
        """Replace the symbol table from /currency.

        Restricted to tickers this app already has an address pattern for.
        StealthEX lists well over a thousand assets, and adding one here
        without a curated pattern would put a coin in the picker that
        preflight then refuses, which reads as a bug rather than a guard.
        """
        data = self._get(f"{self.BASE}/currency", headers=self._headers())
        if not isinstance(data, list):
            raise ProviderError(f"{self.name}: unexpected /currency response shape.")
        syms = {}
        for row in data:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or "").lower()
            tick = sym.upper()
            # USDC excluded here too: the live /currency list gives no
            # network qualification either, so an accepted "usdc" row would
            # be exactly as unverified as the curated SYMBOLS entry would
            # have been. See the SYMBOLS comment above.
            if sym and tick in COINS and tick != "USDC":
                syms[tick] = sym
        if not syms:
            raise ProviderError(f"{self.name}: /currency returned no usable coins.")
        self.symbols = syms
        self.names = {t: COINS[t][0] for t in syms}
        return dict(self.names)

    def get_quote(self, from_coin, to_coin, amount, destination: str = "") -> Quote:
        src, dst = self._sym(from_coin), self._sym(to_coin)
        if not src or not dst:
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         error=self._unsupported(from_coin if not src else to_coin),
                         unsupported=True)
        if not self.configured():
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         error=f"{self.name}: needs an API key (set it in Settings).")
        try:
            data = self._get(f"{self.BASE}/estimate/{src}/{dst}",
                             params=self._params(amount=str(amount)),
                             headers=self._headers())
        except (ProviderError, requests.RequestException) as e:
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         error=str(e))

        est = _dec(data.get("estimated_amount")) if isinstance(data, dict) else _dec(data)
        if est is None:
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         error=f"{self.name}: no estimate returned for this pair/amount.")
        rate = (est / amount) if amount else None

        # Best-effort: a failure here must not lose an otherwise good quote,
        # so the min/max are simply left unset.
        mn = mx = None
        try:
            rng = self._get(f"{self.BASE}/range/{src}/{dst}",
                            headers=self._headers())
            if isinstance(rng, dict):
                mn, mx = _dec(rng.get("min_amount")), _dec(rng.get("max_amount"))
        except (ProviderError, requests.RequestException):
            pass

        return Quote(self.name, from_coin, to_coin, amount, est, rate,
                     min_amount=mn, max_amount=mx, raw=data)

    def create_swap(self, from_coin, to_coin, amount, settle_address,
                    refund_address="") -> Swap:
        src, dst = self._sym(from_coin), self._sym(to_coin)
        if not src or not dst:
            raise ProviderError(self._unsupported(from_coin if not src else to_coin))
        if not self.configured():
            raise ProviderError(f"{self.name}: needs an API key "
                                f"(set it in Settings).")
        body = {
            "currency_from": src,
            "currency_to": dst,
            "address_to": settle_address,
            "amount_from": str(amount),
        }
        if refund_address:
            body["refund_address"] = refund_address
        data = self._post(f"{self.BASE}/exchange", headers=self._headers(), json=body,
                          retry_on=self.ORDER_CREATE_RETRY_STATUSES)
        if not isinstance(data, dict):
            raise ProviderError(f"{self.name}: unexpected /exchange response shape. "
                                f"Do not send funds.")
        deposit = data.get("address_from")
        order_id = data.get("id")
        deposit = self._checked_deposit_address(deposit)
        if not order_id:
            raise ProviderError(f"{self.name}: response had no deposit address "
                                f"or order id: try again.")
        return Swap(
            provider=self.name,
            order_id=self._checked_order_id(order_id),
            from_coin=from_coin,
            to_coin=to_coin,
            deposit_address=str(deposit),
            deposit_memo=self._checked_memo(data.get("extra_id_from")),
            send_amount=amount,
            deposit_min=None,
            deposit_max=None,
            estimated_receive=_dec(data.get("amount_to")),
            settle_address=settle_address,
            expires_at=None,
            status=self._map_status(data.get("status"), STATUS_WAITING),
            raw=data,
            # StealthEX echoes the destination it recorded, which is what
            # makes the independent destination check possible here.
            settle_address_confirmed_by_provider=data.get("address_to"),
        )

    def get_status(self, order_id) -> str:
        try:
            data = self._get(f"{self.BASE}/exchange/{order_id}",
                             headers=self._headers())
        except (ProviderError, requests.RequestException):
            return STATUS_UNKNOWN
        if not isinstance(data, dict):
            return STATUS_UNKNOWN
        state = str(data.get("status") or "").lower()
        if state == "failed":
            # StealthEX documents refund_address as where the deposit goes
            # back to when an exchange fails, and reports the return itself as
            # "refunded" afterwards. Reporting failure as the outcome here
            # would stop polling before that.
            refund_to = data.get("refund_address")
            if isinstance(refund_to, str) and refund_to.strip():
                self.notes.set(order_id, self._NOTES["failed-refunding"])
                return STATUS_REFUNDING
            self.notes.set(order_id, self._NOTES["failed-no-address"])
            return STATUS_NEEDS_ACTION
        self.notes.set(order_id, self._NOTES.get(state))
        return self._map_status(state, STATUS_UNKNOWN)

