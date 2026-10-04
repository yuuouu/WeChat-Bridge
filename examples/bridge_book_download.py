#!/usr/bin/env python3
"""搜索书目、下载公版书并把用户自有电子书导入 CWA。"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
for _path in (_SCRIPT_DIR.parent, _SCRIPT_DIR.parent / "app"):
    if _path.exists():
        sys.path.insert(0, str(_path))

from plugin_base import Plugin  # noqa: E402

logger = logging.getLogger(__name__)

COMMANDS = ["/书籍", "/book", "/找书", "/findbook", "/导入书籍", "/importbook"]
GUTENDEX_API_BASE = os.environ.get("GUTENDEX_API_BASE", "https://gutendex.com").rstrip("/")
DOUBAN_SUGGEST_URL = "https://book.douban.com/j/subject_suggest"
OPEN_LIBRARY_SEARCH_URL = "https://openlibrary.org/search.json"
BOOK_DOWNLOAD_DIR = os.environ.get("BOOK_DOWNLOAD_DIR", "/data/book-downloads")
BOOK_INGEST_DIR = os.environ.get("BOOK_INGEST_DIR", "/data/book-ingest")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


BOOK_DOWNLOAD_MAX_BYTES = max(1024, _env_int("BOOK_DOWNLOAD_MAX_BYTES", 20 * 1024 * 1024))
BOOK_DOWNLOAD_TIMEOUT = max(5, _env_int("BOOK_DOWNLOAD_TIMEOUT", 120))
BOOK_SEARCH_LIMIT = min(10, max(1, _env_int("BOOK_SEARCH_LIMIT", 5)))
BOOK_SESSION_TTL = max(30, _env_int("BOOK_SESSION_TTL", 300))
BOOK_MAX_CONCURRENCY = max(1, _env_int("BOOK_MAX_CONCURRENCY", 1))
BOOK_IMPORT_MAX_BYTES = max(1024, _env_int("BOOK_IMPORT_MAX_BYTES", 20 * 1024 * 1024))

_TASK_SEMAPHORE = threading.BoundedSemaphore(BOOK_MAX_CONCURRENCY)
_SAFE_NAME_RE = re.compile(r"[\\/:*?\"<>|\x00-\x1f]")
_FORMAT_PRIORITY = (
    ("application/epub+zip", "epub"),
    ("application/pdf", "pdf"),
    ("application/x-mobipocket-ebook", "mobi"),
    ("text/plain", "txt"),
)
_HTTP_HEADERS = {
    "User-Agent": "WeChat-Bridge/1.2 book-catalog (low-volume human search)",
    "Accept": "application/json,application/epub+zip,application/pdf,text/plain;q=0.9,*/*;q=0.2",
}


def validate_download_url(url: str) -> str:
    """限制下载到 Project Gutenberg HTTPS 主机，阻止任意 URL 与 SSRF。"""
    parsed = urllib.parse.urlparse(str(url or "").strip())
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https":
        raise ValueError("书籍下载只允许 HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("书籍下载 URL 不允许包含认证信息")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("书籍下载 URL 端口无效") from exc
    if port not in (None, 443):
        raise ValueError("书籍下载只允许 HTTPS 默认端口")
    if hostname != "gutenberg.org" and not hostname.endswith(".gutenberg.org"):
        raise ValueError("书籍下载目标不属于 Project Gutenberg")
    return parsed.geturl()


def validate_catalog_url(url: str) -> str:
    """只允许输出已接入书目 provider 的 HTTPS 详情页。"""
    parsed = urllib.parse.urlparse(str(url or "").strip())
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("书目详情 URL 无效")
    if hostname == "book.douban.com" and re.fullmatch(r"/subject/\d+/?", parsed.path or ""):
        return parsed.geturl()
    if hostname == "openlibrary.org" and re.fullmatch(r"/(works|books)/[A-Za-z0-9._-]+/?", parsed.path or ""):
        return parsed.geturl()
    raise ValueError("书目详情 URL 不属于受信 provider")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_download_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_DOWNLOAD_OPENER = urllib.request.build_opener(_SafeRedirectHandler())


def get_json(url: str):
    request = urllib.request.Request(url, headers=_HTTP_HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def open_download(url: str):
    safe_url = validate_download_url(url)
    request = urllib.request.Request(safe_url, headers=_HTTP_HEADERS)
    return _DOWNLOAD_OPENER.open(request, timeout=BOOK_DOWNLOAD_TIMEOUT)


def select_format(formats: dict) -> dict | None:
    """按 EPUB、PDF、MOBI、TXT 顺序选择可安全下载的格式。"""
    for mime_prefix, extension in _FORMAT_PRIORITY:
        candidates = []
        for mime_type, raw_url in (formats or {}).items():
            if not str(mime_type).lower().startswith(mime_prefix):
                continue
            try:
                url = validate_download_url(str(raw_url))
            except ValueError:
                continue
            candidates.append((str(mime_type), url))
        if candidates:
            mime_type, url = sorted(candidates, key=lambda item: ("noimages" in item[1], item[1]))[0]
            return {"mime_type": mime_type, "extension": extension, "url": url}
    return None


def search_books(query: str, *, limit: int | None = None) -> list[dict]:
    query = str(query or "").strip()
    if not query:
        return []
    limit = BOOK_SEARCH_LIMIT if limit is None else min(10, max(1, int(limit)))
    params = urllib.parse.urlencode({"search": query, "copyright": "false"})
    payload = get_json(f"{GUTENDEX_API_BASE}/books?{params}")
    books = []
    for item in payload.get("results") or []:
        if item.get("copyright") is not False:
            continue
        selected = select_format(item.get("formats") or {})
        if not selected:
            continue
        authors = [str(author.get("name") or "").strip() for author in item.get("authors") or []]
        authors = [author for author in authors if author]
        books.append(
            {
                "id": int(item.get("id") or 0),
                "title": str(item.get("title") or "未命名书籍").strip(),
                "authors": authors,
                "languages": [str(value).upper() for value in item.get("languages") or []],
                "download_count": int(item.get("download_count") or 0),
                "download_url": selected["url"],
                "mime_type": selected["mime_type"],
                "extension": selected["extension"],
            }
        )
        if len(books) >= limit:
            break
    return books


def _search_douban_catalog(query: str, limit: int) -> list[dict]:
    params = urllib.parse.urlencode({"q": query})
    payload = get_json(f"{DOUBAN_SUGGEST_URL}?{params}")
    books = []
    for item in payload if isinstance(payload, list) else []:
        try:
            detail_url = validate_catalog_url(str(item.get("url") or ""))
        except ValueError:
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        author = str(item.get("author_name") or "").strip()
        books.append(
            {
                "id": f"douban:{item.get('id') or detail_url}",
                "title": title,
                "authors": [author] if author else [],
                "year": str(item.get("year") or "").strip(),
                "provider": "豆瓣读书",
                "detail_url": detail_url,
            }
        )
        if len(books) >= limit:
            break
    return books


def _search_open_library_catalog(query: str, limit: int) -> list[dict]:
    params = urllib.parse.urlencode(
        {
            "q": query,
            "fields": "key,title,author_name,first_publish_year,isbn,edition_count",
            "limit": limit,
        }
    )
    payload = get_json(f"{OPEN_LIBRARY_SEARCH_URL}?{params}")
    books = []
    for item in payload.get("docs") or [] if isinstance(payload, dict) else []:
        key = str(item.get("key") or "").strip()
        try:
            detail_url = validate_catalog_url(f"https://openlibrary.org{key}")
        except ValueError:
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        authors = [str(author).strip() for author in item.get("author_name") or []]
        books.append(
            {
                "id": f"openlibrary:{key}",
                "title": title,
                "authors": [author for author in authors if author],
                "year": str(item.get("first_publish_year") or "").strip(),
                "provider": "Open Library",
                "detail_url": detail_url,
            }
        )
        if len(books) >= limit:
            break
    return books


def search_catalog_books(query: str, *, limit: int | None = None) -> list[dict]:
    """低频聚合书目元数据；单个 provider 故障时继续使用其他结果。"""
    query = str(query or "").strip()
    if not query:
        return []
    limit = BOOK_SEARCH_LIMIT if limit is None else min(10, max(1, int(limit)))
    merged = []
    seen = set()
    for provider in (_search_douban_catalog, _search_open_library_catalog):
        try:
            candidates = provider(query, limit)
        except Exception as exc:
            logger.warning("书目 provider 查询失败: provider=%s error=%s", provider.__name__, exc)
            continue
        for book in candidates:
            first_author = book["authors"][0] if book["authors"] else ""
            identity = (book["title"].casefold(), first_author.casefold())
            if identity in seen:
                continue
            seen.add(identity)
            merged.append(book)
            if len(merged) >= limit:
                return merged
    return merged


def _safe_file_name(book: dict) -> str:
    title = _SAFE_NAME_RE.sub("_", str(book.get("title") or "book")).strip(" ._") or "book"
    authors = book.get("authors") or []
    author = _SAFE_NAME_RE.sub("_", str(authors[0])).strip(" ._") if authors else ""
    stem = f"{title} - {author}" if author else title
    stem = stem[:110].rstrip(" ._") or f"book-{int(book.get('id') or 0)}"
    return f"{stem}.{book['extension']}"


def _file_signature_valid(filepath: str, extension: str) -> bool:
    with open(filepath, "rb") as fh:
        header = fh.read(128)
    if extension == "epub":
        return header.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"))
    if extension == "pdf":
        return header.startswith(b"%PDF-")
    if extension == "mobi":
        return len(header) >= 68 and b"BOOKMOBI" in header[60:80]
    if extension == "txt":
        if not header or b"\x00" in header:
            return False
        control_count = sum(byte < 9 or 13 < byte < 32 for byte in header)
        return control_count <= max(1, len(header) // 20)
    return False


def download_book(
    book: dict,
    *,
    download_dir: str = BOOK_DOWNLOAD_DIR,
    max_bytes: int = BOOK_DOWNLOAD_MAX_BYTES,
) -> tuple[str, int, str]:
    """流式下载并验证一本书，失败时清理临时文件。"""
    Path(download_dir).mkdir(parents=True, exist_ok=True)
    file_name = _safe_file_name(book)
    fd, filepath = tempfile.mkstemp(prefix="wb_book_", suffix=f".{book['extension']}", dir=download_dir)
    os.close(fd)
    size = 0
    try:
        with open_download(book["download_url"]) as response:
            validate_download_url(response.geturl())
            try:
                declared_size = int(response.headers.get("Content-Length") or "0")
            except (TypeError, ValueError):
                declared_size = 0
            if declared_size > max_bytes:
                raise RuntimeError(f"书籍超过上限 {max_bytes // 1024 // 1024} MiB")
            with open(filepath, "wb") as output:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise RuntimeError(f"书籍超过上限 {max_bytes // 1024 // 1024} MiB")
                    output.write(chunk)
        if size == 0 or not _file_signature_valid(filepath, book["extension"]):
            raise RuntimeError(f"书籍格式校验失败：期望 {book['extension'].upper()}")
        return filepath, size, file_name
    except Exception:
        try:
            os.unlink(filepath)
        except OSError:
            pass
        raise


def copy_to_ingest(filepath: str, file_name: str, ingest_dir: str = BOOK_INGEST_DIR) -> str:
    """先复制为 part 文件，再原子改名交给 CWA watcher。"""
    target_dir = Path(ingest_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _SAFE_NAME_RE.sub("_", os.path.basename(file_name)).strip(" ._") or "book.epub"
    target = target_dir / safe_name
    if target.exists():
        target = target_dir / f"{target.stem}-{int(time.time())}{target.suffix}"
    fd, part_path = tempfile.mkstemp(prefix=".wb_book_", suffix=".part", dir=str(target_dir))
    os.close(fd)
    try:
        shutil.copyfile(filepath, part_path)
        os.chmod(part_path, 0o644)
        os.replace(part_path, target)
        return str(target)
    except Exception:
        try:
            os.unlink(part_path)
        except OSError:
            pass
        raise


def _format_search_reply(books: list[dict]) -> str:
    lines = ["## 📚 公版书搜索结果", ""]
    for index, book in enumerate(books, start=1):
        author = "、".join(book["authors"]) if book["authors"] else "作者未知"
        languages = "/".join(book["languages"]) if book["languages"] else "语言未知"
        lines.append(f"{index}. **{book['title']}**")
        lines.append(f"   - {author} · {languages} · {book['extension'].upper()}")
    lines.extend(["", "发送 `/书籍 序号` 下载，例如 `/书籍 1`。结果 5 分钟内有效。"])
    return "\n".join(lines)


def _format_catalog_reply(books: list[dict], *, downloadable_missing: bool = False) -> str:
    lines = ["## 🔎 已找到书目", ""]
    for index, book in enumerate(books, start=1):
        author = "、".join(book["authors"]) if book["authors"] else "作者未知"
        year = f" · {book['year']}" if book.get("year") else ""
        lines.append(f"{index}. **{book['title']}**")
        lines.append(f"   - {author}{year} · {book['provider']}")
        lines.append(f"   - [查看书目详情]({book['detail_url']})")
    if downloadable_missing:
        lines.extend(
            [
                "",
                "> 已找到书目，但暂无可验证的合法直链。可从详情页购买/借阅，或发送 `/导入书籍` 导入你自有的电子书文件。",
            ]
        )
    return "\n".join(lines)


class BookDownloadPlugin(Plugin):
    name = "bridge-book-download"
    description = "搜索书目、下载公版书并导入自有电子书"
    commands = COMMANDS

    def __init__(self):
        self._sessions: dict[tuple[str, str], dict] = {}
        self._import_sessions: dict[str, dict] = {}
        self._sessions_lock = threading.RLock()

    def get_command_specs(self) -> list[dict]:
        return [
            {"command": "/书籍", "description": "搜索并下载公版书", "usage": "/书籍 <书名、作者或序号>"},
            {"command": "/book", "description": "Search public-domain books", "usage": "/book <query or number>"},
            {"command": "/找书", "description": "搜索全部书目元数据", "usage": "/找书 <书名或作者>"},
            {"command": "/findbook", "description": "Search book metadata", "usage": "/findbook <title or author>"},
            {"command": "/导入书籍", "description": "导入自有电子书到 CWA", "usage": "/导入书籍"},
            {"command": "/importbook", "description": "Import an owned ebook", "usage": "/importbook"},
        ]

    def has_session(self, user_id: str) -> bool:
        with self._sessions_lock:
            session = self._import_sessions.get(user_id)
            if session and session["expires_at"] >= time.time():
                return True
            if session:
                self._import_sessions.pop(user_id, None)
        return False

    def handle(self, payload: dict) -> None:
        from_user = str(payload.get("from_user") or "").strip()
        bot_id = str(payload.get("bot_id") or "").strip()
        command = str(payload.get("command") or "/书籍").strip().lower()
        args = str(payload.get("args") or "").strip()
        if not from_user:
            return
        if command in ("/导入书籍", "/importbook"):
            self._handle_import_command(from_user, args)
            return
        if not args or args in ("帮助", "help"):
            self.send_reply(
                from_user,
                "## 📚 书籍工具\n\n"
                "- 全部书目：`/找书 <书名或作者>`\n"
                "- 公版下载：`/书籍 <书名或作者>` 后发送 `/书籍 <序号>`\n"
                "- 自有文件：发送 `/导入书籍` 后上传 EPUB、PDF、MOBI 或 TXT\n"
                "- 取消：`/书籍 取消`\n\n"
                "自动下载仅提供 Project Gutenberg 公版书。",
            )
            return
        if args in ("取消", "cancel"):
            with self._sessions_lock:
                self._sessions.pop((bot_id, from_user), None)
                self._import_sessions.pop(from_user, None)
            self.send_reply(from_user, "已取消本次书籍搜索。")
            return
        if command in ("/找书", "/findbook"):
            worker = self.catalog_and_reply
            worker_arg = args
        else:
            worker = self.download_and_reply if args.isdigit() else self.search_and_reply
            worker_arg = int(args) if args.isdigit() else args
        threading.Thread(
            target=self._run_worker,
            args=(worker, bot_id, from_user, worker_arg),
            daemon=True,
        ).start()

    def _run_worker(self, worker, bot_id: str, from_user: str, value) -> None:
        acquired = _TASK_SEMAPHORE.acquire(blocking=False)
        if not acquired:
            self.send_reply(from_user, "## ⏳ 书籍任务繁忙\n\n当前已有搜索或下载任务，请稍后再试。")
            return
        try:
            worker(bot_id, from_user, value)
        except Exception as exc:
            logger.exception("书籍命令执行失败")
            self.send_reply(from_user, f"## ⚠️ 书籍任务失败\n\n- **原因**：{exc}")
        finally:
            _TASK_SEMAPHORE.release()

    def _handle_import_command(self, from_user: str, args: str) -> None:
        if args in ("取消", "cancel"):
            with self._sessions_lock:
                self._import_sessions.pop(from_user, None)
            self.send_reply(from_user, "已取消本次书籍导入。")
            return
        if args:
            self.send_reply(from_user, "请直接发送 `/导入书籍`，然后在 5 分钟内上传电子书文件。")
            return
        with self._sessions_lock:
            self._import_sessions[from_user] = {"expires_at": time.time() + BOOK_SESSION_TTL}
        self.send_reply(
            from_user,
            "## 📥 等待电子书文件\n\n请在 5 分钟内发送你有权使用的 EPUB、PDF、MOBI 或 TXT 文件，大小不超过 20 MiB。",
        )

    def on_message(self, event) -> None:
        from_user = str(event.data.get("from_user") or "").strip()
        text = str(event.data.get("text") or "")
        if not from_user or text.startswith("/") or not self.has_session(from_user):
            return
        media_paths = event.data.get("media_paths") or []
        if not media_paths:
            if text.startswith(("[文件过大:", "[文件下载失败:", "[文件缺少解密参数:")):
                with self._sessions_lock:
                    self._import_sessions.pop(from_user, None)
                self.send_reply(from_user, f"## ⚠️ 书籍导入失败\n\n- **原因**：微信文件接收失败（{text}）")
                return
            self.send_reply(from_user, "当前正在等待电子书文件；如需退出，请发送 `/导入书籍 取消`。")
            return

        original_name = ""
        for item in (event.data.get("msg") or {}).get("item_list") or []:
            if item.get("type") == 4:
                original_name = os.path.basename(str((item.get("file_item") or {}).get("file_name") or ""))
                break
        extension = Path(original_name).suffix.lower().lstrip(".")
        cached_path = ""
        try:
            media_dir = Path(getattr(self.bridge, "_media_dir", "")).resolve()
            cached = (media_dir / os.path.basename(str(media_paths[0]))).resolve()
            if cached.parent != media_dir or not cached.is_file():
                raise RuntimeError("未找到已缓存的微信文件")
            cached_path = str(cached)
            if extension not in {value for _, value in _FORMAT_PRIORITY}:
                raise RuntimeError("仅支持 EPUB、PDF、MOBI 或 TXT 文件")
            size = cached.stat().st_size
            if size > BOOK_IMPORT_MAX_BYTES:
                raise RuntimeError(f"书籍超过上限 {BOOK_IMPORT_MAX_BYTES // 1024 // 1024} MiB")
            if not _file_signature_valid(cached_path, extension):
                raise RuntimeError(f"文件内容与 {extension.upper()} 扩展名不匹配")
            ingest_path = copy_to_ingest(cached_path, original_name, BOOK_INGEST_DIR)
            self.send_reply(
                from_user,
                "## ✅ 书籍导入成功\n\n"
                f"- **文件**：{os.path.basename(ingest_path)}\n"
                f"- **格式**：{extension.upper()}\n"
                f"- **大小**：{size / 1024 / 1024:.2f} MiB\n"
                "- **书库**：已提交 Calibre-Web Automated 入库",
            )
        except Exception as exc:
            self.send_reply(from_user, f"## ⚠️ 书籍导入失败\n\n- **原因**：{exc}")
        finally:
            with self._sessions_lock:
                self._import_sessions.pop(from_user, None)
            if cached_path:
                try:
                    os.unlink(cached_path)
                except OSError:
                    pass

    def catalog_and_reply(self, bot_id: str, from_user: str, query: str) -> None:
        books = search_catalog_books(query)
        if not books:
            self.send_reply(from_user, "## 🔍 未找到书目\n\n可以尝试完整书名、作者名或更短的关键词。")
            return
        self.send_reply(from_user, _format_catalog_reply(books))

    def search_and_reply(self, bot_id: str, from_user: str, query: str) -> None:
        try:
            books = search_books(query)
        except Exception as exc:
            logger.warning("Gutendex 查询失败，降级到书目检索: %s", exc)
            books = []
        if not books:
            catalog = search_catalog_books(query)
            if catalog:
                self.send_reply(from_user, _format_catalog_reply(catalog, downloadable_missing=True))
            else:
                self.send_reply(
                    from_user,
                    "## 🔍 未找到书目或可下载公版书\n\n可以尝试完整书名、作者名或更短的关键词。",
                )
            return
        with self._sessions_lock:
            self._sessions[(bot_id, from_user)] = {
                "expires_at": time.time() + BOOK_SESSION_TTL,
                "books": books,
            }
        self.send_reply(from_user, _format_search_reply(books))

    def download_and_reply(self, bot_id: str, from_user: str, index: int) -> None:
        key = (bot_id, from_user)
        with self._sessions_lock:
            session = self._sessions.get(key)
            if session and session["expires_at"] < time.time():
                self._sessions.pop(key, None)
                session = None
        if not session:
            self.send_reply(from_user, "搜索结果已过期，请重新发送 `/书籍 <关键词>`。")
            return
        books = session["books"]
        if index < 1 or index > len(books):
            self.send_reply(from_user, f"序号无效，请输入 1-{len(books)}。")
            return
        if not getattr(self, "bridge", None) or not hasattr(self.bridge, "send_file_path"):
            self.send_reply(from_user, "书籍下载失败：Bridge 未提供文件路径发送能力。")
            return

        book = books[index - 1]
        filepath = ""
        try:
            self.send_reply(from_user, f"## ⏳ 正在下载\n\n- **书名**：{book['title']}\n- **格式**：{book['extension'].upper()}")
            filepath, size, file_name = download_book(book)
            result = self.bridge.send_file_path(
                from_user,
                filepath,
                file_name=file_name,
                text=f"Project Gutenberg 公版书 · #{book['id']}",
            )
            if not result.get("ok"):
                raise RuntimeError(result.get("error") or "微信文件发送失败")
            ingest_path = copy_to_ingest(filepath, file_name, BOOK_INGEST_DIR)
            with self._sessions_lock:
                self._sessions.pop(key, None)
            self.send_reply(
                from_user,
                "## ✅ 书籍已发送\n\n"
                f"- **书名**：{book['title']}\n"
                f"- **格式**：{book['extension'].upper()}\n"
                f"- **大小**：{size / 1024 / 1024:.2f} MiB\n"
                "- **书库**：已提交 Calibre-Web Automated 入库",
            )
            logger.info("书籍已发送并提交入库: id=%s file=%s ingest=%s", book["id"], file_name, ingest_path)
        finally:
            if filepath:
                try:
                    os.unlink(filepath)
                except OSError:
                    pass


PLUGIN_CLASS = BookDownloadPlugin


if __name__ == "__main__":
    print(json.dumps({"commands": COMMANDS}, ensure_ascii=False))
