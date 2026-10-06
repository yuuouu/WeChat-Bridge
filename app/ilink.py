from __future__ import annotations

"""
iLink Bot API 封装
纯 HTTP/JSON 调用腾讯 iLink 服务，无需 OpenClaw CLI。
"""

import base64
import json
import logging
import os
import re
import struct
import time
from pathlib import Path

import requests

from version import __version__ as BRIDGE_VERSION

logger = logging.getLogger(__name__)

FIXED_BASE_URL = "https://ilinkai.weixin.qq.com"
BASE_URL = "https://ilinkai.weixin.qq.com"
ILINK_CHANNEL_VERSION = "2.4.9"
ILINK_APP_ID = "bot"
ILINK_APP_CLIENT_VERSION = (2 << 16) | (4 << 8) | 9
STALE_TOKEN_ERRCODE = -14
SESSION_PAUSE_SECONDS = 60 * 60
DEFAULT_BOT_AGENT = f"WeChat-Bridge/{BRIDGE_VERSION}"
BOT_AGENT_MAX_BYTES = 256
TOKEN_FILE = os.environ.get("TOKEN_FILE", "./data/token.json")
_DEFAULT_TOKEN_FILE = object()

# UploadMediaType（对应 iLink proto GetUploadUrlReq.media_type）
UPLOAD_MEDIA_TYPE_IMAGE = 1
UPLOAD_MEDIA_TYPE_VIDEO = 2
UPLOAD_MEDIA_TYPE_FILE = 3
UPLOAD_MEDIA_TYPE_VOICE = 4

# MessageItemType（对应 iLink proto MessageItem.type，与上传 media_type 含义不同）
MESSAGE_ITEM_TYPE_TEXT = 1
MESSAGE_ITEM_TYPE_IMAGE = 2
MESSAGE_ITEM_TYPE_VOICE = 3
MESSAGE_ITEM_TYPE_FILE = 4
MESSAGE_ITEM_TYPE_VIDEO = 5


class ILinkAPIError(RuntimeError):
    """iLink HTTP 请求成功、但业务层拒绝了操作。"""

    def __init__(self, operation: str, *, ret=0, errcode=0, errmsg=""):
        self.operation = operation
        self.ret = ret
        self.errcode = errcode
        self.errmsg = str(errmsg or "")
        if ret == -2 or errcode == -2:
            detail = "会话上下文已失效或触发上游发送限制；有效期由服务端控制，请让用户重新互动后重试"
        elif ret == STALE_TOKEN_ERRCODE or errcode == STALE_TOKEN_ERRCODE:
            detail = "Bot token 已失效，客户端将暂停请求后重试；若持续失败请重新扫码"
        else:
            detail = self.errmsg or "unknown business error"
        super().__init__(
            f"{operation} failed: ret={ret}, errcode={errcode}, errmsg={self.errmsg or '(none)'}; {detail}"
        )


class ILinkSessionPausedError(RuntimeError):
    """服务端报告 token 陈旧后的临时冷却状态。"""

    def __init__(self, remaining_seconds: int):
        self.remaining_seconds = max(1, int(remaining_seconds))
        super().__init__(
            f"iLink session paused after errcode={STALE_TOKEN_ERRCODE}, retry in {self.remaining_seconds}s"
        )


def _build_client_version(version: str) -> int:
    """将 semver 编码为 iLink 的 0x00MMNNPP。"""
    parts = []
    for raw in str(version).split(".")[:3]:
        match = re.match(r"\d+", raw)
        parts.append(int(match.group(0)) if match else 0)
    parts.extend([0] * (3 - len(parts)))
    return ((parts[0] & 0xFF) << 16) | ((parts[1] & 0xFF) << 8) | (parts[2] & 0xFF)


def sanitize_bot_agent(raw: str | None) -> str:
    """按官方 2.4.x UA 风格规则清洗 bot_agent。"""
    if not raw or not isinstance(raw, str):
        return DEFAULT_BOT_AGENT
    product_re = re.compile(r"^[A-Za-z0-9_.-]{1,32}/[A-Za-z0-9_.+-]{1,32}$")
    comment_re = re.compile(r"^[\x20-\x27\x2A-\x7E]{1,64}$")
    raw_tokens = raw.strip().split()
    tokens: list[str] = []
    index = 0
    while index < len(raw_tokens):
        token = raw_tokens[index]
        if token.startswith("(") and not token.endswith(")"):
            combined = token
            while index + 1 < len(raw_tokens) and not combined.endswith(")"):
                index += 1
                combined += " " + raw_tokens[index]
            tokens.append(combined)
        else:
            tokens.append(token)
        index += 1

    accepted: list[str] = []
    pending: str | None = None
    for token in tokens:
        if token.startswith("(") and token.endswith(")"):
            inner = token[1:-1]
            if pending and comment_re.fullmatch(inner):
                accepted.append(f"{pending} ({inner})")
                pending = None
            elif pending:
                accepted.append(pending)
                pending = None
            continue
        if pending:
            accepted.append(pending)
            pending = None
        if product_re.fullmatch(token):
            pending = token
    if pending:
        accepted.append(pending)
    if not accepted:
        return DEFAULT_BOT_AGENT

    bounded: list[str] = []
    for token in accepted:
        candidate = " ".join([*bounded, token])
        if len(candidate.encode("utf-8")) > BOT_AGENT_MAX_BYTES:
            break
        bounded.append(token)
    return " ".join(bounded) or DEFAULT_BOT_AGENT


