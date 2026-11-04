import base64
import json
import unittest

import secretbox


TEST_PARAMS = {"n": 1 << 10, "r": 8, "p": 1, "length": 32}


def _encrypted(plaintext="secret", password="correct-password"):
    salt = secretbox.new_salt()
    key = secretbox.derive_key(password, salt, TEST_PARAMS)
    envelope = secretbox.encrypt(plaintext, key, salt, TEST_PARAMS)
    return envelope, key


class SecretBoxTests(unittest.TestCase):
    def test_encrypt_unlock_round_trip(self):
        envelope, _ = _encrypted('{"provider_key":"abc123"}')

        plaintext, key, salt, params = secretbox.unlock(
            envelope, "correct-password"
        )

        self.assertEqual(plaintext, '{"provider_key":"abc123"}')
        self.assertEqual(len(key), 32)
        self.assertEqual(len(salt), 16)
        self.assertEqual(params, TEST_PARAMS)

    def test_wrong_password_is_reported_as_bad_password(self):
        envelope, _ = _encrypted()

        with self.assertRaises(secretbox.BadPassword):
            secretbox.unlock(envelope, "wrong-password")

    def test_parse_header_rejects_invalid_base64_salt(self):
        envelope = json.dumps(
            {
                "swapdesk_encrypted": 1,
                "kdf": "scrypt",
                "kdf_params": TEST_PARAMS,
                "salt": "%%%not-base64%%%",
            }
        )

        with self.assertRaises(secretbox.MalformedEnvelope):
            secretbox.parse_header(envelope)

    def test_parse_header_rejects_wrong_salt_length(self):
        envelope = json.dumps(
            {
                "swapdesk_encrypted": 1,
                "kdf": "scrypt",
                "kdf_params": TEST_PARAMS,
                "salt": base64.b64encode(b"short").decode("ascii"),
            }
        )

        with self.assertRaisesRegex(secretbox.MalformedEnvelope, "salt is"):
            secretbox.parse_header(envelope)

    def test_parse_header_rejects_excessive_scrypt_cost_before_derivation(self):
        envelope = json.dumps(
            {
                "swapdesk_encrypted": 1,
                "kdf": "scrypt",
                "kdf_params": {**TEST_PARAMS, "n": 1 << 23},
                "salt": base64.b64encode(b"0" * 16).decode("ascii"),
            }
        )

        with self.assertRaisesRegex(secretbox.MalformedEnvelope, "scrypt n="):
            secretbox.parse_header(envelope)

    def test_decrypt_rejects_invalid_nonce_length(self):
        envelope, key = _encrypted()
        obj = json.loads(envelope)
        obj["nonce"] = base64.b64encode(b"too-short").decode("ascii")

        with self.assertRaisesRegex(secretbox.MalformedEnvelope, "nonce is"):
            secretbox.decrypt(json.dumps(obj), key)

    def test_decrypt_rejects_ciphertext_shorter_than_gcm_tag(self):
        envelope, key = _encrypted()
        obj = json.loads(envelope)
        obj["ciphertext"] = base64.b64encode(b"short").decode("ascii")

        with self.assertRaisesRegex(
            secretbox.MalformedEnvelope, "ciphertext is too short"
        ):
            secretbox.decrypt(json.dumps(obj), key)

    def test_authenticated_header_tampering_fails_decryption(self):
        envelope, key = _encrypted()
        obj = json.loads(envelope)
        obj["kdf_params"]["n"] = 1 << 11

        with self.assertRaises(secretbox.BadPassword):
            secretbox.decrypt(json.dumps(obj), key)


if __name__ == "__main__":
    unittest.main()
