from __future__ import annotations

"""Privacy-preserving, opt-out usage telemetry."""

import hashlib
import json
import os
import platform
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2
KNOWN_AI_PROVIDERS = {"openai", "gemini", "claude", "deepseek", "minimax"}


def _bucket(value: int, boundaries: tuple[tuple[int, str], ...], fallback: str) -> str:
    for upper, label in boundaries:
        if value <= upper:
            return label
    return fallback


def _atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    try:
        os.chmod(tmp_path, 0o600)
    except OSError:
        pass
    os.replace(tmp_path, path)


class TelemetryClient:
    def __init__(self, data_dir: str, base_url: str, version: str):
        self.data_dir = Path(data_dir).resolve()
        self.base_url = base_url.rstrip("/")
        self.version = version
        self.state_path = self.data_dir / ".telemetry_state.json"
        self.install_pending_path = self.data_dir / ".install_pending"
        self.process_event_id = uuid.uuid4().hex
        self.state = self._load_state()

    def _load_state(self) -> dict:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("install_id"):
                return value
        except (OSError, ValueError, TypeError):
            pass
        return {"install_id": uuid.uuid4().hex, "created_at": int(time.time())}

    def _save_state(self) -> None:
        _atomic_write_json(self.state_path, self.state)

    def _anonymous_id(self, scope: str) -> str:
        raw = f"wechat-bridge:{scope}:{self.state['install_id']}".encode()
        return hashlib.sha256(raw).hexdigest()[:32]

    def _dimensions(self, config: dict, accounts_count: int, plugins_count: int) -> dict:
        created_at = int(self.state.get("created_at") or time.time())
        age_days = max(0, int((time.time() - created_at) / 86400))
        provider = str(config.get("provider") or "none").lower() if config.get("enabled") else "none"
        if provider not in KNOWN_AI_PROVIDERS and provider != "none":
            provider = "other"

        features = []
        if config.get("enabled"):
            features.append("ai")
        if bool(config.get("webhook_enabled")) and config.get("webhook_url"):
            features.append("webhook")
        if plugins_count > 0:
            features.append("plugins")
        if accounts_count > 1:
            features.append("multi_account")
        if os.path.exists("/.dockerenv"):
            features.append("docker")

        return {
            "v": self.version,
            "os": platform.system().lower(),
            "arch": platform.machine(),
            "py": f"{sys.version_info.major}.{sys.version_info.minor}",
            "mode": "docker" if os.path.exists("/.dockerenv") else "native",
            "uptime_bucket": _bucket(
                age_days,
                ((1, "0-1d"), (7, "2-7d"), (30, "8-30d"), (90, "31-90d")),
                "90d+",
            ),
            "accounts_bucket": _bucket(accounts_count, ((0, "0"), (1, "1"), (2, "2")), "3+"),
            "plugins_bucket": _bucket(plugins_count, ((0, "0"), (1, "1"), (2, "2")), "3+"),
            "ai_provider": provider,
            "webhook_enabled": "true" if bool(config.get("webhook_enabled")) and config.get("webhook_url") else "false",
            "features": features,
        }

    def _post(self, payload: dict) -> bool:
        body = json.dumps(payload, separators=(",", ":")).encode()
        request = urllib.request.Request(
            f"{self.base_url}/telemetry",
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "WeChat-Bridge-Telemetry/2"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return 200 <= response.status < 300
        except Exception:
            return False

    def report(self, config: dict, accounts_count: int, plugins_count: int, process_start: bool = False) -> bool:
        now = datetime.now(timezone.utc)
        day = now.strftime("%Y-%m-%d")
        iso_calendar = now.isocalendar()
        week = f"{iso_calendar.year}-W{iso_calendar.week:02d}"
        dimensions = self._dimensions(config, accounts_count, plugins_count)
        heartbeat = {
            "schema": SCHEMA_VERSION,
            "event": "heartbeat",
            "day": day,
            "week": week,
            "daily_id": self._anonymous_id(f"day:{day}"),
            "weekly_id": self._anonymous_id(f"week:{week}"),
            **dimensions,
        }
        heartbeat_ok = self._post(heartbeat)

        process_start_ok = True
        if process_start:
            process_start_ok = self._post(
                {
                    "schema": SCHEMA_VERSION,
                    "event": "process_start",
                    "day": day,
                    "event_id": self.process_event_id,
                    **dimensions,
                }
            )

        if not self.state.get("first_seen_sent"):
            if self._post(
                {
                    "schema": SCHEMA_VERSION,
                    "event": "first_seen",
                    "day": day,
                    "event_id": self._anonymous_id("first_seen"),
                    **dimensions,
                }
            ):
                self.state["first_seen_sent"] = True

        if self.install_pending_path.exists() and not self.state.get("install_success_sent"):
            try:
                install_mode = self.install_pending_path.read_text(encoding="utf-8").strip().lower()
            except OSError:
                install_mode = "unknown"
            if install_mode not in ("docker", "python", "windows"):
                install_mode = "unknown"
            if self._post(
                {
                    "schema": SCHEMA_VERSION,
                    "event": "install_success",
                    "day": day,
                    "event_id": self._anonymous_id("install_success"),
                    "install_mode": install_mode,
                    **dimensions,
                }
            ):
                self.state["install_success_sent"] = True
                try:
                    self.install_pending_path.unlink()
                except OSError:
                    pass

        previous_version = self.state.get("last_version")
        if previous_version and previous_version != self.version:
            upgrade_id = self._anonymous_id(f"upgrade:{previous_version}:{self.version}")
            if self._post(
                {
                    "schema": SCHEMA_VERSION,
                    "event": "upgrade",
                    "day": day,
                    "event_id": upgrade_id,
                    "from_v": str(previous_version)[:30],
                    "to_v": self.version,
                    **dimensions,
                }
            ):
                self.state["last_version"] = self.version
        elif heartbeat_ok:
            self.state["last_version"] = self.version

        self._save_state()
        return heartbeat_ok and process_start_ok
