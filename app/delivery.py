"""
投递状态机 + overflow session + /pull 补拉逻辑

从 bridge.py 拆分而来，通过 Mixin 注入 WeChatBridge。
所有 self.* 引用在运行时由 WeChatBridge 实例提供。
"""

import logging
import os
import time
import uuid
from datetime import datetime

import requests

import db

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_SENDS = 10
WINDOW_DEADLINE_SECONDS = 24 * 3600
PULL_CHUNK_LIMIT = int(os.environ.get("PULL_CHUNK_LIMIT", "5200"))


class DeliveryMixin:
    """投递状态机 Mixin，注入 WeChatBridge。"""

    # ── 状态读写 ──

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

    # ── 窗口与限制检测 ──

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
        if blocked_reason == "api_limit":
            return "上游限制(ret=-2)"
        return "无"

    def _is_window_limit_error(self, exc: Exception) -> bool:
        message = str(exc)
        return "ret=-2" in message or "24小时" in message or "24 小时" in message

    def _is_delivery_uncertain_error(self, exc: Exception) -> bool:
        return isinstance(exc, requests.exceptions.ReadTimeout) or "Read timed out" in str(exc)

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

    # ── Overflow Session 管理 ──

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

    # ── 出站消息记录与缓存 ──

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
        extra_meta: dict | None = None,
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
            extra_meta={"blocked_reason": reason, **(extra_meta or {})},
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

    # ── 发送决策 ──

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

    def _resolve_limit_error_reason(
        self,
        *,
        user_id: str,
        state: dict,
        now_ts: int,
        next_count: int,
        warning_appended: bool,
    ) -> str:
        if warning_appended or next_count >= MAX_CONSECUTIVE_SENDS:
            return "quota_10"
        if self._is_window_expired(user_id, state, now_ts):
            return "window_24h"
        current_reason = state.get("blocked_reason")
        if current_reason in ("quota_10", "window_24h", "api_limit"):
            return current_reason
        return "api_limit"

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
                    limit_reason = self._resolve_limit_error_reason(
                        user_id=user_id,
                        state=state,
                        now_ts=now_ts,
                        next_count=next_count,
                        warning_appended=warning_appended,
                    )
                    return self._buffer_message(
                        user_id=user_id,
                        contact_name=contact_name,
                        text=final_text if warning_appended else text,
                        reason=limit_reason,
                        source=source,
                        title=title,
                        extra_meta={"limit_warning": True} if warning_appended else None,
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

    # ── 状态摘要 ──

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
