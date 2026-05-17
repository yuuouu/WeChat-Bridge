from __future__ import annotations

"""
轻量事件总线

提供 publish/subscribe 模式，解耦消息处理各阶段：
- message_received: 收到入站消息
- command_received: 收到 / 开头的指令
- ai_reply_ready: AI 回复生成完成
- message_sent: 消息发送完成
- webhook_forward: 消息需要转发到外部 Webhook

所有事件回调在独立线程中异步执行，不阻塞主处理流程。
"""

import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 预定义事件名称常量
EVENT_MESSAGE_RECEIVED = "message_received"
EVENT_COMMAND_RECEIVED = "command_received"
EVENT_AI_REPLY_READY = "ai_reply_ready"
EVENT_MESSAGE_SENT = "message_sent"
EVENT_WEBHOOK_FORWARD = "webhook_forward"
EVENT_LOGIN = "login"
EVENT_LOGOUT = "logout"


@dataclass
class Event:
    """事件载体，携带事件名称和上下文数据。"""

    name: str
    data: dict = field(default_factory=dict)


class EventBus:
    """
    线程安全的 pub/sub 事件总线。

    使用方式：
        bus = EventBus()
        bus.subscribe("message_received", my_handler)
        bus.publish(Event("message_received", {"text": "hello"}))
    """

    def __init__(self):
        self._subscribers: dict[str, list[tuple[str, Callable]]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, event_name: str, callback: Callable[[Event], None], *, subscriber_id: str = "") -> None:
        """
        订阅事件。

        Args:
            event_name: 事件名称，使用 EVENT_* 常量
            callback: 回调函数，接收 Event 参数
            subscriber_id: 订阅者标识，用于日志和取消订阅
        """
        sid = subscriber_id or f"{callback.__module__}.{callback.__qualname__}"
        with self._lock:
            self._subscribers[event_name].append((sid, callback))
        logger.debug("事件订阅: %s → %s", event_name, sid)

    def unsubscribe(self, event_name: str, subscriber_id: str) -> bool:
        """按 subscriber_id 取消订阅，返回是否成功移除。"""
        with self._lock:
            subs = self._subscribers.get(event_name, [])
            before = len(subs)
            self._subscribers[event_name] = [(sid, cb) for sid, cb in subs if sid != subscriber_id]
            return len(self._subscribers[event_name]) < before

    def publish(self, event: Event, *, sync: bool = False) -> None:
        """
        发布事件。

        默认异步执行所有订阅者回调（每个回调独立线程）。
        sync=True 时同步执行（仅用于测试或必须顺序执行的场景）。
        """
        with self._lock:
            subscribers = list(self._subscribers.get(event.name, []))

        if not subscribers:
            return

        logger.debug("事件发布: %s → %d 个订阅者", event.name, len(subscribers))

        for sid, callback in subscribers:
            if sync:
                self._safe_call(sid, callback, event)
            else:
                threading.Thread(
                    target=self._safe_call,
                    args=(sid, callback, event),
                    daemon=True,
                    name=f"event-{event.name}-{sid}",
                ).start()

    def _safe_call(self, subscriber_id: str, callback: Callable, event: Event) -> None:
        """安全执行回调，捕获异常避免影响其他订阅者。"""
        try:
            callback(event)
        except Exception as exc:
            logger.error("事件处理异常 [%s → %s]: %s", event.name, subscriber_id, exc, exc_info=True)

    def subscriber_count(self, event_name: str) -> int:
        """返回指定事件的订阅者数量。"""
        with self._lock:
            return len(self._subscribers.get(event_name, []))

    def clear(self) -> None:
        """清除所有订阅（用于测试）。"""
        with self._lock:
            self._subscribers.clear()
