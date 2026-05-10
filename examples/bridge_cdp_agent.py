#!/usr/bin/env python3
"""
Bridge CDP Agent — 通过微信远程操控 Mac 上的 Antigravity (CDP 模式)。

本工具通过 Chrome DevTools Protocol (CDP) 直接与 Antigravity / Cursor 的渲染进程通信，
原生复用 IDE 内的会话上下文、工具能力 (终端、文件读写等) 及自动接受机制。

依赖:
pip3 install websockets

用法：
1. 启动 Antigravity 时增加 --remote-debugging-port=9333 参数。
   （例如：open -a "Antigravity" --args --remote-debugging-port=9333）
2. 启动 WeChat Bridge，Web UI 中配置：
   - Webhook Enabled = true
   - Webhook Mode = all_messages
   - Webhook URL = http://Mac机器IP:18082/webhook
3. 设置环境变量：
   export BRIDGE_BASE_URL=http://192.168.100.1:5200
   export BRIDGE_API_TOKEN=your-token
   export ALLOWED_USERS=your_wechat_user_id
4. 运行：
   python3 examples/bridge_cdp_agent.py
"""

import asyncio
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import websockets
except ImportError:
    print("缺少依赖，请先安装: pip3 install websockets")
    exit(1)

HOST = os.environ.get("WEBHOOK_LISTEN_HOST", "0.0.0.0")
PORT = int(os.environ.get("WEBHOOK_LISTEN_PORT", "18082"))
BRIDGE_BASE_URL = os.environ.get("BRIDGE_BASE_URL", "http://127.0.0.1:5200").rstrip("/")
BRIDGE_API_TOKEN = os.environ.get("BRIDGE_API_TOKEN", "").strip()
SESSION_TIMEOUT = int(os.environ.get("SESSION_TIMEOUT_MINUTES", "30")) * 60
CDP_PORT = int(os.environ.get("CDP_PORT", "9333"))
CDP_TIMEOUT = int(os.environ.get("CDP_TIMEOUT", "180"))
ALLOWED_USERS: set[str] = {
    u.strip() for u in os.environ.get("ALLOWED_USERS", "").split(",") if u.strip()
}

MAX_CHUNK = 4500

def _log(event: str, **kwargs):
    print(json.dumps({"event": event, **kwargs}, ensure_ascii=False), flush=True)

# ── WeChat Bridge API ─────────────────────────────────────────────────────────

