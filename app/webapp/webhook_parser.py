"""Webhook schema 适配器。"""

import json


def parse_webhook_payload(data: dict, schema: str = "") -> str:
    """
    将第三方服务的原生 Webhook 负载转化为人类可读文本。
    schema: grafana / github / uptimekuma / bark / 空字符串(自动检测)
    """
    if not schema:
        if "alerts" in data and "status" in data:
            schema = "grafana"
        elif "repository" in data and ("action" in data or "ref" in data):
            schema = "github"
        elif "heartbeat" in data or "monitor" in data:
            schema = "uptimekuma"

    if schema == "grafana":
        return _parse_grafana(data)
    if schema == "github":
        return _parse_github(data)
    if schema == "uptimekuma":
        return _parse_uptimekuma(data)
    if schema == "bark":
        return _parse_bark(data)
    return _parse_generic(data)


def _parse_grafana(data: dict) -> str:
    status = data.get("status", "unknown").upper()
    emoji = "🔴" if status == "FIRING" else "✅"
    title = data.get("title", data.get("ruleName", "Grafana Alert"))
    lines = [f"{emoji} {title}", f"状态: {status}"]

    message = data.get("message", "")
    if message:
        lines.append(f"详情: {message}")

    for alert in data.get("alerts", [])[:5]:
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        name = labels.get("alertname", labels.get("instance", ""))
        summary = annotations.get("summary", annotations.get("description", ""))
        if name:
            lines.append(f"  • {name}: {summary}" if summary else f"  • {name}")

    org = data.get("orgId", "")
    if org:
        lines.append(f"组织: {org}")

    return "\n".join(lines)


def _parse_github(data: dict) -> str:
    repo = data.get("repository", {}).get("full_name", "unknown")
    sender = data.get("sender", {}).get("login", "unknown")

    if "commits" in data:
        ref = data.get("ref", "").replace("refs/heads/", "")
        commits = data.get("commits", [])
        lines = [f"📦 {repo} 推送到 {ref}", f"推送者: {sender}"]
        for commit in commits[:5]:
            msg = commit.get("message", "").split("\n")[0]
            sha = commit.get("id", "")[:7]
            lines.append(f"  • {sha} {msg}")
        if len(commits) > 5:
            lines.append(f"  ... 共 {len(commits)} 个提交")
        return "\n".join(lines)

    action = data.get("action", "")
    if "issue" in data:
        issue = data["issue"]
        return f"📋 {repo} Issue #{issue.get('number')}\n{action}: {issue.get('title')}\n来自: {sender}"
    if "pull_request" in data:
        pr = data["pull_request"]
        return f"🔀 {repo} PR #{pr.get('number')}\n{action}: {pr.get('title')}\n来自: {sender}"
    if action == "created" and "starred_at" in data:
        return f"⭐ {sender} starred {repo}"
    if "release" in data:
        release = data["release"]
        return f"🚀 {repo} 发布 {release.get('tag_name', '')}\n{release.get('name', '')}\n来自: {sender}"

    return f"📢 GitHub: {repo} ({action or 'event'})\n来自: {sender}"


def _parse_uptimekuma(data: dict) -> str:
    heartbeat = data.get("heartbeat", {})
    monitor = data.get("monitor", {})
    name = monitor.get("name", data.get("name", "未知服务"))
    status = heartbeat.get("status", data.get("status", -1))
    msg = heartbeat.get("msg", data.get("msg", ""))

    if status == 1:
        emoji, status_text = "✅", "恢复正常"
    elif status == 0:
        emoji, status_text = "🔴", "服务宕机"
    else:
        emoji, status_text = "⚠️", f"状态: {status}"

    lines = [f"{emoji} {name} - {status_text}"]
    if msg:
        lines.append(f"详情: {msg}")
    ping = heartbeat.get("ping", "")
    if ping:
        lines.append(f"延迟: {ping}ms")
    return "\n".join(lines)


def _parse_bark(data: dict) -> str:
    title = data.get("title", "")
    body = data.get("body", data.get("content", data.get("text", "")))
    if title and body:
        return f"【{title}】\n{body}"
    return title or body or str(data)


def _parse_generic(data: dict) -> str:
    for key in ("text", "content", "message", "msg", "body", "description", "summary"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            title = data.get("title", "")
            val = value.strip()
            if title:
                return f"【{title}】\n{val}"
            return val

    formatted = json.dumps(data, ensure_ascii=False, indent=2)
    if len(formatted) > 500:
        formatted = formatted[:500] + "\n... (已截断)"
    return f"📩 收到 Webhook:\n{formatted}"

