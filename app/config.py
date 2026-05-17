from __future__ import annotations

import json
import logging
import os
import threading

logger = logging.getLogger(__name__)

_config_cache: dict | None = None
_config_cache_mtime: float = 0.0
_config_cache_file: str = ""


def _resolve_config_file() -> str:
    """优先使用 config.json，向后兼容 ai_config.json"""
    base_dir = os.path.dirname(os.environ.get("AI_CONFIG_FILE", "./data/ai_config.json"))
    new_config = os.path.join(base_dir, "config.json")
    old_config = os.path.join(base_dir, "ai_config.json")

    if os.path.exists(new_config):
        return new_config
    if os.path.exists(old_config):
        return old_config
    return os.environ.get("AI_CONFIG_FILE", old_config)


CONFIG_FILE = _resolve_config_file()
_config_lock = threading.Lock()

# 厂商预设：{provider_id: {name, base_url, models: [{id, name}]}}
PROVIDERS = {
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": [
            {"id": "gpt-4o", "name": "GPT-4o"},
            {"id": "gpt-4o-mini", "name": "GPT-4o Mini"},
            {"id": "gpt-4.1-mini", "name": "GPT-4.1 Mini"},
            {"id": "gpt-4.1-nano", "name": "GPT-4.1 Nano"},
        ],
    },
    "gemini": {
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "models": [
            {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash"},
            {"id": "gemini-2.5-flash-preview-04-17", "name": "Gemini 2.5 Flash"},
            {"id": "gemini-2.5-pro-preview-03-25", "name": "Gemini 2.5 Pro"},
        ],
    },
    "claude": {
        "name": "Anthropic Claude",
        "base_url": "https://api.anthropic.com",
        "sdk": "anthropic",
        "models": [
            {"id": "claude-sonnet-4-20250514", "name": "Claude Sonnet 4"},
            {"id": "claude-3-5-haiku-20241022", "name": "Claude 3.5 Haiku"},
        ],
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "models": [
            {"id": "deepseek-chat", "name": "DeepSeek Chat (V3)"},
            {"id": "deepseek-reasoner", "name": "DeepSeek Reasoner (R1)"},
        ],
    },
    "minimax": {
        "name": "MiniMax",
        "base_url": "https://api.minimax.io/v1",
        "max_tokens_param": "max_completion_tokens",
        "temperature": 1.0,
        "extra_body": {"reasoning_split": True},
        "models": [
            {"id": "MiniMax-M2.7", "name": "MiniMax M2.7"},
            {"id": "MiniMax-M2.7-highspeed", "name": "MiniMax M2.7 Highspeed"},
            {"id": "MiniMax-M2.5", "name": "MiniMax M2.5"},
            {"id": "MiniMax-M2.1", "name": "MiniMax M2.1"},
        ],
    },
}

DEFAULT_WEBHOOK_URL = "http://127.0.0.1:18082/webhook"

DEFAULT_CONFIG = {
    "enabled": False,
    "provider": "openai",
    "model": "gpt-4o-mini",
    "api_key": "",
    "base_url": "",
    "system_prompt": "你是一个全能的微信 AI 助手。你的目标是提供准确、高效、友好的解答。\n\n【核心原则】\n1. 言简意赅：微信聊天场景下屏幕有限，回复务必精炼直接，突出重点，避免冗长段落。\n2. 排版清晰：适当使用换行、分点列表和 Emoji 等元素，确保手机端阅读体验感良好。\n3. 真诚客观：遇到不知道的问题请直接坦诚说明，绝不胡编乱造。",
    "max_history": 10,
    "max_tokens_per_day": 100000,
    "usage": {},
    "keepalive_remind_minutes": 1380,  # 0=关闭, 60~1430=用户最后消息后N分钟发送提醒
    "webhook_enabled": True,
    "webhook_url": DEFAULT_WEBHOOK_URL,
    "webhook_mode": "all_messages",
    "webhook_timeout": 5,
    "telemetry_enabled": False,
}


def _invalidate_config_cache():
    global _config_cache, _config_cache_mtime, _config_cache_file
    _config_cache = None
    _config_cache_mtime = 0.0
    _config_cache_file = ""


