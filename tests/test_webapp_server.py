import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1] / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from webapp.context import WebAppContext
from webapp.server import BridgeHandler, ThreadingHTTPServer


class _FakeClient:
    def __init__(self, logged_in=True):
        self.logged_in = logged_in
        self.bot_id = "bot-test"
        self.cleared = False

    def clear_token(self):
        self.cleared = True


class _FakeBridge:
    def __init__(self):
        self.contacts = {"uid-1": "Alice"}
        self.context_tokens = {"uid-1": "ctx-token"}
        self._running = True
        self.ag_inbox = []
        self.sent = []

    def send(self, to, text, source="api", title=""):
        self.sent.append((to, text, source, title))
        return {"ok": True, "result": {"to": to, "text": text}}

    def get_runtime_status(self):
        return {
            "logged_in": True,
            "bot_id": "bot-test",
            "contacts_count": 1,
            "poll_running": True,
            "pending_total": 0,
            "active_sessions": 0,
            "buffering_users": 0,
        }

    def get_contact_delivery_summaries(self):
        return {
            "uid-1": {
                "user_id": "uid-1",
                "contact": "Alice",
                "status": "NORMAL",
                "blocked_reason_text": "无",
                "pending_count": 0,
                "active_overflow_session_id": None,
            }
        }


class WebAppServerTests(unittest.TestCase):
    def setUp(self):
        self.client = _FakeClient(logged_in=True)
        self.bridge = _FakeBridge()
        self.context = WebAppContext(client=self.client, bridge=self.bridge, api_token="secret-token")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), BridgeHandler)
        self.server.app_context = self.context  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def _request(self, path, method="GET", data=None, headers=None):
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers or {},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status, resp.headers, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read().decode("utf-8")

    def test_root_requires_web_auth_when_token_enabled(self):
        status, headers, body = self._request("/")
        self.assertEqual(status, 200)
        self.assertIn("请输入访问密码以解锁管理面板", body)

    def test_web_auth_sets_cookie_and_web_check_recognizes_it(self):
        payload = json.dumps({"token": "secret-token"}).encode("utf-8")
        status, headers, body = self._request(
            "/api/web_auth",
            method="POST",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        cookie = headers.get("Set-Cookie")
        self.assertIsNotNone(cookie)

        status, _, body = self._request("/api/web_check", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["authed"])
        self.assertTrue(data["need_auth"])

    def test_api_status_returns_service_state(self):
        status, _, body = self._request("/api/status")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["logged_in"])
        self.assertEqual(data["bot_id"], "bot-test")
        self.assertEqual(data["contacts_count"], 1)

    def test_api_send_requires_api_token(self):
        payload = json.dumps({"to": "Alice", "text": "hello"}).encode("utf-8")
        status, _, body = self._request(
            "/api/send",
            method="POST",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 401)
        self.assertIn("Unauthorized", body)

    def test_api_send_with_bearer_token_calls_bridge(self):
        payload = json.dumps({"to": "Alice", "text": "hello"}).encode("utf-8")
        status, _, body = self._request(
            "/api/send",
            method="POST",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer secret-token",
            },
        )
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertEqual(self.bridge.sent, [("Alice", "hello", "api", "")])


if __name__ == "__main__":
    unittest.main()
