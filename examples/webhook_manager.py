#!/usr/bin/env python3
"""
Webhook Manager — 统一 Webhook 入口，自动发现并加载 examples/ 中的插件。

用法：
    python3 examples/webhook_manager.py

插件开发（三步）：
1. 在 examples/ 中创建 .py 文件
2. 定义继承 BasePlugin 的类，实现 commands / get_command_specs / handle
3. 在模块末尾声明 PLUGIN_CLASS = YourPlugin

示例见 bridge_code_agent.py 中的 CodeAgentPlugin。

环境变量：
    BRIDGE_BASE_URL        Bridge 地址（默认 http://127.0.0.1:5200）
    BRIDGE_API_TOKEN       Bridge API Token
    WEBHOOK_LISTEN_HOST    监听地址（默认 0.0.0.0）
    WEBHOOK_LISTEN_PORT    监听端口（默认 18082）
    ALLOWED_USERS          白名单 user_id，逗号分隔（空=不限制）
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = os.environ.get("WEBHOOK_LISTEN_HOST", "0.0.0.0")
PORT = int(os.environ.get("WEBHOOK_LISTEN_PORT", "18082"))
BRIDGE_BASE_URL = os.environ.get("BRIDGE_BASE_URL", "http://127.0.0.1:5200").rstrip("/")
BRIDGE_API_TOKEN = os.environ.get("BRIDGE_API_TOKEN", "").strip()
ALLOWED_USERS: set[str] = {u.strip() for u in os.environ.get("ALLOWED_USERS", "").split(",") if u.strip()}

_EXAMPLES_DIR = Path(__file__).resolve().parent


# ── Plugin 基类 ───────────────────────────────────────────────────────────────


class BasePlugin:
    """所有插件的基类。

    插件文件只需在末尾声明 PLUGIN_CLASS = YourPlugin，
    WebhookManager 启动时会自动发现并实例化。
    """

    name: str = "unnamed"

    @property
    def commands(self) -> list[str]:
        """本插件处理的命令列表，如 ["/code", "/switch", "/exit"]。

        仅需列出通过 Bridge 命令通道（以 / 开头）进入的命令。
        普通消息（非命令）通过 has_session() 路由到对应插件。
        """
        return []

    def get_command_specs(self) -> list[dict]:
        """返回注册到 Bridge /help 的命令描述列表。

        格式：[{"command": "/code", "description": "说明文字"}]
        """
        return []

    def handle(self, payload: dict) -> None:
        """处理一条 webhook payload。在后台线程中调用，可以阻塞。

        payload 字段（由 WeChat Bridge 填充）：
            from_user   str  发送者 user_id
            from_name   str  发送者昵称
            text        str  消息原文
            command     str  命令名，如 "/code"（非命令消息为空）
            args        str  命令参数部分
            is_command  bool 是否为命令消息
        """
        pass

    def has_session(self, user_id: str) -> bool:
        """是否持有该用户的活跃会话。

        all_messages 模式下普通消息（非命令）会投递给此方法返回 True 的插件。
        没有会话概念的插件可保持默认 False。
        """
        return False

    def on_start(self) -> None:
        """Manager 启动时调用，可做初始化。"""
        pass

    def on_stop(self) -> None:
        """Manager 关闭时调用，可做清理。"""
        pass


# ── Manager ───────────────────────────────────────────────────────────────────


class WebhookManager:
    def __init__(self) -> None:
        self.plugins: list[BasePlugin] = []
        self._command_map: dict[str, BasePlugin] = {}

    # ── 插件加载 ──

    def load_plugin(self, plugin: BasePlugin) -> None:
        """注册一个插件实例。"""
        self.plugins.append(plugin)
        for cmd in plugin.commands:
            if cmd in self._command_map:
                _log("command_conflict", cmd=cmd, existing=self._command_map[cmd].name, new=plugin.name)
            self._command_map[cmd] = plugin
        _log("plugin_loaded", name=plugin.name, commands=plugin.commands)

    def auto_discover(self) -> None:
        """扫描 examples/ 目录，加载所有声明了 PLUGIN_CLASS 的脚本。

        使用 AST 预检（不执行文件），只 import 确认包含 PLUGIN_CLASS 的文件，
        避免加载带有副作用的非插件脚本。
        """
        self_name = Path(__file__).name
        for path in sorted(_EXAMPLES_DIR.glob("*.py")):
            if path.name == self_name or path.name.startswith("_"):
                continue
            if not _has_plugin_class_decl(path):
                continue
            try:
                spec = importlib.util.spec_from_file_location(path.stem, str(path))
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                # 必须先注册进 sys.modules，否则 @dataclass 等依赖模块查找的装饰器会报错
                import sys as _sys

                _sys.modules[path.stem] = module
                try:
                    spec.loader.exec_module(module)  # type: ignore[union-attr]
                except Exception:
                    _sys.modules.pop(path.stem, None)
                    raise
                plugin_cls = getattr(module, "PLUGIN_CLASS", None)
                if plugin_cls is None or not isinstance(plugin_cls, type):
                    _log("plugin_skip", file=path.name, reason="PLUGIN_CLASS 不是 class")
                    continue
                self.load_plugin(plugin_cls())
            except Exception as exc:
                _log("plugin_load_error", file=path.name, error=str(exc))

    # ── 路由 ──

    def route(self, payload: dict) -> None:
        """将 payload 投递给对应插件（在后台线程中执行）。"""
        if ALLOWED_USERS:
            from_user = payload.get("from_user", "")
            if from_user and from_user not in ALLOWED_USERS:
                return

        command = payload.get("command", "")
        from_user = payload.get("from_user", "")

        if command:
            plugin = self._command_map.get(command)
            if plugin:
                threading.Thread(target=_safe_handle, args=(plugin, payload), daemon=True).start()
            return

        # 普通消息：路由到持有该用户会话的插件
        for plugin in self.plugins:
            if plugin.has_session(from_user):
                threading.Thread(target=_safe_handle, args=(plugin, payload), daemon=True).start()
                return

    # ── Bridge 命令注册 ──

    def _register_all_commands(self) -> None:
        specs: list[dict] = []
        for plugin in self.plugins:
            specs.extend(plugin.get_command_specs())
        if not specs:
            return
        ok, result = _api_post("/api/register_commands", {"commands": specs})
        _log("register_commands", ok=ok, count=len(specs), result=result[:80])

    def _unregister_all_commands(self) -> None:
        ok, result = _api_post("/api/unregister_commands", {"commands": []})
        _log("unregister_commands", ok=ok, result=result[:80])

    # ── 主循环 ──

    def run(self) -> None:
        """启动 HTTP server，阻塞直到 KeyboardInterrupt。"""
        for plugin in self.plugins:
            if callable(getattr(plugin, "on_start", None)):
                plugin.on_start()

        self._register_all_commands()

        mgr = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length).decode("utf-8", "replace")
                try:
                    payload = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b'{"ok":false,"error":"invalid json"}')
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
                mgr.route(payload)

            def log_message(self, fmt, *args):  # suppress access log
                pass

        _log(
            "startup",
            listen=f"http://{HOST}:{PORT}/webhook",
            bridge_base_url=BRIDGE_BASE_URL,
            plugins=[p.name for p in self.plugins],
            commands=list(self._command_map.keys()),
        )

        try:
            ThreadingHTTPServer((HOST, PORT), _Handler).serve_forever()
        finally:
            self._unregister_all_commands()
            for plugin in self.plugins:
                if callable(getattr(plugin, "on_stop", None)):
                    plugin.on_stop()


# ── 内部工具 ─────────────────────────────────────────────────────────────────


def _has_plugin_class_decl(path: Path) -> bool:
    """用 AST 检查文件是否在模块级声明了 PLUGIN_CLASS，不执行文件代码。"""
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


def _safe_handle(plugin: BasePlugin, payload: dict) -> None:
    try:
        plugin.handle(payload)
    except Exception as exc:
        _log("plugin_handle_error", plugin=plugin.name, error=str(exc))


def _log(event: str, **kwargs) -> None:
    print(json.dumps({"event": event, **kwargs}, ensure_ascii=False), flush=True)


def _api_post(path: str, body: dict) -> tuple[bool, str]:
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
            return True, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return False, exc.read().decode("utf-8", "replace")
    except Exception as exc:
        return False, str(exc)


# ── 入口 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    manager = WebhookManager()
    manager.auto_discover()
    if not manager.plugins:
        _log("warning", msg="没有发现任何插件。请在 examples/ 中添加定义了 PLUGIN_CLASS 的脚本。")
    manager.run()
