"""Bundled keys and the SwapDesk API server are gone; their leftovers are inert.

Covers what can run without a display: the removed modules and build option
stay removed, user-supplied keys still load, plaintext and encrypted configs
still work, and a config carrying a legacy `swapdesk_api` section loads,
is not activated, and is not rewritten or dropped.
"""
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

import config
import preflight
import providers as prov
import secretbox

APP = Path(__file__).resolve().parents[1]

LEGACY = {"enabled": True, "base_url": "https://api.example.test", "api_key": "legacy-secret-key"}


def _source_files():
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP)
        if any(part in {".venv", "build", "dist", "__pycache__", "tests"} for part in rel.parts):
            continue
        yield path


class _TempConfig(unittest.TestCase):
    """Point config at a throwaway folder and restore it afterwards."""

    NAMES = ("APP_DIR", "DATA_DIR", "CONFIG_PATH", "CONFIG_ENC_PATH", "HISTORY_PATH",
             "COINS_PATH", "ADDRESS_BOOK_PATH", "ENCRYPTION_MARKER_PATH")

    def setUp(self):
        self._saved = {n: getattr(config, n) for n in self.NAMES}
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        data = tmp / "data"
        config.APP_DIR = tmp
        config.DATA_DIR = data
        config.CONFIG_PATH = data / "config.json"
        config.CONFIG_ENC_PATH = data / "config.enc"
        config.HISTORY_PATH = data / "history.json"
        config.COINS_PATH = data / "coins.json"
        config.ADDRESS_BOOK_PATH = data / "address_book.json"
        config.ENCRYPTION_MARKER_PATH = tmp / "encryption_enabled"
        config.lock()
        data.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        config.lock()
        for n, v in self._saved.items():
            setattr(config, n, v)
        self._tmp.cleanup()

    def write_plain(self, obj):
        config.CONFIG_PATH.write_text(json.dumps(obj, indent=2), encoding="utf-8")


class RemovedModulesStayRemoved(unittest.TestCase):
    def test_modules_and_files_are_gone(self):
        for name in ("bundled_keys", "remote", "keys_baked"):
            self.assertIsNone(importlib.util.find_spec(name), name)
            self.assertFalse((APP / f"{name}.py").exists(), name)

    def test_core_modules_import_without_them(self):
        self.assertTrue(callable(preflight.main))
        self.assertNotIn("bundled_keys", sys.modules)
        self.assertNotIn("remote", sys.modules)

    def test_no_source_file_references_removed_features(self):
        pattern = re.compile(
            r"bundled_keys|keys_baked|keys\.local|with[-_]keys|RemoteSwapDesk|"
            r"THIRD_PARTY_ROUTING|swapdesk_api|import remote\b|from remote\b")
        hits = []
        for path in _source_files():
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    hits.append(f"{path.relative_to(APP)}:{n}")
        self.assertEqual(hits, [])

    def test_removed_helpers_are_not_exported(self):
        for name in ("CONFIG_SECTIONS", "provider_name_for"):
            self.assertFalse(hasattr(prov, name), name)
        for name in ("bundled_providers_in_use", "refresh_bundled_state", "_apply_bundled",
                     "_strip_bundled", "_BUNDLED_IN_USE"):
            self.assertFalse(hasattr(config, name), name)
        self.assertFalse(hasattr(prov.SwapProvider, "disclosed_commission_bps"))


