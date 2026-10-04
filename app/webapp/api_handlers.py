from __future__ import annotations

"""API route handlers。"""

import base64
import hmac
import importlib.util
import json
import logging
import mimetypes
import os
import queue
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs

import config as cfg
import db
import media as media_mod
from version import __version__
from webapp.auth import check_web_session, make_session_cookie
from webapp.markdown_utils import apply_markdown_mode
from webapp.request_utils import parse_multipart, parse_multipart_form
from webapp.ui.qr_page import _url_to_qr_base64
from webapp.webhook_parser import parse_webhook_payload

logger = logging.getLogger(__name__)
_weather_module = None


def _pick_default_contact(
    bridge,
    to: str,
    *,
    request_path: str = "",
    source: str = "",
    title: str = "",
    message_len: int = 0,
) -> str:
    if to:
        return to
    selected = bridge.get_default_contact()
    if selected:
        bridge.record_default_recipient_decision(
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


def _account_label(account: dict) -> str:
    return str(account.get("remark") or "").strip() or str(account.get("bot_id") or "").strip()


def _account_alias_payload(ctx, *, include_offline: bool = False) -> dict:
    if ctx.account_manager is not None:
        accounts = ctx.account_manager.list_accounts()
    else:
        runtime = ctx.resolve_runtime()
        accounts = []
        if runtime:
            accounts.append(
                {
                    "bot_id": runtime.bot_id,
                    "remark": "",
                    "ilink_user_id": getattr(runtime.client, "user_id", "") or "",
                    "logged_in": runtime.client.logged_in,
                    "is_default": 1,
                }
            )

    if not include_offline:
        accounts = [account for account in accounts if bool(account.get("logged_in"))]

    label_counts: dict[str, int] = {}
    for account in accounts:
        label = _account_label(account)
        if label:
            label_counts[label] = label_counts.get(label, 0) + 1

    aliases = {}
    ambiguous_aliases = {}
    items = []
    for account in accounts:
        bot_id = str(account.get("bot_id") or "").strip()
        if not bot_id:
            continue
        label = _account_label(account) or bot_id
        item = {
            "alias": label,
            "bot_id": bot_id,
            "remark": str(account.get("remark") or "").strip(),
            "ilink_user_id": str(account.get("ilink_user_id") or "").strip(),
            "logged_in": bool(account.get("logged_in")),
            "is_default": bool(account.get("is_default")),
        }
        items.append(item)
        if label_counts.get(label, 0) == 1:
            aliases[label] = bot_id
        else:
            ambiguous_aliases.setdefault(label, []).append(bot_id)

    default_bot_id = ""
    for item in items:
        if item["is_default"]:
            default_bot_id = item["bot_id"]
            break
    return {
        "ok": True,
        "aliases": aliases,
        "accounts": items,
        "default_bot_id": default_bot_id,
        "ambiguous_aliases": ambiguous_aliases,
    }


def _split_account_target(ctx, default_bridge, target: str):
    if ctx.account_manager is None or ":" not in target:
        return default_bridge, target, None
    account_ref, contact_ref = target.split(":", 1)
    account_ref = account_ref.strip()
    contact_ref = contact_ref.strip()
    if not account_ref or not contact_ref:
        return default_bridge, target, None
    runtime = ctx.resolve_runtime(account_ref)
    if runtime is None or not runtime.client.logged_in:
        return default_bridge, target, None
    return runtime.bridge, contact_ref, runtime.bot_id


def _normalize_command(command: str) -> str:
    command = str(command or "").strip().split()[0].lower()
    if command and not command.startswith("/"):
        command = f"/{command}"
    return command


def _run_bridge_command(
    ctx, default_bridge, target: str, command: str, args: str = "", *, source: str = "api_command"
) -> dict:
    bridge, resolved_target, routed_bot_id = _split_account_target(ctx, default_bridge, target)
    resolved_user = bridge.find_user_id(resolved_target)
    if not resolved_user:
        return {
            "ok": False,
            "error": f"找不到联系人「{resolved_target}」。对方需先给你发过消息才会出现在联系人列表中",
            "resolved_to": resolved_target,
            **({"bot_id": routed_bot_id} if routed_bot_id else {}),
        }

    normalized_command = _normalize_command(command)
    plugin_registry = getattr(bridge, "plugin_registry", None)
    plugin = plugin_registry.route_command(normalized_command, resolved_user) if plugin_registry else None
    if not plugin:
        return {
            "ok": False,
            "error": f"未注册命令: {normalized_command}",
            "resolved_to": resolved_user,
            **({"bot_id": routed_bot_id} if routed_bot_id else {}),
        }

    command_args = str(args or "").strip()
    text = f"{normalized_command} {command_args}".strip()
    bot_id = bridge.client.get_bot_id() if hasattr(bridge, "client") else ""
    payload = {
        "from_user": resolved_user,
        "from_name": bridge._contact_name(resolved_user),
        "text": text,
        "command": normalized_command,
        "args": command_args,
        "is_command": True,
        "bot_id": bot_id,
        "is_default": bot_id == (db.get_default_bot_id() or ""),
        "source": source,
        "msg_id": f"api-command-{uuid.uuid4().hex[:12]}",
        "timestamp": int(time.time()),
    }
    accepted = bridge.plugin_registry.dispatch_command(plugin, payload)
    return {
        "ok": bool(accepted),
        "command": normalized_command,
        "args": command_args,
        "plugin": plugin.name,
        "resolved_to": resolved_user,
        **({"bot_id": routed_bot_id or bot_id} if (routed_bot_id or bot_id) else {}),
        **({} if accepted else {"error": "插件执行失败"}),
    }


def _load_weather_module():
    global _weather_module
    if _weather_module is not None:
        return _weather_module

    project_root = Path(__file__).resolve().parents[2]
    module_path = project_root / "examples" / "webhook_receiver.py"
    if not module_path.exists():
        # 兼容 Docker 挂载情况 (./app:/app, ./examples:/app/examples)
        module_path = Path(__file__).resolve().parents[1] / "examples" / "webhook_receiver.py"
    if not module_path.exists():
        raise RuntimeError("找不到天气插件模块")

    spec = importlib.util.spec_from_file_location("bridge_weather_receiver", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载天气插件模块")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _weather_module = module
    return module


def _query_weather_snapshot(city: str, *, force_refresh: bool = False, include_minutely=None) -> dict:
    module = _load_weather_module()
    if not hasattr(module, "build_weather_snapshot"):
        raise RuntimeError("天气插件未提供结构化查询能力")
    return module.build_weather_snapshot(city, force_refresh=force_refresh, include_minutely=include_minutely)


def _weather_error_payload(exc: Exception) -> dict:
    payload = {"ok": False, "error": str(exc)}
    candidates = getattr(exc, "candidates", None)
    if candidates:
        payload["candidates"] = candidates
    status_code = getattr(exc, "status_code", None)
    if status_code:
        payload["status_code"] = status_code
    return payload


def _weather_error_status(exc: Exception) -> int:
    if getattr(exc, "candidates", None):
        return 400
    status_code = getattr(exc, "status_code", None)
    if status_code in {400, 401, 402, 403, 404, 429}:
        return int(status_code)
    return 500


def _send_target(
    ctx,
    default_bridge,
    target: str,
    text: str,
    *,
    source: str = "api",
    title: str = "",
    allow_buffer: bool = True,
    request_id: str = "",
) -> dict:
    bridge, resolved_target, routed_bot_id = _split_account_target(ctx, default_bridge, target)
    result = bridge.send(
        resolved_target,
        text,
        source=source,
        title=title,
        allow_buffer=allow_buffer,
        request_id=request_id,
    )
    if routed_bot_id:
        result = {**result, "bot_id": routed_bot_id, "resolved_to": resolved_target}
    return result


def _multicast_send(
    ctx,
    bridge,
    to_str: str,
    text: str,
    *,
    source: str = "api",
    title: str = "",
    allow_buffer: bool = True,
    request_id: str = "",
) -> dict:
    targets = [item.strip() for item in to_str.split(",") if item.strip()]
    if not targets:
        return {"ok": False, "error": "无有效目标"}

    if len(targets) == 1:
        return _send_target(
            ctx,
            bridge,
            targets[0],
            text,
            source=source,
            title=title,
            allow_buffer=allow_buffer,
            request_id=request_id,
        )

    results = []
    success = 0
    for index, target in enumerate(targets):
        result = _send_target(
            ctx,
            bridge,
            target,
            text,
            source=source,
            title=title,
            allow_buffer=allow_buffer,
            request_id=f"{request_id}.{index + 1}" if request_id else "",
        )
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


def _bot_id_from(params=None, data=None) -> str:
    if isinstance(data, dict) and data.get("bot_id"):
        return str(data.get("bot_id") or "").strip()
    if params:
        return params.get("bot_id", [""])[0].strip()
    return ""


def _truthy_param(params, name: str) -> bool:
    return params.get(name, [""])[0].strip().lower() in {"1", "true", "yes"}


def _bool_value(value, default=None):
    text = str(value if value is not None else "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _contacts_payload(bridge, *, include_history: bool = False) -> dict:
    if include_history or not hasattr(bridge, "get_visible_contacts"):
        contacts = bridge.get_ordered_contacts()
    else:
        contacts = bridge.get_visible_contacts()

    if include_history or not hasattr(bridge, "get_visible_contact_delivery_summaries"):
        delivery_states = bridge.get_contact_delivery_summaries()
    else:
        delivery_states = bridge.get_visible_contact_delivery_summaries()

    return {
        "contacts": contacts,
        "context_tokens": {k: v[:20] + "..." for k, v in bridge.context_tokens.items() if k in contacts},
        "delivery_states": delivery_states,
        "contacts_total": len(getattr(bridge, "contacts", contacts)),
    }


def _resolve_runtime(handler, ctx, params=None, data=None, *, require_logged_in: bool = False):
    bot_id = _bot_id_from(params, data) or None
    runtime = ctx.resolve_runtime(bot_id)
    if runtime is None:
        if bot_id:
            handler._json_response({"ok": False, "error": f"账号不存在: {bot_id}"}, 404)
        else:
            handler._json_response({"ok": False, "error": "未登录"}, 401)
        return None
    if require_logged_in and not runtime.client.logged_in:
        handler._json_response({"ok": False, "error": "未登录", "bot_id": runtime.bot_id}, 401)
        return None
    return runtime


def _account_message(status: str) -> str:
    return {
        "wait": "等待扫码",
        "scaned": "已扫码，请在微信确认",
        "scaned_but_redirect": "正在重定向",
        "expired": "二维码已过期",
        "confirmed": "登录成功",
    }.get(status or "", "")


def handle_web_check(handler, ctx, params):
    handler._json_response(
        {
            "authed": check_web_session(handler, ctx.api_token, ctx.session_secret),
            "need_auth": bool(ctx.api_token),
        }
    )


def handle_accounts(handler, ctx, params):
    if not handler._check_api_token():
        return
    if ctx.account_manager is None:
        runtime = ctx.resolve_runtime()
        accounts = []
        if runtime:
            accounts.append(
                {
                    "bot_id": runtime.bot_id,
                    "logged_in": runtime.client.logged_in,
                    "is_default": 1,
                    "contacts_count": len(
                        runtime.bridge.get_visible_contacts()
                        if hasattr(runtime.bridge, "get_visible_contacts")
                        else runtime.bridge.contacts
                    ),
                    "contacts_total": len(runtime.bridge.contacts),
                    "poll_running": runtime.bridge._running,
                    "data_dir": runtime.data_dir,
                }
            )
        handler._json_response({"accounts": accounts, "default_bot_id": runtime.bot_id if runtime else ""})
        return
    accounts = ctx.account_manager.list_accounts()
    default_bot_id = ""
    for account in accounts:
        if account.get("is_default"):
            default_bot_id = account.get("bot_id", "")
            break
    handler._json_response({"accounts": accounts, "default_bot_id": default_bot_id})


def handle_account_aliases(handler, ctx, params):
    if not handler._check_api_token():
        return
    include_offline = params.get("include_offline", [""])[0].strip() in {"1", "true", "yes"}
    handler._json_response(_account_alias_payload(ctx, include_offline=include_offline))


def handle_account_default(handler, ctx, params, body):
    if not handler._check_api_token():
        return
    data = _load_json(handler, body)
    if data is None:
        return
    bot_id = str(data.get("bot_id") or "").strip()
    if not bot_id:
        handler._json_response({"ok": False, "error": "缺少 bot_id"}, 400)
        return
    if ctx.account_manager is None:
        handler._json_response({"ok": False, "error": "当前运行模式不支持账号切换"}, 400)
        return
    if not ctx.account_manager.set_default(bot_id):
        handler._json_response({"ok": False, "error": f"账号不存在: {bot_id}"}, 404)
        return
    handler._json_response({"ok": True, "bot_id": bot_id})


def handle_account_logout(handler, ctx, params, body):
    if not handler._check_api_token():
        return
    data = _load_json(handler, body)
    if data is None:
        return
    bot_id = str(data.get("bot_id") or "").strip() or None
    if ctx.account_manager is None:
        runtime = ctx.resolve_runtime(bot_id)
        if not runtime:
            handler._json_response({"ok": False, "error": "账号不存在"}, 404)
            return
        runtime.bridge.record_account_event("logout", reason="web_logout")
        runtime.client.clear_token()
        ctx.qr_cache.data = None
        ctx.qr_cache.updated_at = 0.0
        handler._json_response({"ok": True})
        return
    if not ctx.account_manager.logout(bot_id):
        handler._json_response({"ok": False, "error": "账号不存在"}, 404)
        return
    handler._json_response({"ok": True})


def handle_account_qr(handler, ctx, params, body):
    if not handler._check_api_token():
        return
    if ctx.account_manager is None:
        handler._json_response({"ok": False, "error": "当前运行模式不支持多账号扫码"}, 400)
        return
    try:
        data = ctx.account_manager.create_login_qr()
        qr_url = data.get("qrcode_img_content", "")
        data["qr_image_base64"] = _url_to_qr_base64(qr_url) if qr_url else ""
        handler._json_response({"ok": True, **data})
    except Exception as exc:
        handler._json_response({"ok": False, "error": str(exc)}, 500)


def handle_ai_analyze(handler, ctx, params, body):
    if not handler._check_api_token():
        return
    data = _load_json(handler, body)
    if data is None:
        return
    prompt = str(data.get("prompt") or data.get("text") or data.get("content") or "").strip()
    system_prompt = str(data.get("system_prompt") or "").strip() or None
    if not prompt:
        handler._json_response({"ok": False, "error": "缺少 prompt"}, 400)
        return
    ai_manager = None
    if ctx.account_manager is not None:
        ai_manager = ctx.account_manager.ai_manager
    elif ctx.bridge is not None:
        ai_manager = ctx.bridge.ai_manager
    if ai_manager is None:
        handler._json_response({"ok": False, "error": "AI 未启用"}, 400)
        return
    try:
        result = ai_manager.one_shot(prompt, system_prompt=system_prompt)
        handler._json_response({"ok": True, "result": result, "text": result})
    except Exception as exc:
        handler._json_response({"ok": False, "error": str(exc)}, 500)


def handle_account_remark(handler, ctx, params, body):
    if not handler._check_api_token():
        return
    data = _load_json(handler, body)
    if data is None:
        return
    import db as db_mod

    bot_id = str(data.get("bot_id") or "").strip()
    remark = str(data.get("remark") or "")
    if not bot_id:
        handler._json_response({"ok": False, "error": "缺少 bot_id"}, 400)
        return
    if not db_mod.set_account_remark(bot_id, remark):
        handler._json_response({"ok": False, "error": f"账号不存在: {bot_id}"}, 404)
        return
    handler._json_response({"ok": True, "bot_id": bot_id, "remark": remark.strip()})


def handle_account_qr_status(handler, ctx, params):
    if not handler._check_api_token():
        return
    if ctx.account_manager is None:
        handler._json_response({"ok": False, "error": "当前运行模式不支持多账号扫码"}, 400)
        return
    login_id = params.get("login_id", [""])[0].strip()
    if not login_id:
        handler._json_response({"ok": False, "error": "缺少 login_id"}, 400)
        return
    try:
        status_data = ctx.account_manager.poll_login_qr_status(login_id)
        status = status_data.get("status")
        if status == "confirmed":
            ctx.qr_cache.data = None
            ctx.qr_cache.updated_at = 0.0
        handler._json_response(
            {
                "ok": True,
                "status": status,
                "logged_in": status == "confirmed",
                "bot_id": status_data.get("bot_id", ""),
                "message": _account_message(status),
            }
        )
    except KeyError:
        handler._json_response({"ok": False, "error": "登录会话不存在或已过期"}, 404)
    except Exception as exc:
        handler._json_response({"ok": False, "error": str(exc)}, 500)


def handle_status(handler, ctx, params):
    runtime = _resolve_runtime(handler, ctx, params)
    if runtime is None:
        return
    payload = runtime.bridge.get_runtime_status()
    payload["version"] = __version__
    if ctx.account_manager is not None:
        payload["accounts"] = ctx.account_manager.list_accounts()
    handler._json_response(payload)


def handle_contacts(handler, ctx, params):
    if not handler._check_api_token():
        return
    show_all = _truthy_param(params, "all")
    include_history = _truthy_param(params, "include_history")
    if show_all and ctx.account_manager is not None:
        aggregated_contacts = {}
        context_tokens = {}
        delivery_states = {}
        import db as db_mod

        bot_remarks = {}
        try:
            for acc in db_mod.list_bot_accounts():
                bot_id = acc.get("bot_id")
                if bot_id:
                    bot_remarks[bot_id] = acc.get("remark") or acc.get("ilink_user_id") or bot_id[:8]
        except Exception:
            pass
        for bot_id, runtime in ctx.account_manager.runtimes.items():
            if not runtime.client.logged_in:
                continue
            bot_identifier = runtime.client.user_id or bot_id
            remark = bot_remarks.get(bot_id, bot_id[:8])
            payload = _contacts_payload(runtime.bridge, include_history=include_history)
            for uid, name in payload["contacts"].items():
                key = f"{bot_identifier}:{uid}"
                aggregated_contacts[key] = f"{name} ({remark})"
            for k, v in runtime.bridge.context_tokens.items():
                if k not in payload["contacts"]:
                    continue
                context_tokens[f"{bot_identifier}:{k}"] = v[:20] + "..."
            for k, v in payload["delivery_states"].items():
                delivery_states[f"{bot_identifier}:{k}"] = v
        handler._json_response(
            {
                "contacts": aggregated_contacts,
                "context_tokens": context_tokens,
                "delivery_states": delivery_states,
            }
        )
        return
    runtime = _resolve_runtime(handler, ctx, params, require_logged_in=True)
    if runtime is None:
        return
    handler._json_response(_contacts_payload(runtime.bridge, include_history=include_history))


def handle_messages(handler, ctx, params):
    if not handler._check_api_token():
        return
    runtime = _resolve_runtime(handler, ctx, params, require_logged_in=True)
    if runtime is None:
        return
    limit = int(params.get("limit", ["200"])[0])
    before_id = params.get("before_id", [None])[0]
    if before_id:
        before_id = int(before_id)
    messages = runtime.bridge.db.get_messages(limit=limit, before_id=before_id)
    handler._json_response({"messages": messages})


def handle_delivery(handler, ctx, params):
    if not handler._check_api_token():
        return
    account_ref = _bot_id_from(params) or None
    runtime = ctx.resolve_runtime(account_ref)
    message_store = runtime.bridge.db if runtime is not None else None
    if message_store is None and account_ref and ctx.account_manager is not None:
        message_store = ctx.account_manager.get_account_message_store(account_ref)
    if message_store is None:
        if account_ref:
            handler._json_response({"ok": False, "error": f"账号不存在或消息库不可用: {account_ref}"}, 404)
        else:
            handler._json_response({"ok": False, "error": "未登录"}, 401)
        return
    message_id = params.get("message_id", [""])[0].strip()
    if not message_id:
        handler._json_response({"ok": False, "error": "缺少 message_id 参数"}, 400)
        return
    message = message_store.get_message_by_msg_id(message_id)
    if not message:
        handler._json_response({"ok": False, "error": "消息不存在"}, 404)
        return
    meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
    handler._json_response(
        {
            "ok": True,
            "message_id": message["msg_id"],
            "delivery_stage": message.get("delivery_stage") or "direct",
            "pending_message_id": message.get("pending_message_id"),
            "blocked_reason": meta.get("blocked_reason"),
            "overflow_session_id": message.get("overflow_session_id"),
        }
    )


def handle_get_ai_config(handler, ctx, params):
    if not ctx.any_logged_in():
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
        if not ctx.client:
            handler._json_response({"error": "legacy qr endpoint unavailable"}, 400)
            return
        status_data = ctx.client.poll_qrcode_status(qrcode)
        if status_data.get("status") == "expired":
            cached_qrcode = (ctx.qr_cache.data or {}).get("qrcode")
            if cached_qrcode == qrcode:
                ctx.qr_cache.data = None
                ctx.qr_cache.updated_at = 0.0
        if ctx.client.logged_in and status_data.get("status") == "confirmed" and ctx.bridge:
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
                "message": _account_message(status_data.get("status", "")),
            }
        )
    except Exception as exc:
        handler._json_response({"error": str(exc)}, 500)


def handle_send_get(handler, ctx, params):
    if not handler._check_api_token():
        return
    runtime = _resolve_runtime(handler, ctx, params, require_logged_in=True)
    if runtime is None:
        return

    title = params.get("title", [""])[0]
    text = params.get("text", [""])[0] or params.get("content", [""])[0]
    text = _compose_title_text(title, text)
    to = _pick_default_contact(
        runtime.bridge,
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
    allow_buffer = _bool_value(params.get("allow_buffer", [None])[0], True)
    request_id = str(params.get("request_id", [""])[0] or "").strip()
    result = _multicast_send(
        ctx,
        runtime.bridge,
        to,
        text,
        source="api",
        title=title,
        allow_buffer=allow_buffer,
        request_id=request_id,
    )
    handler._json_response(result, 200 if result.get("ok") else 400)


def handle_push_get(handler, ctx, params):
    if not handler._check_api_token():
        return
    runtime = _resolve_runtime(handler, ctx, params, require_logged_in=True)
    if runtime is None:
        return

    title = params.get("title", [""])[0]
    text = params.get("text", [""])[0] or params.get("content", [""])[0]
    final_text = _compose_title_text(title, text)
    to = _pick_default_contact(
        runtime.bridge,
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
    result = _multicast_send(ctx, runtime.bridge, to, final_text, source="api_push", title=title)
    handler._json_response(result, 200 if result.get("ok") else 400)


def handle_media(handler, ctx, path):
    params = parse_qs(handler.path.split("?", 1)[1]) if "?" in handler.path else {}
    runtime = _resolve_runtime(handler, ctx, params, require_logged_in=True)
    if runtime is None:
        return
    filename = path[len("/media/") :]
    filepath = media_mod.get_media_path(filename, media_dir=getattr(runtime.bridge, "_media_dir", None))
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
    if not hmac.compare_digest(token, ctx.api_token):
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

    data = _load_json(handler, body)
    if data is None:
        return
    runtime = _resolve_runtime(handler, ctx, params, data, require_logged_in=True)
    if runtime is None:
        return

    text = data.get("text", "") or data.get("content", "")
    title = data.get("title", "")
    to = _pick_default_contact(
        runtime.bridge,
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
    allow_buffer = _bool_value(data.get("allow_buffer"), True)
    request_id = str(data.get("request_id") or "").strip()
    result = _multicast_send(
        ctx,
        runtime.bridge,
        to,
        text,
        source="api",
        title=title,
        allow_buffer=allow_buffer,
        request_id=request_id,
    )
    handler._json_response(result, 200 if result.get("ok") else 400)


def handle_weather_query_get(handler, ctx, params):
    if not handler._check_api_token():
        return
    city = str(params.get("city", [""])[0] or params.get("q", [""])[0]).strip()
    if not city:
        handler._json_response({"ok": False, "error": "缺少 city 参数"}, 400)
        return
    try:
        force_refresh = (
            _truthy_param(params, "force")
            or _truthy_param(params, "refresh")
            or _truthy_param(params, "latest")
            or _truthy_param(params, "no_cache")
        )
        include_minutely = _bool_value(params.get("include_minutely", [""])[0], None)
        handler._json_response({"ok": True, "weather": _query_weather_snapshot(city, force_refresh=force_refresh, include_minutely=include_minutely)})
    except Exception as exc:
        logger.warning("天气结构化查询失败 [%s]: %s", city, exc)
        handler._json_response(_weather_error_payload(exc), _weather_error_status(exc))


def handle_weather_query_post(handler, ctx, params, body):
    if not handler._check_api_token():
        return
    data = _load_json(handler, body)
    if data is None:
        return
    city = str(data.get("city") or data.get("q") or "").strip()
    if not city:
        handler._json_response({"ok": False, "error": "缺少 city 参数"}, 400)
        return
    try:
        force_refresh = any(_bool_value(data.get(name), False) for name in ("force", "refresh", "latest", "no_cache"))
        include_minutely = _bool_value(data.get("include_minutely"), None)
        handler._json_response({"ok": True, "weather": _query_weather_snapshot(city, force_refresh=force_refresh, include_minutely=include_minutely)})
    except Exception as exc:
        logger.warning("天气结构化查询失败 [%s]: %s", city, exc)
        handler._json_response(_weather_error_payload(exc), _weather_error_status(exc))


def handle_run_command(handler, ctx, params, body):
    if not handler._check_api_token():
        return

    data = _load_json(handler, body)
    if data is None:
        return
    runtime = _resolve_runtime(handler, ctx, params, data, require_logged_in=True)
    if runtime is None:
        return

    target = str(data.get("to") or data.get("target") or "").strip()
    command = _normalize_command(str(data.get("command") or ""))
    args = str(data.get("args") or "").strip()
    source = str(data.get("source") or "api_command").strip() or "api_command"

    if not target:
        handler._json_response({"ok": False, "error": "缺少 to 参数"}, 400)
        return
    if not command:
        handler._json_response({"ok": False, "error": "缺少 command 参数"}, 400)
        return

    result = _run_bridge_command(ctx, runtime.bridge, target, command, args, source=source)
    handler._json_response(result, 200 if result.get("ok") else 400)


def handle_typing(handler, ctx, params, body):
    data = _load_json(handler, body)
    if data is None:
        return
    runtime = _resolve_runtime(handler, ctx, params, data, require_logged_in=True)
    if runtime is None:
        return

    to = data.get("to", "")
    if not to:
        handler._json_response({"ok": False, "error": "缺少 to 参数"}, 400)
        return

    bridge, resolved_to, routed_bot_id = _split_account_target(ctx, runtime.bridge, to)
    result = bridge.send_typing(resolved_to)
    if routed_bot_id:
        result = {**result, "bot_id": routed_bot_id, "resolved_to": resolved_to}
    handler._json_response(result, 200 if result.get("ok") else 400)


def handle_post_ai_config(handler, ctx, params, body):
    if not ctx.any_logged_in():
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
    if ctx.account_manager is not None:
        for runtime in ctx.account_manager.runtimes.values():
            if runtime.bridge.ai_manager:
                runtime.bridge.ai_manager.clear_all_histories()
    elif ctx.bridge and ctx.bridge.ai_manager:
        ctx.bridge.ai_manager.clear_all_histories()
    handler._json_response({"ok": True})


def handle_ag_inbox(handler, ctx, params, body):
    runtime = _resolve_runtime(handler, ctx, params, require_logged_in=True)
    if runtime is None:
        return
    with runtime.bridge._ag_inbox_lock:
        messages = runtime.bridge.ag_inbox
        runtime.bridge.ag_inbox = []
    handler._json_response({"ok": True, "messages": messages})


def handle_logout(handler, ctx, params, body):
    if ctx.account_manager is not None:
        bot_id = params.get("bot_id", [""])[0].strip() or None
        ctx.account_manager.logout(bot_id)
    elif ctx.bridge and ctx.client:
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

    to = ""
    text = ""
    title = ""
    markdown = ""
    markdown_mode = ""
    data = {}
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
        data = {"bot_id": form_data.get("bot_id", [""])[0]}

    runtime = _resolve_runtime(handler, ctx, params, locals().get("data", {}), require_logged_in=True)
    if runtime is None:
        return

    text = text or params.get("text", [""])[0] or params.get("content", [""])[0]
    title = title or params.get("title", [""])[0]
    final_text = _compose_title_text(title, text)
    to = _pick_default_contact(
        runtime.bridge,
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
    result = _multicast_send(ctx, runtime.bridge, to, final_text, source="api_push", title=title)
    handler._json_response(result, 200 if result.get("ok") else 400)


def handle_webhook(handler, ctx, path, params, body):
    if not handler._check_api_token():
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
    runtime = _resolve_runtime(handler, ctx, params, data, require_logged_in=True)
    if runtime is None:
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
        runtime.bridge,
        params.get("to", [""])[0],
        request_path="/api/webhook",
        source=source,
        message_len=len(text),
    )
    if not to:
        handler._json_response({"ok": False, "error": "无可用联系人"}, 400)
        return

    result = _multicast_send(ctx, runtime.bridge, to, text, source=source)
    handler._json_response(result, 200 if result.get("ok") else 400)


def handle_send_image(handler, ctx, params, body):
    if not handler._check_api_token():
        return

    to = ""
    image_data = None
    data = {}
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

    runtime = _resolve_runtime(handler, ctx, params, data, require_logged_in=True)
    if runtime is None:
        return

    to = _pick_default_contact(
        runtime.bridge,
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

    bridge, resolved_to, routed_bot_id = _split_account_target(ctx, runtime.bridge, to)
    result = bridge.send_image(resolved_to, image_data)
    if routed_bot_id:
        result = {**result, "bot_id": routed_bot_id, "resolved_to": resolved_to}
    handler._json_response(result, 200 if result.get("ok") else 400)


def handle_send_video(handler, ctx, params, body):
    if not handler._check_api_token():
        return

    to = ""
    video_data = None
    data = {}
    play_length = 0
    content_type = handler.headers.get("Content-Type", "")

    if "multipart/form-data" in content_type:
        to, video_data = parse_multipart(body, content_type, logger)
    elif content_type.startswith("application/json"):
        try:
            data = json.loads(body) if body else {}
            to = data.get("to", "")
            play_length = int(data.get("play_length") or 0)
            video_b64 = data.get("video", "")
            if video_b64:
                video_data = base64.b64decode(video_b64)
        except Exception as exc:
            handler._json_response({"ok": False, "error": f"JSON 解析失败: {exc}"}, 400)
            return
    else:
        video_data = body

    runtime = _resolve_runtime(handler, ctx, params, data, require_logged_in=True)
    if runtime is None:
        return

    if not play_length:
        try:
            play_length = int(params.get("play_length", ["0"])[0] or 0)
        except ValueError:
            play_length = 0

    to = _pick_default_contact(
        runtime.bridge,
        to or params.get("to", [""])[0],
        request_path="/api/send_video",
        source="video",
        message_len=len(video_data or b""),
    )
    if not to:
        handler._json_response({"ok": False, "error": "缺少 to 参数且无联系人"}, 400)
        return
    if not video_data or len(video_data) < 1024:
        handler._json_response({"ok": False, "error": "缺少视频数据或数据过小"}, 400)
        return
    if len(video_data) > 50 * 1024 * 1024:
        handler._json_response({"ok": False, "error": "视频大小不能超过 50MB"}, 400)
        return

    bridge, resolved_to, routed_bot_id = _split_account_target(ctx, runtime.bridge, to)
    result = None
    tmp_path = ""
    if hasattr(bridge, "send_video_path"):
        fd, tmp_path = tempfile.mkstemp(prefix="wb_api_video_", suffix=".mp4")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(video_data)
            result = bridge.send_video_path(resolved_to, tmp_path, play_length=play_length)
            tmp_path = ""
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
    else:
        result = bridge.send_video(resolved_to, video_data, play_length=play_length)
    if routed_bot_id:
        result = {**result, "bot_id": routed_bot_id, "resolved_to": resolved_to}
    handler._json_response(result, 200 if result.get("ok") else 400)


def handle_send_voice(handler, ctx, params, body):
    if not handler._check_api_token():
        return

    to = ""
    voice_data = None
    data = {}
    playtime_ms = 0
    text = ""
    content_type = handler.headers.get("Content-Type", "")

    if "multipart/form-data" in content_type:
        to, voice_data = parse_multipart(body, content_type, logger)
    elif content_type.startswith("application/json"):
        try:
            data = json.loads(body) if body else {}
            to = data.get("to", "")
            playtime_ms = int(data.get("playtime_ms") or data.get("playtime") or 0)
            text = str(data.get("text") or "")
            voice_b64 = data.get("voice") or data.get("audio") or ""
            if voice_b64:
                voice_data = base64.b64decode(voice_b64)
        except Exception as exc:
            handler._json_response({"ok": False, "error": f"JSON 解析失败: {exc}"}, 400)
            return
    else:
        voice_data = body

    runtime = _resolve_runtime(handler, ctx, params, data, require_logged_in=True)
    if runtime is None:
        return

    if not playtime_ms:
        try:
            playtime_ms = int(params.get("playtime_ms", ["0"])[0] or params.get("playtime", ["0"])[0] or 0)
        except ValueError:
            playtime_ms = 0

    to = _pick_default_contact(
        runtime.bridge,
        to or params.get("to", [""])[0],
        request_path="/api/send_voice",
        source="voice",
        message_len=len(voice_data or b""),
    )
    if not to:
        handler._json_response({"ok": False, "error": "缺少 to 参数且无联系人"}, 400)
        return
    if not voice_data or len(voice_data) < 64:
        handler._json_response({"ok": False, "error": "缺少语音数据或数据过小"}, 400)
        return
    if len(voice_data) > 10 * 1024 * 1024:
        handler._json_response({"ok": False, "error": "语音大小不能超过 10MB"}, 400)
        return
    if not media_mod.is_silk(voice_data):
        handler._json_response({"ok": False, "error": "语音消息只支持 SILK v3 编码（#!SILK_V3），请先转换后上传。"}, 400)
        return

    bridge, resolved_to, routed_bot_id = _split_account_target(ctx, runtime.bridge, to)
    result = bridge.send_voice(resolved_to, voice_data, playtime_ms=playtime_ms, text=text)
    if routed_bot_id:
        result = {**result, "bot_id": routed_bot_id, "resolved_to": resolved_to}
    handler._json_response(result, 200 if result.get("ok") else 400)


def handle_send_file(handler, ctx, params, body):
    if not handler._check_api_token():
        return

    to = ""
    file_data = None
    file_name = ""
    text = ""
    data = {}
    content_type = handler.headers.get("Content-Type", "")

    if "multipart/form-data" in content_type:
        parsed = parse_multipart_form(body, content_type, logger)
        fields = parsed["fields"]
        files = parsed["files"]
        file_part = files.get("file")
        if file_part:
            file_data = file_part["content"]
            file_name = file_part.get("filename") or ""
        to = fields.get("to", "")
        text = fields.get("text", "") or fields.get("content", "")
        data = {"bot_id": fields.get("bot_id", "")}
    elif content_type.startswith("application/json"):
        try:
            data = json.loads(body) if body else {}
            to = data.get("to", "")
            text = str(data.get("text") or data.get("content") or "")
            file_name = str(data.get("file_name") or data.get("filename") or "file.bin")
            file_b64 = data.get("file") or data.get("data") or ""
            if file_b64:
                file_data = base64.b64decode(file_b64)
        except Exception as exc:
            handler._json_response({"ok": False, "error": f"JSON 解析失败: {exc}"}, 400)
            return
    else:
        file_data = body
        file_name = params.get("file_name", [""])[0] or params.get("filename", [""])[0] or "file.bin"

    runtime = _resolve_runtime(handler, ctx, params, data, require_logged_in=True)
    if runtime is None:
        return

    to = _pick_default_contact(
        runtime.bridge,
        to or params.get("to", [""])[0],
        request_path="/api/send_file",
        source="file",
        message_len=len(file_data or b""),
    )
    if not to:
        handler._json_response({"ok": False, "error": "缺少 to 参数且无联系人"}, 400)
        return
    if not file_data:
        handler._json_response({"ok": False, "error": "缺少文件数据"}, 400)
        return
    if len(file_data) > 50 * 1024 * 1024:
        handler._json_response({"ok": False, "error": "文件大小不能超过 50MB"}, 400)
        return

    bridge, resolved_to, routed_bot_id = _split_account_target(ctx, runtime.bridge, to)
    result = bridge.send_file(resolved_to, file_data, file_name=file_name or "file.bin", text=text)
    if routed_bot_id:
        result = {**result, "bot_id": routed_bot_id, "resolved_to": resolved_to}
    handler._json_response(result, 200 if result.get("ok") else 400)


def handle_send_reference(handler, ctx, params, body):
    if not handler._check_api_token():
        return

    data = {}
    if body:
        try:
            data = json.loads(body)
        except Exception as exc:
            handler._json_response({"ok": False, "error": f"JSON 解析失败: {exc}"}, 400)
            return

    runtime = _resolve_runtime(handler, ctx, params, data, require_logged_in=True)
    if runtime is None:
        return

    text = str(data.get("text") or data.get("content") or params.get("text", [""])[0] or params.get("content", [""])[0])
    ref_text = str(data.get("ref_text") or data.get("quote_text") or params.get("ref_text", [""])[0] or params.get("quote_text", [""])[0])
    ref_title = str(data.get("ref_title") or data.get("quote_title") or params.get("ref_title", [""])[0] or params.get("quote_title", [""])[0])
    to = _pick_default_contact(
        runtime.bridge,
        str(data.get("to") or params.get("to", [""])[0]),
        request_path="/api/send_reference",
        source="reference",
        message_len=len(text),
    )
    if not to:
        handler._json_response({"ok": False, "error": "缺少 to 参数且无联系人"}, 400)
        return
    if not text:
        handler._json_response({"ok": False, "error": "引用消息正文不能为空"}, 400)
        return
    if not ref_text and not ref_title:
        handler._json_response({"ok": False, "error": "引用消息需要 ref_text 或 ref_title"}, 400)
        return

    bridge, resolved_to, routed_bot_id = _split_account_target(ctx, runtime.bridge, to)
    result = bridge.send_reference_text(resolved_to, text, ref_text=ref_text, ref_title=ref_title)
    if routed_bot_id:
        result = {**result, "bot_id": routed_bot_id, "resolved_to": resolved_to}
    handler._json_response(result, 200 if result.get("ok") else 400)


def handle_register_commands(handler, ctx, params, body):
    """外部 Webhook 服务注册自定义命令到 /help 列表。"""
    if not handler._check_api_token():
        return

    data = _load_json(handler, body)
    if data is None:
        return
    runtime = _resolve_runtime(handler, ctx, params, data, require_logged_in=False)
    if runtime is None:
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
        cmd = item.get("command", "").strip().lower()
        desc = item.get("description", "").strip()
        if not cmd or not cmd.startswith("/"):
            continue
        if cmd in builtin or cmd.startswith("/ai ") or cmd.startswith("/keepalive "):
            continue
        runtime.bridge._webhook_commands[cmd] = desc or cmd
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
    runtime = _resolve_runtime(handler, ctx, params, data, require_logged_in=False)
    if runtime is None:
        return

    commands = data.get("commands", [])
    removed = []
    if not commands:
        # 空数组 = 清空全部
        removed = list(runtime.bridge._webhook_commands.keys())
        runtime.bridge._webhook_commands.clear()
    else:
        for cmd in commands:
            cmd = cmd.strip() if isinstance(cmd, str) else ""
            if cmd in runtime.bridge._webhook_commands:
                del runtime.bridge._webhook_commands[cmd]
                removed.append(cmd)

    logger.info("外部命令注销: %s", removed)
    handler._json_response({"ok": True, "removed": removed})


def handle_events(handler, ctx, params):
    if not handler._check_api_token():
        return
    runtime = _resolve_runtime(handler, ctx, params, require_logged_in=True)
    if runtime is None:
        return

    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.end_headers()

    q = queue.Queue()

    def _on_event(e):
        q.put(e)

    sid = str(uuid.uuid4())
    from event_bus import EVENT_AI_REPLY_READY, EVENT_MESSAGE_RECEIVED, EVENT_MESSAGE_SENT

    runtime.bridge.event_bus.subscribe(EVENT_MESSAGE_RECEIVED, _on_event, subscriber_id=sid)
    runtime.bridge.event_bus.subscribe(EVENT_MESSAGE_SENT, _on_event, subscriber_id=sid)
    runtime.bridge.event_bus.subscribe(EVENT_AI_REPLY_READY, _on_event, subscriber_id=sid)

    try:
        while True:
            try:
                event = q.get(timeout=15)
                # Ensure no non-serializable objects in event.data
                data_str = json.dumps({"event": event.name, "data": event.data}, ensure_ascii=False)
                handler.wfile.write(f"data: {data_str}\n\n".encode())
                handler.wfile.flush()
            except queue.Empty:
                handler.wfile.write(b": keepalive\n\n")
                handler.wfile.flush()
    except Exception as exc:
        logger.debug("SSE 客户端断开连接: %s", exc)
    finally:
        runtime.bridge.event_bus.unsubscribe(EVENT_MESSAGE_RECEIVED, sid)
        runtime.bridge.event_bus.unsubscribe(EVENT_MESSAGE_SENT, sid)
        runtime.bridge.event_bus.unsubscribe(EVENT_AI_REPLY_READY, sid)
