#!/usr/bin/env python3
"""
有状态插件示例 — 会话式便签收集器。

配置：
    SESSION_TIMEOUT_MINUTES  空闲会话超时，默认 30 分钟
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field

from plugin_base import Plugin

logger = logging.getLogger(__name__)

SESSION_TIMEOUT = int(os.environ.get("SESSION_TIMEOUT_MINUTES", "30")) * 60
START_COMMAND = "/note"
EXIT_COMMAND = "/exit"


@dataclass
class NoteSession:
    user_id: str
    user_name: str
    start_time: float
    last_activity: float
    entries: list[str] = field(default_factory=list)


class SessionNotesPlugin(Plugin):
    """会话式便签收集器。"""

    name = "session-notes"
    description = "会话式便签收集器 (/note 开始, /exit 结束)"
    commands = [START_COMMAND, EXIT_COMMAND]

    def __init__(self) -> None:
        super().__init__()
        self.sessions: dict[str, NoteSession] = {}
        self.sessions_lock = threading.Lock()
        self._stop_event = threading.Event()

    def get_command_specs(self) -> list[dict]:
        return [
            {"command": START_COMMAND, "description": "开始收集一组便签"},
            {"command": EXIT_COMMAND, "description": "结束当前插件会话"},
        ]

    def has_session(self, user_id: str) -> bool:
        with self.sessions_lock:
            return user_id in self.sessions

    def handle(self, payload: dict) -> None:
        from_user = payload.get("from_user", "")
        from_name = payload.get("from_name", "")
        command = payload.get("command", "")

        if not from_user:
            return

        if command == START_COMMAND:
            self._start_session(from_user, from_name)
        elif command == EXIT_COMMAND:
            self._end_session(from_user, reason="user_exit")

    def on_message(self, event) -> None:
        from_user = event.data.get("from_user", "")
        text = event.data.get("text", "").strip()

        if not text or text.startswith("/") or not self.has_session(from_user):
            return

        with self.sessions_lock:
            session = self.sessions.get(from_user)
            if session:
                session.entries.append(text)
                session.last_activity = time.time()

    def on_start(self) -> None:
        threading.Thread(target=self._timeout_scanner, daemon=True).start()

    def on_stop(self) -> None:
        self._stop_event.set()

    def _start_session(self, user_id: str, user_name: str) -> None:
        now = time.time()
        with self.sessions_lock:
            if user_id in self.sessions:
                self.send_reply(user_id, "你已经在收集便签了，继续发送文字即可。发送 /exit 结束。")
                return
            self.sessions[user_id] = NoteSession(
                user_id=user_id,
                user_name=user_name,
                start_time=now,
                last_activity=now,
            )
        self.send_reply(user_id, "开始收集便签。直接发送文字，我会暂存到当前会话；发送 /exit 结束。")

    def _end_session(self, user_id: str, reason: str = "user_exit") -> None:
        with self.sessions_lock:
            session = self.sessions.pop(user_id, None)
        if not session:
            if reason == "user_exit":
                self.send_reply(user_id, "当前没有进行中的便签会话。发送 /note 开始。")
            return

        end_label = "自动超时结束" if reason == "timeout" else "手动结束"
        if not session.entries:
            self.send_reply(user_id, f"便签会话已{end_label}，共记录 0 条。")
            return

        lines = [f"便签会话已{end_label}，共记录 {len(session.entries)} 条：", ""]
        for index, entry in enumerate(session.entries, 1):
            lines.append(f"{index}. {entry}")
        self.send_reply(user_id, "\n".join(lines))

    def _timeout_scanner(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(60)
            now = time.time()
            with self.sessions_lock:
                expired = [
                    uid for uid, session in self.sessions.items() if now - session.last_activity > SESSION_TIMEOUT
                ]
            for uid in expired:
                self._end_session(uid, reason="timeout")


PLUGIN_CLASS = SessionNotesPlugin
