from __future__ import annotations

"""API route handlers。"""

import base64
import json
import logging
import mimetypes
import time
from urllib.parse import parse_qs

import config as cfg
import db as msg_db
import media as media_mod
from version import __version__
from webapp.auth import check_web_session, make_session_cookie
from webapp.markdown_utils import apply_markdown_mode
from webapp.request_utils import parse_multipart
from webapp.webhook_parser import parse_webhook_payload

logger = logging.getLogger(__name__)


def _pick_default_contact(
    ctx,
    to: str,
    *,
    request_path: str = "",
    source: str = "",
    title: str = "",
    message_len: int = 0,
) -> str:
    if to:
        return to
    selected = ctx.bridge.get_default_contact()
    if selected:
        ctx.bridge.record_default_recipient_decision(
            selected,
            request_path=request_path,
            source=source,
            title=title,
            message_len=message_len,
        )
    return selected


def _compose_title_text(title: str, text: str) -> str:
    if title and text:
        return f"【{title}】\n{text}"
    if title:
        return title
    return text


def _multicast_send(ctx, to_str: str, text: str, *, source: str = "api", title: str = "") -> dict:
    targets = [item.strip() for item in to_str.split(",") if item.strip()]
    if not targets:
        return {"ok": False, "error": "无有效目标"}

    if len(targets) == 1:
        return ctx.bridge.send(targets[0], text, source=source, title=title)

    results = []
    success = 0
    for index, target in enumerate(targets):
        result = ctx.bridge.send(target, text, source=source, title=title)
        results.append({"to": target, **result})
        if result.get("ok"):
            success += 1
        if index < len(targets) - 1:
            time.sleep(0.5)

    return {
        "ok": success > 0,
        "summary": f"成功 {success}/{len(targets)}",
        "results": results,
    }


def _load_json(handler, body: bytes):
    try:
        return json.loads(body) if body else {}
    except json.JSONDecodeError:
        handler._json_response({"ok": False, "error": "无效 JSON"}, 400)
        return None


def handle_web_check(handler, ctx, params):
    handler._json_response(
        {
            "authed": check_web_session(handler, ctx.api_token, ctx.session_secret),
            "need_auth": bool(ctx.api_token),
        }
    )


def handle_status(handler, ctx, params):
    payload = ctx.bridge.get_runtime_status()
    payload["version"] = __version__
    handler._json_response(payload)


def handle_contacts(handler, ctx, params):
    if not handler._check_api_token():
        return
    contacts = ctx.bridge.get_ordered_contacts()
    handler._json_response(
        {
            "contacts": contacts,
            "context_tokens": {k: v[:20] + "..." for k, v in ctx.bridge.context_tokens.items()},
            "delivery_states": ctx.bridge.get_contact_delivery_summaries(),
        }
    )


def handle_messages(handler, ctx, params):
    if not handler._check_api_token():
        return
    limit = int(params.get("limit", ["200"])[0])
    before_id = params.get("before_id", [None])[0]
    if before_id:
        before_id = int(before_id)
    messages = msg_db.get_messages(limit=limit, before_id=before_id)
    handler._json_response({"messages": messages})


def handle_get_ai_config(handler, ctx, params):
    if not ctx.client.logged_in:
        handler._json_response({"error": "未登录"}, 401)
        return

    ai_config = cfg.load_config()
    key = ai_config.get("api_key", "")
    if len(key) > 12:
        ai_config["api_key"] = key[:4] + "********" + key[-4:]
    elif len(key) > 0:
        ai_config["api_key"] = "********"
    handler._json_response(ai_config)


def handle_qr_status(handler, ctx, params):
    qrcode = params.get("qrcode", [""])[0]
    if not qrcode:
        handler._json_response({"error": "missing qrcode param"}, 400)
        return

    try:
        status_data = ctx.client.poll_qrcode_status(qrcode)
        if status_data.get("status") == "expired":
            cached_qrcode = (ctx.qr_cache.data or {}).get("qrcode")
            if cached_qrcode == qrcode:
                ctx.qr_cache.data = None
                ctx.qr_cache.updated_at = 0.0
        if ctx.client.logged_in and status_data.get("status") == "confirmed":
            ctx.bridge._setup_data_dir()
            ctx.bridge.record_account_event("login_confirmed", reason="qr_confirmed")
            ctx.bridge._load_contacts()
            ctx.bridge.recent_messages.clear()
            ctx.bridge._consecutive_send_count.clear()
            logger.info("登录成功，数据目录已切换到 bot: %s", ctx.client.get_bot_id())
        handler._json_response(
            {
                "status": status_data.get("status"),
                "logged_in": ctx.client.logged_in,
                "message": {
                    "wait": "等待扫码",
                    "scaned": "已扫码，请在微信确认",
                    "scaned_but_redirect": "正在重定向",
                    "expired": "二维码已过期",
                    "confirmed": "登录成功",
                }.get(status_data.get("status", ""), ""),
            }
        )
    except Exception as exc:
        handler._json_response({"error": str(exc)}, 500)


