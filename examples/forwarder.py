#!/usr/bin/env python3
"""
Webhook Forwarder Plugin — 将所有 Webhook 流量转发到其他服务（如 rj-inbox）。
"""

from __future__ import annotations

import json
import os
import urllib.request

FORWARD_URLS = os.environ.get("FORWARD_URLS", "http://192.168.100.1:5210/webhook").split(",")


class ForwarderPlugin:
    name = "forwarder"

    @property
    def commands(self) -> list[str]:
        return []

    def get_command_specs(self) -> list[dict]:
        return []

    def has_session(self, user_id: str) -> bool:
        return False

    def handle(self, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
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
            except Exception:
                pass

    def on_start(self) -> None:
        pass

    def on_stop(self) -> None:
        pass


PLUGIN_CLASS = ForwarderPlugin
