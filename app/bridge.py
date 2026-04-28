"""
消息桥接逻辑
- 收发消息抽象封装
- 维护联系人 ID 缓存
- 管理 context_token（用于回复关联）
- 管理消息缓存会话与 /pull 补拉
"""

import json
import logging
import os
import threading
import time
import uuid
from collections import deque

import requests

import config as cfg
import db
import media
from commands import CommandMixin, MAGIC_WEBHOOK_COMMAND_PREFIX
from delivery import DeliveryMixin, MAX_CONSECUTIVE_SENDS, WINDOW_DEADLINE_SECONDS, PULL_CHUNK_LIMIT  # noqa: F401 — re-export for backward compat
from ilink import ILinkClient
from keepalive import KeepaliveMixin

logger = logging.getLogger(__name__)

DATA_BASE = os.environ.get("DATA_DIR", "./data")

MSG_TYPE_MAP = {
    1: "文本",
    2: "图片",
    3: "语音",
    4: "文件",
    5: "视频",
}


class WeChatBridge(DeliveryMixin, CommandMixin, KeepaliveMixin):
    """微信消息桥接器。"""

    def __init__(self, client: ILinkClient):
        self.client = client
        self.contacts: dict[str, str] = {}
        self.context_tokens: dict[str, str] = {}
        self._start_time = time.time()
        self.activity_tracker: dict[str, dict] = {}
        self.recent_messages = deque(maxlen=50)
        self.ag_inbox = []
        self._running = False
        self._poll_thread: threading.Thread | None = None
        self.ai_manager = None
        self._consecutive_send_count: dict[str, dict] = {}
        self._outbound_lock = threading.Lock()
        self._setup_data_dir()
        self._load_contacts()

    def _setup_data_dir(self, bot_id: str = None):
        """根据 bot_id 设置数据目录，实现多账号数据隔离。"""
        bid = bot_id or self.client.get_bot_id()
        if bid:
            self._data_dir = os.path.join(DATA_BASE, bid)
            logger.info("数据目录按 bot_id 隔离: %s", self._data_dir)
        else:
            self._data_dir = DATA_BASE
            logger.info("未检测到 bot_id，使用默认数据目录: %s", self._data_dir)
        os.makedirs(self._data_dir, exist_ok=True)

        self._contacts_file = os.path.join(self._data_dir, "contacts.json")
        db.init_db(os.path.join(self._data_dir, "messages.db"))
        media.set_media_dir(os.path.join(self._data_dir, "media"))

    # ── 联系人缓存 ──

    def _load_contacts(self):
        if os.path.exists(self._contacts_file):
            try:
                with open(self._contacts_file, "r", encoding="utf-8") as fh:
                    self.contacts = json.load(fh)
                logger.info("已加载 %d 个联系人缓存", len(self.contacts))
            except Exception as exc:
                logger.warning("加载联系人缓存失败: %s", exc)

        ctx_file = os.path.join(self._data_dir, "context_tokens.json")
        if os.path.exists(ctx_file):
            try:
                with open(ctx_file, "r", encoding="utf-8") as fh:
                    self.context_tokens = json.load(fh)
            except Exception:
                pass

        act_file = os.path.join(self._data_dir, "activity.json")
        if os.path.exists(act_file):
            try:
                with open(act_file, "r", encoding="utf-8") as fh:
                    self.activity_tracker = json.load(fh)
            except Exception:
                pass

    def _save_contacts(self):
        os.makedirs(self._data_dir, exist_ok=True)
        with open(self._contacts_file, "w", encoding="utf-8") as fh:
            json.dump(self.contacts, fh, ensure_ascii=False, indent=2)

        ctx_file = os.path.join(self._data_dir, "context_tokens.json")
        with open(ctx_file, "w", encoding="utf-8") as fh:
            json.dump(self.context_tokens, fh, ensure_ascii=False, indent=2)

        act_file = os.path.join(self._data_dir, "activity.json")
        with open(act_file, "w", encoding="utf-8") as fh:
            json.dump(self.activity_tracker, fh, ensure_ascii=False, indent=2)

    def _update_contact(self, user_id: str, display_name: str = None):
        """从消息中积累联系人信息。"""
        if user_id and user_id not in self.contacts:
            name = display_name or user_id.split("@")[0]
            if not display_name and name.startswith("o9") and len(name) > 15:
                name = f"{name[:6]}***{name[-4:]}"
            self.contacts[user_id] = name
            self._save_contacts()
            logger.info("新联系人: %s → %s", user_id, name)

    def _get_webhook_config(self) -> dict:
        current = cfg.load_config()
        enabled = bool(current.get("webhook_enabled")) and bool(current.get("webhook_url"))
        return {
            "enabled": enabled,
            "url": current.get("webhook_url", "").strip(),
            "mode": current.get("webhook_mode", "unknown_command"),
            "timeout": current.get("webhook_timeout", 5),
        }

    def _should_forward_unknown_command(self) -> bool:
        webhook_cfg = self._get_webhook_config()
        return webhook_cfg["enabled"] and webhook_cfg["mode"] in ("unknown_command", "all_messages")

    def _should_forward_message(self, *, is_command: bool) -> bool:
        webhook_cfg = self._get_webhook_config()
        if not webhook_cfg["enabled"]:
            return False
        if webhook_cfg["mode"] == "all_messages":
            return True
        return is_command and webhook_cfg["mode"] == "unknown_command"

    def find_user_id(self, name_or_id: str) -> str | None:
        """通过名称或 ID 查找 user_id。"""
        if not name_or_id:
            return None
        if "@im.wechat" in name_or_id:
            return name_or_id
        for uid, display_name in self.contacts.items():
            if name_or_id.lower() in display_name.lower():
                return uid
        return None

    def get_context_token(self, user_id: str) -> str:
        return self.context_tokens.get(user_id, "")

    def _contact_name(self, user_id: str, fallback: str = "") -> str:
        return self.contacts.get(user_id, fallback or user_id.split("@")[0])

    # ── 持久化与状态 ──

    def _record_message(self, msg_dict: dict):
        """将消息同时写入内存缓存和 SQLite 持久化存储。"""
        self.recent_messages.append(msg_dict)
        db.save_message(msg_dict)

    def _save_outbound_image(self, file_data: bytes) -> str:
        media._ensure_media_dir()
        filename = f"out_img_{int(time.time())}_{uuid.uuid4().hex[:8]}.jpg"
        save_path = os.path.join(media.MEDIA_DIR, filename)
        with open(save_path, "wb") as fh:
            fh.write(file_data)
        return filename

    # ── 消息处理 ──

    def _extract_text(self, msg: dict) -> str:
        """从消息中提取文本内容。"""
        items = msg.get("item_list") or []
        parts = []
        for item in items:
            item_type = item.get("type", 0)
            if item_type == 1:
                text = item.get("text_item", {}).get("text", "")
                if text:
                    parts.append(text)
            elif item_type == 2:
                logger.info("【媒体诊断】收到图片 item: %s", json.dumps(item, ensure_ascii=False))
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
                        msg.setdefault("_media_paths", []).append(filename)
                        logger.info("图片已解码保存: %s", filename)
                    else:
                        parts.append("[图片:解码失败]")
                        logger.warning("图片解码失败: msg_id=%s", msg.get("msg_id"))
                else:
                    parts.append("[图片:缺少解密参数]")
                    logger.warning("pic_item 缺少解密参数: keys=%s", list(image_item.keys()))
            elif item_type == 3:
                voice_text = item.get("voice_item", {}).get("text", "")
                parts.append(f"[语音] {voice_text}" if voice_text else "[语音]")
            elif item_type == 4:
                file_name = item.get("file_item", {}).get("file_name", "未知文件")
                parts.append(f"[文件: {file_name}]")
            elif item_type == 5:
                logger.info("【媒体诊断】收到视频 item: %s", json.dumps(item, ensure_ascii=False)[:500])
                video_item = item.get("video_item") or {}
                video_info = media.extract_pic_info(video_item)
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

    def _trigger_webhook(self, from_user: str, from_name: str, text: str, msg: dict, *, is_command: bool = False):
        """将消息通过标准 Webhook 转发给外部系统。"""
        webhook_cfg = self._get_webhook_config()
        if not self._should_forward_message(is_command=is_command):
            return
        command_name = ""
        command_args = ""
        if is_command and text.startswith("/"):
            parts = text.strip().split(maxsplit=1)
            command_name = parts[0]
            command_args = parts[1] if len(parts) > 1 else ""
        payload = {
            "source": "wechat-bridge",
            "from_user": from_user,
            "from_name": from_name,
            "text": text,
            "msg_id": msg.get("msg_id", ""),
            "timestamp": int(time.time()),
            "msg_type": msg.get("message_type"),
            "is_command": is_command,
            "command": command_name,
            "args": command_args,
        }
        try:
            requests.post(webhook_cfg["url"], json=payload, timeout=webhook_cfg["timeout"])
            logger.info("已触发外部 Webhook: %s", webhook_cfg["url"])
        except Exception as exc:
            logger.warning("外部 Webhook 触发失败: %s", exc)

    def process_message(self, msg: dict):
        """处理单条收到的消息。"""
        logger.debug("RAW INBOUND: %s", json.dumps(msg, ensure_ascii=False))
        if msg.get("message_type", 0) != 1:
            return

        from_user = msg.get("from_user_id", "")
        context_token = msg.get("context_token", "")
        text = self._extract_text(msg)

        display_name = msg.get("from_user_nickname") or msg.get("from_user_name")
        self._update_contact(from_user, display_name)

        should_save = False
        if from_user and context_token and self.context_tokens.get(from_user) != context_token:
            self.context_tokens[from_user] = context_token
            should_save = True

        if from_user and text:
            now_ts = int(time.time())
            self.activity_tracker[from_user] = {
                "last_receive_time": now_ts,
                "reminded": False,
            }
            self._mark_user_recovered(from_user, now_ts)
            logger.info("用户 [%s] 有新入站消息，已恢复发送窗口", from_user[:20])
            should_save = True

        if should_save:
            self._save_contacts()

        from_name = self._contact_name(from_user)
        logger.info("收到消息 [%s]: %s", from_name, text[:100])

        media_paths = msg.get("_media_paths", [])
        self._record_message(
            {
                "type": "recv",
                "contact": from_name,
                "user_id": from_user,
                "text": text,
                "time": int(time.time()),
                "msg_id": msg.get("msg_id", str(time.time())),
                "media": media_paths[0] if media_paths else None,
            }
        )

        if text and not text.startswith("/"):
            self.ag_inbox.append({"from": from_name, "text": text})

        if text.startswith("/"):
            cmd_reply = self._handle_command(text, from_user)
            if cmd_reply == "__MAGIC_PULL__":
                def _async_pull_worker():
                    result = self.pull_pending_messages(from_user)
                    if result.get("empty"):
                        self.send(from_user, result["message"], source="system")
                    elif not result.get("ok") and result.get("remaining", 0) > 0:
                        logger.warning("缓存补拉中断，剩余 %d 条待发送", result["remaining"])

                threading.Thread(target=_async_pull_worker, daemon=True).start()
                return

            if cmd_reply.startswith("__MAGIC_RETRY__:"):
                retry_text = cmd_reply[len("__MAGIC_RETRY__:"):]

                def _async_retry_worker():
                    try:
                        self.send(from_user, "🔄 正在为您重新生成回答...", source="system")
                        ai_reply = self.ai_manager.chat(from_user, retry_text) if self.ai_manager else ""
                        if ai_reply:
                            result = self.send(from_user, ai_reply, source="ai")
                            if not result.get("ok"):
                                logger.error("Retry 重试回复失败: %s", result.get("error"))
                    except Exception as exc:
                        logger.error("Retry 重试失败: %s", exc)

                threading.Thread(target=_async_retry_worker, daemon=True).start()
                return

            if cmd_reply.startswith(MAGIC_WEBHOOK_COMMAND_PREFIX):
                def _async_command_webhook_worker():
                    self._trigger_webhook(from_user, from_name, text, msg, is_command=True)

                threading.Thread(target=_async_command_webhook_worker, daemon=True).start()
                return

            result = self.send(from_user, cmd_reply, source="command")
            if not result.get("ok"):
                logger.error("指令回复失败: %s", result.get("error"))
            return

        self._trigger_webhook(from_user, from_name, text, msg)

        if self.ai_manager and text and not text.startswith("/"):
            def _async_ai_worker(uid, msg_text):
                try:
                    ai_reply = self.ai_manager.chat(uid, msg_text)
                    if ai_reply:
                        result = self.send(uid, f"🤖 {ai_reply}", source="ai")
                        if not result.get("ok"):
                            logger.error("AI 后台回复失败 [%s]: %s", uid[:16], result.get("error"))
                        else:
                            logger.info("AI 已异步回复 [%s]: %s", uid[:16], ai_reply[:80])
                except Exception as exc:
                    logger.error("AI 后台回复失败 [%s]: %s", uid[:16], exc)
                    try:
                        self.send(uid, f"⚠️ AI 响应异常: {str(exc)[:50]}", source="system")
                    except Exception:
                        pass

            threading.Thread(
                target=_async_ai_worker,
                args=(from_user, text),
                daemon=True,
            ).start()

    # ── 发送消息 ──

    def send(self, to: str, text: str, *, source: str = "api", title: str = "") -> dict:
        """
        发送消息的高级接口。
        to: 可以是 user_id，也可以是联系人名称。
        """
        user_id = self.find_user_id(to)
        if not user_id:
            if not to:
                return {"ok": False, "error": "缺少收件人。iLink 限制：对方需先给你发一条消息，系统才能获取其 user_id"}
            return {"ok": False, "error": f"找不到联系人「{to}」。对方需先给你发过消息才会出现在联系人列表中"}

        return self._send_resolved(
            user_id=user_id,
            contact_name=self._contact_name(user_id, to),
            text=text,
            context_token=self.get_context_token(user_id),
            source=source,
            title=title,
            allow_buffer=True,
            rotate_session_on_warn=True,
            record_timeline=True,
        )

    def send_typing(self, to: str) -> dict:
        """发送"正在输入"状态。"""
        user_id = self.find_user_id(to)
        if not user_id:
            return {"ok": False, "error": f"找不到联系人: {to}"}

        context_token = self.get_context_token(user_id)
        if self._outbound_lock.locked():
            return {"ok": True, "skipped": True, "reason": "busy"}
        try:
            with self._outbound_lock:
                result = self.client.send_typing(user_id, context_token)
            return {"ok": True, "result": result}
        except Exception as exc:
            logger.error("发送 typing 状态失败: %s", exc)
            return {"ok": False, "error": str(exc)}

    def send_image(self, to: str, file_data: bytes) -> dict:
        """发送图片消息。"""
        user_id = self.find_user_id(to)
        if not user_id:
            return {"ok": False, "error": f"找不到联系人「{to}」。对方需先给你发过消息才会出现在联系人列表中"}

        context_token = self.get_context_token(user_id)
        contact_name = self._contact_name(user_id, to)
        filename = self._save_outbound_image(file_data)
        image_text = f"[图片:{filename}]"

        with self._outbound_lock:
            now_ts = int(time.time())
            state = self._get_delivery_state(user_id)

            if self._is_window_expired(user_id, state, now_ts):
                return self._buffer_message(
                    user_id=user_id,
                    contact_name=contact_name,
                    text=image_text,
                    reason="window_24h",
                    source="image",
                    media_name=filename,
                )

            current_count = int(state.get("consecutive_send_count") or 0)
            active_session_id = state.get("active_overflow_session_id")
            if current_count >= MAX_CONSECUTIVE_SENDS:
                return self._buffer_message(
                    user_id=user_id,
                    contact_name=contact_name,
                    text=image_text,
                    reason="quota_10",
                    source="image",
                    media_name=filename,
                )

            next_count = current_count + 1
            warning_session_id = active_session_id
            if next_count == MAX_CONSECUTIVE_SENDS:
                session = self._start_new_overflow_session(user_id, "quota_10")
                warning_session_id = session["id"]

            try:
                result = self.client.send_image(user_id, file_data, context_token)
            except Exception as exc:
                logger.error("发送图片失败: %s", exc)
                if self._is_window_limit_error(exc):
                    limit_reason = self._resolve_limit_error_reason(
                        user_id=user_id,
                        state=state,
                        now_ts=now_ts,
                        next_count=next_count,
                        warning_appended=next_count == MAX_CONSECUTIVE_SENDS,
                    )
                    return self._buffer_message(
                        user_id=user_id,
                        contact_name=contact_name,
                        text=image_text,
                        reason=limit_reason,
                        source="image",
                        media_name=filename,
                    )
                return {"ok": False, "error": str(exc)}

            self._record_outbound_message(
                contact_name=contact_name,
                user_id=user_id,
                text=image_text,
                msg_prefix="s",
                delivery_stage="direct",
                overflow_session_id=warning_session_id if next_count == MAX_CONSECUTIVE_SENDS else None,
                source="image",
                media_name=filename,
                extra_meta={"blocked_reason": "quota_10"} if next_count == MAX_CONSECUTIVE_SENDS else None,
            )

            next_status = "WARNED" if next_count == MAX_CONSECUTIVE_SENDS else "NORMAL"
            next_blocked_reason = "quota_10" if next_count == MAX_CONSECUTIVE_SENDS else None
            next_session_id = warning_session_id if next_count == MAX_CONSECUTIVE_SENDS else None
            self._set_delivery_state(
                user_id,
                status=next_status,
                consecutive_send_count=next_count,
                blocked_reason=next_blocked_reason,
                active_overflow_session_id=next_session_id,
            )
            return {
                "ok": True,
                "result": result,
                "warning": next_count == MAX_CONSECUTIVE_SENDS,
                "overflow_session_id": next_session_id,
            }

    # ── 长轮询主循环 ──

    def _poll_loop(self):
        logger.info("消息轮询循环已启动")
        consecutive_errors = 0

        while self._running:
            if not self.client.logged_in:
                logger.info("未登录，等待扫码...")
                time.sleep(5)
                continue

            try:
                msgs = self.client.get_updates(timeout=35)
                consecutive_errors = 0
                for msg in msgs:
                    try:
                        self.process_message(msg)
                    except Exception as exc:
                        logger.error("处理消息异常: %s", exc, exc_info=True)
            except RuntimeError as exc:
                logger.warning("需要重新登录: %s", exc)
                time.sleep(10)
            except Exception as exc:
                consecutive_errors += 1
                wait = min(consecutive_errors * 5, 60)
                logger.warning("轮询异常 (连续第%d次): %s, %d秒后重试", consecutive_errors, exc, wait)
                time.sleep(wait)

        logger.info("消息轮询循环已停止")

    def start(self):
        if self._running:
            return
        self._running = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

        self._keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
        self._keepalive_thread.start()
        logger.info("WeChatBridge 已启动")

    def stop(self):
        self._running = False
        if getattr(self, "_poll_thread", None):
            self._poll_thread.join(timeout=10)
        if getattr(self, "_keepalive_thread", None):
            self._keepalive_thread.join(timeout=2)
        logger.info("WeChatBridge 已停止")