def handle_send_get(handler, ctx, params):
    if not handler._check_api_token():
        return
    if not ctx.client.logged_in:
        handler._json_response({"ok": False, "error": "未登录"}, 401)
        return

    title = params.get("title", [""])[0]
    text = params.get("text", [""])[0] or params.get("content", [""])[0]
    text = _compose_title_text(title, text)
    to = _pick_default_contact(
        ctx,
        params.get("to", [""])[0],
        request_path="/api/send",
        source="api",
        title=title,
        message_len=len(text),
    )

    if not to:
        handler._json_response({"ok": False, "error": "无可用联系人，请指定 to 参数"}, 400)
        return
    if not text:
        handler._json_response({"ok": False, "error": "缺少 text 参数"}, 400)
        return

    text = apply_markdown_mode(
        text,
        params.get("markdown", [""])[0],
        params.get("markdown_mode", [""])[0],
    )
    result = _multicast_send(ctx, to, text, source="api", title=title)
    handler._json_response(result, 200 if result.get("ok") else 400)


def handle_push_get(handler, ctx, params):
    if not handler._check_api_token():
        return
    if not ctx.client.logged_in:
        handler._json_response({"ok": False, "error": "未登录"}, 401)
        return

    title = params.get("title", [""])[0]
    text = params.get("text", [""])[0] or params.get("content", [""])[0]
    final_text = _compose_title_text(title, text)
    to = _pick_default_contact(
        ctx,
        params.get("to", [""])[0],
        request_path="/api/push",
        source="api_push",
        title=title,
        message_len=len(final_text),
    )

    if not to or not final_text:
        handler._json_response({"ok": False, "error": "需要 to 和 text 或 content 参数"}, 400)
        return

    final_text = apply_markdown_mode(
        final_text,
        params.get("markdown", [""])[0],
        params.get("markdown_mode", [""])[0],
    )
    result = _multicast_send(ctx, to, final_text, source="api_push", title=title)
    handler._json_response(result, 200 if result.get("ok") else 400)


def handle_media(handler, ctx, path):
    filename = path[len("/media/") :]
    filepath = media_mod.get_media_path(filename)
    if not filepath:
        handler._json_response({"error": "file not found"}, 404)
        return

    content_type = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
    try:
        with open(filepath, "rb") as fh:
            file_data = fh.read()
        handler.send_response(200)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(file_data)))
        handler.send_header("Cache-Control", "public, max-age=86400")
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.end_headers()
        handler.wfile.write(file_data)
    except Exception as exc:
        logger.error("读取媒体文件失败: %s", exc)
        handler._json_response({"error": "read error"}, 500)


def handle_web_auth(handler, ctx, params, body):
    data = _load_json(handler, body)
    if data is None:
        return

    token = data.get("token", "")
    if token != ctx.api_token:
        handler._json_response({"ok": False, "error": "密码错误"}, 403)
        return

    session_val = make_session_cookie(token, ctx.session_secret)
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header(
        "Set-Cookie",
        f"wb_session={session_val}; Path=/; HttpOnly; SameSite=Strict; Max-Age=604800",
    )
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    handler.wfile.write(json.dumps({"ok": True}).encode("utf-8"))


def handle_send_post(handler, ctx, params, body):
    if not handler._check_api_token():
        return
    if not ctx.client.logged_in:
        handler._json_response({"ok": False, "error": "未登录"}, 401)
        return

    data = _load_json(handler, body)
    if data is None:
        return

    text = data.get("text", "") or data.get("content", "")
    title = data.get("title", "")
    to = _pick_default_contact(
        ctx,
        data.get("to", ""),
        request_path="/api/send",
        source="api",
        title=title,
        message_len=len(text),
    )

    if not to:
        handler._json_response({"ok": False, "error": "无可用联系人，请指定 to 参数"}, 400)
        return
    if not text:
        handler._json_response({"ok": False, "error": "缺少 text 参数"}, 400)
        return

    text = apply_markdown_mode(text, data.get("markdown"), data.get("markdown_mode"))
    result = _multicast_send(ctx, to, text, source="api", title=title)
    handler._json_response(result, 200 if result.get("ok") else 400)


