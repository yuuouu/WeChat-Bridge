from __future__ import annotations

"""请求体解析辅助函数。"""

import re
import urllib.parse


def _multipart_header_param(header_text: str, name: str) -> str:
    star_match = re.search(rf'{name}\*\s*=\s*([^;\r\n]+)', header_text, flags=re.IGNORECASE)
    if star_match:
        value = star_match.group(1).strip().strip('"')
        if "''" in value:
            value = value.split("''", 1)[1]
        return urllib.parse.unquote(value)

    match = re.search(rf'{name}\s*=\s*"([^"]*)"', header_text, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(rf"{name}\s*=\s*([^;\r\n]+)", header_text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def parse_multipart_form(body: bytes, content_type: str, logger=None) -> dict:
    """解析 multipart/form-data，返回字段和文件。"""
    result = {"fields": {}, "files": {}}

    try:
        boundary = ""
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part[len("boundary=") :].strip('"')
                break

        if not boundary:
            return result

        boundary_bytes = boundary.encode()
        parts = body.split(b"--" + boundary_bytes)

        for part in parts:
            if not part or part.strip() in (b"--", b""):
                continue

            if b"\r\n\r\n" in part:
                header_section, content = part.split(b"\r\n\r\n", 1)
            elif b"\n\n" in part:
                header_section, content = part.split(b"\n\n", 1)
            else:
                continue

            content = content.rstrip(b"\r\n")
            header_text = header_section.decode("utf-8", errors="ignore")
            field_name = _multipart_header_param(header_text, "name")
            if not field_name:
                continue

            filename = _multipart_header_param(header_text, "filename")
            if filename:
                content_type_match = re.search(r"content-type\s*:\s*([^\r\n]+)", header_text, flags=re.IGNORECASE)
                result["files"][field_name] = {
                    "filename": filename,
                    "content": content,
                    "content_type": content_type_match.group(1).strip() if content_type_match else "",
                }
            else:
                result["fields"][field_name] = content.decode("utf-8", errors="ignore").strip()

    except Exception as exc:
        if logger:
            logger.warning("解析 multipart 失败: %s", exc)

    return result


def parse_multipart(body: bytes, content_type: str, logger=None) -> tuple[str, bytes | None]:
    """
    解析 multipart/form-data 请求体。
    返回: (to, image_data)
    """
    parsed = parse_multipart_form(body, content_type, logger)
    file_data = None
    for field_name in ("image", "video", "voice", "audio", "file"):
        file_part = parsed["files"].get(field_name)
        if file_part:
            file_data = file_part["content"]
            break
    return parsed["fields"].get("to", ""), file_data
