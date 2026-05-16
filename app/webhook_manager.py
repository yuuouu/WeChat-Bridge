#!/usr/bin/env python3
"""
Webhook Manager — 统一 Webhook 入口，支持上下文感知的指令路由。
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = os.environ.get("WEBHOOK_LISTEN_HOST", "0.0.0.0")
PORT = int(os.environ.get("WEBHOOK_LISTEN_PORT", "18082"))
BRIDGE_BASE_URL = os.environ.get("BRIDGE_BASE_URL", "http://127.0.0.1:5200").rstrip("/")
BRIDGE_API_TOKEN = os.environ.get("BRIDGE_API_TOKEN", "").strip()
ALLOWED_USERS: set[str] = {u.strip() for u in os.environ.get("ALLOWED_USERS", "").split(",") if u.strip()}

_EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def start_manager_thread() -> threading.Thread:
    """在后台线程启动 Webhook Manager"""

    def _run():
        m = WebhookManager()
        m.auto_discover()
        if not m.plugins:
            _log("warning", msg="没有发现任何插件。请在 examples/ 中添加定义了 PLUGIN_CLASS 的脚本。")
        m.run()

    t = threading.Thread(target=_run, daemon=True, name="WebhookManager")
    t.start()
    return t


class WebhookManager:
    def __init__(self) -> None:
        self.plugins = []
        self._command_map = {}

    def load_plugin(self, plugin) -> None:
        self.plugins.append(plugin)
        cmds = []
        if hasattr(plugin, "commands"):
            cmds = plugin.commands if isinstance(plugin.commands, list) else []
        for cmd in cmds:
            if cmd not in self._command_map:
                self._command_map[cmd] = []
            self._command_map[cmd].append(plugin)
        name = getattr(plugin, "name", "unknown")
        _log("plugin_loaded", name=name)

    def auto_discover(self) -> None:
        for path in sorted(_EXAMPLES_DIR.glob("*.py")):
            if path.name == Path(__file__).name or path.name.startswith("_"):
                continue
            if not _has_plugin_class_decl(path):
                continue
            try:
                spec = importlib.util.spec_from_file_location(path.stem, str(path))
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                import sys as _sys

                _sys.modules[path.stem] = module
                spec.loader.exec_module(module)
                plugin_cls = getattr(module, "PLUGIN_CLASS", None)
                if isinstance(plugin_cls, type):
                    self.load_plugin(plugin_cls())
            except Exception as exc:
                _log("plugin_load_error", file=path.name, error=str(exc))

    def route(self, payload: dict) -> None:
        _log("routing_payload", command=payload.get("command"), user=payload.get("from_user"))
        if ALLOWED_USERS and payload.get("from_user") not in ALLOWED_USERS:
            _log("user_ignored", user=payload.get("from_user"))
            return

        from_user = payload.get("from_user", "")
        command = payload.get("command", "")

        # 1. 广播给监控型插件 (无 commands 的插件，如 forwarder)
        for p in self.plugins:
            if not getattr(p, "commands", []):
                _log("broadcasting", plugin=getattr(p, "name", "uk"))
                threading.Thread(target=_safe_handle, args=(p, payload), daemon=True).start()

        # 2. 路由指令
        if command:
            target_plugins = self._command_map.get(command, [])
            _log("found_targets", cmd=command, count=len(target_plugins))
            if not target_plugins:
                return
            for p in target_plugins:
                if hasattr(p, "has_session") and p.has_session(from_user):
                    _log("session_match", plugin=p.name, cmd=command)
                    threading.Thread(target=_safe_handle, args=(p, payload), daemon=True).start()
                    return
            _log("default_route", plugin=target_plugins[0].name, cmd=command)
            threading.Thread(target=_safe_handle, args=(target_plugins[0], payload), daemon=True).start()
            return

        # 3. 路由普通文本 (给会话持有者)
        for p in self.plugins:
            if hasattr(p, "has_session") and p.has_session(from_user):
                _log("session_routing", plugin=p.name)
                threading.Thread(target=_safe_handle, args=(p, payload), daemon=True).start()
                return

    def run(self) -> None:
        for p in self.plugins:
            if hasattr(p, "on_start") and callable(p.on_start):
                p.on_start()

        # 延迟注册，等待主进程 Web 服务就绪
        def _register_with_retry():
            import time

            for i in range(10):
                time.sleep(2)
                _log("register_attempt", attempt=i + 1)
                ok, res = self._register_all_commands()
                if ok:
                    break

        threading.Thread(target=_register_with_retry, daemon=True).start()

        mgr = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                content = self.rfile.read(length)
                try:
                    payload = json.loads(content) if content else {}
                    _log("incoming_request", payload=payload)
                except json.JSONDecodeError:
                    _log("invalid_json", content=content.decode("utf-8", "ignore"))
                    self.send_response(400)
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
                mgr.route(payload)

            def log_message(self, *args):
                pass

        _log("startup", listen=f"http://{HOST}:{PORT}/webhook")
        try:
            ThreadingHTTPServer((HOST, PORT), _Handler).serve_forever()
        finally:
            self._unregister_all_commands()
            for p in self.plugins:
                if hasattr(p, "on_stop") and callable(p.on_stop):
                    p.on_stop()

    def _register_all_commands(self) -> tuple[bool, str]:
        specs = []
        seen = set()
        for p in self.plugins:
            if hasattr(p, "get_command_specs") and callable(p.get_command_specs):
                for s in p.get_command_specs():
                    if s["command"] not in seen:
                        specs.append(s)
                        seen.add(s["command"])
        if not specs:
            return True, "no_commands"
        ok, res = _api_post("/api/register_commands", {"commands": specs})
        _log("register_result", ok=ok, res=res)
        return ok, res

    def _unregister_all_commands(self) -> None:
        _api_post("/api/unregister_commands", {"commands": []})


def _has_plugin_class_decl(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "PLUGIN_CLASS":
                        return True
    except Exception:
        pass
    return False


def _safe_handle(p, payload):
    try:
        if hasattr(p, "handle") and callable(p.handle):
            p.handle(payload)
    except Exception as exc:
        _log("plugin_error", plugin=getattr(p, "name", "unknown"), error=str(exc))


def _log(event: str, **kwargs):
    print(json.dumps({"event": event, **kwargs}, ensure_ascii=False), flush=True)


def _api_post(path, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{BRIDGE_BASE_URL}{path}",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    if BRIDGE_API_TOKEN:
        req.add_header("Authorization", f"Bearer {BRIDGE_API_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return True, resp.read().decode("utf-8")
    except Exception as exc:
        return False, str(exc)


if __name__ == "__main__":
    m = WebhookManager()
    m.auto_discover()
    m.run()
