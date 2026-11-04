"""providers.fixedfloat: FixedFloat (v2): instant non-custodial swap (signed requests)."""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import ClassVar

import requests

from .base import ProviderError, Quote, Swap, SwapProvider
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
    _epoch_utc,
)


# FixedFloat: instant, non-custodial swap (API v2)
#
# Same fund flow as SideShift/ChangeNOW/Trocador: the provider hands back a
# single-use deposit address and settles the output straight to the user's
# address. No end-user account; the developer needs an API key + secret.
#
# Two things make FixedFloat's API different from the others here:
#   1. EVERY request is signed. X-API-SIGN = HMAC-SHA256(secret, body), where
#      `body` is the EXACT JSON string sent. So we must serialize the body
#      once, sign that exact string, and send that same string as the raw
#      request body. Never re-serialize via requests' json= (that would
#      change the bytes and break the signature). Because signing needs the
#      key, FixedFloat can't do keyless "public" quotes the way SideShift can.
#   2. Responses are enveloped as {code, msg, data}; code 0 means success.
#
# Rate type is "float" (variable): the estimate is indicative and the real
# rate fixes when the deposit is seen, consistent with the other providers.
# "fixed" would lock the rate ~10 min but costs more and auto-cancels if the
# market moves >~1.2%, which is a worse fit for a compare-then-send flow.
class FixedFloat(SwapProvider):
    # Read by preflight, same pattern as the other disclosure flags. True
    # means create_swap accepts refund_address for interface parity and does
    # not transmit it: FixedFloat's /create takes no refund destination, and
    # one is only chosen later through its emergency endpoint. Without the
    # flag, preflight green-ticked the field's format and the user reasonably
    # concluded it had been used.
    IGNORES_REFUND_ADDRESS = True

    name = "FixedFloat"
    site = "https://ff.io/user/apikey"
    BASE = "https://ff.io/api/v2"
    RATE_TYPE = "float"

    # FixedFloat order status (UPPERCASE) -> normalized status.
    _MAP: ClassVar[dict[str, str]] = {
        "NEW": STATUS_WAITING,
        "PENDING": STATUS_CONFIRMING,
        "EXCHANGE": STATUS_EXCHANGING,
        "WITHDRAW": STATUS_SENDING,
        "DONE": STATUS_COMPLETE,
        "EXPIRED": STATUS_EXPIRED,
        # EMERGENCY = deposit arrived late/short/over and FixedFloat needs the
        # user to choose EXCHANGE-at-new-rate or REFUND on ff.io. SwapDesk has
        # no UI for that choice, so surface it as needs-attention, but NOT
        # as STATUS_FAILED, which is terminal and would stop polling before
        # the user has acted. Funds aren't lost; they're parked pending that
        # choice on the FixedFloat order page, and once made, this order's
        # status moves on to a real terminal one (DONE/REFUNDED/EXPIRED)
        # which polling will still be running to catch.
        "EMERGENCY": STATUS_NEEDS_ACTION,
    }

    # App ticker -> FixedFloat currency code. Only native coins with a stable
    # 1:1 code are listed; these match FixedFloat's `code` values. USDC is left
    # out on purpose: FixedFloat uses network-suffixed token codes whose exact
    # spelling (and the ERC-20 contract behind it) must be confirmed against
    # /api/v2/ccies before trusting it with funds. Add it there once verified.
    _CODES: ClassVar[dict[str, str]] = {
        "BTC": "BTC", "ETH": "ETH", "XMR": "XMR", "LTC": "LTC",
        "DOGE": "DOGE", "BCH": "BCH", "DASH": "DASH", "SOL": "SOL",
        "ZEC": "ZEC",
    }

    def __init__(self, api_key: str = "", api_secret: str = "", timeout: int = 20):
        super().__init__(timeout)
        self.api_key = (api_key or "").strip()
        self.api_secret = (api_secret or "").strip()
        # ticker -> FixedFloat currency code. Seeded from the curated
        # _CODES table; refresh_all_coins() replaces/extends this with
        # FixedFloat's own live /api/v2/ccies listing.
        self.codes: dict[str, str] = dict(self._CODES)
        self.names: dict[str, str] = {t: v[0] for t, v in COINS.items()}

    _EMERGENCY_NOTE = ("FixedFloat is holding this order because the deposit "
                       "arrived late, short or over. The choice between "
                       "continuing at the current rate and taking a refund is "
                       "made on ff.io with the order id and token.")

    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def fetch_coins(self) -> dict:
        """Pull FixedFloat's full currency catalogue (signed POST /ccies)
        and use it to REPLACE self.codes/self.names, so the app can offer
        every enabled coin FixedFloat lists rather than just the curated
        set. FixedFloat's `code` values are network-suffixed for tokens
        (e.g. "USDTTRC" vs "USDTERC") and plain for native coins (e.g.
        "BTC"), only native/single-network codes (code == the row's own
        `currency`/base ticker with no receiver-side ambiguity) are kept
        automatically here, matching this app's existing "don't guess a
        token's network" caution; multi-variant tokens are left for manual
        curation the same way USDC was. Returns {ticker: display_name};
        raises on failure (including when not configured, since every
        FixedFloat call is signed) so callers can leave curated defaults."""
        if not self.configured():
            raise ProviderError(f"{self.name}: add an API key + secret in "
                                f"Settings to fetch its coin list.")
        data = self._signed("ccies", {})
        rows = data if isinstance(data, list) else (
            list(data.values()) if isinstance(data, dict) else None)
        if not isinstance(rows, list):
            raise ProviderError(f"{self.name}: unexpected /ccies response shape.")
        codes: dict[str, str] = {}
        names: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code") or "").strip()
            # FixedFloat's /ccies response names the underlying-chain field
            # "coin" (confirmed against ff.io's own docs), not "currency" --
            # row.get("currency") was always None, so `or code` made this
            # always equal `code`, which made the native-only filter below
            # (`code.upper() != currency`) always False and never filter
            # anything, silently defeating it.
            currency = str(row.get("coin") or code).strip().upper()
            if not code or not currency:
                continue
            if row.get("send") == 0 and row.get("recv") == 0:
                continue  # fully disabled coin
            # Only keep it as the ticker's code if this IS the native/base
            # variant (code matches the underlying currency, e.g. BTC/BTC,
            # ETH/ETH), skip network-suffixed tokens like USDTTRC/USDTERC
            # since picking one silently would be exactly the kind of
            # network guess this app avoids elsewhere.
            if code.upper() != currency:
                continue
            codes[currency] = code
            names[currency] = str(row.get("name") or currency)
        if not codes:
            raise ProviderError(f"{self.name}: /ccies returned no usable coins.")
        self.codes = codes
        self.names = names
        return dict(self.names)

    def _code(self, ticker: str) -> str | None:
        return self.codes.get(ticker.upper())

    def _unsupported(self, ticker: str) -> str:
        # self.codes, not self._CODES: fetch_coins() REPLACES the curated
        # seed with FixedFloat's live catalogue, so naming the seed here
        # listed eight coins to a user whose install had a few hundred.
        known = "/".join(sorted(self.codes)) or "none loaded"
        return (f"{self.name}: {ticker} isn't in the enabled FixedFloat coin "
                f"set here ({known}).")

    def _signed_obj(self, method_path: str, params: dict, retry_on=None) -> dict:
        """_signed() for the endpoints whose `data` is a JSON object
        (/price, /create, /order). Anything else is flattened to {} so a
        caller's .get() chain can't raise on an unexpected shape; the
        caller's own "missing deposit address / order id" guard then fails
        loudly rather than proceeding on partial data."""
        data = self._signed(method_path, params, retry_on=retry_on)
        return data if isinstance(data, dict) else {}

    def _signed(self, method_path: str, params: dict, retry_on=None):
        """POST a signed request and return the unwrapped `data` payload,
        in whatever shape that endpoint uses (object for /price, /create,
        /order; list for /ccies). Raises ProviderError on a non-zero API
        code. retry_on narrows the shared gateway-retry set (base.py) --
        /create passes ORDER_CREATE_RETRY_STATUSES since a retried order
        creation could open a second real order; /price and /order are
        read-only and safe to retry in full."""
        # Explicit separators pin the serialization format so it never
        # drifts if Python's json.dumps defaults ever change. Verify
        # against FixedFloat's live API before altering these separators.
        body = json.dumps(params, ensure_ascii=False,
                          separators=(', ', ': '))     # the exact bytes we sign
        sign = hmac.new(self.api_secret.encode("utf-8"),
                        body.encode("utf-8"), hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "X-API-KEY": self.api_key,
            "X-API-SIGN": sign,
        }
        # data=body (raw), NOT json=params: re-serialization breaks the sign.
        resp = self._post(f"{self.BASE}/{method_path}", data=body, headers=headers,
                          retry_on=retry_on)
        if isinstance(resp, dict) and resp.get("code") not in (0, None):
            raise ProviderError(f"{self.name}: {resp.get('msg') or resp.get('code')}")
        # Return `data` in whatever shape the endpoint actually uses and let
        # each caller assert what it expects. /price, /create and /order
        # return an object; /ccies returns a LIST. Coercing a non-dict to {}
        # here (the previous behaviour) meant fetch_coins() saw an empty
        # payload on a perfectly healthy /ccies response and raised
        # "returned no usable coins" every single time, which then surfaced
        # in the coin-refresh status line as FixedFloat being unreachable or
        # unconfigured. `data` absent entirely still yields {} so the
        # object-shaped callers' .get() chains are unaffected.
        data = resp.get("data") if isinstance(resp, dict) else None
        return {} if data is None else data

    def _friendly_errors(self, errs: list, from_coin: str, to_coin: str) -> str:
        s = set(errs or [])
        if "LIMIT_MIN" in s:
            return f"{self.name}: amount is below the minimum for {from_coin}."
        if "LIMIT_MAX" in s:
            return f"{self.name}: amount is above the maximum for {from_coin}."
        if s & {"MAINTENANCE_FROM", "OFFLINE_FROM", "RESERVE_FROM"}:
            return (f"{self.name}: {from_coin} is temporarily unavailable to send "
                    f"(maintenance/low reserves). Try another provider.")
        if s & {"MAINTENANCE_TO", "OFFLINE_TO", "RESERVE_TO"}:
            return (f"{self.name}: {to_coin} is temporarily unavailable to receive "
                    f"(maintenance/low reserves). Try another provider.")
        return f"{self.name}: {', '.join(sorted(s))}"

    def get_quote(self, from_coin, to_coin, amount, destination: str = "") -> Quote:
        # Signed request needs the key+secret, so quotes require them too.
        if not self.configured():
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         error="Add your FixedFloat API key + secret in Settings "
                               "to fetch quotes.")
        frm, to = self._code(from_coin), self._code(to_coin)
        if not frm or not to:
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         error=self._unsupported(from_coin if not frm else to_coin),
                         unsupported=True)
        try:
            data = self._signed_obj("price", {
                "type": self.RATE_TYPE, "fromCcy": frm, "toCcy": to,
                "direction": "from", "amount": str(amount)})
        except (ProviderError, requests.RequestException) as e:
            return Quote(self.name, from_coin, to_coin, amount, None, None, error=str(e))
        errs = data.get("errors") or []
        if errs:
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         error=self._friendly_errors(errs, from_coin, to_coin))
        frm_obj, to_obj = data.get("from") or {}, data.get("to") or {}
        est = _dec(to_obj.get("amount"))
        rate = (est / amount) if (est is not None and amount) else None
        return Quote(self.name, from_coin, to_coin, amount, est, rate,
                     min_amount=_dec(frm_obj.get("min")),
                     max_amount=_dec(frm_obj.get("max")), raw=data)

    def create_swap(self, from_coin, to_coin, amount, settle_address,
                    refund_address="") -> Swap:
        if not self.configured():
            raise ProviderError("FixedFloat needs an API key + secret "
                                "(set them in Settings).")
        frm, to = self._code(from_coin), self._code(to_coin)
        if not frm or not to:
            raise ProviderError(self._unsupported(from_coin if not frm else to_coin))
        # FixedFloat's /create takes no refund address. A refund destination is
        # only chosen later (if needed) via its emergency endpoint. refund_address
        # is accepted for interface parity but intentionally not sent.
        data = self._signed_obj("create", {
            "type": self.RATE_TYPE, "fromCcy": frm, "toCcy": to,
            "direction": "from", "amount": str(amount), "toAddress": settle_address},
            retry_on=self.ORDER_CREATE_RETRY_STATUSES)
        # The order exists by now: a wrongly-shaped field has to end in the
        # guards below (ProviderError), not a raw AttributeError.
        frm_obj, to_obj, time_obj = (
            v if isinstance(v, dict) else {}
            for v in (data.get("from"), data.get("to"), data.get("time")))
        deposit = frm_obj.get("address")
        ff_id, token = data.get("id"), data.get("token")
        deposit = self._checked_deposit_address(deposit)
        if not ff_id or not token:
            raise ProviderError(
                f"{self.name}: response was missing a deposit address, order id, "
                f"or token. Do not send funds. Try again.")
        ff_id, token = self._checked_order_id(ff_id), self._checked_order_id(token)
        if ":" in ff_id:
            # get_status splits "id:token" on the first colon.
            raise ProviderError(f"{self.name}: response gave an unusable order "
                                f"id. Do not send funds. Try again.")
        expires_at = _epoch_utc(time_obj.get("expiration"))
        send_amount = _dec(frm_obj.get("amount"))
        return Swap(
            provider=self.name,
            # id + token, colon-joined. get_status needs BOTH, but the app only
            # passes order_id back to it, so the access token rides along here
            # (it stays in local history only).
            order_id=f"{ff_id}:{token}",
            from_coin=from_coin,
            to_coin=to_coin,
            deposit_address=deposit,
            deposit_memo=self._checked_memo(frm_obj.get("tag")),
            # `is not None`, not `or`: a provider-echoed 0 is a real (if odd)
            # value, not the same as "field missing/unparseable" -- `x or
            # amount` couldn't tell those apart, since Decimal("0") is falsy.
            send_amount=send_amount if send_amount is not None else amount,
            deposit_min=None,
            deposit_max=None,
            estimated_receive=_dec(to_obj.get("amount")),
            settle_address=settle_address,
            expires_at=expires_at,
            status=self._map_status(data.get("status"), STATUS_WAITING, upper=True),
            raw=data,
            # FixedFloat's "to" object includes "address": the recipient
            # address it recorded for the to-currency leg.
            settle_address_confirmed_by_provider=to_obj.get("address"),
        )

    def get_status(self, order_id) -> str:
        ff_id, _, token = (order_id or "").partition(":")
        if not token:
            return STATUS_UNKNOWN     # can't query FixedFloat without the token
        try:
            data = self._signed_obj("order", {"id": ff_id, "token": token})
        except (ProviderError, requests.RequestException):
            return STATUS_UNKNOWN
        status = self._map_status(data.get("status"), STATUS_UNKNOWN, upper=True)
        note = self._EMERGENCY_NOTE if status == STATUS_NEEDS_ACTION else None
        # FixedFloat documents "back" as the refund leg and "to" as the payout
        # leg, each with its own tx. DONE only says the order is finished, so
        # it is not read as a payout when the evidence says the deposit went
        # back instead.
        emergency = data.get("emergency")
        choice = str(emergency.get("choice") or "").upper() if isinstance(emergency, dict) else ""
        refund_tx, payout_tx = self._tx_id(data.get("back")), self._tx_id(data.get("to"))
        if not payout_tx and (refund_tx or choice == "REFUND"):
            status = (STATUS_REFUNDED if refund_tx and status == STATUS_COMPLETE
                      else STATUS_REFUNDING)
            note = (f"FixedFloat is returning this deposit (refund tx {refund_tx})."
                    if refund_tx else
                    "A refund was chosen for this order; the refund transaction "
                    "has not been reported yet.")
        self.notes.set(order_id, note)
        return status

    @staticmethod
    def _tx_id(leg) -> str | None:
        """The transaction id recorded on a "to"/"back" leg, or None."""
        tx = leg.get("tx") if isinstance(leg, dict) else None
        tx_id = tx.get("id") if isinstance(tx, dict) else None
        if not isinstance(tx_id, str) or not tx_id.strip():
            return None
        tx_id = tx_id.strip()
        return tx_id if len(tx_id) <= 128 and tx_id.isprintable() else None

