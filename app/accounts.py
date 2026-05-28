from __future__ import annotations

"""多账号运行时管理。"""

import json
import logging
import os
import shutil
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import db
from account_identity import account_storage_dir_name, safe_account_dir_name
from bridge import WeChatBridge
from ilink import ILinkClient

logger = logging.getLogger(__name__)


def _safe_account_dir_name(bot_id: str) -> str:
    return safe_account_dir_name(bot_id)


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


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _load_json_dict(path: Path) -> dict:
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as exc:
        logger.warning("读取 JSON 缓存失败 [%s]: %s", path, exc)
    return {}


def _merge_json_dict_file(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    src_data = _load_json_dict(src)
    if not src_data:
        return False
    dst_data = _load_json_dict(dst)
    merged = {**src_data, **dst_data}
    if merged == dst_data:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def _copy_missing_tree(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    changed = False
    for item in src.rglob("*"):
        if not item.is_file():
            continue
        rel = item.relative_to(src)
        target = dst / rel
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        changed = True
    return changed


def _sqlite_table_columns(conn: sqlite3.Connection, schema: str, table: str) -> list[str]:
    try:
        rows = conn.execute(f"PRAGMA {schema}.table_info({_quote_ident(table)})").fetchall()
    except sqlite3.DatabaseError:
        return []
    return [row[1] for row in rows]


def _merge_sqlite_table(conn: sqlite3.Connection, table: str, *, skip_columns: set[str] | None = None) -> bool:
    src_cols = set(_sqlite_table_columns(conn, "src", table))
    dst_cols = _sqlite_table_columns(conn, "main", table)
    if not src_cols or not dst_cols:
        return False
    skip_columns = skip_columns or set()
    columns = [col for col in dst_cols if col in src_cols and col not in skip_columns]
    if not columns:
        return False
    quoted_cols = ", ".join(_quote_ident(col) for col in columns)
    before = conn.total_changes
    conn.execute(
        f"INSERT OR IGNORE INTO {_quote_ident(table)} ({quoted_cols}) "
        f"SELECT {quoted_cols} FROM src.{_quote_ident(table)}"
    )
    return conn.total_changes > before


def _merge_sqlite_db(src_db: Path, dst_db: Path) -> bool:
    if not src_db.exists():
        return False
    dst_db.parent.mkdir(parents=True, exist_ok=True)
    if not dst_db.exists():
        for suffix in ("", "-wal", "-shm"):
            src_part = Path(f"{src_db}{suffix}")
            if src_part.exists():
                shutil.copy2(src_part, Path(f"{dst_db}{suffix}"))
        return True

    changed = False
    conn = sqlite3.connect(str(dst_db))
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("ATTACH DATABASE ? AS src", (str(src_db),))
        table_specs = {
            "messages": {"id"},
            "delivery_state": set(),
            "overflow_sessions": set(),
            "pending_messages": set(),
            "contact_activity": set(),
            "default_recipient_decisions": {"id"},
        }
        for table, skip_columns in table_specs.items():
            changed = _merge_sqlite_table(conn, table, skip_columns=skip_columns) or changed
        conn.commit()
        conn.execute("DETACH DATABASE src")
    finally:
        conn.close()
    return changed


def _merge_account_dir(src_dir: Path, dst_dir: Path) -> bool:
    if not src_dir.exists() or src_dir.resolve() == dst_dir.resolve():
        return False
    dst_dir.mkdir(parents=True, exist_ok=True)
    changed = False
    changed = _merge_sqlite_db(src_dir / "messages.db", dst_dir / "messages.db") or changed
    for name in ("contacts.json", "context_tokens.json", "activity.json"):
        changed = _merge_json_dict_file(src_dir / name, dst_dir / name) or changed
    changed = _copy_missing_tree(src_dir / "media", dst_dir / "media") or changed
    return changed


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

    def _account_dir(self, bot_id: str, ilink_user_id: str = "") -> str:
        return os.path.join(
            self.data_base,
            account_storage_dir_name(bot_id=bot_id, ilink_user_id=ilink_user_id),
        )

    def _legacy_account_dir(self, bot_id: str) -> str:
        return os.path.join(self.data_base, _safe_account_dir_name(bot_id))

    def _token_file_for(self, bot_id: str, ilink_user_id: str = "") -> str:
        return os.path.join(self._account_dir(bot_id, ilink_user_id), "token.json")

    def _candidate_legacy_dirs(self, bot_id: str, ilink_user_id: str) -> list[Path]:
        stable_dir = Path(self._account_dir(bot_id, ilink_user_id)).resolve()
        seen: set[str] = set()
        candidates: list[Path] = []

        def add(path: str | Path | None):
            if not path:
                return
            candidate = Path(path)
            try:
                resolved = candidate.resolve()
            except OSError:
                return
            key = str(resolved)
            if key in seen or resolved == stable_dir or not candidate.exists() or not candidate.is_dir():
                return
            seen.add(key)
            candidates.append(candidate)

        if bot_id:
            add(self._legacy_account_dir(bot_id))

        try:
            account_rows = db.list_bot_accounts()
        except Exception:
            account_rows = []
        for row in account_rows:
            row_bot_id = str(row.get("bot_id") or "")
            row_user_id = str(row.get("ilink_user_id") or "")
            if (ilink_user_id and row_user_id == ilink_user_id) or (bot_id and row_bot_id == bot_id):
                add(row.get("data_dir"))
                if row_bot_id:
                    add(self._legacy_account_dir(row_bot_id))

        try:
            token_paths = sorted(Path(self.data_base).glob("*/token.json"))
        except Exception:
            token_paths = []
        for token_path in token_paths:
            try:
                data = json.loads(token_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            token_bot_id = str(data.get("bot_id") or "")
            token_user_id = str(data.get("user_id") or "")
            if (ilink_user_id and token_user_id == ilink_user_id) or (bot_id and token_bot_id == bot_id):
                add(token_path.parent)

        return candidates

    def _migrate_account_data(self, bot_id: str, ilink_user_id: str = "", *, current_token_file: str | None = None):
        stable_dir = Path(self._account_dir(bot_id, ilink_user_id))
        current_token_resolved: Path | None = None
        if current_token_file:
            try:
                current_token_resolved = Path(current_token_file).resolve()
            except OSError:
                current_token_resolved = None
        migrated_from: list[str] = []
        for src_dir in self._candidate_legacy_dirs(bot_id, ilink_user_id):
            try:
                if _merge_account_dir(src_dir, stable_dir):
                    migrated_from.append(str(src_dir))
                legacy_token = src_dir / "token.json"
                stable_token = stable_dir / "token.json"
                legacy_token_resolved = legacy_token.resolve() if legacy_token.exists() else None
                if (
                    legacy_token_resolved
                    and legacy_token_resolved != stable_token.resolve()
                    and legacy_token_resolved != current_token_resolved
                ):
                    legacy_token.unlink()
            except Exception as exc:
                logger.warning("迁移账号数据目录失败 [%s -> %s]: %s", src_dir, stable_dir, exc)

        if current_token_file:
            token_path = Path(current_token_file)
            try:
                stable_token = stable_dir / "token.json"
                if token_path.exists() and token_path.resolve() != stable_token.resolve():
                    stable_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(token_path, stable_token)
                    token_path.unlink()
            except Exception as exc:
                logger.warning("迁移 token 文件失败 [%s -> %s]: %s", token_path, stable_dir, exc)

        if migrated_from:
            logger.info("账号数据已迁移到稳定目录 %s，来源: %s", stable_dir, ", ".join(migrated_from))

    def _prepare_client_storage(self, client: ILinkClient) -> str:
        bot_id = client.get_bot_id() or ""
        if not bot_id:
            raise RuntimeError("无法准备账号存储：缺少 bot_id")
        ilink_user_id = getattr(client, "user_id", "") or ""
        current_token_file = getattr(client, "token_file", None)
        self._migrate_account_data(bot_id, ilink_user_id, current_token_file=current_token_file)
        token_file = self._token_file_for(bot_id, ilink_user_id)
        client.token_file = token_file
        return token_file

    def _migrate_legacy_token(self):
        legacy = Path(self.data_base) / "token.json"
        if not legacy.exists():
            return
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
            bot_token = data.get("bot_token")
            bot_id = data.get("bot_id") or ILinkClient._extract_bot_id(bot_token)
            ilink_user_id = data.get("user_id") or ""
            if not bot_id:
                logger.warning("旧 token.json 缺少 bot_id，跳过多账号迁移")
                return
            target = Path(self._token_file_for(bot_id, ilink_user_id))
            target.parent.mkdir(parents=True, exist_ok=True)
            _merge_account_dir(Path(self.data_base), target.parent)
            if not target.exists():
                os.replace(str(legacy), str(target))
                logger.info("旧 token.json 已迁移到账号目录: %s", target)
            else:
                legacy.unlink()
        except Exception as exc:
            logger.warning("迁移旧 token.json 失败: %s", exc)

    def restore_accounts(self) -> list[AccountRuntime]:
        """从 DATA_DIR/*/token.json 恢复所有已登录账号并启动轮询。"""
        self._migrate_legacy_token()
        restored: list[AccountRuntime] = []
        for token_path in sorted(Path(self.data_base).glob("*/token.json")):
            try:
                if not token_path.exists():
                    continue
                try:
                    client = self.client_factory(token_file=str(token_path))
                except TypeError:
                    client = self.client_factory()
                if not client.logged_in or not client.get_bot_id():
                    continue
                self._prepare_client_storage(client)
                if hasattr(client, "_save_token"):
                    client._save_token()
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
            data_dir=getattr(
                bridge,
                "_data_dir",
                self._account_dir(bot_id, getattr(client, "user_id", "") or ""),
            ),
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
        self._ensure_default_account(preferred=bot_id, preserve_offline_current=True)
        return runtime

    def _ensure_default_account(self, preferred: str | None = None, preserve_offline_current: bool = False):
        if not self._runtimes:
            db.clear_default_bot_account()
            return
        current = db.get_default_bot_id()
        if current in self._runtimes:
            return
        if preserve_offline_current and current and db.get_bot_account(current):
            return
        selected = preferred if preferred in self._runtimes else next(iter(self._runtimes))
        db.set_default_bot_account(selected)

    def get_runtime(self, bot_id: str | None = None) -> AccountRuntime | None:
        if bot_id:
            if bot_id in self._runtimes:
                return self._runtimes[bot_id]
            remark_match = None
            for acc in db.list_bot_accounts():
                if acc.get("ilink_user_id") == bot_id or acc.get("remark") == bot_id:
                    bid = acc.get("bot_id")
                    if acc.get("ilink_user_id") == bot_id and bid in self._runtimes:
                        return self._runtimes[bid]
                    if acc.get("remark") == bot_id and bid in self._runtimes:
                        if remark_match is not None:
                            return None
                        remark_match = self._runtimes[bid]
            if remark_match is not None:
                return remark_match
            return None

        selected = db.get_default_bot_id()
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
            if runtime and hasattr(runtime.bridge, "get_visible_contacts"):
                item["contacts_count"] = len(runtime.bridge.get_visible_contacts())
                item["contacts_total"] = len(runtime.bridge.contacts)
            else:
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
            session.client.token_file = self._prepare_client_storage(session.client)
            session.client._save_token()
            runtime = self._start_runtime(session.client)
            runtime.bridge.record_account_event("login_confirmed", reason="qr_confirmed")
            self._login_sessions.pop(login_id, None)
            status_data["bot_id"] = bot_id
        return status_data
