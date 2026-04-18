"""Web 鉴权与会话辅助函数。"""

import hashlib


def make_session_cookie(token: str, session_secret: str) -> str:
    """根据用户输入的 token 生成会话签名。"""
    return hashlib.sha256(f"{token}:{session_secret}".encode()).hexdigest()[:32]


def check_web_session(handler, api_token: str, session_secret: str) -> bool:
    """检查浏览器请求是否带有合法的 Web 会话 cookie。"""
    if not api_token:
        return True

    cookie_header = handler.headers.get("Cookie", "")
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith("wb_session="):
            session_val = part[len("wb_session="):]
            return session_val == make_session_cookie(api_token, session_secret)
    return False

