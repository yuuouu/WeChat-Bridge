#!/usr/bin/env python3
"""
无状态 Webhook 示例 — 基础命令响应（插件化版本）。

作为 webhook_manager 插件运行（推荐）：
    自动被 discover_and_register_plugins 发现并加载。

单独运行：
    python3 examples/webhook_receiver.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR.parent / "app"))

from plugin_base import Plugin  # noqa: E402

logger = logging.getLogger(__name__)


class ReceiverPlugin(Plugin):
    """基础命令响应示例插件。"""

    name = "receiver"
    description = "基础命令响应示例 (/weather, /echo)"
    commands = ["/weather", "/echo"]

    def get_command_specs(self) -> list[dict]:
        return [
            {"command": "/weather", "description": "获取天气示例"},
            {"command": "/echo", "description": "回显发送的参数"},
        ]

    def handle(self, payload: dict) -> None:
        from_user = payload.get("from_user", "")
        command = payload.get("command", "")
        args = payload.get("args", "")

        if not from_user:
            return

        if command == "/weather":
            city = args or "shanghai"
            reply = f"天气服务示例收到请求：{city}\n这是异步回写示例，你可以在这里接入真实天气 API。"
        elif command == "/echo":
            reply = f"Echo from webhook:\n{args or '(empty)'}"
        else:
            return

        self.send_reply(from_user, reply)


PLUGIN_CLASS = ReceiverPlugin

if __name__ == "__main__":
    from webhook_manager import WebhookManager  # noqa: E402

    mgr = WebhookManager()
    mgr.load_plugin(ReceiverPlugin())
    mgr.run()
