from __future__ import annotations

"""多账号运行时管理。"""

import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import db
from bridge import WeChatBridge
from ilink import ILinkClient

logger = logging.getLogger(__name__)


def _safe_account_dir_name(bot_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.@-]+", "_", bot_id or "").strip("._/")
    return safe or "unknown"


@dataclass
class AccountRuntime:
    bot_id: str
    client: ILinkClient
    bridge: WeChatBridge
    data_dir: str

    def status(self) -> dict:
        payload = self.bridge.get_runtime_status()
        payload["bot_id"] = self.bot_id
        payload["data_dir"] = self.data_dir
        payload["token_mtime"] = self.client.get_token_mtime()
        return payload


@dataclass
class LoginSession:
    login_id: str
    client: ILinkClient
    qrcode: str
    qrcode_data: dict
    created_at: float


class AccountManager:
    """管理多个 iLink Bot 账号的客户端、Bridge 和扫码登录会话。"""

    def __init__(
        self,
        data_base: str,
        *,
        ai_manager=None,
        client_factory=ILinkClient,
        bridge_factory=WeChatBridge,
    ):
        self.data_base = data_base
        self.ai_manager = ai_manager
        self.client_factory = client_factory
        self.bridge_factory = bridge_factory
        self._runtimes: dict[str, AccountRuntime] = {}
        self._login_sessions: dict[str, LoginSession] = {}
        db.init_accounts_db(data_base)

    @property
    def runtimes(self) -> dict[str, AccountRuntime]:
        return dict(self._runtimes)

    def _account_dir(self, bot_id: str) -> str:
        return os.path.join(self.data_base, _safe_account_dir_name(bot_id))

    def _token_file_for(self, bot_id: str) -> str:
        return os.path.join(self._account_dir(bot_id), "token.json")

    def _migrate_legacy_token(self):
        legacy = Path(self.data_base) / "token.json"
        if not legacy.exists():
            return
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
            bot_token = data.get("bot_token")
            bot_id = data.get("bot_id") or ILinkClient._extract_bot_id(bot_token)
            if not bot_id:
                logger.warning("旧 token.json 缺少 bot_id，跳过多账号迁移")
                return
            target = Path(self._token_file_for(bot_id))
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                os.replace(str(legacy), str(target))
                logger.info("旧 token.json 已迁移到账号目录: %s", target)
        except Exception as exc:
            logger.warning("迁移旧 token.json 失败: %s", exc)

    def restore_accounts(self) -> list[AccountRuntime]:
        """从 DATA_DIR/*/token.json 恢复所有已登录账号并启动轮询。"""
        self._migrate_legacy_token()
        restored: list[AccountRuntime] = []
        for token_path in sorted(Path(self.data_base).glob("*/token.json")):
            try:
                try:
                    client = self.client_factory(token_file=str(token_path))
                except TypeError:
                    client = self.client_factory()
                if not client.logged_in or not client.get_bot_id():
                    continue
                runtime = self._start_runtime(client)
                restored.append(runtime)
            except Exception as exc:
                logger.warning("恢复账号失败 [%s]: %s", token_path, exc)
        self._ensure_default_account()
        return restored

    def _start_runtime(self, client: ILinkClient) -> AccountRuntime:
        bot_id = client.get_bot_id()
        if not bot_id:
            raise RuntimeError("无法启动账号：缺少 bot_id")

        old = self._runtimes.get(bot_id)
        if old:
            old.bridge.stop()

        try:
            bridge = self.bridge_factory(client, data_base=self.data_base)
        except TypeError:
            bridge = self.bridge_factory(client)
        if self.ai_manager is not None:
            bridge.ai_manager = self.ai_manager
        bridge.start()

        runtime = AccountRuntime(
            bot_id=bot_id,
            client=client,
            bridge=bridge,
            data_dir=getattr(bridge, "_data_dir", self._account_dir(bot_id)),
        )
        self._runtimes[bot_id] = runtime
        if db.get_bot_account(bot_id) is None:
            db.record_bot_account_event(
                bot_id=bot_id,
                ilink_user_id=getattr(client, "user_id", "") or "",
                event="token_restored",
                data_dir=runtime.data_dir,
                base_url=getattr(client, "base_url", "") or "",
                reason="account_manager",
                token_mtime=client.get_token_mtime() if hasattr(client, "get_token_mtime") else 0,
            )
        self._ensure_default_account(preferred=bot_id)
        return runtime

    def _ensure_default_account(self, preferred: str | None = None):
        if not self._runtimes:
            db.clear_default_bot_account()
            return
        current = db.get_default_bot_id()
        if current in self._runtimes:
            db.set_default_bot_account(current)
            return
        selected = preferred if preferred in self._runtimes else next(iter(self._runtimes))
        db.set_default_bot_account(selected)

    def get_runtime(self, bot_id: str | None = None) -> AccountRuntime | None:
        selected = bot_id or db.get_default_bot_id()
        if selected and selected in self._runtimes:
            return self._runtimes[selected]
        if not bot_id and len(self._runtimes) == 1:
            return next(iter(self._runtimes.values()))
        return None

    def has_accounts(self) -> bool:
        return bool(self._runtimes)

    def any_logged_in(self) -> bool:
        return any(runtime.client.logged_in for runtime in self._runtimes.values())

    def list_accounts(self) -> list[dict]:
        self._ensure_default_account()
        account_rows = {row["bot_id"]: row for row in db.list_bot_accounts()}
        for bot_id, runtime in self._runtimes.items():
            account_rows.setdefault(
                bot_id,
                {
                    "bot_id": bot_id,
                    "ilink_user_id": getattr(runtime.client, "user_id", "") or "",
                    "data_dir": runtime.data_dir,
                    "base_url": getattr(runtime.client, "base_url", "") or "",
                    "status": "active" if runtime.client.logged_in else "logged_out",
                    "is_default": 1 if bot_id == db.get_default_bot_id() else 0,
                    "updated_at": int(time.time()),
                    "last_seen_at": int(time.time()),
                    "token_mtime": runtime.client.get_token_mtime(),
                },
            )

        rows = []
        default_bot_id = db.get_default_bot_id()
        if default_bot_id not in self._runtimes:
            default_bot_id = ""
        for bot_id, row in account_rows.items():
            item = dict(row)
            runtime = self._runtimes.get(bot_id)
            logged_in = bool(runtime and runtime.client.logged_in)
            item["logged_in"] = logged_in
            item["poll_running"] = bool(runtime and runtime.bridge._running)
            item["contacts_count"] = len(runtime.bridge.contacts) if runtime else 0
            if not logged_in:
                item["status"] = "logged_out"
            item["is_default"] = 1 if logged_in and bot_id == default_bot_id else 0
            rows.append(item)
        rows.sort(key=lambda item: (not bool(item.get("is_default")), item.get("bot_id", "")))
        return rows

    def set_default(self, bot_id: str) -> bool:
        if bot_id not in self._runtimes:
            return False
        return db.set_default_bot_account(bot_id)

    def logout(self, bot_id: str | None = None) -> bool:
        runtime = self.get_runtime(bot_id)
        if not runtime:
            return False
        runtime.bridge.record_account_event("logout", reason="web_logout")
        runtime.client.clear_token()
        runtime.bridge.stop()
        self._runtimes.pop(runtime.bot_id, None)
        self._ensure_default_account()
        return True

    def stop_all(self):
        for runtime in list(self._runtimes.values()):
            runtime.bridge.stop()
        self._runtimes.clear()

    def _purge_expired_sessions(self):
        cutoff = time.time() - 120
        expired = [lid for lid, s in self._login_sessions.items() if s.created_at < cutoff]
        for lid in expired:
            self._login_sessions.pop(lid, None)

    def create_login_qr(self) -> dict:
        self._purge_expired_sessions()
        try:
            client = self.client_factory(token_file=None, load_token=False, save_on_login=False)
        except TypeError:
            client = self.client_factory()
        data = client.get_qrcode()
        login_id = uuid.uuid4().hex
        qrcode = data.get("qrcode", "")
        self._login_sessions[login_id] = LoginSession(
            login_id=login_id,
            client=client,
            qrcode=qrcode,
            qrcode_data=data,
            created_at=time.time(),
        )
        return {"login_id": login_id, **data}

    def get_or_create_login_qr(self) -> dict:
        for login_id, session in list(self._login_sessions.items()):
            if time.time() - session.created_at < 60:
                return {"login_id": login_id, **session.qrcode_data}
            self._login_sessions.pop(login_id, None)
        return self.create_login_qr()

    def poll_login_qr_status(self, login_id: str) -> dict:
        session = self._login_sessions.get(login_id)
        if not session:
            raise KeyError("login session not found")

        status_data = session.client.poll_qrcode_status(session.qrcode)
        status = status_data.get("status")
        if status == "expired":
            self._login_sessions.pop(login_id, None)
            return status_data

        if status == "confirmed":
            bot_id = session.client.get_bot_id()
            if not bot_id:
                raise RuntimeError("登录失败：服务器未返回 bot_id")
            session.client.token_file = self._token_file_for(bot_id)
            session.client._save_token()
            runtime = self._start_runtime(session.client)
            runtime.bridge.record_account_event("login_confirmed", reason="qr_confirmed")
            self._login_sessions.pop(login_id, None)
            status_data["bot_id"] = bot_id
        return status_data