def handle_typing(handler, ctx, params, body):
    if not ctx.client.logged_in:
        handler._json_response({"ok": False, "error": "未登录"}, 401)
        return

    data = _load_json(handler, body)
    if data is None:
        return

    to = data.get("to", "")
    if not to:
        handler._json_response({"ok": False, "error": "缺少 to 参数"}, 400)
        return

    result = ctx.bridge.send_typing(to)
    handler._json_response(result, 200 if result.get("ok") else 400)


def handle_post_ai_config(handler, ctx, params, body):
    if not ctx.client.logged_in:
        handler._json_response({"ok": False, "error": "未登录"}, 401)
        return

    data = _load_json(handler, body)
    if data is None:
        return

    original = cfg.load_config()
    current = original.copy()
    for key in (
        "enabled",
        "provider",
        "model",
        "base_url",
        "system_prompt",
        "max_history",
        "keepalive_remind_minutes",
        "webhook_enabled",
        "webhook_url",
        "webhook_mode",
        "webhook_timeout",
        "telemetry_enabled",
    ):
        if key in data:
            current[key] = data[key]

    if "api_key" in data:
        new_key = data["api_key"].strip()
        if "*" not in new_key:
            current["api_key"] = new_key

    if "webhook_url" in current:
        current["webhook_url"] = current["webhook_url"].strip()
    if current.get("webhook_mode") not in ("unknown_command", "all_messages"):
        current["webhook_mode"] = "unknown_command"
    try:
        current["webhook_timeout"] = max(1, min(30, int(current.get("webhook_timeout", 5))))
    except (TypeError, ValueError):
        current["webhook_timeout"] = 5

    cfg.save_config(current)
    if ctx.bridge.ai_manager:
        ctx.bridge.ai_manager.clear_all_histories()
    handler._json_response({"ok": True})


def handle_ag_inbox(handler, ctx, params, body):
    with ctx.bridge._ag_inbox_lock:
        messages = ctx.bridge.ag_inbox
        ctx.bridge.ag_inbox = []
    handler._json_response({"ok": True, "messages": messages})


def handle_logout(handler, ctx, params, body):
    ctx.bridge.record_account_event("logout", reason="web_logout")
    ctx.client.clear_token()
    ctx.qr_cache.data = None
    ctx.qr_cache.updated_at = 0.0
    handler.send_response(302)
    handler.send_header("Location", "/")
    handler.end_headers()


def handle_push_post(handler, ctx, params, body):
    if not handler._check_api_token():
        return
    if not ctx.client.logged_in:
        handler._json_response({"ok": False, "error": "未登录"}, 401)
        return

    to = ""
    text = ""
    title = ""
    markdown = ""
    markdown_mode = ""
    content_type = handler.headers.get("Content-Type", "")
    if content_type.startswith("application/json"):
        try:
            data = json.loads(body) if body else {}
            to = data.get("to", "")
            text = data.get("text", "") or data.get("content", "")
            title = data.get("title", "")
            markdown = data.get("markdown")
            markdown_mode = data.get("markdown_mode")
        except Exception:
            pass
    else:
        form_data = parse_qs(body.decode("utf-8"))
        to = form_data.get("to", [""])[0]
        text = form_data.get("text", [""])[0] or form_data.get("content", [""])[0]
        title = form_data.get("title", [""])[0]
        markdown = form_data.get("markdown", [""])[0]
        markdown_mode = form_data.get("markdown_mode", [""])[0]

    text = text or params.get("text", [""])[0] or params.get("content", [""])[0]
    title = title or params.get("title", [""])[0]
    final_text = _compose_title_text(title, text)
    to = _pick_default_contact(
        ctx,
        to or params.get("to", [""])[0],
        request_path="/api/push",
        source="api_push",
        title=title,
        message_len=len(final_text),
    )

    if not to or not final_text:
        handler._json_response({"ok": False, "error": "需要 to 和 text 或 content 参数"}, 400)
        return

    final_text = apply_markdown_mode(
        final_text,
        markdown,
        markdown_mode,
        params.get("markdown", [""])[0],
        params.get("markdown_mode", [""])[0],
    )
    result = _multicast_send(ctx, to, final_text, source="api_push", title=title)
    handler._json_response(result, 200 if result.get("ok") else 400)


