"""ilink 模块单元测试。

覆盖：bot_id 提取、token 持久化、headers 构造、get_updates 解析、
token 过期清除、send_text ret=-2 异常、logged_in 属性。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

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
        client.get_updates_buf = "buf-cursor-123"
        client._save_token()

        client2 = ilink.ILinkClient()
        self.assertEqual(client2.bot_token, "test-token@im.bot:hash123")
        self.assertEqual(client2.base_url, "https://custom.api.com")
        self.assertEqual(client2.bot_id, "test-token")
        self.assertEqual(client2.get_updates_buf, "buf-cursor-123")

    def test_clear_token_removes_file_and_state(self):
        client = ilink.ILinkClient()
        client.bot_token = "some-token"
        client.bot_id = "some"
        client._save_token()
        self.assertTrue(os.path.exists(ilink.TOKEN_FILE))

        client.clear_token()
        self.assertIsNone(client.bot_token)
        self.assertIsNone(client.bot_id)
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
    """_headers 应构造正确的请求头。"""

    def test_headers_without_token(self):
        h = ilink._headers()
        self.assertEqual(h["Content-Type"], "application/json")
        self.assertEqual(h["AuthorizationType"], "ilink_bot_token")
        self.assertIn("X-WECHAT-UIN", h)
        self.assertNotIn("Authorization", h)

    def test_headers_with_token(self):
        h = ilink._headers("my-token")
        self.assertEqual(h["Authorization"], "Bearer my-token")


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
        self.assertEqual(payload["msg"]["item_list"][0]["text_item"]["text"], "hello world")

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


if __name__ == "__main__":
    unittest.main()
