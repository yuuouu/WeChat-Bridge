#!/usr/bin/env python3
"""
Bridge Qinglong Agent — 桥接青龙脚本到 WeChat Bridge Webhook。

提供如 /jj 等指令来复用青龙目录下的脚本获取实时数据。
作为 webhook_manager 插件运行：
    python3 examples/webhook_manager.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.request
from pathlib import Path

BRIDGE_BASE_URL = os.environ.get("BRIDGE_BASE_URL", "http://127.0.0.1:5200").rstrip("/")
BRIDGE_API_TOKEN = os.environ.get("BRIDGE_API_TOKEN", "").strip()

# 将青龙脚本目录加入 sys.path，以便复用里面的抓取逻辑
QINGLONG_SCRIPTS_DIR = Path("/qinglong_scripts")
if QINGLONG_SCRIPTS_DIR.exists() and str(QINGLONG_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(QINGLONG_SCRIPTS_DIR))


def _send(to_user: str, text: str) -> tuple[bool, str]:
    payload = json.dumps({"to": to_user, "text": text, "markdown": True}).encode("utf-8")
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


class QinglongPlugin:
    name = "bridge-qinglong"

    @property
    def commands(self) -> list[str]:
        return ["/jj", "/gp"]

    def get_command_specs(self) -> list[dict]:
        return [
            {
                "command": "/jj",
                "description": "获取当前金价与 BTC 简报 (对接青龙脚本)",
                "usage": "/jj",
            },
            {
                "command": "/gp",
                "description": "获取自选 A 股速报 (对接青龙脚本)",
                "usage": "/gp",
            },
        ]

    def handle(self, payload: dict) -> None:
        from_user = payload.get("from_user", "")
        command = payload.get("command", "")

        if not from_user:
            return

        if command == "/jj":
            threading.Thread(target=self._handle_jj, args=(from_user,), daemon=True).start()
        elif command == "/gp":
            threading.Thread(target=self._handle_gp, args=(from_user,), daemon=True).start()

    def _handle_gp(self, from_user: str) -> None:
        try:
            import stock_price_wechat

            msg, _ = stock_price_wechat.fetch_and_format_all()
            if msg:
                _send(from_user, f"## 📈 自选A股速报\n\n{msg}")
            else:
                _send(from_user, "❌ 获取股票数据失败，请检查网络或日志。")

        except ImportError as e:
            _send(from_user, f"❌ 无法导入股票脚本: {str(e)}")
        except Exception as e:
            _send(from_user, f"⚠️ 获取股票失败: {str(e)}")

    def _handle_jj(self, from_user: str) -> None:
        try:
            import gold_price_wechat

            msg, _ = gold_price_wechat.fetch_and_format_all()
            if msg:
                _send(from_user, f"💰 **实时金价速报**\n\n{msg}")
            else:
                _send(from_user, "❌ 所有数据源均获取失败，请检查网络。")

        except ImportError as e:
            _send(from_user, f"❌ 无法导入金价脚本: {str(e)}")
        except Exception as e:
            _send(from_user, f"⚠️ 获取金价失败: {str(e)}")

    def on_start(self) -> None:
        pass

    def on_stop(self) -> None:
        pass


PLUGIN_CLASS = QinglongPlugin

if __name__ == "__main__":
    _SCRIPT_DIR = Path(__file__).resolve().parent
    sys.path.insert(0, str(_SCRIPT_DIR))
    from webhook_manager import WebhookManager

    mgr = WebhookManager()
    mgr.load_plugin(QinglongPlugin())
    mgr.run()
