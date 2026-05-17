from __future__ import annotations

"""
插件基类与注册表

定义标准插件接口，社区贡献者只需继承 Plugin 基类并实现所需方法：

    class MyPlugin(Plugin):
        name = "my-plugin"
        description = "示例插件"

        def on_message(self, event):
            # 处理入站消息
            pass

        def get_command_specs(self):
            return [{"command": "/hello", "description": "打招呼"}]

插件会自动注册到事件总线，无需直接操作 EventBus API。
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from event_bus import Event, EventBus

logger = logging.getLogger(__name__)


class Plugin:
    """
    标准插件基类。

    子类可选择覆盖以下方法来响应事件：
    - on_message(event): 所有入站消息
    - on_command(event): 收到 / 开头的指令（在内置指令处理之后）
    - on_ai_reply(event): AI 回复生成完成
    - on_message_sent(event): 消息发送完成
    - on_start(): 插件启动时的初始化逻辑
    - on_stop(): 插件关闭时的清理逻辑

    指令插件还需实现：
    - get_command_specs(): 返回 [{"command": "/xxx", "description": "..."}]
    - has_session(user_id): 返回是否对该用户持有会话上下文
    """

    name: str = "unnamed"
    description: str = ""
    commands: list[str] = []  # 响应的命令列表，如 ["/rj", "/okx"]
    _send_func = None  # 由 PluginRegistry 注入的 bridge.send 引用

    def on_message(self, event: Event) -> None:
        """所有入站消息回调。event.data 包含: from_user, from_name, text, msg, media_paths"""
        pass

    def on_command(self, event: Event) -> None:
        """未匹配内置命令的指令回调。event.data 包含: from_user, from_name, text, command, args"""
        pass

    def on_ai_reply(self, event: Event) -> None:
        """AI 回复生成完成。event.data 包含: user_id, reply_text, provider, model, tokens"""
        pass

    def on_message_sent(self, event: Event) -> None:
        """消息发送完成。event.data 包含: user_id, text, source, result"""
        pass

    def on_start(self) -> None:
        """插件启动时的初始化逻辑。"""
        pass

    def on_stop(self) -> None:
        """插件关闭时的清理逻辑。"""
        pass

    def get_command_specs(self) -> list[dict]:
        """返回插件提供的命令规格列表。"""
        return [{"command": cmd, "description": self.description} for cmd in self.commands]

    def has_session(self, user_id: str) -> bool:
        """返回是否对该用户持有会话上下文（用于路由普通文本到正确的插件）。"""
        return False

    def handle(self, payload: dict) -> None:
        """
        兼容旧 WebhookManager 调用约定。
        新插件建议直接实现 on_message / on_command。
        """
        pass

    def send_reply(self, to_user: str, text: str, *, source: str = "plugin") -> dict:
        """回复消息。优先使用注入的 bridge.send，无需绕 HTTP。"""
        if self._send_func:
            return self._send_func(to_user, text, source=source)
        logger.warning("插件 %s 无 send_func，无法发送回复", self.name)
        return {"ok": False, "error": "no send_func"}


class PluginRegistry:
    """
    插件注册表，负责：
    1. 管理插件生命周期（加载、启动、停止）
    2. 将插件方法自动绑定到事件总线
    3. 维护命令路由表
    """

    def __init__(self, event_bus: EventBus, send_func=None):
        self._bus = event_bus
        self._send_func = send_func
        self._plugins: list[Plugin] = []
        self._command_map: dict[str, list[Plugin]] = {}

    @property
    def plugins(self) -> list[Plugin]:
        return list(self._plugins)

    @property
    def command_map(self) -> dict[str, list[Plugin]]:
        return dict(self._command_map)

    def register(self, plugin: Plugin) -> None:
        """注册插件并自动绑定事件。"""
        from event_bus import (
            EVENT_AI_REPLY_READY,
            EVENT_COMMAND_RECEIVED,
            EVENT_MESSAGE_RECEIVED,
            EVENT_MESSAGE_SENT,
        )

        self._plugins.append(plugin)
        plugin._send_func = self._send_func
        sid = f"plugin:{plugin.name}"

        # 自动绑定实现了的事件方法
        if _is_overridden(plugin, "on_message"):
            self._bus.subscribe(EVENT_MESSAGE_RECEIVED, plugin.on_message, subscriber_id=sid)

        if _is_overridden(plugin, "on_command"):
            self._bus.subscribe(EVENT_COMMAND_RECEIVED, plugin.on_command, subscriber_id=sid)

        if _is_overridden(plugin, "on_ai_reply"):
            self._bus.subscribe(EVENT_AI_REPLY_READY, plugin.on_ai_reply, subscriber_id=sid)

        if _is_overridden(plugin, "on_message_sent"):
            self._bus.subscribe(EVENT_MESSAGE_SENT, plugin.on_message_sent, subscriber_id=sid)

        # 注册命令路由
        for cmd in plugin.commands:
            if cmd not in self._command_map:
                self._command_map[cmd] = []
            self._command_map[cmd].append(plugin)

        logger.info("插件已注册: %s (%s)", plugin.name, plugin.description or "无描述")

    def unregister(self, plugin_name: str) -> bool:
        """按名称注销插件。"""
        target = None
        for p in self._plugins:
            if p.name == plugin_name:
                target = p
                break
        if not target:
            return False

        sid = f"plugin:{plugin_name}"
        from event_bus import (
            EVENT_AI_REPLY_READY,
            EVENT_COMMAND_RECEIVED,
            EVENT_MESSAGE_RECEIVED,
            EVENT_MESSAGE_SENT,
        )

        for event_name in (EVENT_MESSAGE_RECEIVED, EVENT_COMMAND_RECEIVED, EVENT_AI_REPLY_READY, EVENT_MESSAGE_SENT):
            self._bus.unsubscribe(event_name, sid)

        self._plugins.remove(target)
        for cmd, plugins in list(self._command_map.items()):
            self._command_map[cmd] = [p for p in plugins if p is not target]
            if not self._command_map[cmd]:
                del self._command_map[cmd]

        try:
            target.on_stop()
        except Exception as exc:
            logger.error("插件停止异常 [%s]: %s", plugin_name, exc)

        logger.info("插件已注销: %s", plugin_name)
        return True

    def start_all(self) -> None:
        """启动所有已注册插件。"""
        for plugin in self._plugins:
            try:
                plugin.on_start()
            except Exception as exc:
                logger.error("插件启动失败 [%s]: %s", plugin.name, exc)

    def stop_all(self) -> None:
        """停止所有已注册插件。"""
        for plugin in self._plugins:
            try:
                plugin.on_stop()
            except Exception as exc:
                logger.error("插件停止异常 [%s]: %s", plugin.name, exc)

    def get_all_command_specs(self) -> list[dict]:
        """汇总所有插件的命令规格，用于 /help 显示。"""
        specs = []
        seen = set()
        for plugin in self._plugins:
            for spec in plugin.get_command_specs():
                cmd = spec.get("command", "")
                if cmd and cmd not in seen:
                    specs.append(spec)
                    seen.add(cmd)
        return specs

    def route_command(self, command: str, from_user: str) -> Plugin | None:
        """根据命令和用户会话状态路由到目标插件。"""
        target_plugins = self._command_map.get(command, [])
        if not target_plugins:
            return None

        # 优先路由到持有该用户会话的插件
        for p in target_plugins:
            if p.has_session(from_user):
                return p

        # 默认路由到第一个注册该命令的插件
        return target_plugins[0]

    def find_session_holder(self, from_user: str) -> Plugin | None:
        """查找持有该用户会话的插件（用于路由非命令文本）。"""
        for plugin in self._plugins:
            if plugin.has_session(from_user):
                return plugin
        return None


def _is_overridden(instance: Plugin, method_name: str) -> bool:
    """检查子类是否覆盖了基类方法。"""
    base_method = getattr(Plugin, method_name, None)
    instance_method = getattr(type(instance), method_name, None)
    return instance_method is not base_method
