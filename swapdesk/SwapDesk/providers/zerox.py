"""providers.zerox: 0x (DEX) (v2): on-chain DEX aggregator (quote-only)."""
from __future__ import annotations

from decimal import Decimal

import requests

from .base import ProviderError, Quote, Swap, SwapProvider
from .constants import STATUS_UNKNOWN, _dec

# 0x: on-chain DEX aggregator (Swap API v2)
#
# Fundamentally different from the other three: there's no deposit address
# and no custody anywhere. A quote is a live price across 100+ AMMs/market
# makers on one chain; EXECUTION happens by signing a transaction from your
# own wallet (MetaMask, etc.), not by sending funds to SwapDesk or a partner.
# So this only ever applies to pairs where BOTH sides are ERC-20/native
# tokens on the SAME chain. It can never route XMR/BTC/LTC/DOGE/BCH/DASH,
# since those aren't on any EVM chain to begin with.
#
# Auth: requires a free/paid API key from https://dashboard.0x.org/apps
# (0x stopped accepting unauthenticated requests on Swap API v2).
EVM_CHAIN_ID = 1  # Ethereum mainnet

# ticker -> (contract address, decimals). "ETH" uses 0x's native-ETH sentinel.
EVM_TOKENS: dict[str, tuple[str, int]] = {
    "ETH": ("0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE", 18),
    "USDC": ("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6),
    # Add more ERC-20s here as (ticker -> (contract, decimals)).
    # DEX pairs, e.g.:
    # "USDT": ("0xdAC17F958D2ee523a2206206994597C13D831ec7", 6),
    # "WBTC": ("0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", 8),
}


class ZeroExDEX(SwapProvider):
    name = "0x (DEX)"
    site = "https://dashboard.0x.org/apps"
    BASE = "https://api.0x.org"

    def __init__(self, api_key: str = "", timeout: int = 20):
        super().__init__(timeout)
        self.api_key = (api_key or "").strip()

    def configured(self) -> bool:
        # Only used for CREATING swaps elsewhere; 0x has nothing to "create"
        # server-side, so this just gates whether we can quote at all.
        return bool(self.api_key)

    def _headers(self) -> dict:
        return {"0x-api-key": self.api_key, "0x-version": "v2"}

    def _evm_pair_error(self, from_coin: str, to_coin: str) -> str | None:
        missing = [c for c in (from_coin, to_coin) if c not in EVM_TOKENS]
        if missing:
            return (f"{self.name}: DEX swaps only work between tokens on the "
                    f"same chain: {', '.join(missing)} isn't an EVM token "
                    f"SwapDesk knows about, so this pair can't route through "
                    f"a DEX.")
        if from_coin == to_coin:
            return f"{self.name}: pick two different tokens to swap."
        return None

    def get_quote(self, from_coin, to_coin, amount, destination: str = "") -> Quote:
        pair_err = self._evm_pair_error(from_coin, to_coin)
        if pair_err:
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         error=pair_err, unsupported=True)
        if not self.api_key:
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         error=f"{self.name}: add a free API key in Settings "
                               f"(from dashboard.0x.org) to fetch quotes.")
        sell_addr, sell_dec = EVM_TOKENS[from_coin]
        buy_addr, _ = EVM_TOKENS[to_coin]
        amount = _dec(amount)
        if amount is None or amount <= 0:
            return Quote(
                self.name, from_coin, to_coin, amount, None, None,
                error=f"{self.name}: amount must be greater than zero.",
            )
        atomic_amount = amount * (Decimal(10) ** sell_dec)
        if atomic_amount != atomic_amount.to_integral_value():
            return Quote(
                self.name, from_coin, to_coin, amount, None, None,
                error=(
                    f"{self.name}: {from_coin} supports at most "
                    f"{sell_dec} decimal places."
                ),
            )
        sell_amount = str(int(atomic_amount))
        params = {
            "chainId": str(EVM_CHAIN_ID),
            "sellToken": sell_addr,
            "buyToken": buy_addr,
            "sellAmount": sell_amount,
        }
        try:
            data = self._get(f"{self.BASE}/swap/allowance-holder/price",
                             params=params, headers=self._headers())
        except (ProviderError, requests.RequestException) as e:
            return Quote(self.name, from_coin, to_coin, amount, None, None,
                         error=str(e))
        buy_amount = _dec(data.get("buyAmount"))
        _, buy_dec = EVM_TOKENS[to_coin]
        est = (buy_amount / (Decimal(10) ** buy_dec)) if buy_amount is not None else None
        rate = (est / amount) if (est is not None and amount) else None
        sources = data.get("sources") or []
        via = ", ".join(s.get("name", "") for s in sources
                        if _dec(s.get("proportion")) and _dec(s.get("proportion")) > 0) or None
        return Quote(self.name, from_coin, to_coin, amount, est, rate,
                     via=via, raw=data)

    def create_swap(self, from_coin, to_coin, amount, settle_address,
                    refund_address="") -> Swap:
        # There is no custodial/partner deposit flow for a DEX: the trade is
        # signed and broadcast from the user's own wallet. SwapDesk can show
        # the quote but shouldn't pretend to "create" a swap here.
        raise ProviderError(
            f"{self.name}: DEX swaps aren't created through SwapDesk, they're "
            f"executed directly from your own wallet (e.g. MetaMask). Use the "
            f"quote above as a price reference, then swap in your wallet.")

    def get_status(self, order_id) -> str:
        return STATUS_UNKNOWN

# Fixed by j2sec
