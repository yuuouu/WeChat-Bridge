#!/usr/bin/env python3
"""
有状态 Webhook 示例 — 微信日记收集器（插件化版本）。

作为 webhook_manager 插件运行（推荐）：
    python3 examples/webhook_manager.py

单独运行：
    python3 examples/webhook_journal.py

环境变量：
    BRIDGE_BASE_URL        Bridge 地址（默认 http://127.0.0.1:5200）
    BRIDGE_API_TOKEN       Bridge API Token
    SESSION_TIMEOUT_MINUTES  空闲会话超时（默认 30 分钟）
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

BRIDGE_BASE_URL = os.environ.get("BRIDGE_BASE_URL", "http://127.0.0.1:5200").rstrip("/")
BRIDGE_API_TOKEN = os.environ.get("BRIDGE_API_TOKEN", "").strip()
SESSION_TIMEOUT = int(os.environ.get("SESSION_TIMEOUT_MINUTES", "30")) * 60

START_COMMANDS = {"/rj", "/note"}
EXIT_COMMANDS = {"/exit"}


@dataclass
class NoteSession:
    user_id: str
    user_name: str
    start_time: float
    last_activity: float
    entries: list[str] = field(default_factory=list)


def _log(event: str, **kwargs):
    print(json.dumps({"event": event, **kwargs}, ensure_ascii=False), flush=True)


def _send(to_user: str, text: str) -> tuple[bool, str]:
    payload = json.dumps({"to": to_user, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        f"{BRIDGE_BASE_URL}/api/send",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    if BRIDGE_API_TOKEN:
        req.add_header("Authorization", f"Bearer {BRIDGE_API_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return True, resp.read().decode("utf-8", "replace")
    except Exception as exc:
        return False, str(exc)


class JournalPlugin:
    name = "journal"

    def __init__(self) -> None:
        self.sessions: dict[str, NoteSession] = {}
        self.sessions_lock = threading.Lock()
        self._stop_event = threading.Event()

    @property
    def commands(self) -> list[str]:
        return sorted(list(START_COMMANDS | EXIT_COMMANDS))

    def get_command_specs(self) -> list[dict]:
        return [
            {"command": "/rj", "description": "开始记录微信日记"},
            {"command": "/note", "description": "开始记录微信日记 (别名)"},
            {"command": "/exit", "description": "结束日记录入并生成汇总"},
        ]

    def has_session(self, user_id: str) -> bool:
        with self.sessions_lock:
            return user_id in self.sessions

    def handle(self, payload: dict) -> None:
        from_user = payload.get("from_user", "")
        from_name = payload.get("from_name", "")
        text = payload.get("text", "").strip()
        command = payload.get("command", "")
        is_command = payload.get("is_command", False)

        if not from_user or not text:
            return

        if is_command:
            if command in START_COMMANDS:
                self._start_session(from_user, from_name)
            elif command in EXIT_COMMANDS:
                self._end_session(from_user, reason="user_exit")
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
                _send(user_id, "📝 你已经在记录日记了，继续发送文字或图片即可。\n发送 /exit 结束。")
                return
            self.sessions[user_id] = NoteSession(
                user_id=user_id, user_name=user_name, start_time=now, last_activity=now
            )
        _send(user_id, "📝 开始记录日记...\n直接发送文字或图片，我会帮你记下来。\n发送 /exit 结束录入。")
        _log("session_start", user_id=user_id, user_name=user_name)

    def _end_session(self, user_id: str, reason: str = "user_exit"):
        with self.sessions_lock:
            session = self.sessions.pop(user_id, None)
        if not session:
            if reason == "user_exit":
                _send(user_id, "❌ 当前没有进行中的日记录入。\n发送 /rj 开始。")
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
        _send(user_id, summary)
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
    _SCRIPT_DIR = Path(__file__).resolve().parent
    sys.path.insert(0, str(_SCRIPT_DIR))
    from webhook_manager import WebhookManager

    mgr = WebhookManager()
    mgr.load_plugin(JournalPlugin())
    mgr.run()
