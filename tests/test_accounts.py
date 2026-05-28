import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from tests.crypto_stub import install_crypto_stub

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

install_crypto_stub()
import db
from account_identity import account_storage_dir_name
from accounts import AccountManager


class _FakeBridge:
    def __init__(self, client, data_base=None):
        self.client = client
        self.data_base = data_base
        self.contacts = {}
        self._running = False
        self._data_dir = os.path.join(
            data_base or "",
            account_storage_dir_name(
                bot_id=client.get_bot_id() or "",
                ilink_user_id=getattr(client, "user_id", "") or "",
            ),
        )
        self.ai_manager = None
        self.stopped = False
        self.events = []

    def start(self):
        self._running = True

    def stop(self):
        self._running = False
        self.stopped = True

    def get_runtime_status(self):
        return {
            "logged_in": self.client.logged_in,
            "bot_id": self.client.get_bot_id(),
            "contacts_count": len(self.contacts),
            "poll_running": self._running,
            "pending_total": 0,
            "active_sessions": 0,
            "buffering_users": 0,
        }

    def record_account_event(self, event, **kwargs):
        self.events.append((event, kwargs))
        db.record_bot_account_event(
            bot_id=self.client.get_bot_id() or "",
            ilink_user_id=getattr(self.client, "user_id", "") or "",
            event=event,
            data_dir=self._data_dir,
            base_url=getattr(self.client, "base_url", "") or "",
            reason=kwargs.get("reason", ""),
            token_mtime=self.client.get_token_mtime(),
        )


class _FakeLoginClient:
    def __init__(self, token_file=None, load_token=True, save_on_login=True):
        self.token_file = token_file
        self.save_on_login = save_on_login
        self.bot_token = None
        self.bot_id = None
        self.user_id = None
        self.base_url = "https://ilink.example.com"
        self.logged_in = False
        if token_file and load_token and os.path.exists(token_file):
            data = json.loads(Path(token_file).read_text())
            self.bot_token = data["bot_token"]
            self.bot_id = data["bot_id"]
            self.user_id = data.get("user_id")
            self.logged_in = True

    def get_bot_id(self):
        return self.bot_id

    def get_token_mtime(self):
        return int(os.path.getmtime(self.token_file)) if self.token_file and os.path.exists(self.token_file) else 0

    def clear_token(self):
        self.logged_in = False
        if self.token_file and os.path.exists(self.token_file):
            os.remove(self.token_file)

    def get_qrcode(self):
        return {"qrcode": "qr-new", "qrcode_img_content": "https://example.com/qr"}

    def poll_qrcode_status(self, qrcode):
        self.bot_token = "new-token@im.bot:hash"
        self.bot_id = "bot-new"
        self.user_id = "user-new"
        self.logged_in = True
        return {"status": "confirmed"}

    def _save_token(self):
        os.makedirs(os.path.dirname(self.token_file), exist_ok=True)
        Path(self.token_file).write_text(
            json.dumps(
                {
                    "bot_token": self.bot_token,
                    "bot_id": self.bot_id,
                    "user_id": self.user_id,
                    "base_url": self.base_url,
                }
            )
        )


class AccountManagerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        db.close_db()
        db._active_accounts_db_file = os.path.join(self.tempdir.name, "accounts.db")

    def tearDown(self):
        db.close_db()
        db._active_accounts_db_file = db.ACCOUNTS_DB_FILE
        self.tempdir.cleanup()

    def _write_token(self, bot_id: str):
        token_dir = Path(self.tempdir.name) / f"user-{bot_id}"
        token_dir.mkdir(parents=True)
        (token_dir / "token.json").write_text(
            json.dumps(
                {
                    "bot_token": f"{bot_id}@im.bot:hash",
                    "base_url": "https://ilink.example.com",
                    "bot_id": bot_id,
                    "user_id": f"user-{bot_id}",
                }
            )
        )

    def test_restore_accounts_starts_each_runtime_and_sets_default(self):
        self._write_token("bot-a")
        self._write_token("bot-b")
        manager = AccountManager(
            self.tempdir.name,
            client_factory=_FakeLoginClient,
            bridge_factory=_FakeBridge,
        )

        restored = manager.restore_accounts()

        self.assertEqual({runtime.bot_id for runtime in restored}, {"bot-a", "bot-b"})
        self.assertTrue(manager.get_runtime("bot-a").bridge._running)
        self.assertIn(db.get_default_bot_id(), {"bot-a", "bot-b"})

    def test_qr_confirmation_persists_token_and_starts_runtime(self):
        manager = AccountManager(
            self.tempdir.name,
            client_factory=_FakeLoginClient,
            bridge_factory=_FakeBridge,
        )
        qr = manager.create_login_qr()

        status = manager.poll_login_qr_status(qr["login_id"])

        self.assertEqual(status["status"], "confirmed")
        self.assertEqual(status["bot_id"], "bot-new")
        runtime = manager.get_runtime("bot-new")
        self.assertIsNotNone(runtime)
        self.assertTrue(Path(self.tempdir.name, "user-new", "token.json").exists())
        self.assertTrue(runtime.bridge._running)

    def test_qr_confirmation_migrates_previous_bot_dir_for_same_user(self):
        old_dir = Path(self.tempdir.name) / "bot-old"
        old_dir.mkdir(parents=True)
        old_store = db.MessageStore(str(old_dir / "messages.db"))
        old_store.save_message(
            {
                "msg_id": "msg-old",
                "type": "recv",
                "contact": "Alice",
                "user_id": "alice",
                "text": "old message",
                "time": 100,
            }
        )
        (old_dir / "contacts.json").write_text(json.dumps({"alice": "Alice"}), encoding="utf-8")
        db.record_bot_account_event(
            bot_id="bot-old",
            ilink_user_id="user-new",
            event="login_confirmed",
            data_dir=str(old_dir),
        )
        db.close_db()

        manager = AccountManager(
            self.tempdir.name,
            client_factory=_FakeLoginClient,
            bridge_factory=_FakeBridge,
        )
        qr = manager.create_login_qr()

        manager.poll_login_qr_status(qr["login_id"])

        stable_dir = Path(self.tempdir.name) / "user-new"
        self.assertTrue((stable_dir / "token.json").exists())
        self.assertEqual(json.loads((stable_dir / "contacts.json").read_text(encoding="utf-8")), {"alice": "Alice"})
        conn = sqlite3.connect(stable_dir / "messages.db")
        try:
            count = conn.execute("SELECT COUNT(*) FROM messages WHERE msg_id = 'msg-old'").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 1)

    def test_restore_migrates_legacy_bot_token_dir_to_user_dir(self):
        legacy_dir = Path(self.tempdir.name) / "bot-legacy"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "token.json").write_text(
            json.dumps(
                {
                    "bot_token": "bot-legacy@im.bot:hash",
                    "base_url": "https://ilink.example.com",
                    "bot_id": "bot-legacy",
                    "user_id": "user-legacy",
                }
            )
        )
        legacy_store = db.MessageStore(str(legacy_dir / "messages.db"))
        legacy_store.save_message(
            {
                "msg_id": "msg-legacy",
                "type": "recv",
                "contact": "Bob",
                "user_id": "bob",
                "text": "legacy message",
                "time": 200,
            }
        )
        db.close_db()
        manager = AccountManager(
            self.tempdir.name,
            client_factory=_FakeLoginClient,
            bridge_factory=_FakeBridge,
        )

        restored = manager.restore_accounts()

        self.assertEqual([runtime.bot_id for runtime in restored], ["bot-legacy"])
        stable_dir = Path(self.tempdir.name) / "user-legacy"
        self.assertTrue((stable_dir / "token.json").exists())
        self.assertFalse((legacy_dir / "token.json").exists())
        conn = sqlite3.connect(stable_dir / "messages.db")
        try:
            count = conn.execute("SELECT COUNT(*) FROM messages WHERE msg_id = 'msg-legacy'").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 1)

    def test_logout_stops_only_selected_account(self):
        self._write_token("bot-a")
        self._write_token("bot-b")
        manager = AccountManager(
            self.tempdir.name,
            client_factory=_FakeLoginClient,
            bridge_factory=_FakeBridge,
        )
        manager.restore_accounts()

        self.assertTrue(manager.logout("bot-a"))

        self.assertIsNone(manager.get_runtime("bot-a"))
        self.assertIsNotNone(manager.get_runtime("bot-b"))
        self.assertFalse(Path(self.tempdir.name, "user-bot-a", "token.json").exists())

    def test_logout_default_promotes_remaining_account(self):
        self._write_token("bot-a")
        self._write_token("bot-b")
        manager = AccountManager(
            self.tempdir.name,
            client_factory=_FakeLoginClient,
            bridge_factory=_FakeBridge,
        )
        manager.restore_accounts()
        self.assertTrue(manager.set_default("bot-a"))

        self.assertTrue(manager.logout("bot-a"))

        rows = {row["bot_id"]: row for row in manager.list_accounts()}
        self.assertEqual(db.get_default_bot_id(), "bot-b")
        self.assertEqual(rows["bot-a"]["status"], "logged_out")
        self.assertEqual(rows["bot-a"]["is_default"], 0)
        self.assertEqual(rows["bot-b"]["is_default"], 1)
        self.assertTrue(rows["bot-b"]["logged_in"])

    def test_logout_last_account_clears_default(self):
        self._write_token("bot-a")
        manager = AccountManager(
            self.tempdir.name,
            client_factory=_FakeLoginClient,
            bridge_factory=_FakeBridge,
        )
        manager.restore_accounts()

        self.assertTrue(manager.logout("bot-a"))

        self.assertIsNone(db.get_default_bot_id())
        self.assertFalse(manager.has_accounts())
        rows = manager.list_accounts()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["bot_id"], "bot-a")
        self.assertEqual(rows[0]["is_default"], 0)
        self.assertFalse(rows[0]["logged_in"])

    def test_list_accounts_treats_non_runtime_rows_as_logged_out(self):
        self._write_token("bot-a")
        manager = AccountManager(
            self.tempdir.name,
            client_factory=_FakeLoginClient,
            bridge_factory=_FakeBridge,
        )
        manager.restore_accounts()
        db.record_bot_account_event(
            bot_id="bot-stale",
            event="login_confirmed",
            data_dir=os.path.join(self.tempdir.name, "bot-stale"),
        )
        db.set_default_bot_account("bot-stale")

        rows = {row["bot_id"]: row for row in manager.list_accounts()}

        self.assertEqual(rows["bot-stale"]["status"], "logged_out")
        self.assertFalse(rows["bot-stale"]["logged_in"])
        self.assertEqual(rows["bot-stale"]["is_default"], 0)
        self.assertTrue(rows["bot-a"]["logged_in"])
        self.assertEqual(rows["bot-a"]["is_default"], 1)

    def test_expired_sessions_are_purged_on_create_qr(self):
        import time as _time

        manager = AccountManager(
            self.tempdir.name,
            client_factory=_FakeLoginClient,
            bridge_factory=_FakeBridge,
        )

        qr1 = manager.create_login_qr()
        old_id = qr1["login_id"]
        # Backdate the session so it looks 121 s old
        manager._login_sessions[old_id].created_at = _time.time() - 121

        manager.create_login_qr()

        self.assertNotIn(old_id, manager._login_sessions)

    def test_set_default_returns_false_for_unknown_bot(self):
        manager = AccountManager(
            self.tempdir.name,
            client_factory=_FakeLoginClient,
            bridge_factory=_FakeBridge,
        )
        self.assertFalse(manager.set_default("nonexistent-bot"))


if __name__ == "__main__":
    unittest.main()
