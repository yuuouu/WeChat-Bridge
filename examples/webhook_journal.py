#!/usr/bin/env python3
"""
有状态 Webhook 示例 — 微信日记收集器。

演示如何基于 WeChat Bridge 的 Webhook 构建一个"带会话状态"的外部服务：
用户发送 /rj 开始记录，之后发送的文字或图片都会被收集，
发送 /exit 结束并汇总展示。

与 webhook_receiver.py（无状态命令响应）互补，本示例展示的是：
  - 有状态的 session 管理
  - all_messages 模式下的非会话消息快速过滤
  - 异步回写到微信

用法：
1. 启动 WeChat Bridge，并在 Web UI 中配置：
   - Webhook Enabled = true
   - Webhook Mode = all_messages  ← 必须！否则 session 中的普通文本不会被转发
   - Webhook URL = http://你的机器IP:18081/webhook
2. 设置环境变量：
   export BRIDGE_BASE_URL=http://192.168.100.1:5200
   export BRIDGE_API_TOKEN=your-token   # 若未设置 API_TOKEN，可留空
3. 运行：
   python3 examples/webhook_journal.py
4. 在微信里：
   /rj                                → 开始记录
   https://github.com/whoisyurii/...  → 记录一条
   优化 GitHub 主页                    → 再记录一条
   /exit          → 结束，Bot 回复汇总

⚠️ 为什么必须用 all_messages 模式？
   unknown_command 模式下，Bridge 只会转发以 / 开头的未知命令。
   普通文本（如"今天天气不错"）会被 Bridge 拦截，你的 Webhook 永远收不到。
   本示例在入口处做了 O(1) 的 session 检查，非会话消息瞬间丢弃，
   因此 all_messages 模式不会带来任何性能或安全问题。
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ.get("WEBHOOK_LISTEN_HOST", "0.0.0.0")
PORT = int(os.environ.get("WEBHOOK_LISTEN_PORT", "18081"))
BRIDGE_BASE_URL = os.environ.get("BRIDGE_BASE_URL", "http://127.0.0.1:5200").rstrip("/")
BRIDGE_API_TOKEN = os.environ.get("BRIDGE_API_TOKEN", "").strip()

# Session 超时（秒），超过此时间无新消息则自动结束
SESSION_TIMEOUT = int(os.environ.get("SESSION_TIMEOUT_MINUTES", "30")) * 60

# 主命令用 /rj（日记），保留 /note 作为向后兼容别名
START_COMMANDS = {"/rj", "/note"}
EXIT_COMMANDS = {"/exit"}


# ── 数据模型 ──


@dataclass
class NoteSession:
    """一个用户的笔记录入会话。"""

    user_id: str
    user_name: str
    start_time: float
    last_activity: float
    entries: list[str] = field(default_factory=list)


# 全局 session 字典：user_id → NoteSession
sessions: dict[str, NoteSession] = {}
sessions_lock = threading.Lock()


# ── Bridge 通信 ──


def send_wechat_message(to_user: str, text: str) -> tuple[bool, str]:
    """调用 Bridge /api/send 异步回写消息到微信。"""
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
            body = resp.read().decode("utf-8", "replace")
        return True, body
    except urllib.error.HTTPError as exc:
        return False, exc.read().decode("utf-8", "replace")
    except Exception as exc:
        return False, str(exc)


# ── Session 管理 ──


def start_session(user_id: str, user_name: str):
    """开始一个新的日记录入 session。"""
    now = time.time()
    with sessions_lock:
        if user_id in sessions:
            send_wechat_message(user_id, "📝 你已经在记录日记了，继续发送文字或图片即可。\n发送 /exit 结束。")
            return
        sessions[user_id] = NoteSession(
            user_id=user_id,
            user_name=user_name,
            start_time=now,
            last_activity=now,
        )
    send_wechat_message(
        user_id,
        "📝 开始记录日记...\n直接发送文字或图片，我会帮你记下来。\n发送 /exit 结束录入。\n(建议先发送 /ai off 关闭助手打扰)",
    )
    _log("session_start", user_id=user_id, user_name=user_name)


def end_session(user_id: str, reason: str = "user_exit"):
    """结束 session 并回复汇总。"""
    with sessions_lock:
        session = sessions.pop(user_id, None)

    if not session:
        if reason == "user_exit":
            send_wechat_message(user_id, "❌ 当前没有进行中的日记录入。\n发送 /rj 开始。")
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

    send_wechat_message(user_id, summary)
    _log("session_end", user_id=user_id, reason=reason, count=count)


def record_message(user_id: str, text: str):
    """向当前 session 追加一条记录。"""
    with sessions_lock:
        session = sessions.get(user_id)
        if not session:
            return
        session.entries.append(text)
        session.last_activity = time.time()
        count = len(session.entries)

    _log("message_recorded", user_id=user_id, text=text[:60], count=count)


# ── 超时扫描 ──


def _timeout_scanner():
    """后台线程：每 60 秒扫描超时 session 并自动结束。"""
    while True:
        time.sleep(60)
        now = time.time()
        with sessions_lock:
            expired = [
                uid
                for uid, s in sessions.items()
                if now - s.last_activity > SESSION_TIMEOUT
            ]
        for uid in expired:
            _log("session_timeout", user_id=uid)
            end_session(uid, reason="timeout")


# ── Webhook 处理 ──


def process_webhook(payload: dict):
    """处理来自 Bridge 的 webhook payload。"""
    from_user = payload.get("from_user", "")
    from_name = payload.get("from_name", "")
    text = payload.get("text", "")
    is_command = payload.get("is_command", False)
    command = payload.get("command", "")

    # 命令路由
    if is_command:
        if command in START_COMMANDS:
            start_session(from_user, from_name)
            return
        if command in EXIT_COMMANDS:
            end_session(from_user, reason="user_exit")
            return
        # 其他未知命令，忽略
        return

    # 非命令消息：有活跃 session 则记录
    with sessions_lock:
        has_session = from_user in sessions

    if has_session:
        record_message(from_user, text)


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length).decode("utf-8", "replace")
        try:
            payload = json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"ok":false,"error":"invalid json"}')
            return

        from_user = payload.get("from_user", "")
        is_command = payload.get("is_command", False)
        command = payload.get("command", "")

        # ── 快速过滤（核心！）──
        # 非命令 + 非 session 用户 → 立刻丢弃，零开销
        if not is_command and from_user not in sessions:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true,"ignored":true}')
            return

        # 仅处理 /rj、/note、/exit 命令和 session 中的消息
        if is_command and command not in START_COMMANDS | EXIT_COMMANDS:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true,"ignored":true}')
            return

        # 异步处理，立刻返回 200
        threading.Thread(target=process_webhook, args=(payload,), daemon=True).start()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, format, *args):
        pass  # 静默 HTTP 日志


# ── 工具 ──


def _log(event: str, **kwargs):
    print(json.dumps({"event": event, **kwargs}, ensure_ascii=False), flush=True)


# ── 入口 ──

if __name__ == "__main__":
    # 启动超时扫描线程
    threading.Thread(target=_timeout_scanner, daemon=True).start()

    _log(
        "startup",
        listen=f"http://{HOST}:{PORT}/webhook",
        bridge_base_url=BRIDGE_BASE_URL,
        bridge_api_token_set=bool(BRIDGE_API_TOKEN),
        session_timeout_minutes=SESSION_TIMEOUT // 60,
        start_commands=sorted(START_COMMANDS),
    )
    print(
        f"\n💡 提示：请确保 WeChat Bridge 的 Webhook 模式设为 all_messages\n"
        f"   否则 session 中的普通文本或图片不会被转发到此服务。\n"
        f"   在微信里发送 /rj 开始记录，发送 /exit 结束。\n",
        flush=True,
    )
    ThreadingHTTPServer((HOST, PORT), WebhookHandler).serve_forever()
