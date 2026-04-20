import base64
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.crypto_stub import install_crypto_stub

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

install_crypto_stub()
import media


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        media.set_media_dir(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_encrypt_decrypt_roundtrip(self):
        key = b"0123456789abcdef"
        plaintext = b"hello-wechat-bridge"
        encrypted = media.encrypt_aes_ecb(plaintext, key)
        self.assertEqual(media.decrypt_aes_ecb(encrypted, key), plaintext)

    def test_decode_aes_key_supports_image_and_file_modes(self):
        raw_key = b"0123456789abcdef"
        img_key = base64.b64encode(raw_key).decode("ascii")
        file_key = base64.b64encode(raw_key.hex().encode("ascii")).decode("ascii")
        self.assertEqual(media._decode_aes_key(img_key, "image"), raw_key)
        self.assertEqual(media._decode_aes_key(file_key, "file"), raw_key)

    def test_download_and_decrypt_media_saves_file(self):
        key = b"0123456789abcdef"
        plaintext = b"\xff\xd8\xffwechat-image"
        encrypted = media.encrypt_aes_ecb(plaintext, key)

        class _FakeResp:
            content = encrypted

            def raise_for_status(self):
                return None

        with patch("media.requests.get", return_value=_FakeResp()), patch("media.time.time", return_value=1710000000):
            path = media.download_and_decrypt_media(
                encrypted_query_param="abc",
                aes_key_b64=base64.b64encode(key).decode("ascii"),
                msg_id="msg-1",
                media_type="image",
            )

        self.assertIsNotNone(path)
        saved = Path(path)
        self.assertTrue(saved.exists())
        self.assertEqual(saved.read_bytes(), plaintext)
        self.assertEqual(saved.suffix, ".jpg")


if __name__ == "__main__":
    unittest.main()
