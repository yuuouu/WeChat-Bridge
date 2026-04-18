"""WeChat Bridge Web 兼容入口。"""

import os

from bridge import WeChatBridge
from ilink import ILinkClient
from webapp.context import WebAppContext
from webapp.server import run_server as _run_server

_app_context: WebAppContext | None = None


def set_context(client: ILinkClient, bridge: WeChatBridge, api_token: str | None = None):
    """由 main.py 注入 Web 层运行上下文。"""
    global _app_context
    token = api_token if api_token is not None else os.environ.get("API_TOKEN", "")
    _app_context = WebAppContext(client=client, bridge=bridge, api_token=token)


def _require_context() -> WebAppContext:
    if _app_context is None:
        raise RuntimeError("web context not initialized")
    return _app_context


def run_server(host: str = "0.0.0.0", port: int = 5200):
    """兼容旧入口，内部委托给 webapp.server。"""
    _run_server(_require_context(), host=host, port=port)
