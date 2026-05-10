#!/usr/bin/env python3
"""
Bridge Code Agent — 通过微信远程操控 Mac 上的 AI CLI（Gemini / Claude / Codex）。

作为 webhook_manager 插件运行（推荐）：
    python3 examples/webhook_manager.py          ← 自动发现并加载本插件

单独运行（仅加载本插件）：
    python3 examples/bridge_code_agent.py

环境变量：
    BRIDGE_BASE_URL        Bridge 地址（默认 http://127.0.0.1:5200）
    BRIDGE_API_TOKEN       Bridge API Token
    WEBHOOK_LISTEN_HOST    监听地址（默认 0.0.0.0）
    WEBHOOK_LISTEN_PORT    监听端口（默认 18082）
    SESSION_TIMEOUT_MINUTES  空闲会话超时（默认 30 分钟）
    GEMINI_TIMEOUT         CLI 单次执行超时秒数（默认 180）
    ALLOWED_USERS          白名单 user_id，逗号分隔（空=不限制）
    DEFAULT_BACKEND        默认 AI 后端：gemini / claude / codex（默认 gemini）

在微信里：
    /code <项目名> [后端]  → 进入项目，开启 AI 会话
    /switch <后端>         → 切换 AI 后端，CLI session 重置
    /cli <参数>            → 透传给当前后端 CLI 原生执行
    /exit                  → 退出当前会话
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

BRIDGE_BASE_URL = os.environ.get("BRIDGE_BASE_URL", "http://127.0.0.1:5200").rstrip("/")
BRIDGE_API_TOKEN = os.environ.get("BRIDGE_API_TOKEN", "").strip()
SESSION_TIMEOUT = int(os.environ.get("SESSION_TIMEOUT_MINUTES", "30")) * 60
CLI_TIMEOUT = int(os.environ.get("GEMINI_TIMEOUT", "180"))
ALLOWED_USERS: set[str] = {u.strip() for u in os.environ.get("ALLOWED_USERS", "").split(",") if u.strip()}

_SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_MAP_FILE = _SCRIPT_DIR / "project_map.json"
PROJECT_MAP_EXAMPLE = _SCRIPT_DIR / "project_map.json.example"

START_COMMAND = "/code"
EXIT_COMMANDS = {"/exit"}
SWITCH_COMMAND = "/switch"
CLI_COMMAND = "/cli"
MAX_CHUNK = 4500

# ── CLI 后端配置 ──────────────────────────────────────────────────────────────

CLI_BACKENDS: dict[str, str] = {
    "gemini": "Gemini",
    "claude": "Claude Code",
    "codex": "Codex",
}
CLI_BINARIES: dict[str, str] = {
    "gemini": "gemini",
    "claude": "claude",
    "codex": "codex",
}

DEFAULT_BACKEND = os.environ.get("DEFAULT_BACKEND", "gemini")
if DEFAULT_BACKEND not in CLI_BACKENDS:
    DEFAULT_BACKEND = "gemini"


def _find_bin(name: str) -> str:
    """Resolve a CLI binary name to its full path.

    On Windows, npm-installed CLIs live as .cmd wrappers in PATH; shutil.which()
    finds them via PATHEXT so subprocess can call them without shell=True.
    Falls back to the bare name if not found (preserves the FileNotFoundError path).
    """
    return shutil.which(name) or name


def _build_cmd(backend: str, prompt: str, cwd: str, *, resume: bool) -> list[str]:
    """构造 AI CLI 调用命令。Claude 使用 JSON 模式以获取 token 用量。"""
    if backend == "gemini":
        cmd = [_find_bin("gemini"), "-p", prompt, "--approval-mode", "yolo", "--output-format", "text"]
        if resume:
            cmd += ["--resume", "latest"]
        return cmd

    if backend == "claude":
        # JSON 模式：response 含 result / usage / total_cost_usd
        cmd = [_find_bin("claude"), "-p", prompt, "--dangerously-skip-permissions", "--output-format", "json"]
        if resume:
            cmd += ["-c"]
        return cmd

    if backend == "codex":
        base_flags = ["--dangerously-bypass-approvals-and-sandbox", "--color", "never", "-C", cwd]
        if resume:
            return [_find_bin("codex"), "exec", "resume", "--last", prompt] + base_flags
        return [_find_bin("codex"), "exec", prompt] + base_flags

    raise ValueError(f"未知后端: {backend}")


# ── Project map ──────────────────────────────────────────────────────────────


def _load_project_map() -> dict[str, str]:
    if not PROJECT_MAP_FILE.exists():
        _log("warn", msg=f"project_map.json 不存在，请复制 {PROJECT_MAP_EXAMPLE.name} 并按实际路径修改")
        return {}
    try:
        with open(PROJECT_MAP_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        _log("warn", msg=f"无法加载 project_map.json: {exc}")
        return {}


# ── DataClasses ───────────────────────────────────────────────────────────────


@dataclass
class SessionStats:
    messages: int = 0
    input_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def update(self, stats: dict) -> None:
        self.messages += 1
        self.input_tokens += stats.get("input_tokens", 0)
        self.cache_read_tokens += stats.get("cache_read_tokens", 0)
        self.output_tokens += stats.get("output_tokens", 0)
        self.cost_usd += stats.get("cost_usd", 0.0)

    @property
    def has_token_data(self) -> bool:
        return self.input_tokens > 0 or self.output_tokens > 0


@dataclass
class CodeSession:
    user_id: str
    project_name: str
    project_path: str
    backend: str = field(default_factory=lambda: DEFAULT_BACKEND)
    started_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    is_first_cli_call: bool = True
    stats: SessionStats = field(default_factory=SessionStats)


# ── Logging ───────────────────────────────────────────────────────────────────


def _log(event: str, **kwargs) -> None:
    print(json.dumps({"event": event, **kwargs}, ensure_ascii=False), flush=True)


# ── WeChat Bridge API ─────────────────────────────────────────────────────────


def _api_post(path: str, body: dict) -> tuple[bool, str]:
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{BRIDGE_BASE_URL}{path}",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    if BRIDGE_API_TOKEN:
        req.add_header("Authorization", f"Bearer {BRIDGE_API_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return True, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return False, exc.read().decode("utf-8", "replace")
    except Exception as exc:
        return False, str(exc)


def _send(to_user: str, text: str) -> bool:
    ok, result = _api_post("/api/send", {"to": to_user, "text": text, "markdown": True})
    if not ok:
        _log("send_failed", to=to_user[:16], error=result[:120])
    return ok


def _send_output(to_user: str, output: str, *, footer: str = "") -> None:
    """按段落分块回传，超限再硬截断。footer 附加在最后一块末尾。"""
    chunks: list[str] = []
    current = ""
    for para in output.split("\n\n"):
        candidate = (current + "\n\n" + para).lstrip("\n") if current else para
        if len(candidate) <= MAX_CHUNK:
            current = candidate
        else:
            if current:
                chunks.append(current)
            while len(para) > MAX_CHUNK:
                chunks.append(para[:MAX_CHUNK])
                para = para[MAX_CHUNK:]
            current = para
    if current:
        chunks.append(current)
    if not chunks:
        chunks = ["(空响应)"]

    if footer:
        chunks[-1] = chunks[-1] + "\n\n" + footer

    total = len(chunks)
    for i, chunk in enumerate(chunks):
        text = f"({i + 1}/{total})\n\n{chunk}" if total > 1 else chunk
        _send(to_user, text)
        if i < total - 1:
            time.sleep(0.5)


# ── AI CLI 调用 ───────────────────────────────────────────────────────────────


def _parse_claude_json(raw: str) -> tuple[str, dict]:
    """解析 Claude --output-format json 的输出，返回 (文本, 用量统计)。"""
    try:
        data = json.loads(raw)
        text = data.get("result", "")
        usage = data.get("usage", {})
        stats = {
            "input_tokens": usage.get("input_tokens", 0),
            "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cost_usd": data.get("total_cost_usd", 0.0),
        }
        return text or "(Claude 无输出)", stats
    except (json.JSONDecodeError, AttributeError):
        return raw or "(Claude 无输出)", {}


def _run_cli(prompt: str, cwd: str, backend: str, *, resume: bool) -> tuple[str, dict]:
    """调用 AI CLI，返回 (输出文本, 用量统计)。"""
    cmd = _build_cmd(backend, prompt, cwd, resume=resume)
    _log("cli_invoke", backend=backend, resume=resume, cmd_head=" ".join(cmd[:3]))
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT,
        )
        raw = result.stdout.strip()

        if backend == "claude":
            text, stats = _parse_claude_json(raw)
            if result.returncode != 0 and not text:
                text = result.stderr.strip() or "(Claude 执行失败)"
            return text, stats

        # gemini / codex：text 模式，无结构化 token 数据
        output = raw
        if result.returncode != 0 and result.stderr.strip():
            stderr = result.stderr.strip()
            output = (output + "\n\n" + stderr).strip() if output else stderr
        return output or f"({CLI_BACKENDS[backend]} 无输出)", {}

    except subprocess.TimeoutExpired:
        return f"⚠️ {CLI_BACKENDS[backend]} 执行超时（>{CLI_TIMEOUT}s）", {}
    except FileNotFoundError:
        return f"⚠️ 找不到 `{CLI_BINARIES[backend]}` 命令，请确认已安装并在 PATH 中", {}
    except Exception as exc:
        return f"⚠️ {CLI_BACKENDS[backend]} 执行异常: {exc}", {}


def _run_raw(cmd: list[str], cwd: str) -> str:
    """透传原生命令，捕获 stdout + stderr，不注入任何 approval 参数。"""
    _log("cli_raw", cmd=" ".join(cmd[:4]))
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT,
        )
        output = result.stdout.strip()
        if result.stderr.strip():
            stderr = result.stderr.strip()
            output = (output + "\n\n" + stderr).strip() if output else stderr
        return output or "(无输出)"
    except subprocess.TimeoutExpired:
        return f"⚠️ 执行超时（>{CLI_TIMEOUT}s）"
    except FileNotFoundError:
        return f"⚠️ 找不到命令: `{cmd[0]}`"
    except Exception as exc:
        return f"⚠️ 执行异常: {exc}"


def _format_project_list(project_map: dict[str, str]) -> str:
    """把 project_map 格式化为 Markdown 列表，显示缩写和路径。"""
    if not project_map:
        return "_(project_map.json 为空，请添加项目)_"
    return "\n".join(f"- `{k}` → `{v}`" for k, v in project_map.items())


def _stats_footer(stats: dict, cumulative: SessionStats) -> str:
    """构造 Claude token 用量单行 footer。"""
    in_tok = stats.get("input_tokens", 0)
    cache = stats.get("cache_read_tokens", 0)
    out = stats.get("output_tokens", 0)
    cost = stats.get("cost_usd", 0.0)
    cache_hint = f" +{cache:,}↩" if cache else ""
    return f"📊 `{in_tok:,}`{cache_hint} in / `{out:,}` out · ${cost:.4f}（累计 ${cumulative.cost_usd:.4f}）"


# ── Plugin ────────────────────────────────────────────────────────────────────


class CodeAgentPlugin:
    """Bridge Code Agent 插件。

    处理 /code、/switch、/cli、/exit 命令，以及会话内的普通 AI 对话消息。
    由 webhook_manager.py 自动发现并加载，也可通过 bridge_code_agent.py 单独运行。
    """

    name = "bridge-code-agent"

    def __init__(self) -> None:
        self._sessions: dict[str, CodeSession] = {}
        self._sessions_lock = threading.Lock()

    # ── BasePlugin 接口 ──

    @property
    def commands(self) -> list[str]:
        return [START_COMMAND, SWITCH_COMMAND, CLI_COMMAND] + list(EXIT_COMMANDS)

    def get_command_specs(self) -> list[dict]:
        backends_str = " / ".join(CLI_BACKENDS.keys())
        return [
            {"command": START_COMMAND, "description": f"开启代码 Agent 会话，可选后端: {backends_str}"},
            {"command": SWITCH_COMMAND, "description": "session 内切换 AI 后端（重置 CLI session）"},
            {"command": CLI_COMMAND, "description": "透传原生 CLI 命令，如 /cli --list-sessions"},
            {"command": "/exit", "description": "退出代码 Agent 会话"},
        ]

    def has_session(self, user_id: str) -> bool:
        """普通消息路由判断：该用户是否有活跃会话。"""
        with self._sessions_lock:
            sess = self._sessions.get(user_id)
            if sess is None:
                return False
            if time.time() - sess.last_active > SESSION_TIMEOUT:
                del self._sessions[user_id]
                return False
            return True

    def handle(self, payload: dict) -> None:
        from_user = payload.get("from_user", "")
        from_name = payload.get("from_name", "用户")
        text = payload.get("text", "").strip()
        command = payload.get("command", "")

        if not from_user or not text:
            return
        if ALLOWED_USERS and from_user not in ALLOWED_USERS:
            return

        # ── /code [project] [backend] ──
        if command == START_COMMAND:
            parts = payload.get("args", "").strip().split()
            project_name = parts[0] if parts else ""
            backend = parts[1].lower() if len(parts) > 1 else DEFAULT_BACKEND
            if backend not in CLI_BACKENDS:
                backend = DEFAULT_BACKEND

            project_map = _load_project_map()
            if not project_name:
                backends = " | ".join(CLI_BACKENDS.keys())
                _send(
                    from_user,
                    (
                        f"## ❓ 用法\n\n`/code <项目名> [后端]`\n\n"
                        f"**可用项目**\n\n{_format_project_list(project_map)}\n\n"
                        f"**可用后端**：`{backends}`（默认 `{DEFAULT_BACKEND}`）"
                    ),
                )
                return
            if project_name not in project_map:
                _send(
                    from_user,
                    (
                        f"## ❌ 未知项目\n\n`{project_name}` 不在 project_map.json 中\n\n"
                        f"**可用项目**\n\n{_format_project_list(project_map)}"
                    ),
                )
                return

            project_path = project_map[project_name]
            self._start_session(from_user, project_name, project_path, backend)
            _send(
                from_user,
                (
                    f"## ✅ 已进入 `{project_name}`\n\n"
                    f"- **路径**：`{project_path}`\n"
                    f"- **后端**：`{CLI_BACKENDS[backend]}` (`{backend}`)\n"
                    f"- 直接发消息与 AI 对话，`/switch <后端>` 切换，`/cli <参数>` 调用原生命令，`/exit` 退出"
                ),
            )
            _log("session_start", user=from_name, project=project_name, backend=backend)
            return

        # ── /switch <backend> ──
        if command == SWITCH_COMMAND:
            sess = self._get_session(from_user)
            if sess is None:
                _send(from_user, "## ℹ️ 当前没有活跃的代码会话\n\n先用 `/code <项目>` 开启")
                return
            new_backend = payload.get("args", "").strip().lower()
            if new_backend not in CLI_BACKENDS:
                options = " | ".join(CLI_BACKENDS.keys())
                _send(from_user, f"## ❓ 可用后端\n\n`{options}`")
                return
            old_backend = sess.backend
            sess.backend = new_backend
            sess.is_first_cli_call = True
            _send(
                from_user,
                (
                    f"## 🔄 后端已切换\n\n"
                    f"- **{CLI_BACKENDS[old_backend]}** → **{CLI_BACKENDS[new_backend]}**\n"
                    f"- CLI session 已重置，下一条消息开始新的 {CLI_BACKENDS[new_backend]} 会话"
                ),
            )
            _log("backend_switch", user=from_name, old=old_backend, new=new_backend)
            return

        # ── /cli <原生参数> ──
        if command == CLI_COMMAND:
            sess = self._get_session(from_user)
            if sess is None:
                _send(from_user, "## ℹ️ 当前没有活跃的代码会话\n\n先用 `/code <项目>` 开启")
                return
            raw_args = payload.get("args", "").strip()
            if not raw_args:
                binary = CLI_BINARIES[sess.backend]
                _send(
                    from_user,
                    (
                        f"## ❓ 用法\n\n`/cli <原生参数>`\n\n"
                        f"透传给 `{binary}`，例如：\n"
                        f"- `/cli --list-sessions`\n"
                        f"- `/cli --version`\n"
                        f"- `/cli exec review`（codex）"
                    ),
                )
                return
            try:
                args = shlex.split(raw_args, posix=(sys.platform != "win32"))
            except ValueError:
                args = raw_args.split()
            cmd = [_find_bin(CLI_BINARIES[sess.backend])] + args
            output = _run_raw(cmd, cwd=sess.project_path)
            _send_output(from_user, output)
            return

        # ── /exit ──
        if command in EXIT_COMMANDS or text in EXIT_COMMANDS:
            sess = self._end_session(from_user)
            if sess is not None:
                lines = ["## 👋 会话已退出\n"]
                s = sess.stats
                lines.append(f"- **项目**：{sess.project_name} / {CLI_BACKENDS[sess.backend]}")
                lines.append(f"- **对话轮数**：{s.messages} 条")
                if s.has_token_data:
                    lines.append(
                        f"- **Token 用量**：{s.input_tokens:,} in / {s.output_tokens:,} out"
                        + (f"（含 {s.cache_read_tokens:,} cache 命中）" if s.cache_read_tokens else "")
                    )
                    lines.append(f"- **累计费用**：${s.cost_usd:.4f}")
                _send(from_user, "\n".join(lines))
                _log("session_end", user=from_name, messages=s.messages, cost_usd=round(s.cost_usd, 6))
            else:
                _send(from_user, "## ℹ️ 当前没有活跃的代码会话")
            return

        # ── 普通消息 → 转发 AI CLI ──
        sess = self._get_session(from_user)
        if sess is None:
            return  # 无会话，静默丢弃（manager 的 has_session 已预检，正常不会到此）

        sess.last_active = time.time()
        backend = sess.backend
        _send(from_user, f"## ⏳ {CLI_BACKENDS[backend]} 处理中…\n\n- 请稍候")

        resume = not sess.is_first_cli_call
        output, stats = _run_cli(text, sess.project_path, backend, resume=resume)
        if sess.is_first_cli_call:
            sess.is_first_cli_call = False

        sess.stats.update(stats)

        footer = _stats_footer(stats, sess.stats) if stats.get("input_tokens") else ""
        _send_output(from_user, output, footer=footer)
        _log(
            "cli_reply",
            user=from_name,
            project=sess.project_name,
            backend=backend,
            prompt_len=len(text),
            output_len=len(output),
            cost_usd=round(stats.get("cost_usd", 0), 6),
        )

    def on_start(self) -> None:
        pass

    def on_stop(self) -> None:
        pass

    # ── 内部会话管理 ──

    def _get_session(self, user_id: str) -> CodeSession | None:
        with self._sessions_lock:
            sess = self._sessions.get(user_id)
            if sess is None:
                return None
            if time.time() - sess.last_active > SESSION_TIMEOUT:
                del self._sessions[user_id]
                _log("session_timeout", user_id=user_id[:16])
                return None
            return sess

    def _start_session(self, user_id: str, project_name: str, project_path: str, backend: str) -> CodeSession:
        sess = CodeSession(
            user_id=user_id,
            project_name=project_name,
            project_path=project_path,
            backend=backend,
        )
        with self._sessions_lock:
            self._sessions[user_id] = sess
        return sess

    def _end_session(self, user_id: str) -> CodeSession | None:
        with self._sessions_lock:
            return self._sessions.pop(user_id, None)


# ── 插件声明（供 webhook_manager 自动发现） ───────────────────────────────────

PLUGIN_CLASS = CodeAgentPlugin


# ── 单独运行入口 ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # 单独启动时：通过 webhook_manager 只加载本插件
    sys.path.insert(0, str(_SCRIPT_DIR))
    from webhook_manager import WebhookManager  # noqa: E402

    mgr = WebhookManager()
    mgr.load_plugin(CodeAgentPlugin())
    mgr.run()
