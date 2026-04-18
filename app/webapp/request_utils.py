"""请求体解析辅助函数。"""


def parse_multipart(body: bytes, content_type: str, logger=None) -> tuple[str, bytes | None]:
    """
    解析 multipart/form-data 请求体。
    返回: (to, image_data)
    """
    to = ""
    image_data = None

    try:
        boundary = ""
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part[len("boundary="):]
                break

        if not boundary:
            return to, image_data

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
            header_text = header_section.decode("utf-8", errors="ignore").lower()
            if 'name="to"' in header_text:
                to = content.decode("utf-8", errors="ignore").strip()
            elif 'name="image"' in header_text or 'name="file"' in header_text:
                image_data = content

    except Exception as exc:
        if logger:
            logger.warning("解析 multipart 失败: %s", exc)

    return to, image_data

