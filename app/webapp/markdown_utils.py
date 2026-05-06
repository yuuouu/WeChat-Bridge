from __future__ import annotations

"""Markdown 文本处理。"""

import os
import re

_SEPARATOR_RE = re.compile(r"^\s*[━─╌┄┈┉—\-_*]{3,}\s*$")
_BRACKET_TITLE_RE = re.compile(r"^\s*[【\[]([^】\]]{1,120})[】\]]\s*$")
_PINNED_TITLE_RE = re.compile(r"^\s*(?:📌|📢|🔔|📝)\s*(.{1,120})\s*$")
_BULLET_RE = re.compile(r"^(\s*)(?:[•●▪▫◦‣⁃]|🔹)\s+(.+)$")
_ARROW_ITEM_RE = re.compile(r"^\s*(?:➜|→|👉)\s+(.+)$")

_MARKDOWN_ALIASES = {
    "0",
    "1",
    "false",
    "true",
    "no",
    "yes",
    "off",
    "on",
    "raw",
    "preserve",
    "none",
    "md",
    "markdown",
}
_NORMALIZE_ALIASES = {
    "normalize",
    "normalise",
    "normalized",
    "normalised",
    "format",
    "formatted",
}
_PLAIN_ALIASES = {
    "plain",
    "downgrade",
    "degrade",
    "text",
}


def _append_markdown_block(lines: list[str], line: str):
    if lines and lines[-1] != "":
        lines.append("")
    lines.append(line)
    lines.append("")


def markdown_to_plain(text: str) -> str:
    """
    将 Markdown 格式的文本降级为微信友好的纯文本。
    - **bold** / __bold__  → bold
    - *italic* / _italic_  → italic
    - [link text](url)     → link text (url)
    - ![alt](url)          → [图片: alt]
    - # 标题               → 【标题】
    - `code`               → code
    - ```code block```     → code block
    - > 引用               →「引用」
    - - / * 列表           → • 列表项
    - --- / ***            → ————
    """
    if not text:
        return text

    text = re.sub(r"```[\w]*\n?(.*?)```", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"[图片: \1]", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"【\1】", text, flags=re.MULTILINE)
    text = re.sub(r"\*{3}(.+?)\*{3}", r"\1", text)
    text = re.sub(r"\*{2}(.+?)\*{2}", r"\1", text)
    text = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", text)
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    text = re.sub(r"^>\s?(.+)$", r"「\1」", text, flags=re.MULTILINE)
    text = re.sub(r"^[\s]*[-*+]\s+", "• ", text, flags=re.MULTILINE)
    text = re.sub(r"^[\s]*(\d+)\.\s+", r"\1. ", text, flags=re.MULTILINE)
    text = re.sub(r"^[-*_]{3,}\s*$", "————————", text, flags=re.MULTILINE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_markdown_text(text: str) -> str:
    """
    将外部系统常见的“微信友好纯文本”整理为稳定 Markdown。
    主要面向 iStoreOS/luci-app-wechatpush、青龙、Bark 等通知：
    - 【标题】 / 📌 标题      → ## 标题
    - ━━━━━ / ┈┈┈ / ---       → ---
    - • / 🔹 / 👉 项目        → - 项目
    - 自动清理多余空行和行尾空白
    """
    if not text:
        return text

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    output: list[str] = []
    seen_content = False

    for raw_line in normalized.split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped:
            if output and output[-1] != "":
                output.append("")
            continue

        if _SEPARATOR_RE.match(stripped):
            _append_markdown_block(output, "---")
            seen_content = True
            continue

        bracket_title = _BRACKET_TITLE_RE.match(stripped)
        pinned_title = _PINNED_TITLE_RE.match(stripped)
        if not seen_content and (bracket_title or pinned_title):
            title = (bracket_title or pinned_title).group(1).strip()
            _append_markdown_block(output, f"## {title}")
            seen_content = True
            continue

        bullet = _BULLET_RE.match(line)
        arrow_item = _ARROW_ITEM_RE.match(stripped)
        if bullet:
            indent = bullet.group(1)
            line = f"{indent}- {bullet.group(2).strip()}"
        elif arrow_item:
            line = f"- {arrow_item.group(1).strip()}"

        output.append(line)
        seen_content = True

    result = "\n".join(output)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def should_plainify_markdown(flag) -> bool:
    """
    仅在显式要求 plain 降级时才做 Markdown -> 纯文本转换。
    历史上的 markdown=true 现在视为“允许原样发送 Markdown 文本”。
    """
    return _coerce_markdown_mode(flag) == "plain"


def default_markdown_mode() -> str:
    return (
        os.environ.get("WECHAT_BRIDGE_MARKDOWN_MODE")
        or os.environ.get("MARKDOWN_MODE")
        or os.environ.get("MESSAGE_MARKDOWN_MODE")
        or ""
    )


def _coerce_markdown_mode(flag) -> str:
    """
    将历史参数别名收敛成 3 种内部模式。
    对外推荐只使用：markdown、normalize、plain。
    """
    if flag in (None, ""):
        return ""
    if isinstance(flag, bool):
        return "markdown"
    if not isinstance(flag, str):
        return ""

    value = flag.strip().lower()
    if value in _PLAIN_ALIASES:
        return "plain"
    if value in _NORMALIZE_ALIASES:
        return "normalize"
    if value in _MARKDOWN_ALIASES:
        return "markdown"
    return ""


def resolve_markdown_mode(*flags) -> str:
    """
    解析 Markdown 处理模式。
    请求参数优先；环境变量只作为默认值；最终模式仅为 markdown/normalize/plain。
    """
    explicit_modes = [_coerce_markdown_mode(flag) for flag in flags if flag not in (None, "")]
    explicit_modes = [mode for mode in explicit_modes if mode]
    if explicit_modes:
        if "plain" in explicit_modes:
            return "plain"
        if "normalize" in explicit_modes:
            return "normalize"
        return "markdown"

    return _coerce_markdown_mode(default_markdown_mode()) or "markdown"


def apply_markdown_mode(text: str, *flags) -> str:
    """
    按请求参数和环境变量处理 Markdown。
    公开模式：默认 Markdown、normalize 整理、plain 降级。
    """
    mode = resolve_markdown_mode(*flags)
    if mode == "plain":
        return markdown_to_plain(text)
    if mode == "normalize":
        return normalize_markdown_text(text)
    return text
