"""ilink 模块单元测试。

覆盖：bot_id 提取、token 持久化、headers 构造、get_updates 解析、
token 过期清除、send_text ret=-2 异常、logged_in 属性。
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.crypto_stub import install_crypto_stub

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

install_crypto_stub()
import ilink


class ExtractBotIdTests(unittest.TestCase):
    """_extract_bot_id 从 bot_token 中提取稳定标识。"""

    def test_extracts_prefix_before_at(self):
        self.assertEqual(ilink.ILinkClient._extract_bot_id("abc123@im.bot:somehash"), "abc123")

    def test_falls_back_to_first_12_chars(self):
        self.assertEqual(ilink.ILinkClient._extract_bot_id("abcdefghijklmnop"), "abcdefghijkl")

    def test_short_token_returns_as_is(self):
        self.assertEqual(ilink.ILinkClient._extract_bot_id("short"), "short")

    def test_none_returns_none(self):
        self.assertIsNone(ilink.ILinkClient._extract_bot_id(None))

    def test_empty_returns_none(self):
        self.assertIsNone(ilink.ILinkClient._extract_bot_id(""))


class TokenPersistenceTests(unittest.TestCase):
    """Token 保存和恢复。"""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self._orig_token_file = ilink.TOKEN_FILE
        ilink.TOKEN_FILE = str(Path(self.tempdir.name) / "token.json")

    def tearDown(self):
        ilink.TOKEN_FILE = self._orig_token_file
        self.tempdir.cleanup()

    def test_save_and_load_token_roundtrip(self):
        client = ilink.ILinkClient()
        client.bot_token = "test-token@im.bot:hash123"
        client.base_url = "https://custom.api.com"
        client.bot_id = "test-token"
        client.user_id = "user-123"
        client.get_updates_buf = "buf-cursor-123"
        client._save_token()

        client2 = ilink.ILinkClient()
        self.assertEqual(client2.bot_token, "test-token@im.bot:hash123")
        self.assertEqual(client2.base_url, "https://custom.api.com")
        self.assertEqual(client2.bot_id, "test-token")
        self.assertEqual(client2.user_id, "user-123")
        self.assertEqual(client2.get_updates_buf, "buf-cursor-123")
        self.assertGreater(client2.get_token_mtime(), 0)

    def test_load_token_without_user_id_keeps_backward_compatibility(self):
        Path(ilink.TOKEN_FILE).write_text(
            json.dumps(
                {
                    "bot_token": "legacy-token@im.bot:hash123",
                    "base_url": "https://legacy.api.com",
                    "bot_id": "legacy-token",
                    "get_updates_buf": "legacy-cursor",
                }
            )
        )

        client = ilink.ILinkClient()
        self.assertEqual(client.bot_token, "legacy-token@im.bot:hash123")
        self.assertEqual(client.user_id, None)

    def test_clear_token_removes_file_and_state(self):
        client = ilink.ILinkClient()
        client.bot_token = "some-token"
        client.bot_id = "some"
        client.user_id = "user-123"
        client._save_token()
        self.assertTrue(os.path.exists(ilink.TOKEN_FILE))

        client.clear_token()
        self.assertIsNone(client.bot_token)
        self.assertIsNone(client.bot_id)
        self.assertIsNone(client.user_id)
        self.assertEqual(client.get_updates_buf, "")
        self.assertFalse(os.path.exists(ilink.TOKEN_FILE))


class LoggedInPropertyTests(unittest.TestCase):
    """logged_in 属性应反映 bot_token 是否存在。"""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self._orig_token_file = ilink.TOKEN_FILE
        ilink.TOKEN_FILE = str(Path(self.tempdir.name) / "token.json")

    def tearDown(self):
        ilink.TOKEN_FILE = self._orig_token_file
        self.tempdir.cleanup()

    def test_not_logged_in_by_default(self):
        client = ilink.ILinkClient()
        self.assertFalse(client.logged_in)

    def test_logged_in_with_token(self):
        client = ilink.ILinkClient()
        client.bot_token = "some-token"
        self.assertTrue(client.logged_in)


class HeadersTests(unittest.TestCase):
    """GET/POST headers 与 base_info 应对齐 openclaw-weixin@2.1.7。"""

    def test_get_headers_are_qr_only(self):
        h = ilink._get_headers()
        self.assertEqual(
            h,
            {
                "iLink-App-Id": "bot",
                "iLink-App-ClientVersion": "131335",
            },
        )
        self.assertNotIn("Content-Type", h)
        self.assertNotIn("AuthorizationType", h)
        self.assertNotIn("X-WECHAT-UIN", h)
        self.assertNotIn("Authorization", h)

    def test_json_headers_without_token(self):
        h = ilink._json_headers()
        self.assertEqual(h["Content-Type"], "application/json")
        self.assertEqual(h["AuthorizationType"], "ilink_bot_token")
        self.assertEqual(h["iLink-App-Id"], "bot")
        self.assertEqual(h["iLink-App-ClientVersion"], "131335")
        self.assertIn("X-WECHAT-UIN", h)
        self.assertNotIn("Authorization", h)

    def test_json_headers_with_token(self):
        h = ilink._json_headers("my-token")
        self.assertEqual(h["Authorization"], "Bearer my-token")
        self.assertEqual(h["iLink-App-Id"], "bot")
        self.assertEqual(h["iLink-App-ClientVersion"], "131335")

    def test_headers_alias_remains_backward_compatible(self):
        self.assertIs(ilink._headers, ilink._json_headers)

    def test_base_info_uses_ilink_channel_version(self):
        self.assertEqual(ilink._base_info(), {"channel_version": "2.1.7"})


class QRLoginTests(unittest.TestCase):
    """QR 登录状态机。"""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self._orig_token_file = ilink.TOKEN_FILE
        ilink.TOKEN_FILE = str(Path(self.tempdir.name) / "token.json")

    def tearDown(self):
        ilink.TOKEN_FILE = self._orig_token_file
        self.tempdir.cleanup()

    def _mock_get_response(self, payload):
        mock_resp = MagicMock()
        mock_resp.json.return_value = payload
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    def test_get_qrcode_uses_fixed_base_url_and_get_headers(self):
        client = ilink.ILinkClient()
        client.base_url = "https://custom.example.com"
        mock_resp = self._mock_get_response({"qrcode": "qr-1"})

        with patch.object(client._session, "get", return_value=mock_resp) as mock_get:
            data = client.get_qrcode()

        self.assertEqual(data["qrcode"], "qr-1")
        self.assertEqual(mock_get.call_args.args[0], f"{ilink.FIXED_BASE_URL}/ilink/bot/get_bot_qrcode")
        self.assertEqual(mock_get.call_args.kwargs["params"], {"bot_type": "3"})
        self.assertEqual(mock_get.call_args.kwargs["headers"], ilink._get_headers())

    def test_scaned_but_redirect_only_updates_login_poll_base_url(self):
        client = ilink.ILinkClient()
        client.base_url = "https://message.example.com"
        mock_resp = self._mock_get_response({"status": "scaned_but_redirect", "redirect_host": "redirect.example.com"})

        with patch.object(client._session, "get", return_value=mock_resp) as mock_get:
            data = client.poll_qrcode_status("qr-redirect")

        self.assertEqual(data["status"], "scaned_but_redirect")
        self.assertEqual(mock_get.call_args.args[0], f"{ilink.FIXED_BASE_URL}/ilink/bot/get_qrcode_status")
        self.assertEqual(client._login_poll_base_url, "https://redirect.example.com")
        self.assertEqual(client.base_url, "https://message.example.com")
        self.assertIsNone(client.bot_token)
        self.assertFalse(Path(ilink.TOKEN_FILE).exists())

    def test_expired_resets_login_poll_base_url(self):
        client = ilink.ILinkClient()
        client._login_poll_base_url = "https://redirect.example.com"
        mock_resp = self._mock_get_response({"status": "expired"})

        with patch.object(client._session, "get", return_value=mock_resp):
            data = client.poll_qrcode_status("qr-expired")

        self.assertEqual(data["status"], "expired")
        self.assertEqual(client._login_poll_base_url, ilink.FIXED_BASE_URL)

    def test_confirmed_without_ilink_bot_id_raises(self):
        client = ilink.ILinkClient()
        mock_resp = self._mock_get_response({"status": "confirmed", "bot_token": "token@im.bot:hash"})

        with patch.object(client._session, "get", return_value=mock_resp):
            with self.assertRaises(RuntimeError) as ctx:
                client.poll_qrcode_status("qr-confirmed")

        self.assertIn("ilink_bot_id", str(ctx.exception))
        self.assertIsNone(client.bot_token)
        self.assertFalse(Path(ilink.TOKEN_FILE).exists())

    def test_confirmed_saves_token_and_user_id(self):
        client = ilink.ILinkClient()
        client._login_poll_base_url = "https://redirect.example.com"
        mock_resp = self._mock_get_response(
            {
                "status": "confirmed",
                "bot_token": "token@im.bot:hash",
                "baseurl": "https://message.example.com",
                "ilink_bot_id": "bot-123",
                "ilink_user_id": "user-456",
            }
        )

        with patch.object(client._session, "get", return_value=mock_resp):
            data = client.poll_qrcode_status("qr-confirmed")

        self.assertEqual(data["status"], "confirmed")
        self.assertEqual(client.bot_token, "token@im.bot:hash")
        self.assertEqual(client.base_url, "https://message.example.com")
        self.assertEqual(client.bot_id, "bot-123")
        self.assertEqual(client.user_id, "user-456")
        self.assertEqual(client._login_poll_base_url, ilink.FIXED_BASE_URL)

        saved = json.loads(Path(ilink.TOKEN_FILE).read_text())
        self.assertEqual(saved["bot_token"], "token@im.bot:hash")
        self.assertEqual(saved["base_url"], "https://message.example.com")
        self.assertEqual(saved["bot_id"], "bot-123")
        self.assertEqual(saved["user_id"], "user-456")

    def test_confirmed_without_baseurl_uses_default_base_url(self):
        client = ilink.ILinkClient()
        mock_resp = self._mock_get_response(
            {
                "status": "confirmed",
                "bot_token": "token@im.bot:hash",
                "ilink_bot_id": "bot-123",
            }
        )

        with patch.object(client._session, "get", return_value=mock_resp):
            client.poll_qrcode_status("qr-confirmed")

        self.assertEqual(client.base_url, ilink.BASE_URL)


class GetUpdatesTests(unittest.TestCase):
    """get_updates 消息解析、游标更新和异常处理。"""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self._orig_token_file = ilink.TOKEN_FILE
        ilink.TOKEN_FILE = str(Path(self.tempdir.name) / "token.json")

    def tearDown(self):
        ilink.TOKEN_FILE = self._orig_token_file
        self.tempdir.cleanup()

    def test_raises_runtime_error_when_not_logged_in(self):
        client = ilink.ILinkClient()
        with self.assertRaises(RuntimeError):
            client.get_updates()

    def test_parses_messages_and_updates_cursor(self):
        client = ilink.ILinkClient()
        client.bot_token = "test-token"

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "ret": 0,
            "errcode": 0,
            "get_updates_buf": "new-cursor-456",
            "msgs": [
                {"msg_id": "1", "message_type": 1},
                {"msg_id": "2", "message_type": 1},
            ],
        }
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "post", return_value=mock_resp):
            msgs = client.get_updates(timeout=5)

        self.assertEqual(len(msgs), 2)
        self.assertEqual(client.get_updates_buf, "new-cursor-456")

    def test_get_updates_posts_base_info_and_protocol_headers(self):
        client = ilink.ILinkClient()
        client.bot_token = "test-token"
        client.get_updates_buf = "cursor-123"

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ret": 0, "errcode": 0, "msgs": []}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "post", return_value=mock_resp) as mock_post:
            client.get_updates(timeout=5)

        self.assertEqual(mock_post.call_args.args[0], f"{ilink.BASE_URL}/ilink/bot/getupdates")
        headers = mock_post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer test-token")
        self.assertEqual(headers["iLink-App-Id"], "bot")
        self.assertEqual(headers["iLink-App-ClientVersion"], "131335")
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["get_updates_buf"], "cursor-123")
        self.assertEqual(payload["base_info"], {"channel_version": "2.1.7"})

    def test_returns_empty_on_timeout(self):
        import requests

        client = ilink.ILinkClient()
        client.bot_token = "test-token"

        with patch.object(client._session, "post", side_effect=requests.exceptions.Timeout("timeout")):
            msgs = client.get_updates(timeout=1)

        self.assertEqual(msgs, [])

    def test_clears_token_on_auth_error(self):
        client = ilink.ILinkClient()
        client.bot_token = "test-token"

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ret": 401, "errcode": 0}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "post", return_value=mock_resp):
            msgs = client.get_updates(timeout=1)

        self.assertEqual(msgs, [])
        self.assertIsNone(client.bot_token)

    def test_returns_empty_on_nonzero_ret_without_auth_error(self):
        client = ilink.ILinkClient()
        client.bot_token = "test-token"

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ret": -100, "errcode": 0}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "post", return_value=mock_resp):
            msgs = client.get_updates(timeout=1)

        self.assertEqual(msgs, [])
        # token 不应被清除（非 auth 错误）
        self.assertIsNotNone(client.bot_token)


class SendTextTests(unittest.TestCase):
    """send_text 的正常和异常路径。"""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self._orig_token_file = ilink.TOKEN_FILE
        ilink.TOKEN_FILE = str(Path(self.tempdir.name) / "token.json")

    def tearDown(self):
        ilink.TOKEN_FILE = self._orig_token_file
        self.tempdir.cleanup()

    def test_raises_when_not_logged_in(self):
        client = ilink.ILinkClient()
        with self.assertRaises(RuntimeError):
            client.send_text("user@im.wechat", "hello")

    def test_send_text_success(self):
        client = ilink.ILinkClient()
        client.bot_token = "test-token"

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ret": 0, "errcode": 0}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "post", return_value=mock_resp) as mock_post:
            result = client.send_text("user@im.wechat", "hello world")

        self.assertEqual(result["ret"], 0)
        # 验证 payload 结构
        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        self.assertEqual(payload["msg"]["to_user_id"], "user@im.wechat")
        self.assertEqual(payload["msg"]["item_list"][0]["type"], ilink.MESSAGE_ITEM_TYPE_TEXT)
        self.assertEqual(payload["msg"]["item_list"][0]["text_item"]["text"], "hello world")
        self.assertEqual(payload["base_info"], {"channel_version": "2.1.7"})
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers")
        self.assertEqual(headers["Authorization"], "Bearer test-token")
        self.assertEqual(headers["iLink-App-Id"], "bot")
        self.assertEqual(headers["iLink-App-ClientVersion"], "131335")

    def test_send_text_ret_minus_two_raises_window_error(self):
        client = ilink.ILinkClient()
        client.bot_token = "test-token"

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ret": -2, "errcode": 0}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "post", return_value=mock_resp):
            with self.assertRaises(RuntimeError) as ctx:
                client.send_text("user@im.wechat", "hello")
            self.assertIn("ret=-2", str(ctx.exception))


class GetBotIdTests(unittest.TestCase):
    """get_bot_id 优先返回缓存值，否则从 token 提取。"""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self._orig_token_file = ilink.TOKEN_FILE
        ilink.TOKEN_FILE = str(Path(self.tempdir.name) / "token.json")

    def tearDown(self):
        ilink.TOKEN_FILE = self._orig_token_file
        self.tempdir.cleanup()

    def test_returns_cached_bot_id(self):
        client = ilink.ILinkClient()
        client.bot_id = "cached-id"
        client.bot_token = "abc@im.bot:hash"
        self.assertEqual(client.get_bot_id(), "cached-id")

    def test_extracts_from_token_when_no_cache(self):
        client = ilink.ILinkClient()
        client.bot_id = None
        client.bot_token = "abc@im.bot:hash"
        self.assertEqual(client.get_bot_id(), "abc")


class SendTypingTests(unittest.TestCase):
    """send_typing 应通过 _post_json 注入统一 base_info。"""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self._orig_token_file = ilink.TOKEN_FILE
        ilink.TOKEN_FILE = str(Path(self.tempdir.name) / "token.json")

    def tearDown(self):
        ilink.TOKEN_FILE = self._orig_token_file
        self.tempdir.cleanup()

    def test_send_typing_posts_base_info_to_getconfig_and_sendtyping(self):
        client = ilink.ILinkClient()
        client.bot_token = "test-token"

        config_resp = MagicMock()
        config_resp.json.return_value = {"typing_ticket": "ticket-123"}
        config_resp.raise_for_status = MagicMock()
        typing_resp = MagicMock()
        typing_resp.json.return_value = {"ret": 0, "errcode": 0}
        typing_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "post", side_effect=[config_resp, typing_resp]) as mock_post:
            result = client.send_typing("user@im.wechat", "ctx-token")

        self.assertEqual(result["ret"], 0)
        self.assertEqual(mock_post.call_count, 2)
        getconfig_call, sendtyping_call = mock_post.call_args_list
        self.assertEqual(getconfig_call.args[0], f"{ilink.BASE_URL}/ilink/bot/getconfig")
        self.assertEqual(sendtyping_call.args[0], f"{ilink.BASE_URL}/ilink/bot/sendtyping")
        self.assertEqual(getconfig_call.kwargs["json"]["base_info"], {"channel_version": "2.1.7"})
        self.assertEqual(sendtyping_call.kwargs["json"]["base_info"], {"channel_version": "2.1.7"})
        self.assertEqual(getconfig_call.kwargs["json"]["context_token"], "ctx-token")
        self.assertEqual(sendtyping_call.kwargs["json"]["typing_ticket"], "ticket-123")


class MediaProtocolTests(unittest.TestCase):
    """上传 media_type 和消息 item type 使用不同常量。"""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self._orig_token_file = ilink.TOKEN_FILE
        ilink.TOKEN_FILE = str(Path(self.tempdir.name) / "token.json")

    def tearDown(self):
        ilink.TOKEN_FILE = self._orig_token_file
        self.tempdir.cleanup()

    def test_upload_media_uses_image_upload_media_type_and_base_info(self):
        client = ilink.ILinkClient()
        client.bot_token = "test-token"

        upload_url_resp = MagicMock()
        upload_url_resp.json.return_value = {
            "ret": 0,
            "upload_param": "upload-param",
            "upload_full_url": "https://cdn.example.com/upload",
        }
        upload_url_resp.raise_for_status = MagicMock()
        cdn_resp = MagicMock()
        cdn_resp.headers = {"X-Encrypted-Param": "download-ref"}
        cdn_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "post", side_effect=[upload_url_resp, cdn_resp]) as mock_post:
            result = client.upload_media(b"image-bytes", to_user_id="user@im.wechat")

        self.assertEqual(result["encrypt_query_param"], "download-ref")
        getupload_call, cdn_call = mock_post.call_args_list
        self.assertEqual(getupload_call.args[0], f"{ilink.BASE_URL}/ilink/bot/getuploadurl")
        self.assertEqual(getupload_call.kwargs["json"]["media_type"], ilink.UPLOAD_MEDIA_TYPE_IMAGE)
        self.assertEqual(getupload_call.kwargs["json"]["base_info"], {"channel_version": "2.1.7"})
        self.assertEqual(getupload_call.kwargs["headers"]["Content-Type"], "application/json")
        self.assertEqual(cdn_call.args[0], "https://cdn.example.com/upload")
        self.assertEqual(cdn_call.kwargs["headers"]["Content-Type"], "application/octet-stream")
        self.assertNotIn("json", cdn_call.kwargs)

    def test_send_image_uses_upload_image_type_and_message_image_item_type(self):
        client = ilink.ILinkClient()
        client.bot_token = "test-token"

        send_resp = MagicMock()
        send_resp.json.return_value = {"ret": 0, "errcode": 0}
        send_resp.raise_for_status = MagicMock()
        upload_result = {
            "encrypt_query_param": "download-ref",
            "aes_key_b64": "aes-b64",
            "aes_key_hex": "aes-hex",
            "encrypted_size": 123,
        }

        with (
            patch.object(client, "upload_media", return_value=upload_result) as mock_upload,
            patch.object(client._session, "post", return_value=send_resp) as mock_post,
        ):
            result = client.send_image("user@im.wechat", b"image-bytes", "ctx-token")

        self.assertEqual(result["ret"], 0)
        mock_upload.assert_called_once_with(
            b"image-bytes", media_type=ilink.UPLOAD_MEDIA_TYPE_IMAGE, to_user_id="user@im.wechat"
        )
        payload = mock_post.call_args.kwargs["json"]
        image_item = payload["msg"]["item_list"][0]
        self.assertEqual(image_item["type"], ilink.MESSAGE_ITEM_TYPE_IMAGE)
        self.assertEqual(payload["base_info"], {"channel_version": "2.1.7"})


if __name__ == "__main__":
    unittest.main()
