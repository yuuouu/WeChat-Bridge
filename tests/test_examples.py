import os
import sys
import unittest
import urllib.error
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
        webhook_receiver.clear_weather_cache()

    def test_weather_reply_uses_qweather_payload(self):
        geocode = {
            "code": "200",
            "location": [
                {
                    "id": "101280601",
                    "name": "深圳",
                    "adm1": "广东省",
                    "country": "中国",
                    "lat": "22.5455",
                    "lon": "114.0683",
                }
            ],
        }
        now = {
            "code": "200",
            "now": {
                "obsTime": "2026-05-27T12:00+08:00",
                "temp": "28.5",
                "feelsLike": "31.0",
                "humidity": "72",
                "precip": "0.0",
                "icon": "101",
                "text": "多云",
                "windSpeed": "12.3",
            },
            "refer": {"sources": ["QWeather"]},
        }
        hourly = {
            "code": "200",
            "hourly": [
                {"fxTime": "2026-05-27T13:00+08:00", "precip": "0.0", "pop": "10"},
                {"fxTime": "2026-05-27T14:00+08:00", "precip": "0.0", "pop": "20"},
            ],
            "refer": {"sources": ["QWeather"]},
        }
        with (
            patch.object(webhook_receiver, "WEATHER_PROVIDER", "qweather"),
            patch.object(webhook_receiver, "_get_json", side_effect=[geocode, now, hourly]),
        ):
            reply = webhook_receiver.build_weather_reply("深圳")

        self.assertIn("深圳市天气", reply)
        self.assertIn("多云", reply)
        self.assertIn("28.5°C", reply)
        self.assertIn("**降雨**：无降雨", reply)
        self.assertIn("**穿衣建议**：短袖为主", reply)
        self.assertIn("**时间**：2026-05-27 12:00", reply)
        self.assertNotIn("更新时间", reply)
        self.assertNotIn("数据源", reply)

    def test_weather_snapshot_uses_qweather_payload(self):
        geocode = {
            "code": "200",
            "location": [
                {
                    "id": "101280601",
                    "name": "深圳",
                    "adm1": "广东省",
                    "country": "中国",
                    "lat": "22.5455",
                    "lon": "114.0683",
                }
            ],
        }
        now = {
            "code": "200",
            "now": {
                "obsTime": "2026-05-27T12:00+08:00",
                "temp": "28.5",
                "feelsLike": "31.0",
                "humidity": "72",
                "precip": "0.0",
                "icon": "101",
                "text": "多云",
                "windSpeed": "12.3",
            },
            "refer": {"sources": ["QWeather"]},
        }
        hourly = {
            "code": "200",
            "hourly": [
                {"fxTime": "2026-05-27T13:00+08:00", "precip": "0.0", "pop": "10"},
                {"fxTime": "2026-05-27T14:00+08:00", "precip": "0.2", "pop": "80"},
            ],
            "refer": {"sources": ["QWeather"]},
        }
        with (
            patch.object(webhook_receiver, "WEATHER_PROVIDER", "qweather"),
            patch.object(webhook_receiver, "_get_json", side_effect=[geocode, now, hourly]),
        ):
            snapshot = webhook_receiver.build_weather_snapshot("深圳")

        self.assertEqual(snapshot["location"]["label"], "深圳 · 广东省 · 中国")
        self.assertEqual(snapshot["location"]["title"], "深圳市")
        self.assertEqual(snapshot["location"]["id"], "101280601")
        self.assertEqual(snapshot["current"]["condition"], "多云")
        self.assertEqual(snapshot["current"]["temperature_2m"], 28.5)
        self.assertEqual(snapshot["display"]["rain"], "无降雨（预计 14:00 下雨）")
        self.assertTrue(snapshot["forecast"]["next_hours"][1]["has_rain"])

    def test_weather_reply_uses_local_alias_for_heyuan(self):
        now = {
            "code": "200",
            "now": {
                "obsTime": "2026-05-27T11:15+08:00",
                "temp": "24.5",
                "feelsLike": "29.6",
                "humidity": "94",
                "precip": "0.1",
                "icon": "305",
                "text": "小雨",
                "windSpeed": "4.1",
            },
            "refer": {"sources": ["QWeather"]},
        }
        hourly = {
            "code": "200",
            "hourly": [
                {"fxTime": "2026-05-27T12:00+08:00", "precip": "0.0", "pop": "10"},
                {"fxTime": "2026-05-27T13:00+08:00", "precip": "0.0", "pop": "10"},
            ],
            "refer": {"sources": ["QWeather"]},
        }
        minutely = {
            "code": "200",
            "minutely": [],
            "refer": {"sources": ["QWeather"]},
        }

        with (
            patch.object(webhook_receiver, "WEATHER_PROVIDER", "qweather"),
            patch.object(webhook_receiver, "_get_json", side_effect=[now, hourly, minutely]),
        ):
            reply = webhook_receiver.build_weather_reply("河源")

        self.assertIn("河源市天气", reply)
        self.assertNotIn("重庆", reply)
        self.assertIn("**时间**：2026-05-27 11:15", reply)
        self.assertNotIn("数据源", reply)

    def test_weather_reply_defaults_to_zijin(self):
        now = {
            "code": "200",
            "now": {
                "obsTime": "2026-05-27T11:15+08:00",
                "temp": "25",
                "feelsLike": "28",
                "humidity": "88",
                "precip": "0",
                "icon": "104",
                "text": "阴",
                "windSpeed": "6",
            },
            "refer": {"sources": ["QWeather"]},
        }
        hourly = {
            "code": "200",
            "hourly": [
                {"fxTime": "2026-05-27T12:00+08:00", "precip": "0.0", "pop": "10"},
                {"fxTime": "2026-05-27T13:00+08:00", "precip": "0.0", "pop": "10"},
            ],
            "refer": {"sources": ["QWeather"]},
        }
        minutely = {
            "code": "200",
            "minutely": [],
            "refer": {"sources": ["QWeather"]},
        }

        with (
            patch.object(webhook_receiver, "WEATHER_PROVIDER", "qweather"),
            patch.object(webhook_receiver, "_get_json", side_effect=[now, hourly, minutely]),
        ):
            reply = webhook_receiver.build_weather_reply("")

        self.assertIn("紫金县天气", reply)
        self.assertIn("阴", reply)
        self.assertIn("**降雨**：无降雨", reply)

    def test_weather_auto_provider_falls_back_to_open_meteo_without_credentials(self):
        geocode = {
            "results": [
                {
                    "id": 1795565,
                    "name": "深圳",
                    "admin1": "广东省",
                    "country": "中国",
                    "latitude": 22.54554,
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
            "current_units": {
                "temperature_2m": "°C",
                "apparent_temperature": "°C",
                "relative_humidity_2m": "%",
                "precipitation": "mm",
                "wind_speed_10m": "km/h",
            },
            "hourly": {
                "time": ["2026-05-27T13:00", "2026-05-27T14:00"],
                "precipitation": [0.0, 0.2],
                "precipitation_probability": [10, 80],
            },
        }

        with (
            patch.object(webhook_receiver, "WEATHER_PROVIDER", "auto"),
            patch.dict(os.environ, {"QWEATHER_API_KEY": "", "QWEATHER_JWT": ""}, clear=False),
            patch.object(webhook_receiver, "_get_json", side_effect=[geocode, forecast]) as get_json,
        ):
            snapshot = webhook_receiver.build_weather_snapshot("深圳")

        self.assertEqual(snapshot["source"]["name"], "Open-Meteo")
        self.assertEqual(snapshot["location"]["title"], "深圳市")
        self.assertEqual(snapshot["current"]["condition"], "局部多云")
        self.assertEqual(snapshot["display"]["rain"], "无降雨（预计 14:00 下雨）")
        self.assertEqual(2, get_json.call_count)

    def test_weather_auto_provider_uses_qweather_with_credentials(self):
        with (
            patch.object(webhook_receiver, "WEATHER_PROVIDER", "auto"),
            patch.dict(os.environ, {"QWEATHER_API_KEY": "demo-key", "QWEATHER_JWT": ""}, clear=False),
        ):
            self.assertEqual("qweather", webhook_receiver._weather_provider())

        with (
            patch.object(webhook_receiver, "WEATHER_PROVIDER", "auto"),
            patch.dict(os.environ, {"QWEATHER_API_KEY": "", "QWEATHER_JWT": ""}, clear=False),
        ):
            self.assertEqual("open-meteo", webhook_receiver._weather_provider())

    def test_weather_open_meteo_ambiguous_city_returns_candidates(self):
        geocode = {
            "results": [
                {
                    "id": 1,
                    "name": "朝阳",
                    "admin1": "北京市",
                    "country": "中国",
                    "latitude": 39.92,
                    "longitude": 116.43,
                },
                {
                    "id": 2,
                    "name": "朝阳",
                    "admin1": "辽宁省",
                    "country": "中国",
                    "latitude": 41.57,
                    "longitude": 120.45,
                },
            ],
        }

        with (
            patch.object(webhook_receiver, "WEATHER_PROVIDER", "open-meteo"),
            patch.object(webhook_receiver, "_get_json", return_value=geocode),
        ):
            reply = webhook_receiver.build_weather_reply("朝阳")

        self.assertIn("城市名不明确", reply)
        self.assertIn("朝阳 · 北京市 · 中国", reply)
        self.assertIn("朝阳 · 辽宁省 · 中国", reply)

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

    def test_weather_json_request_does_not_retry_auth_error(self):
        error = urllib.error.HTTPError(
            "https://example.test/weather",
            403,
            "Forbidden",
            {},
            None,
        )

        with (
            patch.object(webhook_receiver.urllib.request, "urlopen", side_effect=error) as urlopen,
            patch.object(webhook_receiver.time, "sleep") as sleep,
        ):
            with self.assertRaises(webhook_receiver.WeatherQueryError):
                webhook_receiver._get_json("https://example.test/weather")

        self.assertEqual(1, urlopen.call_count)
        sleep.assert_not_called()

    def test_weather_plugin_uses_chinese_command(self):
        plugin = webhook_receiver.ReceiverPlugin()

        commands = {item["command"] for item in plugin.get_command_specs()}

        self.assertEqual(["/天气", "/echo"], plugin.commands)
        self.assertIn("/天气", commands)
        self.assertNotIn("/weather", commands)

    def test_weather_query_latest_forces_refresh(self):
        self.assertEqual(
            {"city": "深圳", "force_refresh": True, "include_minutely": None},
            webhook_receiver.parse_weather_query("深圳 最新"),
        )

    def test_qweather_url_encodes_special_characters(self):
        url = webhook_receiver._qweather_url("/v7/minutely/5m", {"location": "114.06,22.54", "lang": "zh"})

        self.assertIn("location=114.06%2C22.54", url)
        self.assertIn("lang=zh", url)

    def test_weather_snapshot_uses_cache_and_force_refresh(self):
        now = {
            "code": "200",
            "now": {
                "obsTime": "2026-05-27T11:15+08:00",
                "temp": "25",
                "feelsLike": "28",
                "humidity": "88",
                "precip": "0",
                "icon": "104",
                "text": "阴",
                "windSpeed": "6",
            },
            "refer": {"sources": ["QWeather"]},
        }
        hourly = {
            "code": "200",
            "hourly": [
                {"fxTime": "2026-05-27T12:00+08:00", "precip": "0.0", "pop": "10"},
            ],
            "refer": {"sources": ["QWeather"]},
        }
        minutely = {"code": "200", "minutely": [], "refer": {"sources": ["QWeather"]}}

        with (
            patch.object(webhook_receiver, "WEATHER_PROVIDER", "qweather"),
            patch.object(webhook_receiver, "_get_json", side_effect=[now, hourly, minutely]) as get_json,
        ):
            first = webhook_receiver.build_weather_snapshot("紫金")
            second = webhook_receiver.build_weather_snapshot("紫金")

        self.assertFalse(first["cache"]["hit"])
        self.assertTrue(second["cache"]["hit"])
        self.assertEqual(3, get_json.call_count)

        with (
            patch.object(webhook_receiver, "WEATHER_PROVIDER", "qweather"),
            patch.object(webhook_receiver, "_get_json", side_effect=[now, hourly, minutely]) as get_json,
        ):
            refreshed = webhook_receiver.build_weather_snapshot("紫金", force_refresh=True)

        self.assertFalse(refreshed["cache"]["hit"])
        self.assertEqual(3, get_json.call_count)

    def test_weather_force_refresh_reuses_location_cache(self):
        geocode = {
            "code": "200",
            "location": [
                {
                    "id": "101280601",
                    "name": "深圳",
                    "adm1": "广东省",
                    "adm2": "深圳市",
                    "country": "中国",
                    "lat": "22.5455",
                    "lon": "114.0683",
                }
            ],
        }
        now = {
            "code": "200",
            "now": {
                "obsTime": "2026-05-27T12:00+08:00",
                "temp": "28",
                "feelsLike": "30",
                "humidity": "70",
                "precip": "0",
                "icon": "101",
                "text": "多云",
                "windSpeed": "10",
            },
            "refer": {"sources": ["QWeather"]},
        }
        hourly = {
            "code": "200",
            "hourly": [
                {"fxTime": "2026-05-27T13:00+08:00", "precip": "0", "pop": "10"},
                {"fxTime": "2026-05-27T14:00+08:00", "precip": "0", "pop": "10"},
            ],
            "refer": {"sources": ["QWeather"]},
        }

        with (
            patch.object(webhook_receiver, "WEATHER_PROVIDER", "qweather"),
            patch.object(webhook_receiver, "_get_json", side_effect=[geocode, now, hourly, now, hourly]) as get_json,
        ):
            webhook_receiver.build_weather_snapshot("深圳", force_refresh=True)
            webhook_receiver.build_weather_snapshot("深圳", force_refresh=True)

        self.assertEqual(5, get_json.call_count)

    def test_weather_ambiguous_city_returns_candidates(self):
        geocode = {
            "code": "200",
            "location": [
                {
                    "id": "1",
                    "name": "朝阳",
                    "adm1": "北京市",
                    "adm2": "北京市",
                    "country": "中国",
                    "lat": "39.92",
                    "lon": "116.43",
                },
                {
                    "id": "2",
                    "name": "朝阳",
                    "adm1": "辽宁省",
                    "adm2": "朝阳市",
                    "country": "中国",
                    "lat": "41.57",
                    "lon": "120.45",
                },
            ],
        }

        with (
            patch.object(webhook_receiver, "WEATHER_PROVIDER", "qweather"),
            patch.object(webhook_receiver, "_get_json", return_value=geocode),
        ):
            reply = webhook_receiver.build_weather_reply("朝阳")

        self.assertIn("城市名不明确", reply)
        self.assertIn("朝阳 · 北京市 · 中国", reply)
        self.assertIn("朝阳 · 辽宁省 · 中国", reply)

    def test_weather_minutely_skipped_for_clear_remote_city(self):
        geocode = {
            "code": "200",
            "location": [
                {
                    "id": "101280601",
                    "name": "深圳",
                    "adm1": "广东省",
                    "adm2": "深圳市",
                    "country": "中国",
                    "lat": "22.5455",
                    "lon": "114.0683",
                }
            ],
        }
        now = {
            "code": "200",
            "now": {
                "obsTime": "2026-05-27T12:00+08:00",
                "temp": "28",
                "feelsLike": "30",
                "humidity": "70",
                "precip": "0",
                "icon": "101",
                "text": "多云",
                "windSpeed": "10",
            },
            "refer": {"sources": ["QWeather"]},
        }
        hourly = {
            "code": "200",
            "hourly": [
                {"fxTime": "2026-05-27T13:00+08:00", "precip": "0", "pop": "10"},
                {"fxTime": "2026-05-27T14:00+08:00", "precip": "0", "pop": "10"},
            ],
            "refer": {"sources": ["QWeather"]},
        }

        with (
            patch.object(webhook_receiver, "WEATHER_PROVIDER", "qweather"),
            patch.object(webhook_receiver, "_get_json", side_effect=[geocode, now, hourly]) as get_json,
        ):
            webhook_receiver.build_weather_snapshot("深圳")

        self.assertEqual(3, get_json.call_count)


if __name__ == "__main__":
    unittest.main()
