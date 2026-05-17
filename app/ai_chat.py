from __future__ import annotations

"""
AI 对话模块
- 统一适配 OpenAI / Gemini / DeepSeek / MiniMax 与自定义 OpenAI 兼容 REST API
- Claude 走 Anthropic Messages API（仅 payload 结构不同）
- 全部使用 requests 库，零额外依赖
- 按 user_id 隔离会话历史（OrderedDict LRU 防 OOM）
- Token 用量日统计
"""

import json
import logging
from collections import OrderedDict
from collections.abc import Generator
from datetime import datetime

import requests

logger = logging.getLogger(__name__)


def _parse_sse(line: bytes) -> dict | None:
    line = line.strip()
    if line.startswith(b"data: ") and line != b"data: [DONE]":
        try:
            return json.loads(line[6:])
        except json.JSONDecodeError:
            pass
    return None


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

    def chat_stream(self, user_id: str, text: str) -> Generator[str, None, str]:
        """
        流式处理一轮对话。
        Yields: AI 回复的增量文本片段
        Returns: 完整的 AI 回复
        """
        config = self._load_config()

        if not config.get("enabled"):
            return ""

        if not config.get("api_key"):
            msg = "⚠️ AI 未配置 API Key，请在 Web 管理面板中设置。"
            yield msg
            return msg

        if not self._check_daily_limit(config):
            msg = "⚠️ 今日 AI 调用额度已用尽，明天再试吧。"
            yield msg
            return msg

        max_history = config.get("max_history", 10)
        with self._lock:
            history = self._get_history(user_id)
            history.append({"role": "user", "content": text})
            if len(history) > max_history * 2:
                history[:] = history[-(max_history * 2) :]
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
            is_anthropic = provider_info.get("sdk") == "anthropic"

            headers = {
                "Content-Type": "application/json",
            }
            if is_anthropic:
                headers["x-api-key"] = api_key
                headers["anthropic-version"] = "2023-06-01"
                payload = {
                    "model": model,
                    "max_tokens": 2048,
                    "system": system_prompt,
                    "messages": req_history,
                    "stream": True,
                }
                endpoint = f"{effective_url.rstrip('/')}/v1/messages"
            else:
                headers["Authorization"] = f"Bearer {api_key}"
                payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": provider_info.get("temperature", 0.7),
                    "stream": True,
                }
                payload[provider_info.get("max_tokens_param", "max_tokens")] = 2048
                payload.update(provider_info.get("extra_body", {}))
                endpoint = effective_url
                if "chat/completions" not in endpoint:
                    endpoint = f"{endpoint.rstrip('/')}/chat/completions"

            resp = requests.post(endpoint, json=payload, headers=headers, timeout=60, stream=True)
            resp.raise_for_status()

            full_reply = ""
            reasoning_started = False
            reasoning_ended = False

            for line in resp.iter_lines():
                if not line:
                    continue

                chunk = _parse_sse(line)
                if not chunk:
                    continue

                if is_anthropic:
                    if chunk.get("type") == "content_block_delta":
                        delta = chunk.get("delta", {}).get("text", "")
                        if delta:
                            full_reply += delta
                            yield delta
                else:
                    choices = chunk.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        reasoning = delta.get("reasoning_content", "")

                        if reasoning:
                            if not reasoning_started:
                                reasoning_started = True
                                yield "【思考过程】\n"
                                full_reply += "【思考过程】\n"
                            full_reply += reasoning
                            yield reasoning

                        if content:
                            if reasoning_started and not reasoning_ended:
                                reasoning_ended = True
                                yield "\n\n"
                                full_reply += "\n\n"
                            full_reply += content
                            yield content

            # 记录历史与用量
            with self._lock:
                history.append({"role": "assistant", "content": full_reply})
            # 对于 stream 模式，简单按照字数估算 tokens
            estimated_tokens = len(text) + len(full_reply)
            self._record_usage(config, estimated_tokens)

            logger.info("AI 流式回复完成 [%s]: %s...", user_id[:16], full_reply[:30].replace("\n", " "))
            return full_reply

        except Exception as e:
            logger.error("AI 调用失败 [%s]: %s", user_id[:16], e)
            with self._lock:
                history = self._get_history(user_id)
                if history and history[-1]["role"] == "user":
                    history.pop()
            err_msg = f"⚠️ AI 暂时不可用: {str(e)[:100]}"
            yield err_msg
            return err_msg

    def chat(self, user_id: str, text: str) -> str:
        """
        处理一轮对话（非流式，内部基于 stream 实现组合）
        """
        generator = self.chat_stream(user_id, text)
        full_text = ""
        try:
            while True:
                full_text += next(generator)
        except StopIteration as e:
            if e.value is not None:
                full_text = e.value

        if len(full_text) > 5200:
            full_text = full_text[:5200] + "\n...(字数超限截断)"
        return full_text

    def clear_history(self, user_id: str):
        """清除某用户的会话历史"""
        with self._lock:
            self._histories.pop(user_id, None)

    def clear_all_histories(self):
        """清除所有会话历史"""
        with self._lock:
            self._histories.clear()