def handle_webhook(handler, ctx, path, params, body):
    if not handler._check_api_token():
        return
    if not ctx.client.logged_in:
        handler._json_response({"ok": False, "error": "未登录"}, 401)
        return

    schema = ""
    if path.startswith("/api/webhook/"):
        schema = path[len("/api/webhook/") :].strip("/")
    schema = schema or params.get("type", [""])[0]

    try:
        data = json.loads(body) if body else {}
    except Exception:
        handler._json_response({"ok": False, "error": "无效 JSON"}, 400)
        return

    text = parse_webhook_payload(data, schema)
    text = apply_markdown_mode(
        text,
        params.get("markdown", [""])[0],
        params.get("markdown_mode", [""])[0],
    )
    if not text:
        handler._json_response({"ok": False, "error": "无法解析 Webhook 内容"}, 400)
        return

    source = f"webhook:{schema or 'generic'}"
    to = _pick_default_contact(
        ctx,
        params.get("to", [""])[0],
        request_path="/api/webhook",
        source=source,
        message_len=len(text),
    )
    if not to:
        handler._json_response({"ok": False, "error": "无可用联系人"}, 400)
        return

    result = _multicast_send(ctx, to, text, source=source)
    handler._json_response(result, 200 if result.get("ok") else 400)


def handle_send_image(handler, ctx, params, body):
    if not handler._check_api_token():
        return
    if not ctx.client.logged_in:
        handler._json_response({"ok": False, "error": "未登录"}, 401)
        return

    to = ""
    image_data = None
    content_type = handler.headers.get("Content-Type", "")

    if "multipart/form-data" in content_type:
        to, image_data = parse_multipart(body, content_type, logger)
    elif content_type.startswith("application/json"):
        try:
            data = json.loads(body) if body else {}
            to = data.get("to", "")
            img_b64 = data.get("image", "")
            if img_b64:
                image_data = base64.b64decode(img_b64)
        except Exception as exc:
            handler._json_response({"ok": False, "error": f"JSON 解析失败: {exc}"}, 400)
            return
    elif content_type.startswith("application/octet-stream"):
        image_data = body
    else:
        image_data = body

    to = _pick_default_contact(
        ctx,
        to or params.get("to", [""])[0],
        request_path="/api/send_image",
        source="image",
        message_len=len(image_data or b""),
    )
    if not to:
        handler._json_response({"ok": False, "error": "缺少 to 参数且无联系人"}, 400)
        return
    if not image_data or len(image_data) < 100:
        handler._json_response({"ok": False, "error": "缺少图片数据或数据过小"}, 400)
        return
    if len(image_data) > 10 * 1024 * 1024:
        handler._json_response({"ok": False, "error": "图片大小不能超过 10MB"}, 400)
        return

    result = ctx.bridge.send_image(to, image_data)
    handler._json_response(result, 200 if result.get("ok") else 400)


def handle_register_commands(handler, ctx, params, body):
    """外部 Webhook 服务注册自定义命令到 /help 列表。"""
    if not handler._check_api_token():
        return

    data = _load_json(handler, body)
    if data is None:
        return

    commands = data.get("commands", [])
    if not isinstance(commands, list) or not commands:
        handler._json_response({"ok": False, "error": "需要 commands 数组"}, 400)
        return

    # 内置命令保护
    builtin = {
        "/help",
        "/帮助",
        "/status",
        "/状态",
        "/pull",
        "/uid",
        "/retry",
        "/重试",
        "/clear",
        "/清除",
        "/ai",
        "/keepalive",
        "/保活",
        "/mute",
    }
    registered = []
    for item in commands:
        cmd = item.get("command", "").strip()
        desc = item.get("description", "").strip()
        if not cmd or not cmd.startswith("/"):
            continue
        if cmd.lower() in builtin or cmd.lower().startswith("/ai ") or cmd.lower().startswith("/keepalive "):
            continue
        ctx.bridge._webhook_commands[cmd] = desc or cmd
        registered.append(cmd)

    logger.info("外部命令注册: %s", registered)
    handler._json_response({"ok": True, "registered": registered})


def handle_unregister_commands(handler, ctx, params, body):
    """注销已注册的外部命令。"""
    if not handler._check_api_token():
        return

    data = _load_json(handler, body)
    if data is None:
        return

    commands = data.get("commands", [])
    removed = []
    if not commands:
        # 空数组 = 清空全部
        removed = list(ctx.bridge._webhook_commands.keys())
        ctx.bridge._webhook_commands.clear()
    else:
        for cmd in commands:
            cmd = cmd.strip() if isinstance(cmd, str) else ""
            if cmd in ctx.bridge._webhook_commands:
                del ctx.bridge._webhook_commands[cmd]
                removed.append(cmd)

    logger.info("外部命令注销: %s", removed)
    handler._json_response({"ok": True, "removed": removed})