def classify_request_error(exc: Exception) -> dict:
    """把 requests/urllib3 网络异常归类，避免只有模糊的 fetch failed。"""
    detail = f"{type(exc).__name__}: {exc}"
    lowered = detail.lower()
    if isinstance(exc, requests.exceptions.Timeout) or "timed out" in lowered:
        return {"type": "timeout", "description": "request timeout"}
    if any(token in lowered for token in ("name or service not known", "nodename nor servname", "getaddrinfo")):
        return {"type": "dns", "description": "DNS resolution failed"}
    if any(token in lowered for token in ("ssl", "tls", "certificate")):
        return {"type": "tls", "description": "TLS handshake or certificate error"}
    if isinstance(exc, requests.exceptions.ConnectionError):
        return {"type": "tcp", "description": "TCP connection failed"}
    return {"type": "unknown", "description": "network request failed"}


def format_reference_fallback_text(text: str, *, ref_text: str = "", ref_title: str = "") -> str:
    """将引用消息降级为普通文本。

    OpenClaw 目前只在入站解析中使用 ref_msg；直接下发 ref_msg 会返回成功，
    但微信客户端不会渲染成原生引用气泡。
    """
    ref_parts = []
    if ref_title:
        ref_parts.append(ref_title.strip())
    if ref_text:
        normalized_ref_text = " ".join(ref_text.split())
        ref_parts.append(normalized_ref_text[:80] + ("..." if len(normalized_ref_text) > 80 else ""))
    ref_preview = " | ".join(part for part in ref_parts if part)
    return f"[引用:{ref_preview}]\n{text}"


def _random_uin() -> str:
    """生成随机 X-WECHAT-UIN（uint32 → 十进制字符串 → base64）"""
    rand_bytes = os.urandom(4)
    rand_uint32 = struct.unpack(">I", rand_bytes)[0]
    return base64.b64encode(str(rand_uint32).encode()).decode()


def _get_headers() -> dict:
    """构造 QR GET 请求专用 headers。"""
    headers = {
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }
    route_tag = os.environ.get("ILINK_ROUTE_TAG", "").strip()
    if route_tag:
        headers["SKRouteTag"] = route_tag
    return headers


def _json_headers(bot_token: str | None = None) -> dict:
    """构造 iLink JSON POST 请求头。"""
    h = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _random_uin(),
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }
    route_tag = os.environ.get("ILINK_ROUTE_TAG", "").strip()
    if route_tag:
        h["SKRouteTag"] = route_tag
    if bot_token:
        h["Authorization"] = f"Bearer {bot_token}"
    return h


_headers = _json_headers


def _base_info() -> dict:
    configured = os.environ.get("ILINK_BOT_AGENT", DEFAULT_BOT_AGENT)
    return {
        "channel_version": ILINK_CHANNEL_VERSION,
        "bot_agent": sanitize_bot_agent(configured),
    }


