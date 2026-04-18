"""API route handlers。"""

import base64
import json
import logging
import mimetypes
from urllib.parse import parse_qs

import config as cfg
import db as msg_db
import media as media_mod

from webapp.auth import check_web_session, make_session_cookie
from webapp.markdown_utils import markdown_to_plain, should_plainify_markdown
from webapp.request_utils import parse_multipart
from webapp.webhook_parser import parse_webhook_payload

logger = logging.getLogger(__name__)


def _pick_default_contact(ctx, to: str) -> str:
    if to:
        return to
    if ctx.bridge.contacts:
        return list(ctx.bridge.contacts.values())[0]
    return ""


def _compose_title_text(title: str, text: str) -> str:
    if title and text:
        return f"【{title}】\n{text}"
    if title:
        return title
    return text


def _maybe_plainify(text: str, *flags) -> str:
    if any(should_plainify_markdown(flag) for flag in flags):
        return markdown_to_plain(text)
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
            import time as _time

            _time.sleep(0.5)

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
    handler._json_response(ctx.bridge.get_runtime_status())


def handle_contacts(handler, ctx, params):
    if not handler._check_api_token():
        return
    handler._json_response(
        {
            "contacts": ctx.bridge.contacts,
            "context_tokens": {k: v[:20] + "..." for k, v in ctx.bridge.context_tokens.items()},
            "delivery_states": ctx.bridge.get_contact_delivery_summaries(),
        }
    )


def handle_messages(handler, ctx, params):
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
        if ctx.client.logged_in and status_data.get("status") == "confirmed":
            ctx.bridge._setup_data_dir()
            ctx.bridge._load_contacts()
            ctx.bridge.recent_messages.clear()
            ctx.bridge._consecutive_send_count.clear()
            logger.info("登录成功，数据目录已切换到 bot: %s", ctx.client.get_bot_id())
        handler._json_response(
            {
                "status": status_data.get("status"),
                "logged_in": ctx.client.logged_in,
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

    to = _pick_default_contact(ctx, params.get("to", [""])[0])
    title = params.get("title", [""])[0]
    text = params.get("text", [""])[0] or params.get("content", [""])[0]
    text = _compose_title_text(title, text)

    if not to:
        handler._json_response({"ok": False, "error": "无可用联系人，请指定 to 参数"}, 400)
        return
    if not text:
        handler._json_response({"ok": False, "error": "缺少 text 参数"}, 400)
        return

    text = _maybe_plainify(
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

    to = _pick_default_contact(ctx, params.get("to", [""])[0])
    title = params.get("title", [""])[0]
    text = params.get("text", [""])[0] or params.get("content", [""])[0]
    final_text = _compose_title_text(title, text)

    if not to or not final_text:
        handler._json_response({"ok": False, "error": "需要 to 和 text 或 content 参数"}, 400)
        return

    final_text = _maybe_plainify(
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

    to = _pick_default_contact(ctx, data.get("to", ""))
    text = data.get("text", "") or data.get("content", "")

    if not to:
        handler._json_response({"ok": False, "error": "无可用联系人，请指定 to 参数"}, 400)
        return
    if not text:
        handler._json_response({"ok": False, "error": "缺少 text 参数"}, 400)
        return

    text = _maybe_plainify(text, data.get("markdown"), data.get("markdown_mode"))
    result = _multicast_send(ctx, to, text, source="api", title=data.get("title", ""))
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
    ):
        if key in data:
            current[key] = data[key]

    if "api_key" in data:
        new_key = data["api_key"].strip()
        if "*" not in new_key:
            current["api_key"] = new_key

    cfg.save_config(current)
    if ctx.bridge.ai_manager:
        ctx.bridge.ai_manager.clear_all_histories()
    handler._json_response({"ok": True})


def handle_ag_inbox(handler, ctx, params, body):
    messages = ctx.bridge.ag_inbox.copy()
    ctx.bridge.ag_inbox.clear()
    handler._json_response({"ok": True, "messages": messages})


def handle_logout(handler, ctx, params, body):
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
    content_type = handler.headers.get("Content-Type", "")
    if content_type.startswith("application/json"):
        try:
            data = json.loads(body) if body else {}
            to = data.get("to", "")
            text = data.get("text", "") or data.get("content", "")
        except Exception:
            pass
    else:
        form_data = parse_qs(body.decode("utf-8"))
        to = form_data.get("to", [""])[0]
        text = form_data.get("text", [""])[0] or form_data.get("content", [""])[0]

    to = _pick_default_contact(ctx, to or params.get("to", [""])[0])
    text = text or params.get("text", [""])[0] or params.get("content", [""])[0]
    title = params.get("title", [""])[0]
    final_text = _compose_title_text(title, text)

    if not to or not final_text:
        handler._json_response({"ok": False, "error": "需要 to 和 text 或 content 参数"}, 400)
        return

    final_text = _maybe_plainify(
        final_text,
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

    to = _pick_default_contact(ctx, params.get("to", [""])[0])
    if not to:
        handler._json_response({"ok": False, "error": "无可用联系人"}, 400)
        return

    try:
        data = json.loads(body) if body else {}
    except Exception:
        handler._json_response({"ok": False, "error": "无效 JSON"}, 400)
        return

    text = parse_webhook_payload(data, schema)
    if not text:
        handler._json_response({"ok": False, "error": "无法解析 Webhook 内容"}, 400)
        return

    result = _multicast_send(ctx, to, text, source=f"webhook:{schema or 'generic'}")
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

    to = _pick_default_contact(ctx, to or params.get("to", [""])[0])
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
