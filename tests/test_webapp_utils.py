import sys
import unittest
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1] / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from webapp.auth import check_web_session, make_session_cookie
from webapp.markdown_utils import markdown_to_plain, should_plainify_markdown
from webapp.request_utils import parse_multipart
from webapp.webhook_parser import parse_webhook_payload


class _FakeHandler:
    def __init__(self, cookie=""):
        self.headers = {"Cookie": cookie}


class WebAppUtilsTests(unittest.TestCase):
    def test_should_plainify_markdown_requires_explicit_plain_flag(self):
        self.assertTrue(should_plainify_markdown("plain"))
        self.assertTrue(should_plainify_markdown("degrade"))
        self.assertFalse(should_plainify_markdown("true"))
        self.assertFalse(should_plainify_markdown(True))

    def test_markdown_to_plain_converts_common_markdown(self):
        text = "# 标题\n**加粗** [链接](https://example.com)\n- 列表项"
        self.assertEqual(markdown_to_plain(text), "【标题】\n加粗 链接 (https://example.com)\n• 列表项")

    def test_make_session_cookie_and_check_web_session(self):
        token = "secret"
        session_secret = "session-key"
        cookie = make_session_cookie(token, session_secret)
        handler = _FakeHandler(f"wb_session={cookie}")
        self.assertTrue(check_web_session(handler, token, session_secret))
        self.assertFalse(check_web_session(handler, token, "other-session"))

    def test_parse_multipart_extracts_to_and_image_data(self):
        boundary = "----boundary"
        body = (
            f"--{boundary}\r\n"
            "Content-Disposition: form-data; name=\"to\"\r\n\r\n"
            "Alice\r\n"
            f"--{boundary}\r\n"
            "Content-Disposition: form-data; name=\"image\"; filename=\"a.png\"\r\n"
            "Content-Type: image/png\r\n\r\n"
            "PNGDATA123\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")
        to, image_data = parse_multipart(body, f"multipart/form-data; boundary={boundary}")
        self.assertEqual(to, "Alice")
        self.assertEqual(image_data, b"PNGDATA123")

    def test_parse_webhook_payload_handles_github_push(self):
        payload = {
            "repository": {"full_name": "demo/repo"},
            "sender": {"login": "yuu"},
            "ref": "refs/heads/main",
            "commits": [{"id": "abcdef123456", "message": "fix: hello"}],
        }
        text = parse_webhook_payload(payload)
        self.assertIn("demo/repo 推送到 main", text)
        self.assertIn("abcdef1 fix: hello", text)


if __name__ == "__main__":
    unittest.main()
