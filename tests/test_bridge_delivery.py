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
        self.assertIn("## ⚠️ 系统提醒", self.client.sent_texts[-1][1])
        self.assertIn("- Bot 已连续发送 10 条通知", self.client.sent_texts[-1][1])

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

    def test_builtin_command_replies_are_markdown_formatted(self):
        cases = [
            ("/help", "## 📋 可用指令", "- `/status`"),
            ("/status", "## 🤖 WeChat Bridge", "### 运行状态"),
            ("/ai", "## 🤖 AI 助手", "- **状态**：❌ 未启用"),
            ("/clear", "## ✅ 清除完成", "- AI 对话历史已清除"),
            ("/uid", "## 🆔 用户 ID", "`uid-1`"),
            ("/retry", "## 🤖 AI 助手", "- **状态**：❌ 未启用"),
            ("/keepalive bad", "## ❓ 用法", "`/keepalive on`"),
            ("/keepalive on", "## ✅ 保活提醒", "- **提醒时间**"),
            ("/keepalive off", "## ❌ 保活提醒", "- **状态**：已关闭"),
            ("/ai on", "## ✅ AI 助手", "- **提醒**"),
            ("/ai off", "## ❌ AI 助手", "- **状态**：已关闭"),
            ("/ai bad", "## ❓ 用法", "`/ai on`"),
            ("/unknown", "## ❓ 未知指令", "- **收到**：`/unknown`"),
        ]

        for command, heading, marker in cases:
            with self.subTest(command=command):
                reply = self.bridge._handle_command(command, "uid-1")
                self.assertIn(heading, reply)
                self.assertIn(marker, reply)

    def test_pull_empty_message_is_markdown_formatted(self):
        result = self.bridge.pull_pending_messages("uid-1")

        self.assertTrue(result["empty"])
        self.assertIn("## 📭 缓存消息", result["message"])
        self.assertIn("- 当前没有待拉取的缓存消息", result["message"])

    def test_pending_message_header_is_markdown_formatted(self):
        block = self.bridge._format_pending_message(
            {
                "id": 1,
                "created_at": int(time.time()),
                "source": "api",
                "blocked_reason": "quota_10",
                "title": "测试标题",
                "content": "测试内容",
                "media": "image.jpg",
            }
        )

        self.assertIn("### 缓存消息", block)
        self.assertIn("- **来源**：`api`", block)
        self.assertIn("- **原因**：`quota_10`", block)
        self.assertIn("**标题**：测试标题", block)
        self.assertIn("> 图片已缓存，文件：`image.jpg`", block)

    def test_retry_progress_message_is_markdown_formatted(self):
        class _FakeAIManager:
            def chat(self, user_id, text):
                return "AI reply"

        user_id = "uid-1@im.wechat"
        self.bridge.contacts[user_id] = "Alice"
        self.bridge.context_tokens[user_id] = "ctx-1"
        self.bridge.ai_manager = _FakeAIManager()
        self.bridge.recent_messages.append(
            {
                "user_id": user_id,
                "type": "recv",
                "text": "上一条问题",
            }
        )

        self.bridge.process_message(
            {
                "message_type": 1,
                "from_user_id": user_id,
                "from_user_nickname": "Alice",
                "context_token": "ctx-1",
                "msg_id": "retry-1",
                "item_list": [{"type": 1, "text_item": {"text": "/retry"}}],
            }
        )

        deadline = time.time() + 1
        while len(self.client.sent_texts) < 2 and time.time() < deadline:
            time.sleep(0.01)

        self.assertGreaterEqual(len(self.client.sent_texts), 2)
        self.assertIn("## 🔄 正在重试", self.client.sent_texts[0][1])
        self.assertIn("- 正在为您重新生成回答", self.client.sent_texts[0][1])

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
        self.assertIn("## ⚠️ 系统提醒", buffered[0]["text"])
        self.assertIn("- Bot 已连续发送 10 条通知", buffered[0]["text"])

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

    # ── send_image 边界场景 ──

    def test_send_image_success_records_media_in_message_history(self):
        """正常发送：消息记录里 media 字段指向保存的文件名。"""
        image_bytes = b"\xff\xd8\xff" + b"\x00" * 200
        result = self.bridge.send_image("Alice", image_bytes)

        self.assertTrue(result["ok"])
        self.assertFalse(result.get("buffered"))
        self.assertEqual(len(self.client.sent_images), 1)
        sent_uid, sent_size, _ = self.client.sent_images[0]
        self.assertEqual(sent_uid, "uid-1")
        self.assertEqual(sent_size, len(image_bytes))

        messages = db.get_messages(limit=10)
        img_records = [m for m in messages if m["type"] == "send" and m["media"]]
        self.assertEqual(len(img_records), 1)
        self.assertIn("[图片:", img_records[0]["text"])
        self.assertEqual(img_records[0]["delivery_stage"], "direct")

    def test_tenth_image_creates_overflow_session_without_appending_text(self):
        """第 10 张图片：建立 overflow session，但不向图片字节拼接告警文字。"""
        for idx in range(9):
            self.bridge.send("Alice", f"hello-{idx}")

        image_bytes = b"\xff\xd8\xff" + b"\x00" * 200
        result = self.bridge.send_image("Alice", image_bytes)

        self.assertTrue(result["ok"])
        self.assertTrue(result["warning"])
        # send_image 只调用了一次，发送的字节与原始一致（无文字拼接）
        self.assertEqual(len(self.client.sent_images), 1)
        self.assertEqual(self.client.sent_images[0][1], len(image_bytes))

        summary = self.bridge.get_delivery_summary("uid-1")
        self.assertEqual(summary["status"], "WARNED")
        self.assertEqual(summary["consecutive_send_count"], 10)
        self.assertIsNotNone(summary["active_overflow_session_id"])

    def test_eleventh_image_is_buffered_with_media_name(self):
        """第 11 张图片：进缓冲队列，pending_messages 里 media 字段不为空。"""
        for idx in range(10):
            self.bridge.send("Alice", f"hello-{idx}")

        image_bytes = b"\xff\xd8\xff" + b"\x00" * 200
        result = self.bridge.send_image("Alice", image_bytes)

        self.assertTrue(result["ok"])
        self.assertTrue(result["buffered"])

        summary = self.bridge.get_delivery_summary("uid-1")
        self.assertEqual(summary["status"], "BUFFERING")
        self.assertEqual(summary["pending_count"], 1)

        session = db.get_overflow_session(summary["active_overflow_session_id"])
        pending = db.get_pending_messages(session["id"])[0]
        self.assertIsNotNone(pending["media"])
        self.assertEqual(pending["blocked_reason"], "quota_10")

        messages = db.get_messages(limit=20)
        buffered_img = [
            m for m in messages
            if m["delivery_stage"] == "buffered" and m["media"]
        ]
        self.assertEqual(len(buffered_img), 1)

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
