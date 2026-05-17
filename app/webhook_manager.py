#!/usr/bin/env python3
"""
Webhook Manager — 统一 Webhook 入口，支持上下文感知的指令路由。
"""

from __future__ import annotations

import ast
import importlib.util
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from plugin_base import Plugin, PluginRegistry

logger = logging.getLogger(__name__)

_project_root = Path(__file__).resolve().parent.parent
_EXAMPLES_DIR = _project_root / "examples"
if not _EXAMPLES_DIR.exists():
    # 兼容 Docker 挂载情况 (./app:/app, ./examples:/app/examples)
    _EXAMPLES_DIR = Path(__file__).resolve().parent / "examples"


def discover_and_register_plugins(registry: PluginRegistry) -> None:
    """自动发现并注册 examples 目录下的插件。"""
    for path in sorted(_EXAMPLES_DIR.glob("*.py")):
        if path.name.startswith("_"):
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
                plugin = plugin_cls()
                if not isinstance(plugin, Plugin):
                    plugin = LegacyPluginWrapper(plugin)
                registry.register(plugin)
        except Exception as exc:
            logger.error("加载插件失败 [%s]: %s", path.name, exc)


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


class LegacyPluginWrapper(Plugin):
    """用于兼容旧版 webhook_manager 插件的适配器"""

    def __init__(self, legacy_plugin):
        super().__init__()
        self.legacy = legacy_plugin
        self.name = getattr(legacy_plugin, "name", "legacy-plugin")
        # 从旧插件复制命令列表，确保 PluginRegistry 能正确注册
        legacy_cmds = getattr(legacy_plugin, "commands", [])
        self.commands = list(legacy_cmds) if legacy_cmds else []

    def on_start(self):
        if hasattr(self.legacy, "on_start"):
            self.legacy.on_start()
        # 注入 send_func 给旧插件
        if self._send_func and not getattr(self.legacy, "_send_func", None):
            self.legacy._send_func = self._send_func

    def on_stop(self):
        if hasattr(self.legacy, "on_stop"):
            self.legacy.on_stop()

    def handle(self, payload: dict) -> None:
        """委托给旧插件的 handle 方法。"""
        if hasattr(self.legacy, "handle"):
            self.legacy.handle(payload)

    def has_session(self, user_id: str) -> bool:
        if hasattr(self.legacy, "has_session"):
            return self.legacy.has_session(user_id)
        return False

    def get_command_specs(self) -> list[dict]:
        if hasattr(self.legacy, "get_command_specs"):
            return self.legacy.get_command_specs()
        return super().get_command_specs()

    def on_message(self, event):
        """入站消息回调：仅当旧插件持有该用户会话时，才路由普通文本。"""
        from_user = event.data.get("from_user", "")
        text = event.data.get("text", "")
        # 命令由 _handle_command → plugin.handle() 路径处理，这里只处理非命令文本
        if text.startswith("/"):
            return
        if not self.has_session(from_user):
            return
        payload = {
            "from_user": from_user,
            "from_name": event.data.get("from_name"),
            "text": text,
            "command": "",
            "args": "",
            "is_command": False,
            "msg_id": event.data.get("msg", {}).get("msg_id"),
        }
        self.handle(payload)
