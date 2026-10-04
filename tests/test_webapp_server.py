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
import webapp.api_handlers as api_handlers
import webapp.server as server_mod
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


class _FakeMessageStore:
    def __init__(self):
        self.messages = {}

    def get_message_by_msg_id(self, message_id):
        return self.messages.get(message_id)


class _FakeBridge:
    def __init__(self):
        self.contacts = {"uid-1": "Alice"}
        self.context_tokens = {"uid-1": "ctx-token"}
        self._running = True
        self.ag_inbox = []
        self.sent = []
        self.send_allow_buffer = []
        self.db = _FakeMessageStore()
        self.sent_video_paths = []
        self.sent_voices = []
        self.sent_files = []
        self.sent_references = []
        self.default_recipient_decisions = []
        self.ai_manager = None
        self.recent_messages = ["stale-message"]
        self._consecutive_send_count = {"uid-1": {"count": 1}}
        self.delivery_summaries = {}
        self.setup_data_dir_called = False
        self.load_contacts_called = False

    def send(self, to, text, source="api", title="", allow_buffer=True, request_id=""):
        self.sent.append((to, text, source, title))
        self.send_allow_buffer.append(allow_buffer)
        return {"ok": True, "result": {"to": to, "text": text}, "message_id": request_id or None}

    def send_video_path(self, to, filepath, play_length=0):
        self.sent_video_paths.append((to, Path(filepath).read_bytes(), play_length, Path(filepath).exists()))
        return {"ok": True, "result": {"to": to, "size": Path(filepath).stat().st_size, "play_length": play_length}}

    def send_voice(self, to, file_data, playtime_ms=0, text=""):
        self.sent_voices.append((to, file_data, playtime_ms, text))
        return {"ok": True, "result": {"to": to, "size": len(file_data), "playtime_ms": playtime_ms}}

    def send_file(self, to, file_data, file_name="file.bin", text=""):
        self.sent_files.append((to, file_data, file_name, text))
        return {"ok": True, "result": {"to": to, "size": len(file_data), "file_name": file_name}}

    def send_reference_text(self, to, text, ref_text="", ref_title=""):
        self.sent_references.append((to, text, ref_text, ref_title))
        return {"ok": True, "result": {"to": to, "text": text}, "native_reference": False, "fallback": "text_quote"}

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
            uid: self.delivery_summaries.get(
                uid,
                {
                    "user_id": uid,
                    "contact": name,
                    "status": "NORMAL",
                    "blocked_reason_text": "无",
                    "pending_count": 0,
                    "active_overflow_session_id": None,
                },
            )
            for uid, name in self.contacts.items()
        }

    def get_visible_contact_delivery_summaries(self):
        return {
            uid: summary
            for uid, summary in self.get_contact_delivery_summaries().items()
            if uid in self.get_visible_contacts()
        }

    def get_visible_contacts(self):
        return dict(list(self.get_ordered_contacts().items())[:1])

    def get_ordered_contacts(self):
        return self.contacts

    def get_default_contact(self):
        return next(iter(self.get_ordered_contacts()), "")

    def record_default_recipient_decision(self, selected_user_id, **kwargs):
        self.default_recipient_decisions.append((selected_user_id, kwargs))

    def record_account_event(self, event, **kwargs):
        self.last_account_event = (event, kwargs)

    def _setup_data_dir(self):
        self.setup_data_dir_called = True

    def _load_contacts(self):
        self.load_contacts_called = True


class _FakeAIManager:
    def __init__(self):
        self.calls = []

    def one_shot(self, prompt, system_prompt=None):
        self.calls.append((prompt, system_prompt))
        return f"分析结果: {prompt}"


class _JsonHandler:
    def __init__(self):
        self.status = None
        self.payload = None

    def _json_response(self, payload, status=200):
        self.status = status
        self.payload = payload


