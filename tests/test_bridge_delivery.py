import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

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
        self.sent_videos = []
        self.sent_video_paths = []
        self.sent_voices = []
        self.sent_files = []
        self.sent_file_paths = []
        self.sent_references = []
        self.file_error = None
        self.reference_error = None

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

    def send_video(self, to_user_id: str, file_data: bytes, context_token: str = "", play_length: int = 0) -> dict:
        self.sent_videos.append((to_user_id, len(file_data), context_token, play_length))
        return {"to_user_id": to_user_id, "size": len(file_data), "play_length": play_length}

    def send_video_path(self, to_user_id: str, filepath: str, context_token: str = "", play_length: int = 0) -> dict:
        size = os.path.getsize(filepath)
        self.sent_video_paths.append((to_user_id, filepath, size, context_token, play_length))
        return {"to_user_id": to_user_id, "size": size, "play_length": play_length}

    def send_voice(self, to_user_id: str, file_data: bytes, context_token: str = "", playtime_ms: int = 0, text: str = "") -> dict:
        self.sent_voices.append((to_user_id, len(file_data), context_token, playtime_ms, text))
        return {"to_user_id": to_user_id, "size": len(file_data), "playtime_ms": playtime_ms}

    def send_file(self, to_user_id: str, file_data: bytes, context_token: str = "", file_name: str = "file.bin", text: str = "") -> dict:
        if self.file_error:
            raise self.file_error
        self.sent_files.append((to_user_id, len(file_data), context_token, file_name, text))
        return {"to_user_id": to_user_id, "size": len(file_data), "file_name": file_name}

    def send_file_path(self, to_user_id: str, filepath: str, context_token: str = "", file_name: str = "", text: str = "") -> dict:
        if self.file_error:
            raise self.file_error
        size = os.path.getsize(filepath)
        self.sent_file_paths.append((to_user_id, filepath, size, context_token, file_name, text))
        return {"to_user_id": to_user_id, "size": size, "file_name": file_name}

    def send_reference_text(self, to_user_id: str, text: str, context_token: str = "", ref_text: str = "", ref_title: str = "") -> dict:
        if self.reference_error:
            raise self.reference_error
        self.sent_references.append((to_user_id, text, context_token, ref_text, ref_title))
        return {"to_user_id": to_user_id, "text": text, "native_reference": False, "fallback": "text_quote"}


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
        try:
            self.tempdir.cleanup()
        except Exception as exc:
            import logging

            logging.warning(f"Failed to cleanup tempdir {self.tempdir.name}: {exc}")

    def test_tenth_message_appends_warning_and_creates_session(self):
        for idx in range(9):
            result = self.bridge.send("Alice", f"hello-{idx}")
            self.assertTrue(result["ok"])

        result = self.bridge.send("Alice", "hello-10")
        self.assertTrue(result["ok"])
        self.assertTrue(result["warning"])
        self.assertEqual(result["blocked_reason"], "quota_10")
        self.assertIn("## ⚠️ 微信 bot 10 条上限", self.client.sent_texts[-1][1])
        self.assertIn("- 回复任意内容恢复消息发送", self.client.sent_texts[-1][1])

        summary = self.bridge.get_delivery_summary("uid-1")
        self.assertEqual(summary["status"], "WARNED")
        self.assertEqual(summary["consecutive_send_count"], 10)
        self.assertEqual(summary["pending_count"], 0)
        self.assertIsNotNone(summary["active_overflow_session_id"])

    def test_direct_send_returns_queryable_delivery_contract(self):
        result = self.bridge.send("Alice", "hello-direct")

        self.assertTrue(result["ok"])
        self.assertEqual(result["delivery_stage"], "direct")
        self.assertIsNotNone(result["message_id"])
        self.assertIsNone(result["pending_message_id"])
        self.assertIsNone(result["blocked_reason"])
        self.assertIsNone(result["overflow_session_id"])

        message = self.bridge.db.get_message_by_msg_id(result["message_id"])
        self.assertIsNotNone(message)
        self.assertEqual(message["delivery_stage"], "direct")

    def test_request_id_is_idempotent_and_rejects_payload_conflict(self):
        first = self.bridge.send("Alice", "hello-once", request_id="nh_request_001")
        repeated = self.bridge.send("Alice", "hello-once", request_id="nh_request_001")
        conflict = self.bridge.send("Alice", "different", request_id="nh_request_001")

        self.assertTrue(first["ok"])
        self.assertEqual(first["message_id"], "nh_request_001")
        self.assertTrue(repeated["ok"])
        self.assertTrue(repeated["deduplicated"])
        self.assertEqual(repeated["message_id"], first["message_id"])
        self.assertEqual(len(self.client.sent_texts), 1)
        self.assertFalse(conflict["ok"])
        self.assertEqual(conflict["blocked_reason"], "idempotency_conflict")

    def test_invalid_request_id_is_rejected_before_send(self):
        result = self.bridge.send("Alice", "hello", request_id="bad request id")

        self.assertFalse(result["ok"])
        self.assertEqual(result["blocked_reason"], "invalid_request_id")
        self.assertEqual(self.client.sent_texts, [])

    def test_persistence_failure_does_not_claim_queryable_direct_delivery(self):
        with patch.object(self.bridge.db, "save_message", return_value=False):
            result = self.bridge.send("Alice", "hello", request_id="nh_persist_001")

        self.assertTrue(result["ok"])
        self.assertEqual(result["delivery_stage"], "uncertain")
        self.assertIsNone(result["message_id"])
        self.assertEqual(result["blocked_reason"], "local_persistence")
        self.assertIsNone(self.bridge.db.get_message_by_msg_id("nh_persist_001"))

    def test_extract_text_downloads_file_attachment_with_limit(self):
        cached = Path(self.bridge._media_dir) / "cached.bin"
        cached.write_bytes(b"PK\x03\x04ebook")
        msg = {
            "msg_id": "msg-file",
            "item_list": [
                {
                    "type": 4,
                    "file_item": {
                        "file_name": "My Book.epub",
                        "len": str(cached.stat().st_size),
                        "media": {"encrypt_query_param": "download-ref", "aes_key": "key"},
                    },
                }
            ],
        }

        with (
            patch.object(
                bridge_module.media,
                "extract_pic_info",
                return_value={"encrypted_query_param": "download-ref", "aes_key": "key"},
            ),
            patch.object(
                bridge_module.media,
                "download_and_decrypt_media",
                return_value=str(cached),
            ) as mock_download,
        ):
            text = self.bridge._extract_text(msg)

        self.assertEqual(text, "[文件:My Book.epub]")
        self.assertEqual(msg["_media_paths"], ["cached.bin"])
        mock_download.assert_called_once_with(
            encrypted_query_param="download-ref",
            aes_key_b64="key",
            msg_id="msg-file",
            media_type="file",
            media_dir=self.bridge._media_dir,
            max_bytes=bridge_module.FILE_MEDIA_MAX_BYTES,
        )

    def test_eleventh_message_is_buffered(self):
        for idx in range(10):
            result = self.bridge.send("Alice", f"hello-{idx}")
            self.assertTrue(result["ok"])

        result = self.bridge.send("Alice", "hello-11")
        self.assertTrue(result["ok"])
        self.assertTrue(result["buffered"])
        self.assertEqual(result["delivery_stage"], "buffered")
        self.assertIsNotNone(result["message_id"])
        self.assertIsNotNone(result["pending_message_id"])
        self.assertEqual(result["blocked_reason"], "quota_10")
        self.assertIsNotNone(result["overflow_session_id"])

        summary = self.bridge.get_delivery_summary("uid-1")
        self.assertEqual(summary["status"], "BUFFERING")
        self.assertEqual(summary["pending_count"], 1)

        messages = db.get_messages(limit=20)
        buffered = [message for message in messages if message["delivery_stage"] == "buffered"]
        self.assertEqual(len(buffered), 1)
        self.assertEqual(buffered[0]["meta"]["blocked_reason"], "quota_10")
        self.assertEqual(buffered[0]["msg_id"], result["message_id"])

    def test_pull_drains_buffered_messages_after_recovery(self):
        for idx in range(10):
            self.bridge.send("Alice", f"hello-{idx}")
        buffered_result = self.bridge.send("Alice", "hello-11")

        self.bridge._mark_user_recovered("uid-1", int(time.time()))
        result = self.bridge.pull_pending_messages("uid-1")

        self.assertTrue(result["ok"])
        self.assertEqual(result["remaining"], 0)
        summary = self.bridge.get_delivery_summary("uid-1")
        self.assertEqual(summary["status"], "DRAINED")
        self.assertEqual(summary["pending_count"], 0)

        buffered_message = self.bridge.db.get_message_by_msg_id(buffered_result["message_id"])
        self.assertIsNotNone(buffered_message)
        self.assertEqual(buffered_message["delivery_stage"], "pulled")

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

    def test_pull_timeout_is_uncertain_and_not_requeued_or_marked_pulled(self):
        for idx in range(10):
            self.bridge.send("Alice", f"hello-{idx}")
        buffered_result = self.bridge.send("Alice", "hello-11")
        pending_id = buffered_result["pending_message_id"]
        self.bridge._mark_user_recovered("uid-1", int(time.time()))

        def _raise_timeout(to_user_id: str, text: str, context_token: str = "") -> dict:
            raise requests.exceptions.ReadTimeout("Read timed out.")

        self.client.send_text = _raise_timeout
        result = self.bridge.pull_pending_messages("uid-1")

        self.assertFalse(result["ok"])
        self.assertEqual(result["uncertain_pending_ids"], [pending_id])
        self.assertEqual(result["remaining"], 0)
        self.assertEqual(self.bridge.db.get_pending_message(pending_id)["status"], "UNCERTAIN")
        self.assertEqual(
            self.bridge.db.get_message_by_msg_id(buffered_result["message_id"])["delivery_stage"],
            "uncertain",
        )
        self.assertEqual(self.bridge.pull_pending_messages("uid-1")["empty"], True)

    def test_bot_account_is_recorded_on_token_restore(self):
        account = db.get_bot_account("bot-test")
        events = db.list_bot_login_events("bot-test")

        self.assertIsNotNone(account)
        self.assertEqual(account["data_dir"], str(Path(self.tempdir.name) / "bot-test"))
        self.assertEqual(account["status"], "active")
        self.assertEqual(events[0]["event"], "token_restored")

    def test_contacts_are_ordered_by_latest_activity(self):
        self.bridge.contacts = {
            "uid-old": "Old",
            "uid-new": "New",
            "uid-empty": "Empty",
        }
        self.bridge.activity_tracker = {
            "uid-old": {"last_receive_time": 100, "reminded": False},
            "uid-new": {"last_receive_time": 200, "reminded": False},
        }

        ordered = self.bridge.get_ordered_contacts()

        self.assertEqual(list(ordered.keys()), ["uid-new", "uid-old", "uid-empty"])
        self.assertEqual(self.bridge.get_default_contact(), "uid-new")
        self.assertEqual(self.bridge.find_user_id("uid-new"), "uid-new")

    def test_visible_contacts_only_include_latest_contact(self):
        self.bridge.contacts = {
            "uid-old": "Old",
            "uid-new": "New",
            "uid-empty": "Empty",
        }
        self.bridge.activity_tracker = {
            "uid-old": {"last_receive_time": 100, "reminded": False},
            "uid-new": {"last_receive_time": 200, "reminded": False},
        }

        visible = self.bridge.get_visible_contacts()

        self.assertEqual(visible, {"uid-new": "New"})
        self.assertEqual(set(self.bridge.contacts), {"uid-old", "uid-new", "uid-empty"})
        self.assertEqual(self.bridge.find_user_id("uid-old"), "uid-old")

    def test_visible_delivery_status_ignores_hidden_history(self):
        self.bridge.contacts = {
            "uid-new": "New",
            "uid-old": "Old",
        }
        self.bridge.activity_tracker = {
            "uid-new": {"last_receive_time": 200, "reminded": False},
            "uid-old": {"last_receive_time": 100, "reminded": False},
        }
        session = db.create_overflow_session("ofs-old", "uid-old", "window_24h")
        db.create_pending_message(
            session_id=session["id"],
            user_id="uid-old",
            source="api",
            title="",
            content="old pending",
            blocked_reason="window_24h",
        )
        self.bridge._set_delivery_state(
            "uid-old",
            status="BUFFERING",
            blocked_reason="window_24h",
            active_overflow_session_id=session["id"],
        )

        summaries = self.bridge.get_visible_contact_delivery_summaries()
        status = self.bridge.get_runtime_status()

        self.assertEqual(set(summaries.keys()), {"uid-new"})
        self.assertEqual(status["contacts_count"], 1)
        self.assertEqual(status["contacts_total"], 2)
        self.assertEqual(status["pending_total"], 0)
        self.assertEqual(status["active_sessions"], 0)
        self.assertEqual(status["buffering_users"], 0)
        self.assertEqual(status["all_pending_total"], 1)
        self.assertEqual(status["all_active_sessions"], 1)
        self.assertEqual(status["all_buffering_users"], 1)

    def test_contact_order_ignores_newer_outbound_messages(self):
        self.bridge.contacts = {
            "uid-inbound": "Inbound",
            "uid-outbound": "Outbound",
        }
        db.save_message(
            {
                "msg_id": "recv-new",
                "type": "recv",
                "contact": "Inbound",
                "user_id": "uid-inbound",
                "text": "hello",
                "time": 200,
            }
        )
        db.save_message(
            {
                "msg_id": "send-newer",
                "type": "send",
                "contact": "Outbound",
                "user_id": "uid-outbound",
                "text": "system push",
                "time": 300,
            }
        )

        ordered = self.bridge.get_ordered_contacts()

        self.assertEqual(list(ordered.keys()), ["uid-inbound", "uid-outbound"])

    def test_contact_activity_records_inbound_and_outbound(self):
        user_id = "uid-activity"
        self.bridge.process_message(
            {
                "message_type": 1,
                "from_user_id": user_id,
                "from_user_nickname": "Activity",
                "context_token": "ctx-activity",
                "msg_id": "activity-1",
                "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
            }
        )
        inbound = db.get_contact_activity(user_id)

        self.assertEqual(inbound["display_name"], "Activity")
        self.assertGreater(inbound["last_inbound_at"], 0)
        self.assertGreater(inbound["last_context_token_at"], 0)
        self.assertEqual(inbound["context_token_present"], 1)

        self.bridge.send("Activity", "reply")
        outbound = db.get_contact_activity(user_id)
        self.assertGreater(outbound["last_outbound_at"], 0)

    def test_default_recipient_decision_is_recorded_without_message_body(self):
        self.bridge.record_default_recipient_decision(
            "uid-1",
            request_path="/api/send",
            source="api",
            title="Title",
            message_len=123,
        )

        decisions = db.list_default_recipient_decisions()

        self.assertEqual(decisions[0]["selected_user_id"], "uid-1")
        self.assertEqual(decisions[0]["selected_display_name"], "Alice")
        self.assertEqual(decisions[0]["reason"], "latest_inbound_contact")
        self.assertEqual(decisions[0]["message_len"], 123)
        self.assertNotIn("text", decisions[0])

    def test_load_contacts_clears_previous_account_state_when_cache_missing(self):
        self.bridge.contacts = {"uid-old": "Old"}
        self.bridge.context_tokens = {"uid-old": "ctx-old"}
        self.bridge.activity_tracker = {"uid-old": {"last_receive_time": 100, "reminded": False}}

        self.client.bot_id = "bot-new"
        self.bridge._setup_data_dir()
        self.bridge._load_contacts()

        self.assertEqual(self.bridge.contacts, {})
        self.assertEqual(self.bridge.context_tokens, {})
        self.assertEqual(self.bridge.activity_tracker, {})

    def test_two_bridges_keep_message_and_media_storage_isolated(self):
        client_two = _FakeClient()
        client_two.bot_id = "bot-two"
        bridge_two = bridge_module.WeChatBridge(client_two)
        bridge_two.contacts["uid-2"] = "Bob"
        bridge_two.context_tokens["uid-2"] = "ctx-2"
        bridge_two._save_contacts()

        self.bridge.send("Alice", "from bot one")
        bridge_two.send("Bob", "from bot two")

        messages_one = self.bridge.db.get_messages(limit=10)
        messages_two = bridge_two.db.get_messages(limit=10)
        self.assertTrue(any(message["text"] == "from bot one" for message in messages_one))
        self.assertFalse(any(message["text"] == "from bot two" for message in messages_one))
        self.assertTrue(any(message["text"] == "from bot two" for message in messages_two))
        self.assertNotEqual(self.bridge._media_dir, bridge_two._media_dir)

    def test_builtin_command_replies_are_markdown_formatted(self):
        # Disable webhook to ensure we get the fallback "未知指令" reply
        current_cfg = cfg.load_config()
        current_cfg["webhook_enabled"] = False
        cfg.save_config(current_cfg)

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

    def test_window_expired_message_does_not_buffer_when_disabled(self):
        self.bridge.activity_tracker["uid-1"] = {
            "last_receive_time": int(time.time()) - WINDOW_DEADLINE_SECONDS - 60,
            "reminded": False,
        }

        result = self.bridge.send("Alice", "late-message", allow_buffer=False)

        self.assertFalse(result["ok"])
        self.assertEqual(result["delivery_stage"], "failed")
        self.assertEqual(result["blocked_reason"], "window_24h")
        self.assertIn("24 小时", result["error"])
        self.assertEqual(self.client.sent_texts, [])
        self.assertEqual(self.bridge.get_delivery_summary("uid-1")["pending_count"], 0)

    def test_discarded_buffered_message_is_queryable_by_message_id(self):
        now_ts = int(time.time())
        self.bridge.activity_tracker["uid-1"] = {
            "last_receive_time": now_ts - WINDOW_DEADLINE_SECONDS - 60,
            "reminded": False,
        }
        buffered_result = self.bridge.send("Alice", "expired-buffered-message")

        cleanup_result = self.bridge.cleanup_expired_pending_messages(
            now_ts=now_ts + 73 * 3600,
            force=True,
        )

        self.assertEqual(cleanup_result["expired"], 1)
        message = self.bridge.db.get_message_by_msg_id(buffered_result["message_id"])
        self.assertIsNotNone(message)
        self.assertEqual(message["delivery_stage"], "discarded")

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

    def test_expired_default_pending_messages_are_discarded(self):
        now_ts = int(time.time())
        old_ts = now_ts - 73 * 3600
        session = self.bridge.db.create_overflow_session(
            "old-default-session",
            "uid-1",
            "window_24h",
            opened_at=old_ts,
        )
        pending = self.bridge.db.create_pending_message(
            session_id=session["id"],
            user_id="uid-1",
            source="api",
            content="old pending message",
            blocked_reason="window_24h",
            created_at=old_ts,
        )
        self.bridge._set_delivery_state(
            "uid-1",
            status="BUFFERING",
            active_overflow_session_id=session["id"],
            blocked_reason="window_24h",
        )

        result = self.bridge.cleanup_expired_pending_messages(now_ts=now_ts, force=True)

        self.assertEqual(result["expired"], 1)
        self.assertEqual(result["sessions_discarded"], 1)
        self.assertEqual(self.bridge.db.get_pending_message(pending["id"])["status"], "DISCARDED")
        self.assertEqual(self.bridge.db.get_overflow_session(session["id"])["status"], "DISCARDED")
        summary = self.bridge.get_delivery_summary("uid-1")
        self.assertEqual(summary["status"], "NORMAL")
        self.assertEqual(summary["pending_count"], 0)
        self.assertIsNone(summary["active_overflow_session_id"])

    def test_time_sensitive_pending_messages_expire_after_24_hours(self):
        now_ts = int(time.time())
        old_ts = now_ts - 25 * 3600
        session = self.bridge.db.create_overflow_session(
            "old-keepalive-session", "uid-1", "window_24h", opened_at=old_ts
        )
        pending = self.bridge.db.create_pending_message(
            session_id=session["id"],
            user_id="uid-1",
            source="keepalive",
            content="## ⏰ 通道保活提醒",
            blocked_reason="window_24h",
            created_at=old_ts,
        )

        result = self.bridge.cleanup_expired_pending_messages(now_ts=now_ts, force=True)

        self.assertEqual(result["expired"], 1)
        self.assertEqual(self.bridge.db.get_pending_message(pending["id"])["status"], "DISCARDED")

    def test_media_pending_messages_keep_for_seven_days(self):
        now_ts = int(time.time())
        old_ts = now_ts - 100 * 3600
        session = self.bridge.db.create_overflow_session("old-media-session", "uid-1", "window_24h", opened_at=old_ts)
        pending = self.bridge.db.create_pending_message(
            session_id=session["id"],
            user_id="uid-1",
            source="image",
            content="[图片:out_img.jpg]",
            media="out_img.jpg",
            blocked_reason="window_24h",
            created_at=old_ts,
        )

        result = self.bridge.cleanup_expired_pending_messages(now_ts=now_ts, force=True)
        self.assertEqual(result["expired"], 0)
        self.assertEqual(self.bridge.db.get_pending_message(pending["id"])["status"], "PENDING")

        result = self.bridge.cleanup_expired_pending_messages(now_ts=old_ts + 169 * 3600, force=True)
        self.assertEqual(result["expired"], 1)
        self.assertEqual(self.bridge.db.get_pending_message(pending["id"])["status"], "DISCARDED")

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
        self.assertIn("## ⚠️ 微信 bot 10 条上限", buffered[0]["text"])
        self.assertIn("- 回复任意内容恢复消息发送", buffered[0]["text"])

    def test_read_timeout_is_recorded_as_uncertain_delivery(self):
        def _raise_timeout(to_user_id: str, text: str, context_token: str = "") -> dict:
            self.client.sent_texts.append((to_user_id, text, context_token))
            raise requests.exceptions.ReadTimeout("Read timed out.")

        self.client.send_text = _raise_timeout

        result = self.bridge.send("Alice", "hello-timeout")

        self.assertTrue(result["ok"])
        self.assertTrue(result["uncertain"])
        self.assertEqual(result["delivery_stage"], "uncertain")
        self.assertIsNotNone(result["message_id"])
        self.assertIsNone(result["pending_message_id"])
        self.assertIsNone(result["blocked_reason"])
        self.assertIsNone(result["overflow_session_id"])
        summary = self.bridge.get_delivery_summary("uid-1")
        self.assertEqual(summary["status"], "NORMAL")
        self.assertEqual(summary["consecutive_send_count"], 1)

        messages = db.get_messages(limit=20)
        uncertain = [message for message in messages if message["delivery_stage"] == "uncertain"]
        self.assertEqual(len(uncertain), 1)
        self.assertEqual(uncertain[0]["text"], "hello-timeout")
        self.assertTrue(uncertain[0]["meta"]["delivery_uncertain"])
        self.assertEqual(uncertain[0]["msg_id"], result["message_id"])

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
                "item_list": [{"type": 1, "text_item": {"text": "/random_unknown_cmd xyz"}}],
            }
        )
        deadline = time.time() + 1.0
        while len(triggered) == 0 and time.time() < deadline:
            time.sleep(0.05)

        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0]["text"], "/random_unknown_cmd xyz")
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

    def test_send_video_success_records_media_in_message_history(self):
        video_bytes = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2048
        result = self.bridge.send_video("Alice", video_bytes, play_length=12)

        self.assertTrue(result["ok"])
        self.assertEqual(len(self.client.sent_videos), 1)
        sent_uid, sent_size, sent_context, sent_length = self.client.sent_videos[0]
        self.assertEqual(sent_uid, "uid-1")
        self.assertEqual(sent_size, len(video_bytes))
        self.assertEqual(sent_context, "ctx-1")
        self.assertEqual(sent_length, 12)

        messages = db.get_messages(limit=10)
        video_records = [m for m in messages if m["type"] == "send" and m["media"]]
        self.assertEqual(len(video_records), 1)
        self.assertIn("[视频:", video_records[0]["text"])
        self.assertEqual(video_records[0]["delivery_stage"], "direct")

    def test_send_video_path_moves_file_and_records_media(self):
        source_path = Path(self.tempdir.name) / "source.mp4"
        source_path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2048)

        result = self.bridge.send_video_path("Alice", str(source_path), play_length=8)

        self.assertTrue(result["ok"])
        self.assertFalse(source_path.exists())
        self.assertEqual(len(self.client.sent_video_paths), 1)
        sent_uid, sent_path, sent_size, sent_context, sent_length = self.client.sent_video_paths[0]
        self.assertEqual(sent_uid, "uid-1")
        self.assertEqual(sent_size, 2060)
        self.assertEqual(sent_context, "ctx-1")
        self.assertEqual(sent_length, 8)
        self.assertTrue(Path(sent_path).exists())

        messages = db.get_messages(limit=10)
        video_records = [m for m in messages if m["type"] == "send" and m["media"]]
        self.assertEqual(len(video_records), 1)
        self.assertTrue(video_records[0]["media"].startswith("out_video_"))

    def test_send_voice_success_records_media_in_message_history(self):
        voice_bytes = b"\x02#!SILK_V3.\x00" + b"\x00" * 128
        result = self.bridge.send_voice("Alice", voice_bytes, playtime_ms=1000)

        self.assertTrue(result["ok"])
        self.assertEqual(len(self.client.sent_voices), 1)
        sent_uid, sent_size, sent_context, sent_playtime, sent_text = self.client.sent_voices[0]
        self.assertEqual(sent_uid, "uid-1")
        self.assertEqual(sent_size, len(voice_bytes))
        self.assertEqual(sent_context, "ctx-1")
        self.assertEqual(sent_playtime, 1000)
        self.assertEqual(sent_text, "")

        messages = db.get_messages(limit=10)
        voice_records = [m for m in messages if m["type"] == "send" and m["media"]]
        self.assertEqual(len(voice_records), 1)
        self.assertIn("[语音:", voice_records[0]["text"])
        self.assertTrue(voice_records[0]["media"].startswith("out_voice_"))
        self.assertTrue(voice_records[0]["media"].endswith(".silk"))

    def test_send_voice_rejects_non_silk_audio(self):
        voice_bytes = b"#!AMR\n" + b"\x00" * 128

        result = self.bridge.send_voice("Alice", voice_bytes, playtime_ms=1000)

        self.assertFalse(result["ok"])
        self.assertIn("SILK", result["error"])
        self.assertEqual(self.client.sent_voices, [])

    def test_send_file_success_records_media_in_message_history(self):
        file_bytes = b"hello file"

        result = self.bridge.send_file("Alice", file_bytes, file_name="report.txt", text="附件说明")

        self.assertTrue(result["ok"])
        self.assertEqual(self.client.sent_files, [("uid-1", len(file_bytes), "ctx-1", "report.txt", "附件说明")])
        self.assertEqual(self.bridge.get_delivery_summary("uid-1")["consecutive_send_count"], 1)
        messages = db.get_messages(limit=10)
        file_records = [m for m in messages if m["type"] == "send" and m["media"]]
        self.assertEqual(len(file_records), 1)
        self.assertIn("[文件:report.txt]", file_records[0]["text"])
        self.assertTrue(file_records[0]["media"].startswith("out_file_"))

    def test_send_file_after_quota_is_blocked_without_pending_attachment(self):
        for idx in range(10):
            self.bridge.send("Alice", f"hello-{idx}")

        result = self.bridge.send_file("Alice", b"hello file", file_name="report.txt")

        self.assertFalse(result["ok"])
        self.assertTrue(result["blocked"])
        self.assertEqual(result["blocked_reason"], "quota_10")
        self.assertEqual(self.client.sent_files, [])
        summary = self.bridge.get_delivery_summary("uid-1")
        self.assertEqual(summary["status"], "BUFFERING")
        self.assertEqual(summary["pending_count"], 0)

    def test_send_file_path_success_records_media(self):
        source_path = Path(self.tempdir.name) / "source.epub"
        source_path.write_bytes(b"PK\x03\x04ebook")

        result = self.bridge.send_file_path("Alice", str(source_path), file_name="../bad/name.epub", text="公版书")

        self.assertTrue(result["ok"])
        self.assertTrue(source_path.exists())
        self.assertEqual(len(self.client.sent_file_paths), 1)
        sent_uid, sent_path, sent_size, sent_context, sent_name, sent_text = self.client.sent_file_paths[0]
        self.assertEqual(sent_uid, "uid-1")
        self.assertEqual(sent_size, source_path.stat().st_size)
        self.assertEqual(sent_context, "ctx-1")
        self.assertEqual(sent_name, "name.epub")
        self.assertEqual(sent_text, "公版书")
        self.assertNotEqual(sent_path, str(source_path))
        self.assertTrue(Path(sent_path).exists())
        messages = db.get_messages(limit=10)
        file_records = [m for m in messages if m["type"] == "send" and m["media"]]
        self.assertEqual(len(file_records), 1)
        self.assertIn("[文件:name.epub]", file_records[0]["text"])
        self.assertTrue(file_records[0]["media"].startswith("out_file_"))

    def test_send_file_path_after_quota_is_blocked_without_copy(self):
        source_path = Path(self.tempdir.name) / "source.epub"
        source_path.write_bytes(b"PK\x03\x04ebook")
        for idx in range(10):
            self.bridge.send("Alice", f"hello-{idx}")

        result = self.bridge.send_file_path("Alice", str(source_path), file_name="book.epub")

        self.assertFalse(result["ok"])
        self.assertTrue(result["blocked"])
        self.assertEqual(result["blocked_reason"], "quota_10")
        self.assertEqual(self.client.sent_file_paths, [])
        self.assertEqual(list(Path(self.bridge._media_dir).glob("out_file_*")), [])

    def test_send_file_ret_minus_two_marks_api_limit_without_pending_attachment(self):
        self.client.file_error = RuntimeError("API限制(ret=-2)：距离该用户最后一次发消息可能已超24小时")

        result = self.bridge.send_file("Alice", b"hello file", file_name="report.txt")

        self.assertFalse(result["ok"])
        self.assertTrue(result["blocked"])
        self.assertEqual(result["blocked_reason"], "api_limit")
        self.assertEqual(result["retry_after_user_reply"], True)
        summary = self.bridge.get_delivery_summary("uid-1")
        self.assertEqual(summary["status"], "BUFFERING")
        self.assertEqual(summary["blocked_reason"], "api_limit")
        self.assertEqual(summary["pending_count"], 0)

    def test_send_file_when_already_buffering_does_not_save_or_upload(self):
        self.bridge._set_delivery_state("uid-1", status="BUFFERING", blocked_reason="api_limit")

        result = self.bridge.send_file("Alice", b"hello file", file_name="report.txt")

        self.assertFalse(result["ok"])
        self.assertTrue(result["blocked"])
        self.assertEqual(result["blocked_reason"], "api_limit")
        self.assertNotIn("media", result)
        self.assertEqual(self.client.sent_files, [])
        self.assertEqual(list(Path(self.bridge._media_dir).glob("out_file_*")), [])

    def test_send_reference_text_records_reference_message(self):
        result = self.bridge.send_reference_text("Alice", "这是回复", ref_title="原消息", ref_text="被引用内容")

        self.assertTrue(result["ok"])
        self.assertFalse(result["native_reference"])
        self.assertEqual(result["fallback"], "text_quote")
        self.assertEqual(self.client.sent_texts, [("uid-1", "[引用:原消息 | 被引用内容]\n这是回复", "ctx-1")])
        self.assertEqual(self.client.sent_references, [])
        self.assertEqual(self.bridge.get_delivery_summary("uid-1")["consecutive_send_count"], 1)
        messages = db.get_messages(limit=10)
        ref_records = [m for m in messages if m["type"] == "send" and m["text"].startswith("[引用:")]
        self.assertEqual(len(ref_records), 1)
        self.assertEqual(ref_records[0]["text"], "[引用:原消息 | 被引用内容]\n这是回复")
        self.assertIn("这是回复", ref_records[0]["text"])
        self.assertEqual(ref_records[0]["meta"]["native_reference"], False)
        self.assertEqual(ref_records[0]["meta"]["fallback"], "text_quote")

    def test_send_reference_ret_minus_two_marks_api_limit(self):
        def _raise_limit(to_user_id: str, text: str, context_token: str = "") -> dict:
            raise RuntimeError("API限制(ret=-2)：距离该用户最后一次发消息可能已超24小时")

        self.client.send_text = _raise_limit

        result = self.bridge.send_reference_text("Alice", "这是回复", ref_title="原消息", ref_text="被引用内容")

        self.assertTrue(result["ok"])
        self.assertTrue(result["buffered"])
        self.assertFalse(result["native_reference"])
        self.assertEqual(result["fallback"], "text_quote")
        self.assertEqual(result["blocked_reason"], "api_limit")
        self.assertEqual(self.client.sent_references, [])
        summary = self.bridge.get_delivery_summary("uid-1")
        self.assertEqual(summary["status"], "BUFFERING")
        self.assertEqual(summary["blocked_reason"], "api_limit")
        messages = db.get_messages(limit=10)
        buffered = [m for m in messages if m["delivery_stage"] == "buffered" and m["text"].startswith("[引用:")]
        self.assertEqual(len(buffered), 1)
        self.assertEqual(buffered[0]["meta"]["native_reference"], False)
        self.assertEqual(buffered[0]["meta"]["fallback"], "text_quote")

    def test_cleanup_expired_media_files_deletes_old_outbound_videos_only(self):
        media_dir = Path(self.bridge._media_dir)
        old_video = media_dir / "out_video_old.mp4"
        fresh_video = media_dir / "out_video_fresh.mp4"
        old_image = media_dir / "out_img_old.jpg"
        old_video.write_bytes(b"old")
        fresh_video.write_bytes(b"fresh")
        old_image.write_bytes(b"image")
        now_ts = int(time.time())
        os.utime(old_video, (now_ts - 8 * 24 * 3600, now_ts - 8 * 24 * 3600))
        os.utime(fresh_video, (now_ts, now_ts))
        os.utime(old_image, (now_ts - 8 * 24 * 3600, now_ts - 8 * 24 * 3600))

        result = self.bridge.cleanup_expired_media_files(now_ts=now_ts, force=True)

        self.assertEqual(result["deleted"], 1)
        self.assertFalse(old_video.exists())
        self.assertTrue(fresh_video.exists())
        self.assertTrue(old_image.exists())

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
        buffered_img = [m for m in messages if m["delivery_stage"] == "buffered" and m["media"]]
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


