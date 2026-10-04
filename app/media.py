from __future__ import annotations

"""
媒体文件处理模块
- AES-128-ECB 解密（PKCS7 填充）
- CDN 下载 + 解密 → 本地持久化
- 支持图片/文件/视频/语音（当前仅实现图片）
"""

import base64
import hashlib
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from contextvars import ContextVar

import requests
from Crypto.Cipher import AES

logger = logging.getLogger(__name__)

# 媒体文件存储根目录（Docker volume 挂载 /data）
MEDIA_DIR = os.environ.get("MEDIA_DIR", "./data/media")
CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
_current_media_dir: ContextVar[str | None] = ContextVar("wechat_bridge_media_dir", default=None)
SILK_MAGIC = b"#!SILK_V3"


def _get_media_dir(media_dir: str | None = None) -> str:
    return media_dir or _current_media_dir.get() or MEDIA_DIR


@contextmanager
def use_media_dir(media_dir: str):
    token = _current_media_dir.set(media_dir)
    try:
        yield
    finally:
        _current_media_dir.reset(token)


def set_media_dir(new_dir: str):
    """切换媒体存储目录（用于多账号隔离）"""
    global MEDIA_DIR
    MEDIA_DIR = new_dir
    logger.info("媒体目录切换为: %s", MEDIA_DIR)


def _ensure_media_dir(media_dir: str | None = None):
    """确保媒体存储目录存在"""
    os.makedirs(_get_media_dir(media_dir), exist_ok=True)


def _unpad_pkcs7(data: bytes) -> bytes:
    """移除 PKCS7 填充"""
    if not data:
        raise ValueError("空数据无法去除 PKCS7 填充")
    pad_len = data[-1]
    if pad_len < 1 or pad_len > 16:
        raise ValueError(f"无效的 PKCS7 填充值: {pad_len}")
    # 验证所有填充字节
    if data[-pad_len:] != bytes([pad_len]) * pad_len:
        raise ValueError("PKCS7 填充验证失败")
    return data[:-pad_len]


def decrypt_aes_ecb(ciphertext: bytes, aes_key: bytes) -> bytes:
    """
    AES-128-ECB 解密 + PKCS7 去填充

    参数:
        ciphertext: 加密的二进制数据
        aes_key: 16 字节 AES 密钥（原始字节）
    返回:
        解密后的原始文件字节
    """
    if len(aes_key) != 16:
        raise ValueError(f"AES-128 密钥长度必须为 16 字节，实际: {len(aes_key)}")

    cipher = AES.new(aes_key, AES.MODE_ECB)
    padded = cipher.decrypt(ciphertext)
    return _unpad_pkcs7(padded)


def encrypt_aes_ecb(plaintext: bytes, aes_key: bytes) -> bytes:
    """
    AES-128-ECB 加密 + PKCS7 填充（用于上传媒体文件到 CDN）

    参数:
        plaintext: 原始文件字节
        aes_key: 16 字节 AES 密钥（原始字节）
    返回:
        加密后的二进制数据
    """
    if len(aes_key) != 16:
        raise ValueError(f"AES-128 密钥长度必须为 16 字节，实际: {len(aes_key)}")

    # PKCS7 填充
    pad_len = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([pad_len]) * pad_len

    cipher = AES.new(aes_key, AES.MODE_ECB)
    return cipher.encrypt(padded)


def encrypted_size_for_plain_size(size: int) -> int:
    """返回 PKCS7 填充后的 AES-ECB 加密文件大小。"""
    pad_len = 16 - (size % 16)
    return size + pad_len


