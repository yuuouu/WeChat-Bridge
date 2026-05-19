from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bridge import WeChatBridge
from ilink import ILinkClient

if TYPE_CHECKING:
    from accounts import AccountManager, AccountRuntime


@dataclass
class QRCacheState:
    """缓存二维码数据，避免频繁刷新。"""

    data: dict | None = None
    updated_at: float = 0.0


@dataclass
class WebAppContext:
    """Web 层运行时上下文。"""

    client: ILinkClient | None = None
    bridge: WeChatBridge | None = None
    account_manager: AccountManager | None = None
    api_token: str = ""
    session_secret: str = field(default_factory=lambda: secrets.token_hex(16))
    qr_cache: QRCacheState = field(default_factory=QRCacheState)

    def resolve_runtime(self, bot_id: str | None = None) -> AccountRuntime | None:
        if self.account_manager is not None:
            return self.account_manager.get_runtime(bot_id)
        if bot_id:
            current_bot_id = self.client.get_bot_id() if self.client else None
            if bot_id != current_bot_id:
                return None
        if self.client is None or self.bridge is None:
            return None
        from accounts import AccountRuntime

        return AccountRuntime(
            bot_id=self.client.get_bot_id() or "",
            client=self.client,
            bridge=self.bridge,
            data_dir=getattr(self.bridge, "_data_dir", ""),
        )

    def has_accounts(self) -> bool:
        if self.account_manager is not None:
            return self.account_manager.has_accounts()
        return bool(self.client and self.client.logged_in)

    def any_logged_in(self) -> bool:
        if self.account_manager is not None:
            return self.account_manager.any_logged_in()
        return bool(self.client and self.client.logged_in)
