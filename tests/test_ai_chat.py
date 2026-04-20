import sys
import unittest
from pathlib import Path
from unittest.mock import patch


APP_ROOT = Path(__file__).resolve().parents[1] / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from ai_chat import AIChatManager


class AIChatTests(unittest.TestCase):
    def setUp(self):
        self.saved_configs = []
        self.config = {
            "enabled": True,
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-test",
            "base_url": "https://api.openai.com/v1",
            "system_prompt": "你是测试助手。",
            "max_history": 2,
            "max_tokens_per_day": 100,
            "usage": {},
        }
        self.manager = AIChatManager(self._load_config, self._save_config)

    def _load_config(self):
        return self.config

    def _save_config(self, config):
        self.saved_configs.append(config.copy())

    def test_lru_evicts_oldest_user(self):
        self.manager.MAX_USERS = 2
        with self.manager._lock:
            self.manager._get_history("u1")
            self.manager._get_history("u2")
            self.manager._get_history("u3")
        self.assertNotIn("u1", self.manager._histories)
        self.assertIn("u2", self.manager._histories)
        self.assertIn("u3", self.manager._histories)

    def test_daily_limit_blocks_when_tokens_exhausted(self):
        with patch("ai_chat.datetime") as fake_datetime:
            fake_datetime.now.return_value.strftime.return_value = "2026-04-20"
            self.config["usage"] = {"2026-04-20": {"tokens": 100, "requests": 1}}
            reply = self.manager.chat("u1", "hello")
        self.assertIn("额度已用尽", reply)

    def test_chat_records_usage_and_trims_history(self):
        class _FakeResp:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "choices": [{"message": {"content": "reply"}}],
                    "usage": {"total_tokens": 12},
                }

        with patch("ai_chat.requests.post", return_value=_FakeResp()):
            self.assertEqual(self.manager.chat("u1", "hello"), "reply")
            self.assertEqual(self.manager.chat("u1", "world"), "reply")
            self.assertEqual(self.manager.chat("u1", "again"), "reply")

        history = self.manager._histories["u1"]
        self.assertEqual(len(history), 5)
        self.assertEqual(history[0]["content"], "reply")
        self.assertEqual(history[-1]["content"], "reply")
        self.assertTrue(self.saved_configs)


if __name__ == "__main__":
    unittest.main()
