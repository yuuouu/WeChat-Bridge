import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

import requests
from tests.crypto_stub import install_crypto_stub

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

install_crypto_stub()
import bridge as bridge_module
import config as cfg
import db
from bridge import WINDOW_DEADLINE_SECONDS


class _FakeClient:
    def __init__(self):
        self.logged_in = True
        self.bot_id = "bot-test"
        self.sent_texts = []
        self.sent_images = []

    def get_bot_id(self):
        return self.bot_id

    def send_text(self, to_user_id: str, text: str, context_token: str = "") -> dict:
        self.sent_texts.append((to_user_id, text, context_token))
        return {"to_user_id": to_user_id, "text": text}

    def send_typing(self, to_user_id: str, context_token: str = "") -> dict:
        return {"to_user_id": to_user_id, "typing": True}

    def send_image(self, to_user_id: str, file_data: bytes, context_token: str = "") -> dict:
        self.sent_images.append((to_user_id, len(file_data), context_token))
        return {"to_user_id": to_user_id, "size": len(file_data)}


class BridgeDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self._old_data_dir = os.environ.get("DATA_DIR")
        self._old_config_file = cfg.CONFIG_FILE
        os.environ["DATA_DIR"] = self.tempdir.name
        cfg.CONFIG_FILE = str(Path(self.tempdir.name) / "ai_config.json")
        cfg.save_config(cfg.DEFAULT_CONFIG.copy())
        self.client = _FakeClient()
        bridge_module.DATA_BASE = self.tempdir.name
        self.bridge = bridge_module.WeChatBridge(self.client)
        self.bridge.contacts["uid-1"] = "Alice"
        self.bridge.context_tokens["uid-1"] = "ctx-1"
        self.bridge._save_contacts()

    def tearDown(self):
        db.close_db()
        if self._old_data_dir is None:
            os.environ.pop("DATA_DIR", None)
            bridge_module.DATA_BASE = "./data"
        else:
            os.environ["DATA_DIR"] = self._old_data_dir
            bridge_module.DATA_BASE = self._old_data_dir
        cfg.CONFIG_FILE = self._old_config_file
        self.tempdir.cleanup()

    def test_tenth_message_appends_warning_and_creates_session(self):
        for idx in range(9):
            result = self.bridge.send("Alice", f"hello-{idx}")
            self.assertTrue(result["ok"])

        result = self.bridge.send("Alice", "hello-10")
        self.assertTrue(result["ok"])
        self.assertTrue(result["warning"])
        self.assertIn("⚠️【系统提醒】", self.client.sent_texts[-1][1])

        summary = self.bridge.get_delivery_summary("uid-1")
        self.assertEqual(summary["status"], "WARNED")
        self.assertEqual(summary["consecutive_send_count"], 10)
        self.assertEqual(summary["pending_count"], 0)
        self.assertIsNotNone(summary["active_overflow_session_id"])

    def test_eleventh_message_is_buffered(self):
        for idx in range(10):
            result = self.bridge.send("Alice", f"hello-{idx}")
            self.assertTrue(result["ok"])

        result = self.bridge.send("Alice", "hello-11")
        self.assertTrue(result["ok"])
        self.assertTrue(result["buffered"])

        summary = self.bridge.get_delivery_summary("uid-1")
        self.assertEqual(summary["status"], "BUFFERING")
        self.assertEqual(summary["pending_count"], 1)

        messages = db.get_messages(limit=20)
        buffered = [message for message in messages if message["delivery_stage"] == "buffered"]
        self.assertEqual(len(buffered), 1)
        self.assertEqual(buffered[0]["meta"]["blocked_reason"], "quota_10")

    def test_pull_drains_buffered_messages_after_recovery(self):
        for idx in range(10):
            self.bridge.send("Alice", f"hello-{idx}")
        self.bridge.send("Alice", "hello-11")

        self.bridge._mark_user_recovered("uid-1", int(time.time()))
        result = self.bridge.pull_pending_messages("uid-1")

        self.assertTrue(result["ok"])
        self.assertEqual(result["remaining"], 0)
        summary = self.bridge.get_delivery_summary("uid-1")
        self.assertEqual(summary["status"], "DRAINED")
        self.assertEqual(summary["pending_count"], 0)

        messages = db.get_messages(limit=20)
        self.assertTrue(any(message["delivery_stage"] == "pulled" for message in messages))
        self.assertTrue(
            any(
                message["type"] == "send"
                and message["meta"]
                and message["meta"].get("source") == "pull"
                and "hello-11" in message["text"]
                for message in messages
            )
        )

    def test_window_expired_messages_are_buffered(self):
        self.bridge.activity_tracker["uid-1"] = {
            "last_receive_time": int(time.time()) - WINDOW_DEADLINE_SECONDS - 60,
            "reminded": False,
        }

        result = self.bridge.send("Alice", "late-message")
        self.assertTrue(result["ok"])
        self.assertTrue(result["buffered"])

        summary = self.bridge.get_delivery_summary("uid-1")
        self.assertEqual(summary["status"], "BUFFERING")
        self.assertEqual(summary["blocked_reason"], "window_24h")
        self.assertEqual(summary["pending_count"], 1)

    def test_window_expired_images_are_buffered(self):
        self.bridge.activity_tracker["uid-1"] = {
            "last_receive_time": int(time.time()) - WINDOW_DEADLINE_SECONDS - 60,
            "reminded": False,
        }

        result = self.bridge.send_image("Alice", b"\xff\xd8\xff" * 128)
        self.assertTrue(result["ok"])
        self.assertTrue(result["buffered"])

        summary = self.bridge.get_delivery_summary("uid-1")
        self.assertEqual(summary["status"], "BUFFERING")
        self.assertEqual(summary["pending_count"], 1)

        session = db.get_overflow_session(summary["active_overflow_session_id"])
        pending = db.get_pending_messages(session["id"])[0]
        self.assertIsNotNone(pending["media"])

        messages = db.get_messages(limit=20)
        self.assertTrue(
            any(
                message["delivery_stage"] == "buffered"
                and message["media"]
                and message["meta"]
                and message["meta"].get("blocked_reason") == "window_24h"
                for message in messages
            )
        )

    def test_ret_minus_two_without_local_window_expiry_is_marked_as_api_limit(self):
        def _raise_limit(to_user_id: str, text: str, context_token: str = "") -> dict:
            raise RuntimeError(
                "API限制(ret=-2)：距离该用户最后一次发消息可能已超24小时，无法主动下发。请在微信上让对方先发一条消息。"
            )

        self.client.send_text = _raise_limit
        self.bridge.activity_tracker["uid-1"] = {
            "last_receive_time": int(time.time()) - 60,
            "reminded": False,
        }

        result = self.bridge.send("Alice", "hello-limit")

        self.assertTrue(result["ok"])
        self.assertTrue(result["buffered"])
        summary = self.bridge.get_delivery_summary("uid-1")
        self.assertEqual(summary["blocked_reason"], "api_limit")

        messages = db.get_messages(limit=20)
        buffered = [message for message in messages if message["delivery_stage"] == "buffered"]
        self.assertEqual(len(buffered), 1)
        self.assertEqual(buffered[0]["meta"]["blocked_reason"], "api_limit")

    def test_ret_minus_two_on_tenth_message_keeps_quota_warning(self):
        for idx in range(9):
            result = self.bridge.send("Alice", f"hello-{idx}")
            self.assertTrue(result["ok"])

        def _raise_limit(to_user_id: str, text: str, context_token: str = "") -> dict:
            raise RuntimeError(
                "API限制(ret=-2)：距离该用户最后一次发消息可能已超24小时，无法主动下发。请在微信上让对方先发一条消息。"
            )

        self.client.send_text = _raise_limit
        result = self.bridge.send("Alice", "hello-10")

        self.assertTrue(result["ok"])
        self.assertTrue(result["buffered"])
        summary = self.bridge.get_delivery_summary("uid-1")
        self.assertEqual(summary["blocked_reason"], "quota_10")

        messages = db.get_messages(limit=20)
        buffered = [message for message in messages if message["delivery_stage"] == "buffered"]
        self.assertEqual(len(buffered), 1)
        self.assertEqual(buffered[0]["meta"]["blocked_reason"], "quota_10")
        self.assertTrue(buffered[0]["meta"]["limit_warning"])
        self.assertIn("⚠️【系统提醒】", buffered[0]["text"])

    def test_read_timeout_is_recorded_as_uncertain_delivery(self):
        def _raise_timeout(to_user_id: str, text: str, context_token: str = "") -> dict:
            self.client.sent_texts.append((to_user_id, text, context_token))
            raise requests.exceptions.ReadTimeout("Read timed out.")

        self.client.send_text = _raise_timeout

        result = self.bridge.send("Alice", "hello-timeout")

        self.assertTrue(result["ok"])
        self.assertTrue(result["uncertain"])
        summary = self.bridge.get_delivery_summary("uid-1")
        self.assertEqual(summary["status"], "NORMAL")
        self.assertEqual(summary["consecutive_send_count"], 1)

        messages = db.get_messages(limit=20)
        uncertain = [message for message in messages if message["delivery_stage"] == "uncertain"]
        self.assertEqual(len(uncertain), 1)
        self.assertEqual(uncertain[0]["text"], "hello-timeout")
        self.assertTrue(uncertain[0]["meta"]["delivery_uncertain"])

    def test_unknown_command_can_be_handed_off_to_webhook(self):
        triggered = []
        current = cfg.load_config()
        current["webhook_enabled"] = True
        current["webhook_url"] = "https://example.com/hook"
        current["webhook_mode"] = "unknown_command"
        cfg.save_config(current)

        def _fake_trigger(from_user, from_name, text, msg, *, is_command=False):
            triggered.append(
                {
                    "from_user": from_user,
                    "from_name": from_name,
                    "text": text,
                    "is_command": is_command,
                }
            )

        self.bridge._trigger_webhook = _fake_trigger
        self.bridge.process_message(
            {
                "message_type": 1,
                "from_user_id": "uid-1",
                "from_user_nickname": "Alice",
                "context_token": "ctx-1",
                "msg_id": "m1",
                "item_list": [{"type": 1, "text_item": {"text": "/weather shanghai"}}],
            }
        )
        time.sleep(0.05)

        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0]["text"], "/weather shanghai")
        self.assertTrue(triggered[0]["is_command"])
        self.assertEqual(len(self.client.sent_texts), 0)

    def test_all_messages_mode_forwards_regular_messages(self):
        triggered = []
        current = cfg.load_config()
        current["webhook_enabled"] = True
        current["webhook_url"] = "https://example.com/hook"
        current["webhook_mode"] = "all_messages"
        cfg.save_config(current)

        def _fake_trigger(from_user, from_name, text, msg, *, is_command=False):
            triggered.append({"text": text, "is_command": is_command})

        self.bridge._trigger_webhook = _fake_trigger
        self.bridge.process_message(
            {
                "message_type": 1,
                "from_user_id": "uid-1",
                "from_user_nickname": "Alice",
                "context_token": "ctx-1",
                "msg_id": "m2",
                "item_list": [{"type": 1, "text_item": {"text": "hello bridge"}}],
            }
        )

        self.assertEqual(triggered, [{"text": "hello bridge", "is_command": False}])


if __name__ == "__main__":
    unittest.main()
