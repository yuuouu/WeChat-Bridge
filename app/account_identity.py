from __future__ import annotations

"""Account storage identity helpers."""

import re


def safe_account_dir_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.@-]+", "_", value or "").strip("._/")
    return safe or "unknown"


def account_storage_dir_name(*, bot_id: str = "", ilink_user_id: str = "") -> str:
    """Return a stable local data directory name for an iLink account."""
    stable_id = (ilink_user_id or "").strip() or (bot_id or "").strip()
    return safe_account_dir_name(stable_id)
