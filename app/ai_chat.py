"""
AI 对话模块
- 统一适配 OpenAI / Gemini / DeepSeek（OpenAI 兼容 REST API）
- Claude 走 Anthropic Messages API（仅 payload 结构不同）
- 全部使用 requests 库，零额外依赖
- 按 user_id 隔离会话历史（OrderedDict LRU 防 OOM）
- Token 用量日统计
"""
import logging
import time
import requests
from datetime import datetime
from collections import OrderedDict

logger = logging.getLogger(__name__)


class AIChatManager:
    """AI 对话管理器"""

    MAX_USERS = 200  # 最大同时跟踪的用户会话数（LRU 淘汰）

    def __init__(self, config_loader, config_saver):
        """
        config_loader: callable，返回当前 AI 配置 dict
        config_saver: callable，接受 config dict 并持久化
        """
        import threading
        self._lock = threading.Lock()  # 保护 _histories 的并发读写
        self._load_config = config_loader
        self._save_config = config_saver
        # 使用 OrderedDict 实现简易 LRU，防海量用户导致 OOM
        self._histories: OrderedDict[str, list] = OrderedDict()

    def _get_history(self, user_id: str) -> list:
        """获取用户历史（LRU 淘汰最久未活跃的用户）。调用方需持有 self._lock"""
        if user_id in self._histories:
            self._histories.move_to_end(user_id)
        else:
            if len(self._histories) >= self.MAX_USERS:
                self._histories.popitem(last=False)
            self._histories[user_id] = []
        return self._histories[user_id]

    def _check_daily_limit(self, config: dict) -> bool:
        """检查是否超过每日 Token 限额"""
        today = datetime.now().strftime("%Y-%m-%d")
        usage = config.get("usage", {})
        day_usage = usage.get(today, {})
        return day_usage.get("tokens", 0) < config.get("max_tokens_per_day", 100000)

    def _record_usage(self, config: dict, tokens_used: int):
        """记录 Token 用量"""
        today = datetime.now().strftime("%Y-%m-%d")
        if "usage" not in config:
            config["usage"] = {}
        if today not in config["usage"]:
            config["usage"][today] = {"tokens": 0, "requests": 0}
        config["usage"][today]["tokens"] += tokens_used
        config["usage"][today]["requests"] += 1
        # 只保留最近 7 天的用量记录
        keys = sorted(config["usage"].keys())
        while len(keys) > 7:
            del config["usage"][keys.pop(0)]
        self._save_config(config)

    def chat(self, user_id: str, text: str) -> str:
        """
        处理一轮对话
        Returns: AI 回复文本，或错误消息
        """
        config = self._load_config()

        if not config.get("enabled"):
            return ""  # AI 未启用，返回空表示不处理

        if not config.get("api_key"):
            return "⚠️ AI 未配置 API Key，请在 Web 管理面板中设置。"

        if not self._check_daily_limit(config):
            return "⚠️ 今日 AI 调用额度已用尽，明天再试吧。"

        # 维护会话历史（加锁防并发写 RuntimeError）
        max_history = config.get("max_history", 10)
        with self._lock:
            history = self._get_history(user_id)
            history.append({"role": "user", "content": text})
            # 截断历史
            if len(history) > max_history * 2:
                history[:] = history[-(max_history * 2):]
            # 拷贝副本用于网络请求，避免长时间 I/O 期间被其他线程修改
            req_history = list(history)

        system_prompt = config.get("system_prompt", "你是一个有帮助的 AI 助手。")
        messages = [{"role": "system", "content": system_prompt}] + req_history

        try:
            provider = config["provider"]
            model = config["model"]
            api_key = config["api_key"]
            from config import get_provider_info
            provider_info = get_provider_info(provider)
            effective_url = config.get("base_url") or provider_info["base_url"]

            if provider_info.get("sdk") == "anthropic":
                # Claude 原生 REST API 调用（Anthropic Messages API）
                headers = {
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                }
                payload = {
                    "model": model,
                    "max_tokens": 2048,
                    "system": system_prompt,
                    "messages": req_history,  # Anthropic 不在 messages 里放 system
                }
                endpoint = f"{effective_url.rstrip('/')}/v1/messages"
                resp = requests.post(endpoint, json=payload, headers=headers, timeout=60)
                resp.raise_for_status()
                data = resp.json()
                reply = data.get("content", [{}])[0].get("text", "")
                tokens_used = data.get("usage", {}).get("input_tokens", 0) + \
                              data.get("usage", {}).get("output_tokens", 0)
            else:
                # OpenAI / Gemini / DeepSeek 统一走 OpenAI 兼容 REST API
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": 2048,
                    "temperature": 0.7,
                }
                endpoint = effective_url
                if "chat/completions" not in endpoint:
                    endpoint = f"{endpoint.rstrip('/')}/chat/completions"

                resp = requests.post(endpoint, json=payload, headers=headers, timeout=60)
                resp.raise_for_status()
                data = resp.json()

                msg_obj = data.get("choices", [{}])[0].get("message", {})
                reply = msg_obj.get("content", "") or ""

                # 兼容 DeepSeek Reasoner 的思维链
                reasoning = msg_obj.get("reasoning_content")
                if reasoning:
                    reply = f"【思考过程】\n{reasoning}\n\n{reply}"

                tokens_used = data.get("usage", {}).get("total_tokens", 0)

            # ⚠️ 微信单条消息长度限制防爆
            if len(reply) > 1500:
                reply = reply[:1500] + "\n...(字数超限截断)"

            # 记录助手回复到历史与记录用量（加锁防竞争）
            with self._lock:
                history.append({"role": "assistant", "content": reply})
            self._record_usage(config, tokens_used)

            logger.info("AI 回复 [%s] (%s/%s, %d tokens): %s",
                        user_id[:16], provider, model, tokens_used, reply[:80])
            return reply

        except Exception as e:
            logger.error("AI 调用失败 [%s]: %s", user_id[:16], e)
            # 移除用户刚发的这条，避免污染历史
            with self._lock:
                history = self._get_history(user_id)
                if history and history[-1]["role"] == "user":
                    history.pop()
            return f"⚠️ AI 暂时不可用: {str(e)[:100]}"

    def clear_history(self, user_id: str):
        """清除某用户的会话历史"""
        with self._lock:
            self._histories.pop(user_id, None)

    def clear_all_histories(self):
        """清除所有会话历史"""
        with self._lock:
            self._histories.clear()
