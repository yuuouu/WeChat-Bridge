from __future__ import annotations

"""HTTP server 与路由分发。"""

import hmac
import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

from webapp import api_handlers
from webapp.auth import check_web_session
from webapp.context import WebAppContext
from webapp.pages import render_auth_page, render_logged_in, render_qr_page

logger = logging.getLogger(__name__)
GET_API_ROUTES = {
    "/api/accounts": api_handlers.handle_accounts,
    "/api/accounts/aliases": api_handlers.handle_account_aliases,
    "/api/accounts/qr_status": api_handlers.handle_account_qr_status,
    "/api/web_check": api_handlers.handle_web_check,
    "/api/status": api_handlers.handle_status,
    "/api/contacts": api_handlers.handle_contacts,
    "/api/messages": api_handlers.handle_messages,
    "/api/delivery": api_handlers.handle_delivery,
    "/api/weather/query": api_handlers.handle_weather_query_get,
    "/api/ai_config": api_handlers.handle_get_ai_config,
    "/api/qr_status": api_handlers.handle_qr_status,
    "/api/send": api_handlers.handle_send_get,
    "/api/push": api_handlers.handle_push_get,
    "/api/events": api_handlers.handle_events,
}

POST_API_ROUTES = {
    "/api/accounts/default": api_handlers.handle_account_default,
    "/api/accounts/logout": api_handlers.handle_account_logout,
    "/api/accounts/qr": api_handlers.handle_account_qr,
    "/api/accounts/remark": api_handlers.handle_account_remark,
    "/api/ai_analyze": api_handlers.handle_ai_analyze,
    "/api/web_auth": api_handlers.handle_web_auth,
    "/api/send": api_handlers.handle_send_post,
    "/api/commands/run": api_handlers.handle_run_command,
    "/api/weather/query": api_handlers.handle_weather_query_post,
    "/api/typing": api_handlers.handle_typing,
    "/api/ai_config": api_handlers.handle_post_ai_config,
    "/api/ag_inbox": api_handlers.handle_ag_inbox,
    "/api/logout": api_handlers.handle_logout,
    "/api/push": api_handlers.handle_push_post,
    "/api/send_image": api_handlers.handle_send_image,
    "/api/send_video": api_handlers.handle_send_video,
    "/api/send_voice": api_handlers.handle_send_voice,
    "/api/send_file": api_handlers.handle_send_file,
    "/api/send_reference": api_handlers.handle_send_reference,
    "/api/register_commands": api_handlers.handle_register_commands,
    "/api/unregister_commands": api_handlers.handle_unregister_commands,
}


class BridgeHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器。"""

    def log_message(self, format, *args):
        logger.info(format, *args)

    def _get_context(self) -> WebAppContext:
        return self.server.app_context  # type: ignore[attr-defined]

    def _check_api_token(self) -> bool:
        """检查 API Token 鉴权，未配置 TOKEN 时直接放行。"""
        api_token = self._get_context().api_token
        if not api_token:
            return True
        if check_web_session(self, api_token, self._get_context().session_secret):
            return True

        auth = self.headers.get("Authorization", "")
        if hmac.compare_digest(auth, f"Bearer {api_token}") or hmac.compare_digest(auth, api_token):
            return True

        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if hmac.compare_digest(params.get("token", [""])[0], api_token):
            return True

        self._json_response({"ok": False, "error": "Unauthorized: invalid or missing API token"}, 401)
        return False

    def _json_response(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _html_response(self, html: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _do_GET_internal(self):
        ctx = self._get_context()
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/":
            if "refresh_qr" in params:
                ctx.qr_cache.data = None
                ctx.qr_cache.updated_at = 0.0
            if not check_web_session(self, ctx.api_token, ctx.session_secret):
                self._html_response(render_auth_page())
            elif ctx.account_manager is not None:
                if ctx.has_accounts():
                    self._html_response(render_logged_in())
                else:
                    self._html_response(render_qr_page(ctx))
            elif ctx.client and ctx.client.logged_in:
                self._html_response(render_logged_in())
            else:
                self._html_response(render_qr_page(ctx))
            return

        if path.startswith("/media/"):
            api_handlers.handle_media(self, ctx, path)
            return

        route_handler = GET_API_ROUTES.get(path)
        if route_handler:
            route_handler(self, ctx, params)
            return

        self._json_response({"error": "not found"}, 404)

    def do_GET(self):
        try:
            self._do_GET_internal()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
            logger.warning("客户端提前断开 GET 连接: %s", exc)
        except Exception as exc:
            logger.error("do_GET error: %s", exc, exc_info=True)
            try:
                self._json_response({"ok": False, "error": "Internal server error"}, 500)
            except Exception:
                pass

    def _do_POST_internal(self):
        ctx = self._get_context()
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        if path == "/api/webhook" or path.startswith("/api/webhook/"):
            api_handlers.handle_webhook(self, ctx, path, params, body)
            return

        route_handler = POST_API_ROUTES.get(path)
        if route_handler:
            route_handler(self, ctx, params, body)
            return

        self._json_response({"error": "not found"}, 404)

    def do_POST(self):
        try:
            self._do_POST_internal()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
            logger.warning("客户端提前断开 POST 连接: %s", exc)
        except Exception as exc:
            logger.error("do_POST error: %s", exc, exc_info=True)
            try:
                self._json_response({"ok": False, "error": "Internal server error"}, 500)
            except Exception:
                pass

    def do_OPTIONS(self):
        """CORS preflight。"""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """多线程 HTTP 服务器，防止长轮询阻塞其他请求。"""

    daemon_threads = True


def run_server(app_context: WebAppContext, host: str = "0.0.0.0", port: int = 5200):
    """启动 HTTP 服务器。"""
    server = ThreadingHTTPServer((host, port), BridgeHandler)
    server.app_context = app_context  # type: ignore[attr-defined]
    display_host = "localhost" if host == "0.0.0.0" else host
    logger.info("HTTP 服务监听: %s:%d (绑定: %s)", display_host, port, host)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
