from __future__ import annotations

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
import shutil
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable

import requests

import config as cfg
import db
import media
from account_identity import account_storage_dir_name
from commands import MAGIC_WEBHOOK_COMMAND_PREFIX, CommandMixin
from delivery import (  # noqa: F401 — re-export for backward compat
    MAX_CONSECUTIVE_SENDS,
    PULL_CHUNK_LIMIT,
    WINDOW_DEADLINE_SECONDS,
    DeliveryMixin,
)
from event_bus import (
    EVENT_MESSAGE_RECEIVED,
    Event,
    EventBus,
)
from ilink import ILinkClient, format_reference_fallback_text
from keepalive import KeepaliveMixin
from plugin_base import PluginRegistry
from webhook_manager import discover_and_register_plugins

logger = logging.getLogger(__name__)

DATA_BASE = os.environ.get("DATA_DIR", "./data")
VISIBLE_CONTACT_LIMIT = 1


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


VIDEO_MEDIA_RETENTION_HOURS = _env_int("VIDEO_MEDIA_RETENTION_HOURS", 168)
VIDEO_MEDIA_CLEANUP_INTERVAL_SECONDS = _env_int("VIDEO_MEDIA_CLEANUP_INTERVAL_SECONDS", 3600)
FILE_MEDIA_MAX_BYTES = max(1024, _env_int("FILE_MEDIA_MAX_BYTES", 20 * 1024 * 1024))

MSG_TYPE_MAP = {
    1: "文本",
    2: "图片",
    3: "语音",
    4: "文件",
    5: "视频",
}


