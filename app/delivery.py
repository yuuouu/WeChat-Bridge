from __future__ import annotations

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

from fmt import md_inline as _md_inline

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_SENDS = 10
WINDOW_DEADLINE_SECONDS = 24 * 3600
PULL_CHUNK_LIMIT = int(os.environ.get("PULL_CHUNK_LIMIT", "5200"))
PENDING_CLEANUP_INTERVAL_SECONDS = int(os.environ.get("PENDING_CLEANUP_INTERVAL_SECONDS", "3600"))

TIME_SENSITIVE_PENDING_KEYWORDS = (
    "市场简报",
    "金价速报",
    "自选A股",
    "okx-bot",
    "行情",
    "持仓",
    "设备断开",
    "新设备连接",
    "连接了你的路由器",
    "断开连接",
    "通道保活提醒",
)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


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
        state = self.db.get_delivery_state(user_id)
        self._sync_send_count_cache(user_id, state)
        return state

    def _set_delivery_state(self, user_id: str, **fields) -> dict:
        state = self.db.update_delivery_state(user_id, **fields)
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
        if blocked_reason == "muted":
            return "静默模式"
        return "无"

    # ── 过期缓存清理 ──

    def _pending_ttl_hours(self, pending: dict) -> int:
        """按消息类型选择缓存保留时长；返回 <=0 表示不自动过期。"""
        if pending.get("media"):
            return _env_int("PENDING_MEDIA_TTL_HOURS", 168)

        source = (pending.get("source") or "").lower()
        haystack = "\n".join(str(pending.get(key) or "") for key in ("title", "content", "blocked_reason")).lower()
        if source == "keepalive" or any(keyword.lower() in haystack for keyword in TIME_SENSITIVE_PENDING_KEYWORDS):
            return _env_int("PENDING_TIME_SENSITIVE_TTL_HOURS", 24)

        return _env_int("PENDING_MESSAGE_TTL_HOURS", 72)

    def cleanup_expired_pending_messages(self, *, now_ts: int | None = None, force: bool = True) -> dict:
        """将超出 TTL 的缓存消息标记为已丢弃，并收口空的 overflow session。"""
        now_ts = now_ts or int(time.time())
        last_cleanup = int(getattr(self, "_last_pending_cleanup_at", 0) or 0)
        if not force and now_ts - last_cleanup < 300:
            return {"ok": True, "skipped": True, "expired": 0, "sessions_discarded": 0}
        self._last_pending_cleanup_at = now_ts

        expired_ids: list[int] = []
        ttl_by_pending_id: dict[int, int] = {}
        for pending in self.db.list_pending_messages("PENDING"):
            ttl_hours = self._pending_ttl_hours(pending)
            if ttl_hours <= 0:
                continue
            ttl_seconds = ttl_hours * 3600
            if now_ts - int(pending.get("created_at") or now_ts) >= ttl_seconds:
                expired_ids.append(pending["id"])
                ttl_by_pending_id[pending["id"]] = ttl_hours

        if not expired_ids:
            return {"ok": True, "expired": 0, "sessions_discarded": 0}

        session_ids = self.db.discard_pending_message_ids(expired_ids, now_ts)
        self.db.update_message_delivery_stage_for_pending_ids(expired_ids, "discarded")
        discarded_sessions = self.db.discard_empty_overflow_sessions(session_ids, now_ts)
        for session in discarded_sessions:
            state = self._get_delivery_state(session["user_id"])
            if state.get("active_overflow_session_id") != session["id"]:
                continue
            self._set_delivery_state(
                session["user_id"],
                status="NORMAL",
                consecutive_send_count=0,
                blocked_reason=None,
                active_overflow_session_id=None,
            )

        logger.info(
            "过期缓存清理完成: expired=%d, sessions_discarded=%d, ttl_hours=%s",
            len(expired_ids),
            len(discarded_sessions),
            sorted(set(ttl_by_pending_id.values())),
        )
        return {
            "ok": True,
            "expired": len(expired_ids),
            "sessions_discarded": len(discarded_sessions),
            "expired_ids": expired_ids,
            "discarded_session_ids": [session["id"] for session in discarded_sessions],
        }

    def _pending_cleanup_loop(self):
        while self._running:
            time.sleep(PENDING_CLEANUP_INTERVAL_SECONDS)
            if not self._running:
                break
            try:
                self.cleanup_expired_pending_messages(force=True)
            except Exception as exc:
                logger.warning("过期缓存清理失败: %s", exc)

    def _is_window_limit_error(self, exc: Exception) -> bool:
        message = str(exc)
        return "ret=-2" in message or "24小时" in message or "24 小时" in message

    def _is_delivery_uncertain_error(self, exc: Exception) -> bool:
        return isinstance(exc, requests.exceptions.ReadTimeout) or "Read timed out" in str(exc)

    def _build_limit_warning(self, for_pull: bool = False) -> str:
        return (
            "\n\n---\n\n## ⚠️ 微信 bot 10 条上限\n\n- 回复任意内容恢复消息发送，后续消息将自动缓存，可发送 `/pull` 拉取"
        )

    # ── Overflow Session 管理 ──

    def _discard_active_overflow_sessions(self, user_id: str):
        now_ts = int(time.time())
        sessions = self.db.discard_active_overflow_sessions(user_id, now_ts)
        for session in sessions:
            pending_ids = self.db.discard_pending_messages(session["id"], now_ts)
            if pending_ids:
                self.db.update_message_delivery_stage_for_pending_ids(pending_ids, "discarded")

    def _start_new_overflow_session(self, user_id: str, reason: str, trigger_msg_id: str | None = None) -> dict:
        self._discard_active_overflow_sessions(user_id)
        session_id = f"ofs_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        return self.db.create_overflow_session(
            session_id=session_id,
            user_id=user_id,
            reason=reason,
            trigger_msg_id=trigger_msg_id,
        )

    def _ensure_active_overflow_session(self, user_id: str, reason: str) -> dict:
        session = self.db.get_active_overflow_session(user_id)
        if session:
            return session
        session_id = f"ofs_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        return self.db.create_overflow_session(
            session_id=session_id,
            user_id=user_id,
            reason=reason,
        )

    def _mark_user_recovered(self, user_id: str, now_ts: int):
        session = self.db.get_active_overflow_session(user_id)
        active_session_id = session["id"] if session else None
        next_status = "NORMAL"

        if session and session["pending_count"] > 0:
            self.db.mark_overflow_session_ready(session["id"], now_ts)
            next_status = "READY_PULL"
        elif session:
            self.db.mark_overflow_session_drained(session["id"], now_ts)
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
        now_ts = int(time.time())
        self.db.record_contact_activity(
            user_id=user_id,
            bot_id=self.client.get_bot_id() or "",
            display_name=contact_name,
            outbound_at=now_ts,
        )
        self._record_message(
            {
                "type": "send",
                "contact": contact_name,
                "user_id": user_id,
                "text": text,
                "time": now_ts,
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
        self.cleanup_expired_pending_messages(force=False)
        session = self._ensure_active_overflow_session(user_id, reason)
        pending = self.db.create_pending_message(
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
        if active_session_id and self.db.get_pending_count(active_session_id) > 0:
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
        image_data: bytes | None = None,
        media_name: str | None = None,
    ) -> dict:
        with self._outbound_lock:
            now_ts = int(time.time())
            state = self._get_delivery_state(user_id)

            # 静默模式检查（keepalive / command / system 不受 mute 影响）
            mute_ts = self._mute_until.get(user_id, 0)
            if mute_ts and now_ts < mute_ts and source not in ("keepalive", "command", "system"):
                if allow_buffer:
                    return self._buffer_message(
                        user_id=user_id,
                        contact_name=contact_name,
                        text=text,
                        reason="muted",
                        source=source,
                        title=title,
                        media_name=media_name,
                    )
                from datetime import datetime

                unmute_str = datetime.fromtimestamp(mute_ts).strftime("%H:%M")
                return {"ok": False, "error": f"静默模式中，{unmute_str} 后恢复。"}

            if self._is_window_expired(user_id, state, now_ts):
                if allow_buffer:
                    return self._buffer_message(
                        user_id=user_id,
                        contact_name=contact_name,
                        text=text,
                        reason="window_24h",
                        source=source,
                        title=title,
                        media_name=media_name,
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
                        media_name=media_name,
                    )
                return {"ok": False, "error": "已连续发送 10 条消息，请等待用户回复后发送 /pull 拉取缓存消息。"}

            next_count = current_count + 1
            warning_appended = False
            final_text = text

            if next_count == MAX_CONSECUTIVE_SENDS:
                warning_appended = True
                # 图片消息无法拼接文字告警，仅建立 overflow session
                if image_data is None:
                    final_text = text + self._build_limit_warning(for_pull=not rotate_session_on_warn)
                if rotate_session_on_warn:
                    session = self._start_new_overflow_session(user_id, "quota_10")
                    active_session_id = session["id"]
                else:
                    session = self.db.get_active_overflow_session(user_id)
                    active_session_id = session["id"] if session else active_session_id

            try:
                if image_data is not None:
                    result = self.client.send_image(user_id, image_data, context_token)
                else:
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
                        media_name=media_name,
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
                            media_name=media_name,
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
                    media_name=media_name,
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
        session = self.db.get_overflow_session(session_id) if session_id else None
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
        known_user_ids.update(state["user_id"] for state in self.db.list_delivery_states())
        for user_id in known_user_ids:
            summaries[user_id] = self.get_delivery_summary(user_id)
        return summaries

    def get_runtime_status(self) -> dict:
        stats = self.db.get_global_delivery_stats()
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
        parts = [
            "---",
            "### 缓存消息",
            "",
            f"- **时间**：{_md_inline(ts)}",
            f"- **来源**：{_md_inline(pending.get('source') or 'system')}",
            f"- **原因**：{_md_inline(pending.get('blocked_reason') or 'quota_10')}",
        ]
        if pending.get("title"):
            parts.extend(["", f"**标题**：{pending['title']}"])
        if pending.get("media") and "[图片:" not in (pending.get("content") or ""):
            parts.extend(["", f"> 图片已缓存，文件：{_md_inline(pending['media'])}"])
        parts.extend(["", pending["content"]])
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

                segments = [block[idx : idx + PULL_CHUNK_LIMIT] for idx in range(0, len(block), PULL_CHUNK_LIMIT)]
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
            return {"ok": False, "empty": True, "message": "## 📭 缓存消息\n\n- 当前没有待拉取的缓存消息"}

        pending_messages = self.db.get_pending_messages(session_id)
        if not pending_messages:
            self.db.mark_overflow_session_drained(session_id)
            self._set_delivery_state(
                user_id,
                status="DRAINED",
                blocked_reason=None,
                active_overflow_session_id=None,
            )
            return {"ok": False, "empty": True, "message": "## 📭 缓存消息\n\n- 当前没有待拉取的缓存消息"}

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
                self.db.mark_pending_messages_pulled(chunk["completed_ids"])
                self.db.update_message_delivery_stage_for_pending_ids(chunk["completed_ids"], "pulled")

        remaining = self.db.get_pending_count(session_id)
        if remaining <= 0:
            self.db.mark_overflow_session_drained(session_id)
            self._set_delivery_state(
                user_id,
                status="DRAINED",
                blocked_reason=None,
                active_overflow_session_id=None,
            )
        else:
            current_state = self._get_delivery_state(user_id)
            next_status = (
                "WARNED" if current_state.get("consecutive_send_count", 0) >= MAX_CONSECUTIVE_SENDS else "READY_PULL"
            )
            if next_status == "READY_PULL":
                self.db.mark_overflow_session_ready(session_id)
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
