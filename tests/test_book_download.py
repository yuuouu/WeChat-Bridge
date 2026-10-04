from __future__ import annotations

import io
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
ROOT = APP_ROOT.parent
for path in (ROOT, APP_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from examples import bridge_book_download


class _FakeResponse:
    def __init__(self, data: bytes, *, url: str, content_type: str, content_length: int | None = None):
        self._stream = io.BytesIO(data)
        self._url = url
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(data) if content_length is None else content_length),
        }

    def read(self, size=-1):
        return self._stream.read(size)

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeBridge:
    def __init__(self):
        self.sent_files = []

    def send_file_path(self, to, filepath, *, file_name="", text=""):
        self.sent_files.append((to, filepath, file_name, text, os.path.getsize(filepath)))
        return {"ok": True, "file_name": file_name}


class BookDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)

    def test_select_format_prefers_epub_and_requires_gutenberg_https(self):
        formats = {
            "application/pdf": "https://www.gutenberg.org/files/11/11-pdf.pdf",
            "application/epub+zip": "https://www.gutenberg.org/ebooks/11.epub3.images",
            "text/plain; charset=utf-8": "https://www.gutenberg.org/files/11/11-0.txt",
        }

        selected = bridge_book_download.select_format(formats)

        self.assertEqual(selected["extension"], "epub")
        self.assertEqual(selected["mime_type"], "application/epub+zip")

    def test_validate_download_url_rejects_non_gutenberg_and_non_https(self):
        rejected = [
            "http://www.gutenberg.org/files/11/11.epub",
            "https://gutenberg.org.evil.example/book.epub",
            "https://127.0.0.1/book.epub",
            "https://user:pass@www.gutenberg.org/book.epub",
            "https://www.gutenberg.org:8443/book.epub",
        ]

        for url in rejected:
            with self.subTest(url=url), self.assertRaises(ValueError):
                bridge_book_download.validate_download_url(url)

        self.assertEqual(
            bridge_book_download.validate_download_url("https://www.gutenberg.org/ebooks/11.epub3.images"),
            "https://www.gutenberg.org/ebooks/11.epub3.images",
        )

    def test_search_books_filters_copyright_and_unusable_formats(self):
        payload = {
            "results": [
                {
                    "id": 11,
                    "title": "Alice's Adventures in Wonderland",
                    "authors": [{"name": "Carroll, Lewis"}],
                    "languages": ["en"],
                    "copyright": False,
                    "formats": {"application/epub+zip": "https://www.gutenberg.org/ebooks/11.epub3.images"},
                    "download_count": 100,
                },
                {
                    "id": 12,
                    "title": "Copyrighted",
                    "authors": [],
                    "languages": ["en"],
                    "copyright": True,
                    "formats": {"application/pdf": "https://www.gutenberg.org/files/12/12.pdf"},
                },
                {
                    "id": 13,
                    "title": "HTML only",
                    "authors": [],
                    "languages": ["en"],
                    "copyright": False,
                    "formats": {"text/html": "https://www.gutenberg.org/ebooks/13.html.images"},
                },
            ]
        }

        with patch.object(bridge_book_download, "get_json", return_value=payload) as mock_get:
            books = bridge_book_download.search_books("Alice")

        self.assertEqual(len(books), 1)
        self.assertEqual(books[0]["id"], 11)
        self.assertEqual(books[0]["extension"], "epub")
        requested_url = mock_get.call_args.args[0]
        self.assertIn("search=Alice", requested_url)
        self.assertIn("copyright=false", requested_url)

    def test_search_catalog_books_merges_douban_and_open_library(self):
        douban = [
            {
                "id": "34836531",
                "title": "了不起的我",
                "author_name": "陈海贤",
                "year": "2019",
                "url": "https://book.douban.com/subject/34836531/",
            }
        ]
        open_library = {
            "docs": [
                {
                    "key": "/works/OL1W",
                    "title": "The Great Gatsby",
                    "author_name": ["F. Scott Fitzgerald"],
                    "first_publish_year": 1925,
                }
            ]
        }

        with patch.object(bridge_book_download, "get_json", side_effect=[douban, open_library]):
            books = bridge_book_download.search_catalog_books("了不起的我", limit=5)

        self.assertEqual(books[0]["title"], "了不起的我")
        self.assertEqual(books[0]["authors"], ["陈海贤"])
        self.assertEqual(books[0]["provider"], "豆瓣读书")
        self.assertEqual(books[1]["provider"], "Open Library")

    def test_search_catalog_books_isolates_provider_failure(self):
        open_library = {
            "docs": [
                {
                    "key": "/works/OL1W",
                    "title": "Fallback Book",
                    "author_name": ["Author"],
                    "first_publish_year": 2020,
                }
            ]
        }

        with patch.object(
            bridge_book_download,
            "get_json",
            side_effect=[TimeoutError("douban timeout"), open_library],
        ):
            books = bridge_book_download.search_catalog_books("Fallback", limit=5)

        self.assertEqual([book["title"] for book in books], ["Fallback Book"])

    def test_plugin_falls_back_to_catalog_when_no_downloadable_book(self):
        plugin = bridge_book_download.BookDownloadPlugin()
        replies = []
        plugin._send_func = lambda to, text, source="plugin": replies.append(text) or {"ok": True}
        catalog = [
            {
                "id": "douban:34836531",
                "title": "了不起的我",
                "authors": ["陈海贤"],
                "year": "2019",
                "provider": "豆瓣读书",
                "detail_url": "https://book.douban.com/subject/34836531/",
            }
        ]

        with (
            patch.object(bridge_book_download, "search_books", return_value=[]),
            patch.object(bridge_book_download, "search_catalog_books", return_value=catalog),
        ):
            plugin.search_and_reply("bot-1", "uid-1", "了不起的我")

        self.assertIn("已找到书目", replies[-1])
        self.assertIn("了不起的我", replies[-1])
        self.assertIn("暂无可验证的合法直链", replies[-1])

    def test_download_book_streams_valid_epub_and_rejects_oversize(self):
        book = {
            "id": 11,
            "title": "Alice",
            "authors": ["Carroll, Lewis"],
            "download_url": "https://www.gutenberg.org/ebooks/11.epub3.images",
            "mime_type": "application/epub+zip",
            "extension": "epub",
        }
        response = _FakeResponse(
            b"PK\x03\x04" + b"ebook-data",
            url=book["download_url"],
            content_type="application/epub+zip",
        )

        with patch.object(bridge_book_download, "open_download", return_value=response):
            filepath, size, file_name = bridge_book_download.download_book(
                book,
                download_dir=self.tempdir.name,
                max_bytes=1024,
            )

        self.assertEqual(size, len(b"PK\x03\x04ebook-data"))
        self.assertEqual(Path(filepath).read_bytes(), b"PK\x03\x04ebook-data")
        self.assertTrue(file_name.endswith(".epub"))

        oversized = _FakeResponse(
            b"PK\x03\x04oversized",
            url=book["download_url"],
            content_type="application/epub+zip",
            content_length=2048,
        )
        with (
            patch.object(bridge_book_download, "open_download", return_value=oversized),
            self.assertRaisesRegex(RuntimeError, "超过上限"),
        ):
            bridge_book_download.download_book(book, download_dir=self.tempdir.name, max_bytes=1024)

    def test_download_book_rejects_invalid_signature_and_cleans_temp_file(self):
        book = {
            "id": 11,
            "title": "Alice",
            "authors": ["Carroll, Lewis"],
            "download_url": "https://www.gutenberg.org/ebooks/11.epub3.images",
            "mime_type": "application/epub+zip",
            "extension": "epub",
        }
        response = _FakeResponse(
            b"<html>blocked</html>",
            url=book["download_url"],
            content_type="text/html",
        )

        with (
            patch.object(bridge_book_download, "open_download", return_value=response),
            self.assertRaisesRegex(RuntimeError, "格式校验失败"),
        ):
            bridge_book_download.download_book(book, download_dir=self.tempdir.name, max_bytes=1024)

        self.assertEqual(list(Path(self.tempdir.name).glob("wb_book_*")), [])

    def test_ingest_copy_is_atomic_and_preserves_source(self):
        source = Path(self.tempdir.name) / "source.epub"
        source.write_bytes(b"PK\x03\x04ebook")
        ingest_dir = Path(self.tempdir.name) / "ingest"

        target = bridge_book_download.copy_to_ingest(str(source), "Alice.epub", str(ingest_dir))

        self.assertTrue(source.exists())
        self.assertEqual(Path(target).read_bytes(), source.read_bytes())
        self.assertEqual(list(ingest_dir.glob("*.part")), [])

    def test_plugin_search_then_download_sends_file_and_ingests(self):
        plugin = bridge_book_download.BookDownloadPlugin()
        plugin.bridge = _FakeBridge()
        replies = []
        plugin._send_func = lambda to, text, source="plugin": replies.append((to, text)) or {"ok": True}
        book = {
            "id": 11,
            "title": "Alice",
            "authors": ["Carroll, Lewis"],
            "languages": ["en"],
            "download_count": 100,
            "download_url": "https://www.gutenberg.org/ebooks/11.epub3.images",
            "mime_type": "application/epub+zip",
            "extension": "epub",
        }
        source = Path(self.tempdir.name) / "Alice.epub"
        source.write_bytes(b"PK\x03\x04ebook")
        ingest_dir = Path(self.tempdir.name) / "ingest"

        with patch.object(bridge_book_download, "search_books", return_value=[book]):
            plugin.search_and_reply("bot-1", "uid-1", "Alice")
        with (
            patch.object(
                bridge_book_download,
                "download_book",
                return_value=(str(source), source.stat().st_size, "Alice.epub"),
            ),
            patch.object(bridge_book_download, "BOOK_INGEST_DIR", str(ingest_dir)),
        ):
            plugin.download_and_reply("bot-1", "uid-1", 1)

        self.assertIn("Alice", replies[0][1])
        self.assertEqual(len(plugin.bridge.sent_files), 1)
        self.assertTrue((ingest_dir / "Alice.epub").exists())
        self.assertFalse(source.exists())
        self.assertIn("书籍已发送", replies[-1][1])

    def test_expired_session_cannot_download(self):
        plugin = bridge_book_download.BookDownloadPlugin()
        replies = []
        plugin._send_func = lambda to, text, source="plugin": replies.append(text) or {"ok": True}
        plugin._sessions[("bot-1", "uid-1")] = {
            "expires_at": time.time() - 1,
            "books": [{"id": 11}],
        }

        plugin.download_and_reply("bot-1", "uid-1", 1)

        self.assertIn("已过期", replies[-1])

    def test_import_session_accepts_owned_epub_and_cleans_cache(self):
        plugin = bridge_book_download.BookDownloadPlugin()
        replies = []
        plugin._send_func = lambda to, text, source="plugin": replies.append(text) or {"ok": True}
        media_dir = Path(self.tempdir.name) / "media"
        ingest_dir = Path(self.tempdir.name) / "ingest"
        media_dir.mkdir()
        cached = media_dir / "cached.bin"
        cached.write_bytes(b"PK\x03\x04owned-ebook")
        plugin.bridge = SimpleNamespace(_media_dir=str(media_dir))

        plugin.handle(
            {
                "bot_id": "bot-1",
                "from_user": "uid-1",
                "command": "/导入书籍",
                "args": "",
            }
        )
        event = SimpleNamespace(
            data={
                "bot_id": "bot-1",
                "from_user": "uid-1",
                "text": "[文件: Owned Book.epub]",
                "media_paths": ["cached.bin"],
                "msg": {"item_list": [{"type": 4, "file_item": {"file_name": "Owned Book.epub"}}]},
            }
        )

        with patch.object(bridge_book_download, "BOOK_INGEST_DIR", str(ingest_dir)):
            plugin.on_message(event)

        self.assertTrue((ingest_dir / "Owned Book.epub").exists())
        self.assertFalse(cached.exists())
        self.assertFalse(plugin.has_session("uid-1"))
        self.assertIn("导入成功", replies[-1])

    def test_import_session_rejects_mismatched_extension_and_cleans_cache(self):
        plugin = bridge_book_download.BookDownloadPlugin()
        replies = []
        plugin._send_func = lambda to, text, source="plugin": replies.append(text) or {"ok": True}
        media_dir = Path(self.tempdir.name) / "media"
        media_dir.mkdir()
        cached = media_dir / "cached.bin"
        cached.write_bytes(b"<html>not an epub</html>")
        plugin.bridge = SimpleNamespace(_media_dir=str(media_dir))
        plugin.handle(
            {
                "bot_id": "bot-1",
                "from_user": "uid-1",
                "command": "/导入书籍",
                "args": "",
            }
        )
        event = SimpleNamespace(
            data={
                "bot_id": "bot-1",
                "from_user": "uid-1",
                "text": "[文件: Fake.epub]",
                "media_paths": ["cached.bin"],
                "msg": {"item_list": [{"type": 4, "file_item": {"file_name": "Fake.epub"}}]},
            }
        )

        plugin.on_message(event)

        self.assertFalse(cached.exists())
        self.assertFalse(plugin.has_session("uid-1"))
        self.assertIn("内容与 EPUB 扩展名不匹配", replies[-1])

    def test_import_session_reports_rejected_inbound_file(self):
        plugin = bridge_book_download.BookDownloadPlugin()
        replies = []
        plugin._send_func = lambda to, text, source="plugin": replies.append(text) or {"ok": True}
        plugin.handle(
            {
                "bot_id": "bot-1",
                "from_user": "uid-1",
                "command": "/导入书籍",
                "args": "",
            }
        )

        plugin.on_message(
            SimpleNamespace(
                data={
                    "bot_id": "bot-1",
                    "from_user": "uid-1",
                    "text": "[文件过大:Huge.epub]",
                    "media_paths": [],
                    "msg": {},
                }
            )
        )

        self.assertFalse(plugin.has_session("uid-1"))
        self.assertIn("微信文件接收失败", replies[-1])


if __name__ == "__main__":
    unittest.main()
