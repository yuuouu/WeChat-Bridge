#!/usr/bin/env python3
"""
最小可运行的异步 Webhook Receiver 示例。

用法：
1. 启动 WeChat Bridge，并在 Web UI 中配置：
   - Webhook Enabled = true
   - Webhook Mode = unknown_command 或 all_messages
   - Webhook URL = http://你的机器IP:18080/webhook
2. 设置环境变量：
   export BRIDGE_BASE_URL=http://192.168.100.1:5200
   export BRIDGE_API_TOKEN=your-token   # 若未设置 API_TOKEN，可留空
3. 运行：
   python3 examples/webhook_receiver.py
4. 在微信里给 Bot 发送未知命令，例如：
   /weather shanghai

这个示例会：
- 接收 WeChat Bridge 的 Webhook
- 解析 command / args
- 异步调用 /api/send 回写结果到微信
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


HOST = os.environ.get("WEBHOOK_LISTEN_HOST", "0.0.0.0")
PORT = int(os.environ.get("WEBHOOK_LISTEN_PORT", "18080"))
BRIDGE_BASE_URL = os.environ.get("BRIDGE_BASE_URL", "http://127.0.0.1:5200").rstrip("/")
BRIDGE_API_TOKEN = os.environ.get("BRIDGE_API_TOKEN", "").strip()


def send_wechat_message(to_user: str, text: str) -> tuple[bool, str]:
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
    except Exception as exc:  # pragma: no cover - 示例脚本
        return False, str(exc)


def build_reply(payload: dict) -> str:
    command = payload.get("command", "")
    args = payload.get("args", "")
    from_name = payload.get("from_name", "用户")
    text = payload.get("text", "")

    if command == "/weather":
        city = args or "shanghai"
        return f"天气服务示例收到请求：{city}\n这是异步回写示例，你可以在这里接入真实天气 API。"

    if command == "/echo":
        return f"Echo from webhook:\n{args or '(empty)'}"

    return (
        f"Webhook 已收到来自 {from_name} 的消息。\n"
        f"command={command or '(none)'}\n"
        f"args={args or '(empty)'}\n"
        f"text={text}"
    )


def handle_incoming(payload: dict):
    to_user = payload.get("from_user", "")
    if not to_user:
        print("忽略：缺少 from_user", flush=True)
        return

    reply = build_reply(payload)
    ok, result = send_wechat_message(to_user, reply)
    print(
        json.dumps(
            {
                "event": "callback_result",
                "ok": ok,
                "to_user": to_user,
                "reply": reply,
                "bridge_response": result,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


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

        print(
            json.dumps(
                {
                    "event": "incoming_webhook",
                    "path": self.path,
                    "payload": payload,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

        threading.Thread(target=handle_incoming, args=(payload,), daemon=True).start()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, format, *args):  # pragma: no cover - 示例脚本
        pass


if __name__ == "__main__":
    print(
        json.dumps(
            {
                "event": "startup",
                "listen": f"http://{HOST}:{PORT}/webhook",
                "bridge_base_url": BRIDGE_BASE_URL,
                "bridge_api_token_set": bool(BRIDGE_API_TOKEN),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    ThreadingHTTPServer((HOST, PORT), WebhookHandler).serve_forever()
