"""
SQLite 消息持久化存储
- 单连接 + 线程锁：避免 ThreadingHTTPServer 下的连接泄漏
- WAL 模式：提高并发读性能
- 数据文件存放在 /data/messages.db，通过 Docker volume 持久化
"""

import os
import sqlite3
import threading
import logging
import time

logger = logging.getLogger(__name__)

DB_FILE = os.environ.get("DB_FILE", "./data/messages.db")
_active_db_file = DB_FILE  # 实际使用的路径（可被 init_db 覆盖）

# 单连接 + 读写锁（SQLite 本身线程安全，但 Python 绑定需要 check_same_thread=False）
_conn: sqlite3.Connection | None = None
_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    """获取全局共享的 SQLite 连接"""
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(_active_db_file) or ".", exist_ok=True)
        _conn = sqlite3.connect(_active_db_file, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")       # 写前日志，提高并发读
        _conn.execute("PRAGMA synchronous=NORMAL")      # 平衡可靠性与性能
        _conn.execute("PRAGMA wal_autocheckpoint=500")   # 每 500 页自动合并 WAL
        _conn.execute("PRAGMA busy_timeout=5000")        # 等锁最多 5 秒
    return _conn


def init_db(db_file: str = None):
    """初始化数据库表结构。可传入 db_file 切换到新路径（用于多账号隔离）"""
    global _active_db_file, _conn
    if db_file and db_file != _active_db_file:
        # 切换数据库路径：先关闭旧连接
        with _lock:
            if _conn is not None:
                _conn.close()
                _conn = None
        _active_db_file = db_file
        logger.info("数据库路径切换为: %s", _active_db_file)
    with _lock:
        conn = _get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                msg_id      TEXT UNIQUE NOT NULL,
                type        TEXT NOT NULL,            -- 'recv' | 'send'
                contact     TEXT NOT NULL,
                user_id     TEXT NOT NULL,
                text        TEXT NOT NULL,
                time        INTEGER NOT NULL,
                media       TEXT DEFAULT NULL,        -- 媒体文件名（图片/视频/文件）
                created_at  REAL DEFAULT (strftime('%s','now'))
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_time ON messages(time DESC)
        """)
        conn.commit()

        # 向后兼容：已有数据库可能缺少 media 列
        try:
            conn.execute("ALTER TABLE messages ADD COLUMN media TEXT DEFAULT NULL")
            conn.commit()
            logger.info("已添加 media 列到 messages 表")
        except Exception:
            pass  # 列已存在，忽略

        # 启动时自动清理超过 90 天的旧消息（可通过环境变量调整）
        retention_days = int(os.environ.get("MSG_RETENTION_DAYS", "90"))
        if retention_days > 0:
            cutoff = int(time.time()) - retention_days * 86400
            cursor = conn.execute("DELETE FROM messages WHERE time < ?", (cutoff,))
            if cursor.rowcount > 0:
                conn.commit()
                logger.info("已清理 %d 条超过 %d 天的旧消息", cursor.rowcount, retention_days)

        count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        logger.info("消息数据库已初始化: %s (现有 %d 条记录)", _active_db_file, count)


def save_message(msg: dict):
    """存储一条消息（去重：msg_id 唯一约束）"""
    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO messages (msg_id, type, contact, user_id, text, time, media) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(msg["msg_id"]),
                    msg["type"],
                    msg["contact"],
                    msg["user_id"],
                    msg["text"],
                    msg["time"],
                    msg.get("media"),
                ),
            )
            conn.commit()
        except Exception as e:
            logger.warning("保存消息失败: %s", e)


def get_messages(limit: int = 200, before_id: int = None) -> list[dict]:
    """
    获取最近的消息列表（按时间升序返回）
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

    # 反转为时间升序
    messages = []
    for row in reversed(rows):
        messages.append({
            "id": row["id"],
            "msg_id": row["msg_id"],
            "type": row["type"],
            "contact": row["contact"],
            "user_id": row["user_id"],
            "text": row["text"],
            "time": row["time"],
            "media": row["media"] if "media" in row.keys() else None,
        })
    return messages


def get_message_count() -> int:
    """获取消息总数"""
    with _lock:
        conn = _get_conn()
        row = conn.execute("SELECT COUNT(*) FROM messages").fetchone()
        return row[0] if row else 0
