from decimal import Decimal
import unittest

from providers.base import group_native_rows, pick_native_network
from providers.constants import _dec


class ProviderHelperTests(unittest.TestCase):
    def test_dec_rejects_non_finite_and_extreme_values(self):
        for value in ("nan", "inf", "-inf", "1e31", "1e-31"):
            with self.subTest(value=value):
                self.assertIsNone(_dec(value))

    def test_dec_preserves_exact_decimal_value(self):
        self.assertEqual(
            _dec("0.123456789012345678"),
            Decimal("0.123456789012345678"),
        )

    def test_expected_native_network_wins_case_insensitively(self):
        networks = ["bsc", "Ethereum", "polygon"]
        self.assertEqual(
            pick_native_network(networks, "USDC", expected="ethereum"),
            "Ethereum",
        )

    def test_expected_native_network_missing_does_not_guess_bridged_singleton(self):
        self.assertIsNone(
            pick_native_network(["bsc"], "FIRO", expected="firo")
        )

    def test_single_network_is_accepted_without_curated_expectation(self):
        self.assertEqual(pick_native_network(["bitcoin"], "BTC"), "bitcoin")

    def test_ambiguous_networks_without_match_are_rejected(self):
        self.assertIsNone(pick_native_network(["erc20", "bsc"], "USDT"))

    def test_group_native_rows_drops_only_bridged_variant_when_native_expected(self):
        rows = [
            {"ticker": "FIRO", "network": "bsc", "id": 1},
            {"ticker": "BTC", "network": "btc", "id": 2},
        ]

        grouped, skipped = group_native_rows(
            rows,
            ticker_of=lambda row: row["ticker"],
            network_of=lambda row: row["network"],
            expected={"FIRO": "firo", "BTC": "btc"},
        )

        self.assertEqual(grouped, {"BTC": rows[1]})
        self.assertEqual(skipped, 1)


if __name__ == "__main__":
    unittest.main()
