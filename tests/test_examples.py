import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "app"
for path in (ROOT, APP_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from examples import webhook_receiver


class WeatherExampleTests(unittest.TestCase):
    def setUp(self):
        self._ai_patcher = patch.object(webhook_receiver, "_build_ai_clothing_advice", return_value="")
        self._ai_patcher.start()
        self.addCleanup(self._ai_patcher.stop)

    def test_weather_reply_uses_open_meteo_payload(self):
        geocode = {
            "results": [
                {
                    "name": "深圳",
                    "admin1": "广东省",
                    "country": "中国",
                    "latitude": 22.5455,
                    "longitude": 114.0683,
                }
            ]
        }
        forecast = {
            "current": {
                "time": "2026-05-27T12:00",
                "temperature_2m": 28.5,
                "apparent_temperature": 31.0,
                "relative_humidity_2m": 72,
                "precipitation": 0.0,
                "weather_code": 2,
                "wind_speed_10m": 12.3,
            },
            "hourly": {
                "time": ["2026-05-27T13:00", "2026-05-27T14:00"],
                "precipitation": [0.0, 0.0],
                "precipitation_probability": [10, 20],
            },
            "current_units": {
                "temperature_2m": "°C",
                "apparent_temperature": "°C",
                "relative_humidity_2m": "%",
                "precipitation": "mm",
                "wind_speed_10m": "km/h",
            },
        }

        with patch.object(webhook_receiver, "_get_json", side_effect=[geocode, forecast]):
            reply = webhook_receiver.build_weather_reply("深圳")

        self.assertIn("深圳 · 广东省 · 中国天气", reply)
        self.assertIn("局部多云", reply)
        self.assertIn("28.5°C", reply)
        self.assertIn("**降雨**：无降雨", reply)
        self.assertIn("**穿衣建议**：短袖为主", reply)
        self.assertIn("**时间**：2026-05-27 12:00", reply)
        self.assertNotIn("更新时间", reply)
        self.assertNotIn("数据源", reply)

    def test_weather_snapshot_uses_open_meteo_payload(self):
        geocode = {
            "results": [
                {
                    "name": "深圳",
                    "admin1": "广东省",
                    "country": "中国",
                    "latitude": 22.5455,
                    "longitude": 114.0683,
                }
            ]
        }
        forecast = {
            "current": {
                "time": "2026-05-27T12:00",
                "temperature_2m": 28.5,
                "apparent_temperature": 31.0,
                "relative_humidity_2m": 72,
                "precipitation": 0.0,
                "weather_code": 2,
                "wind_speed_10m": 12.3,
            },
            "hourly": {
                "time": ["2026-05-27T13:00", "2026-05-27T14:00"],
                "precipitation": [0.0, 0.2],
                "precipitation_probability": [10, 80],
            },
            "current_units": {
                "temperature_2m": "°C",
                "apparent_temperature": "°C",
                "relative_humidity_2m": "%",
                "precipitation": "mm",
                "wind_speed_10m": "km/h",
            },
        }

        with patch.object(webhook_receiver, "_get_json", side_effect=[geocode, forecast]):
            snapshot = webhook_receiver.build_weather_snapshot("深圳")

        self.assertEqual(snapshot["location"]["label"], "深圳 · 广东省 · 中国")
        self.assertEqual(snapshot["current"]["condition"], "局部多云")
        self.assertEqual(snapshot["current"]["temperature_2m"], 28.5)
        self.assertEqual(snapshot["display"]["rain"], "无降雨（预计 14:00 下雨）")
        self.assertTrue(snapshot["forecast"]["next_hours"][1]["has_rain"])

    def test_weather_reply_uses_local_alias_for_heyuan(self):
        forecast = {
            "current": {
                "time": "2026-05-27T11:15",
                "temperature_2m": 24.5,
                "apparent_temperature": 29.6,
                "relative_humidity_2m": 94,
                "precipitation": 0.1,
                "weather_code": 51,
                "wind_speed_10m": 4.1,
            },
            "hourly": {
                "time": ["2026-05-27T12:00", "2026-05-27T13:00"],
                "precipitation": [0.0, 0.0],
                "precipitation_probability": [10, 10],
            },
            "current_units": {
                "temperature_2m": "°C",
                "apparent_temperature": "°C",
                "relative_humidity_2m": "%",
                "precipitation": "mm",
                "wind_speed_10m": "km/h",
            },
        }

        with patch.object(webhook_receiver, "_get_json", return_value=forecast):
            reply = webhook_receiver.build_weather_reply("河源")

        self.assertIn("河源市 · 广东省天气", reply)
        self.assertNotIn("重庆", reply)
        self.assertIn("**时间**：2026-05-27 11:15", reply)
        self.assertNotIn("数据源", reply)

    def test_weather_reply_defaults_to_zijin(self):
        forecast = {
            "current": {
                "time": "2026-05-27T11:15",
                "temperature_2m": 25,
                "apparent_temperature": 28,
                "relative_humidity_2m": 88,
                "precipitation": 0,
                "weather_code": 3,
                "wind_speed_10m": 6,
            },
            "hourly": {
                "time": ["2026-05-27T12:00", "2026-05-27T13:00"],
                "precipitation": [0.0, 0.0],
                "precipitation_probability": [10, 10],
            },
            "current_units": {
                "temperature_2m": "°C",
                "apparent_temperature": "°C",
                "relative_humidity_2m": "%",
                "precipitation": "mm",
                "wind_speed_10m": "km/h",
            },
        }

        with patch.object(webhook_receiver, "_get_json", return_value=forecast):
            reply = webhook_receiver.build_weather_reply("")

        self.assertIn("紫金县 · 河源市 · 广东省天气", reply)
        self.assertIn("阴", reply)
        self.assertIn("**降雨**：无降雨", reply)

    def test_weather_rain_mentions_next_rain_time(self):
        weather = {
            "current": {
                "time": "2026-05-27T11:30",
                "precipitation": 0.0,
            },
            "hourly": {
                "time": ["2026-05-27T12:00", "2026-05-27T13:00", "2026-05-27T14:00"],
                "precipitation": [0.0, 0.2, 0.0],
                "precipitation_probability": [20, 80, 30],
            },
        }

        rain = webhook_receiver._format_rain(0.0, "mm", weather, "2026-05-27T11:30")

        self.assertEqual("无降雨（预计 13:00 下雨）", rain)

    def test_weather_rain_mentions_stop_time(self):
        weather = {
            "current": {
                "time": "2026-05-27T11:30",
                "precipitation": 0.4,
            },
            "hourly": {
                "time": ["2026-05-27T12:00", "2026-05-27T13:00", "2026-05-27T14:00"],
                "precipitation": [0.3, 0.0, 0.0],
                "precipitation_probability": [80, 20, 10],
            },
        }

        rain = webhook_receiver._format_rain(0.4, "mm", weather, "2026-05-27T11:30")

        self.assertEqual("0.4mm（预计 13:00 停雨）", rain)

    def test_weather_json_request_retries_transient_failure(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return b'{"ok": true}'

        with (
            patch.object(
                webhook_receiver.urllib.request, "urlopen", side_effect=[OSError("reset"), FakeResponse()]
            ) as urlopen,
            patch.object(webhook_receiver.time, "sleep") as sleep,
        ):
            data = webhook_receiver._get_json("https://example.test/weather")

        self.assertEqual({"ok": True}, data)
        self.assertEqual(2, urlopen.call_count)
        sleep.assert_called_once()


if __name__ == "__main__":
    unittest.main()
