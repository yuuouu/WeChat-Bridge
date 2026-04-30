"""
SQLite 消息与投递状态持久化存储
- 单连接 + 线程锁：避免 ThreadingHTTPServer 下的连接泄漏
- WAL 模式：提高并发读性能
- 数据文件存放在 /data/messages.db，通过 Docker volume 持久化
"""

import json
import logging
import os
import sqlite3
import threading
import time

logger = logging.getLogger(__name__)

DB_FILE = os.environ.get("DB_FILE", "./data/messages.db")
_active_db_file = DB_FILE  # 实际使用的路径（可被 init_db 覆盖）

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()

DEFAULT_DELIVERY_STATE = {
    "status": "NORMAL",
    "consecutive_send_count": 0,
    "active_overflow_session_id": None,
    "blocked_reason": None,
    "last_user_message_at": 0,
    "last_warned_at": 0,
    "updated_at": 0,
}

ACTIVE_OVERFLOW_STATUSES = ("OPEN", "READY_PULL")


def _now_ts() -> int:
    return int(time.time())


def _get_conn() -> sqlite3.Connection:
    """获取全局共享的 SQLite 连接。"""
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(_active_db_file) or ".", exist_ok=True)
        _conn = sqlite3.connect(_active_db_file, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.execute("PRAGMA wal_autocheckpoint=500")
        _conn.execute("PRAGMA busy_timeout=5000")
    return _conn


def close_db():
    """关闭全局连接，供测试或进程退出时调用。"""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


def _ensure_column(conn: sqlite3.Connection, table: str, column_name: str, definition: str):
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
        logger.info("已为 %s 表添加列: %s", table, column_name)


def _decode_meta(meta_json: str | None):
    if not meta_json:
        return None
    try:
        return json.loads(meta_json)
    except Exception:
        return None


def _row_to_message(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "msg_id": row["msg_id"],
        "type": row["type"],
        "contact": row["contact"],
        "user_id": row["user_id"],
        "text": row["text"],
        "time": row["time"],
        "media": row["media"],
        "delivery_stage": row["delivery_stage"],
        "overflow_session_id": row["overflow_session_id"],
        "pending_message_id": row["pending_message_id"],
        "meta": _decode_meta(row["meta_json"]),
    }


def _row_to_delivery_state(row: sqlite3.Row | None) -> dict:
    state = DEFAULT_DELIVERY_STATE.copy()
    if row is None:
        return state
    state.update(
        {
            "status": row["status"] or "NORMAL",
            "consecutive_send_count": row["consecutive_send_count"] or 0,
            "active_overflow_session_id": row["active_overflow_session_id"],
            "blocked_reason": row["blocked_reason"],
            "last_user_message_at": row["last_user_message_at"] or 0,
            "last_warned_at": row["last_warned_at"] or 0,
            "updated_at": row["updated_at"] or 0,
        }
    )
    return state


def _row_to_overflow_session(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "status": row["status"],
        "reason": row["reason"],
        "opened_at": row["opened_at"],
        "ready_at": row["ready_at"],
        "closed_at": row["closed_at"],
        "discarded_at": row["discarded_at"],
        "trigger_msg_id": row["trigger_msg_id"],
        "pending_count": row["pending_count"] or 0,
    }


def _row_to_pending_message(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "user_id": row["user_id"],
        "source": row["source"],
        "title": row["title"],
        "content": row["content"],
        "media": row["media"],
        "content_len": row["content_len"],
        "blocked_reason": row["blocked_reason"],
        "created_at": row["created_at"],
        "status": row["status"],
        "delivered_at": row["delivered_at"],
        "discarded_at": row["discarded_at"],
    }


def init_db(db_file: str = None):
    """初始化数据库表结构。可传入 db_file 切换到新路径（用于多账号隔离）。"""
    global _active_db_file, _conn
    if db_file and db_file != _active_db_file:
        with _lock:
            if _conn is not None:
                _conn.close()
                _conn = None
        _active_db_file = db_file
        logger.info("数据库路径切换为: %s", _active_db_file)

    with _lock:
        conn = _get_conn()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                msg_id              TEXT UNIQUE NOT NULL,
                type                TEXT NOT NULL,
                contact             TEXT NOT NULL,
                user_id             TEXT NOT NULL,
                text                TEXT NOT NULL,
                time                INTEGER NOT NULL,
                media               TEXT DEFAULT NULL,
                delivery_stage      TEXT DEFAULT 'direct',
                overflow_session_id TEXT DEFAULT NULL,
                pending_message_id  INTEGER DEFAULT NULL,
                meta_json           TEXT DEFAULT NULL,
                created_at          REAL DEFAULT (strftime('%s','now'))
            )
        """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS delivery_state (
                user_id                     TEXT PRIMARY KEY,
                status                      TEXT NOT NULL DEFAULT 'NORMAL',
                consecutive_send_count      INTEGER NOT NULL DEFAULT 0,
                active_overflow_session_id  TEXT DEFAULT NULL,
                blocked_reason              TEXT DEFAULT NULL,
                last_user_message_at        INTEGER NOT NULL DEFAULT 0,
                last_warned_at              INTEGER NOT NULL DEFAULT 0,
                updated_at                  INTEGER NOT NULL DEFAULT 0
            )
        """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS overflow_sessions (
                id              TEXT PRIMARY KEY,
                user_id         TEXT NOT NULL,
                status          TEXT NOT NULL,
                reason          TEXT NOT NULL,
                opened_at       INTEGER NOT NULL,
                ready_at        INTEGER DEFAULT NULL,
                closed_at       INTEGER DEFAULT NULL,
                discarded_at    INTEGER DEFAULT NULL,
                trigger_msg_id  TEXT DEFAULT NULL,
                pending_count   INTEGER NOT NULL DEFAULT 0
            )
        """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id      TEXT NOT NULL,
                user_id         TEXT NOT NULL,
                source          TEXT NOT NULL DEFAULT 'system',
                title           TEXT DEFAULT '',
                content         TEXT NOT NULL,
                media           TEXT DEFAULT NULL,
                content_len     INTEGER NOT NULL,
                blocked_reason  TEXT DEFAULT NULL,
                created_at      INTEGER NOT NULL,
                status          TEXT NOT NULL DEFAULT 'PENDING',
                delivered_at    INTEGER DEFAULT NULL,
                discarded_at    INTEGER DEFAULT NULL
            )
        """
        )

        _ensure_column(conn, "messages", "media", "media TEXT DEFAULT NULL")
        _ensure_column(conn, "messages", "delivery_stage", "delivery_stage TEXT DEFAULT 'direct'")
        _ensure_column(conn, "messages", "overflow_session_id", "overflow_session_id TEXT DEFAULT NULL")
        _ensure_column(conn, "messages", "pending_message_id", "pending_message_id INTEGER DEFAULT NULL")
        _ensure_column(conn, "messages", "meta_json", "meta_json TEXT DEFAULT NULL")
        _ensure_column(conn, "pending_messages", "media", "media TEXT DEFAULT NULL")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_time ON messages(time DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(overflow_session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_pending ON messages(pending_message_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_overflow_sessions_user ON overflow_sessions(user_id, status)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_messages_session ON pending_messages(session_id, status, created_at)"
        )
        conn.commit()

        retention_days = int(os.environ.get("MSG_RETENTION_DAYS", "90"))
        if retention_days > 0:
            cutoff = _now_ts() - retention_days * 86400
            cursor = conn.execute("DELETE FROM messages WHERE time < ?", (cutoff,))
            if cursor.rowcount > 0:
                conn.commit()
                logger.info("已清理 %d 条超过 %d 天的旧消息", cursor.rowcount, retention_days)

        count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        logger.info("消息数据库已初始化: %s (现有 %d 条记录)", _active_db_file, count)


def save_message(msg: dict):
    """存储一条消息（去重：msg_id 唯一约束）。"""
    with _lock:
        conn = _get_conn()
        try:
            meta = msg.get("meta")
            meta_json = json.dumps(meta, ensure_ascii=False) if meta is not None else None
            conn.execute(
                """
                INSERT OR IGNORE INTO messages (
                    msg_id, type, contact, user_id, text, time, media,
                    delivery_stage, overflow_session_id, pending_message_id, meta_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    str(msg["msg_id"]),
                    msg["type"],
                    msg["contact"],
                    msg["user_id"],
                    msg["text"],
                    msg["time"],
                    msg.get("media"),
                    msg.get("delivery_stage", "direct"),
                    msg.get("overflow_session_id"),
                    msg.get("pending_message_id"),
                    meta_json,
                ),
            )
            conn.commit()
        except Exception as exc:
            logger.warning("保存消息失败: %s", exc)


def get_messages(limit: int = 200, before_id: int = None) -> list[dict]:
    """
    获取最近的消息列表（按时间升序返回）。
    - limit: 最多返回条数
    - before_id: 分页用，获取 id < before_id 的消息
    """
    with _lock:
        conn = _get_conn()
        if before_id:
            rows = conn.execute(
                "SELECT * FROM messages WHERE id < ? ORDER BY id DESC LIMIT ?",
                (before_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM messages ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()

    return [_row_to_message(row) for row in reversed(rows)]


def get_message_count() -> int:
    """获取消息总数。"""
    with _lock:
        conn = _get_conn()
        row = conn.execute("SELECT COUNT(*) FROM messages").fetchone()
        return row[0] if row else 0


def update_message_delivery_stage_for_pending_ids(pending_ids: list[int], stage: str):
    if not pending_ids:
        return
    placeholders = ",".join("?" for _ in pending_ids)
    with _lock:
        conn = _get_conn()
        conn.execute(
            f"UPDATE messages SET delivery_stage = ? WHERE pending_message_id IN ({placeholders})",
            (stage, *pending_ids),
        )
        conn.commit()


def get_delivery_state(user_id: str) -> dict:
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM delivery_state WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    state = _row_to_delivery_state(row)
    state["user_id"] = user_id
    return state


def save_delivery_state(state: dict) -> dict:
    user_id = state["user_id"]
    payload = DEFAULT_DELIVERY_STATE.copy()
    payload.update(state)
    payload["updated_at"] = payload.get("updated_at") or _now_ts()
    with _lock:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO delivery_state (
                user_id, status, consecutive_send_count, active_overflow_session_id,
                blocked_reason, last_user_message_at, last_warned_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                status = excluded.status,
                consecutive_send_count = excluded.consecutive_send_count,
                active_overflow_session_id = excluded.active_overflow_session_id,
                blocked_reason = excluded.blocked_reason,
                last_user_message_at = excluded.last_user_message_at,
                last_warned_at = excluded.last_warned_at,
                updated_at = excluded.updated_at
        """,
            (
                user_id,
                payload["status"],
                payload["consecutive_send_count"],
                payload["active_overflow_session_id"],
                payload["blocked_reason"],
                payload["last_user_message_at"],
                payload["last_warned_at"],
                payload["updated_at"],
            ),
        )
        conn.commit()
    return get_delivery_state(user_id)


def update_delivery_state(user_id: str, **fields) -> dict:
    state = get_delivery_state(user_id)
    state.update(fields)
    state["user_id"] = user_id
    if "updated_at" not in fields:
        state["updated_at"] = _now_ts()
    return save_delivery_state(state)


def list_delivery_states() -> list[dict]:
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            """
            SELECT
                ds.*,
                COALESCE(os.pending_count, 0) AS pending_count
            FROM delivery_state ds
            LEFT JOIN overflow_sessions os
                ON os.id = ds.active_overflow_session_id
            ORDER BY ds.updated_at DESC
        """
        ).fetchall()

    results = []
    for row in rows:
        state = _row_to_delivery_state(row)
        state["user_id"] = row["user_id"]
        state["pending_count"] = row["pending_count"] or 0
        results.append(state)
    return results


def get_global_delivery_stats() -> dict:
    with _lock:
        conn = _get_conn()
        pending_total = conn.execute("SELECT COUNT(*) FROM pending_messages WHERE status = 'PENDING'").fetchone()[0]
        active_sessions = conn.execute(
            "SELECT COUNT(*) FROM overflow_sessions WHERE status IN ('OPEN', 'READY_PULL')"
        ).fetchone()[0]
        buffering_users = conn.execute(
            "SELECT COUNT(*) FROM delivery_state WHERE status IN ('WARNED', 'BUFFERING', 'READY_PULL')"
        ).fetchone()[0]
    return {
        "pending_total": pending_total,
        "active_sessions": active_sessions,
        "buffering_users": buffering_users,
    }


def create_overflow_session(
    session_id: str,
    user_id: str,
    reason: str,
    trigger_msg_id: str | None = None,
    status: str = "OPEN",
    opened_at: int | None = None,
) -> dict:
    opened_at = opened_at or _now_ts()
    with _lock:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO overflow_sessions (
                id, user_id, status, reason, opened_at, trigger_msg_id, pending_count
            )
            VALUES (?, ?, ?, ?, ?, ?, 0)
        """,
            (session_id, user_id, status, reason, opened_at, trigger_msg_id),
        )
        conn.commit()
    return get_overflow_session(session_id)


def get_overflow_session(session_id: str | None) -> dict | None:
    if not session_id:
        return None
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM overflow_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    return _row_to_overflow_session(row)


def get_active_overflow_session(user_id: str) -> dict | None:
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            """
            SELECT * FROM overflow_sessions
            WHERE user_id = ? AND status IN ('OPEN', 'READY_PULL')
            ORDER BY opened_at DESC
            LIMIT 1
        """,
            (user_id,),
        ).fetchone()
    return _row_to_overflow_session(row)


def recount_overflow_session_pending_count(session_id: str) -> int:
    with _lock:
        conn = _get_conn()
        pending_count = conn.execute(
            "SELECT COUNT(*) FROM pending_messages WHERE session_id = ? AND status = 'PENDING'",
            (session_id,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE overflow_sessions SET pending_count = ? WHERE id = ?",
            (pending_count, session_id),
        )
        conn.commit()
    return pending_count


def mark_overflow_session_ready(session_id: str, ready_at: int | None = None):
    ready_at = ready_at or _now_ts()
    with _lock:
        conn = _get_conn()
        conn.execute(
            """
            UPDATE overflow_sessions
            SET status = 'READY_PULL', ready_at = ?
            WHERE id = ?
        """,
            (ready_at, session_id),
        )
        conn.commit()


def mark_overflow_session_drained(session_id: str, closed_at: int | None = None):
    closed_at = closed_at or _now_ts()
    with _lock:
        conn = _get_conn()
        conn.execute(
            """
            UPDATE overflow_sessions
            SET status = 'DRAINED', closed_at = ?, pending_count = 0
            WHERE id = ?
        """,
            (closed_at, session_id),
        )
        conn.commit()


def discard_overflow_session(session_id: str, discarded_at: int | None = None):
    discarded_at = discarded_at or _now_ts()
    with _lock:
        conn = _get_conn()
        conn.execute(
            """
            UPDATE overflow_sessions
            SET status = 'DISCARDED', discarded_at = ?, pending_count = 0
            WHERE id = ?
        """,
            (discarded_at, session_id),
        )
        conn.commit()


def discard_active_overflow_sessions(user_id: str, discarded_at: int | None = None) -> list[dict]:
    discarded_at = discarded_at or _now_ts()
    sessions = []
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            """
            SELECT * FROM overflow_sessions
            WHERE user_id = ? AND status IN ('OPEN', 'READY_PULL')
            ORDER BY opened_at DESC
        """,
            (user_id,),
        ).fetchall()
        sessions = [_row_to_overflow_session(row) for row in rows]
        if rows:
            conn.execute(
                """
                UPDATE overflow_sessions
                SET status = 'DISCARDED', discarded_at = ?, pending_count = 0
                WHERE user_id = ? AND status IN ('OPEN', 'READY_PULL')
            """,
                (discarded_at, user_id),
            )
            conn.commit()
    return [session for session in sessions if session]


def create_pending_message(
    session_id: str,
    user_id: str,
    content: str,
    source: str = "system",
    title: str = "",
    media: str | None = None,
    blocked_reason: str | None = None,
    created_at: int | None = None,
) -> dict:
    created_at = created_at or _now_ts()
    with _lock:
        conn = _get_conn()
        cursor = conn.execute(
            """
            INSERT INTO pending_messages (
                session_id, user_id, source, title, content, media,
                content_len, blocked_reason, created_at, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
        """,
            (
                session_id,
                user_id,
                source,
                title,
                content,
                media,
                len(content),
                blocked_reason,
                created_at,
            ),
        )
        pending_id = cursor.lastrowid
        conn.commit()
    recount_overflow_session_pending_count(session_id)
    return get_pending_message(pending_id)


def get_pending_message(pending_id: int) -> dict | None:
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM pending_messages WHERE id = ?",
            (pending_id,),
        ).fetchone()
    return _row_to_pending_message(row) if row else None


def get_pending_messages(session_id: str, status: str = "PENDING") -> list[dict]:
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            """
            SELECT * FROM pending_messages
            WHERE session_id = ? AND status = ?
            ORDER BY created_at ASC, id ASC
        """,
            (session_id, status),
        ).fetchall()
    return [_row_to_pending_message(row) for row in rows]


def get_pending_count(session_id: str | None) -> int:
    if not session_id:
        return 0
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT pending_count FROM overflow_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        return row["pending_count"] if row else 0


def mark_pending_messages_pulled(pending_ids: list[int], delivered_at: int | None = None):
    if not pending_ids:
        return
    delivered_at = delivered_at or _now_ts()
    placeholders = ",".join("?" for _ in pending_ids)
    with _lock:
        conn = _get_conn()
        session_rows = conn.execute(
            f"SELECT DISTINCT session_id FROM pending_messages WHERE id IN ({placeholders})",
            tuple(pending_ids),
        ).fetchall()
        conn.execute(
            f"""
            UPDATE pending_messages
            SET status = 'PULLED', delivered_at = ?, discarded_at = NULL
            WHERE id IN ({placeholders})
        """,
            (delivered_at, *pending_ids),
        )
        conn.commit()
    for row in session_rows:
        recount_overflow_session_pending_count(row["session_id"])


def discard_pending_messages(session_id: str, discarded_at: int | None = None) -> list[int]:
    discarded_at = discarded_at or _now_ts()
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT id FROM pending_messages WHERE session_id = ? AND status = 'PENDING'",
            (session_id,),
        ).fetchall()
        pending_ids = [row["id"] for row in rows]
        if pending_ids:
            conn.execute(
                """
                UPDATE pending_messages
                SET status = 'DISCARDED', discarded_at = ?
                WHERE session_id = ? AND status = 'PENDING'
            """,
                (discarded_at, session_id),
            )
            conn.commit()
    recount_overflow_session_pending_count(session_id)
    return pending_ids
