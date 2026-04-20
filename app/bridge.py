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
from datetime import datetime

import requests

import config as cfg
import db
import media
from ilink import ILinkClient

logger = logging.getLogger(__name__)

DATA_BASE = os.environ.get("DATA_DIR", "./data")
MAX_CONSECUTIVE_SENDS = 10
WINDOW_DEADLINE_SECONDS = 24 * 3600
PULL_CHUNK_LIMIT = int(os.environ.get("PULL_CHUNK_LIMIT", "1500"))
MAGIC_WEBHOOK_COMMAND_PREFIX = "__MAGIC_WEBHOOK_COMMAND__:"

MSG_TYPE_MAP = {
    1: "文本",
    2: "图片",
    3: "语音",
    4: "文件",
    5: "视频",
}


class WeChatBridge:
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

    def _sync_send_count_cache(self, user_id: str, state: dict):
        count = int(state.get("consecutive_send_count") or 0)
        status = state.get("status", "NORMAL")
        if count <= 0 and status == "NORMAL":
            self._consecutive_send_count.pop(user_id, None)
            return
        self._consecutive_send_count[user_id] = {
            "count": count,
            "warned": status in ("WARNED", "BUFFERING"),
        }

    def _get_delivery_state(self, user_id: str) -> dict:
        state = db.get_delivery_state(user_id)
        self._sync_send_count_cache(user_id, state)
        return state

    def _set_delivery_state(self, user_id: str, **fields) -> dict:
        state = db.update_delivery_state(user_id, **fields)
        self._sync_send_count_cache(user_id, state)
        return state

    def _last_user_message_at(self, user_id: str, state: dict | None = None) -> int:
        state = state or self._get_delivery_state(user_id)
        last_state = int(state.get("last_user_message_at") or 0)
        last_activity = int(self.activity_tracker.get(user_id, {}).get("last_receive_time", 0) or 0)
        return max(last_state, last_activity)

    def _is_window_expired(self, user_id: str, state: dict | None = None, now_ts: int | None = None) -> bool:
        now_ts = now_ts or int(time.time())
        last_user_at = self._last_user_message_at(user_id, state)
        if not last_user_at:
            return False
        return now_ts - last_user_at >= WINDOW_DEADLINE_SECONDS

    def _blocked_reason_text(self, blocked_reason: str | None) -> str:
        if blocked_reason == "quota_10":
            return "连续 10 条限制"
        if blocked_reason == "window_24h":
            return "24h 窗口失效"
        return "无"

    def _is_window_limit_error(self, exc: Exception) -> bool:
        message = str(exc)
        return "ret=-2" in message or "24小时" in message or "24 小时" in message

    def _is_delivery_uncertain_error(self, exc: Exception) -> bool:
        return isinstance(exc, requests.exceptions.ReadTimeout) or "Read timed out" in str(exc)

    def _save_outbound_image(self, file_data: bytes) -> str:
        media._ensure_media_dir()
        filename = f"out_img_{int(time.time())}_{uuid.uuid4().hex[:8]}.jpg"
        save_path = os.path.join(media.MEDIA_DIR, filename)
        with open(save_path, "wb") as fh:
            fh.write(file_data)
        return filename

    def _build_limit_warning(self, for_pull: bool = False) -> str:
        if for_pull:
            return (
                "\n\n━━━━━━━━━━━━━━\n"
                "⚠️【系统提醒】本次补拉已再次触发微信接收上限，剩余缓存消息已暂停发送\n"
                "👉请回复任意内容后再次发送 /pull 继续拉取"
            )
        return (
            "\n\n━━━━━━━━━━━━━━\n"
            "⚠️【系统提醒】bot已连续发送 10 条通知，已触发微信接收上限，后续消息无法发送\n"
            "👉请回复任意内容解除限制恢复接收！"
        )

    def _discard_active_overflow_sessions(self, user_id: str):
        now_ts = int(time.time())
        sessions = db.discard_active_overflow_sessions(user_id, now_ts)
        for session in sessions:
            pending_ids = db.discard_pending_messages(session["id"], now_ts)
            if pending_ids:
                db.update_message_delivery_stage_for_pending_ids(pending_ids, "discarded")

    def _start_new_overflow_session(self, user_id: str, reason: str, trigger_msg_id: str | None = None) -> dict:
        self._discard_active_overflow_sessions(user_id)
        session_id = f"ofs_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        return db.create_overflow_session(
            session_id=session_id,
            user_id=user_id,
            reason=reason,
            trigger_msg_id=trigger_msg_id,
        )

    def _ensure_active_overflow_session(self, user_id: str, reason: str) -> dict:
        session = db.get_active_overflow_session(user_id)
        if session:
            return session
        session_id = f"ofs_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        return db.create_overflow_session(
            session_id=session_id,
            user_id=user_id,
            reason=reason,
        )

    def _mark_user_recovered(self, user_id: str, now_ts: int):
        session = db.get_active_overflow_session(user_id)
        active_session_id = session["id"] if session else None
        next_status = "NORMAL"

        if session and session["pending_count"] > 0:
            db.mark_overflow_session_ready(session["id"], now_ts)
            next_status = "READY_PULL"
        elif session:
            db.mark_overflow_session_drained(session["id"], now_ts)
            active_session_id = None

        self._set_delivery_state(
            user_id,
            status=next_status,
            consecutive_send_count=0,
            blocked_reason=None,
            last_user_message_at=now_ts,
            active_overflow_session_id=active_session_id,
        )

    def _record_outbound_message(
        self,
        *,
        contact_name: str,
        user_id: str,
        text: str,
        msg_prefix: str,
        delivery_stage: str,
        overflow_session_id: str | None = None,
        pending_message_id: int | None = None,
        source: str = "api",
        title: str = "",
        media_name: str | None = None,
        extra_meta: dict | None = None,
    ):
        meta = {"source": source}
        if title:
            meta["title"] = title
        if extra_meta:
            meta.update(extra_meta)
        self._record_message(
            {
                "type": "send",
                "contact": contact_name,
                "user_id": user_id,
                "text": text,
                "time": int(time.time()),
                "msg_id": f"{msg_prefix}_{uuid.uuid4().hex[:10]}",
                "media": media_name,
                "delivery_stage": delivery_stage,
                "overflow_session_id": overflow_session_id,
                "pending_message_id": pending_message_id,
                "meta": meta,
            }
        )

    def _buffer_message(
        self,
        *,
        user_id: str,
        contact_name: str,
        text: str,
        reason: str,
        source: str,
        title: str = "",
        media_name: str | None = None,
    ) -> dict:
        session = self._ensure_active_overflow_session(user_id, reason)
        pending = db.create_pending_message(
            session_id=session["id"],
            user_id=user_id,
            source=source,
            title=title,
            content=text,
            media=media_name,
            blocked_reason=reason,
        )
        self._record_outbound_message(
            contact_name=contact_name,
            user_id=user_id,
            text=text,
            msg_prefix="buf",
            delivery_stage="buffered",
            overflow_session_id=session["id"],
            pending_message_id=pending["id"],
            source=source,
            title=title,
            media_name=media_name,
            extra_meta={"blocked_reason": reason},
        )
        self._set_delivery_state(
            user_id,
            status="BUFFERING",
            active_overflow_session_id=session["id"],
            blocked_reason=reason,
        )
        reason_text = self._blocked_reason_text(reason)
        return {
            "ok": True,
            "buffered": True,
            "overflow_session_id": session["id"],
            "message": f"消息已进入缓存队列（{reason_text}），用户回复后发送 /pull 可继续拉取。",
        }

    def _next_status_after_send(
        self,
        *,
        consecutive_send_count: int,
        warning_appended: bool,
        active_session_id: str | None,
    ) -> tuple[str, str | None, str | None]:
        if warning_appended:
            return "WARNED", "quota_10", active_session_id
        if active_session_id and db.get_pending_count(active_session_id) > 0:
            return "READY_PULL", None, active_session_id
        return "NORMAL", None, None

    def _send_resolved(
        self,
        *,
        user_id: str,
        contact_name: str,
        text: str,
        context_token: str,
        source: str,
        title: str = "",
        allow_buffer: bool = True,
        rotate_session_on_warn: bool = True,
        record_timeline: bool = True,
        delivery_stage_on_success: str = "direct",
        extra_meta: dict | None = None,
    ) -> dict:
        with self._outbound_lock:
            now_ts = int(time.time())
            state = self._get_delivery_state(user_id)

            if self._is_window_expired(user_id, state, now_ts):
                if allow_buffer:
                    return self._buffer_message(
                        user_id=user_id,
                        contact_name=contact_name,
                        text=text,
                        reason="window_24h",
                        source=source,
                        title=title,
                    )
                return {"ok": False, "error": "已超过 24 小时未收到用户消息，请等待对方回复后再继续发送。"}

            current_count = int(state.get("consecutive_send_count") or 0)
            active_session_id = state.get("active_overflow_session_id")

            if current_count >= MAX_CONSECUTIVE_SENDS:
                if allow_buffer:
                    return self._buffer_message(
                        user_id=user_id,
                        contact_name=contact_name,
                        text=text,
                        reason="quota_10",
                        source=source,
                        title=title,
                    )
                return {"ok": False, "error": "已连续发送 10 条消息，请等待用户回复后再继续发送。"}

            next_count = current_count + 1
            warning_appended = False
            final_text = text

            if next_count == MAX_CONSECUTIVE_SENDS:
                warning_appended = True
                final_text = text + self._build_limit_warning(for_pull=not rotate_session_on_warn)
                if rotate_session_on_warn:
                    session = self._start_new_overflow_session(user_id, "quota_10")
                    active_session_id = session["id"]
                else:
                    session = db.get_active_overflow_session(user_id)
                    active_session_id = session["id"] if session else active_session_id

            try:
                result = self.client.send_text(user_id, final_text, context_token)
            except Exception as exc:
                logger.error("发送消息失败(但可能已送达): %s", exc)
                if allow_buffer and self._is_window_limit_error(exc):
                    return self._buffer_message(
                        user_id=user_id,
                        contact_name=contact_name,
                        text=text,
                        reason="window_24h",
                        source=source,
                        title=title,
                    )
                if self._is_delivery_uncertain_error(exc):
                    resolved_meta = dict(extra_meta or {})
                    resolved_meta["delivery_uncertain"] = True
                    resolved_meta["delivery_error"] = str(exc)
                    if warning_appended:
                        resolved_meta["limit_warning"] = True
                        resolved_meta["blocked_reason"] = "quota_10"
                    if record_timeline:
                        self._record_outbound_message(
                            contact_name=contact_name,
                            user_id=user_id,
                            text=final_text,
                            msg_prefix="s",
                            delivery_stage="uncertain",
                            overflow_session_id=active_session_id if warning_appended else None,
                            source=source,
                            title=title,
                            extra_meta=resolved_meta,
                        )
                    status, blocked_reason, saved_session_id = self._next_status_after_send(
                        consecutive_send_count=next_count,
                        warning_appended=warning_appended,
                        active_session_id=active_session_id,
                    )
                    self._set_delivery_state(
                        user_id,
                        status=status,
                        consecutive_send_count=next_count,
                        blocked_reason=blocked_reason,
                        active_overflow_session_id=saved_session_id,
                    )
                    return {
                        "ok": True,
                        "result": None,
                        "warning": warning_appended,
                        "uncertain": True,
                        "overflow_session_id": saved_session_id,
                        "message": "接口响应超时，消息可能已送达，已先写入消息记录。",
                    }
                return {"ok": False, "error": str(exc)}

            if record_timeline:
                resolved_meta = dict(extra_meta or {})
                if warning_appended:
                    resolved_meta["limit_warning"] = True
                    resolved_meta["blocked_reason"] = "quota_10"
                self._record_outbound_message(
                    contact_name=contact_name,
                    user_id=user_id,
                    text=final_text,
                    msg_prefix="s",
                    delivery_stage=delivery_stage_on_success,
                    overflow_session_id=active_session_id if warning_appended else None,
                    source=source,
                    title=title,
                    extra_meta=resolved_meta or None,
                )

            status, blocked_reason, saved_session_id = self._next_status_after_send(
                consecutive_send_count=next_count,
                warning_appended=warning_appended,
                active_session_id=active_session_id,
            )
            self._set_delivery_state(
                user_id,
                status=status,
                consecutive_send_count=next_count,
                blocked_reason=blocked_reason,
                active_overflow_session_id=saved_session_id,
            )
            return {
                "ok": True,
                "result": result,
                "warning": warning_appended,
                "overflow_session_id": saved_session_id,
            }

    def get_delivery_summary(self, user_id: str) -> dict:
        state = self._get_delivery_state(user_id)
        session_id = state.get("active_overflow_session_id")
        session = db.get_overflow_session(session_id) if session_id else None
        pending_count = session["pending_count"] if session else 0
        display_reason = state.get("blocked_reason") or (session.get("reason") if session else None)
        return {
            "user_id": user_id,
            "contact": self._contact_name(user_id),
            "status": state.get("status", "NORMAL"),
            "consecutive_send_count": state.get("consecutive_send_count", 0),
            "blocked_reason": display_reason,
            "blocked_reason_text": self._blocked_reason_text(display_reason),
            "active_overflow_session_id": session_id,
            "pending_count": pending_count,
            "last_user_message_at": state.get("last_user_message_at", 0),
            "last_warned_at": state.get("last_warned_at", 0),
        }

    def get_contact_delivery_summaries(self) -> dict[str, dict]:
        summaries = {}
        known_user_ids = set(self.contacts.keys())
        known_user_ids.update(state["user_id"] for state in db.list_delivery_states())
        for user_id in known_user_ids:
            summaries[user_id] = self.get_delivery_summary(user_id)
        return summaries

    def get_runtime_status(self) -> dict:
        stats = db.get_global_delivery_stats()
        return {
            "logged_in": self.client.logged_in,
            "bot_id": self.client.bot_id,
            "contacts_count": len(self.contacts),
            "poll_running": self._running,
            "pending_total": stats["pending_total"],
            "active_sessions": stats["active_sessions"],
            "buffering_users": stats["buffering_users"],
        }

    # ── /pull 补拉 ──

    def _format_pending_message(self, pending: dict) -> str:
        dt = datetime.fromtimestamp(pending["created_at"])
        ts = f"{dt.month}-{dt.day} {dt:%H:%M:%S}"
        header = f"[{ts}][{pending.get('source') or 'system'}][{pending.get('blocked_reason') or 'quota_10'}]"
        parts = [header]
        if pending.get("title"):
            parts.append(pending["title"])
        if pending.get("media") and "[图片:" not in (pending.get("content") or ""):
            parts.append(f"[图片:{pending['media']}]")
        parts.append(pending["content"])
        return "\n".join(part for part in parts if part)

    def _build_pull_chunks(self, pending_messages: list[dict]) -> list[dict]:
        chunks: list[dict] = []
        current_text = ""
        current_completed_ids: list[int] = []

        for pending in pending_messages:
            block = self._format_pending_message(pending)
            pending_id = pending["id"]

            if len(block) > PULL_CHUNK_LIMIT:
                if current_text:
                    chunks.append({"text": current_text, "completed_ids": current_completed_ids[:]})
                    current_text = ""
                    current_completed_ids = []

                segments = [
                    block[idx : idx + PULL_CHUNK_LIMIT]
                    for idx in range(0, len(block), PULL_CHUNK_LIMIT)
                ]
                for idx, segment in enumerate(segments):
                    chunks.append(
                        {
                            "text": segment,
                            "completed_ids": [pending_id] if idx == len(segments) - 1 else [],
                        }
                    )
                continue

            candidate = f"{current_text}\n\n{block}" if current_text else block
            if len(candidate) <= PULL_CHUNK_LIMIT:
                current_text = candidate
                current_completed_ids.append(pending_id)
                continue

            chunks.append({"text": current_text, "completed_ids": current_completed_ids[:]})
            current_text = block
            current_completed_ids = [pending_id]

        if current_text:
            chunks.append({"text": current_text, "completed_ids": current_completed_ids[:]})
        return chunks

    def pull_pending_messages(self, user_id: str) -> dict:
        summary = self.get_delivery_summary(user_id)
        session_id = summary.get("active_overflow_session_id")
        if not session_id or summary.get("pending_count", 0) <= 0:
            return {"ok": False, "empty": True, "message": "📭 当前没有待拉取的缓存消息。"}

        pending_messages = db.get_pending_messages(session_id)
        if not pending_messages:
            db.mark_overflow_session_drained(session_id)
            self._set_delivery_state(
                user_id,
                status="DRAINED",
                blocked_reason=None,
                active_overflow_session_id=None,
            )
            return {"ok": False, "empty": True, "message": "📭 当前没有待拉取的缓存消息。"}

        chunks = self._build_pull_chunks(pending_messages)
        contact_name = self._contact_name(user_id)
        context_token = self.get_context_token(user_id)
        delivered_pending_ids: list[int] = []
        sent_chunks = 0

        for chunk in chunks:
            result = self._send_resolved(
                user_id=user_id,
                contact_name=contact_name,
                text=chunk["text"],
                context_token=context_token,
                source="pull",
                title="缓存补拉",
                allow_buffer=False,
                rotate_session_on_warn=False,
                record_timeline=True,
                delivery_stage_on_success="pulled",
                extra_meta={"pull_batch": True, "pull_session_id": session_id},
            )
            if not result.get("ok"):
                break

            sent_chunks += 1
            if chunk["completed_ids"]:
                delivered_pending_ids.extend(chunk["completed_ids"])
                db.mark_pending_messages_pulled(chunk["completed_ids"])
                db.update_message_delivery_stage_for_pending_ids(chunk["completed_ids"], "pulled")

        remaining = db.get_pending_count(session_id)
        if remaining <= 0:
            db.mark_overflow_session_drained(session_id)
            self._set_delivery_state(
                user_id,
                status="DRAINED",
                blocked_reason=None,
                active_overflow_session_id=None,
            )
        else:
            current_state = self._get_delivery_state(user_id)
            next_status = "WARNED" if current_state.get("consecutive_send_count", 0) >= MAX_CONSECUTIVE_SENDS else "READY_PULL"
            if next_status == "READY_PULL":
                db.mark_overflow_session_ready(session_id)
            self._set_delivery_state(
                user_id,
                status=next_status,
                blocked_reason="quota_10" if next_status == "WARNED" else None,
                active_overflow_session_id=session_id,
            )

        return {
            "ok": sent_chunks > 0,
            "sent_chunks": sent_chunks,
            "delivered_pending_ids": delivered_pending_ids,
            "remaining": remaining,
        }

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
            import config as cfg

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
                import config as cfg

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
                import config as cfg

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
                retry_text = cmd_reply[len("__MAGIC_RETRY__:") :]

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
        """发送“正在输入”状态。"""
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
                    return self._buffer_message(
                        user_id=user_id,
                        contact_name=contact_name,
                        text=image_text,
                        reason="window_24h",
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

    def _keepalive_loop(self):
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
                now_ts = time.time()
                for user_id, activity in list(self.activity_tracker.items()):
                    last_time = activity.get("last_receive_time", 0)
                    if not last_time:
                        continue

                    elapsed = now_ts - last_time
                    if elapsed >= WINDOW_DEADLINE_SECONDS:
                        continue

                    if not activity.get("reminded") and elapsed >= remind_seconds:
                        activity["reminded"] = True
                        self._save_contacts()

                        remaining = WINDOW_DEADLINE_SECONDS - elapsed
                        remain_h = int(remaining // 3600)
                        remain_m = int((remaining % 3600) // 60)

                        self.send(
                            user_id,
                            (
                                f"【⏰ 通道保活提醒】\n"
                                f"您已超过 {remind_minutes // 60} 小时 {remind_minutes % 60} 分钟未发送消息。\n"
                                f"微信通道将在约 {remain_h}h{remain_m}m 后自动休眠。\n"
                                f"回复任意内容即可保持连接。"
                            ),
                            source="keepalive",
                        )
            except Exception as exc:
                logger.error("保活检查异常: %s", exc)

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
