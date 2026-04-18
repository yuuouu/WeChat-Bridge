"""Markdown 文本处理。"""

import re


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


def should_plainify_markdown(flag) -> bool:
    """
    仅在显式要求 plain 降级时才做 Markdown -> 纯文本转换。
    历史上的 markdown=true 现在视为“允许原样发送 Markdown 文本”。
    """
    if isinstance(flag, str):
        return flag.strip().lower() in ("plain", "downgrade", "degrade", "text")
    return False

