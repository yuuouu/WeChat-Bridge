#!/usr/bin/env python3
"""
无状态 Webhook 示例 — 基础命令响应（插件化版本）。

作为 webhook_manager 插件运行（推荐）：
    自动被 discover_and_register_plugins 发现并加载。

配置：
    WEATHER_TIMEOUT      天气接口超时秒数，默认 8
    WEATHER_RETRIES      天气接口失败重试次数，默认 2
    WEATHER_AI_ADVICE    穿衣建议是否尝试调用 AI，默认 auto
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR.parent / "app"))

from plugin_base import Plugin  # noqa: E402

logger = logging.getLogger(__name__)

WEATHER_TIMEOUT = float(os.environ.get("WEATHER_TIMEOUT", "8"))
WEATHER_RETRIES = int(os.environ.get("WEATHER_RETRIES", "2"))
WEATHER_RAIN_LOOKAHEAD_HOURS = int(os.environ.get("WEATHER_RAIN_LOOKAHEAD_HOURS", "12"))
WEATHER_AI_ADVICE = os.environ.get("WEATHER_AI_ADVICE", "auto").strip().lower()
GEOCODING_API = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_API = "https://api.open-meteo.com/v1/forecast"
DEFAULT_WEATHER_CITY = "紫金"
LOCAL_WEATHER_LOCATIONS = {
    "河源": {
        "name": "河源市",
        "label": "河源市 · 广东省",
        "latitude": 23.73333,
        "longitude": 114.68333,
    },
    "河源市": {
        "name": "河源市",
        "label": "河源市 · 广东省",
        "latitude": 23.73333,
        "longitude": 114.68333,
    },
    "紫金": {
        "name": "紫金县",
        "label": "紫金县 · 河源市 · 广东省",
        "latitude": 23.6306226,
        "longitude": 115.1845453,
    },
    "紫金县": {
        "name": "紫金县",
        "label": "紫金县 · 河源市 · 广东省",
        "latitude": 23.6306226,
        "longitude": 115.1845453,
    },
    "河源紫金": {
        "name": "紫金县",
        "label": "紫金县 · 河源市 · 广东省",
        "latitude": 23.6306226,
        "longitude": 115.1845453,
    },
    "河源市紫金县": {
        "name": "紫金县",
        "label": "紫金县 · 河源市 · 广东省",
        "latitude": 23.6306226,
        "longitude": 115.1845453,
    },
    "广东紫金": {
        "name": "紫金县",
        "label": "紫金县 · 河源市 · 广东省",
        "latitude": 23.6306226,
        "longitude": 115.1845453,
    },
    "广东省紫金县": {
        "name": "紫金县",
        "label": "紫金县 · 河源市 · 广东省",
        "latitude": 23.6306226,
        "longitude": 115.1845453,
    },
}
WEATHER_CODE_MAP = {
    0: "晴",
    1: "大部晴朗",
    2: "局部多云",
    3: "阴",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "中等毛毛雨",
    55: "大毛毛雨",
    56: "冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "小阵雨",
    81: "中阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "强阵雪",
    95: "雷暴",
    96: "雷暴伴小冰雹",
    99: "雷暴伴强冰雹",
}


class ReceiverPlugin(Plugin):
    """基础命令响应示例插件。"""

    name = "receiver"
    description = "基础命令响应示例 (/weather, /echo)"
    commands = ["/weather", "/echo"]

    def get_command_specs(self) -> list[dict]:
        return [
            {"command": "/weather", "description": "查询城市实时天气，例如 /weather 深圳"},
            {"command": "/echo", "description": "回显发送的参数"},
        ]

    def handle(self, payload: dict) -> None:
        from_user = payload.get("from_user", "")
        command = payload.get("command", "")
        args = payload.get("args", "")

        if not from_user:
            return

        if command == "/weather":
            city = args or DEFAULT_WEATHER_CITY
            reply = build_weather_reply(city)
        elif command == "/echo":
            reply = f"Echo from webhook:\n{args or '(empty)'}"
        else:
            return

        self.send_reply(from_user, reply)


PLUGIN_CLASS = ReceiverPlugin


def build_weather_reply(city: str) -> str:
    """查询 Open-Meteo 免费 API 并格式化为微信友好文本。"""
    city = city.strip() or DEFAULT_WEATHER_CITY
    try:
        snapshot = build_weather_snapshot(city)
    except Exception as exc:
        logger.warning("天气查询失败 [%s]: %s", city, exc)
        return f"## 🌦️ 天气查询失败\n\n- **城市**：{city}\n- **原因**：暂时无法获取天气数据，请稍后重试。"
    return format_weather_reply(snapshot)


def build_weather_snapshot(city: str) -> dict:
    """返回结构化天气快照，供命令和通知中台共同使用。"""
    city = city.strip() or DEFAULT_WEATHER_CITY
    location = _resolve_city(city)
    weather = _fetch_current_weather(location["latitude"], location["longitude"])

    current = weather.get("current", {})
    units = weather.get("current_units", {})
    temp = _fmt_value(current.get("temperature_2m"), units.get("temperature_2m", "°C"))
    feels = _fmt_value(current.get("apparent_temperature"), units.get("apparent_temperature", "°C"))
    humidity = _fmt_value(current.get("relative_humidity_2m"), units.get("relative_humidity_2m", "%"))
    rain = _format_rain(current.get("precipitation"), units.get("precipitation", "mm"), weather, current.get("time"))
    wind = _fmt_value(current.get("wind_speed_10m"), units.get("wind_speed_10m", "km/h"))
    condition = WEATHER_CODE_MAP.get(current.get("weather_code"), f"天气代码 {current.get('weather_code')}")
    display_name = _location_label(location)
    advice = _build_clothing_advice(display_name, condition, current, units, rain)

    return {
        "query": city,
        "location": {
            "name": location.get("name") or "",
            "label": display_name,
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
        },
        "current": {
            "condition": condition,
            "weather_code": current.get("weather_code"),
            "temperature_2m": _to_number(current.get("temperature_2m")),
            "apparent_temperature": _to_number(current.get("apparent_temperature")),
            "relative_humidity_2m": _to_number(current.get("relative_humidity_2m")),
            "precipitation": _to_number(current.get("precipitation")),
            "wind_speed_10m": _to_number(current.get("wind_speed_10m")),
            "time": current.get("time"),
        },
        "current_units": units,
        "display": {
            "temperature": temp,
            "apparent_temperature": feels,
            "humidity": humidity,
            "rain": rain,
            "wind": wind,
            "time": _format_weather_time(current.get("time")),
        },
        "forecast": {
            "rain_lookahead_hours": WEATHER_RAIN_LOOKAHEAD_HOURS,
            "next_hours": _build_hourly_forecast(weather, current.get("time")),
        },
        "advice": advice,
        "raw_updated_at": int(time.time()),
    }


def format_weather_reply(snapshot: dict) -> str:
    """将结构化天气快照格式化为微信命令回复。"""
    current = snapshot.get("current") or {}
    display = snapshot.get("display") or {}
    location = snapshot.get("location") or {}
    display_name = location.get("label") or snapshot.get("query") or DEFAULT_WEATHER_CITY

    return "\n".join(
        [
            f"## 🌦️ {display_name}天气",
            "",
            f"- **天气**：{current.get('condition') or '-'}",
            f"- **温度**：{display.get('temperature') or '-'}（体感 {display.get('apparent_temperature') or '-'}）",
            f"- **湿度**：{display.get('humidity') or '-'}",
            f"- **降雨**：{display.get('rain') or '-'}",
            f"- **风速**：{display.get('wind') or '-'}",
            f"- **穿衣建议**：{snapshot.get('advice') or '-'}",
            f"- **时间**：{display.get('time') or '-'}",
        ]
    )


def _resolve_city(city: str) -> dict:
    key = _normalize_city_key(city)
    if key in LOCAL_WEATHER_LOCATIONS:
        return LOCAL_WEATHER_LOCATIONS[key]

    params = urllib.parse.urlencode({"name": city, "count": 5, "language": "zh", "format": "json"})
    data = _get_json(f"{GEOCODING_API}?{params}")
    results = data.get("results") or []
    if not results:
        raise ValueError("城市未匹配")
    return results[0]


def _fetch_current_weather(latitude: float, longitude: float) -> dict:
    params = urllib.parse.urlencode(
        {
            "latitude": latitude,
            "longitude": longitude,
            "current": ",".join(
                [
                    "temperature_2m",
                    "relative_humidity_2m",
                    "apparent_temperature",
                    "precipitation",
                    "weather_code",
                    "wind_speed_10m",
                ]
            ),
            "hourly": "precipitation,precipitation_probability",
            "forecast_days": 1,
            "timezone": "auto",
        }
    )
    return _get_json(f"{FORECAST_API}?{params}")


def _get_json(url: str) -> dict:
    last_exc: Exception | None = None
    for attempt in range(WEATHER_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "WeChat-Bridge/1.2"})
            with urllib.request.urlopen(req, timeout=WEATHER_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            last_exc = exc
            if attempt >= WEATHER_RETRIES:
                break
            logger.info("天气接口请求失败，准备重试 [%s/%s]: %s", attempt + 1, WEATHER_RETRIES, exc)
            time.sleep(0.5 * (attempt + 1))
    raise last_exc or RuntimeError("天气接口请求失败")


def _fmt_value(value, unit: str) -> str:
    if value is None:
        return "-"
    return f"{value}{unit}"


def _format_rain(value, unit: str, weather: dict, current_time: str | None) -> str:
    amount = _to_float(value)
    if amount <= 0:
        next_rain_at = _find_rain_transition(weather, current_time, want_rain=True)
        if next_rain_at:
            return f"无降雨（预计 {_format_transition_time(next_rain_at, current_time)} 下雨）"
        return "无降雨"

    stop_at = _find_rain_transition(weather, current_time, want_rain=False)
    rain = _fmt_value(value, unit)
    if stop_at:
        return f"{rain}（预计 {_format_transition_time(stop_at, current_time)} 停雨）"
    return rain


def _find_rain_transition(weather: dict, current_time: str | None, *, want_rain: bool) -> datetime | None:
    current_dt = _parse_weather_time(current_time)
    if current_dt is None:
        return None

    deadline = current_dt + timedelta(hours=WEATHER_RAIN_LOOKAHEAD_HOURS)
    hourly = weather.get("hourly") or {}
    times = hourly.get("time") or []
    precipitation = hourly.get("precipitation") or []
    probability = hourly.get("precipitation_probability") or []

    for idx, raw_time in enumerate(times):
        dt = _parse_weather_time(raw_time)
        if dt is None or dt <= current_dt or dt > deadline:
            continue
        amount = _to_float(precipitation[idx] if idx < len(precipitation) else 0)
        prob = _to_float(probability[idx] if idx < len(probability) else 0)
        has_rain = amount > 0 or prob >= 60
        if has_rain is want_rain:
            return dt
    return None


def _build_hourly_forecast(weather: dict, current_time: str | None) -> list[dict]:
    current_dt = _parse_weather_time(current_time)
    if current_dt is None:
        return []

    deadline = current_dt + timedelta(hours=WEATHER_RAIN_LOOKAHEAD_HOURS)
    hourly = weather.get("hourly") or {}
    times = hourly.get("time") or []
    precipitation = hourly.get("precipitation") or []
    probability = hourly.get("precipitation_probability") or []
    rows = []

    for idx, raw_time in enumerate(times):
        dt = _parse_weather_time(raw_time)
        if dt is None or dt <= current_dt or dt > deadline:
            continue
        amount = _to_float(precipitation[idx] if idx < len(precipitation) else 0)
        prob = _to_float(probability[idx] if idx < len(probability) else 0)
        rows.append(
            {
                "time": raw_time,
                "label": _format_transition_time(dt, current_time),
                "precipitation": amount,
                "precipitation_probability": prob,
                "has_rain": amount > 0 or prob >= 60,
            }
        )
    return rows


def _build_clothing_advice(display_name: str, condition: str, current: dict, units: dict, rain: str) -> str:
    local_advice = _build_local_clothing_advice(current, rain)
    ai_advice = _build_ai_clothing_advice(display_name, condition, current, units, rain)
    return ai_advice or local_advice


def _build_ai_clothing_advice(display_name: str, condition: str, current: dict, units: dict, rain: str) -> str:
    if WEATHER_AI_ADVICE in ("0", "false", "off", "no"):
        return ""
    try:
        import config as cfg
        from ai_chat import AIChatManager

        ai_config = cfg.load_config()
        if not ai_config.get("enabled") or not ai_config.get("api_key"):
            return ""

        prompt = "\n".join(
            [
                f"地点：{display_name}",
                f"天气：{condition}",
                f"温度：{current.get('temperature_2m')}{units.get('temperature_2m', '°C')}",
                f"体感：{current.get('apparent_temperature')}{units.get('apparent_temperature', '°C')}",
                f"湿度：{current.get('relative_humidity_2m')}{units.get('relative_humidity_2m', '%')}",
                f"降雨：{rain}",
                f"风速：{current.get('wind_speed_10m')}{units.get('wind_speed_10m', 'km/h')}",
                "请给出一句中文穿衣和出门建议，25字以内，不要寒暄，不要 Markdown。",
            ]
        )
        manager = AIChatManager(cfg.load_config, cfg.save_config)
        return _clean_advice(manager.one_shot(prompt, system_prompt="你是简洁的本地天气穿衣建议助手。"))
    except Exception as exc:
        logger.info("AI 穿衣建议不可用，使用本地规则: %s", exc)
        return ""


def _build_local_clothing_advice(current: dict, rain: str) -> str:
    temp = _to_float(current.get("temperature_2m"))
    feels = _to_float(current.get("apparent_temperature"))
    wind = _to_float(current.get("wind_speed_10m"))
    effective_temp = feels or temp
    rainy = not rain.startswith("无降雨")

    if effective_temp >= 32:
        advice = "闷热，短袖透气衣物，注意防晒补水"
    elif effective_temp >= 26:
        advice = "短袖为主，怕冷可带薄外套"
    elif effective_temp >= 20:
        advice = "薄长袖或短袖加薄外套都合适"
    elif effective_temp >= 14:
        advice = "建议长袖加外套"
    else:
        advice = "偏冷，穿外套并注意保暖"

    if rainy:
        advice += "，带伞"
    elif wind >= 20:
        advice += "，风大可带薄外套"
    return advice


def _clean_advice(value: str) -> str:
    text = " ".join(str(value or "").replace("\n", " ").split())
    text = text.strip(" -_*`#。")
    if len(text) > 40:
        text = text[:40].rstrip("，、；。 ") + "..."
    return text


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_number(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_weather_time(value: str | None) -> str:
    if not value:
        return "-"
    return str(value).replace("T", " ")


def _format_transition_time(value: datetime, current_time: str | None) -> str:
    current_dt = _parse_weather_time(current_time)
    if current_dt and value.date() != current_dt.date():
        return f"次日 {value:%H:%M}"
    return f"{value:%H:%M}"


def _parse_weather_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value)
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _normalize_city_key(city: str) -> str:
    key = "".join(city.strip().split())
    return key.removesuffix("天气")


def _location_label(location: dict) -> str:
    if location.get("label"):
        return str(location["label"])
    parts = [location.get("name"), location.get("admin1"), location.get("country")]
    return " · ".join(str(part) for part in parts if part)
