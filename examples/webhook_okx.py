#!/usr/bin/env python3
"""
WeChat Bridge OKX Bot 交易查询插件。

响应 /okx 命令，支持账户、持仓、风控、行情、历史、健康检查等子命令。
作为 webhook_manager 插件运行：
    自动被 discover_and_register_plugins 发现并加载。

环境变量：
    OKX_BOT_API_URL    OKX Bot API 地址（默认 http://192.168.100.1:8210）
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR.parent / "app"))

from plugin_base import Plugin  # noqa: E402

logger = logging.getLogger(__name__)

OKX_BOT_API_URL = os.environ.get("OKX_BOT_API_URL", "http://192.168.100.1:8210").rstrip("/")


def _okx_api(endpoint: str, params: dict = None) -> dict:
    url = f"{OKX_BOT_API_URL}{endpoint}"
    if params:
        query = urllib.parse.urlencode(params)
        url = f"{url}?{query}"

    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if not data.get("ok"):
                raise RuntimeError(data.get("error", "未知错误"))
            return data
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络请求失败: {e}")
    except json.JSONDecodeError:
        raise RuntimeError("解析返回的 JSON 失败")


def build_okx_reply(command_args: str) -> str:
    parts = command_args.strip().split()
    sub = parts[0].lower() if parts else "help"

    try:
        if sub in ("help", "帮助"):
            return (
                "## 📊 OKX Bot 指令\n\n"
                "- `/okx 账户`：账户权益与余额\n"
                "- `/okx 持仓`：当前持仓与浮盈\n"
                "- `/okx 风控`：风控状态\n"
                "- `/okx 行情 BTC`：查询行情\n"
                "- `/okx 历史 [N]`：最近N笔交易\n"
                "- `/okx 健康`：Bot运行状态"
            )
        if sub in ("账户", "account", "acc"):
            d = _okx_api("/api/account")
            return (
                f"## 💰 OKX 账户\n\n"
                f"- **总权益**：`{d['equity_usd']:.2f}` USDT\n"
                f"- **可用余额**：`{d['available_usd']:.2f}` USDT\n"
                f"- **占用保证金**：`{d['total_margin_usd']:.2f}` USDT\n"
                f"- **实际杠杆**：`{d['actual_leverage']:.1f}`x\n"
                f"- **持仓数**：`{d['position_count']}`"
            )
        if sub in ("持仓", "pos", "positions", "position"):
            d = _okx_api("/api/positions")
            if not d.get("positions"):
                return f"## 📭 当前无持仓\n\n- **权益**：`{d['equity_usd']:.2f}` USDT"
            lines = [f"## 📊 当前持仓 ({len(d['positions'])}笔)\n\n- **权益**：`{d['equity_usd']:.2f}` USDT\n"]
            for p in d["positions"]:
                direction = "🟢 多" if p["side"] == "long" else "🔴 空"
                pnl_text = ""
                if p["unrealized_pnl"] is not None:
                    emoji = "📈" if p["unrealized_pnl"] >= 0 else "📉"
                    pnl_text = f"\n  - {emoji} **浮盈**：`{p['unrealized_pnl']:+.2f}` ({p['pnl_pct']:+.1f}%)"
                sl_tp = ""
                if p.get("sl_px"):
                    sl_tp += f"\n  - 🛑 **止损**：`{p['sl_px']:.0f}`"
                if p.get("tp_px"):
                    sl_tp += f"\n  - 🎯 **止盈**：`{p['tp_px']:.0f}`"
                lines.append(
                    f"### {direction} {p['inst_id']}\n\n"
                    f"- **数量**：`{p['size_contracts']:.4g}`张 · `{p['leverage']:.0f}`x\n"
                    f"- **价格**：入场 `{p['avg_entry_px']:.2f}` → 现价 `{p['current_px']:.2f}`"
                    f"{pnl_text}{sl_tp}\n"
                )
            return "\n".join(lines)
        if sub in ("风控", "risk"):
            d = _okx_api("/api/risk")
            cooldown = ""
            if d.get("cooldown_remaining_s", 0) > 0:
                mins = d["cooldown_remaining_s"] // 60
                cooldown = f"\n- ⏳ **冷却剩余**：`{mins}`分钟"
            return (
                f"## 🛡️ 风控状态\n\n"
                f"- **连胜/连败**：`{d['streak_wins']}` / `{d['streak_losses']}`\n"
                f"- **上笔交易**：`{d.get('last_trade_result') or '无'}`\n"
                f"- **今日 PnL**：`{d['today_realized_pnl']:+.2f}` USDT\n"
                f"- **本周 PnL**：`{d['week_realized_pnl']:+.2f}` USDT"
                f"{cooldown}"
            )
        if sub in ("行情", "market", "price"):
            inst = parts[1] if len(parts) > 1 else ""
            if not inst:
                return "## ❓ 用法\n\n- 例如：`/okx 行情 BTC`"
            d = _okx_api("/api/market", {"inst": inst})
            return f"## 📈 行情：{d['inst_id']}\n\n- **最新价**：`{d['last_price']}`"
        if sub in ("历史", "trades", "history"):
            limit = parts[1] if len(parts) > 1 else "5"
            try:
                n = max(1, min(20, int(limit)))
            except ValueError:
                n = 5
            d = _okx_api("/api/trades", {"status": "closed", "limit": str(n)})
            if not d.get("trades"):
                return "## 📭 暂无交易记录"
            lines = [f"## 📋 最近 {d['count']} 笔交易\n"]
            for t in d["trades"]:
                direction = "多" if t["side"] == "long" else "空"
                pnl = t.get("pnl_usd")
                result = "✅" if pnl is not None and pnl >= 0 else "❌"
                pnl_text = f"`{pnl:+.2f}` USDT" if pnl is not None else "N/A"
                tag = " `[模拟]`" if t.get("dry_run") else ""
                lines.append(
                    f"- {result} **{t['inst_id']}** {direction}{tag}\n"
                    f"  - **PnL**：{pnl_text} ({t.get('close_reason', '')})\n"
                    f"  - **价格**：入 `{t.get('entry_px', 0):.2f}` → 出 `{t.get('close_px', 0):.2f}`"
                )
            return "\n".join(lines)
        if sub in ("健康", "health"):
            d = _okx_api("/api/health")
            status = "✅ 健康" if d.get("healthy") else "⚠️ 异常"
            mode = "模拟盘" if d.get("mode") == "demo" else "实盘"
            dry = " `[DRY-RUN]`" if d.get("dry_run") else ""
            hb_s = d.get("heartbeat_age_s", -1)
            hb = f"`{hb_s}`秒前" if hb_s >= 0 else "无记录"
            return f"## 🤖 OKX Bot 状态\n\n- **运行模式**：{mode}{dry}\n- **心跳**：{hb}\n- **状态**：{status}"
        return f"## ❓ 未知子命令\n\n- **收到**：`{sub}`\n- 发送 `/okx help` 查看可用指令"
    except Exception as e:
        logger.error("OKX 查询失败 [%s]: %s", sub, e)
        return f"## ⚠️ OKX 查询失败\n\n- **原因**：`{str(e)}`"


class OKXPlugin(Plugin):
    """OKX 交易查询插件，响应 /okx 命令。"""

    name = "okx"
    description = "OKX 交易查询 (账户/持仓/风控/行情)"
    commands = ["/okx"]

    def get_command_specs(self):
        return [{"command": "/okx", "description": "OKX 交易查询 (账户/持仓/风控/行情/历史/健康)"}]

    def handle(self, payload):
        command = payload.get("command", "")
        if command != "/okx":
            return
        to_user = payload.get("from_user", "")
        if not to_user:
            return
        args = payload.get("args", "")
        reply = build_okx_reply(args)
        self.send_reply(to_user, reply)


PLUGIN_CLASS = OKXPlugin
