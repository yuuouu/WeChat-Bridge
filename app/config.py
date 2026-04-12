import os
import json
import logging
import threading

logger = logging.getLogger(__name__)

CONFIG_FILE = os.environ.get("AI_CONFIG_FILE", "/data/ai_config.json")
_config_lock = threading.Lock()  # 防止多线程并发写 JSON 文件撕裂

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
}

DEFAULT_CONFIG = {
    "enabled": False,
    "provider": "openai",
    "model": "gpt-4o-mini",
    "api_key": "",
    "base_url": "",
    "system_prompt": "你是一个有帮助的 AI 助手。回复尽量简洁。",
    "max_history": 10,
    "max_tokens_per_day": 100000,
    "usage": {},
    "keepalive_remind_minutes": 0,  # 0=关闭, 60~1430=用户最后消息后N分钟发送提醒
}


def load_config() -> dict:
    """加载 AI 配置，优先从文件读取，环境变量可覆盖"""
    config = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                saved = json.load(f)
            config.update(saved)
            logger.info("已加载 AI 配置: provider=%s, model=%s, enabled=%s",
                        config["provider"], config["model"], config["enabled"])
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
        
    # 向后兼容：将旧的双布尔迁移为新字段
    if "keepalive_23h" in config or "keepalive_23h58m" in config:
        if "keepalive_remind_minutes" not in config or config.get("keepalive_remind_minutes") == 0:
            if config.pop("keepalive_23h58m", False):
                config["keepalive_remind_minutes"] = 1438  # 23h58m
            elif config.pop("keepalive_23h", False):
                config["keepalive_remind_minutes"] = 1380  # 23h
        config.pop("keepalive_23h", None)
        config.pop("keepalive_23h58m", None)
    if "keepalive_remind_minutes" not in config:
        config["keepalive_remind_minutes"] = 0

    return config


def save_config(config: dict):
    """持久化 AI 配置到文件（加锁防并发写撕裂）"""
    with _config_lock:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        logger.info("AI 配置已保存")


def get_provider_info(provider_id: str) -> dict:
    """获取厂商预设信息"""
    return PROVIDERS.get(provider_id, PROVIDERS["openai"])
