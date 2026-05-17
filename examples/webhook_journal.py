#!/usr/bin/env python3
"""
有状态 Webhook 示例 — 微信日记收集器（插件化版本）。

作为 webhook_manager 插件运行（推荐）：
    自动被 discover_and_register_plugins 发现并加载。

单独运行：
    python3 examples/webhook_journal.py

环境变量：
    SESSION_TIMEOUT_MINUTES  空闲会话超时（默认 30 分钟）
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR.parent / "app"))

from plugin_base import Plugin  # noqa: E402

logger = logging.getLogger(__name__)

SESSION_TIMEOUT = int(os.environ.get("SESSION_TIMEOUT_MINUTES", "30")) * 60

START_COMMANDS = {"/rj"}
EXIT_COMMANDS = {"/exit"}


def _log(event: str, **kwargs):
    print(json.dumps({"event": event, **kwargs}, ensure_ascii=False), flush=True)


@dataclass
class NoteSession:
    user_id: str
    user_name: str
    start_time: float
    last_activity: float
    entries: list[str] = field(default_factory=list)


class JournalPlugin(Plugin):
    """微信日记收集器插件。"""

    name = "journal"
    description = "微信日记收集器 (/rj 开始, /exit 结束)"
    commands = ["/rj", "/exit"]

    def __init__(self) -> None:
        super().__init__()
        self.sessions: dict[str, NoteSession] = {}
        self.sessions_lock = threading.Lock()
        self._stop_event = threading.Event()

    def get_command_specs(self) -> list[dict]:
        return [
            {"command": "/rj", "description": "开始记录微信日记"},
            {"command": "/exit", "description": "结束日记录入并生成汇总"},
        ]

    def has_session(self, user_id: str) -> bool:
        with self.sessions_lock:
            return user_id in self.sessions

    def handle(self, payload: dict) -> None:
        """处理通过 commands.py 路由过来的指令。"""
        from_user = payload.get("from_user", "")
        from_name = payload.get("from_name", "")
        command = payload.get("command", "")

        if not from_user:
            return

        if command in START_COMMANDS:
            self._start_session(from_user, from_name)
        elif command in EXIT_COMMANDS:
            self._end_session(from_user, reason="user_exit")

    def on_message(self, event) -> None:
        """处理普通的聊天消息，判断是否有 session 进行记录。"""
        from_user = event.data.get("from_user", "")
        text = event.data.get("text", "").strip()

        # 忽略空消息或者指令
        if not text or text.startswith("/"):
            return

        if not self.has_session(from_user):
            return

        with self.sessions_lock:
            session = self.sessions.get(from_user)
            if session:
                session.entries.append(text)
                session.last_activity = time.time()
                _log("message_recorded", user_id=from_user, text=text[:30], count=len(session.entries))

    def on_start(self) -> None:
        threading.Thread(target=self._timeout_scanner, daemon=True).start()

    def on_stop(self) -> None:
        self._stop_event.set()

    def _start_session(self, user_id: str, user_name: str):
        now = time.time()
        with self.sessions_lock:
            if user_id in self.sessions:
                self.send_reply(user_id, "📝 你已经在记录日记了，继续发送文字或图片即可。\n发送 /exit 结束。")
                return
            self.sessions[user_id] = NoteSession(
                user_id=user_id, user_name=user_name, start_time=now, last_activity=now
            )
        self.send_reply(user_id, "📝 开始记录日记...\n直接发送文字或图片，我会帮你记下来。\n发送 /exit 结束录入。")
        _log("session_start", user_id=user_id, user_name=user_name)

    def _end_session(self, user_id: str, reason: str = "user_exit"):
        with self.sessions_lock:
            session = self.sessions.pop(user_id, None)
        if not session:
            if reason == "user_exit":
                self.send_reply(user_id, "❌ 当前没有进行中的日记录入。\n发送 /rj 开始。")
            return
        count = len(session.entries)
        end_label = "自动超时结束" if reason == "timeout" else "手动结束"
        if count == 0:
            summary = f"📝 日记录入已{end_label}，共记录 0 条消息。"
        else:
            lines = [f"📝 日记录入已{end_label}，共记录 {count} 条消息：", ""]
            for i, entry in enumerate(session.entries, 1):
                lines.append(f"  {i}. {entry}")
            summary = "\n".join(lines)
        self.send_reply(user_id, summary)
        _log("session_end", user_id=user_id, reason=reason, count=count)

    def _timeout_scanner(self):
        while not self._stop_event.is_set():
            time.sleep(60)
            now = time.time()
            with self.sessions_lock:
                expired = [uid for uid, s in self.sessions.items() if now - s.last_activity > SESSION_TIMEOUT]
            for uid in expired:
                self._end_session(uid, reason="timeout")


PLUGIN_CLASS = JournalPlugin

if __name__ == "__main__":
    from webhook_manager import WebhookManager  # noqa: E402

    mgr = WebhookManager()
    mgr.load_plugin(JournalPlugin())
    mgr.run()
