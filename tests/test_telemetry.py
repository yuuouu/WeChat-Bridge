from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from telemetry import TelemetryClient, _bucket


class TelemetryClientTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_bucket_boundaries(self):
        bounds = ((0, "0"), (1, "1"), (2, "2"))
        self.assertEqual(_bucket(0, bounds, "3+"), "0")
        self.assertEqual(_bucket(2, bounds, "3+"), "2")
        self.assertEqual(_bucket(4, bounds, "3+"), "3+")

    def test_daily_ids_are_stable_but_rotate_by_day(self):
        client = TelemetryClient(str(self.data_dir), "https://example.com", "1.4.0")
        first = client._anonymous_id("day:2026-10-06")
        self.assertEqual(first, client._anonymous_id("day:2026-10-06"))
        self.assertNotEqual(first, client._anonymous_id("day:2026-10-07"))
        self.assertNotIn(client.state["install_id"], first)

    def test_report_sends_deduplicated_events_and_marks_success(self):
        (self.data_dir / ".install_pending").write_text("docker", encoding="utf-8")
        client = TelemetryClient(str(self.data_dir), "https://example.com", "1.4.0")
        payloads = []

        def fake_post(payload):
            payloads.append(payload)
            return True

        with patch.object(client, "_post", side_effect=fake_post):
            self.assertTrue(client.report({}, 2, 3, process_start=True))

        self.assertEqual(
            [payload["event"] for payload in payloads],
            ["heartbeat", "process_start", "first_seen", "install_success"],
        )
        self.assertFalse((self.data_dir / ".install_pending").exists())
        state = json.loads((self.data_dir / ".telemetry_state.json").read_text(encoding="utf-8"))
        self.assertTrue(state["first_seen_sent"])
        self.assertTrue(state["install_success_sent"])
        self.assertEqual(state["last_version"], "1.4.0")

    def test_existing_state_emits_upgrade_once(self):
        client = TelemetryClient(str(self.data_dir), "https://example.com", "1.4.0")
        client.state.update({"first_seen_sent": True, "last_version": "1.3.0"})
        payloads = []
        with patch.object(client, "_post", side_effect=lambda payload: payloads.append(payload) or True):
            client.report({}, 1, 0)
        self.assertEqual([payload["event"] for payload in payloads], ["heartbeat", "upgrade"])
        self.assertEqual(client.state["last_version"], "1.4.0")

    def test_process_start_failure_keeps_initial_report_retryable(self):
        client = TelemetryClient(str(self.data_dir), "https://example.com", "1.4.0")
        client.state.update({"first_seen_sent": True, "last_version": "1.4.0"})
        outcomes = iter((True, False))
        with patch.object(client, "_post", side_effect=lambda _payload: next(outcomes)):
            self.assertFalse(client.report({}, 1, 0, process_start=True))

    @patch("telemetry.datetime")
    def test_heartbeat_contains_rotating_anonymous_ids(self, mock_datetime):
        mock_datetime.now.return_value = datetime(2026, 10, 6, tzinfo=timezone.utc)
        client = TelemetryClient(str(self.data_dir), "https://example.com", "1.4.0")
        payloads = []
        client.state["first_seen_sent"] = True
        with patch.object(client, "_post", side_effect=lambda payload: payloads.append(payload) or True):
            client.report({"enabled": True, "provider": "custom-provider"}, 4, 2)
        heartbeat = payloads[0]
        self.assertEqual(heartbeat["day"], "2026-10-06")
        self.assertEqual(heartbeat["week"], "2026-W41")
        self.assertEqual(heartbeat["accounts_bucket"], "3+")
        self.assertEqual(heartbeat["plugins_bucket"], "2")
        self.assertEqual(heartbeat["ai_provider"], "other")


if __name__ == "__main__":
    unittest.main()
