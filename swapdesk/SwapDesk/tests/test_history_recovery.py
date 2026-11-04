"""swap-record (history.json) safety, plus guards for the audited dependency pins.

A swap record holds the order ID that lets a user find an order at the
provider after a failure, so a damaged history file must never be silently
replaced by an empty one.
"""

import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

import config

APP = Path(__file__).resolve().parent.parent


class _TempData(unittest.TestCase):
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

    def set_aside(self):
        return sorted(config.DATA_DIR.glob("history.corrupt-*.json"))


class CorruptHistoryIsPreserved(_TempData):
    DAMAGED = b'[{"order_id": "abc123", "provider": "FixedFloat", "token": "t"'  # truncated

    def test_unparseable_history_is_set_aside_not_overwritten(self):
        config.HISTORY_PATH.write_bytes(self.DAMAGED)
        with mock.patch.object(sys, "stderr", io.StringIO()):
            self.assertEqual(config.load_history(), [])
        saved = self.set_aside()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_bytes(), self.DAMAGED)
        self.assertFalse(config.HISTORY_PATH.exists())

    def test_next_swap_does_not_destroy_the_damaged_file(self):
        config.HISTORY_PATH.write_bytes(self.DAMAGED)
        with mock.patch.object(sys, "stderr", io.StringIO()):
            config.append_history({"order_id": "new1"})
        self.assertEqual(json.loads(config.HISTORY_PATH.read_text("utf-8")),
                         [{"order_id": "new1"}])
        saved = self.set_aside()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_bytes(), self.DAMAGED)

    def test_valid_json_of_the_wrong_shape_is_also_set_aside(self):
        config.HISTORY_PATH.write_text('{"not": "a list"}', encoding="utf-8")
        with mock.patch.object(sys, "stderr", io.StringIO()):
            self.assertEqual(config.load_history(), [])
        self.assertEqual(len(self.set_aside()), 1)

    def test_repeated_loads_do_not_pile_up_copies(self):
        config.HISTORY_PATH.write_bytes(self.DAMAGED)
        with mock.patch.object(sys, "stderr", io.StringIO()):
            for _ in range(5):
                config.load_history()
        self.assertEqual(len(self.set_aside()), 1)

    def test_a_second_corruption_does_not_overwrite_the_first_copy(self):
        with mock.patch.object(sys, "stderr", io.StringIO()):
            config.HISTORY_PATH.write_bytes(b"[1,")
            config.load_history()
            config.HISTORY_PATH.write_bytes(b"[2,")
            config.load_history()
        self.assertEqual(sorted(p.read_bytes() for p in self.set_aside()),
                         [b"[1,", b"[2,"])

    def test_healthy_history_is_left_alone(self):
        rows = [{"order_id": "a"}, {"order_id": "b"}]
        config.HISTORY_PATH.write_text(json.dumps(rows), encoding="utf-8")
        self.assertEqual(config.load_history(), rows)
        self.assertEqual(self.set_aside(), [])
        self.assertTrue(config.HISTORY_PATH.exists())

    def test_unreadable_file_is_not_moved(self):
        # An I/O error says nothing about the content, so nothing is set aside.
        config.HISTORY_PATH.write_text("[]", encoding="utf-8")
        with mock.patch.object(Path, "read_text", side_effect=OSError("device error")):
            self.assertEqual(config.load_history(), [])
        self.assertTrue(config.HISTORY_PATH.exists())
        self.assertEqual(self.set_aside(), [])

    @unittest.skipIf(os.name == "nt", "POSIX permission bits")
    def test_set_aside_copy_stays_owner_only(self):
        config.HISTORY_PATH.write_bytes(self.DAMAGED)
        config._secure_write(config.HISTORY_PATH, self.DAMAGED.decode())
        with mock.patch.object(sys, "stderr", io.StringIO()):
            config.load_history()
        mode = self.set_aside()[0].stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_notice_names_the_file_and_nothing_secret(self):
        config.HISTORY_PATH.write_bytes(self.DAMAGED)
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            config.load_history()
        text = err.getvalue()
        self.assertIn("history.corrupt-", text)
        self.assertNotIn("abc123", text)

    def test_no_notice_crash_when_stderr_is_none(self):
        # PyInstaller --windowed builds have no stderr.
        config.HISTORY_PATH.write_bytes(self.DAMAGED)
        with mock.patch.object(sys, "stderr", None):
            self.assertEqual(config.load_history(), [])


class HistoryCapIsExplicit(_TempData):
    def test_history_keeps_the_newest_200_swap_records(self):
        for i in range(205):
            config.append_history({"order_id": f"o{i}"})
        rows = config.load_history()
        self.assertEqual(len(rows), 200)
        self.assertEqual(rows[0]["order_id"], "o204")
        self.assertEqual(rows[-1]["order_id"], "o5")


def _pins():
    text = (APP / "requirements.txt").read_text(encoding="utf-8")
    return {m.group(1).lower(): m.group(2)
            for m in re.finditer(r"^([A-Za-z0-9_.-]+)==([0-9][^\s;\\]*)", text, re.M)}


def _ver(s):
    return tuple(int(p) for p in re.findall(r"\d+", s))


class AuditedDependencyFloors(unittest.TestCase):
    """Versions below these carry published advisories (pip-audit, Nov 2026)."""

    FLOORS: ClassVar[dict[str, str]] = {
        "certifi": "2026.10.19",   # emSign roots with compromised intermediates
        "urllib3": "2.8.0",        # streaming DoS and proxy-TLS advisories
        "pillow": "13.0.0",        # decoder and parser advisories
    }

    def test_pins_are_at_or_above_the_fixed_versions(self):
        pins = _pins()
        for name, floor in self.FLOORS.items():
            with self.subTest(package=name):
                self.assertIn(name, pins)
                self.assertGreaterEqual(_ver(pins[name]), _ver(floor))

    def test_qr_image_path_works_with_the_pinned_pillow(self):
        # The same calls ui/deposit.py makes, minus the Tk wrapper.
        qrcode = __import__("qrcode")
        from PIL import Image
        buf = io.BytesIO()
        qrcode.make("bitcoin:bc1qexample?amount=0.001").save(buf, format="PNG")
        buf.seek(0)
        img = Image.open(buf)
        img.load()
        self.assertGreater(img.size[0], 20)


if __name__ == "__main__":
    unittest.main()
