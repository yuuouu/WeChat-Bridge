"""
微信指令路由

从 bridge.py 拆分而来，通过 Mixin 注入 WeChatBridge。
处理 / 开头的交互指令（/help, /status, /pull, /ai, /clear 等）。
"""

import logging
import time
from datetime import datetime

import config as cfg
from delivery import MAX_CONSECUTIVE_SENDS

logger = logging.getLogger(__name__)

MAGIC_WEBHOOK_COMMAND_PREFIX = "__MAGIC_WEBHOOK_COMMAND__:"


class CommandMixin:
    """指令路由 Mixin，注入 WeChatBridge。"""

    def _handle_command(self, text: str, user_id: str) -> str:
        """处理 / 开头的指令。"""
        cmd = text.strip().lower()
        if cmd in ("/help", "/帮助"):
            lines = [
                "📋 可用指令：",
                "/help - 显示帮助",
                "/status - 查看 Bot 状态",
                "/pull - 拉取缓存中的未送达消息",
                "/uid - 查看自己的用户ID",
                "/retry - 重新生成AI回复",
                "/keepalive [on|off] - 开启或关闭23h断联提醒",
                "/ai [on|off] - 开启或关闭AI助手",
                "/clear - 清除 AI 对话历史",
            ]
            return "\n".join(lines)

        if cmd in ("/status", "/状态"):
            ai_config = cfg.load_config()
            summary = self.get_delivery_summary(user_id)
            uptime_seconds = int(time.time() - getattr(self, "_start_time", time.time()))
            m, s = divmod(uptime_seconds, 60)
            h, m = divmod(m, 60)
            uptime_str = f"{h}小时 {m}分钟" if h > 0 else f"{m}分钟"
            if uptime_seconds < 60:
                uptime_str = f"{uptime_seconds}秒"

            keepalive = ai_config.get("keepalive_remind_minutes", 1380)
            if keepalive > 0:
                k_hours = keepalive // 60
                k_mins = keepalive % 60
                time_fmt = f"{k_hours}时" if k_mins == 0 else f"{k_hours}时{k_mins}分"
                notify_enabled = f"✅开启 ({time_fmt})"
            else:
                notify_enabled = "❌关闭"

            webhook_cfg = self._get_webhook_config()
            if webhook_cfg["enabled"]:
                mode_text = "全部消息" if webhook_cfg["mode"] == "all_messages" else "仅未知命令"
                webhook_enabled = f"✅启用 ({mode_text})"
            elif webhook_cfg["url"]:
                webhook_enabled = "⏸️已配置未启用"
            else:
                webhook_enabled = "❌未配置"
            api_key_status = "✅已填" if ai_config.get("api_key") else "❌空缺"

            return (
                f"🤖 WeChat Bridge\n"
                f"⏳ 运行: {uptime_str}\n"
                f"📊 额度: 已连续发送 {summary['consecutive_send_count']}/{MAX_CONSECUTIVE_SENDS} 条\n"
                f"📬 缓存: {summary['pending_count']} 条\n"
                f"🚦 状态: {summary['status']}\n"
                f"🧱 原因: {summary['blocked_reason_text']}\n"
                f"⏰ 保活提醒: {notify_enabled}\n"
                f"🔗 Webhook: {webhook_enabled}\n"
                f"---\n"
                f"🤖 AI: {'✅已启用' if ai_config.get('enabled') else '❌未启用'}\n"
                f"🧠 模型: {ai_config.get('provider', 'N/A')} · {ai_config.get('model', 'N/A')}\n"
                f"🔑 API Key: {api_key_status}"
            )

        if cmd in ("/pull",):
            return "__MAGIC_PULL__"

        if cmd in ("/ai",):
            ai_config = cfg.load_config()
            if not ai_config.get("enabled"):
                return "🤖 AI 未启用。请在 Web 管理面板中开启。"
            today = datetime.now().strftime("%Y-%m-%d")
            usage = ai_config.get("usage", {}).get(today, {})
            return (
                f"🤖 AI 状态\n"
                f"厂商: {ai_config['provider']}\n"
                f"模型: {ai_config['model']}\n"
                f"今日用量: {usage.get('tokens', 0)} tokens / {usage.get('requests', 0)} 次"
            )

        if cmd in ("/clear", "/清除"):
            if self.ai_manager:
                self.ai_manager.clear_history(user_id)
            return "✅ 对话历史已清除"

        if cmd in ("/uid",):
            return f"🆔 您的用户ID:\n{user_id}"

        if cmd in ("/retry", "/重试"):
            if not self.ai_manager:
                return "🤖 AI 未启用"
            last_text = None
            for message in reversed(self.recent_messages):
                if message.get("user_id") == user_id and message.get("type") == "recv":
                    candidate = message.get("text", "")
                    if candidate and not candidate.startswith("/"):
                        last_text = candidate
                        break
            if not last_text:
                return "❌ 未找到您最近的有效对话记录"
            return f"__MAGIC_RETRY__:{last_text}"

        if cmd.startswith("/keepalive ") or cmd.startswith("/保活 "):
            parts = cmd.split()
            if len(parts) > 1:
                action = parts[1].lower()
                current = cfg.load_config()
                if action in ("on", "1", "true", "开启"):
                    current["keepalive_remind_minutes"] = 1380
                    cfg.save_config(current)
                    return "✅ 保活提醒已开启 (距最后发言23h时提醒)"
                if action in ("off", "0", "false", "关闭"):
                    current["keepalive_remind_minutes"] = 0
                    cfg.save_config(current)
                    return "❌ 保活提醒已关闭"
            return "❓ 用法: /keepalive [on|off]"

        if cmd.startswith("/ai ") or cmd.startswith("/ai状态 "):
            parts = cmd.split()
            if len(parts) > 1:
                action = parts[1].lower()
                current = cfg.load_config()
                if action in ("on", "1", "true", "开启"):
                    current["enabled"] = True
                    cfg.save_config(current)
                    msg = "✅ AI 助手已开启！"
                    if not current.get("api_key"):
                        msg += "\n⚠️ 注意：尚未配置 API Key，将无法正常回复，请登录 Web 控制台配置。"
                    return msg
                if action in ("off", "0", "false", "关闭"):
                    current["enabled"] = False
                    cfg.save_config(current)
                    return "❌ AI 助手已关闭"
            return "❓ AI状态见/status，开关请基于网页，或者: /ai [on|off]"

        if self._should_forward_unknown_command():
            return f"{MAGIC_WEBHOOK_COMMAND_PREFIX}{text}"
        return f"❓ 未知指令: {text}\n发送 /help 查看可用指令"