class WeChatBridge(DeliveryMixin, CommandMixin, KeepaliveMixin):
    """微信消息桥接器。"""

    def __init__(self, client: ILinkClient, data_base: str | None = None):
        self.client = client
        self.data_base = data_base or DATA_BASE
        self.db = db.MessageStore(os.path.join(self.data_base, "messages.db"))
        self._media_dir = os.path.join(self.data_base, "media")
        self.contacts: dict[str, str] = {}
        self.context_tokens: dict[str, str] = {}
        self._start_time = time.time()
        self.activity_tracker: dict[str, dict] = {}
        self.recent_messages = deque(maxlen=50)
        self.ag_inbox = []
        self._ag_inbox_lock = threading.Lock()
        self._running = False
        self._poll_thread: threading.Thread | None = None
        self._pending_cleanup_thread: threading.Thread | None = None
        self._last_pending_cleanup_at = 0
        self._last_media_cleanup_at = 0
        self.ai_manager = None
        self._consecutive_send_count: dict[str, dict] = {}
        self._webhook_commands: dict[str, str] = {}  # 外部服务注册的命令 {"/todo": "记录待办"}
        self._mute_until: dict[str, float] = {}  # 用户静默截止时间 {user_id: timestamp}
        self._outbound_lock = threading.Lock()
        self._contacts_lock = threading.Lock()
        self._setup_data_dir()
        if self.client.logged_in:
            self.record_account_event("token_restored", reason="startup")
        self._load_contacts()

        self.event_bus = EventBus()
        self.plugin_registry = PluginRegistry(self.event_bus, send_func=self.send, bridge=self)
        discover_and_register_plugins(self.plugin_registry)
        self.plugin_registry.start_all()

    def _setup_data_dir(self, bot_id: str | None = None):
        """根据稳定账号标识设置数据目录，实现多账号数据隔离。"""
        db.init_accounts_db(self.data_base)
        bid = bot_id or self.client.get_bot_id()
        ilink_user_id = getattr(self.client, "user_id", "") or ""
        storage_dir = account_storage_dir_name(bot_id=bid or "", ilink_user_id=ilink_user_id)
        if bid or ilink_user_id:
            self._data_dir = os.path.join(self.data_base, storage_dir)
            if ilink_user_id:
                logger.info("数据目录按 ilink_user_id 隔离: %s", self._data_dir)
            else:
                logger.info("数据目录按 bot_id 隔离: %s", self._data_dir)
        else:
            self._data_dir = self.data_base
            logger.info("未检测到 bot_id，使用默认数据目录: %s", self._data_dir)
        os.makedirs(self._data_dir, exist_ok=True)

        self._contacts_file = os.path.join(self._data_dir, "contacts.json")
        self.db = db.MessageStore(os.path.join(self._data_dir, "messages.db"))
        self._media_dir = os.path.join(self._data_dir, "media")
        os.makedirs(self._media_dir, exist_ok=True)

    def record_account_event(
        self,
        event: str,
        *,
        reason: str = "",
        bot_id: str = None,
        ilink_user_id: str = None,
        base_url: str = None,
        meta: dict | None = None,
    ):
        """记录 Bot 账号登录态事件，不保存 token 明文。"""
        resolved_bot_id = bot_id or self.client.get_bot_id()
        if not resolved_bot_id:
            return
        db.record_bot_account_event(
            bot_id=resolved_bot_id,
            ilink_user_id=ilink_user_id if ilink_user_id is not None else (getattr(self.client, "user_id", "") or ""),
            event=event,
            data_dir=getattr(self, "_data_dir", os.path.join(self.data_base, resolved_bot_id)),
            base_url=base_url if base_url is not None else (getattr(self.client, "base_url", "") or ""),
            reason=reason,
            meta=meta,
            token_mtime=self.client.get_token_mtime() if hasattr(self.client, "get_token_mtime") else 0,
        )

    # ── 联系人缓存 ──

    def _load_contacts(self):
        self.contacts = {}
        self.context_tokens = {}
        self.activity_tracker = {}

        if os.path.exists(self._contacts_file):
            try:
                with open(self._contacts_file, encoding="utf-8") as fh:
                    self.contacts = json.load(fh)
                logger.info("已加载 %d 个联系人缓存", len(self.contacts))
            except Exception as exc:
                logger.warning("加载联系人缓存失败: %s", exc)

        ctx_file = os.path.join(self._data_dir, "context_tokens.json")
        if os.path.exists(ctx_file):
            try:
                with open(ctx_file, encoding="utf-8") as fh:
                    self.context_tokens = json.load(fh)
            except Exception:
                pass

        act_file = os.path.join(self._data_dir, "activity.json")
        if os.path.exists(act_file):
            try:
                with open(act_file, encoding="utf-8") as fh:
                    self.activity_tracker = json.load(fh)
            except Exception:
                pass

    def _latest_contact_times(self) -> dict[str, int]:
        try:
            latest_times = self.db.get_latest_receive_times_by_user()
        except Exception as exc:
            logger.debug("读取联系人最近入站时间失败: %s", exc)
            latest_times = {}

        for state in self.db.list_delivery_states():
            try:
                last_user_message_at = int(state.get("last_user_message_at") or 0)
            except (AttributeError, TypeError, ValueError):
                last_user_message_at = 0
            user_id = state.get("user_id")
            if user_id and last_user_message_at > latest_times.get(user_id, 0):
                latest_times[user_id] = last_user_message_at

        for user_id, activity in self.activity_tracker.items():
            try:
                last_receive_time = int(activity.get("last_receive_time") or 0)
            except (AttributeError, TypeError, ValueError):
                last_receive_time = 0
            if last_receive_time > latest_times.get(user_id, 0):
                latest_times[user_id] = last_receive_time
        return latest_times

    def get_ordered_contacts(self) -> dict[str, str]:
        """联系人按最近入站时间倒序返回。"""
        latest_times = self._latest_contact_times()
        indexed_items = list(enumerate(self.contacts.items()))
        indexed_items.sort(key=lambda item: (-latest_times.get(item[1][0], 0), item[0]))
        return dict(contact for _, contact in indexed_items)

    def get_visible_contacts(self) -> dict[str, str]:
        """默认只展示最近一个联系人，历史联系人继续保留用于精确发送。"""
        ordered = self.get_ordered_contacts()
        return dict(list(ordered.items())[:VISIBLE_CONTACT_LIMIT])

    def get_default_contact(self) -> str:
        """返回默认联系人 user_id，按最近入站优先。"""
        for user_id in self.get_ordered_contacts():
            return user_id
        return ""

    def record_default_recipient_decision(
        self,
        selected_user_id: str,
        *,
        request_path: str = "",
        source: str = "",
        title: str = "",
        message_len: int = 0,
    ):
        self.db.record_default_recipient_decision(
            bot_id=self.client.get_bot_id() or "",
            request_path=request_path,
            source=source,
            selected_user_id=selected_user_id,
            selected_display_name=self._contact_name(selected_user_id),
            reason="latest_inbound_contact",
            message_len=message_len,
            title=title,
        )

    def _save_contacts(self):
        with self._contacts_lock:
            os.makedirs(self._data_dir, exist_ok=True)
            with open(self._contacts_file, "w", encoding="utf-8") as fh:
                json.dump(self.get_ordered_contacts(), fh, ensure_ascii=False, indent=2)

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

    def _record_contact_activity(
        self,
        user_id: str,
        *,
        display_name: str = "",
        inbound_at: int | None = None,
        outbound_at: int | None = None,
        context_token_at: int | None = None,
    ):
        self.db.record_contact_activity(
            user_id=user_id,
            bot_id=self.client.get_bot_id() or "",
            display_name=display_name or self.contacts.get(user_id, ""),
            inbound_at=inbound_at,
            outbound_at=outbound_at,
            context_token_at=context_token_at,
        )

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
        if name_or_id in self.contacts:
            return name_or_id
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

    def _account_scoped_user_id(self, user_id: str) -> str:
        bot_id = self.client.get_bot_id() or ""
        return f"{bot_id}:{user_id}" if bot_id else user_id

    # ── 持久化与状态 ──

    def _record_message(self, msg_dict: dict) -> bool:
        """将消息同时写入内存缓存和 SQLite 持久化存储。"""
        if not self.db.save_message(msg_dict):
            return False
        self.recent_messages.append(msg_dict)
        return True

    def _save_outbound_image(self, file_data: bytes) -> str:
        media._ensure_media_dir(self._media_dir)
        filename = f"out_img_{int(time.time())}_{uuid.uuid4().hex[:8]}.jpg"
        save_path = os.path.join(self._media_dir, filename)
        with open(save_path, "wb") as fh:
            fh.write(file_data)
        return filename

    def _save_outbound_video(self, file_data: bytes) -> str:
        media._ensure_media_dir(self._media_dir)
        filename = f"out_video_{int(time.time())}_{uuid.uuid4().hex[:8]}.mp4"
        save_path = os.path.join(self._media_dir, filename)
        with open(save_path, "wb") as fh:
            fh.write(file_data)
        return filename

    def _save_outbound_voice(self, file_data: bytes) -> str:
        media._ensure_media_dir(self._media_dir)
        ext = media._detect_media_format(file_data, media_type="voice")
        filename = f"out_voice_{int(time.time())}_{uuid.uuid4().hex[:8]}.{ext}"
        save_path = os.path.join(self._media_dir, filename)
        with open(save_path, "wb") as fh:
            fh.write(file_data)
        return filename

    def _save_outbound_file(self, file_data: bytes, file_name: str = "") -> str:
        media._ensure_media_dir(self._media_dir)
        base_name = os.path.basename(file_name or "file.bin").strip() or "file.bin"
        safe_name = "".join("_" if ch in "/\\\x00" or ord(ch) < 32 else ch for ch in base_name)[:120]
        filename = f"out_file_{int(time.time())}_{uuid.uuid4().hex[:8]}_{safe_name or 'file.bin'}"
        save_path = os.path.join(self._media_dir, filename)
        with open(save_path, "wb") as fh:
            fh.write(file_data)
        return filename

    def _copy_outbound_file(self, filepath: str, file_name: str = "") -> tuple[str, str]:
        media._ensure_media_dir(self._media_dir)
        source_path = os.path.abspath(filepath)
        if not os.path.isfile(source_path):
            raise FileNotFoundError(f"文件不存在: {source_path}")
        base_name = os.path.basename(file_name or source_path).strip() or "file.bin"
        safe_name = "".join("_" if ch in "/\\\x00" or ord(ch) < 32 else ch for ch in base_name)[:120]
        filename = f"out_file_{int(time.time())}_{uuid.uuid4().hex[:8]}_{safe_name or 'file.bin'}"
        save_path = os.path.join(self._media_dir, filename)
        shutil.copyfile(source_path, save_path)
        return filename, save_path

    def _blocked_non_replayable_send(
        self,
        *,
        user_id: str,
        reason: str,
        media_label: str,
        media_name: str | None = None,
        active_session_id: str | None = None,
    ) -> dict:
        """标记非文本类消息受限。

        这些消息当前不能通过 /pull 原样重放，不能写入 pending 队列假装可补拉。
        """
        self._set_delivery_state(
            user_id,
            status="BUFFERING",
            blocked_reason=reason,
            active_overflow_session_id=active_session_id,
        )
        if reason == "muted":
            message = f"{media_label}未发送：当前处于静默模式，恢复后请重试。"
        else:
            reason_text = self._blocked_reason_text(reason)
            message = f"{media_label}未发送：当前联系人发送受限（{reason_text}），该类型暂不能进入 /pull 缓存，请等用户回复后重试。"
        result = {
            "ok": False,
            "blocked": True,
            "blocked_reason": reason,
            "retry_after_user_reply": reason != "muted",
            "error": message,
        }
        if media_name:
            result["media"] = media_name
        return result

    def _send_non_replayable_resolved(
        self,
        *,
        user_id: str,
        contact_name: str,
        record_text: str | Callable[[], str],
        context_token: str,
        source: str,
        media_label: str,
        send_action,
        media_name_getter=None,
        title: str = "",
        extra_meta: dict | None = None,
    ) -> dict:
        """发送无法通过 /pull 原样重放的结构化消息。"""
        with self._outbound_lock:
            now_ts = int(time.time())
            state = self._get_delivery_state(user_id)
            active_session_id = state.get("active_overflow_session_id")
            current_status = state.get("status", "NORMAL")
            current_reason = state.get("blocked_reason")

            if current_status in ("BUFFERING", "WARNED") and current_reason in (
                "quota_10",
                "window_24h",
                "api_limit",
            ):
                return self._blocked_non_replayable_send(
                    user_id=user_id,
                    reason=current_reason,
                    media_label=media_label,
                    active_session_id=active_session_id,
                )

            mute_ts = self._mute_until.get(user_id, 0)
            if mute_ts and now_ts < mute_ts and source not in ("keepalive", "command", "system"):
                return self._blocked_non_replayable_send(
                    user_id=user_id,
                    reason="muted",
                    media_label=media_label,
                    active_session_id=active_session_id,
                )

            if self._is_window_expired(user_id, state, now_ts):
                return self._blocked_non_replayable_send(
                    user_id=user_id,
                    reason="window_24h",
                    media_label=media_label,
                    active_session_id=active_session_id,
                )

            current_count = int(state.get("consecutive_send_count") or 0)
            if current_count >= MAX_CONSECUTIVE_SENDS:
                return self._blocked_non_replayable_send(
                    user_id=user_id,
                    reason="quota_10",
                    media_label=media_label,
                    active_session_id=active_session_id,
                )

            next_count = current_count + 1
            warning_appended = False
            if next_count == MAX_CONSECUTIVE_SENDS:
                warning_appended = True
                session = self._start_new_overflow_session(user_id, "quota_10")
                active_session_id = session["id"]

            try:
                result = send_action()
            except Exception as exc:
                logger.error("%s发送失败: %s", media_label, exc)
                media_name = media_name_getter() if media_name_getter else None
                if self._is_window_limit_error(exc):
                    limit_reason = self._resolve_limit_error_reason(
                        user_id=user_id,
                        state=state,
                        now_ts=now_ts,
                        next_count=next_count,
                        warning_appended=warning_appended,
                    )
                    return self._blocked_non_replayable_send(
                        user_id=user_id,
                        reason=limit_reason,
                        media_label=media_label,
                        media_name=media_name,
                        active_session_id=active_session_id,
                    )
                if self._is_delivery_uncertain_error(exc):
                    resolved_text = record_text() if callable(record_text) else record_text
                    resolved_meta = dict(extra_meta or {})
                    resolved_meta["delivery_uncertain"] = True
                    resolved_meta["delivery_error"] = str(exc)
                    if warning_appended:
                        resolved_meta["limit_warning"] = True
                        resolved_meta["blocked_reason"] = "quota_10"
                    self._record_outbound_message(
                        contact_name=contact_name,
                        user_id=user_id,
                        text=resolved_text,
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
                        **({"media": media_name} if media_name else {}),
                    }
                return {
                    "ok": False,
                    "error": str(exc),
                    **({"media": media_name} if media_name else {}),
                }

            media_name = media_name_getter() if media_name_getter else None
            resolved_text = record_text() if callable(record_text) else record_text
            resolved_meta = dict(extra_meta or {})
            if warning_appended:
                resolved_meta["limit_warning"] = True
                resolved_meta["blocked_reason"] = "quota_10"
            self._record_outbound_message(
                contact_name=contact_name,
                user_id=user_id,
                text=resolved_text,
                msg_prefix="s",
                delivery_stage="direct",
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
                **({"media": media_name} if media_name else {}),
            }

    def _move_outbound_video(self, filepath: str) -> tuple[str, str]:
        media._ensure_media_dir(self._media_dir)
        filename = f"out_video_{int(time.time())}_{uuid.uuid4().hex[:8]}.mp4"
        save_path = os.path.join(self._media_dir, filename)
        shutil.move(filepath, save_path)
        return filename, save_path

    def _media_retention_hours(self) -> int:
        return _env_int("VIDEO_MEDIA_RETENTION_HOURS", VIDEO_MEDIA_RETENTION_HOURS)

    def cleanup_expired_media_files(self, *, now_ts: int | None = None, force: bool = True) -> dict:
        """按 VIDEO_MEDIA_RETENTION_HOURS 清理已发送视频文件。"""
        now_ts = now_ts or int(time.time())
        if not force and now_ts - int(self._last_media_cleanup_at or 0) < VIDEO_MEDIA_CLEANUP_INTERVAL_SECONDS:
            return {"ok": True, "skipped": True, "deleted": 0}
        self._last_media_cleanup_at = now_ts
        return media.cleanup_expired_media_files(
            self._media_dir,
            retention_hours=self._media_retention_hours(),
            prefixes=("out_video_",),
            now_ts=now_ts,
        )

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
                        media_dir=self._media_dir,
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
                file_item = item.get("file_item") or {}
                file_name = os.path.basename(str(file_item.get("file_name") or "未知文件"))
                try:
                    declared_size = int(file_item.get("len") or "0")
                except (TypeError, ValueError):
                    declared_size = 0
                if declared_size > FILE_MEDIA_MAX_BYTES:
                    parts.append(f"[文件过大:{file_name}]")
                    logger.warning("文件超过接收上限: name=%s size=%d", file_name, declared_size)
                    continue
                file_info = media.extract_pic_info(file_item)
                if file_info:
                    msg_id = msg.get("msg_id", str(time.time()))
                    filepath = media.download_and_decrypt_media(
                        encrypted_query_param=file_info["encrypted_query_param"],
                        aes_key_b64=file_info["aes_key"],
                        msg_id=msg_id,
                        media_type="file",
                        media_dir=self._media_dir,
                        max_bytes=FILE_MEDIA_MAX_BYTES,
                    )
                    if filepath:
                        filename = os.path.basename(filepath)
                        parts.append(f"[文件:{file_name}]")
                        msg.setdefault("_media_paths", []).append(filename)
                        logger.info("文件已解码保存: name=%s cache=%s", file_name, filename)
                    else:
                        parts.append(f"[文件下载失败:{file_name}]")
                else:
                    parts.append(f"[文件缺少解密参数:{file_name}]")
                    logger.warning("file_item 缺少解密参数: keys=%s", list(file_item.keys()))
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
                        media_dir=self._media_dir,
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
        res = " ".join(parts) if parts else "[空消息]"
        return res.strip() if parts else res

    def _trigger_webhook(self, from_user: str, from_name: str, text: str, msg: dict, *, is_command: bool = False):
        """将消息通过标准 Webhook 转发给外部系统。"""
        webhook_cfg = self._get_webhook_config()
        if not self._should_forward_message(is_command=is_command):
            return
        command_name = ""
        command_args = ""
        if is_command and text.startswith("/"):
            parts = text.strip().split(maxsplit=1)
            command_name = parts[0].lower()
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
        activity_at = int(time.time())
        context_token_at = None
        if from_user and context_token and self.context_tokens.get(from_user) != context_token:
            self.context_tokens[from_user] = context_token
            context_token_at = activity_at
            should_save = True

        if from_user and text:
            now_ts = activity_at
            self.activity_tracker[from_user] = {
                "last_receive_time": now_ts,
                "reminded": False,
            }
            self._mute_until.pop(from_user, None)
            self._mark_user_recovered(from_user, now_ts)
            logger.info("用户 [%s] 有新入站消息，已恢复发送窗口", from_user[:20])
            should_save = True
            self._record_contact_activity(
                from_user,
                display_name=display_name or self._contact_name(from_user),
                inbound_at=now_ts,
                context_token_at=context_token_at,
            )
        elif from_user and context_token_at:
            self._record_contact_activity(
                from_user,
                display_name=display_name or self._contact_name(from_user),
                context_token_at=context_token_at,
            )

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
            with self._ag_inbox_lock:
                self.ag_inbox.append({"from": from_name, "text": text})

        # 派发基础消息事件
        self.event_bus.publish(
            Event(
                EVENT_MESSAGE_RECEIVED,
                {
                    "bot_id": self.client.get_bot_id() or "",
                    "from_user": from_user,
                    "from_name": from_name,
                    "text": text,
                    "msg": msg,
                    "media_paths": media_paths,
                },
            )
        )

        if text.startswith("/"):
            cmd_reply = self._handle_command(text, from_user)
            if cmd_reply == "":
                # 插件已自行处理回复
                return
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
                        self.send(from_user, "## 🔄 正在重试\n\n- 正在为您重新生成回答", source="system")
                        ai_key = self._account_scoped_user_id(from_user)
                        ai_reply = self.ai_manager.chat(ai_key, retry_text) if self.ai_manager else ""
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

        # 非命令消息触发旧 Webhook（兼容）
        self._trigger_webhook(from_user, from_name, text, msg)

        # 插件持有该用户会话时（如便签收集中），跳过 AI
        if self.plugin_registry.find_session_holder(from_user):
            logger.debug("插件持有用户 [%s] 会话，跳过 AI", from_user[:16])
            return

        if self.ai_manager and text and not text.startswith("/"):

            def _async_ai_worker(uid, msg_text):
                try:
                    self.send_typing(uid)
                    chunk_buffer = ""
                    first_send = True
                    # 缓冲块，尽量一次性发送，防止碎消息刷屏，微信单条限制约 5200 字符
                    ai_key = self._account_scoped_user_id(uid)
                    for chunk in self.ai_manager.chat_stream(ai_key, msg_text):
                        chunk_buffer += chunk
                        # 超过 4500 字且遇到换行，或者极限达到 5000 字时，分段发送
                        if len(chunk_buffer) >= 5000 or (len(chunk_buffer) >= 4500 and "\n\n" in chunk):
                            prefix = "🤖 " if first_send else ""
                            result = self.send(uid, f"{prefix}{chunk_buffer}", source="ai")
                            if not result.get("ok"):
                                logger.error("AI 后台回复片段失败 [%s]: %s", uid[:16], result.get("error"))
                            first_send = False
                            chunk_buffer = ""
                            self.send_typing(uid)  # 继续打字状态

                    if chunk_buffer:
                        prefix = "🤖 " if first_send else ""
                        result = self.send(uid, f"{prefix}{chunk_buffer}", source="ai")
                        if not result.get("ok"):
                            logger.error("AI 后台回复最后片段失败 [%s]: %s", uid[:16], result.get("error"))

                    logger.info("AI 流式回复已发送完毕 [%s]", uid[:16])
                except Exception as exc:
                    logger.error("AI 后台回复处理失败 [%s]: %s", uid[:16], exc)
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

    def send(
        self,
        to: str,
        text: str,
        *,
        source: str = "api",
        title: str = "",
        allow_buffer: bool = True,
        request_id: str = "",
    ) -> dict:
        """
        发送消息的高级接口。
        to: 可以是 user_id，也可以是联系人名称。
        """
        user_id = self.find_user_id(to)
        if not user_id:
            if not to:
                return {"ok": False, "error": "缺少收件人。iLink 限制：对方需先给你发一条消息，系统才能获取其 user_id"}
            return {"ok": False, "error": f"找不到联系人「{to}」。对方需先给你发过消息才会出现在联系人列表中"}

        normalized_request_id = self._normalize_request_id(request_id)
        if request_id and not normalized_request_id:
            return {
                "ok": False,
                "delivery_stage": "failed",
                "message_id": None,
                "pending_message_id": None,
                "blocked_reason": "invalid_request_id",
                "overflow_session_id": None,
                "error": "request_id 格式无效",
            }
        request_fingerprint = self._delivery_request_fingerprint(user_id, text, source, title)
        if normalized_request_id:
            existing = self._existing_request_delivery(normalized_request_id, request_fingerprint)
            if existing is not None:
                return existing

        return self._send_resolved(
            user_id=user_id,
            contact_name=self._contact_name(user_id, to),
            text=text,
            context_token=self.get_context_token(user_id),
            source=source,
            title=title,
            allow_buffer=allow_buffer,
            rotate_session_on_warn=True,
            record_timeline=True,
            message_id=normalized_request_id or None,
            request_fingerprint=request_fingerprint if normalized_request_id else None,
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

        filename = self._save_outbound_image(file_data)
        return self._send_resolved(
            user_id=user_id,
            contact_name=self._contact_name(user_id, to),
            text=f"[图片:{filename}]",
            context_token=self.get_context_token(user_id),
            source="image",
            allow_buffer=True,
            rotate_session_on_warn=True,
            record_timeline=True,
            image_data=file_data,
            media_name=filename,
        )

    def send_video(self, to: str, file_data: bytes, *, play_length: int = 0) -> dict:
        """发送视频消息。"""
        user_id = self.find_user_id(to)
        if not user_id:
            return {"ok": False, "error": f"找不到联系人「{to}」。对方需先给你发过消息才会出现在联系人列表中"}

        context_token = self.get_context_token(user_id)
        filename = None

        def _send_video():
            nonlocal filename
            filename = self._save_outbound_video(file_data)
            return self.client.send_video(user_id, file_data, context_token, play_length=play_length)

        return self._send_non_replayable_resolved(
            user_id=user_id,
            contact_name=self._contact_name(user_id, to),
            record_text=lambda: f"[视频:{filename}]" if filename else "[视频]",
            context_token=context_token,
            source="video",
            media_label="视频",
            send_action=_send_video,
            media_name_getter=lambda: filename,
        )

    def send_video_path(self, to: str, filepath: str, *, play_length: int = 0) -> dict:
        """从文件路径发送视频消息，减少原视频在内存中的重复驻留。"""
        user_id = self.find_user_id(to)
        if not user_id:
            return {"ok": False, "error": f"找不到联系人「{to}」。对方需先给你发过消息才会出现在联系人列表中"}

        context_token = self.get_context_token(user_id)
        filename = None

        def _send_video_path():
            nonlocal filename
            filename, save_path = self._move_outbound_video(filepath)
            if hasattr(self.client, "send_video_path"):
                return self.client.send_video_path(user_id, save_path, context_token, play_length=play_length)
            with open(save_path, "rb") as fh:
                return self.client.send_video(user_id, fh.read(), context_token, play_length=play_length)

        return self._send_non_replayable_resolved(
            user_id=user_id,
            contact_name=self._contact_name(user_id, to),
            record_text=lambda: f"[视频:{filename}]" if filename else "[视频]",
            context_token=context_token,
            source="video",
            media_label="视频",
            send_action=_send_video_path,
            media_name_getter=lambda: filename,
        )

    def send_voice(self, to: str, file_data: bytes, *, playtime_ms: int = 0, text: str = "") -> dict:
        """发送语音消息。"""
        if not media.is_silk(file_data):
            return {"ok": False, "error": "语音消息只支持 SILK v3 编码（#!SILK_V3），请先转换后上传。"}

        user_id = self.find_user_id(to)
        if not user_id:
            return {"ok": False, "error": f"找不到联系人「{to}」。对方需先给你发过消息才会出现在联系人列表中"}

        context_token = self.get_context_token(user_id)
        filename = None

        def _send_voice():
            nonlocal filename
            filename = self._save_outbound_voice(file_data)
            return self.client.send_voice(
                user_id,
                file_data,
                context_token,
                playtime_ms=playtime_ms,
                text=text,
            )

        return self._send_non_replayable_resolved(
            user_id=user_id,
            contact_name=self._contact_name(user_id, to),
            record_text=lambda: f"[语音:{filename}]" if filename else "[语音]",
            context_token=context_token,
            source="voice",
            media_label="语音",
            send_action=_send_voice,
            media_name_getter=lambda: filename,
        )

    def send_file(self, to: str, file_data: bytes, *, file_name: str = "file.bin", text: str = "") -> dict:
        """发送文件附件消息。"""
        user_id = self.find_user_id(to)
        if not user_id:
            return {"ok": False, "error": f"找不到联系人「{to}」。对方需先给你发过消息才会出现在联系人列表中"}

        display_name = os.path.basename(file_name or "file.bin") or "file.bin"
        context_token = self.get_context_token(user_id)
        filename = None

        def _send_file():
            nonlocal filename
            filename = self._save_outbound_file(file_data, display_name)
            return self.client.send_file(
                user_id,
                file_data,
                context_token,
                file_name=display_name,
                text=text,
            )

        result = self._send_non_replayable_resolved(
            user_id=user_id,
            contact_name=self._contact_name(user_id, to),
            record_text=f"{text}\n[文件:{display_name}]" if text else f"[文件:{display_name}]",
            context_token=context_token,
            source="file",
            media_label="文件",
            send_action=_send_file,
            media_name_getter=lambda: filename,
        )
        if result.get("ok"):
            result["file_name"] = display_name
        return result

    def send_file_path(self, to: str, filepath: str, *, file_name: str = "", text: str = "") -> dict:
        """从文件路径发送附件消息，避免原文件整体读入内存。"""
        user_id = self.find_user_id(to)
        if not user_id:
            return {"ok": False, "error": f"找不到联系人「{to}」。对方需先给你发过消息才会出现在联系人列表中"}

        display_name = os.path.basename(file_name or filepath or "file.bin") or "file.bin"
        context_token = self.get_context_token(user_id)
        filename = None

        def _send_file_path():
            nonlocal filename
            filename, save_path = self._copy_outbound_file(filepath, display_name)
            if hasattr(self.client, "send_file_path"):
                return self.client.send_file_path(
                    user_id,
                    save_path,
                    context_token,
                    file_name=display_name,
                    text=text,
                )
            with open(save_path, "rb") as fh:
                return self.client.send_file(
                    user_id,
                    fh.read(),
                    context_token,
                    file_name=display_name,
                    text=text,
                )

        result = self._send_non_replayable_resolved(
            user_id=user_id,
            contact_name=self._contact_name(user_id, to),
            record_text=f"{text}\n[文件:{display_name}]" if text else f"[文件:{display_name}]",
            context_token=context_token,
            source="file",
            media_label="文件",
            send_action=_send_file_path,
            media_name_getter=lambda: filename,
        )
        if result.get("ok"):
            result["file_name"] = display_name
        return result

    def send_reference_text(self, to: str, text: str, *, ref_text: str = "", ref_title: str = "") -> dict:
        """发送带引用文本的消息。"""
        user_id = self.find_user_id(to)
        if not user_id:
            return {"ok": False, "error": f"找不到联系人「{to}」。对方需先给你发过消息才会出现在联系人列表中"}
        if not text:
            return {"ok": False, "error": "引用消息正文不能为空"}
        if not ref_text and not ref_title:
            return {"ok": False, "error": "引用消息需要 ref_text 或 ref_title"}

        context_token = self.get_context_token(user_id)
        fallback_text = format_reference_fallback_text(text, ref_text=ref_text, ref_title=ref_title)
        result = self._send_resolved(
            user_id=user_id,
            contact_name=self._contact_name(user_id, to),
            text=fallback_text,
            context_token=context_token,
            source="reference",
            title="引用消息",
            allow_buffer=True,
            rotate_session_on_warn=True,
            record_timeline=True,
            extra_meta={"native_reference": False, "fallback": "text_quote"},
        )
        if result.get("ok"):
            result["native_reference"] = False
            result["fallback"] = "text_quote"
        return result

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
                old_bot_id = getattr(self.client, "bot_id", "")
                old_user_id = getattr(self.client, "user_id", "")
                old_base_url = getattr(self.client, "base_url", "")
                msgs = self.client.get_updates(timeout=35)
                if old_bot_id and not self.client.logged_in:
                    self.record_account_event(
                        "auth_error",
                        reason="token_invalid",
                        bot_id=old_bot_id,
                        ilink_user_id=old_user_id or "",
                        base_url=old_base_url or "",
                    )
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
        self.cleanup_expired_pending_messages(force=True)
        self.cleanup_expired_media_files(force=True)
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

        self._keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
        self._keepalive_thread.start()
        self._pending_cleanup_thread = threading.Thread(target=self._pending_cleanup_loop, daemon=True)
        self._pending_cleanup_thread.start()
        logger.info("WeChatBridge 已启动")

    def stop(self):
        self._running = False
        if getattr(self, "plugin_registry", None):
            try:
                self.plugin_registry.stop_all()
            except Exception as e:
                logger.error("停止插件失败: %s", e)
        if getattr(self, "_poll_thread", None):
            self._poll_thread.join(timeout=10)
        if getattr(self, "_keepalive_thread", None):
            self._keepalive_thread.join(timeout=2)
        if getattr(self, "_pending_cleanup_thread", None):
            self._pending_cleanup_thread.join(timeout=2)
        logger.info("WeChatBridge 已停止")