def validate_config(cfg: dict) -> dict:
    """简单的 schema 校验与格式化"""
    valid = cfg.copy()
    valid["enabled"] = bool(valid.get("enabled"))
    valid["webhook_enabled"] = bool(valid.get("webhook_enabled"))
    valid["telemetry_enabled"] = bool(valid.get("telemetry_enabled"))

    try:
        valid["webhook_timeout"] = max(1, min(30, int(valid.get("webhook_timeout", 5))))
    except (TypeError, ValueError):
        valid["webhook_timeout"] = 5

    if valid.get("webhook_mode") not in ("unknown_command", "all_messages"):
        valid["webhook_mode"] = "all_messages"

    try:
        valid["keepalive_remind_minutes"] = max(-1, int(valid.get("keepalive_remind_minutes", 1380)))
    except (TypeError, ValueError):
        valid["keepalive_remind_minutes"] = 1380

    return valid


def load_config() -> dict:
    """加载配置，优先从文件读取，基于 mtime 热加载"""
    global _config_cache, _config_cache_mtime, _config_cache_file

    try:
        current_mtime = os.path.getmtime(CONFIG_FILE) if os.path.exists(CONFIG_FILE) else 0.0
    except OSError:
        current_mtime = 0.0

    if _config_cache is not None and _config_cache_file == CONFIG_FILE and _config_cache_mtime == current_mtime:
        return _config_cache.copy()

    config = DEFAULT_CONFIG.copy()
    saved = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                saved = json.load(f)
            config.update(saved)
            logger.info(
                "已加载 AI 配置: provider=%s, model=%s, enabled=%s",
                config["provider"],
                config["model"],
                config["enabled"],
            )
        except Exception as e:
            logger.warning("读取 AI 配置失败: %s", e)

    # 环境变量覆盖
    if os.environ.get("AI_API_KEY"):
        config["api_key"] = os.environ["AI_API_KEY"]
    if os.environ.get("AI_PROVIDER"):
        config["provider"] = os.environ["AI_PROVIDER"]
    if os.environ.get("AI_MODEL"):
        config["model"] = os.environ["AI_MODEL"]
    if os.environ.get("AI_ENABLED"):
        config["enabled"] = os.environ["AI_ENABLED"].lower() in ("true", "1", "yes")
    if os.environ.get("AI_BASE_URL"):
        config["base_url"] = os.environ["AI_BASE_URL"]
    if os.environ.get("WEBHOOK_URL"):
        config["webhook_url"] = os.environ["WEBHOOK_URL"]
    if os.environ.get("WEBHOOK_ENABLED"):
        config["webhook_enabled"] = os.environ["WEBHOOK_ENABLED"].lower() in ("true", "1", "yes")
    elif "webhook_enabled" not in saved and config.get("webhook_url"):
        # 兼容旧配置：JSON 中无 webhook_enabled 字段但有 URL 时默认开启
        config["webhook_enabled"] = True
    if os.environ.get("WEBHOOK_MODE"):
        config["webhook_mode"] = os.environ["WEBHOOK_MODE"].strip() or "unknown_command"
    if os.environ.get("WEBHOOK_TIMEOUT"):
        try:
            config["webhook_timeout"] = int(os.environ["WEBHOOK_TIMEOUT"])
        except ValueError:
            logger.warning("无效的 WEBHOOK_TIMEOUT: %s", os.environ["WEBHOOK_TIMEOUT"])

    # 应用 schema 校验
    config = validate_config(config)

    # 向后兼容：将旧的双布尔迁移为新字段，如有旧字段则自动升级保存一次
    needs_save = False
    if "keepalive_23h" in config or "keepalive_23h58m" in config:
        if "keepalive_remind_minutes" not in config:
            if config.get("keepalive_23h58m", False):
                config["keepalive_remind_minutes"] = 1438  # 23h58m
            elif config.get("keepalive_23h", False):
                config["keepalive_remind_minutes"] = 1380  # 23h
        config.pop("keepalive_23h", None)
        config.pop("keepalive_23h58m", None)
        needs_save = True

    if "keepalive_remind_minutes" not in config:
        config["keepalive_remind_minutes"] = 1380
        needs_save = True

    if needs_save:
        save_config(config)

    _config_cache = config.copy()
    _config_cache_mtime = current_mtime
    _config_cache_file = CONFIG_FILE
    return config


def save_config(config: dict):
    """持久化 AI 配置到文件（加锁防并发写撕裂），同时清除缓存。"""
    _invalidate_config_cache()
    with _config_lock:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        logger.info("AI 配置已保存")


def get_provider_info(provider_id: str) -> dict:
    """获取厂商预设信息"""
    provider_id_lower = provider_id.lower() if provider_id else ""
    return PROVIDERS.get(provider_id_lower, PROVIDERS["openai"])
