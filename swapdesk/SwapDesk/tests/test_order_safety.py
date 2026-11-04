"""Order-creation safety, exercised against a mocked HTTP session.

No network access and no real orders: every provider's `session.post` is
replaced with a fake. These tests pin the rule that an ambiguous failure
(a read timeout, or a 500/502/504 after the request was sent) must never be
retried by the shared request layer, because the order may already exist at
the provider and a retry would open a second one.
"""

import unittest
from decimal import Decimal
from unittest import mock

import requests

from providers import ChangeNow, FixedFloat, SideShift, StealthEX, Trocador
from providers import base
from providers.base import ProviderError, ProviderNetworkError

ADDR = "bc1qexampleexampleexampleexampleexample"


def _response(status=200, body=b'{"ok": true}', headers=None):
    r = requests.Response()
    r.status_code = status
    r._content = body
    r.headers.update(headers or {"Content-Type": "application/json"})
    return r


def _make_providers():
    return [
        ChangeNow(api_key="test"),
        FixedFloat(api_key="test", api_secret="test"),
        SideShift(secret="test"),
        Trocador(api_key="test"),
        StealthEX(api_key="test"),
    ]


class _Isolated(unittest.TestCase):
    def setUp(self):
        # Keep DNS-over-HTTPS out of the picture and never really sleep.
        for p in (
            mock.patch.object(base._dns, "_DOH_ENABLED", False),
            mock.patch.object(base.time, "sleep"),
        ):
            p.start()
            self.addCleanup(p.stop)


class AmbiguousFailureIsNeverRetried(_Isolated):
    def _create(self, provider):
        return provider.create_swap("BTC", "XMR", Decimal("0.01"), ADDR, ADDR)

    def test_server_error_on_create_never_repeats_the_order_post(self):
        # Reads (e.g. Trocador's new_rate GET) may be retried; the request
        # that opens the order may not, whatever the provider.
        for status in (500, 502, 504):
            for provider in _make_providers():
                with self.subTest(provider=provider.name, status=status):
                    post = mock.Mock(return_value=_response(status, b'{"error": "boom"}'))
                    provider.session.post = post
                    provider.session.get = mock.Mock(
                        return_value=_response(status, b'{"error": "boom"}'))
                    with self.assertRaises(ProviderError):
                        self._create(provider)
                    self.assertLessEqual(post.call_count, 1)

    def test_providers_that_reach_the_order_endpoint_send_it_once(self):
        # Guards against the vacuous pass above: these three are known to
        # POST the order straight away, so the count must be exactly one.
        for provider in (ChangeNow(api_key="test"),
                         FixedFloat(api_key="test", api_secret="test"),
                         StealthEX(api_key="test")):
            with self.subTest(provider=provider.name):
                post = mock.Mock(return_value=_response(500, b'{"error": "boom"}'))
                provider.session.post = post
                with self.assertRaises(ProviderNetworkError):
                    self._create(provider)
                self.assertEqual(post.call_count, 1)

    def test_read_timeout_on_create_is_not_retried(self):
        provider = ChangeNow(api_key="test")
        post = mock.Mock(side_effect=requests.exceptions.ReadTimeout("slow"))
        provider.session.post = post
        with self.assertRaises(ProviderNetworkError):
            self._create(provider)
        self.assertEqual(post.call_count, 1)

    def test_order_post_is_not_repeated_after_http_500(self):
        provider = ChangeNow(api_key="test")
        post = mock.Mock(return_value=_response(500, b'{"error": "boom"}'))
        provider.session.post = post
        with self.assertRaises(ProviderNetworkError):
            self._create(provider)
        self.assertEqual(post.call_count, 1)

    def test_not_acted_on_statuses_are_retried_then_succeed(self):
        provider = ChangeNow(api_key="test")
        good = _response(200, (
            b'{"id": "abc123", "payinAddress": "' + ADDR.encode() + b'", '
            b'"fromAmount": "0.01", "toAmount": "1.5"}'))
        post = mock.Mock(side_effect=[_response(503, b'{"error": "busy"}'), good])
        provider.session.post = post
        swap = self._create(provider)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(swap.order_id, "abc123")

    def test_retries_are_bounded(self):
        provider = ChangeNow(api_key="test")
        post = mock.Mock(return_value=_response(429, b'{"error": "slow down"}'))
        provider.session.post = post
        with self.assertRaises(ProviderNetworkError):
            self._create(provider)
        self.assertEqual(post.call_count, provider.MAX_RETRIES + 1)