def _api_post(path: str, body: dict) -> tuple[bool, str]:
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{BRIDGE_BASE_URL}{path}",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    if BRIDGE_API_TOKEN:
        req.add_header("Authorization", f"Bearer {BRIDGE_API_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return True, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return False, exc.read().decode("utf-8", "replace")
    except Exception as exc:
        return False, str(exc)

def _send(to_user: str, text: str) -> bool:
    ok, result = _api_post("/api/send", {"to": to_user, "text": text, "markdown": True})
    if not ok:
        _log("send_failed", to=to_user[:16], error=result[:120])
    return ok

# ── CDP Client ────────────────────────────────────────────────────────────────

class CDPClient:
    def __init__(self, ws_url: str):
        self.ws_url = ws_url
        self.ws = None
        self._msg_id = 0
        self._pending = {}
        self._loop = asyncio.get_event_loop()
        self._recv_task = None

    async def connect(self):
        self.ws = await websockets.connect(self.ws_url)
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def _recv_loop(self):
        try:
            async for msg in self.ws:
                data = json.loads(msg)
                if "id" in data and data["id"] in self._pending:
                    self._pending[data["id"]].set_result(data)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            _log("cdp_recv_error", error=str(e))

    async def send_cmd(self, method: str, params: dict = None) -> dict:
        if not self.ws:
            raise RuntimeError("Not connected")
        self._msg_id += 1
        msg_id = self._msg_id
        fut = self._loop.create_future()
        self._pending[msg_id] = fut
        req = {"id": msg_id, "method": method}
        if params:
            req["params"] = params
        await self.ws.send(json.dumps(req))
        try:
            res = await asyncio.wait_for(fut, timeout=10.0)
            return res
        finally:
            self._pending.pop(msg_id, None)

    async def evaluate(self, expression: str, await_promise: bool = True) -> dict:
        res = await self.send_cmd("Runtime.evaluate", {
            "expression": expression,
            "awaitPromise": await_promise,
            "returnByValue": True
        })
        return res

    async def disconnect(self):
        if self._recv_task:
            self._recv_task.cancel()
        if self.ws:
            await self.ws.close()

async def get_target_ws_url():
    def fetch():
        req = urllib.request.Request(f"http://127.0.0.1:{CDP_PORT}/json/list")
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception:
            return None

    targets = await asyncio.to_thread(fetch)
    if not targets:
        return None

    # 查找 Antigravity 的 agent webview (通常带 vscode-webview 前缀)
    for target in targets:
        if target.get("url", "").startswith("vscode-webview://"):
            return target.get("webSocketDebuggerUrl")
    return None

async def _process_cdp_task(user_id: str, text: str):
    ws_url = await get_target_ws_url()
    if not ws_url:
        _send(user_id, "❌ 无法连接到 Antigravity。请确保 IDE Agent 面板已打开，且已开启 --remote-debugging-port=9333")
        return

    client = CDPClient(ws_url)
    try:
        await client.connect()

        # 1. 定位输入框并选中
        focus_expr = '''
            (() => {
                const input = document.querySelector('[contenteditable="true"][role="textbox"]:not(.xterm-helper-textarea)');
                if (input) {
                    input.focus();
                    const sel = window.getSelection();
                    const range = document.createRange();
                    range.selectNodeContents(input);
                    sel.removeAllRanges();
                    sel.addRange(range);
                    return 'ready';
                }
                return 'not-found';
            })()
        '''
        res = await client.evaluate(focus_expr)
        if res.get("result", {}).get("result", {}).get("value") != "ready":
            _send(user_id, "❌ 未能在 IDE 面板中找到聊天输入框。")
            return

        # 2. 注入提示词
        await client.send_cmd("Input.insertText", {"text": text})
        await asyncio.sleep(0.1)

        # 3. 模拟回车提交
        await client.send_cmd("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})
        await client.send_cmd("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})

        _send(user_id, "🚀 提示词已投递到 Antigravity，正在生成...")

        # 4. 等待 Agent 处理完成（监测 stop 按钮或加载动画消失）
        start_time = time.time()
        agent_busy = True

        # 等待开始加载
        await asyncio.sleep(2.0)

        while time.time() - start_time < CDP_TIMEOUT:
            check_busy_expr = '''
                (() => {
                    const stopBtn = document.querySelector('button[aria-label*="stop"], button[aria-label*="Stop"], [class*="stop"]');
                    const progress = document.querySelector('[class*="progress_activity"], [class*="animate-spin"]');
                    return !!(stopBtn || progress);
                })()
            '''
            res = await client.evaluate(check_busy_expr)
            is_busy = res.get("result", {}).get("result", {}).get("value")

            if not is_busy:
                agent_busy = False
                break

            await asyncio.sleep(1.5)

        if agent_busy:
            _send(user_id, "⚠️ 生成超时，正在抓取当前已有的回复...")

        # 5. 抓取最新的回复
        extract_expr = '''
            (() => {
                const msgs = document.querySelectorAll('.rendered-markdown:not([data-message-author-role="user"]):not([data-message-role="user"]), [data-message-author-role="assistant"], [data-message-role="assistant"]');
                if (msgs.length > 0) {
                    return msgs[msgs.length - 1].innerText;
                }
                return '';
            })()
        '''
        res = await client.evaluate(extract_expr)
        reply_text = res.get("result", {}).get("result", {}).get("value", "")

        # 处理长文本
        if reply_text:
            if len(reply_text) > MAX_CHUNK:
                reply_text = reply_text[:MAX_CHUNK] + "\n\n...(内容过长，已截断)"
            _send(user_id, f"💡 Antigravity：\n\n{reply_text}")
        else:
            _send(user_id, "⚠️ 未能抓取到回复文本，可能 IDE 内已报错，请直接查看 IDE。")

    except Exception as e:
        _log("cdp_task_error", error=str(e))
        _send(user_id, f"❌ CDP 交互失败: {str(e)}")
    finally:
        await client.disconnect()

def handle_incoming(payload: dict):
    from_user = payload.get("from_user", "")
    text = payload.get("text", "").strip()
    command = payload.get("command", "")

    if not from_user or not text:
        return

    if ALLOWED_USERS and from_user not in ALLOWED_USERS:
        return

    if command:
        return

    # 在新线程中运行 asyncio loop
    def _run_async():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_process_cdp_task(from_user, text))
        loop.close()

    threading.Thread(target=_run_async, daemon=True).start()

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

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

        threading.Thread(target=handle_incoming, args=(payload,), daemon=True).start()

    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    _log("startup",
         listen=f"http://{HOST}:{PORT}/webhook",
         bridge_base_url=BRIDGE_BASE_URL,
         cdp_port=CDP_PORT)

    try:
        ThreadingHTTPServer((HOST, PORT), WebhookHandler).serve_forever()
    finally:
        pass
