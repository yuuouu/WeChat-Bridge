"""
iLink Bot API 封装
纯 HTTP/JSON 调用腾讯 iLink 服务，无需 OpenClaw CLI。
"""

import os
import json
import time
import struct
import base64
import logging
import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://ilinkai.weixin.qq.com"
CHANNEL_VERSION = "1.0.2"
TOKEN_FILE = os.environ.get("TOKEN_FILE", "./data/token.json")


def _random_uin() -> str:
    """生成随机 X-WECHAT-UIN（uint32 → 十进制字符串 → base64）"""
    rand_bytes = os.urandom(4)
    rand_uint32 = struct.unpack("<I", rand_bytes)[0]
    return base64.b64encode(str(rand_uint32).encode()).decode()


def _headers(bot_token: str = None) -> dict:
    """构造 iLink 标准请求头"""
    h = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _random_uin(),
    }
    if bot_token:
        h["Authorization"] = f"Bearer {bot_token}"
    return h


class ILinkClient:
    """iLink Bot API 客户端"""

    def __init__(self):
        self.bot_token: str | None = None
        self.base_url: str = BASE_URL
        self.bot_id: str | None = None
        self.get_updates_buf: str = ""
        self._session = requests.Session()
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

    # ── Token 持久化 ──

    def _load_token(self):
        """从文件恢复 token"""
        if os.path.exists(TOKEN_FILE):
            try:
                with open(TOKEN_FILE, "r") as f:
                    data = json.load(f)
                self.bot_token = data.get("bot_token")
                self.base_url = data.get("base_url", BASE_URL)
                self.bot_id = data.get("bot_id") or self._extract_bot_id(self.bot_token)
                self.get_updates_buf = data.get("get_updates_buf", "")
                if self.bot_token:
                    logger.info("已从文件恢复登录态: bot_id=%s", self.bot_id)
            except Exception as e:
                logger.warning("读取 token 文件失败: %s", e)

    def _save_token(self):
        """持久化 token 到文件"""
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        with open(TOKEN_FILE, "w") as f:
            json.dump({
                "bot_token": self.bot_token,
                "base_url": self.base_url,
                "bot_id": self.bot_id,
                "get_updates_buf": self.get_updates_buf,
            }, f, indent=2)

    def clear_token(self):
        """清除登录态"""
        self.bot_token = None
        self.bot_id = None
        self.get_updates_buf = ""
        if os.path.exists(TOKEN_FILE):
            os.remove(TOKEN_FILE)
        logger.info("登录态已清除")

    # ── 登录流程 ──

    def get_qrcode(self) -> dict:
        """
        获取登录二维码
        返回: {"qrcode": "xxx", "qrcode_img_content": "base64图片数据", "url": "扫码链接"}
        """
        resp = self._session.get(
            f"{BASE_URL}/ilink/bot/get_bot_qrcode",
            params={"bot_type": "3"},
            headers=_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info("获取二维码成功: qrcode=%s", data.get("qrcode", "")[:20])
        return data

    def poll_qrcode_status(self, qrcode: str) -> dict:
        """
        轮询扫码状态
        返回: {"status": "waiting|scanned|confirmed|expired", "bot_token": "...", "baseurl": "..."}
        """
        resp = self._session.get(
            f"{BASE_URL}/ilink/bot/get_qrcode_status",
            params={"qrcode": qrcode},
            headers=_headers(),
            timeout=60,  # 长轮询可能 hold 较久
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") == "confirmed":
            self.bot_token = data["bot_token"]
            self.base_url = data.get("baseurl", BASE_URL)
            self.bot_id = data.get("bot_id") or self._extract_bot_id(self.bot_token)
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
            resp = self._session.post(
                f"{self.base_url}/ilink/bot/getupdates",
                headers=_headers(self.bot_token),
                json={
                    "get_updates_buf": self.get_updates_buf,
                    "base_info": {"channel_version": CHANNEL_VERSION},
                },
                timeout=timeout + 10,  # 比服务器 hold 时间多留一点
            )
            resp.raise_for_status()
            data = resp.json()

            ret = data.get("ret", 0)
            errcode = data.get("errcode", 0)
            if ret != 0 or errcode != 0:
                logger.warning("getupdates 返回异常数据: %s", json.dumps(data, ensure_ascii=False))
                # 如果真的是凭证过期
                if ret in (-1, 401, 403) or errcode in (401, 403, "TokenExpired"):
                    logger.error("Token 可能已过期，需重新扫码登录")
                    self.clear_token()
                return []

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
                "message_type": 2,       # BOT 发出
                "message_state": 2,      # FINISH（完整消息）
                "context_token": context_token,
                "item_list": [
                    {"type": 1, "text_item": {"text": text}}
                ],
            },
            "base_info": {"channel_version": CHANNEL_VERSION}
        }

        resp = self._session.post(
            f"{self.base_url}/ilink/bot/sendmessage",
            headers=_headers(self.bot_token),
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        ret = data.get("ret", 0)
        errcode = data.get("errcode", 0)
        if ret != 0 or errcode != 0:
            logger.error("发送消息失败: %s", json.dumps(data, ensure_ascii=False))
            if ret == -2:
                raise RuntimeError("API限制(ret=-2)：距离该用户最后一次发消息可能已超24小时，无法主动下发。请在微信上让对方先发一条消息。")
            raise RuntimeError(f"API Error: ret={ret}, errcode={errcode}, errmsg={data.get('errmsg')}")
            
        logger.info("发送消息到 %s: %s (ret=%s)", to_user_id[:20], text[:50], ret)
        return data

    def send_typing(self, to_user_id: str, context_token: str = "") -> dict:
        """发送"正在输入"状态"""
        if not self.bot_token:
            raise RuntimeError("未登录")

        # 先获取 typing_ticket
        config_payload = {
            "ilink_user_id": to_user_id,
            "context_token": context_token,
            "base_info": {"channel_version": "1.0.0"}
        }
        config_resp = self._session.post(
            f"{self.base_url}/ilink/bot/getconfig",
            headers=_headers(self.bot_token),
            json=config_payload,
            timeout=10,
        )
        config_data = config_resp.json()
        typing_ticket = config_data.get("typing_ticket", "")

        payload = {
            "ilink_user_id": to_user_id,
            "typing_ticket": typing_ticket,
            "status": 1,
            "base_info": {"channel_version": "1.0.0"}
        }

        resp = self._session.post(
            f"{self.base_url}/ilink/bot/sendtyping",
            headers=_headers(self.bot_token),
            json=payload,
            timeout=10,
        )
        data = resp.json()
        ret = data.get("ret", 0)
        errcode = data.get("errcode", 0)
        
        if ret != 0 or errcode != 0:
            if ret == -2:
                raise RuntimeError("API限制(ret=-2)：距离该用户最后一次发消息可能已超24小时，无法发送状态。")
            raise RuntimeError(f"API Error: ret={ret}, errcode={errcode}")
            
        return data

    # ── 媒体上传 ──

    def upload_media(self, file_data: bytes, media_type: int = 1, to_user_id: str = "") -> dict:
        """
        上传媒体文件到腾讯 CDN

        流程: 生成 AES key → 加密文件 → 获取上传 URL → 上传到 CDN → 返回下载凭证
        
        参数:
            file_data: 原始文件字节
            media_type: 1=图片, 2=视频, 3=语音, 4=文件
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
            "base_info": {"channel_version": CHANNEL_VERSION},
            "to_user_id": to_user_id
        }
        logger.info("getuploadurl req: %s", json.dumps(upload_req))
        resp = self._session.post(
            f"{self.base_url}/ilink/bot/getuploadurl",
            headers=_headers(self.bot_token),
            json=upload_req,
            timeout=15,
        )
        resp.raise_for_status()
        upload_data = resp.json()

        ret = upload_data.get("ret", 0)
        if ret != 0:
            raise RuntimeError(f"获取上传 URL 失败: {json.dumps(upload_data, ensure_ascii=False)}")

        upload_param = upload_data.get("upload_param", "")
        if not upload_param:
            raise RuntimeError(f"上传参数为空: {json.dumps(upload_data, ensure_ascii=False)}")

        # 5. 上传加密文件到 CDN
        cdn_upload_url = upload_data.get("upload_full_url", "")
        if not cdn_upload_url:
            import urllib.parse
            cdn_upload_url = f"https://novac2c.cdn.weixin.qq.com/c2c/upload?encrypted_query_param={urllib.parse.quote(upload_param)}&filekey={urllib.parse.quote(filekey)}"

        upload_resp = self._session.post(
            cdn_upload_url,
            headers={
                "Content-Type": "application/octet-stream",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            },
            data=encrypted_data,
            timeout=60,
        )
        upload_resp.raise_for_status()

        # 6. 从响应头提取下载凭证
        download_ref = upload_resp.headers.get("X-Encrypted-Param") or upload_resp.headers.get("x-encrypted-param")

        if not download_ref:
            logger.warning("CDN 上传成功但似乎没有返回 x-encrypted-param，使用 upload_param 可能会导致客户端无法下载。Headers: %s", upload_resp.headers)
            download_ref = upload_param

        logger.info("媒体上传成功: filekey=%s, size=%d, encrypted_size=%d",
                     filekey, len(file_data), len(encrypted_data))

        # WeChat 客户端可能期望 AES_Key 是 hex 字符串的 Base64 编码，参照 openclaw-weixin
        aes_key_hex = aes_key.hex()
        aes_key_b64 = base64.b64encode(aes_key_hex.encode('utf-8')).decode()

        return {
            "encrypt_query_param": download_ref,
            "aes_key_b64": aes_key_b64,
            "aes_key_hex": aes_key_hex,
            "file_size": len(file_data),
            "encrypted_size": len(encrypted_data),
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
        upload_result = self.upload_media(file_data, media_type=1, to_user_id=to_user_id)

        client_id = f"openclaw-weixin:{int(time.time() * 1000)}-{os.urandom(4).hex()}"
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": client_id,
                "message_type": 2,       # BOT 发出
                "message_state": 2,      # FINISH
                "context_token": context_token,
                "item_list": [
                    {
                        "type": 2,
                        "image_item": {
                            "aeskey": upload_result["aes_key_hex"],
                            "media": {
                                "encrypt_query_param": upload_result["encrypt_query_param"],
                                "aes_key": upload_result["aes_key_b64"],
                                "encrypt_type": 1,
                            },
                            "mid_size": upload_result["encrypted_size"],
                        }
                    }
                ],
            },
            "base_info": {"channel_version": CHANNEL_VERSION}
        }

        resp = self._session.post(
            f"{self.base_url}/ilink/bot/sendmessage",
            headers=_headers(self.bot_token),
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        ret = data.get("ret", 0)
        errcode = data.get("errcode", 0)
        if ret != 0 or errcode != 0:
            logger.error("发送图片失败: %s", json.dumps(data, ensure_ascii=False))
            if ret == -2:
                raise RuntimeError("API限制(ret=-2)：距离该用户最后一次发消息可能已超24小时，无法主动下发。")
            raise RuntimeError(f"API Error: ret={ret}, errcode={errcode}, errmsg={data.get('errmsg')}")

        logger.info("发送图片到 %s: %d bytes (ret=%s)", to_user_id[:20], len(file_data), ret)
        return data
