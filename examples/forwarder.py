#!/usr/bin/env python3
"""
Webhook Forwarder Plugin — 将所有入站消息转发到外部服务（如 rj-inbox）。

通过 on_message 事件总线订阅全量消息，不走旧的 handle(payload) 路径。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.request
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR.parent / "app"))

from plugin_base import Plugin  # noqa: E402

logger = logging.getLogger(__name__)

FORWARD_URLS = os.environ.get("FORWARD_URLS", "http://192.168.100.1:5210/webhook").split(",")


class ForwarderPlugin(Plugin):
    """将所有入站消息转发到配置的外部 URL 列表。"""

    name = "forwarder"
    description = "将入站消息转发到外部 Webhook"

    def on_message(self, event) -> None:
        """订阅 message_received 事件，转发完整的 event.data。"""
        data = json.dumps(event.data).encode("utf-8")
        for url in FORWARD_URLS:
            url = url.strip()
            if not url:
                continue
            try:
                req = urllib.request.Request(
                    url, data=data, method="POST", headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=5):
                    pass
            except Exception as exc:
                logger.debug("转发到 %s 失败: %s", url, exc)


PLUGIN_CLASS = ForwarderPlugin
