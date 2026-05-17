#!/usr/bin/env python3
"""
Bridge Qinglong Agent — 桥接青龙脚本到 WeChat Bridge Webhook。

提供 /jj、/gp 等指令来复用青龙目录下的脚本获取实时数据。
作为 webhook_manager 插件运行：
    自动被 discover_and_register_plugins 发现并加载。
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR.parent / "app"))

from plugin_base import Plugin  # noqa: E402

logger = logging.getLogger(__name__)

# 将青龙脚本目录加入 sys.path，以便复用里面的抓取逻辑
QINGLONG_SCRIPTS_DIR = Path("/qinglong_scripts")
if QINGLONG_SCRIPTS_DIR.exists() and str(QINGLONG_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(QINGLONG_SCRIPTS_DIR))


class QinglongPlugin(Plugin):
    """桥接青龙脚本的数据查询插件。"""

    name = "bridge-qinglong"
    description = "青龙脚本数据查询 (/jj 金价, /gp 股票)"
    commands = ["/jj", "/gp"]

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
                self.send_reply(from_user, f"## 📈 自选A股速报\n\n{msg}")
            else:
                self.send_reply(from_user, "❌ 获取股票数据失败，请检查网络或日志。")

        except ImportError as e:
            self.send_reply(from_user, f"❌ 无法导入股票脚本: {str(e)}")
        except Exception as e:
            self.send_reply(from_user, f"⚠️ 获取股票失败: {str(e)}")

    def _handle_jj(self, from_user: str) -> None:
        try:
            import gold_price_wechat

            msg, _ = gold_price_wechat.fetch_and_format_all()
            if msg:
                self.send_reply(from_user, f"💰 **实时金价速报**\n\n{msg}")
            else:
                self.send_reply(from_user, "❌ 所有数据源均获取失败，请检查网络。")

        except ImportError as e:
            self.send_reply(from_user, f"❌ 无法导入金价脚本: {str(e)}")
        except Exception as e:
            self.send_reply(from_user, f"⚠️ 获取金价失败: {str(e)}")


PLUGIN_CLASS = QinglongPlugin

if __name__ == "__main__":
    from webhook_manager import WebhookManager  # noqa: E402

    mgr = WebhookManager()
    mgr.load_plugin(QinglongPlugin())
    mgr.run()