class _Runtime:
    def __init__(self, bot_id, bridge):
        self.bot_id = bot_id
        self.bridge = bridge
        self.client = bridge.client
        self.data_dir = f"/tmp/{bot_id}"


class _FakeAccountManager:
    def __init__(self, default_bot_id="bot-a"):
        self.default_bot_id = default_bot_id
        self.bridge_a = _FakeBridge()
        self.bridge_a.client = _FakeClient(logged_in=True)
        self.bridge_a.client.bot_id = "bot-a"
        self.bridge_a.client.user_id = "wx-a"
        self.bridge_a.contacts = {"uid-a": "Alice"}
        self.bridge_b = _FakeBridge()
        self.bridge_b.client = _FakeClient(logged_in=True)
        self.bridge_b.client.bot_id = "bot-b"
        self.bridge_b.client.user_id = "wx-b"
        self.bridge_b.contacts = {"uid-b": "Bob"}
        self._runtimes = {
            "bot-a": _Runtime("bot-a", self.bridge_a),
            "bot-b": _Runtime("bot-b", self.bridge_b),
        }

    @property
    def runtimes(self):
        return dict(self._runtimes)

    def get_runtime(self, bot_id=None):
        if not bot_id:
            return self._runtimes.get(self.default_bot_id)
        aliases = {
            "主号": "bot-a",
            "副号": "bot-b",
            "wx-a": "bot-a",
            "wx-b": "bot-b",
        }
        return self._runtimes.get(bot_id) or self._runtimes.get(aliases.get(bot_id, ""))

    def has_accounts(self):
        return True

    def any_logged_in(self):
        return True

    def list_accounts(self):
        return [
            {
                "bot_id": "bot-a",
                "remark": "主号",
                "ilink_user_id": "wx-a",
                "logged_in": True,
                "is_default": 1 if self.default_bot_id == "bot-a" else 0,
            },
            {
                "bot_id": "bot-b",
                "remark": "副号",
                "ilink_user_id": "wx-b",
                "logged_in": True,
                "is_default": 1 if self.default_bot_id == "bot-b" else 0,
            },
            {
                "bot_id": "bot-stale",
                "remark": "离线号",
                "ilink_user_id": "wx-stale",
                "logged_in": False,
                "is_default": 0,
            },
        ]

    def set_default(self, bot_id):
        if bot_id not in self._runtimes:
            return False
        self.default_bot_id = bot_id
        return True

    def logout(self, bot_id=None):
        return self._runtimes.pop(bot_id or self.default_bot_id, None) is not None


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
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

        status, _, body = self._request("/api/web_check", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["authed"])
        self.assertTrue(data["need_auth"])

    def test_internal_post_error_returns_generic_message(self):
        original_routes = dict(server_mod.POST_API_ROUTES)

        def _raise(handler, ctx, params, body):
            raise RuntimeError("private internal detail")

        try:
            server_mod.POST_API_ROUTES["/api/bomb"] = _raise
            status, _, body = self._request(
                "/api/bomb",
                method="POST",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )
        finally:
            server_mod.POST_API_ROUTES.clear()
            server_mod.POST_API_ROUTES.update(original_routes)

        self.assertEqual(status, 500)
        self.assertIn("Internal server error", body)
        self.assertNotIn("private internal detail", body)

    def test_internal_get_error_returns_generic_message(self):
        original_routes = dict(server_mod.GET_API_ROUTES)

        def _raise(handler, ctx, params):
            raise RuntimeError("private get detail")

        try:
            server_mod.GET_API_ROUTES["/api/bomb"] = _raise
            status, _, body = self._request("/api/bomb")
        finally:
            server_mod.GET_API_ROUTES.clear()
            server_mod.GET_API_ROUTES.update(original_routes)

        self.assertEqual(status, 500)
        self.assertIn("Internal server error", body)
        self.assertNotIn("private get detail", body)

    def test_weather_module_loader_finds_repo_examples_dir(self):
        previous_module = api_handlers._weather_module
        api_handlers._weather_module = None
        try:
            module = api_handlers._load_weather_module()
        finally:
            api_handlers._weather_module = previous_module

        self.assertTrue(hasattr(module, "build_weather_snapshot"))
        self.assertEqual(module.DEFAULT_WEATHER_CITY, "紫金")

    def test_api_status_returns_service_state(self):
        status, _, body = self._request("/api/status")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["logged_in"])
        self.assertEqual(data["bot_id"], "bot-test")
        self.assertEqual(data["contacts_count"], 1)
        self.assertIn("version", data)

    def test_api_contacts_returns_only_visible_contact_by_default(self):
        self.bridge.contacts = {"uid-new": "New", "uid-old": "Old"}
        self.bridge.context_tokens = {"uid-new": "ctx-new", "uid-old": "ctx-old"}
        self.bridge.delivery_summaries = {
            "uid-old": {
                "user_id": "uid-old",
                "contact": "Old",
                "status": "BUFFERING",
                "blocked_reason_text": "24h 窗口失效",
                "pending_count": 1,
                "active_overflow_session_id": "ofs-old",
            }
        }

        status, _, body = self._request(
            "/api/contacts",
            headers={"Authorization": "Bearer secret-token"},
        )

        self.assertEqual(status, 200, body)
        data = json.loads(body)
        self.assertEqual(data["contacts"], {"uid-new": "New"})
        self.assertEqual(data["context_tokens"], {"uid-new": "ctx-new..."})
        self.assertEqual(set(data["delivery_states"].keys()), {"uid-new"})
        self.assertEqual(data["contacts_total"], 2)

    def test_api_contacts_can_include_history_for_diagnostics(self):
        self.bridge.contacts = {"uid-new": "New", "uid-old": "Old"}
        self.bridge.context_tokens = {"uid-new": "ctx-new", "uid-old": "ctx-old"}

        status, _, body = self._request(
            "/api/contacts?include_history=1",
            headers={"Authorization": "Bearer secret-token"},
        )

        self.assertEqual(status, 200, body)
        data = json.loads(body)
        self.assertEqual(data["contacts"], {"uid-new": "New", "uid-old": "Old"})
        self.assertEqual(set(data["context_tokens"].keys()), {"uid-new", "uid-old"})

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
        self.assertEqual(self.bridge.send_allow_buffer, [True])

    def test_api_send_post_can_disable_buffering(self):
        payload = json.dumps({"to": "Alice", "text": "hello", "allow_buffer": False}).encode("utf-8")

        status, _, body = self._request(
            "/api/send",
            method="POST",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer secret-token",
            },
        )

        self.assertEqual(status, 200, body)
        self.assertEqual(self.bridge.send_allow_buffer, [False])

    def test_api_send_post_forwards_request_id(self):
        payload = json.dumps({"to": "Alice", "text": "hello", "request_id": "nh_api_001"}).encode("utf-8")

        status, _, body = self._request(
            "/api/send",
            method="POST",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer secret-token",
            },
        )

        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["message_id"], "nh_api_001")

    def test_api_send_get_can_disable_buffering(self):
        status, _, body = self._request(
            "/api/send?to=Alice&text=hello&allow_buffer=0",
            headers={"Authorization": "Bearer secret-token"},
        )

        self.assertEqual(status, 200, body)
        self.assertEqual(self.bridge.send_allow_buffer, [False])

    def test_api_delivery_requires_api_token(self):
        status, _, body = self._request("/api/delivery?message_id=message-1")

        self.assertEqual(status, 401)
        self.assertIn("Unauthorized", body)

    def test_api_delivery_returns_current_stage(self):
        self.bridge.db.messages["message-1"] = {
            "msg_id": "message-1",
            "delivery_stage": "pulled",
            "pending_message_id": 7,
            "overflow_session_id": "overflow-1",
            "meta": {"blocked_reason": "quota_10"},
        }

        status, _, body = self._request(
            "/api/delivery?message_id=message-1",
            headers={"Authorization": "Bearer secret-token"},
        )

        self.assertEqual(status, 200, body)
        data = json.loads(body)
        self.assertEqual(data["message_id"], "message-1")
        self.assertEqual(data["delivery_stage"], "pulled")
        self.assertEqual(data["pending_message_id"], 7)
        self.assertEqual(data["blocked_reason"], "quota_10")
        self.assertEqual(data["overflow_session_id"], "overflow-1")

    def test_api_delivery_reads_local_state_while_account_is_logged_out(self):
        self.client.logged_in = False
        self.bridge.db.messages["message-offline"] = {
            "msg_id": "message-offline",
            "delivery_stage": "discarded",
        }

        status, _, body = self._request(
            "/api/delivery?message_id=message-offline",
            headers={"Authorization": "Bearer secret-token"},
        )

        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["delivery_stage"], "discarded")

    def test_api_send_without_to_uses_default_contact(self):
        self.bridge.contacts = {"uid-new": "New", "uid-old": "Old"}
        payload = json.dumps({"text": "hello"}).encode("utf-8")
        status, _, body = self._request(
            "/api/send",
            method="POST",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer secret-token",
            },
        )

        self.assertEqual(status, 200, body)
        self.assertEqual(self.bridge.sent, [("uid-new", "hello", "api", "")])
        self.assertEqual(self.bridge.default_recipient_decisions[0][0], "uid-new")
        self.assertEqual(self.bridge.default_recipient_decisions[0][1]["source"], "api")
        self.assertEqual(self.bridge.default_recipient_decisions[0][1]["message_len"], 5)

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

    def test_api_ai_analyze_calls_one_shot(self):
        self.bridge.ai_manager = _FakeAIManager()
        payload = json.dumps({"prompt": "分析签到失败", "system_prompt": "你是青龙运维助手"}).encode("utf-8")

        status, _, body = self._request(
            "/api/ai_analyze",
            method="POST",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer secret-token",
            },
        )

        self.assertEqual(status, 200, body)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertEqual(data["result"], "分析结果: 分析签到失败")
        self.assertEqual(data["text"], "分析结果: 分析签到失败")
        self.assertEqual(self.bridge.ai_manager.calls, [("分析签到失败", "你是青龙运维助手")])

    def test_api_ai_analyze_accepts_content_alias(self):
        self.bridge.ai_manager = _FakeAIManager()
        payload = json.dumps({"content": "分析内容字段"}).encode("utf-8")

        status, _, body = self._request(
            "/api/ai_analyze",
            method="POST",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer secret-token",
            },
        )

        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["result"], "分析结果: 分析内容字段")

    def test_api_ai_analyze_requires_prompt(self):
        self.bridge.ai_manager = _FakeAIManager()
        payload = json.dumps({"prompt": ""}).encode("utf-8")

        status, _, body = self._request(
            "/api/ai_analyze",
            method="POST",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer secret-token",
            },
        )

        self.assertEqual(status, 400)
        self.assertIn("缺少 prompt", body)

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

    def test_send_video_route_uses_video_path_sender(self):
        boundary = "----video-boundary"
        video_bytes = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2048
        body = (
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="to"\r\n\r\n'
                "Alice\r\n"
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="video"; filename="a.mp4"\r\n'
                "Content-Type: video/mp4\r\n\r\n"
            ).encode()
            + video_bytes
            + f"\r\n--{boundary}--\r\n".encode()
        )

        status, _, resp_body = self._request(
            "/api/send_video?play_length=3",
            method="POST",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Authorization": "Bearer secret-token",
            },
        )

        self.assertEqual(status, 200, resp_body)
        self.assertEqual(len(self.bridge.sent_video_paths), 1)
        to, data, play_length, existed_during_call = self.bridge.sent_video_paths[0]
        self.assertEqual(to, "Alice")
        self.assertEqual(data, video_bytes)
        self.assertEqual(play_length, 3)
        self.assertTrue(existed_during_call)

    def test_send_voice_route_sends_voice_bytes(self):
        boundary = "----voice-boundary"
        voice_bytes = b"\x02#!SILK_V3.\x00" + b"\x00" * 128
        body = (
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="to"\r\n\r\n'
                "Alice\r\n"
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="voice"; filename="a.silk"\r\n'
                "Content-Type: audio/silk\r\n\r\n"
            ).encode()
            + voice_bytes
            + f"\r\n--{boundary}--\r\n".encode()
        )

        status, _, resp_body = self._request(
            "/api/send_voice?playtime_ms=1000",
            method="POST",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Authorization": "Bearer secret-token",
            },
        )

        self.assertEqual(status, 200, resp_body)
        self.assertEqual(self.bridge.sent_voices, [("Alice", voice_bytes, 1000, "")])

    def test_send_voice_route_rejects_non_silk_audio(self):
        boundary = "----voice-boundary-reject"
        voice_bytes = b"#!AMR\n" + b"\x00" * 128
        body = (
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="to"\r\n\r\n'
                "Alice\r\n"
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="voice"; filename="a.amr"\r\n'
                "Content-Type: audio/amr\r\n\r\n"
            ).encode()
            + voice_bytes
            + f"\r\n--{boundary}--\r\n".encode()
        )

        status, _, resp_body = self._request(
            "/api/send_voice?playtime_ms=1000",
            method="POST",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Authorization": "Bearer secret-token",
            },
        )

        self.assertEqual(status, 400, resp_body)
        self.assertIn("SILK", resp_body)
        self.assertEqual(self.bridge.sent_voices, [])

    def test_send_file_route_sends_file_bytes_with_filename(self):
        boundary = "----file-boundary"
        file_bytes = b"hello file"
        body = (
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="to"\r\n\r\n'
                "Alice\r\n"
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="text"\r\n\r\n'
                "附件说明\r\n"
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="file"; filename="report.txt"\r\n'
                "Content-Type: text/plain\r\n\r\n"
            ).encode()
            + file_bytes
            + f"\r\n--{boundary}--\r\n".encode()
        )

        status, _, resp_body = self._request(
            "/api/send_file",
            method="POST",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Authorization": "Bearer secret-token",
            },
        )

        self.assertEqual(status, 200, resp_body)
        self.assertEqual(self.bridge.sent_files, [("Alice", file_bytes, "report.txt", "附件说明")])

    def test_send_reference_route_sends_reference_text(self):
        payload = {
            "to": "Alice",
            "text": "这是回复",
            "ref_title": "原消息",
            "ref_text": "被引用内容",
        }

        status, _, resp_body = self._request(
            "/api/send_reference",
            method="POST",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer secret-token"},
        )

        self.assertEqual(status, 200, resp_body)
        response = json.loads(resp_body)
        self.assertFalse(response["native_reference"])
        self.assertEqual(response["fallback"], "text_quote")
        self.assertEqual(self.bridge.sent_references, [("Alice", "这是回复", "被引用内容", "原消息")])