class MalformedCreateResponses(_Isolated):
    def _create_with(self, body, status=200):
        provider = ChangeNow(api_key="test")
        provider.session.post = mock.Mock(return_value=_response(status, body))
        return provider.create_swap("BTC", "XMR", Decimal("0.01"), ADDR, ADDR)

    def test_non_object_json_is_rejected_with_do_not_send_funds(self):
        with self.assertRaisesRegex(ProviderError, "Do not send funds"):
            self._create_with(b"[1, 2, 3]")

    def test_blank_or_non_string_deposit_address_is_rejected(self):
        for addr in ('" "', "null", "12345", '"bad\\u0000addr"'):
            with self.subTest(addr=addr):
                body = ('{"id": "abc", "payinAddress": %s}' % addr).encode()
                with self.assertRaises(ProviderError):
                    self._create_with(body)

    def test_missing_order_id_is_rejected(self):
        body = b'{"payinAddress": "' + ADDR.encode() + b'"}'
        with self.assertRaisesRegex(ProviderError, "Do not send funds"):
            self._create_with(body)

    def test_html_instead_of_json_is_a_network_error_not_a_crash(self):
        with self.assertRaises(ProviderNetworkError):
            self._create_with(b"<html>Just a moment...</html>", status=200)

    def test_redirect_is_never_treated_as_an_answer(self):
        with self.assertRaisesRegex(ProviderError, "redirect"):
            self._create_with(b'{"id": "x", "payinAddress": "y"}', status=302)

    def test_provider_error_text_is_bounded(self):
        body = b'{"message": "' + b"x" * 5000 + b'"}'
        with self.assertRaises(ProviderError) as ctx:
            self._create_with(body, status=400)
        self.assertLess(len(str(ctx.exception)), 400)


class CreateTimeoutKeepsItsType(_Isolated):
    """A timeout while opening an order must reach the GUI as a
    ProviderNetworkError: that type is what makes the deposit window warn
    that the order may already exist at the provider."""

    def test_each_order_creating_provider_reports_a_network_error(self):
        rate = _response(200, b'{"trade_id": "r1", "provider": "x", "fixed": false}')
        for provider in (ChangeNow(api_key="test"),
                         FixedFloat(api_key="test", api_secret="test"),
                         SideShift(secret="test", affiliate_id="test"),
                         StealthEX(api_key="test"),
                         Trocador(api_key="test")):
            with self.subTest(provider=provider.name):
                post = mock.Mock(side_effect=requests.exceptions.ReadTimeout("slow"))
                provider.session.post = post
                provider.session.get = mock.Mock(return_value=rate)
                with self.assertRaises(ProviderNetworkError):
                    provider.create_swap("BTC", "ETH", Decimal("0.01"),
                                         "0x" + "a" * 40, ADDR)
                self.assertEqual(post.call_count, 1)


class DestinationEchoIsChecked(unittest.TestCase):
    def _swap(self, echo):
        return base.Swap(provider="P", order_id="o1", from_coin="BTC", to_coin="ETH",
                         deposit_address=ADDR, deposit_memo=None,
                         send_amount=Decimal("0.01"), deposit_min=None,
                         deposit_max=None, estimated_receive=None,
                         settle_address="0x" + "a" * 40, expires_at=None,
                         settle_address_confirmed_by_provider=echo)

    def test_non_string_echo_is_a_mismatch_not_an_exception(self):
        import preflight
        for echo in ({"address": "0x" + "a" * 40}, ["0x" + "a" * 40], 12345, True):
            with self.subTest(echo=echo):
                status, message = preflight.verify_provider_confirmed_destination(
                    "P", self._swap(echo), "0x" + "a" * 40)
                self.assertEqual(status, "mismatch")
                self.assertIn("Do NOT send funds", message)

    def test_string_echo_rules_are_unchanged(self):
        import preflight
        dest = "0x" + "a" * 40
        cases = ((dest.upper().replace("0X", "0x"), "ok"), (None, "unverified"),
                 ("0x" + "b" * 40, "mismatch"))
        for echo, expected in cases:
            with self.subTest(echo=echo):
                status, _ = preflight.verify_provider_confirmed_destination(
                    "P", self._swap(echo), dest)
                self.assertEqual(status, expected)


class RedirectsAreDisabled(_Isolated):
    def test_requests_are_sent_with_allow_redirects_false(self):
        provider = ChangeNow(api_key="test")
        post = mock.Mock(return_value=_response(200, b"{}"))
        provider.session.post = post
        provider._post("https://api.changenow.io/v2/exchange", json={})
        self.assertIs(post.call_args.kwargs["allow_redirects"], False)


if __name__ == "__main__":
    unittest.main()