class TestMuteFeature(BridgeDeliveryTests):
    """Tests for /mute command and its interaction with outbound delivery."""

    def _activate_mute(self, minutes: int = 60):
        """Helper: set mute via command channel (source='command')."""
        self.bridge.process_message(
            {
                "message_type": 1,
                "from_user_id": "uid-1",
                "from_user_nickname": "Alice",
                "context_token": "ctx-mute",
                "msg_id": "mute-cmd",
                "item_list": [{"type": 1, "text_item": {"text": f"/mute {minutes}"}}],
            }
        )

    def test_mute_command_reply_is_not_buffered(self):
        """After /mute activation, the confirmation reply itself must be delivered directly
        (source='command' is exempt from mute check)."""
        self._activate_mute(30)
        sent = [t for _, t, *_ in self.client.sent_texts]
        self.assertTrue(
            any("静默模式已开启" in t for t in sent),
            "Mute confirmation should be sent directly, not buffered",
        )

    def test_mute_buffers_api_messages(self):
        """Outbound API messages sent during mute period are buffered (ok=True, buffered=True)."""
        self._activate_mute(60)
        result = self.bridge.send("Alice", "这条应该被缓存", source="api")
        self.assertTrue(result.get("buffered"), "API message should be buffered during mute")
        self.assertNotIn("error", result)

    def test_mute_buffers_ai_messages(self):
        """AI-sourced messages are also buffered during mute."""
        self._activate_mute(60)
        result = self.bridge.send("Alice", "AI 回复", source="ai")
        self.assertTrue(result.get("buffered"), "AI message should be buffered during mute")

    def test_mute_keepalive_bypasses_mute(self):
        """Keepalive reminders must go through even when muted."""
        self._activate_mute(60)
        result = self.bridge.send("Alice", "保活提醒", source="keepalive")
        self.assertTrue(result["ok"])

    def test_mute_duration_30_minutes(self):
        """Plain integer is treated as minutes."""
        self.bridge.process_message(
            {
                "message_type": 1,
                "from_user_id": "uid-1",
                "from_user_nickname": "Alice",
                "context_token": "ctx-mute",
                "msg_id": "mute-30",
                "item_list": [{"type": 1, "text_item": {"text": "/mute 30"}}],
            }
        )
        mute_ts = self.bridge._mute_until.get("uid-1", 0)
        self.assertGreater(mute_ts, 0)
        import time

        expected = time.time() + 30 * 60
        self.assertAlmostEqual(mute_ts, expected, delta=5)

    def test_mute_duration_2h(self):
        """'2h' parses to 120 minutes."""
        self.bridge.process_message(
            {
                "message_type": 1,
                "from_user_id": "uid-1",
                "from_user_nickname": "Alice",
                "context_token": "ctx-mute",
                "msg_id": "mute-2h",
                "item_list": [{"type": 1, "text_item": {"text": "/mute 2h"}}],
            }
        )
        mute_ts = self.bridge._mute_until.get("uid-1", 0)
        import time

        expected = time.time() + 120 * 60
        self.assertAlmostEqual(mute_ts, expected, delta=5)

    def test_mute_duration_1_5h(self):
        """'1.5h' parses to 90 minutes."""
        self.bridge.process_message(
            {
                "message_type": 1,
                "from_user_id": "uid-1",
                "from_user_nickname": "Alice",
                "context_token": "ctx-mute",
                "msg_id": "mute-1h30",
                "item_list": [{"type": 1, "text_item": {"text": "/mute 1.5h"}}],
            }
        )
        mute_ts = self.bridge._mute_until.get("uid-1", 0)
        import time

        expected = time.time() + 90 * 60
        self.assertAlmostEqual(mute_ts, expected, delta=5)

    def test_mute_duration_30m(self):
        """'30m' suffix parses to 30 minutes."""
        self.bridge.process_message(
            {
                "message_type": 1,
                "from_user_id": "uid-1",
                "from_user_nickname": "Alice",
                "context_token": "ctx-mute",
                "msg_id": "mute-30m",
                "item_list": [{"type": 1, "text_item": {"text": "/mute 30m"}}],
            }
        )
        mute_ts = self.bridge._mute_until.get("uid-1", 0)
        import time

        expected = time.time() + 30 * 60
        self.assertAlmostEqual(mute_ts, expected, delta=5)

    def test_incoming_message_clears_mute(self):
        """Any incoming message from the user automatically clears mute."""
        self._activate_mute(60)
        mute_ts = self.bridge._mute_until.get("uid-1", 0)
        self.assertGreater(mute_ts, 0, "Mute should be active after /mute command")

        self.bridge.process_message(
            {
                "message_type": 1,
                "from_user_id": "uid-1",
                "from_user_nickname": "Alice",
                "context_token": "ctx-clear",
                "msg_id": "msg-clear",
                "item_list": [{"type": 1, "text_item": {"text": "随便说点什么"}}],
            }
        )
        self.assertEqual(self.bridge._mute_until.get("uid-1", 0), 0)

    def test_mute_status_no_args(self):
        """'/mute' with no args returns current status (not active)."""
        self.bridge.process_message(
            {
                "message_type": 1,
                "from_user_id": "uid-1",
                "from_user_nickname": "Alice",
                "context_token": "ctx-mute",
                "msg_id": "mute-query",
                "item_list": [{"type": 1, "text_item": {"text": "/mute"}}],
            }
        )
        sent = [t for _, t, *_ in self.client.sent_texts]
        self.assertTrue(
            any("静默模式" in t for t in sent),
            "Status query should reply with mute status info",
        )

    def test_mute_status_when_active(self):
        """'/mute' with no args while muted shows active status — tested via _handle_command
        directly because process_message clears mute on any inbound message before routing."""
        self._activate_mute(60)
        # Verify mute is set
        self.assertGreater(self.bridge._mute_until.get("uid-1", 0), 0)
        # Query status directly through the command handler (not via process_message which clears mute)
        reply = self.bridge._handle_command("/mute", "uid-1")
        self.assertIn("开启中", reply, "Direct command status should show mute is active")

    def test_mute_invalid_duration_returns_help(self):
        """'/mute abc' returns usage help, does not activate mute."""
        self.bridge.process_message(
            {
                "message_type": 1,
                "from_user_id": "uid-1",
                "from_user_nickname": "Alice",
                "context_token": "ctx-mute",
                "msg_id": "mute-invalid",
                "item_list": [{"type": 1, "text_item": {"text": "/mute abc"}}],
            }
        )
        self.assertEqual(self.bridge._mute_until.get("uid-1", 0), 0)
        sent = [t for _, t, *_ in self.client.sent_texts]
        self.assertTrue(any("用法" in t for t in sent))


if __name__ == "__main__":
    unittest.main()