def inspect_media_file(filepath: str, *, chunk_size: int = 1024 * 1024) -> dict:
    """流式读取文件，返回上传所需的大小、MD5 和前 1024 字节。"""
    rawfilemd5 = hashlib.md5()
    first1024 = bytearray()
    rawsize = 0
    with open(filepath, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            rawsize += len(chunk)
            rawfilemd5.update(chunk)
            if len(first1024) < 1024:
                first1024.extend(chunk[: 1024 - len(first1024)])
    return {
        "rawsize": rawsize,
        "rawfilemd5": rawfilemd5.hexdigest(),
        "first1024": bytes(first1024),
        "encrypted_size": encrypted_size_for_plain_size(rawsize),
    }


def encrypt_aes_ecb_file(src_path: str, dst_path: str, aes_key: bytes, *, chunk_size: int = 1024 * 1024) -> int:
    """将文件流式 AES-128-ECB 加密到目标路径，返回加密后大小。"""
    if len(aes_key) != 16:
        raise ValueError(f"AES-128 密钥长度必须为 16 字节，实际: {len(aes_key)}")

    cipher = AES.new(aes_key, AES.MODE_ECB)
    pending = b""
    written = 0
    with open(src_path, "rb") as src, open(dst_path, "wb") as dst:
        while True:
            chunk = src.read(chunk_size)
            if not chunk:
                break
            pending += chunk
            aligned_len = (len(pending) // 16) * 16
            if aligned_len:
                encrypted = cipher.encrypt(pending[:aligned_len])
                dst.write(encrypted)
                written += len(encrypted)
                pending = pending[aligned_len:]

        pad_len = 16 - (len(pending) % 16)
        encrypted = cipher.encrypt(pending + bytes([pad_len]) * pad_len)
        dst.write(encrypted)
        written += len(encrypted)
    return written


def create_encrypted_upload_file(src_path: str, aes_key: bytes, *, temp_dir: str | None = None) -> tuple[str, int]:
    """创建用于 CDN 上传的临时加密文件，调用方负责删除。"""
    directory = temp_dir or os.path.dirname(os.path.abspath(src_path)) or None
    fd, tmp_path = tempfile.mkstemp(prefix=".wb_upload_", suffix=".enc", dir=directory)
    os.close(fd)
    try:
        encrypted_size = encrypt_aes_ecb_file(src_path, tmp_path, aes_key)
        return tmp_path, encrypted_size
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def cleanup_expired_media_files(
    media_dir: str,
    *,
    retention_hours: int,
    prefixes: tuple[str, ...] = ("out_video_",),
    now_ts: int | None = None,
) -> dict:
    """删除超过保留时间的指定前缀媒体文件。"""
    if retention_hours <= 0:
        return {"ok": True, "skipped": True, "deleted": 0, "retention_hours": retention_hours}
    if not os.path.isdir(media_dir):
        return {"ok": True, "deleted": 0, "retention_hours": retention_hours}

    now_ts = now_ts or int(time.time())
    cutoff = now_ts - retention_hours * 3600
    deleted = []
    errors = []
    for entry in os.scandir(media_dir):
        if not entry.is_file():
            continue
        if not entry.name.startswith(prefixes):
            continue
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
            os.unlink(entry.path)
            deleted.append(entry.name)
        except OSError as exc:
            errors.append({"file": entry.name, "error": str(exc)})

    if deleted:
        logger.info("过期媒体文件清理完成: deleted=%d, retention_hours=%d", len(deleted), retention_hours)
    return {
        "ok": not errors,
        "deleted": len(deleted),
        "files": deleted,
        "errors": errors,
        "retention_hours": retention_hours,
    }


def _decode_aes_key(aes_key_b64: str, media_type: str = "image") -> bytes:
    """
    解码 aes_key（base64 编码）

    - 图片: base64(raw 16 bytes) → 直接 base64 解码得到 16 字节密钥
    - 文件/语音/视频: base64(hex string of 16 bytes) → 先 base64 解码得到 hex 字符串，再 bytes.fromhex
    """
    raw = base64.b64decode(aes_key_b64)

    if media_type == "image":
        # 图片：raw 就是 16 字节密钥
        if len(raw) == 16:
            return raw
        # 兜底：可能也是 hex 编码
        try:
            key = bytes.fromhex(raw.decode("ascii"))
            if len(key) == 16:
                logger.info("图片 aes_key 使用 hex 编码格式")
                return key
        except (ValueError, UnicodeDecodeError):
            pass
        raise ValueError(f"图片 aes_key 解码后长度异常: {len(raw)} 字节")
    else:
        # 文件/语音/视频：raw 是 hex 字符串
        try:
            key = bytes.fromhex(raw.decode("ascii"))
            if len(key) == 16:
                return key
        except (ValueError, UnicodeDecodeError):
            pass
        # 兜底：可能直接是 16 字节
        if len(raw) == 16:
            logger.info("非图片 aes_key 使用 raw 格式")
            return raw
        raise ValueError(f"非图片 aes_key 解码失败: raw_len={len(raw)}")


def download_and_decrypt_image(
    encrypted_query_param: str,
    aes_key_b64: str,
    msg_id: str = "",
    timeout: int = 30,
    media_dir: str | None = None,
) -> str | None:
    """从微信 CDN 下载加密图片并解密保存到本地（download_and_decrypt_media 的便捷别名）"""
    return download_and_decrypt_media(
        encrypted_query_param=encrypted_query_param,
        aes_key_b64=aes_key_b64,
        msg_id=msg_id,
        media_type="image",
        timeout=timeout,
        media_dir=media_dir,
    )


def download_and_decrypt_media(
    encrypted_query_param: str,
    aes_key_b64: str,
    msg_id: str = "",
    media_type: str = "video",
    timeout: int = 60,
    media_dir: str | None = None,
    max_bytes: int | None = None,
) -> str | None:
    """
    通用媒体文件下载解密（图片/视频/文件/语音等）

    流程：CDN 下载 → AES-128-ECB 解密 → 本地保存
    """
    target_media_dir = _get_media_dir(media_dir)
    _ensure_media_dir(target_media_dir)

    cdn_url = f"{CDN_BASE_URL}/download?encrypted_query_param={encrypted_query_param}"
    logger.info("开始下载 CDN %s: msg_id=%s, url=%s", media_type, msg_id, cdn_url[:120])

    try:
        resp = requests.get(cdn_url, timeout=timeout, stream=True)
        resp.raise_for_status()
        encrypted_limit = encrypted_size_for_plain_size(max_bytes) if max_bytes is not None else None
        try:
            declared_size = int(resp.headers.get("Content-Length") or "0")
        except (TypeError, ValueError):
            declared_size = 0
        if encrypted_limit is not None and declared_size > encrypted_limit:
            raise ValueError(f"CDN {media_type} 超过大小上限: {declared_size} > {encrypted_limit}")

        encrypted_buffer = bytearray()
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            encrypted_buffer.extend(chunk)
            if encrypted_limit is not None and len(encrypted_buffer) > encrypted_limit:
                raise ValueError(f"CDN {media_type} 超过大小上限: {len(encrypted_buffer)} > {encrypted_limit}")
        encrypted_data = bytes(encrypted_buffer)
        logger.info(
            "CDN %s 下载完成: %d bytes (%.1f MB)", media_type, len(encrypted_data), len(encrypted_data) / 1048576
        )

        if len(encrypted_data) < 16:
            logger.error("CDN 返回数据过短: %d bytes，可能是错误响应", len(encrypted_data))
            return None

        # 解码密钥
        aes_key = _decode_aes_key(aes_key_b64, media_type=media_type)

        # AES-128-ECB 解密
        decrypted = decrypt_aes_ecb(encrypted_data, aes_key)
        if max_bytes is not None and len(decrypted) > max_bytes:
            raise ValueError(f"解密后 {media_type} 超过大小上限: {len(decrypted)} > {max_bytes}")
        logger.info("%s 解密成功: %d bytes (%.1f MB)", media_type, len(decrypted), len(decrypted) / 1048576)

        # 检测格式
        ext = _detect_media_format(decrypted, media_type)

        # 保存
        ts = int(time.time())
        safe_id = hashlib.md5(msg_id.encode()).hexdigest()[:12] if msg_id else f"{ts}"
        filename = f"{ts}_{safe_id}.{ext}"
        filepath = os.path.join(target_media_dir, filename)

        with open(filepath, "wb") as f:
            f.write(decrypted)

        logger.info("%s 已保存: %s (%d bytes)", media_type, filepath, len(decrypted))
        return filepath

    except requests.exceptions.RequestException as e:
        logger.error("CDN %s 下载失败: %s", media_type, e)
        return None
    except ValueError as e:
        logger.error("%s 解密失败: %s", media_type, e)
        return None
    except Exception as e:
        logger.error("%s 处理异常: %s", media_type, e, exc_info=True)
        return None


def _detect_image_format(data: bytes) -> str:
    """通过文件头魔数检测图片格式"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    elif data[:3] == b"\xff\xd8\xff":
        return "jpg"
    elif data[:4] == b"GIF8":
        return "gif"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    elif data[:4] == b"\x00\x00\x00\x1c" or data[:4] == b"\x00\x00\x00\x18":
        return "heic"
    else:
        # 默认按 jpg 处理（微信最常见格式）
        logger.info("无法识别图片格式 (magic: %s)，默认使用 jpg", data[:4].hex())
        return "jpg"


def _detect_media_format(data: bytes, media_type: str = "video") -> str:
    """通过文件头魔数检测媒体格式（优先尝试图片格式）"""
    # 先尝试图片格式检测
    if media_type == "image":
        return _detect_image_format(data)

    # 非图片类型也先检测是否为图片（兜底）
    img_check = data[:8]
    if img_check[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if img_check[:3] == b"\xff\xd8\xff":
        return "jpg"
    if img_check[:4] == b"GIF8":
        return "gif"
    if img_check[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"

    # 视频格式
    if data[4:8] == b"ftyp":  # MP4/MOV/3GP 容器
        sub = data[8:12]
        if sub in (b"isom", b"iso2", b"mp41", b"mp42", b"avc1", b"M4V "):
            return "mp4"
        elif sub in (b"qt  ", b"M4VH", b"M4VP"):
            return "mov"
        elif sub in (b"3gp4", b"3gp5", b"3ge6", b"3ge7"):
            return "3gp"
        return "mp4"  # ftyp 系默认 mp4
    if data[:4] == b"\x1a\x45\xdf\xa3":  # WebM/MKV (EBML)
        return "webm"
    if data[:4] == b"RIFF" and data[8:12] == b"AVI ":
        return "avi"
    if data[:3] == b"\x00\x00\x01":  # MPEG-TS / MPEG-PS
        return "ts"
    if data[:4] == b"FLV\x01":
        return "flv"

    # 音频格式
    if is_silk(data):
        return "silk"
    if data[:4] == b"#!AM":  # AMR
        return "amr"
    if data[:4] == b"fLaC":  # FLAC
        return "flac"
    if data[:3] == b"ID3" or (data[0:2] == b"\xff\xfb"):  # MP3
        return "mp3"

    # 默认
    default = {"video": "mp4", "voice": "amr", "file": "bin"}
    ext = default.get(media_type, "bin")
    logger.info("无法识别 %s 格式 (magic: %s)，默认使用 %s", media_type, data[:8].hex(), ext)
    return ext


def is_silk(data: bytes) -> bool:
    """判断是否为微信语音可直接发送的 SILK v3 数据。"""
    return data.startswith(SILK_MAGIC) or data.startswith(b"\x02" + SILK_MAGIC)


def get_media_path(filename: str, media_dir: str | None = None) -> str | None:
    """获取媒体文件完整路径（带安全校验）"""
    # 防路径穿越
    safe_name = os.path.basename(filename)
    filepath = os.path.join(_get_media_dir(media_dir), safe_name)
    if os.path.isfile(filepath):
        return filepath
    return None


def extract_pic_info(image_item: dict) -> dict | None:
    """
    从 image_item 中提取图片下载所需参数

    实际 iLink API 返回结构:
    {
        "aeskey": "e54e9a8a...",           # 顶层 hex 格式密钥(无下划线)
        "media": {
            "encrypt_query_param": "...",   # CDN 查询参数
            "aes_key": "base64...",         # base64 编码的密钥
            "full_url": "https://novac2c.cdn.weixin.qq.com/c2c/download?..."
        },
        "mid_size": 462924,
        "thumb_height": 210,
        "thumb_width": 95,
        "hd_size": 462924
    }

    返回 {"encrypted_query_param": ..., "aes_key": ..., ...} 或 None
    """
    if not image_item:
        return None

    # media 子对象（核心数据在这里）
    media_obj = image_item.get("media") or {}

    # 提取 encrypted_query_param
    eqp = (
        media_obj.get("encrypt_query_param")
        or media_obj.get("encrypted_query_param")
        or image_item.get("encrypt_query_param")
        or image_item.get("encrypted_query_param")
    )

    # 提取 aes_key（优先用 media 子对象里的 base64 格式）
    aes_key = (
        media_obj.get("aes_key")
        or image_item.get("aes_key")
        or image_item.get("aesKey")
        or image_item.get("aes_key_base64")
    )

    # 兜底：如果没有 base64 格式 key，尝试从顶层 aeskey (hex) 构造
    if not aes_key:
        hex_key = image_item.get("aeskey") or image_item.get("aes_key_hex")
        if hex_key:
            # 将 hex 字符串编码为 base64（与 _decode_aes_key 兼容）
            import base64

            aes_key = base64.b64encode(hex_key.encode("ascii")).decode("ascii")
            logger.info("使用顶层 hex aeskey 构造 base64 key")

    # 如果还有 full_url 且没有 eqp，尝试从 full_url 提取
    if not eqp:
        full_url = media_obj.get("full_url") or image_item.get("full_url") or ""
        if "encrypted_query_param=" in full_url:
            from urllib.parse import parse_qs, urlparse

            parsed = urlparse(full_url)
            qs = parse_qs(parsed.query)
            eqp = qs.get("encrypted_query_param", [""])[0]
            if eqp:
                logger.info("从 full_url 中提取到 encrypt_query_param")

    if not eqp or not aes_key:
        logger.warning(
            "image_item 缺少必要字段: has_eqp=%s, has_aes_key=%s, image_keys=%s, media_keys=%s",
            bool(eqp),
            bool(aes_key),
            list(image_item.keys()),
            list(media_obj.keys()),
        )
        return None

    return {
        "encrypted_query_param": eqp,
        "aes_key": aes_key,
        "width": image_item.get("thumb_width", 0),
        "height": image_item.get("thumb_height", 0),
        "file_size": image_item.get("hd_size") or image_item.get("mid_size", 0),
    }
