"""
消息桥接逻辑
- 收发消息抽象封装
- 维护联系人 ID 缓存
- 管理 context_token（用于回复关联）
"""

import os
import json
import logging
import time
import threading
import requests
from datetime import datetime

from ilink import ILinkClient
import db
import media

logger = logging.getLogger(__name__)

CONTACTS_FILE = os.environ.get("CONTACTS_FILE", "./data/contacts.json")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")  # 反向推送的 Webhook 地址

# 消息类型映射
MSG_TYPE_MAP = {
    1: "文本",
    2: "图片",
    3: "语音",
    4: "文件",
    5: "视频",
}


class WeChatBridge:
    """微信消息桥接器"""

    def __init__(self, client: ILinkClient):
        self.client = client
        self.contacts: dict[str, str] = {}  # user_id → 显示名
        self.context_tokens: dict[str, str] = {}  # user_id → 最新 context_token
        self._start_time = time.time()
        self.activity_tracker: dict[str, dict] = {} # user_id → 活跃状态
        from collections import deque
        self.recent_messages = deque(maxlen=50)  # 内存缓存（兼容 ag_inbox 等）
        self.ag_inbox = []  # 提供给 ag_monitor 的待填充消息队列
        self._running = False
        self._poll_thread: threading.Thread | None = None
        self.ai_manager = None  # 由 main.py 注入 AIChatManager 实例
        # 每日发送计数器（微信风控 10 条/天）
        # 结构: { user_id: { "date": "2026-04-12", "count": 5, "warned": False } }
        self._daily_send_count: dict[str, dict] = {}
        db.init_db()  # 初始化 SQLite 消息库
        self._load_contacts()

    # ── 联系人缓存 ──

    def _load_contacts(self):
        if os.path.exists(CONTACTS_FILE):
            try:
                with open(CONTACTS_FILE, "r") as f:
                    self.contacts = json.load(f)
                logger.info("已加载 %d 个联系人缓存", len(self.contacts))
            except Exception as e:
                logger.warning("加载联系人缓存失败: %s", e)
        
        ctx_file = CONTACTS_FILE.replace("contacts", "context_tokens")
        if os.path.exists(ctx_file):
            try:
                with open(ctx_file, "r") as f:
                    self.context_tokens = json.load(f)
            except Exception:
                pass
                
        act_file = CONTACTS_FILE.replace("contacts", "activity")
        if os.path.exists(act_file):
            try:
                with open(act_file, "r") as f:
                    self.activity_tracker = json.load(f)
            except Exception:
                pass

    def _save_contacts(self):
        os.makedirs(os.path.dirname(CONTACTS_FILE), exist_ok=True)
        with open(CONTACTS_FILE, "w") as f:
            json.dump(self.contacts, f, ensure_ascii=False, indent=2)
            
        ctx_file = CONTACTS_FILE.replace("contacts", "context_tokens")
        with open(ctx_file, "w") as f:
            json.dump(self.context_tokens, f, ensure_ascii=False, indent=2)
            
        act_file = CONTACTS_FILE.replace("contacts", "activity")
        with open(act_file, "w") as f:
            json.dump(self.activity_tracker, f, ensure_ascii=False, indent=2)

    def _update_contact(self, user_id: str, display_name: str = None):
        """从消息中积累联系人信息"""
        if user_id and user_id not in self.contacts:
            name = display_name or user_id.split("@")[0]
            # 若仍是原始的 o9... OpenID 格式，则进行脱敏 (通常是28位长)
            if not display_name and name.startswith("o9") and len(name) > 15:
                name = f"{name[:6]}***{name[-4:]}"
                
            self.contacts[user_id] = name
            self._save_contacts()
            logger.info("新联系人: %s → %s", user_id, name)

    def find_user_id(self, name_or_id: str) -> str | None:
        """通过名称或 ID 查找 user_id"""
        if not name_or_id:
            return None

        # 直接是 ID
        if "@im.wechat" in name_or_id:
            return name_or_id

        # 按名称搜索
        for uid, display_name in self.contacts.items():
            if name_or_id.lower() in display_name.lower():
                return uid

        return None

    def get_context_token(self, user_id: str) -> str:
        """获取与某用户对话的最新 context_token"""
        return self.context_tokens.get(user_id, "")

    # ── 消息记录 ──

    def _record_message(self, msg_dict: dict):
        """将消息同时写入内存缓存和 SQLite 持久化存储"""
        self.recent_messages.append(msg_dict)
        db.save_message(msg_dict)

    # ── 消息处理 ──

    def _extract_text(self, msg: dict) -> str:
        """从消息中提取文本内容"""
        items = msg.get("item_list") or []
        parts = []
        for item in items:
            item_type = item.get("type", 0)
            if item_type == 1:  # 文本
                text = item.get("text_item", {}).get("text", "")
                if text:
                    parts.append(text)
            elif item_type == 2:  # 图片
                logger.info("【媒体诊断】收到图片 item: %s", json.dumps(item, ensure_ascii=False))
                # iLink API 实际字段名为 image_item（非 pic_item）
                image_item = item.get("image_item") or item.get("pic_item") or {}
                pic_info = media.extract_pic_info(image_item)
                if pic_info:
                    msg_id = msg.get("msg_id", str(time.time()))
                    filepath = media.download_and_decrypt_image(
                        encrypted_query_param=pic_info["encrypted_query_param"],
                        aes_key_b64=pic_info["aes_key"],
                        msg_id=msg_id,
                    )
                    if filepath:
                        filename = os.path.basename(filepath)
                        parts.append(f"[图片:{filename}]")
                        # 将媒体路径挂到 msg 字典上，供 _record_message 使用
                        msg.setdefault("_media_paths", []).append(filename)
                        logger.info("图片已解码保存: %s", filename)
                    else:
                        parts.append("[图片:解码失败]")
                        logger.warning("图片解码失败: msg_id=%s", msg.get("msg_id"))
                else:
                    parts.append("[图片:缺少解密参数]")
                    logger.warning("pic_item 缺少解密参数: keys=%s", list(pic_item.keys()))
            elif item_type == 3:  # 语音
                voice_text = item.get("voice_item", {}).get("text", "")
                parts.append(f"[语音] {voice_text}" if voice_text else "[语音]")
            elif item_type == 4:  # 文件
                file_name = item.get("file_item", {}).get("file_name", "未知文件")
                parts.append(f"[文件: {file_name}]")
            elif item_type == 5:  # 视频
                logger.info("【媒体诊断】收到视频 item: %s", json.dumps(item, ensure_ascii=False)[:500])
                video_item = item.get("video_item") or {}
                video_info = media.extract_pic_info(video_item)  # 结构与 image_item 相同
                if video_info:
                    msg_id = msg.get("msg_id", str(time.time()))
                    filepath = media.download_and_decrypt_media(
                        encrypted_query_param=video_info["encrypted_query_param"],
                        aes_key_b64=video_info["aes_key"],
                        msg_id=msg_id,
                        media_type="video",
                    )
                    if filepath:
                        filename = os.path.basename(filepath)
                        play_len = video_item.get("play_length", 0)
                        parts.append(f"[视频:{filename}]")
                        msg.setdefault("_media_paths", []).append(filename)
                        logger.info("视频已解码保存: %s (%ds)", filename, play_len)
                    else:
                        parts.append("[视频:解码失败]")
                        logger.warning("视频解码失败: msg_id=%s", msg.get("msg_id"))
                else:
                    parts.append("[视频:缺少解密参数]")
                    logger.warning("video_item 缺少解密参数: keys=%s", list(video_item.keys()))
            else:
                parts.append(f"[未知类型:{item_type}]")

        return " ".join(parts) if parts else "[空消息]"

    def _handle_command(self, text: str, user_id: str) -> str:
        """处理 / 开头的指令"""
        cmd = text.strip().lower()
        if cmd in ("/help", "/帮助"):
            lines = [
                "📋 可用指令：",
                "/help - 显示帮助",
                "/status - 查看 Bot 状态",
                "/uid - 查看自己的用户ID",
                "/quota - 查看主动配额与用量",
                "/retry - 重新生成AI回复",
                "/keepalive [on|off] - 开启或关闭23h断联提醒",
                "/ai [on|off] - 开启或关闭AI助手",
                "/clear - 清除 AI 对话历史",
            ]
            return "\n".join(lines)
        elif cmd in ("/status", "/状态"):
            import config as cfg
            ai_config = cfg.load_config()
            
            # 计算运行时间
            uptime_seconds = int(time.time() - getattr(self, '_start_time', time.time()))
            m, s = divmod(uptime_seconds, 60)
            h, m = divmod(m, 60)
            uptime_str = f"{h}小时 {m}分钟" if h > 0 else f"{m}分钟"
            if uptime_seconds < 60: uptime_str = f"{uptime_seconds}秒"
            
            # 保活配置详情
            keepalive = ai_config.get("keepalive_remind_minutes", 1380)
            if keepalive > 0:
                k_hours = keepalive // 60
                k_mins = keepalive % 60
                time_fmt = f"{k_hours}时" if k_mins == 0 else f"{k_hours}时{k_mins}分"
                notify_enabled = f"✅开启 ({time_fmt})"
            else:
                notify_enabled = "❌关闭"
                
            webhook_enabled = "✅启用" if ai_config.get("webhook_url") else "❌未配置"
            api_key_status = "✅已填" if ai_config.get("api_key") else "❌空缺"
            
            # 配额情况（当天）
            quota_used = 0
            if user_id in self._daily_send_count:
                if self._daily_send_count[user_id].get("date") == datetime.now().strftime("%Y-%m-%d"):
                    quota_used = self._daily_send_count[user_id].get("count", 0)
            
            return (
                f"🤖 WeChat Bridge\n"
                f"⏳ 运行: {uptime_str}\n"
                f"📊 额度: 剩余 {10 - quota_used} 次主动推送\n"
                f"⏰ 保活提醒: {notify_enabled}\n"
                f"🔗 Webhook: {webhook_enabled}\n"
                f"---\n"
                f"🤖 AI: {'✅已启用' if ai_config.get('enabled') else '❌未启用'}\n"
                f"🧠 模型: {ai_config.get('provider', 'N/A')} · {ai_config.get('model', 'N/A')}\n"
                f"🔑 API Key: {api_key_status}"
            )
        elif cmd in ("/ai",):
            import config as cfg
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
        elif cmd in ("/clear", "/清除"):
            if self.ai_manager:
                self.ai_manager.clear_history(user_id)
            return "✅ 对话历史已清除"
        elif cmd in ("/uid",):
            return f"🆔 您的用户ID:\n{user_id}"
        elif cmd in ("/quota", "/配额"):
            count = 1  # 当前这条回复算 1 条
            if user_id in self._daily_send_count:
                self._daily_send_count[user_id]["count"] = 1
            return f"📊 配额信息\n已接收{count}条信息，现已重置，可继续接收9条信息"
        elif cmd in ("/retry", "/重试"):
            if not self.ai_manager:
                return "🤖 AI 未启用"
            last_text = None
            for m in reversed(self.recent_messages):
                if m.get("user_id") == user_id and m.get("type") == "recv":
                    t = m.get("text", "")
                    if t and not t.startswith("/"):
                        last_text = t
                        break
            if not last_text:
                return "❌ 未找到您最近的有效对话记录"
            return f"__MAGIC_RETRY__:{last_text}"
        elif cmd.startswith("/keepalive ") or cmd.startswith("/保活 "):
            parts = cmd.split()
            if len(parts) > 1:
                action = parts[1].lower()
                import config as cfg
                import json
                c = cfg.load_config()
                if action in ("on", "1", "true", "开启"):
                    c["keepalive_remind_minutes"] = 1380
                    with cfg._config_lock:
                        with open(cfg.CONFIG_FILE, "w", encoding="utf-8") as f:
                            json.dump(c, f, indent=2, ensure_ascii=False)
                    return "✅ 保活提醒已开启 (距最后发言23h时提醒)"
                elif action in ("off", "0", "false", "关闭"):
                    c["keepalive_remind_minutes"] = 0
                    with cfg._config_lock:
                        with open(cfg.CONFIG_FILE, "w", encoding="utf-8") as f:
                            json.dump(c, f, indent=2, ensure_ascii=False)
                    return "❌ 保活提醒已关闭"
            return "❓ 用法: /keepalive [on|off]"
        elif cmd.startswith("/ai ") or cmd.startswith("/ai状态 "):
            parts = cmd.split()
            if len(parts) > 1:
                action = parts[1].lower()
                import config as cfg
                import json
                c = cfg.load_config()
                if action in ("on", "1", "true", "开启"):
                    c["enabled"] = True
                    with cfg._config_lock:
                        with open(cfg.CONFIG_FILE, "w", encoding="utf-8") as f:
                            json.dump(c, f, indent=2, ensure_ascii=False)
                    msg = "✅ AI 助手已开启！"
                    if not c.get("api_key"):
                        msg += "\n⚠️ 注意：尚未配置 API Key，将无法正常回复，请登录 Web 控制台配置。"
                    return msg
                elif action in ("off", "0", "false", "关闭"):
                    c["enabled"] = False
                    with cfg._config_lock:
                        with open(cfg.CONFIG_FILE, "w", encoding="utf-8") as f:
                            json.dump(c, f, indent=2, ensure_ascii=False)
                    return "❌ AI 助手已关闭"
            return "❓ AI状态见/status，开关请基于网页，或者: /ai [on|off]"
        else:
            return f"❓ 未知指令: {text}\n发送 /help 查看可用指令"


    def _trigger_webhook(self, from_user: str, from_name: str, text: str, msg: dict):
        """将消息通过标准 Webhook 转发给外部系统 (如 FastGPT, Dify, 自建服务)"""
        if not WEBHOOK_URL:
            return
        payload = {
            "source": "wechat-bridge",
            "from_user": from_user,
            "from_name": from_name,
            "text": text,
            "msg_id": msg.get("msg_id", ""),
            "timestamp": int(time.time()),
            "msg_type": msg.get("message_type")
        }
        try:
            requests.post(WEBHOOK_URL, json=payload, timeout=5)
            logger.info("已触发外部 Webhook: %s", WEBHOOK_URL)
        except Exception as e:
            logger.warning("外部 Webhook 触发失败: %s", e)

    def process_message(self, msg: dict):
        """处理单条收到的消息"""
        logger.debug("RAW INBOUND: %s", json.dumps(msg, ensure_ascii=False))
        msg_type = msg.get("message_type", 0)

        # message_type=1 表示用户发来的消息，2 表示 bot 自己发的
        if msg_type != 1:
            return

        from_user = msg.get("from_user_id", "")
        context_token = msg.get("context_token", "")
        text = self._extract_text(msg)

        # 提取显示名（如果有 nickname 字段）
        display_name = msg.get("from_user_nickname") or msg.get("from_user_name")
        self._update_contact(from_user, display_name)

        # 缓存 context_token
        if from_user and context_token:
            should_save = False
            if self.context_tokens.get(from_user) != context_token:
                self.context_tokens[from_user] = context_token
                should_save = True
                
            # 记录用户活跃时间以防断联
            now = time.time()
            if text and not text.startswith("/"):
                self.activity_tracker[from_user] = {
                    "last_receive_time": now,
                    "reminded": False
                }
                # 用户发消息了，重置当日发送计数器
                today = datetime.now().strftime("%Y-%m-%d")
                if from_user in self._daily_send_count:
                    entry = self._daily_send_count[from_user]
                    if entry.get("date") == today:
                        entry["count"] = 0
                        entry["warned"] = False
                        logger.info("用户 [%s] 回复了消息，重置当日发送计数器", from_user[:20])
                should_save = True
                
            if should_save:
                self._save_contacts()

        # 获取显示名
        from_name = self.contacts.get(from_user, from_user.split("@")[0])

        logger.info("收到消息 [%s]: %s", from_name, text[:100])
        
        media_paths = msg.get("_media_paths", [])
        self._record_message({
            "type": "recv",
            "contact": from_name,
            "user_id": from_user,
            "text": text,
            "time": int(time.time()),
            "msg_id": msg.get("msg_id", str(time.time())),
            "media": media_paths[0] if media_paths else None,
        })

        # 加入 ag_inbox 供本地客户端同步到输入框
        if text and not text.startswith("/"):
            self.ag_inbox.append({"from": from_name, "text": text})

        # 指令路由（以 "/" 开头的消息）
        if text.startswith("/"):
            import uuid
            cmd_reply = self._handle_command(text, from_user)
            if cmd_reply:
                # 拦截魔法前缀进行 Retry 触发
                if cmd_reply.startswith("__MAGIC_RETRY__:"):
                    retry_text = cmd_reply[len("__MAGIC_RETRY__:"):]
                    if self.ai_manager:
                        import threading
                        def _async_retry_worker():
                            try:
                                self.client.send_text(from_user, "🔄 正在为您重新生成回答...", context_token)
                                ai_reply = self.ai_manager.chat(from_user, retry_text)
                                if ai_reply:
                                    self.client.send_text(from_user, ai_reply, context_token)
                                    self._record_message({
                                        "type": "send",
                                        "contact": from_name,
                                        "user_id": from_user,
                                        "text": ai_reply,
                                        "time": int(time.time()),
                                        "msg_id": "ai_" + uuid.uuid4().hex[:8]
                                    })
                            except Exception as e:
                                logger.error("Retry 重试失败: %s", e)
                        threading.Thread(target=_async_retry_worker, daemon=True).start()
                    return

                try:
                    self._record_message({
                        "type": "send",
                        "contact": from_name,
                        "user_id": from_user,
                        "text": cmd_reply,
                        "time": int(time.time()),
                        "msg_id": "cmd_" + uuid.uuid4().hex[:8]
                    })
                    self.client.send_text(from_user, cmd_reply, context_token)
                except Exception as e:
                    logger.error("指令回复失败: %s", e)
            return  # 指令消息不触发 Webhook 和 AI 处理

        # 触发标准反向 Webhook
        self._trigger_webhook(from_user, from_name, text, msg)

        # AI 对话分发（异步线程，防止阻塞微信长轮询）
        if self.ai_manager and text and not text.startswith("/"):
            import threading
            def _async_ai_worker(uid, name, msg_text, c_token):
                try:
                    ai_reply = self.ai_manager.chat(uid, msg_text)
                    if ai_reply:
                        self.client.send_text(uid, ai_reply, c_token)
                        self._record_message({
                            "type": "send",
                            "contact": name,
                            "user_id": uid,
                            "text": f"🤖 {ai_reply}",
                            "time": int(time.time()),
                            "msg_id": f"ai_{time.time()}"
                        })
                        logger.info("AI 已异步回复 [%s]: %s", name, ai_reply[:80])
                except Exception as e:
                    logger.error("AI 后台回复失败 [%s]: %s", name, e)
                    try:
                        self.client.send_text(uid, f"⚠️ AI 响应异常: {str(e)[:50]}", c_token)
                    except:
                        pass

            threading.Thread(
                target=_async_ai_worker,
                args=(from_user, from_name, text, context_token),
                daemon=True
            ).start()

    # ── 发送消息 ──

    def _increment_send_count(self, user_id: str) -> int:
        """增加当日发送计数，返回新的计数值"""
        today = datetime.now().strftime("%Y-%m-%d")
        entry = self._daily_send_count.get(user_id, {})
        if entry.get("date") != today:
            entry = {"date": today, "count": 0, "warned": False}
        entry["count"] += 1
        self._daily_send_count[user_id] = entry
        return entry["count"]


    def send(self, to: str, text: str) -> dict:
        """
        发送消息的高级接口
        to: 可以是 user_id，也可以是联系人名称
        """
        user_id = self.find_user_id(to)
        if not user_id:
            if not to:
                return {"ok": False, "error": "缺少收件人。iLink 限制：对方需先给你发一条消息，系统才能获取其 user_id"}
            return {"ok": False, "error": f"找不到联系人「{to}」。对方需先给你发过消息才会出现在联系人列表中"}

        context_token = self.get_context_token(user_id)
        
        # 检查当日发送计数，到 10 条时直接附带在第10条消息末尾
        count = self._increment_send_count(user_id)
        contact_name = self.contacts.get(user_id, to)
        logger.info("准备向 [%s] 发送消息 (%d/10 当日)", contact_name, count)

        entry = self._daily_send_count.get(user_id, {})
        if count >= 10 and not entry.get("warned"):
            entry["warned"] = True
            text += (
                "\n\n━━━━━━━━━━━━━━\n"
                "⚠️【系统提醒】bot已连续发送 10 条通知，已触发微信接收上限！触发了微信风控自动屏蔽，后续消息无法发送。\n"
                "👉请回复任意内容解除限制恢复接收！"
            )
            logger.warning("将风控提醒附着在第 10 条消息末尾发出")

        try:
            result = self.client.send_text(user_id, text, context_token)
            
            self._record_message({
                "type": "send",
                "contact": contact_name,
                "user_id": user_id,
                "text": text,
                "time": int(time.time()),
                "msg_id": f"s_{time.time()}"
            })

            return {"ok": True, "result": result}
        except Exception as e:
            logger.error("发送消息失败(但可能已送达): %s", e)
            return {"ok": False, "error": str(e)}
    def send_typing(self, to: str) -> dict:
        """
        发送"正在输入"状态的高级接口
        to: 可以是 user_id，也可以是联系人名称
        """
        user_id = self.find_user_id(to)
        if not user_id:
            return {"ok": False, "error": f"找不到联系人: {to}"}

        context_token = self.get_context_token(user_id)
        try:
            result = self.client.send_typing(user_id, context_token)
            return {"ok": True, "result": result}
        except Exception as e:
            logger.error("发送 typing 状态失败: %s", e)
            return {"ok": False, "error": str(e)}

    def send_image(self, to: str, file_data: bytes) -> dict:
        """
        发送图片的高级接口
        to: 可以是 user_id，也可以是联系人名称
        file_data: 原始图片字节
        """
        user_id = self.find_user_id(to)
        if not user_id:
            return {"ok": False, "error": f"找不到联系人「{to}」。对方需先给你发过消息才会出现在联系人列表中"}

        context_token = self.get_context_token(user_id)
        try:
            result = self.client.send_image(user_id, file_data, context_token)

            contact_name = self.contacts.get(user_id, to)
            
            # 在本地保存发送的图片，以便 Web 端回显
            import hashlib
            import media
            import os
            media._ensure_media_dir()
            filename = f"out_img_{int(time.time())}_{hashlib.md5(file_data).hexdigest()[:8]}.jpg"
            save_path = os.path.join(media.MEDIA_DIR, filename)
            with open(save_path, "wb") as f:
                f.write(file_data)
                
            self._record_message({
                "type": "send",
                "contact": contact_name,
                "user_id": user_id,
                "text": f"[图片:{filename}]",
                "time": int(time.time()),
                "msg_id": f"s_{time.time()}",
                "media": filename
            })

            return {"ok": True, "result": result}
        except Exception as e:
            logger.error("发送图片失败: %s", e)
            return {"ok": False, "error": str(e)}

    # ── 长轮询主循环 ──

    def _poll_loop(self):
        """长轮询消息接收主循环"""
        logger.info("消息轮询循环已启动")
        consecutive_errors = 0

        while self._running:
            if not self.client.logged_in:
                logger.info("未登录，等待扫码...")
                time.sleep(5)
                continue

            try:
                msgs = self.client.get_updates(timeout=35)
                consecutive_errors = 0  # 成功则重置错误计数

                for msg in msgs:
                    try:
                        self.process_message(msg)
                    except Exception as e:
                        logger.error("处理消息异常: %s", e, exc_info=True)

            except RuntimeError as e:
                # Token 失效，等待重新登录
                logger.warning("需要重新登录: %s", e)
                time.sleep(10)
            except Exception as e:
                consecutive_errors += 1
                wait = min(consecutive_errors * 5, 60)
                logger.warning("轮询异常 (连续第%d次): %s, %d秒后重试",
                               consecutive_errors, e, wait)
                time.sleep(wait)

        logger.info("消息轮询循环已停止")

    def _keepalive_loop(self):
        """心跳/断线提醒检查循环"""
        logger.info("断线提醒检查循环已启动")
        import config as cfg
        while self._running:
            try:
                time.sleep(60)
                if not self.client.logged_in:
                    continue
                    
                cfg_data = cfg.load_config()
                remind_minutes = cfg_data.get("keepalive_remind_minutes", 0)
                
                if not remind_minutes or remind_minutes <= 0:
                    continue
                    
                remind_seconds = remind_minutes * 60
                deadline_seconds = 24 * 3600  # 24小时硬性上限
                now = time.time()
                
                for user_id, act in list(self.activity_tracker.items()):
                    last_time = act.get("last_receive_time", 0)
                    if last_time == 0:
                        continue
                        
                    elapsed = now - last_time
                    
                    # 已超过24h，不再提醒（通道已失效）
                    if elapsed >= deadline_seconds:
                        continue
                    
                    # 到达提醒阈值且尚未提醒
                    if not act.get("reminded") and elapsed >= remind_seconds:
                        act["reminded"] = True
                        self._save_contacts()
                        
                        remaining = deadline_seconds - elapsed
                        remain_h = int(remaining // 3600)
                        remain_m = int((remaining % 3600) // 60)
                        
                        self.send(user_id,
                            f"【⏰ 通道保活提醒】\n"
                            f"您已超过 {remind_minutes // 60} 小时 {remind_minutes % 60} 分钟未发送消息。\n"
                            f"微信通道将在约 {remain_h}h{remain_m}m 后自动休眠。\n"
                            f"回复任意内容即可保持连接。"
                        )
                        
            except Exception as e:
                logger.error("保活检查异常: %s", e)

    def start(self):
        """启动消息轮询"""
        if self._running:
            return
        self._running = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        
        self._keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
        self._keepalive_thread.start()
        logger.info("WeChatBridge 已启动")

    def stop(self):
        """停止消息轮询"""
        self._running = False
        if getattr(self, '_poll_thread', None):
            self._poll_thread.join(timeout=10)
        if getattr(self, '_keepalive_thread', None):
            self._keepalive_thread.join(timeout=2)
        logger.info("WeChatBridge 已停止")
