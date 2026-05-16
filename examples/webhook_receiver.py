#!/usr/bin/env python3
"""
无状态 Webhook 示例 — 基础命令响应（插件化版本）。

作为 webhook_manager 插件运行（推荐）：
    python3 examples/webhook_manager.py

单独运行：
    python3 examples/webhook_receiver.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

BRIDGE_BASE_URL = os.environ.get("BRIDGE_BASE_URL", "http://127.0.0.1:5200").rstrip("/")
BRIDGE_API_TOKEN = os.environ.get("BRIDGE_API_TOKEN", "").strip()


def _send(to_user: str, text: str) -> tuple[bool, str]:
    payload = json.dumps({"to": to_user, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        f"{BRIDGE_BASE_URL}/api/send",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    if BRIDGE_API_TOKEN:
        req.add_header("Authorization", f"Bearer {BRIDGE_API_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return True, resp.read().decode("utf-8", "replace")
    except Exception as exc:
        return False, str(exc)


class ReceiverPlugin:
    name = "receiver"

    @property
    def commands(self) -> list[str]:
        return ["/weather", "/echo"]

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
            # 这是一个通用后备，如果 manager 路由了其他消息过来
            return

        _send(from_user, reply)


PLUGIN_CLASS = ReceiverPlugin

if __name__ == "__main__":
    _SCRIPT_DIR = Path(__file__).resolve().parent
    sys.path.insert(0, str(_SCRIPT_DIR))
    from webhook_manager import WebhookManager

    mgr = WebhookManager()
    mgr.load_plugin(ReceiverPlugin())
    mgr.run()
