import json
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tests.crypto_stub import install_crypto_stub

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

install_crypto_stub()
sys.modules.setdefault("qrcode", types.ModuleType("qrcode"))
import config as cfg
from webapp.api_handlers import handle_qr_status
from webapp.context import WebAppContext
from webapp.server import BridgeHandler, ThreadingHTTPServer


class _FakeClient:
    def __init__(self, logged_in=True):
        self.logged_in = logged_in
        self.bot_id = "bot-test"
        self.cleared = False
        self.qr_status_response = {"status": "wait"}

    def clear_token(self):
        self.cleared = True

    def poll_qrcode_status(self, qrcode):
        self.polled_qrcode = qrcode
        return self.qr_status_response

    def get_bot_id(self):
        return self.bot_id


class _FakeBridge:
    def __init__(self):
        self.contacts = {"uid-1": "Alice"}
        self.context_tokens = {"uid-1": "ctx-token"}
        self._running = True
        self.ag_inbox = []
        self.sent = []
        self.ai_manager = None
        self.recent_messages = ["stale-message"]
        self._consecutive_send_count = {"uid-1": {"count": 1}}
        self.setup_data_dir_called = False
        self.load_contacts_called = False

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

    def _setup_data_dir(self):
        self.setup_data_dir_called = True

    def _load_contacts(self):
        self.load_contacts_called = True


class _JsonHandler:
    def __init__(self):
        self.status = None
        self.payload = None

    def _json_response(self, payload, status=200):
        self.status = status
        self.payload = payload


class QRStatusHandlerUnitTests(unittest.TestCase):
    def setUp(self):
        self.client = _FakeClient(logged_in=True)
        self.bridge = _FakeBridge()
        self.context = WebAppContext(client=self.client, bridge=self.bridge, api_token="secret-token")
        self.handler = _JsonHandler()

    def _call(self, qrcode="qr-test"):
        handle_qr_status(self.handler, self.context, {"qrcode": [qrcode]})
        return self.handler.status, self.handler.payload

    def test_scaned_qr_status_returns_message(self):
        self.client.qr_status_response = {"status": "scaned"}

        status, data = self._call("qr-scaned")

        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "scaned")
        self.assertEqual(data["message"], "已扫码，请在微信确认")
        self.assertEqual(self.client.polled_qrcode, "qr-scaned")

    def test_redirect_qr_status_returns_message(self):
        self.client.qr_status_response = {"status": "scaned_but_redirect"}

        status, data = self._call("qr-redirect")

        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "scaned_but_redirect")
        self.assertEqual(data["message"], "正在重定向")

    def test_expired_qr_status_clears_matching_qr_cache(self):
        self.context.qr_cache.data = {"qrcode": "qr-expired", "qrcode_img_content": "https://example.com/qr"}
        self.context.qr_cache.updated_at = 123.0
        self.client.qr_status_response = {"status": "expired"}

        status, data = self._call("qr-expired")

        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "expired")
        self.assertEqual(data["message"], "二维码已过期")
        self.assertIsNone(self.context.qr_cache.data)
        self.assertEqual(self.context.qr_cache.updated_at, 0.0)

    def test_confirmed_qr_status_refreshes_bridge_state_and_returns_message(self):
        self.client.qr_status_response = {"status": "confirmed"}

        status, data = self._call("qr-confirmed")

        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "confirmed")
        self.assertTrue(data["logged_in"])
        self.assertEqual(data["message"], "登录成功")
        self.assertTrue(self.bridge.setup_data_dir_called)
        self.assertTrue(self.bridge.load_contacts_called)
        self.assertEqual(self.bridge.recent_messages, [])
        self.assertEqual(self.bridge._consecutive_send_count, {})


class WebAppServerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self._old_config_file = cfg.CONFIG_FILE
        cfg.CONFIG_FILE = str(Path(self.tempdir.name) / "ai_config.json")
        cfg.save_config(cfg.DEFAULT_CONFIG.copy())
        self.client = _FakeClient(logged_in=True)
        self.bridge = _FakeBridge()
        self.context = WebAppContext(client=self.client, bridge=self.bridge, api_token="secret-token")
        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), BridgeHandler)
        except PermissionError as exc:
            raise unittest.SkipTest(f"socket bind not permitted in sandbox: {exc}")
        self.server.app_context = self.context  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        cfg.CONFIG_FILE = self._old_config_file
        self.tempdir.cleanup()

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
        self.assertIn("version", data)

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

    def test_api_push_post_json_composes_title_and_normalizes_markdown(self):
        payload = json.dumps(
            {
                "to": "Alice",
                "title": "市场简报 11:05",
                "content": "━━━━━━━━━━━━━━\n🔹 金财互联: +0.66%",
                "markdown": "normalize",
            }
        ).encode("utf-8")
        status, _, body = self._request(
            "/api/push",
            method="POST",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer secret-token",
            },
        )

        self.assertEqual(status, 200, body)
        self.assertEqual(
            self.bridge.sent,
            [("Alice", "## 市场简报 11:05\n\n---\n\n- 金财互联: +0.66%", "api_push", "市场简报 11:05")],
        )

    def test_api_ai_config_persists_webhook_settings(self):
        payload = json.dumps(
            {
                "webhook_enabled": True,
                "webhook_url": " https://example.com/webhook ",
                "webhook_mode": "all_messages",
                "webhook_timeout": 9,
            }
        ).encode("utf-8")
        status, _, body = self._request(
            "/api/ai_config",
            method="POST",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200, body)

        saved = cfg.load_config()
        self.assertTrue(saved["webhook_enabled"])
        self.assertEqual(saved["webhook_url"], "https://example.com/webhook")
        self.assertEqual(saved["webhook_mode"], "all_messages")
        self.assertEqual(saved["webhook_timeout"], 9)

    def test_expired_qr_status_clears_matching_qr_cache(self):
        self.context.qr_cache.data = {"qrcode": "qr-expired", "qrcode_img_content": "https://example.com/qr"}
        self.context.qr_cache.updated_at = 123.0
        self.client.qr_status_response = {"status": "expired"}

        status, _, body = self._request("/api/qr_status?qrcode=qr-expired")
        self.assertEqual(status, 200, body)
        data = json.loads(body)
        self.assertEqual(data["status"], "expired")
        self.assertEqual(data["message"], "二维码已过期")
        self.assertIsNone(self.context.qr_cache.data)
        self.assertEqual(self.context.qr_cache.updated_at, 0.0)

    def test_scaned_qr_status_returns_message(self):
        self.client.qr_status_response = {"status": "scaned"}

        status, _, body = self._request("/api/qr_status?qrcode=qr-scaned")

        self.assertEqual(status, 200, body)
        data = json.loads(body)
        self.assertEqual(data["status"], "scaned")
        self.assertEqual(data["message"], "已扫码，请在微信确认")
        self.assertEqual(self.client.polled_qrcode, "qr-scaned")

    def test_redirect_qr_status_returns_message(self):
        self.client.qr_status_response = {"status": "scaned_but_redirect"}

        status, _, body = self._request("/api/qr_status?qrcode=qr-redirect")

        self.assertEqual(status, 200, body)
        data = json.loads(body)
        self.assertEqual(data["status"], "scaned_but_redirect")
        self.assertEqual(data["message"], "正在重定向")

    def test_confirmed_qr_status_refreshes_bridge_state_and_returns_message(self):
        self.client.qr_status_response = {"status": "confirmed"}

        status, _, body = self._request("/api/qr_status?qrcode=qr-confirmed")

        self.assertEqual(status, 200, body)
        data = json.loads(body)
        self.assertEqual(data["status"], "confirmed")
        self.assertTrue(data["logged_in"])
        self.assertEqual(data["message"], "登录成功")
        self.assertTrue(self.bridge.setup_data_dir_called)
        self.assertTrue(self.bridge.load_contacts_called)
        self.assertEqual(self.bridge.recent_messages, [])
        self.assertEqual(self.bridge._consecutive_send_count, {})


if __name__ == "__main__":
    unittest.main()
