#!/usr/bin/env python3
"""
无状态 Webhook 示例 — 基础命令响应（插件化版本）。

作为 webhook_manager 插件运行（推荐）：
    自动被 discover_and_register_plugins 发现并加载。

配置：
    WEATHER_TIMEOUT      天气接口超时秒数，默认 8
    WEATHER_RETRIES      天气接口失败重试次数，默认 2
    WEATHER_PROVIDER     天气数据源，默认 auto；无 QWeather 凭证时走 Open-Meteo
    WEATHER_CACHE_TTL_SECONDS  天气快照缓存秒数，默认 300
    QWEATHER_LOCATION_CACHE_TTL_SECONDS  城市 LocationID 解析缓存秒数，默认 86400
    WEATHER_AI_ADVICE    穿衣建议是否尝试调用 AI，默认 auto
    QWEATHER_API_KEY     和风天气 API KEY
    QWEATHER_JWT         和风天气 JWT，设置后优先于 QWEATHER_API_KEY
    QWEATHER_API_HOST    和风天气专属 API Host，默认使用旧共享域名 devapi.qweather.com
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import random
import sys
import time
import urllib.error
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
WEATHER_CACHE_TTL_SECONDS = int(os.environ.get("WEATHER_CACHE_TTL_SECONDS", "300"))
WEATHER_RAIN_LOOKAHEAD_HOURS = int(os.environ.get("WEATHER_RAIN_LOOKAHEAD_HOURS", "12"))
WEATHER_RETRY_BASE_DELAY_SECONDS = float(os.environ.get("WEATHER_RETRY_BASE_DELAY_SECONDS", "1"))
QWEATHER_LOCATION_CACHE_TTL_SECONDS = int(os.environ.get("QWEATHER_LOCATION_CACHE_TTL_SECONDS", "86400"))
WEATHER_PROVIDER = os.environ.get("WEATHER_PROVIDER", "auto").strip().lower() or "auto"
WEATHER_AI_ADVICE = os.environ.get("WEATHER_AI_ADVICE", "auto").strip().lower()
QWEATHER_DEFAULT_API_HOST = "devapi.qweather.com"
QWEATHER_DEFAULT_GEO_API_HOST = "geoapi.qweather.com"
OPEN_METEO_GEOCODING_API = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_FORECAST_API = "https://api.open-meteo.com/v1/forecast"
DEFAULT_WEATHER_CITY = "紫金"
WEATHER_REFRESH_KEYWORDS = {"最新", "刷新", "强制刷新", "refresh", "force", "fresh"}
WEATHER_MINUTELY_KEYWORDS = {"降雨", "下雨", "分钟", "分钟降水", "雨量"}
WEATHER_REGION_SUFFIXES = ("省", "市", "县", "区", "旗", "州", "盟", "地区", "自治县", "自治州", "特别行政区")
QWEATHER_RETRYABLE_HTTP_STATUS = {408, 429, 500, 502, 503, 504}
_WEATHER_CACHE: dict[str, dict] = {}
_LOCATION_CACHE: dict[str, dict] = {}
LOCAL_WEATHER_LOCATIONS = {
    "河源": {
        "id": "101281201",
        "name": "河源市",
        "label": "河源市 · 广东省",
        "latitude": 23.73333,
        "longitude": 114.68333,
    },
    "河源市": {
        "id": "101281201",
        "name": "河源市",
        "label": "河源市 · 广东省",
        "latitude": 23.73333,
        "longitude": 114.68333,
    },
    "紫金": {
        "id": "101281202",
        "name": "紫金县",
        "label": "紫金县 · 河源市 · 广东省",
        "latitude": 23.6306226,
        "longitude": 115.1845453,
    },
    "紫金县": {
        "id": "101281202",
        "name": "紫金县",
        "label": "紫金县 · 河源市 · 广东省",
        "latitude": 23.6306226,
        "longitude": 115.1845453,
    },
    "河源紫金": {
        "id": "101281202",
        "name": "紫金县",
        "label": "紫金县 · 河源市 · 广东省",
        "latitude": 23.6306226,
        "longitude": 115.1845453,
    },
    "河源市紫金县": {
        "id": "101281202",
        "name": "紫金县",
        "label": "紫金县 · 河源市 · 广东省",
        "latitude": 23.6306226,
        "longitude": 115.1845453,
    },
    "广东紫金": {
        "id": "101281202",
        "name": "紫金县",
        "label": "紫金县 · 河源市 · 广东省",
        "latitude": 23.6306226,
        "longitude": 115.1845453,
    },
    "广东省紫金县": {
        "id": "101281202",
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
    description = "基础命令响应示例 (/天气, /echo)"
    commands = ["/天气", "/echo"]

    def get_command_specs(self) -> list[dict]:
        return [
            {"command": "/天气", "description": "查询城市实时天气，例如 /天气 深圳"},
            {"command": "/echo", "description": "回显发送的参数"},
        ]

    def handle(self, payload: dict) -> None:
        from_user = payload.get("from_user", "")
        command = payload.get("command", "")
        args = payload.get("args", "")

        if not from_user:
            return

        if command == "/天气":
            query = parse_weather_query(args)
            reply = build_weather_reply(query["city"], force_refresh=query["force_refresh"], include_minutely=query["include_minutely"])
        elif command == "/echo":
            reply = f"Echo from webhook:\n{args or '(empty)'}"
        else:
            return

        self.send_reply(from_user, reply)


PLUGIN_CLASS = ReceiverPlugin


class WeatherQueryError(RuntimeError):
    """天气查询错误，携带可直接展示给用户的提示。"""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.user_message = message
        self.status_code = status_code


class AmbiguousCityError(WeatherQueryError):
    """城市名匹配到多个候选。"""

    def __init__(self, city: str, candidates: list[dict]):
        super().__init__("城市名不明确，请补充省市县信息")
        self.city = city
        self.candidates = candidates


def parse_weather_query(args: str) -> dict:
    """解析 `/天气` 参数，支持 `最新` 强制刷新和 `降雨` 强制分钟降水。"""
    tokens = [token.strip() for token in str(args or "").split() if token.strip()]
    force_refresh = False
    include_minutely: bool | None = None
    city_tokens = []
    for token in tokens:
        lowered = token.lower()
        if token in WEATHER_REFRESH_KEYWORDS or lowered in WEATHER_REFRESH_KEYWORDS:
            force_refresh = True
            continue
        if token in WEATHER_MINUTELY_KEYWORDS or lowered in WEATHER_MINUTELY_KEYWORDS:
            include_minutely = True
            continue
        city_tokens.append(token)
    city = "".join(city_tokens).strip() or DEFAULT_WEATHER_CITY
    return {"city": city, "force_refresh": force_refresh, "include_minutely": include_minutely}


def build_weather_reply(city: str, *, force_refresh: bool = False, include_minutely: bool | None = None) -> str:
    """查询天气 API 并格式化为微信友好文本。"""
    city = city.strip() or DEFAULT_WEATHER_CITY
    try:
        snapshot = build_weather_snapshot(city, force_refresh=force_refresh, include_minutely=include_minutely)
    except AmbiguousCityError as exc:
        return _format_ambiguous_city_reply(exc)
    except Exception as exc:
        logger.warning("天气查询失败 [%s]: %s", city, exc)
        reason = _weather_error_message(exc)
        return f"## 🌦️ 天气查询失败\n\n- **城市**：{city}\n- **原因**：{reason}"
    return format_weather_reply(snapshot)


def build_weather_snapshot(city: str, *, force_refresh: bool = False, include_minutely: bool | None = None) -> dict:
    """返回结构化天气快照，供命令和通知中台共同使用。"""
    city = city.strip() or DEFAULT_WEATHER_CITY
    provider = _weather_provider()
    cache_key = _weather_cache_key(city, include_minutely=include_minutely, provider=provider)
    if not force_refresh:
        cached = _get_cached_weather(cache_key)
        if cached:
            cached["cache"] = {**(cached.get("cache") or {}), "hit": True}
            return cached

    location = _resolve_city(city, provider=provider)
    weather = _fetch_current_weather(location, include_minutely=include_minutely, query_city=city, provider=provider)

    current = weather.get("current", {})
    units = weather.get("current_units", {})
    temp = _fmt_value(current.get("temperature_2m"), units.get("temperature_2m", "°C"))
    feels = _fmt_value(current.get("apparent_temperature"), units.get("apparent_temperature", "°C"))
    humidity = _fmt_value(current.get("relative_humidity_2m"), units.get("relative_humidity_2m", "%"))
    rain = _format_rain(current.get("precipitation"), units.get("precipitation", "mm"), weather, current.get("time"))
    wind = _fmt_value(current.get("wind_speed_10m"), units.get("wind_speed_10m", "km/h"))
    condition = current.get("condition") or WEATHER_CODE_MAP.get(
        current.get("weather_code"), f"天气代码 {current.get('weather_code')}"
    )
    display_name = _location_label(location)
    title_name = _weather_title_name(location)
    advice = _build_clothing_advice(display_name, condition, current, units, rain)

    snapshot = {
        "query": city,
        "location": {
            "name": location.get("name") or "",
            "label": display_name,
            "title": title_name,
            "id": location.get("id"),
            "admin1": location.get("admin1") or location.get("adm1"),
            "admin2": location.get("admin2") or location.get("adm2"),
            "country": location.get("country"),
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
        "source": weather.get("source") or {"name": provider},
        "cache": {
            "hit": False,
            "ttl_seconds": WEATHER_CACHE_TTL_SECONDS,
            "cached_at": int(time.time()),
        },
        "raw_updated_at": int(time.time()),
    }
    _set_cached_weather(cache_key, snapshot)
    return snapshot


def format_weather_reply(snapshot: dict) -> str:
    """将结构化天气快照格式化为微信命令回复。"""
    current = snapshot.get("current") or {}
    display = snapshot.get("display") or {}
    location = snapshot.get("location") or {}
    title_name = location.get("title") or _weather_title_name(location) or snapshot.get("query") or DEFAULT_WEATHER_CITY

    return "\n".join(
        [
            f"## 🌦️ {title_name}天气",
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


def _weather_provider() -> str:
    provider = WEATHER_PROVIDER
    if provider in {"qweather", "q-weather", "heweather"}:
        return "qweather"
    if provider in {"openmeteo", "open-meteo", "open_meteo"}:
        return "open-meteo"
    if provider != "auto":
        logger.warning("未知天气数据源 %s，回退到 auto", provider)
    return "qweather" if _has_qweather_credentials() else "open-meteo"


def _has_qweather_credentials() -> bool:
    return bool(os.environ.get("QWEATHER_JWT", "").strip() or os.environ.get("QWEATHER_API_KEY", "").strip())


def _resolve_city(city: str, *, provider: str | None = None) -> dict:
    key = _normalize_city_key(city)
    if key in LOCAL_WEATHER_LOCATIONS:
        return LOCAL_WEATHER_LOCATIONS[key]
    provider = provider or _weather_provider()
    if provider == "open-meteo":
        return _resolve_city_open_meteo(city)

    cache_key = _location_cache_key(city)
    cached = _get_cached_location(cache_key)
    if cached:
        return cached

    data = _qweather_get_json(
        "/geo/v2/city/lookup",
        {"location": city, "number": 5, "lang": "zh"},
        geo=True,
    )
    results = data.get("location") or []
    if not results:
        raise WeatherQueryError("城市未匹配，请换用更具体的城市名")
    location = _pick_qweather_location(city, results)
    _set_cached_location(cache_key, location)
    return location


def _fetch_current_weather(
    location: dict,
    *,
    include_minutely: bool | None = None,
    query_city: str = "",
    provider: str | None = None,
) -> dict:
    provider = provider or _weather_provider()
    if provider == "open-meteo":
        return _fetch_open_meteo_weather(location)

    location_ref = location.get("id") or _location_coordinates(location)
    if not location_ref:
        raise WeatherQueryError("缺少天气查询位置")

    now = _qweather_get_json("/v7/weather/now", {"location": location_ref, "lang": "zh", "unit": "m"})
    hourly = _qweather_get_json("/v7/weather/24h", {"location": location_ref, "lang": "zh", "unit": "m"})
    minutely = {}
    coordinates = _location_coordinates(location, max_digits=2)
    if coordinates and _should_fetch_minutely(location, now, hourly, include_minutely=include_minutely, query_city=query_city):
        try:
            minutely = _qweather_get_json("/v7/minutely/5m", {"location": coordinates, "lang": "zh"})
        except Exception as exc:
            logger.info("分钟级降水不可用，使用小时预报兜底: %s", exc)
    return _normalize_qweather_weather(now, hourly, minutely)


def _resolve_city_open_meteo(city: str) -> dict:
    params = urllib.parse.urlencode(
        {"name": city, "count": 5, "language": "zh", "format": "json"},
        quote_via=urllib.parse.quote,
        safe="",
    )
    data = _get_json(f"{OPEN_METEO_GEOCODING_API}?{params}")
    results = data.get("results") or []
    if not results:
        raise WeatherQueryError("城市未匹配，请换用更具体的城市名")
    return _pick_open_meteo_location(city, results)


def _fetch_open_meteo_weather(location: dict) -> dict:
    coordinates = _location_coordinates(location)
    if not coordinates:
        raise WeatherQueryError("缺少天气查询位置")
    params = urllib.parse.urlencode(
        {
            "latitude": location["latitude"],
            "longitude": location["longitude"],
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
        },
        quote_via=urllib.parse.quote,
        safe=",",
    )
    return _normalize_open_meteo_weather(_get_json(f"{OPEN_METEO_FORECAST_API}?{params}"))


def _qweather_get_json(path: str, params: dict, *, geo: bool = False) -> dict:
    data = _get_json(_qweather_url(path, params, geo=geo), headers=_qweather_headers())
    code = str(data.get("code") or "")
    if code and code != "200":
        raise WeatherQueryError(_qweather_code_message(code))
    return data


def _qweather_url(path: str, params: dict, *, geo: bool = False) -> str:
    host = _qweather_geo_host() if geo else _qweather_api_host()
    base = host.rstrip("/")
    if not base.startswith(("http://", "https://")):
        base = f"https://{base}"
    query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote, safe="")
    return f"{base}{path}?{query}"


def _qweather_api_host() -> str:
    return os.environ.get("QWEATHER_API_HOST", "").strip() or QWEATHER_DEFAULT_API_HOST


def _qweather_geo_host() -> str:
    return (
        os.environ.get("QWEATHER_GEO_API_HOST", "").strip()
        or os.environ.get("QWEATHER_API_HOST", "").strip()
        or QWEATHER_DEFAULT_GEO_API_HOST
    )


def _qweather_headers() -> dict:
    headers = {"Accept-Encoding": "gzip"}
    jwt = os.environ.get("QWEATHER_JWT", "").strip()
    api_key = os.environ.get("QWEATHER_API_KEY", "").strip()
    if jwt:
        headers["Authorization"] = f"Bearer {jwt}"
    elif api_key:
        headers["X-QW-Api-Key"] = api_key
    return headers


def _normalize_qweather_location(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "name": item.get("name") or "",
        "label": _location_label(item),
        "admin1": item.get("admin1") or item.get("adm1"),
        "admin2": item.get("admin2") or item.get("adm2"),
        "country": item.get("country"),
        "latitude": _to_number(item.get("lat")),
        "longitude": _to_number(item.get("lon")),
    }


def _normalize_open_meteo_location(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "name": item.get("name") or "",
        "label": _location_label(item),
        "admin1": item.get("admin1"),
        "admin2": item.get("admin2"),
        "country": item.get("country"),
        "latitude": _to_number(item.get("latitude")),
        "longitude": _to_number(item.get("longitude")),
    }


def _normalize_qweather_weather(now_data: dict, hourly_data: dict, minutely_data: dict | None = None) -> dict:
    now = now_data.get("now") or {}
    hourly_rows = hourly_data.get("hourly") or []
    minutely_rows = (minutely_data or {}).get("minutely") or []
    refer_sources = []
    for data in (now_data, hourly_data, minutely_data or {}):
        refer = data.get("refer") or {}
        refer_sources.extend(refer.get("sources") or [])

    return {
        "current": {
            "time": now.get("obsTime") or now_data.get("updateTime"),
            "condition": now.get("text") or "",
            "weather_code": now.get("icon"),
            "temperature_2m": now.get("temp"),
            "apparent_temperature": now.get("feelsLike"),
            "relative_humidity_2m": now.get("humidity"),
            "precipitation": now.get("precip"),
            "wind_speed_10m": now.get("windSpeed"),
        },
        "current_units": {
            "temperature_2m": "°C",
            "apparent_temperature": "°C",
            "relative_humidity_2m": "%",
            "precipitation": "mm",
            "wind_speed_10m": "km/h",
        },
        "hourly": {
            "time": [row.get("fxTime") for row in hourly_rows],
            "precipitation": [row.get("precip") for row in hourly_rows],
            "precipitation_probability": [row.get("pop") for row in hourly_rows],
        },
        "minutely": {
            "time": [row.get("fxTime") for row in minutely_rows],
            "precipitation": [row.get("precip") for row in minutely_rows],
        },
        "source": {
            "name": "QWeather",
            "refer_sources": sorted({str(source) for source in refer_sources if source}),
        },
    }


def _normalize_open_meteo_weather(data: dict) -> dict:
    current = data.get("current") or {}
    current_units = data.get("current_units") or {}
    hourly = data.get("hourly") or {}
    weather_code = current.get("weather_code")

    return {
        "current": {
            "time": current.get("time"),
            "condition": WEATHER_CODE_MAP.get(weather_code, f"天气代码 {weather_code}"),
            "weather_code": weather_code,
            "temperature_2m": current.get("temperature_2m"),
            "apparent_temperature": current.get("apparent_temperature"),
            "relative_humidity_2m": current.get("relative_humidity_2m"),
            "precipitation": current.get("precipitation"),
            "wind_speed_10m": current.get("wind_speed_10m"),
        },
        "current_units": current_units,
        "hourly": {
            "time": hourly.get("time") or [],
            "precipitation": hourly.get("precipitation") or [],
            "precipitation_probability": hourly.get("precipitation_probability") or [],
        },
        "minutely": {"time": [], "precipitation": []},
        "source": {"name": "Open-Meteo"},
    }


def _weather_cache_key(city: str, *, include_minutely: bool | None = None, provider: str | None = None) -> str:
    return json.dumps(
        {
            "city": _normalize_city_key(city or DEFAULT_WEATHER_CITY),
            "minutely": include_minutely,
            "provider": provider or _weather_provider(),
            "api_host": _qweather_api_host(),
            "geo_host": _qweather_geo_host(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _location_cache_key(city: str) -> str:
    return json.dumps(
        {
            "city": _normalize_city_key(city or DEFAULT_WEATHER_CITY),
            "geo_host": _qweather_geo_host(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _get_cached_weather(cache_key: str) -> dict | None:
    if WEATHER_CACHE_TTL_SECONDS <= 0:
        return None
    cached = _WEATHER_CACHE.get(cache_key)
    if not cached:
        return None
    if time.time() - cached.get("stored_at", 0) > WEATHER_CACHE_TTL_SECONDS:
        _WEATHER_CACHE.pop(cache_key, None)
        return None
    return json.loads(json.dumps(cached["snapshot"], ensure_ascii=False))


def _set_cached_weather(cache_key: str, snapshot: dict) -> None:
    if WEATHER_CACHE_TTL_SECONDS <= 0:
        return
    _WEATHER_CACHE[cache_key] = {
        "stored_at": time.time(),
        "snapshot": json.loads(json.dumps(snapshot, ensure_ascii=False)),
    }


def _get_cached_location(cache_key: str) -> dict | None:
    if QWEATHER_LOCATION_CACHE_TTL_SECONDS <= 0:
        return None
    cached = _LOCATION_CACHE.get(cache_key)
    if not cached:
        return None
    if time.time() - cached.get("stored_at", 0) > QWEATHER_LOCATION_CACHE_TTL_SECONDS:
        _LOCATION_CACHE.pop(cache_key, None)
        return None
    return json.loads(json.dumps(cached["location"], ensure_ascii=False))


def _set_cached_location(cache_key: str, location: dict) -> None:
    if QWEATHER_LOCATION_CACHE_TTL_SECONDS <= 0:
        return
    _LOCATION_CACHE[cache_key] = {
        "stored_at": time.time(),
        "location": json.loads(json.dumps(location, ensure_ascii=False)),
    }


def clear_weather_cache() -> None:
    """清空天气缓存，供测试或运维诊断使用。"""
    _WEATHER_CACHE.clear()
    _LOCATION_CACHE.clear()


def _pick_qweather_location(city: str, results: list[dict]) -> dict:
    normalized = _normalize_city_key(city)
    exact = [
        item
        for item in results
        if _normalize_city_key(item.get("name") or "") == normalized
        or _normalize_city_key(item.get("name") or "").removesuffix("市") == normalized
    ]
    if len(exact) == 1:
        return _normalize_qweather_location(exact[0])
    if len(exact) > 1:
        raise AmbiguousCityError(city, [_normalize_qweather_location(item) for item in exact])
    if len(results) > 1 and _is_ambiguous_city_query(city, results):
        raise AmbiguousCityError(city, [_normalize_qweather_location(item) for item in results])
    return _normalize_qweather_location(results[0])


def _pick_open_meteo_location(city: str, results: list[dict]) -> dict:
    normalized = _normalize_city_key(city)
    exact = [
        item
        for item in results
        if _normalize_city_key(item.get("name") or "") == normalized
        or _normalize_city_key(item.get("name") or "").removesuffix("市") == normalized
    ]
    if len(exact) == 1:
        return _normalize_open_meteo_location(exact[0])
    if len(exact) > 1:
        strong = [item for item in exact if _is_strong_open_meteo_city_match(normalized, item)]
        if len(strong) == 1:
            return _normalize_open_meteo_location(strong[0])
        raise AmbiguousCityError(city, [_normalize_open_meteo_location(item) for item in exact])
    if len(results) > 1 and _is_ambiguous_open_meteo_query(city, results):
        raise AmbiguousCityError(city, [_normalize_open_meteo_location(item) for item in results])
    return _normalize_open_meteo_location(results[0])


def _is_ambiguous_city_query(city: str, results: list[dict]) -> bool:
    key = _normalize_city_key(city)
    if any(key.endswith(suffix) for suffix in WEATHER_REGION_SUFFIXES):
        return False
    names = {_normalize_city_key(item.get("name") or "").removesuffix("市") for item in results}
    if key in names and len(results) > 1:
        return True
    return len({(item.get("name"), item.get("adm1"), item.get("adm2"), item.get("country")) for item in results}) > 1


def _is_ambiguous_open_meteo_query(city: str, results: list[dict]) -> bool:
    key = _normalize_city_key(city)
    if any(key.endswith(suffix) for suffix in WEATHER_REGION_SUFFIXES):
        return False
    names = {_normalize_city_key(item.get("name") or "").removesuffix("市") for item in results}
    if key in names and len(results) > 1:
        return True
    return len({(item.get("name"), item.get("admin1"), item.get("country")) for item in results}) > 1


def _is_strong_open_meteo_city_match(city_key: str, item: dict) -> bool:
    admin_keys = {
        _normalize_city_key(str(item.get("admin1") or "")).removesuffix("市"),
        _normalize_city_key(str(item.get("admin2") or "")).removesuffix("市"),
    }
    if city_key in admin_keys:
        return True
    feature_code = str(item.get("feature_code") or "")
    return feature_code in {"PPLA", "PPLA2"} and _to_float(item.get("population")) >= 1_000_000


def _format_ambiguous_city_reply(exc: AmbiguousCityError) -> str:
    lines = [
        "## 🌦️ 城市名不明确",
        "",
        f"- **城市**：{exc.city}",
        "- **原因**：匹配到多个候选，请补充省市县信息。",
        "",
        "可选候选：",
    ]
    for item in exc.candidates[:5]:
        lines.append(f"- {item.get('label') or item.get('name') or '-'}")
    return "\n".join(lines)


def _weather_error_message(exc: Exception) -> str:
    if isinstance(exc, WeatherQueryError):
        return exc.user_message
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            if _weather_provider() == "qweather":
                return "QWeather 鉴权失败，请检查 API Key 和 API Host。"
            return f"天气接口返回 HTTP {exc.code}"
        if exc.code == 429:
            if _weather_provider() == "qweather":
                return "QWeather 调用频率或额度受限，请稍后重试。"
            return "天气接口调用频率受限，请稍后重试。"
        return f"天气接口返回 HTTP {exc.code}"
    if isinstance(exc, TimeoutError):
        return "天气接口超时，请稍后重试。"
    if isinstance(exc, OSError):
        return "网络连接异常，请稍后重试。"
    return "暂时无法获取天气数据，请稍后重试。"


def _qweather_code_message(code: str) -> str:
    return {
        "204": "城市未匹配，请换用更具体的城市名。",
        "400": "天气请求参数无效。",
        "401": "QWeather 鉴权失败，请检查 API Key。",
        "402": "QWeather 额度不足或服务未开通。",
        "403": "QWeather API Host 未授权，请检查专属 API Host。",
        "429": "QWeather 调用频率过高，请稍后重试。",
    }.get(code, f"和风天气接口返回 code={code}")


def _should_fetch_minutely(
    location: dict,
    now_data: dict,
    hourly_data: dict,
    *,
    include_minutely: bool | None = None,
    query_city: str = "",
) -> bool:
    if include_minutely is True:
        return True
    if include_minutely is False:
        return False
    if _normalize_city_key(query_city or location.get("name") or "") in LOCAL_WEATHER_LOCATIONS:
        return True
    now = now_data.get("now") or {}
    if _to_float(now.get("precip")) > 0:
        return True
    if _weather_icon_indicates_rain(now.get("icon")):
        return True
    for row in (hourly_data.get("hourly") or [])[:2]:
        if _to_float(row.get("precip")) > 0 or _to_float(row.get("pop")) >= 60:
            return True
    return False


def _weather_icon_indicates_rain(icon) -> bool:
    try:
        code = int(icon)
    except (TypeError, ValueError):
        return False
    return 300 <= code <= 399 or code in {456, 457}


def _location_coordinates(location: dict, *, max_digits: int | None = None) -> str:
    lat = _to_number(location.get("latitude"))
    lon = _to_number(location.get("longitude"))
    if lat is None or lon is None:
        return ""
    if max_digits is None:
        return f"{lon},{lat}"
    return f"{lon:.{max_digits}f},{lat:.{max_digits}f}"


def _get_json(url: str, headers: dict | None = None) -> dict:
    last_exc: Exception | None = None
    for attempt in range(WEATHER_RETRIES + 1):
        try:
            request_headers = {"User-Agent": "WeChat-Bridge/1.2"}
            request_headers.update(headers or {})
            req = urllib.request.Request(url, headers=request_headers)
            with urllib.request.urlopen(req, timeout=WEATHER_TIMEOUT) as resp:
                body = resp.read()
                response_headers = getattr(resp, "headers", {}) or {}
                if str(response_headers.get("Content-Encoding", "")).lower() == "gzip":
                    body = gzip.decompress(body)
                return json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_exc = _weather_http_error(exc)
            if exc.code not in QWEATHER_RETRYABLE_HTTP_STATUS or attempt >= WEATHER_RETRIES:
                break
            logger.info("天气接口请求失败，准备重试 [%s/%s]: %s", attempt + 1, WEATHER_RETRIES, last_exc)
            _sleep_before_weather_retry(attempt, last_exc)
        except Exception as exc:
            last_exc = exc
            if attempt >= WEATHER_RETRIES:
                break
            logger.info("天气接口请求失败，准备重试 [%s/%s]: %s", attempt + 1, WEATHER_RETRIES, exc)
            _sleep_before_weather_retry(attempt, exc)
    raise last_exc or RuntimeError("天气接口请求失败")


def _sleep_before_weather_retry(attempt: int, exc: Exception) -> None:
    delay = WEATHER_RETRY_BASE_DELAY_SECONDS * (2 ** max(0, attempt))
    if isinstance(exc, WeatherQueryError) and exc.status_code == 429:
        delay = max(delay, 2.0)
    jitter = random.uniform(0, min(0.5, delay * 0.25))
    time.sleep(delay + jitter)


def _weather_http_error(exc: urllib.error.HTTPError) -> WeatherQueryError:
    detail = ""
    try:
        body = exc.read()
        if str(exc.headers.get("Content-Encoding", "")).lower() == "gzip":
            body = gzip.decompress(body)
        payload = json.loads(body.decode("utf-8"))
        error = payload.get("error") or {}
        detail = error.get("title") or error.get("detail") or ""
    except Exception:
        detail = ""
    return WeatherQueryError(_weather_error_message(exc) if not detail else f"{_weather_error_message(exc)}（{detail}）", status_code=exc.code)


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
    minutely_at = _find_minutely_rain_transition(weather, current_dt, deadline, want_rain=want_rain)
    if minutely_at:
        return minutely_at

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


def _find_minutely_rain_transition(
    weather: dict, current_dt: datetime, deadline: datetime, *, want_rain: bool
) -> datetime | None:
    minutely = weather.get("minutely") or {}
    times = minutely.get("time") or []
    precipitation = minutely.get("precipitation") or []

    for idx, raw_time in enumerate(times):
        dt = _parse_weather_time(raw_time)
        if dt is None or dt <= current_dt or dt > deadline:
            continue
        amount = _to_float(precipitation[idx] if idx < len(precipitation) else 0)
        if (amount > 0) is want_rain:
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
    parsed = _parse_weather_time(value)
    if parsed:
        return f"{parsed:%Y-%m-%d %H:%M}"
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
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        pass
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
    parts = [location.get("name"), location.get("admin1") or location.get("adm1"), location.get("country")]
    return " · ".join(str(part) for part in parts if part)


def _weather_title_name(location: dict) -> str:
    name = str(location.get("name") or "").strip()
    if not name:
        name = str(location.get("label") or "").split("·", 1)[0].strip()
    if not name:
        return ""
    if name.endswith(WEATHER_REGION_SUFFIXES):
        return name

    name_key = _normalize_city_key(name).removesuffix("市")
    admin2 = str(location.get("admin2") or location.get("adm2") or location.get("adm1") or "").strip()
    admin1 = str(location.get("admin1") or location.get("adm1") or "").strip()
    for admin in (admin2, admin1):
        admin_key = _normalize_city_key(admin).removesuffix("市")
        if admin and admin_key == name_key:
            return admin if admin.endswith(WEATHER_REGION_SUFFIXES) else f"{name}市"
    if str(location.get("country") or "").strip() in {"中国", "China", "CN"}:
        return f"{name}市"
    return name