class BuildHasNoKeyBaking(unittest.TestCase):
    def test_with_keys_option_is_gone(self):
        out = subprocess.run([sys.executable, str(APP / "build.py"), "--help"],
                             capture_output=True, text=True, cwd=APP, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn("--with-keys", out.stdout)
        out = subprocess.run([sys.executable, str(APP / "build.py"), "--with-keys"],
                             capture_output=True, text=True, cwd=APP, timeout=60)
        self.assertEqual(out.returncode, 2)  # argparse: unrecognized argument

    def test_build_scripts_and_docs_do_not_mention_it(self):
        for path in (APP / "scripts").rglob("*"):
            if path.is_file():
                self.assertNotIn("--with-keys", path.read_text(encoding="utf-8", errors="ignore"),
                                 str(path))


class UserKeysAndConfig(_TempConfig):
    KEYS: ClassVar[dict] = {
        "trocador": {"api_key": "tr-key"},
        "changenow": {"api_key": "cn-key"},
        "dex": {"api_key": "dx-key"},
        "fixedfloat": {"api_key": "ff-key", "api_secret": "ff-secret"},
        "sideshift": {"secret": "ss-secret", "affiliate_id": "ss-aff"},
        "chainflip": {"api_key": "cf-key"},
        "stealthex": {"api_key": "sx-key"},
    }

    def test_default_config_has_no_removed_section(self):
        self.assertNotIn("swapdesk_api", config.DEFAULT_CONFIG)

    def test_plaintext_user_keys_load_and_reach_providers(self):
        self.write_plain({**self.KEYS, "auto_select_best": False})
        cfg = config.load_config()
        for section, fields in self.KEYS.items():
            for field, value in fields.items():
                self.assertEqual(cfg[section][field], value)
        built = {p.name: p for p in prov.build_providers(cfg)}
        self.assertEqual(built["Trocador"].api_key, "tr-key")
        self.assertEqual(built["ChangeNOW"].api_key, "cn-key")
        self.assertEqual(built["0x (DEX)"].api_key, "dx-key")
        self.assertEqual(built["Chainflip"].api_key, "cf-key")
        self.assertEqual(built["StealthEX"].api_key, "sx-key")
        self.assertEqual(built["FixedFloat"].api_secret, "ff-secret")
        self.assertEqual(built["SideShift"].secret, "ss-secret")

    def test_blank_fields_stay_blank(self):
        # Nothing fills a blank key from anywhere but the user's own config.
        cfg = config.load_config()
        for section in ("trocador", "changenow", "dex"):
            self.assertEqual(cfg[section]["api_key"], "")
        self.assertEqual(config.load_config(), cfg)

    @unittest.skipUnless(secretbox.available(), "cryptography not installed")
    def test_encrypted_config_round_trips(self):
        self.write_plain(self.KEYS)
        config.set_master_password("correct horse")
        self.assertTrue(config.CONFIG_ENC_PATH.exists())
        self.assertFalse(config.CONFIG_PATH.exists())
        config.lock()
        with self.assertRaises(config.ConfigLocked):
            config.load_config()
        self.assertFalse(config.unlock("wrong"))
        self.assertTrue(config.unlock("correct horse"))
        cfg = config.load_config()
        self.assertEqual(cfg["trocador"]["api_key"], "tr-key")
        self.assertEqual(cfg["fixedfloat"]["api_secret"], "ff-secret")


class LegacySwapdeskApiSection(_TempConfig):
    def _legacy_config(self):
        return {"trocador": {"api_key": "tr-key"},
                "privacy": {"proxy_mode": "off", "auto_clear_history_days": 7},
                "swapdesk_api": dict(LEGACY),
                "some_future_field": {"keep": "me"}}

    def test_loads_without_crashing_and_is_not_activated(self):
        legacy = self._legacy_config()
        legacy["enabled_providers"] = {"SwapDesk API": True, "Trocador": True}
        self.write_plain(legacy)
        cfg = config.load_config()
        built = prov.build_providers(cfg)
        names = [p.name for p in built]
        self.assertEqual(len(built), 9)
        self.assertNotIn("SwapDesk API", names)
        self.assertNotIn("SwapDesk API", {p.name for p in built if p.configured()})

    def test_loading_does_not_rewrite_the_file(self):
        self.write_plain(self._legacy_config())
        before = config.CONFIG_PATH.read_bytes()
        config.load_config()
        self.assertEqual(config.CONFIG_PATH.read_bytes(), before)

    def test_legacy_section_and_unrelated_fields_survive_a_save(self):
        self.write_plain(self._legacy_config())
        cfg = config.load_config()
        self.assertEqual(cfg["swapdesk_api"], LEGACY)
        self.assertEqual(cfg["privacy"]["proxy_mode"], "off")
        self.assertEqual(cfg["privacy"]["auto_clear_history_days"], 7)
        self.assertEqual(cfg["some_future_field"], {"keep": "me"})
        config.save_config(cfg)
        on_disk = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["swapdesk_api"], LEGACY)
        self.assertEqual(on_disk["trocador"]["api_key"], "tr-key")
        self.assertEqual(on_disk["some_future_field"], {"keep": "me"})

    @unittest.skipUnless(secretbox.available(), "cryptography not installed")
    def test_encrypted_config_with_legacy_section(self):
        self.write_plain(self._legacy_config())
        config.set_master_password("pw-for-test")
        config.lock()
        self.assertTrue(config.unlock("pw-for-test"))
        cfg = config.load_config()
        self.assertEqual(cfg["swapdesk_api"], LEGACY)
        self.assertEqual(cfg["trocador"]["api_key"], "tr-key")
        self.assertEqual(len(prov.build_providers(cfg)), 9)


class ProviderSurface(unittest.TestCase):
    def test_nine_providers_all_off_by_default(self):
        built = prov.build_providers(json.loads(json.dumps(config.DEFAULT_CONFIG)))
        self.assertEqual(len(built), 9)
        self.assertEqual(sorted(p.name for p in built), sorted([
            "Trocador", "SideShift", "FixedFloat", "0x (DEX)", "ChangeNOW",
            "THORChain", "Maya Protocol", "Chainflip", "StealthEX"]))
        for p in built:
            self.assertFalse(prov.provider_enabled({}, p.name))

    def test_preflight_pair_state_is_yes_or_no(self):
        built = {p.name: p for p in prov.build_providers(
            json.loads(json.dumps(config.DEFAULT_CONFIG)))}
        for p in built.values():
            self.assertIn(preflight._pair_support_state(p, "BTC", "XMR"), {"yes", "no"})


if __name__ == "__main__":
    unittest.main()
