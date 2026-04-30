import secrets
from dataclasses import dataclass, field

from bridge import WeChatBridge
from ilink import ILinkClient


@dataclass
class QRCacheState:
    """缓存二维码数据，避免频繁刷新。"""

    data: dict | None = None
    updated_at: float = 0.0


@dataclass
class WebAppContext:
    """Web 层运行时上下文。"""

    client: ILinkClient
    bridge: WeChatBridge
    api_token: str = ""
    session_secret: str = field(default_factory=lambda: secrets.token_hex(16))
    qr_cache: QRCacheState = field(default_factory=QRCacheState)
