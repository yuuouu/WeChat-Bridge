import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import bridge as bridge_module
import db


class TestDB(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self._old_data_dir = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = self.tempdir.name
        bridge_module.DATA_BASE = self.tempdir.name
        db.close_db()
        db_file = os.path.join(self.tempdir.name, "messages.db")
        db._active_db_file = db_file
        db._active_accounts_db_file = os.path.join(self.tempdir.name, "accounts.db")
        db.init_db(db_file)

    def tearDown(self):
        db.close_db()
        if self._old_data_dir is None:
            os.environ.pop("DATA_DIR", None)
            bridge_module.DATA_BASE = "./data"
        else:
            os.environ["DATA_DIR"] = self._old_data_dir
            bridge_module.DATA_BASE = self._old_data_dir
        db._active_db_file = db.DB_FILE
        db._active_accounts_db_file = db.ACCOUNTS_DB_FILE
        self.tempdir.cleanup()

    def test_save_and_get_message(self):
        msg = {
            "msg_id": "test_msg_1",
            "type": "recv",
            "contact": "Alice",
            "user_id": "uid-1",
            "text": "hello from db test",
            "time": int(time.time()),
        }
        db.save_message(msg)

        saved_messages = db.get_messages(limit=10)
        self.assertEqual(len(saved_messages), 1)
        self.assertEqual(saved_messages[0]["text"], "hello from db test")
        self.assertEqual(saved_messages[0]["contact"], "Alice")
        self.assertEqual(saved_messages[0]["delivery_stage"], "direct")

    def test_update_message_delivery_stage(self):
        # We can test update_delivery_state instead
        db.update_delivery_state("uid-2", status="BUFFERING", blocked_reason="quota")
        state = db.get_delivery_state("uid-2")
        self.assertEqual(state["status"], "BUFFERING")
        self.assertEqual(state["blocked_reason"], "quota")

    def test_overflow_sessions(self):
        session = db.create_overflow_session("session-test-1", "uid-3", "api_limit", "test title")
        self.assertIsNotNone(session)

        session_id = session["id"]
        self.assertEqual(session["user_id"], "uid-3")
        self.assertEqual(session["reason"], "api_limit")
        self.assertEqual(session["status"], "OPEN")

        # Save pending message
        pending_msg = db.create_pending_message(session_id, "uid-3", "test text", title="test title")
        self.assertIsNotNone(pending_msg)

        pending_msgs = db.get_pending_messages(session_id)
        self.assertEqual(len(pending_msgs), 1)
        self.assertEqual(pending_msgs[0]["content"], "test text")

        db.mark_overflow_session_drained(session_id)
        session_after = db.get_overflow_session(session_id)
        self.assertEqual(session_after["status"], "DRAINED")


if __name__ == "__main__":
    unittest.main()
