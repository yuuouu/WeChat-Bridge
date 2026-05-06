from __future__ import annotations


def md_inline(value) -> str:
    """将值包装为 Markdown 行内代码，转义反引号和换行。"""
    text = str(value if value is not None else "N/A")
    text = text.replace("\r", " ").replace("\n", " ").replace("`", "\\`")
    return f"`{text}`"
