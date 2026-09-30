from decimal import Decimal
import unittest

from providers.zerox import ZeroExDEX


class ZeroExTests(unittest.TestCase):
    def test_usdc_quote_uses_exact_atomic_amount(self):
        provider = ZeroExDEX(api_key="test-key")
        captured = {}

        def fake_get(url, **kwargs):
            captured["url"] = url
            captured["params"] = kwargs["params"]
            return {
                "buyAmount": "500000000000000",
                "sources": [{"name": "Uniswap_V3", "proportion": "1"}],
            }

        provider._get = fake_get
        quote = provider.get_quote("USDC", "ETH", "1.000001")

        self.assertTrue(quote.ok)
        self.assertEqual(captured["params"]["sellAmount"], "1000001")
        self.assertEqual(quote.send_amount, Decimal("1.000001"))
        self.assertEqual(quote.estimated_receive, Decimal("0.0005"))
        self.assertEqual(
            quote.rate, Decimal("0.0005") / Decimal("1.000001")
        )
        self.assertEqual(quote.via, "Uniswap_V3")

    def test_quote_rejects_more_decimals_than_token_supports_without_network_call(self):
        provider = ZeroExDEX(api_key="test-key")
        called = False

        def fake_get(*args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("network call should not happen")

        provider._get = fake_get
        quote = provider.get_quote("USDC", "ETH", "0.0000001")

        self.assertFalse(quote.ok)
        self.assertIn("supports at most 6 decimal places", quote.error)
        self.assertFalse(called)

    def test_quote_accepts_smallest_usdc_atomic_unit(self):
        provider = ZeroExDEX(api_key="test-key")
        captured = {}

        def fake_get(url, **kwargs):
            captured.update(kwargs["params"])
            return {"buyAmount": "1", "sources": []}

        provider._get = fake_get
        quote = provider.get_quote("USDC", "ETH", "0.000001")

        self.assertTrue(quote.ok)
        self.assertEqual(captured["sellAmount"], "1")

    def test_quote_rejects_zero_and_negative_amounts(self):
        provider = ZeroExDEX(api_key="test-key")

        zero = provider.get_quote("USDC", "ETH", "0")
        negative = provider.get_quote("USDC", "ETH", "-1")

        self.assertIn("greater than zero", zero.error)
        self.assertIn("greater than zero", negative.error)

    def test_quote_rejects_non_evm_pair_as_unsupported(self):
        provider = ZeroExDEX(api_key="test-key")
        quote = provider.get_quote("BTC", "ETH", "1")

        self.assertTrue(quote.unsupported)
        self.assertFalse(quote.ok)
        self.assertIn("isn't an EVM token", quote.error)

    def test_quote_requires_api_key_before_network_call(self):
        provider = ZeroExDEX(api_key="")
        quote = provider.get_quote("USDC", "ETH", "1")

        self.assertFalse(quote.ok)
        self.assertIn("add a free API key", quote.error)


if __name__ == "__main__":
    unittest.main()