class MultiAccountWebAppServerTests(unittest.TestCase):
    def setUp(self):
        self.manager = _FakeAccountManager()
        self.context = WebAppContext(account_manager=self.manager, api_token="secret-token")
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

    def test_account_aliases_returns_remark_to_bot_map(self):
        status, _, body = self._request(
            "/api/accounts/aliases",
            headers={"Authorization": "Bearer secret-token"},
        )

        self.assertEqual(status, 200, body)
        data = json.loads(body)
        self.assertEqual(data["aliases"], {"主号": "bot-a", "副号": "bot-b"})
        self.assertEqual(data["default_bot_id"], "bot-a")
        self.assertEqual(data["accounts"][0]["alias"], "主号")
        self.assertNotIn("离线号", data["aliases"])

    def test_account_aliases_can_include_offline_accounts(self):
        status, _, body = self._request(
            "/api/accounts/aliases?include_offline=1",
            headers={"Authorization": "Bearer secret-token"},
        )

        self.assertEqual(status, 200, body)
        data = json.loads(body)
        self.assertEqual(data["aliases"]["离线号"], "bot-stale")
        self.assertFalse(next(item for item in data["accounts"] if item["bot_id"] == "bot-stale")["logged_in"])

    def test_send_without_bot_id_uses_default_account(self):
        payload = json.dumps({"to": "Alice", "text": "hello default"}).encode("utf-8")

        status, _, body = self._request(
            "/api/send",
            method="POST",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": "Bearer secret-token"},
        )

        self.assertEqual(status, 200, body)
        self.assertEqual(self.manager.bridge_a.sent, [("Alice", "hello default", "api", "")])
        self.assertEqual(self.manager.bridge_b.sent, [])

    def test_send_with_bot_id_routes_to_selected_account(self):
        payload = json.dumps({"bot_id": "bot-b", "to": "Bob", "text": "hello b"}).encode("utf-8")

        status, _, body = self._request(
            "/api/send",
            method="POST",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": "Bearer secret-token"},
        )

        self.assertEqual(status, 200, body)
        self.assertEqual(self.manager.bridge_b.sent, [("Bob", "hello b", "api", "")])
        self.assertEqual(self.manager.bridge_a.sent, [])

    def test_send_with_bot_id_remark_routes_to_selected_account(self):
        payload = json.dumps({"bot_id": "副号", "to": "Bob", "text": "hello remark"}).encode("utf-8")

        status, _, body = self._request(
            "/api/send",
            method="POST",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": "Bearer secret-token"},
        )

        self.assertEqual(status, 200, body)
        self.assertEqual(self.manager.bridge_b.sent, [("Bob", "hello remark", "api", "")])
        self.assertEqual(self.manager.bridge_a.sent, [])

    def test_send_with_to_remark_prefix_routes_to_selected_account(self):
        payload = json.dumps({"to": "副号:Bob", "text": "hello alias"}).encode("utf-8")

        status, _, body = self._request(
            "/api/send",
            method="POST",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": "Bearer secret-token"},
        )

        self.assertEqual(status, 200, body)
        self.assertEqual(self.manager.bridge_b.sent, [("Bob", "hello alias", "api", "")])
        self.assertEqual(self.manager.bridge_a.sent, [])
        data = json.loads(body)
        self.assertEqual(data["bot_id"], "bot-b")
        self.assertEqual(data["resolved_to"], "Bob")

    def test_delivery_query_uses_selected_account(self):
        self.manager.bridge_a.db.messages["message-1"] = {
            "msg_id": "message-1",
            "delivery_stage": "pulled",
        }
        self.manager.bridge_b.db.messages["message-1"] = {
            "msg_id": "message-1",
            "delivery_stage": "discarded",
            "pending_message_id": 9,
            "overflow_session_id": "overflow-b",
            "meta": {"blocked_reason": "window_24h"},
        }

        status, _, body = self._request(
            "/api/delivery?bot_id=bot-b&message_id=message-1",
            headers={"Authorization": "Bearer secret-token"},
        )

        self.assertEqual(status, 200, body)
        data = json.loads(body)
        self.assertEqual(data["delivery_stage"], "discarded")
        self.assertEqual(data["pending_message_id"], 9)
        self.assertEqual(data["blocked_reason"], "window_24h")
        self.assertEqual(data["overflow_session_id"], "overflow-b")

    def test_invalid_bot_id_returns_404(self):
        payload = json.dumps({"bot_id": "missing", "to": "Bob", "text": "hello"}).encode("utf-8")

        status, _, body = self._request(
            "/api/send",
            method="POST",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": "Bearer secret-token"},
        )

        self.assertEqual(status, 404)
        self.assertIn("账号不存在", body)


if __name__ == "__main__":
    unittest.main()
