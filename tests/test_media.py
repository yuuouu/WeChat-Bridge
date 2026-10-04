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
            headers = {}

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size=64 * 1024):
                yield self.content

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

    def test_download_and_decrypt_media_rejects_stream_over_limit(self):
        key = b"0123456789abcdef"
        encrypted = media.encrypt_aes_ecb(b"x" * 64, key)

        class _FakeResp:
            headers = {}

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size=64 * 1024):
                yield encrypted[:32]
                yield encrypted[32:]

        with patch("media.requests.get", return_value=_FakeResp()):
            path = media.download_and_decrypt_media(
                encrypted_query_param="abc",
                aes_key_b64=base64.b64encode(key.hex().encode("ascii")).decode("ascii"),
                msg_id="msg-file",
                media_type="file",
                max_bytes=32,
            )

        self.assertIsNone(path)
        self.assertEqual(list(Path(self.tempdir.name).iterdir()), [])

    def test_encrypt_aes_ecb_file_matches_bytes_encryption(self):
        key = b"0123456789abcdef"
        src = Path(self.tempdir.name) / "plain.bin"
        dst = Path(self.tempdir.name) / "encrypted.bin"
        plaintext = b"hello-wechat-bridge" * 100
        src.write_bytes(plaintext)

        written = media.encrypt_aes_ecb_file(str(src), str(dst), key, chunk_size=17)

        self.assertEqual(written, media.encrypted_size_for_plain_size(len(plaintext)))
        self.assertEqual(dst.read_bytes(), media.encrypt_aes_ecb(plaintext, key))

    def test_inspect_media_file_returns_md5_and_sizes(self):
        src = Path(self.tempdir.name) / "plain.bin"
        src.write_bytes(b"abc" * 500)

        result = media.inspect_media_file(str(src), chunk_size=64)

        self.assertEqual(result["rawsize"], 1500)
        self.assertEqual(result["first1024"], (b"abc" * 500)[:1024])
        self.assertEqual(result["encrypted_size"], media.encrypted_size_for_plain_size(1500))

    def test_detect_media_format_recognizes_silk_v3(self):
        silk_bytes = b"\x02#!SILK_V3.\x00" + b"\x00" * 64

        self.assertTrue(media.is_silk(silk_bytes))
        self.assertEqual(media._detect_media_format(silk_bytes, media_type="voice"), "silk")

    def test_cleanup_expired_media_files_deletes_matching_prefix_only(self):
        old_video = Path(self.tempdir.name) / "out_video_old.mp4"
        fresh_video = Path(self.tempdir.name) / "out_video_fresh.mp4"
        old_image = Path(self.tempdir.name) / "out_img_old.jpg"
        old_video.write_bytes(b"old")
        fresh_video.write_bytes(b"fresh")
        old_image.write_bytes(b"image")
        now_ts = 1710000000
        old_ts = now_ts - 8 * 24 * 3600
        for path, ts in ((old_video, old_ts), (fresh_video, now_ts), (old_image, old_ts)):
            path.touch()
            import os

            os.utime(path, (ts, ts))

        result = media.cleanup_expired_media_files(self.tempdir.name, retention_hours=168, now_ts=now_ts)

        self.assertEqual(result["deleted"], 1)
        self.assertFalse(old_video.exists())
        self.assertTrue(fresh_video.exists())
        self.assertTrue(old_image.exists())


if __name__ == "__main__":
    unittest.main()