class ILinkClient:
    """iLink Bot API 客户端"""

    def __init__(self, token_file=_DEFAULT_TOKEN_FILE, *, load_token: bool = True, save_on_login: bool = True):
        if token_file is _DEFAULT_TOKEN_FILE:
            token_file = TOKEN_FILE
        self.token_file: str | None = token_file
        self.save_on_login = save_on_login
        self.bot_token: str | None = None
        self.base_url: str = BASE_URL
        self.bot_id: str | None = None
        self.user_id: str | None = None
        self.get_updates_buf: str = ""
        self.long_poll_timeout_ms: int = 35_000
        self._session_paused_until: float = 0
        self._login_poll_base_url: str = FIXED_BASE_URL
        self._session = requests.Session()
        if load_token:
            self._load_token()

    @staticmethod
    def _extract_bot_id(bot_token: str) -> str | None:
        """从 bot_token 中提取稳定唯一标识
        bot_token 格式: 'xxxx@im.bot:060000ab3d3d...' → 取 '@' 前的 'xxxx' 作为 bot_id
        """
        if not bot_token:
            return None
        if "@" in bot_token:
            return bot_token.split("@")[0]
        # 兜底：取 token 前 12 位
        return bot_token[:12] if len(bot_token) >= 12 else bot_token

    def get_bot_id(self) -> str | None:
        """获取当前登录的 bot 唯一标识（用于数据目录隔离）"""
        if self.bot_id:
            return self.bot_id
        return self._extract_bot_id(self.bot_token)

    @property
    def logged_in(self) -> bool:
        return self.bot_token is not None

    def session_pause_remaining(self) -> int:
        remaining = int(self._session_paused_until - time.time())
        if remaining <= 0:
            self._session_paused_until = 0
            return 0
        return remaining

    def _assert_session_active(self):
        remaining = self.session_pause_remaining()
        if remaining:
            raise ILinkSessionPausedError(remaining)

    def _pause_stale_session(self):
        self._session_paused_until = time.time() + SESSION_PAUSE_SECONDS

    def _validate_response(self, data: dict, operation: str) -> dict:
        ret = data.get("ret", 0)
        errcode = data.get("errcode", 0)
        if ret == STALE_TOKEN_ERRCODE or errcode == STALE_TOKEN_ERRCODE:
            self._pause_stale_session()
        if ret not in (None, 0) or errcode not in (None, 0):
            raise ILinkAPIError(operation, ret=ret, errcode=errcode, errmsg=data.get("errmsg"))
        return data

    def _validate_delivery_response(self, data: dict, operation: str) -> dict:
        self._validate_response(data, operation)
        # iLink 没有端到端送达回执。即使 ret=0，也只能证明后端受理。
        data.setdefault("_delivery_state", "accepted_unconfirmed")
        data.setdefault("_delivery_confirmed", False)
        if data.get("message_id") is not None:
            data["_server_message_id"] = str(data["message_id"])
        return data

    def _post_json(
        self,
        path: str,
        payload: dict,
        *,
        token: str | None = None,
        base_url: str | None = None,
        timeout: int = 15,
    ) -> dict:
        """POST JSON 到 iLink API，并统一注入 base_info。"""
        if token:
            self._assert_session_active()
        url = f"{base_url or self.base_url}/{path.lstrip('/')}"
        body = {**payload, "base_info": _base_info()}
        try:
            resp = self._session.post(
                url,
                headers=_json_headers(token),
                json=body,
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as exc:
            classified = classify_request_error(exc)
            logger.error(
                "%s 请求失败: type=%s description=%s endpoint=%s",
                path,
                classified["type"],
                classified["description"],
                url,
            )
            raise

    # ── Token 持久化 ──

    def _load_token(self):
        """从文件恢复 token"""
        if self.token_file and os.path.exists(self.token_file):
            try:
                with open(self.token_file) as f:
                    data = json.load(f)
                self.bot_token = data.get("bot_token")
                self.base_url = data.get("base_url", BASE_URL)
                self.bot_id = data.get("bot_id") or self._extract_bot_id(self.bot_token)
                self.user_id = data.get("user_id")
                self.get_updates_buf = data.get("get_updates_buf", "")
                if self.bot_token:
                    logger.info("已从文件恢复登录态: bot_id=%s", self.bot_id)
            except Exception as e:
                logger.warning("读取 token 文件失败: %s", e)

    def _save_token(self):
        """持久化 token 到文件"""
        if not self.token_file:
            return
        os.makedirs(os.path.dirname(self.token_file), exist_ok=True)
        with open(self.token_file, "w") as f:
            json.dump(
                {
                    "bot_token": self.bot_token,
                    "base_url": self.base_url,
                    "bot_id": self.bot_id,
                    "get_updates_buf": self.get_updates_buf,
                    "user_id": self.user_id,
                },
                f,
                indent=2,
            )

    def get_token_mtime(self) -> int:
        """返回 token 文件修改时间，用于账号审计。"""
        try:
            return int(os.path.getmtime(self.token_file)) if self.token_file else 0
        except OSError:
            return 0

    def clear_token(self):
        """清除登录态"""
        self.bot_token = None
        self.bot_id = None
        self.user_id = None
        self.get_updates_buf = ""
        self.long_poll_timeout_ms = 35_000
        self._session_paused_until = 0
        self._login_poll_base_url = FIXED_BASE_URL
        if self.token_file and os.path.exists(self.token_file):
            os.remove(self.token_file)
        logger.info("登录态已清除")

    # ── 登录流程 ──

    def get_qrcode(self, local_token_list: list[str] | None = None) -> dict:
        """
        获取登录二维码
        返回: {"qrcode": "xxx", "qrcode_img_content": "base64图片数据", "url": "扫码链接"}
        """
        tokens = [str(token).strip() for token in (local_token_list or []) if str(token).strip()][-10:]
        resp = self._session.post(
            f"{FIXED_BASE_URL}/ilink/bot/get_bot_qrcode",
            params={"bot_type": "3"},
            headers=_json_headers(),
            json={"local_token_list": tokens},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info("获取二维码成功: qrcode=%s", data.get("qrcode", "")[:20])
        return data

    def poll_qrcode_status(self, qrcode: str, verify_code: str = "") -> dict:
        """
        轮询扫码状态
        返回: {"status": "wait|scaned|confirmed|expired|scaned_but_redirect", ...}
        """
        params = {"qrcode": qrcode}
        if verify_code.strip():
            params["verify_code"] = verify_code.strip()
        resp = self._session.get(
            f"{self._login_poll_base_url}/ilink/bot/get_qrcode_status",
            params=params,
            headers=_get_headers(),
            timeout=45,  # 35s long-poll + 余量
        )
        resp.raise_for_status()
        data = resp.json()

        status = data.get("status")
        if status == "scaned_but_redirect":
            redirect_host = data.get("redirect_host")
            if redirect_host:
                self._login_poll_base_url = f"https://{redirect_host}"
        elif status == "expired":
            self._login_poll_base_url = FIXED_BASE_URL
        elif status == "confirmed":
            ilink_bot_id = data.get("ilink_bot_id")
            if not ilink_bot_id:
                raise RuntimeError("登录失败：服务器未返回 ilink_bot_id")
            self.bot_token = data["bot_token"]
            self.base_url = data.get("baseurl") or BASE_URL
            self.bot_id = ilink_bot_id
            self.user_id = data.get("ilink_user_id")
            self._login_poll_base_url = FIXED_BASE_URL
            if self.save_on_login:
                self._save_token()
            logger.info("扫码登录成功! bot_id=%s", self.bot_id)

        return data

    # ── 消息收发 ──

    def get_updates(self, timeout: int = 35) -> list[dict]:
        """
        长轮询收取消息
        返回消息列表，同时更新游标 get_updates_buf
        """
        if not self.bot_token:
            raise RuntimeError("未登录，请先扫码")

        try:
            effective_timeout = max(timeout, int(self.long_poll_timeout_ms / 1000))
            data = self._post_json(
                "ilink/bot/getupdates",
                {"get_updates_buf": self.get_updates_buf},
                token=self.bot_token,
                timeout=effective_timeout + 10,  # 比服务器 hold 时间多留一点
            )

            ret = data.get("ret", 0)
            errcode = data.get("errcode", 0)
            if ret != 0 or errcode != 0:
                logger.warning("getupdates 返回异常数据: %s", json.dumps(data, ensure_ascii=False))
                if ret == STALE_TOKEN_ERRCODE or errcode == STALE_TOKEN_ERRCODE:
                    self._pause_stale_session()
                    logger.error("Bot token 已失效，暂停 iLink 请求 1 小时后重试")
                elif ret in (-1, 401, 403) or errcode in (401, 403, "TokenExpired"):
                    logger.error("Token 可能已过期，需重新扫码登录")
                    self.clear_token()
                return []

            suggested_timeout = data.get("longpolling_timeout_ms")
            if isinstance(suggested_timeout, (int, float)) and suggested_timeout > 0:
                self.long_poll_timeout_ms = max(1_000, int(suggested_timeout))

            # 更新游标
            new_buf = data.get("get_updates_buf")
            if new_buf:
                self.get_updates_buf = new_buf
                self._save_token()

            msgs = data.get("msgs") or []
            if msgs:
                logger.info("收到 %d 条消息", len(msgs))
            return msgs

        except requests.exceptions.Timeout:
            # 长轮询超时是正常的（无新消息时）
            return []
        except requests.exceptions.ConnectionError as e:
            logger.warning("连接错误: %s", e)
            raise

    def send_text(self, to_user_id: str, text: str, context_token: str = "") -> dict:
        """
        发送文本消息
        context_token: 从收到的消息中获取，用于关联对话
        """
        if not self.bot_token:
            raise RuntimeError("未登录，请先扫码")

        client_id = f"openclaw-weixin:{int(time.time() * 1000)}-{os.urandom(4).hex()}"
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": client_id,
                "message_type": 2,  # BOT 发出
                "message_state": 2,  # FINISH（完整消息）
                "context_token": context_token,
                "item_list": [{"type": MESSAGE_ITEM_TYPE_TEXT, "text_item": {"text": text}}],
            },
        }

        data = self._post_json(
            "ilink/bot/sendmessage",
            payload,
            token=self.bot_token,
            timeout=8,
        )
        self._validate_delivery_response(data, "sendMessage")
        ret = data.get("ret", 0)

        logger.info("发送消息到 %s: %s (ret=%s)", to_user_id[:20], text[:50], ret)
        return data

    def send_typing(self, to_user_id: str, context_token: str = "", *, status: int = 1) -> dict:
        """发送"正在输入"状态"""
        if not self.bot_token:
            raise RuntimeError("未登录")

        # 先获取 typing_ticket
        config_payload = {
            "ilink_user_id": to_user_id,
            "context_token": context_token,
        }
        config_data = self._post_json(
            "ilink/bot/getconfig",
            config_payload,
            token=self.bot_token,
            timeout=10,
        )
        self._validate_response(config_data, "getConfig")
        typing_ticket = config_data.get("typing_ticket", "")
        if not typing_ticket:
            raise ILinkAPIError("getConfig", errmsg="missing typing_ticket")

        payload = {
            "ilink_user_id": to_user_id,
            "typing_ticket": typing_ticket,
            "status": 2 if status == 2 else 1,
        }

        data = self._post_json(
            "ilink/bot/sendtyping",
            payload,
            token=self.bot_token,
            timeout=10,
        )
        self._validate_response(data, "sendTyping")

        return data

    def notify_start(self) -> dict:
        """通知 iLink 后端当前账号客户端已启动。失败由调用方按非致命错误处理。"""
        if not self.bot_token:
            raise RuntimeError("未登录")
        data = self._post_json(
            "ilink/bot/msg/notifystart",
            {},
            token=self.bot_token,
            timeout=10,
        )
        return self._validate_response(data, "notifyStart")

    def notify_stop(self) -> dict:
        """通知 iLink 后端当前账号客户端正在停止。"""
        if not self.bot_token:
            raise RuntimeError("未登录")
        data = self._post_json(
            "ilink/bot/msg/notifystop",
            {},
            token=self.bot_token,
            timeout=10,
        )
        return self._validate_response(data, "notifyStop")

    # ── 媒体上传 ──

    def upload_media(self, file_data: bytes, media_type: int = UPLOAD_MEDIA_TYPE_IMAGE, to_user_id: str = "") -> dict:
        """
        上传媒体文件到腾讯 CDN

        流程: 生成 AES key → 加密文件 → 获取上传 URL → 上传到 CDN → 返回下载凭证

        参数:
            file_data: 原始文件字节
            media_type: 1=图片, 2=视频, 3=文件, 4=语音
        返回:
            {"encrypt_query_param": "...", "aes_key_b64": "...", "file_size": ...}
        """
        if not self.bot_token:
            raise RuntimeError("未登录，请先扫码")

        import media as media_mod

        # 1. 生成随机 AES-128 密钥
        aes_key = os.urandom(16)

        # 2. AES-128-ECB 加密文件
        encrypted_data = media_mod.encrypt_aes_ecb(file_data, aes_key)

        # 3. 生成 filekey
        import hashlib

        filekey = hashlib.md5(file_data[:1024] + str(time.time()).encode()).hexdigest()

        # 4. 获取上传 URL
        rawfilemd5 = hashlib.md5(file_data).hexdigest()

        upload_req = {
            "filekey": filekey,
            "media_type": media_type,
            "rawsize": len(file_data),
            "rawfilemd5": rawfilemd5,
            "filesize": len(encrypted_data),
            "no_need_thumb": True,
            "aeskey": aes_key.hex(),
            "to_user_id": to_user_id,
        }
        logger.info("getuploadurl req: %s", json.dumps(upload_req))
        upload_data = self._post_json(
            "ilink/bot/getuploadurl",
            upload_req,
            token=self.bot_token,
            timeout=15,
        )

        self._validate_response(upload_data, "getUploadUrl")

        upload_param = upload_data.get("upload_param", "")
        cdn_upload_url = upload_data.get("upload_full_url", "").strip()
        if not upload_param and not cdn_upload_url:
            raise RuntimeError(f"上传 URL 为空: {json.dumps(upload_data, ensure_ascii=False)}")

        # 5. 上传加密文件到 CDN
        if not cdn_upload_url:
            import urllib.parse

            cdn_upload_url = f"https://novac2c.cdn.weixin.qq.com/c2c/upload?encrypted_query_param={urllib.parse.quote(upload_param)}&filekey={urllib.parse.quote(filekey)}"

        upload_resp = None
        last_error = None
        for attempt in range(1, 4):
            try:
                upload_resp = self._session.post(
                    cdn_upload_url,
                    headers={"Content-Type": "application/octet-stream"},
                    data=encrypted_data,
                    timeout=60,
                )
                upload_resp.raise_for_status()
                if upload_resp.headers.get("X-Encrypted-Param") or upload_resp.headers.get("x-encrypted-param"):
                    break
                raise RuntimeError("CDN response missing x-encrypted-param")
            except Exception as exc:
                last_error = exc
                status_code = getattr(getattr(exc, "response", None), "status_code", 0) or 0
                if 400 <= status_code < 500 or attempt == 3:
                    raise
                logger.warning("CDN 上传第 %d 次失败，准备重试: %s", attempt, exc)
                time.sleep(attempt)
        if upload_resp is None:
            raise RuntimeError(f"CDN 上传失败: {last_error}")

        # 6. 从响应头提取下载凭证
        download_ref = upload_resp.headers.get("X-Encrypted-Param") or upload_resp.headers.get("x-encrypted-param")

        if not download_ref:
            logger.warning(
                "CDN 上传成功但似乎没有返回 x-encrypted-param，使用 upload_param 可能会导致客户端无法下载。Headers: %s",
                upload_resp.headers,
            )
            download_ref = upload_param or upload_data.get("upload_full_url", "")
        if not download_ref:
            raise RuntimeError("CDN 上传成功但缺少下载凭证")

        logger.info(
            "媒体上传成功: filekey=%s, size=%d, encrypted_size=%d", filekey, len(file_data), len(encrypted_data)
        )

        # WeChat 客户端可能期望 AES_Key 是 hex 字符串的 Base64 编码，参照 openclaw-weixin
        aes_key_hex = aes_key.hex()
        aes_key_b64 = base64.b64encode(aes_key_hex.encode("utf-8")).decode()

        return {
            "encrypt_query_param": download_ref,
            "aes_key_b64": aes_key_b64,
            "aes_key_hex": aes_key_hex,
            "file_size": len(file_data),
            "encrypted_size": len(encrypted_data),
        }

    def upload_media_path(self, filepath: str, media_type: int = UPLOAD_MEDIA_TYPE_VIDEO, to_user_id: str = "") -> dict:
        """从文件路径上传媒体，避免把原始媒体整体读入内存。"""
        if not self.bot_token:
            raise RuntimeError("未登录，请先扫码")

        import hashlib

        import media as media_mod

        path = str(Path(filepath))
        if not os.path.isfile(path):
            raise FileNotFoundError(path)

        aes_key = os.urandom(16)
        meta = media_mod.inspect_media_file(path)
        filekey = hashlib.md5(meta["first1024"] + str(time.time()).encode()).hexdigest()
        encrypted_path, encrypted_size = media_mod.create_encrypted_upload_file(path, aes_key)

        upload_req = {
            "filekey": filekey,
            "media_type": media_type,
            "rawsize": meta["rawsize"],
            "rawfilemd5": meta["rawfilemd5"],
            "filesize": encrypted_size,
            "no_need_thumb": True,
            "aeskey": aes_key.hex(),
            "to_user_id": to_user_id,
        }
        logger.info("getuploadurl req: %s", json.dumps(upload_req))
        try:
            upload_data = self._post_json(
                "ilink/bot/getuploadurl",
                upload_req,
                token=self.bot_token,
                timeout=15,
            )

            self._validate_response(upload_data, "getUploadUrl")

            upload_param = upload_data.get("upload_param", "")
            cdn_upload_url = upload_data.get("upload_full_url", "").strip()
            if not upload_param and not cdn_upload_url:
                raise RuntimeError(f"上传 URL 为空: {json.dumps(upload_data, ensure_ascii=False)}")

            if not cdn_upload_url:
                import urllib.parse

                cdn_upload_url = f"https://novac2c.cdn.weixin.qq.com/c2c/upload?encrypted_query_param={urllib.parse.quote(upload_param)}&filekey={urllib.parse.quote(filekey)}"

            upload_resp = None
            last_error = None
            for attempt in range(1, 4):
                try:
                    with open(encrypted_path, "rb") as encrypted_fh:
                        upload_resp = self._session.post(
                            cdn_upload_url,
                            headers={"Content-Type": "application/octet-stream"},
                            data=encrypted_fh,
                            timeout=60,
                        )
                    upload_resp.raise_for_status()
                    if upload_resp.headers.get("X-Encrypted-Param") or upload_resp.headers.get("x-encrypted-param"):
                        break
                    raise RuntimeError("CDN response missing x-encrypted-param")
                except Exception as exc:
                    last_error = exc
                    status_code = getattr(getattr(exc, "response", None), "status_code", 0) or 0
                    if 400 <= status_code < 500 or attempt == 3:
                        raise
                    logger.warning("CDN 文件上传第 %d 次失败，准备重试: %s", attempt, exc)
                    time.sleep(attempt)
            if upload_resp is None:
                raise RuntimeError(f"CDN 上传失败: {last_error}")
        finally:
            try:
                os.unlink(encrypted_path)
            except OSError:
                pass

        download_ref = upload_resp.headers.get("X-Encrypted-Param") or upload_resp.headers.get("x-encrypted-param")
        if not download_ref:
            logger.warning(
                "CDN 上传成功但似乎没有返回 x-encrypted-param，使用 upload_param 可能会导致客户端无法下载。Headers: %s",
                upload_resp.headers,
            )
            download_ref = upload_param or upload_data.get("upload_full_url", "")
        if not download_ref:
            raise RuntimeError("CDN 上传成功但缺少下载凭证")

        logger.info(
            "媒体文件上传成功: filekey=%s, size=%d, encrypted_size=%d", filekey, meta["rawsize"], encrypted_size
        )

        aes_key_hex = aes_key.hex()
        aes_key_b64 = base64.b64encode(aes_key_hex.encode("utf-8")).decode()
        return {
            "encrypt_query_param": download_ref,
            "aes_key_b64": aes_key_b64,
            "aes_key_hex": aes_key_hex,
            "file_size": meta["rawsize"],
            "encrypted_size": encrypted_size,
        }

    def send_image(self, to_user_id: str, file_data: bytes, context_token: str = "") -> dict:
        """
        发送图片消息

        参数:
            to_user_id: 目标用户 ID
            file_data: 原始图片字节
            context_token: 对话关联 token
        返回:
            API 响应 dict
        """
        if not self.bot_token:
            raise RuntimeError("未登录，请先扫码")

        # 上传图片到 CDN
        upload_result = self.upload_media(file_data, media_type=UPLOAD_MEDIA_TYPE_IMAGE, to_user_id=to_user_id)

        client_id = f"openclaw-weixin:{int(time.time() * 1000)}-{os.urandom(4).hex()}"
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": client_id,
                "message_type": 2,  # BOT 发出
                "message_state": 2,  # FINISH
                "context_token": context_token,
                "item_list": [
                    {
                        "type": MESSAGE_ITEM_TYPE_IMAGE,
                        "image_item": {
                            "aeskey": upload_result["aes_key_hex"],
                            "media": {
                                "encrypt_query_param": upload_result["encrypt_query_param"],
                                "aes_key": upload_result["aes_key_b64"],
                                "encrypt_type": 1,
                            },
                            "mid_size": upload_result["encrypted_size"],
                        },
                    }
                ],
            },
        }

        data = self._post_json(
            "ilink/bot/sendmessage",
            payload,
            token=self.bot_token,
            timeout=15,
        )
        self._validate_delivery_response(data, "sendImage")
        ret = data.get("ret", 0)

        logger.info("发送图片到 %s: %d bytes (ret=%s)", to_user_id[:20], len(file_data), ret)
        return data

    def send_video(self, to_user_id: str, file_data: bytes, context_token: str = "", play_length: int = 0) -> dict:
        """
        发送视频消息

        参数:
            to_user_id: 目标用户 ID
            file_data: 原始视频字节
            context_token: 对话关联 token
            play_length: 视频时长（秒），未知可传 0
        返回:
            API 响应 dict
        """
        if not self.bot_token:
            raise RuntimeError("未登录，请先扫码")

        upload_result = self.upload_media(file_data, media_type=UPLOAD_MEDIA_TYPE_VIDEO, to_user_id=to_user_id)

        video_item = {
            "media": {
                "encrypt_query_param": upload_result["encrypt_query_param"],
                "aes_key": upload_result["aes_key_b64"],
                "encrypt_type": 1,
            },
            "video_size": upload_result["encrypted_size"],
        }
        if play_length > 0:
            video_item["play_length"] = play_length

        client_id = f"openclaw-weixin:{int(time.time() * 1000)}-{os.urandom(4).hex()}"
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": client_id,
                "message_type": 2,  # BOT 发出
                "message_state": 2,  # FINISH
                "context_token": context_token,
                "item_list": [
                    {
                        "type": MESSAGE_ITEM_TYPE_VIDEO,
                        "video_item": video_item,
                    }
                ],
            },
        }

        data = self._post_json(
            "ilink/bot/sendmessage",
            payload,
            token=self.bot_token,
            timeout=15,
        )
        self._validate_delivery_response(data, "sendVideo")
        ret = data.get("ret", 0)

        logger.info("发送视频到 %s: %d bytes (ret=%s)", to_user_id[:20], len(file_data), ret)
        return data

    def _send_uploaded_voice(
        self,
        to_user_id: str,
        upload_result: dict,
        context_token: str = "",
        *,
        playtime_ms: int = 0,
        text: str = "",
    ) -> dict:
        voice_item = {
            "media": {
                "encrypt_query_param": upload_result["encrypt_query_param"],
                "aes_key": upload_result["aes_key_b64"],
                "encrypt_type": 1,
            },
            "encode_type": 1,
            "bits_per_sample": 16,
            "sample_rate": 8000,
            "playtime": max(0, int(playtime_ms or 0)),
            "text": text or "",
        }

        client_id = f"openclaw-weixin:{int(time.time() * 1000)}-{os.urandom(4).hex()}"
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": client_id,
                "message_type": 2,
                "message_state": 2,
                "context_token": context_token,
                "item_list": [
                    {
                        "type": MESSAGE_ITEM_TYPE_VOICE,
                        "voice_item": voice_item,
                    }
                ],
            },
        }

        data = self._post_json(
            "ilink/bot/sendmessage",
            payload,
            token=self.bot_token,
            timeout=15,
        )
        self._validate_delivery_response(data, "sendVoice")
        ret = data.get("ret", 0)
        logger.info("发送语音到 %s: %d bytes (ret=%s)", to_user_id[:20], upload_result.get("file_size", 0), ret)
        return data

    def send_voice(
        self,
        to_user_id: str,
        file_data: bytes,
        context_token: str = "",
        *,
        playtime_ms: int = 0,
        text: str = "",
    ) -> dict:
        """发送语音消息。"""
        if not self.bot_token:
            raise RuntimeError("未登录，请先扫码")
        upload_result = self.upload_media(file_data, media_type=UPLOAD_MEDIA_TYPE_VOICE, to_user_id=to_user_id)
        return self._send_uploaded_voice(
            to_user_id,
            upload_result,
            context_token,
            playtime_ms=playtime_ms,
            text=text,
        )

    def send_voice_path(
        self,
        to_user_id: str,
        filepath: str,
        context_token: str = "",
        *,
        playtime_ms: int = 0,
        text: str = "",
    ) -> dict:
        """从文件路径发送语音消息。"""
        if not self.bot_token:
            raise RuntimeError("未登录，请先扫码")
        upload_result = self.upload_media_path(filepath, media_type=UPLOAD_MEDIA_TYPE_VOICE, to_user_id=to_user_id)
        return self._send_uploaded_voice(
            to_user_id,
            upload_result,
            context_token,
            playtime_ms=playtime_ms,
            text=text,
        )

    def send_video_path(self, to_user_id: str, filepath: str, context_token: str = "", play_length: int = 0) -> dict:
        """从文件路径发送视频消息。"""
        if not self.bot_token:
            raise RuntimeError("未登录，请先扫码")

        upload_result = self.upload_media_path(filepath, media_type=UPLOAD_MEDIA_TYPE_VIDEO, to_user_id=to_user_id)

        video_item = {
            "media": {
                "encrypt_query_param": upload_result["encrypt_query_param"],
                "aes_key": upload_result["aes_key_b64"],
                "encrypt_type": 1,
            },
            "video_size": upload_result["encrypted_size"],
        }
        if play_length > 0:
            video_item["play_length"] = play_length

        client_id = f"openclaw-weixin:{int(time.time() * 1000)}-{os.urandom(4).hex()}"
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": client_id,
                "message_type": 2,
                "message_state": 2,
                "context_token": context_token,
                "item_list": [
                    {
                        "type": MESSAGE_ITEM_TYPE_VIDEO,
                        "video_item": video_item,
                    }
                ],
            },
        }

        data = self._post_json(
            "ilink/bot/sendmessage",
            payload,
            token=self.bot_token,
            timeout=15,
        )
        self._validate_delivery_response(data, "sendVideo")
        ret = data.get("ret", 0)

        logger.info("发送视频到 %s: %d bytes (ret=%s)", to_user_id[:20], upload_result["file_size"], ret)
        return data

    def send_file(
        self,
        to_user_id: str,
        file_data: bytes,
        context_token: str = "",
        *,
        file_name: str = "file.bin",
        text: str = "",
    ) -> dict:
        """发送文件附件消息。"""
        if not self.bot_token:
            raise RuntimeError("未登录，请先扫码")

        upload_result = self.upload_media(file_data, media_type=UPLOAD_MEDIA_TYPE_FILE, to_user_id=to_user_id)
        return self._send_uploaded_file(
            to_user_id,
            upload_result,
            context_token,
            file_name=file_name,
            text=text,
        )

    def send_file_path(
        self,
        to_user_id: str,
        filepath: str,
        context_token: str = "",
        *,
        file_name: str = "",
        text: str = "",
    ) -> dict:
        """从文件路径发送文件附件消息。"""
        if not self.bot_token:
            raise RuntimeError("未登录，请先扫码")

        path = str(Path(filepath))
        upload_result = self.upload_media_path(path, media_type=UPLOAD_MEDIA_TYPE_FILE, to_user_id=to_user_id)
        return self._send_uploaded_file(
            to_user_id,
            upload_result,
            context_token,
            file_name=file_name or Path(path).name,
            text=text,
        )

    def _send_uploaded_file(
        self,
        to_user_id: str,
        upload_result: dict,
        context_token: str = "",
        *,
        file_name: str = "file.bin",
        text: str = "",
    ) -> dict:
        items = []
        if text:
            items.append({"type": MESSAGE_ITEM_TYPE_TEXT, "text_item": {"text": text}})
        items.append(
            {
                "type": MESSAGE_ITEM_TYPE_FILE,
                "file_item": {
                    "media": {
                        "encrypt_query_param": upload_result["encrypt_query_param"],
                        "aes_key": upload_result["aes_key_b64"],
                        "encrypt_type": 1,
                    },
                    "file_name": file_name or "file.bin",
                    "len": str(upload_result["file_size"]),
                },
            }
        )
        return self._send_items(to_user_id, items, context_token, label="发送文件")

    def send_reference_text(
        self,
        to_user_id: str,
        text: str,
        context_token: str = "",
        *,
        ref_text: str = "",
        ref_title: str = "",
    ) -> dict:
        """发送引用样式文本。

        iLink 接口会接受 ref_msg 字段，但当前微信客户端不会按原生引用渲染；
        因此这里显式降级为普通文本，避免对外承诺不存在的原生 quote 能力。
        """
        if not text:
            raise ValueError("引用消息正文不能为空")
        if not ref_text and not ref_title:
            raise ValueError("引用消息需要 ref_text 或 ref_title")

        fallback_text = format_reference_fallback_text(text, ref_text=ref_text, ref_title=ref_title)
        data = self.send_text(to_user_id, fallback_text, context_token)
        data["native_reference"] = False
        data["fallback"] = "text_quote"
        return data

    def _send_items(
        self, to_user_id: str, items: list[dict], context_token: str = "", *, label: str = "发送消息"
    ) -> dict:
        """发送一组结构化 MessageItem。"""
        if not self.bot_token:
            raise RuntimeError("未登录，请先扫码")

        last_data = {}
        for item in items:
            client_id = f"openclaw-weixin:{int(time.time() * 1000)}-{os.urandom(4).hex()}"
            payload = {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": to_user_id,
                    "client_id": client_id,
                    "message_type": 2,
                    "message_state": 2,
                    "context_token": context_token,
                    "item_list": [item],
                },
            }
            data = self._post_json(
                "ilink/bot/sendmessage",
                payload,
                token=self.bot_token,
                timeout=15,
            )
            self._validate_delivery_response(data, label)
            last_data = data

        logger.info("%s到 %s: items=%d (ret=%s)", label, to_user_id[:20], len(items), last_data.get("ret", 0))
        return last_data
