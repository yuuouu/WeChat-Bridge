"""
HTTP API + QR 码 Web UI
提供：
  - / — 状态页 + 二维码扫码登录
  - /api/send — 发送消息
  - /api/status — 健康检查
  - /api/contacts — 联系人列表
  - /api/logout — 登出
"""

import base64
import io
import json
import logging
import mimetypes
import os
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

import qrcode

from ilink import ILinkClient
from bridge import WeChatBridge
import media as media_mod
import re

logger = logging.getLogger(__name__)

# 全局引用，由 main.py 注入
client: ILinkClient = None
bridge: WeChatBridge = None

# API 鉴权 Token（可选，未设置则接口无鉴权）
API_TOKEN = os.environ.get("API_TOKEN", "")

# 登录状态缓存
_qr_data: dict | None = None
_qr_time: float = 0


# ── 改进4: Markdown → 纯文本 降级渲染 ──

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

    # 代码块 (```...```)
    text = re.sub(r'```[\w]*\n?(.*?)```', r'\1', text, flags=re.DOTALL)
    # 行内代码
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # 图片
    text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'[图片: \1]', text)
    # 链接
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1 (\2)', text)
    # 标题 (# ~ ######)
    text = re.sub(r'^#{1,6}\s+(.+)$', r'【\1】', text, flags=re.MULTILINE)
    # 粗体 + 斜体
    text = re.sub(r'\*{3}(.+?)\*{3}', r'\1', text)
    text = re.sub(r'\*{2}(.+?)\*{2}', r'\1', text)
    text = re.sub(r'(?<!\w)\*(.+?)\*(?!\w)', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'(?<!\w)_(.+?)_(?!\w)', r'\1', text)
    # 删除线
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    # 引用
    text = re.sub(r'^>\s?(.+)$', r'「\1」', text, flags=re.MULTILINE)
    # 无序列表
    text = re.sub(r'^[\s]*[-*+]\s+', '• ', text, flags=re.MULTILINE)
    # 有序列表保留数字
    text = re.sub(r'^[\s]*(\d+)\.\s+', r'\1. ', text, flags=re.MULTILINE)
    # 分割线
    text = re.sub(r'^[-*_]{3,}\s*$', '————————', text, flags=re.MULTILINE)
    # HTML 标签
    text = re.sub(r'<[^>]+>', '', text)
    # 压缩多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


# ── 改进2: 多播发送 ──

def _multicast_send(to_str: str, text: str) -> dict:
    """
    支持逗号分隔的多目标发送。
    返回 {"ok": True/False, "results": [...], "summary": "..."}
    """
    targets = [t.strip() for t in to_str.split(",") if t.strip()]
    if not targets:
        return {"ok": False, "error": "无有效目标"}

    # 单目标走普通路径
    if len(targets) == 1:
        return bridge.send(targets[0], text)

    results = []
    success = 0
    for i, target in enumerate(targets):
        result = bridge.send(target, text)
        results.append({"to": target, **result})
        if result.get("ok"):
            success += 1
        # 多目标间间隔 0.5s 防风控
        if i < len(targets) - 1:
            import time as _t
            _t.sleep(0.5)

    return {
        "ok": success > 0,
        "summary": f"成功 {success}/{len(targets)}",
        "results": results,
    }


# ── 改进3: Webhook Schema 适配器 ──

def _parse_webhook_payload(data: dict, schema: str = "") -> str:
    """
    将第三方服务的原生 Webhook 负载转化为人类可读文本。
    schema: grafana / github / uptimekuma / bark / 空字符串(自动检测)
    """
    # 自动检测 schema
    if not schema:
        if "alerts" in data and "status" in data:
            schema = "grafana"
        elif "repository" in data and ("action" in data or "ref" in data):
            schema = "github"
        elif "heartbeat" in data or "monitor" in data:
            schema = "uptimekuma"

    if schema == "grafana":
        return _parse_grafana(data)
    elif schema == "github":
        return _parse_github(data)
    elif schema == "uptimekuma":
        return _parse_uptimekuma(data)
    elif schema == "bark":
        return _parse_bark(data)
    else:
        return _parse_generic(data)


def _parse_grafana(data: dict) -> str:
    """Grafana Alert Webhook"""
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
    """GitHub Webhook (push / issue / PR / star)"""
    repo = data.get("repository", {}).get("full_name", "unknown")
    sender = data.get("sender", {}).get("login", "unknown")

    # Push event
    if "commits" in data:
        ref = data.get("ref", "").replace("refs/heads/", "")
        commits = data.get("commits", [])
        lines = [f"📦 {repo} 推送到 {ref}", f"推送者: {sender}"]
        for c in commits[:5]:
            msg = c.get("message", "").split("\n")[0]
            sha = c.get("id", "")[:7]
            lines.append(f"  • {sha} {msg}")
        if len(commits) > 5:
            lines.append(f"  ... 共 {len(commits)} 个提交")
        return "\n".join(lines)

    # Issue / PR
    action = data.get("action", "")
    if "issue" in data:
        issue = data["issue"]
        return f"📋 {repo} Issue #{issue.get('number')}\n{action}: {issue.get('title')}\n来自: {sender}"
    if "pull_request" in data:
        pr = data["pull_request"]
        return f"🔀 {repo} PR #{pr.get('number')}\n{action}: {pr.get('title')}\n来自: {sender}"

    # Star
    if action == "created" and "starred_at" in data:
        return f"⭐ {sender} starred {repo}"

    # Release
    if "release" in data:
        rel = data["release"]
        return f"🚀 {repo} 发布 {rel.get('tag_name', '')}\n{rel.get('name', '')}\n来自: {sender}"

    return f"📢 GitHub: {repo} ({action or 'event'})\n来自: {sender}"


def _parse_uptimekuma(data: dict) -> str:
    """Uptime Kuma Webhook"""
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
    """Bark 推送格式兼容"""
    title = data.get("title", "")
    body = data.get("body", data.get("content", data.get("text", "")))
    if title and body:
        return f"【{title}】\n{body}"
    return title or body or str(data)


def _parse_generic(data: dict) -> str:
    """通用 Webhook 兜底：尝试提取常见字段"""
    # 尝试常见字段名
    for key in ("text", "content", "message", "msg", "body", "description", "summary"):
        if key in data and isinstance(data[key], str) and data[key].strip():
            title = data.get("title", "")
            val = data[key].strip()
            if title:
                return f"【{title}】\n{val}"
            return val

    # 兜底：格式化JSON（限长）
    formatted = json.dumps(data, ensure_ascii=False, indent=2)
    if len(formatted) > 500:
        formatted = formatted[:500] + "\n... (已截断)"
    return f"📩 收到 Webhook:\n{formatted}"


def _url_to_qr_base64(url: str) -> str:
    """将 URL 转为 QR 码 base64 PNG 图片"""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WeChat Bridge</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'SF Pro Display', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
    background: #0f0f13;
    color: #e0e0ea;
    height: 100vh;
    display: flex;
    justify-content: center;
    align-items: center;
    overflow: hidden;
  }
  ::-webkit-scrollbar { width: 8px; height: 8px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.15); border-radius: 4px; }
  ::-webkit-scrollbar-thumb:hover { background: rgba(255, 255, 255, 0.3); }
  
  .card {
    background: linear-gradient(135deg, #1a1a2e 0%%, #16213e 100%%);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 20px;
    padding: 40px;
    max-width: 480px;
    width: 90%%;
    text-align: center;
    box-shadow: 0 20px 60px rgba(0,0,0,0.5);
    transition: all 0.3s ease;
  }
  .card.logged-in {
    max-width: 800px;
    height: 85vh;
    padding: 24px;
    text-align: left;
    display: flex;
    flex-direction: column;
  }
  .header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; border-bottom: 1px solid rgba(255,255,255,0.05); padding-bottom: 20px;}
  .logo { font-size: 48px; margin-bottom: 16px; }
  .header .logo { font-size: 32px; margin-bottom: 0; margin-right: 12px; }
  h1 {
    font-size: 24px;
    font-weight: 600;
    background: linear-gradient(135deg, #07c160, #06ad56);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 8px;
  }
  .header h1 { font-size: 20px; margin-bottom: 0;}
  .subtitle { color: #888; font-size: 14px; margin-bottom: 32px; }
  .header .subtitle { display: none; }
  
  .qr-container {
    background: white;
    border-radius: 16px;
    padding: 20px;
    display: inline-block;
    margin-bottom: 24px;
  }
  .qr-container img { width: 240px; height: 240px; }
  .status-badge {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 6px 16px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 500;
  }
  .status-online {
    background: rgba(7,193,96,0.15);
    color: #07c160;
    border: 1px solid rgba(7,193,96,0.3);
  }
  .status-offline {
    background: rgba(255,107,107,0.15);
    color: #ff6b6b;
    border: 1px solid rgba(255,107,107,0.3);
  }
  .dot { width: 6px; height: 6px; border-radius: 50%%; display: inline-block; }
  .dot-green { background: #07c160; animation: pulse 2s infinite; }
  .dot-red { background: #ff6b6b; }
  @keyframes pulse { 0%%, 100%% { opacity: 1; } 50%% { opacity: 0.4; } }

  /* Chat UI Styles */
  .chat-container {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    background: rgba(0,0,0,0.25);
    border-radius: 16px;
    border: 1px solid rgba(255,255,255,0.03);
  }
  .chat-messages {
    flex: 1;
    overflow-y: auto;
    padding: 24px;
    display: flex;
    flex-direction: column;
    gap: 20px;
  }
  .msg {
    display: flex;
    flex-direction: column;
    max-width: 85%%;
    animation: fadeIn 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);
  }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
  .msg.recv { align-self: flex-start; }
  .msg.send { align-self: flex-end; }
  .msg-meta { font-size: 11px; color: #888; margin-bottom: 6px; display: flex; gap: 8px; }
  .msg.send .msg-meta { justify-content: flex-end; }
  .msg-bubble {
    padding: 12px 16px;
    border-radius: 14px;
    font-size: 14px;
    line-height: 1.5;
    word-break: break-word;
    white-space: pre-wrap;
    box-shadow: 0 4px 15px rgba(0,0,0,0.1);
  }
  .msg.recv .msg-bubble {
    background: #2a2a3e;
    color: #e0e0e0;
    border-top-left-radius: 4px;
  }
  .msg.send .msg-bubble {
    background: linear-gradient(135deg, #07c160, #06ad56);
    color: #fff;
    border-top-right-radius: 4px;
  }
  /* 图片消息样式 */
  .msg-bubble img.chat-img {
    max-width: 280px;
    max-height: 320px;
    border-radius: 10px;
    margin: 6px 0 2px;
    cursor: pointer;
    transition: transform 0.2s, box-shadow 0.2s;
    display: block;
  }
  .msg-bubble img.chat-img:hover {
    transform: scale(1.03);
    box-shadow: 0 6px 24px rgba(0,0,0,0.4);
  }
  .msg-bubble video.chat-video {
    max-width: 320px;
    max-height: 280px;
    border-radius: 10px;
    margin: 6px 0 2px;
    display: block;
    background: #000;
  }
  /* 图片/视频全屏预览 */
  .img-lightbox {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.85);
    z-index: 9999;
    justify-content: center;
    align-items: center;
    cursor: zoom-out;
  }
  .img-lightbox.active { display: flex; }
  .img-lightbox img, .img-lightbox video {
    max-width: 92vw;
    max-height: 92vh;
    border-radius: 8px;
    box-shadow: 0 0 40px rgba(0,0,0,0.5);
  }
  
  .chat-input-area {
    padding: 16px;
    background: rgba(20,20,35,0.9);
    border-top: 1px solid rgba(255,255,255,0.05);
    display: flex;
    gap: 12px;
  }
  .contact-select {
    background: #1e1e2d;
    color: #e0e0ea;
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 10px;
    padding: 0 32px 0 14px;
    font-size: 14px;
    outline: none;
    width: 140px;
    appearance: none;
    background-image: url("data:image/svg+xml;charset=UTF-8,%%3csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%%23888' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%%3e%%3cpolyline points='6 9 12 15 18 9'%%3e%%3c/polyline%%3e%%3c/svg%%3e");
    background-repeat: no-repeat;
    background-position: right 10px center;
    background-size: 14px;
    transition: all 0.2s ease;
  }
  .contact-select:focus, .contact-select:hover { border-color: rgba(7,193,96,0.6); background-color: #252538; }
  .chat-input {
    flex: 1;
    background: #1e1e2d;
    border: 1px solid rgba(255,255,255,0.08);
    color: white;
    border-radius: 10px;
    padding: 12px 16px;
    font-size: 14px;
    outline: none;
    transition: all 0.2s ease;
  }
  .chat-input:focus { border-color: rgba(7,193,96,0.6); box-shadow: 0 0 0 2px rgba(7,193,96,0.15); background-color: #252538; }
  .send-btn {
    background: linear-gradient(135deg, #07c160, #06ad56);
    color: white;
    border: none;
    border-radius: 10px;
    padding: 0 24px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s;
  }
  .send-btn:hover { opacity: 0.9; transform: translateY(-1px); box-shadow: 0 4px 15px rgba(7,193,96,0.3); }
  .send-btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; box-shadow: none;}

  .header-actions { display: flex; align-items: center; gap: 16px; }
  .logout-btn {
    background: rgba(255,107,107,0.1);
    color: #ff6b6b;
    border: 1px solid rgba(255,107,107,0.2);
    padding: 6px 14px;
    border-radius: 8px;
    font-size: 12px;
    cursor: pointer;
    transition: all 0.2s;
  }
  .logout-btn:hover { background: rgba(255,107,107,0.2); }

  .refresh-btn {
    margin-top: 16px;
    padding: 10px 24px;
    background: linear-gradient(135deg, #07c160, #06ad56);
    color: white;
    border: none;
    border-radius: 10px;
    font-size: 14px;
    cursor: pointer;
    transition: transform 0.2s;
  }
  .refresh-btn:hover { transform: scale(1.05); }
  .hint { margin-top: 20px; color: #666; font-size: 12px; line-height: 1.6; }

  /* AI Settings Modal */
  .ai-settings-btn {
    background: rgba(99,102,241,0.15);
    color: #818cf8;
    border: 1px solid rgba(99,102,241,0.3);
    padding: 6px 14px;
    border-radius: 8px;
    font-size: 12px;
    cursor: pointer;
    transition: all 0.2s;
  }
  .ai-settings-btn:hover { background: rgba(99,102,241,0.25); }
  .modal-overlay {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.6);
    backdrop-filter: blur(4px);
    z-index: 100;
    justify-content: center;
    align-items: center;
  }
  .modal-overlay.active { display: flex; }
  .modal {
    background: #1a1a2e;
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 16px;
    padding: 32px;
    width: 90%%;
    max-width: 480px;
    max-height: 85vh;
    overflow-y: auto;
    animation: fadeIn 0.3s;
  }
  .modal h2 { font-size: 18px; margin-bottom: 24px; color: #818cf8; }
  .form-group { margin-bottom: 16px; }
  .form-label { display: block; font-size: 12px; color: #888; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
  .form-select {
    appearance: none;
    background-image: url("data:image/svg+xml;charset=UTF-8,%%3csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%%23888' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%%3e%%3cpolyline points='6 9 12 15 18 9'%%3e%%3c/polyline%%3e%%3c/svg%%3e");
    background-repeat: no-repeat;
    background-position: right 10px center;
    background-size: 14px;
  }
  .form-input, .form-select, .form-textarea {
    width: 100%%;
    background: #1e1e2d;
    border: 1px solid rgba(255,255,255,0.1);
    color: white;
    border-radius: 8px;
    padding: 10px 12px;
    font-size: 14px;
    font-family: inherit;
    transition: all 0.2s ease;
  }
  .form-input:focus, .form-select:focus, .form-textarea:focus {
    outline: none;
    border-color: rgba(7,193,96,0.6);
    box-shadow: 0 0 0 2px rgba(7,193,96,0.15);
    background-color: #252538;
  }
  .form-textarea { resize: vertical; min-height: 60px; font-family: inherit; }
  .toggle-switch { display: flex; align-items: center; gap: 12px; cursor: pointer; }
  .toggle-track {
    width: 44px; height: 24px;
    background: #333;
    border-radius: 12px;
    position: relative;
    transition: background 0.3s;
  }
  .toggle-track.on { background: #07c160; }
  .toggle-knob {
    width: 18px; height: 18px;
    background: white;
    border-radius: 50%%;
    position: absolute;
    top: 3px; left: 3px;
    transition: transform 0.3s;
  }
  .toggle-track.on .toggle-knob { transform: translateX(20px); }
  .modal-actions { display: flex; justify-content: flex-end; gap: 12px; margin-top: 24px; }
  .btn-cancel { background: #333; color: #ccc; border: none; padding: 10px 20px; border-radius: 8px; cursor: pointer; }
  .btn-save { background: linear-gradient(135deg, #6366f1, #4f46e5); color: white; border: none; padding: 10px 20px; border-radius: 8px; cursor: pointer; font-weight: 500; }
  .btn-save:hover { opacity: 0.9; }
  
  .toast-container {
    position: fixed; top: 20px; left: 50%%; transform: translateX(-50%%);
    z-index: 9999; display: flex; flex-direction: column; gap: 10px;
  }
  .toast {
    background: #2a2a2a; color: white; padding: 12px 24px; border-radius: 8px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.5); font-size: 14px;
    animation: slideDown 0.3s ease-out, fadeOut 0.3s ease-in 2.7s forwards;
    display: flex; align-items: center; gap: 8px; border: 1px solid #444;
  }
  .toast.success { border-left: 4px solid #07c160; }
  .toast.error { border-left: 4px solid #ef4444; }
  @keyframes slideDown { from{transform:translateY(-20px);opacity:0} to{transform:translateY(0);opacity:1} }
  @keyframes fadeOut { from{opacity:1} to{opacity:0; visibility:hidden} }
  
  .dialog-overlay {
    position: fixed; top: 0; left: 0; width: 100%%; height: 100%%; z-index: 10000;
    background: rgba(0,0,0,0.6); backdrop-filter: blur(4px);
    display: flex; align-items: center; justify-content: center;
    animation: fadeIn 0.2s ease-out;
  }
  @keyframes fadeIn { from{opacity:0} to{opacity:1} }
  .dialog-box {
    background: #1e1e2d; border: 1px solid rgba(255,255,255,0.1);
    border-radius: 16px; padding: 28px 32px; max-width: 420px; width: 90%%;
    box-shadow: 0 20px 60px rgba(0,0,0,0.6); color: #e0e0e0;
    animation: scaleIn 0.2s ease-out;
  }
  @keyframes scaleIn { from{transform:scale(0.9);opacity:0} to{transform:scale(1);opacity:1} }
  .dialog-title {
    font-size: 16px; font-weight: 600; margin-bottom: 12px;
    display: flex; align-items: center; gap: 8px;
  }
  .dialog-title.error { color: #ef4444; }
  .dialog-title.warning { color: #f59e0b; }
  .dialog-title.info { color: #6366f1; }
  .dialog-body { font-size: 14px; line-height: 1.6; color: #aaa; margin-bottom: 24px; white-space: pre-line; }
  .dialog-btn {
    background: linear-gradient(135deg, #6366f1, #4f46e5); color: white;
    border: none; padding: 10px 28px; border-radius: 8px; cursor: pointer;
    font-size: 14px; font-weight: 500; float: right;
  }
  .dialog-btn:hover { opacity: 0.9; }
  
  .img-upload-btn {
    background: #1e1e2d;
    border: 1px solid rgba(255,255,255,0.1);
    color: white;
    border-radius: 8px;
    padding: 0 14px;
    height: 40px;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    font-size: 16px;
    transition: all 0.2s;
  }
  .img-upload-btn:hover { background: #252538; border-color: rgba(7,193,96,0.5); }
</style>
</head>
<body>
%s
<script>
%s
</script>
</body>
</html>"""


def _render_logged_in():
    """已登录聊天界面"""
    content = f"""
  <div class="card logged-in">
    <div class="header">
      <div style="display:flex; align-items:center;">
        <div class="logo">💬</div>
        <h1>WeChat Bridge</h1>
      </div>
      <div class="header-actions">
        <div class="status-badge status-online">
          <span class="dot dot-green"></span> 已连接
        </div>
        <button class="ai-settings-btn" onclick="openAISettings()">⚙️ 设置</button>
        <form action="/api/logout" method="POST" style="margin:0;">
          <button type="submit" class="logout-btn">退出登录</button>
        </form>
      </div>
    </div>
    
    <div class="chat-container">
      <div class="chat-messages" id="msgs">
        <!-- 动态加载消息 -->
        <div style="text-align:center; color:#666; font-size:12px; margin-top:20px;">服务启动，等待收发消息...</div>
      </div>
      <div class="chat-input-area">
        <input type="text" id="contact" class="contact-select" list="contact_list" placeholder="发送给谁...">
        <datalist id="contact_list"></datalist>
        
        <label for="imgUpload" class="img-upload-btn" title="发送图片">🖼️</label>
        <input type="file" id="imgUpload" accept="image/*" style="display: none;">
        
        <input type="text" id="ipt" class="chat-input" placeholder="输入消息内容，回车发送..." autocomplete="off">
        <button id="sendBtn" class="send-btn">发送</button>
      </div>
    </div>
  </div>

<!-- 图片全屏预览 Lightbox -->
<div class="img-lightbox" id="imgLightbox" onclick="this.classList.remove('active')">
  <img id="lightboxImg" src="" alt="preview">
</div>

<!-- System Settings Modal -->
<div class="modal-overlay" id="aiModal">
  <div class="modal">
    <h2>⚙️ 系统设置</h2>
    
    <h3 style="margin-bottom: 15px; margin-top:10px; font-size:15px; color:#ddd;">连接保活提醒 (24h限制)</h3>
    <div class="form-group">
      <label class="form-label">用户最后一条消息后，超过以下时间发送保活提醒</label>
      <div style="display:flex; align-items:center; gap:10px;">
        <select class="form-select" id="kaHours" style="width:auto; min-width:80px;" onchange="updateKALabel()">
          <option value="-1">关闭</option>
        </select>
        <span id="kaHourText" style="color:#aaa;">时</span>
        <select class="form-select" id="kaMinutes" style="width:auto; min-width:80px;" onchange="updateKALabel()">
        </select>
        <span id="kaMinuteText" style="color:#aaa;">分</span>
      </div>
      <div id="kaHint" style="color:#888; font-size:12px; margin-top:6px;"></div>
    </div>

    <div style="border-top: 1px solid #444; margin: 20px 0;"></div>
    
    <h3 style="margin-bottom: 15px; font-size:15px; color:#ddd;">🤖 智能回复助手</h3>
    <div class="form-group">
      <div class="toggle-switch" onclick="toggleAI()">
        <div class="toggle-track" id="aiToggle"><div class="toggle-knob"></div></div>
        <span id="aiToggleLabel">AI 已关闭</span>
      </div>
    </div>
    
    <div id="aiSettingsGroup" style="display: none;">
      <div class="form-group">
        <label class="form-label">AI 厂商</label>
        <select class="form-select" id="aiProvider" onchange="updateModels()">
          <option value="openai">OpenAI</option>
          <option value="gemini">Google Gemini</option>
          <option value="claude">Anthropic Claude</option>
          <option value="deepseek">DeepSeek</option>
        </select>
      </div>
      <div class="form-group">
        <label class="form-label">模型</label>
        <select class="form-select" id="aiModel"></select>
      </div>
      <div class="form-group">
        <label class="form-label">API Key</label>
        <input class="form-input" id="aiKey" type="password" placeholder="输入你的 API Key">
      </div>
      <div class="form-group">
        <label class="form-label">自定义 Base URL（可选）</label>
        <input class="form-input" id="aiBaseUrl" placeholder="留空使用默认地址">
      </div>
      <div class="form-group">
        <label class="form-label">System Prompt</label>
        <textarea class="form-textarea" id="aiPrompt" rows="3"></textarea>
      </div>
      <div class="form-group">
        <label class="form-label">历史轮数</label>
        <input class="form-input" id="aiHistory" type="number" min="1" max="50" value="10">
      </div>
    </div>

    <div class="modal-actions">
      <button class="btn-cancel" onclick="closeAISettings()">取消</button>
      <button class="btn-save" onclick="saveAISettings()">保存设置</button>
    </div>
  </div>
</div>
"""
    # 动态轮询逻辑与发送请求
    js = """
    // === AI Settings ===
    const PROVIDER_MODELS = {
      openai: [{id:'gpt-4o',name:'GPT-4o'},{id:'gpt-4o-mini',name:'GPT-4o Mini'},{id:'gpt-4.1-mini',name:'GPT-4.1 Mini'},{id:'gpt-4.1-nano',name:'GPT-4.1 Nano'}],
      gemini: [{id:'gemini-2.0-flash',name:'Gemini 2.0 Flash'},{id:'gemini-2.5-flash-preview-04-17',name:'Gemini 2.5 Flash'},{id:'gemini-2.5-pro-preview-03-25',name:'Gemini 2.5 Pro'}],
      claude: [{id:'claude-sonnet-4-20250514',name:'Claude Sonnet 4'},{id:'claude-3-5-haiku-20241022',name:'Claude 3.5 Haiku'}],
      deepseek: [{id:'deepseek-chat',name:'DeepSeek Chat (V3)'},{id:'deepseek-reasoner',name:'DeepSeek Reasoner (R1)'}],
    };
    let aiEnabled = false;
    let keepaliveMinutes = 0;

    // 生成保活时间选择器选项
    (function initKAOptions() {
      const hSel = document.getElementById('kaHours');
      const mSel = document.getElementById('kaMinutes');
      for (let h = 1; h <= 23; h++) {
        const opt = document.createElement('option');
        opt.value = h; opt.textContent = h;
        hSel.appendChild(opt);
      }
      for (let m = 0; m <= 50; m += 10) {
        const opt = document.createElement('option');
        opt.value = m; opt.textContent = m.toString().padStart(2,'0');
        mSel.appendChild(opt);
      }
    })();

    function updateKALabel() {
      const h = parseInt(document.getElementById('kaHours').value);
      const hint = document.getElementById('kaHint');
      const mSel = document.getElementById('kaMinutes');
      const hTxt = document.getElementById('kaHourText');
      const mTxt = document.getElementById('kaMinuteText');
      if (h === -1) {
        mSel.style.display = 'none';
        hTxt.style.display = 'none';
        mTxt.style.display = 'none';
        hint.textContent = '保活提醒已关闭';
        keepaliveMinutes = 0;
      } else {
        mSel.style.display = '';
        hTxt.style.display = '';
        mTxt.style.display = '';
        const m = parseInt(mSel.value) || 0;
        const totalMin = h * 60 + m;
        const remain = 24 * 60 - totalMin;
        hint.textContent = `将在用户最后消息后 ${h}小时${m}分钟 提醒，距断联还剩 ${Math.floor(remain/60)}h${remain%60}m`;
        keepaliveMinutes = totalMin;
      }
    }

    function setKAFromMinutes(totalMin) {
      const hSel = document.getElementById('kaHours');
      const mSel = document.getElementById('kaMinutes');
      if (!totalMin || totalMin <= 0) {
        hSel.value = '-1';
      } else {
        hSel.value = Math.floor(totalMin / 60);
        mSel.value = Math.floor(totalMin % 60 / 10) * 10;
      }
      updateKALabel();
    }

    function updateModels() {
      const provider = document.getElementById('aiProvider').value;
      const modelEl = document.getElementById('aiModel');
      const currentModel = modelEl.value;
      modelEl.innerHTML = '';
      (PROVIDER_MODELS[provider] || []).forEach(m => {
        const opt = document.createElement('option');
        opt.value = m.id; opt.textContent = m.name;
        modelEl.appendChild(opt);
      });
      if ([...modelEl.options].some(o => o.value === currentModel)) modelEl.value = currentModel;
    }

    function toggleAI() {
      aiEnabled = !aiEnabled;
      document.getElementById('aiToggle').classList.toggle('on', aiEnabled);
      document.getElementById('aiToggleLabel').textContent = aiEnabled ? 'AI 已启用' : 'AI 已关闭';
      document.getElementById('aiSettingsGroup').style.display = aiEnabled ? 'block' : 'none';
    }



    async function openAISettings() {
      try {
        const res = await fetch('/api/ai_config');
        const cfg = await res.json();
        
        aiEnabled = cfg.enabled || false;
        document.getElementById('aiToggle').classList.toggle('on', aiEnabled);
        document.getElementById('aiToggleLabel').textContent = aiEnabled ? 'AI 已启用' : 'AI 已关闭';
        document.getElementById('aiSettingsGroup').style.display = aiEnabled ? 'block' : 'none';
        
        setKAFromMinutes(cfg.keepalive_remind_minutes || 0);

        document.getElementById('aiProvider').value = cfg.provider || 'openai';
        updateModels();
        document.getElementById('aiModel').value = cfg.model || '';
        document.getElementById('aiKey').value = cfg.api_key || '';
        document.getElementById('aiBaseUrl').value = cfg.base_url || '';
        document.getElementById('aiPrompt').value = cfg.system_prompt || '';
        document.getElementById('aiHistory').value = cfg.max_history || 10;
      } catch(e) {}
      document.getElementById('aiModal').classList.add('active');
    }

    function closeAISettings() {
      document.getElementById('aiModal').classList.remove('active');
    }

    function showToast(msg, type='success') {
      let container = document.getElementById('toast-container');
      if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'toast-container';
        document.body.appendChild(container);
      }
      const toast = document.createElement('div');
      toast.className = `toast ${type}`;
      toast.innerHTML = type === 'success' ? `✅ ${msg}` : `❌ ${msg}`;
      container.appendChild(toast);
      setTimeout(() => toast.remove(), 3000);
    }

    function showDialog(msg, type='error') {
      const overlay = document.createElement('div');
      overlay.className = 'dialog-overlay';
      const icons = { error: '❌', warning: '⚠️', info: 'ℹ️' };
      const titles = { error: '发送失败', warning: '提示', info: '提示' };
      overlay.innerHTML = `
        <div class="dialog-box">
          <div class="dialog-title ${type}">${icons[type] || '⚠️'} ${titles[type] || '提示'}</div>
          <div class="dialog-body">${msg}</div>
          <button class="dialog-btn" onclick="this.closest('.dialog-overlay').remove()">确定</button>
        </div>
      `;
      document.body.appendChild(overlay);
      overlay.querySelector('.dialog-btn').focus();
      overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
    }

    async function saveAISettings() {
      const cfg = {
        enabled: aiEnabled,
        keepalive_remind_minutes: keepaliveMinutes,
        provider: document.getElementById('aiProvider').value,
        model: document.getElementById('aiModel').value,
        api_key: document.getElementById('aiKey').value,
        base_url: document.getElementById('aiBaseUrl').value,
        system_prompt: document.getElementById('aiPrompt').value,
        max_history: parseInt(document.getElementById('aiHistory').value) || 10,
      };
      try {
        const res = await fetch('/api/ai_config', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(cfg)
        });
        if (res.ok) {
          closeAISettings();
          showToast('设置已保存');
        } else {
          const err = await res.json();
          showToast('保存失败: ' + (err.error || '未知错误'), 'error');
        }
      } catch(e) { showToast('网络错误', 'error'); }
    }

    // 点击遮罩关闭
    document.getElementById('aiModal').addEventListener('click', e => {
      if (e.target.id === 'aiModal') closeAISettings();
    });

    const msgsEl = document.getElementById('msgs');
    const contactIpt = document.getElementById('contact');
    const contactList = document.getElementById('contact_list');
    const textIpt = document.getElementById('ipt');
    const sendBtn = document.getElementById('sendBtn');
    
    let knownMsgIds = new Set();
    let isScrolledToBottom = true;
    let initialLoad = true;
    
    msgsEl.addEventListener('scroll', () => {
      isScrolledToBottom = msgsEl.scrollHeight - msgsEl.scrollTop - msgsEl.clientHeight < 50;
    });

    async function fetchContacts() {
      try {
        const res = await fetch('/api/contacts?_t=' + Date.now());
        const data = await res.json();
        contactList.innerHTML = '';
        const entries = Object.entries(data.contacts);
        for (let [uid, name] of entries) {
          const opt = document.createElement('option');
          opt.value = name;
          contactList.appendChild(opt);
        }
        // 如果联系人有且仅有一个，且输入框为空，则默认选中它
        if (entries.length === 1 && !contactIpt.value) {
          contactIpt.value = entries[0][1];
        }
      } catch (e) {}
    }

    async function fetchMsgs() {
      try {
        const res = await fetch('/api/messages?_t=' + Date.now());
        const data = await res.json();
        let appended = false;
        
        data.messages.forEach(m => {
          if (!knownMsgIds.has(m.msg_id)) {
            if(initialLoad && knownMsgIds.size === 0) msgsEl.innerHTML = ''; // 清除 loading 提示
            knownMsgIds.add(m.msg_id);
            const isSend = m.type === 'send';
            const date = new Date(m.time * 1000).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
            
            const div = document.createElement('div');
            div.className = `msg ${m.type}`;
            
            // 渲染消息内容（支持图片/视频内联显示）
            let bubbleContent = m.text.replace(/</g, "&lt;");
            
            if (m.media) {
              const mediaUrl = '/media/' + encodeURIComponent(m.media);
              const isVideo = /\.(mp4|mov|webm|3gp|avi|ts|flv)$/i.test(m.media);
              const scrollJs = "document.getElementById('msgs').scrollTop = document.getElementById('msgs').scrollHeight";
              if (isVideo) {
                bubbleContent = bubbleContent.replace(
                  /\[视频:[^\]]*\]/g,
                  `<video class="chat-video" src="${mediaUrl}" controls preload="metadata" playsinline onloadedmetadata="${scrollJs}"></video>`
                );
              } else {
                bubbleContent = bubbleContent.replace(
                  /\[图片:[^\]]*\]/g, 
                  `<img class="chat-img" src="${mediaUrl}" alt="图片" onclick="openLightbox('${mediaUrl}')" loading="lazy" onload="${scrollJs}">`
                );
              }
            }
            
            div.innerHTML = `
              <div class="msg-meta">
                <span>${isSend ? '我 ➞ ' + m.contact : m.contact}</span>
                <span>${date}</span>
              </div>
              <div class="msg-bubble">${bubbleContent}</div>
            `;
            msgsEl.appendChild(div);
            appended = true;
          }
        });
        if (initialLoad) {
          msgsEl.scrollTop = msgsEl.scrollHeight;
          initialLoad = false;
        } else if (appended && isScrolledToBottom) {
          msgsEl.scrollTo({ top: msgsEl.scrollHeight, behavior: 'smooth' });
        }
      } catch(e) {}
    }

    async function sendMsg() {
      const to = contactIpt.value.trim();
      const text = textIpt.value.trim();
      if (!text) return;
      if (!to) {
        showDialog('请先输入收件人名称\\n\\niLink API 限制：用户需要先给你发一条消息，系统才能获取其 user_id。\\n请在左侧联系人列表选择，或输入已经给你发过消息的联系人名称', 'warning');
        return;
      }
      
      sendBtn.disabled = true;
      try {
        const res = await fetch('/api/send', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({to, text})
        });
        if (res.ok) {
          textIpt.value = '';
          await fetchMsgs(); // 立即刷新
          msgsEl.scrollTo({ top: msgsEl.scrollHeight, behavior: 'smooth' });
        } else {
          const err = await res.json();
          showDialog(err.error, 'error');
        }
      } catch(e) {
        showDialog('无法连接到服务器，请检查网络', 'error');
      }
      sendBtn.disabled = false;
      textIpt.focus();
    }
    
    let lastTypingTime = 0;
    async function sendTypingStatus() {
      const to = contactIpt.value.trim();
      const text = textIpt.value.trim();
      // 仅当输入框有内容、有焦点、并且距上次发送满 5 秒时才发送
      if (!to || !text || Date.now() - lastTypingTime < 5000) return;
      
      lastTypingTime = Date.now();
      try {
        await fetch('/api/typing', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({to})
        });
      } catch (e) {}
    }

    const imgUpload = document.getElementById('imgUpload');
    imgUpload.addEventListener('change', async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      
      const to = contactIpt.value.trim();
      // 这里不强制要求 to 存在，如果是单联系人后端可以兜底，但前端提示一下更好
      if (!to) {
        showToast('请先选择或输入收件人', 'error');
        imgUpload.value = ''; // 清除选择，以便可重复选同一张图
        return;
      }
      
      const formData = new FormData();
      formData.append('to', to);
      formData.append('image', file);
      
      // 显示上传中的状态，用 toast
      showToast('图片上传发送中...');
      
      try {
        const res = await fetch('/api/send_image', {
          method: 'POST',
          body: formData
        });
        
        if (res.ok) {
          showToast('\u56fe\u7247\u53d1\u9001\u6210\u529f\uff01\u624b\u673a\u7aef\u53ef\u67e5\u770b');
          await fetchMsgs(); // 立即刷新查看消息
          msgsEl.scrollTo({ top: msgsEl.scrollHeight, behavior: 'smooth' });
        } else {
          const err = await res.json();
          showToast('图片发送失败: ' + err.error, 'error');
        }
      } catch(error) {
        showToast('网络错误', 'error');
      }
      imgUpload.value = ''; // 重置 file input
    });

    sendBtn.addEventListener('click', sendMsg);
    
    textIpt.addEventListener('keypress', (e) => {
      if(e.key === 'Enter') sendMsg();
    });
    
    // 监听输入和焦点变化，触发正在输入状态
    textIpt.addEventListener('input', sendTypingStatus);
    textIpt.addEventListener('focus', sendTypingStatus);

    fetchContacts();
    fetchMsgs();
    setInterval(fetchMsgs, 2000);

    // 图片全屏预览
    function openLightbox(url) {
      document.getElementById('lightboxImg').src = url;
      document.getElementById('imgLightbox').classList.add('active');
    }
"""
    return HTML_TEMPLATE % (content, js)


def _render_qr_page():
    """二维码登录页面"""
    global _qr_data, _qr_time

    # 每 3 分钟刷新一次二维码
    if not _qr_data or (time.time() - _qr_time > 180):
        try:
            _qr_data = client.get_qrcode()
            _qr_time = time.time()
        except Exception as e:
            error_content = f"""
  <div class="status-badge status-offline">
    <span class="dot dot-red"></span> 获取二维码失败
  </div>
  <div class="info">
    <div class="info-row">
      <span class="info-label">错误信息</span>
      <span class="info-value">{str(e)[:100]}</span>
    </div>
  </div>
  <button class="refresh-btn" onclick="location.reload()">重试</button>
"""
            return HTML_TEMPLATE % (error_content, "")

    qr_url = _qr_data.get("qrcode_img_content", "")
    qrcode_id = _qr_data.get("qrcode", "")

    # 将扫码 URL 转为 QR 码 base64 图片
    try:
        img_b64 = _url_to_qr_base64(qr_url)
    except Exception as e:
        logger.warning("生成 QR 码图片失败: %s", e)
        img_b64 = ""

    content = f"""
  <div class="card">
    <div class="logo">💬</div>
    <h1>WeChat Bridge</h1>
    <div class="subtitle">iStoreOS 微信消息桥接服务</div>
    <div class="status-badge status-offline">
      <span class="dot dot-red"></span> 等待扫码登录
    </div>
    <div style="margin-top:24px;"></div>
    <div class="qr-container">
      <img src="data:image/png;base64,{img_b64}" alt="QR Code">
    </div>
    <p class="hint">请使用微信扫描上方二维码<br>扫码后将自动跳转到已登录状态</p>
    <button class="refresh-btn" onclick="location.reload()">刷新二维码</button>
  </div>
"""
    # 自动轮询扫码状态
    auto_refresh = f"""
  let checking = false;
  setInterval(async () => {{
    if (checking) return;
    checking = true;
    try {{
      const resp = await fetch('/api/qr_status?qrcode={qrcode_id}');
      const data = await resp.json();
      if (data.logged_in) location.reload();
    }} catch(e) {{}}
    checking = false;
  }}, 3000);
"""
    return HTML_TEMPLATE % (content, auto_refresh)


class BridgeHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器"""

    def log_message(self, format, *args):
        logger.info(format, *args)

    def _check_api_token(self) -> bool:
        """检查 API Token 鉴权，未配置 TOKEN 时直接放行"""
        if not API_TOKEN:
            return True
        auth = self.headers.get("Authorization", "")
        if auth == f"Bearer {API_TOKEN}" or auth == API_TOKEN:
            return True
        # 也支持 query 参数 ?token=xxx
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if params.get("token", [""])[0] == API_TOKEN:
            return True
        self._json_response({"ok": False, "error": "Unauthorized: invalid or missing API token"}, 401)
        return False

    def _json_response(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _html_response(self, html: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _parse_multipart(self, body: bytes, content_type: str) -> tuple:
        """
        解析 multipart/form-data 请求体
        返回: (to, image_data) 元组
        """
        to = ""
        image_data = None

        try:
            # 提取 boundary
            boundary = ""
            for part in content_type.split(";"):
                part = part.strip()
                if part.startswith("boundary="):
                    boundary = part[len("boundary="):]
                    break

            if not boundary:
                return to, image_data

            # 分割 multipart 段
            boundary_bytes = boundary.encode()
            parts = body.split(b"--" + boundary_bytes)

            for part in parts:
                if not part or part.strip() == b"--" or part.strip() == b"":
                    continue

                # 分离头部和内容
                if b"\r\n\r\n" in part:
                    header_section, content = part.split(b"\r\n\r\n", 1)
                elif b"\n\n" in part:
                    header_section, content = part.split(b"\n\n", 1)
                else:
                    continue

                # 去掉末尾的 \r\n
                content = content.rstrip(b"\r\n")

                header_text = header_section.decode("utf-8", errors="ignore").lower()
                if 'name="to"' in header_text:
                    to = content.decode("utf-8", errors="ignore").strip()
                elif 'name="image"' in header_text or 'name="file"' in header_text:
                    image_data = content

        except Exception as e:
            logger.warning("解析 multipart 失败: %s", e)

        return to, image_data

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/":
            if client.logged_in:
                self._html_response(_render_logged_in())
            else:
                self._html_response(_render_qr_page())

        elif path == "/api/status":
            self._json_response({
                "logged_in": client.logged_in,
                "bot_id": client.bot_id,
                "contacts_count": len(bridge.contacts),
                "poll_running": bridge._running,
            })

        elif path == "/api/contacts":
            if not self._check_api_token(): return
            self._json_response({
                "contacts": bridge.contacts,
                "context_tokens": {k: v[:20] + "..." for k, v in bridge.context_tokens.items()},
            })

        elif path == "/api/messages":
            import db as msg_db
            limit = int(params.get("limit", ["200"])[0])
            before_id = params.get("before_id", [None])[0]
            if before_id:
                before_id = int(before_id)
            messages = msg_db.get_messages(limit=limit, before_id=before_id)
            self._json_response({"messages": messages})

        elif path == "/api/ai_config":
            if not client.logged_in:
                self._json_response({"error": "未登录"}, 401)
                return
            import config as cfg
            ai_config = cfg.load_config()
            # 脱敏 API Key（防短字符负数越界 Bug）
            key = ai_config.get("api_key", "")
            if len(key) > 12:
                ai_config["api_key"] = key[:4] + "********" + key[-4:]
            elif len(key) > 0:
                ai_config["api_key"] = "********"
            self._json_response(ai_config)

        elif path == "/api/qr_status":
            qrcode = params.get("qrcode", [""])[0]
            if not qrcode:
                self._json_response({"error": "missing qrcode param"}, 400)
                return
            try:
                status_data = client.poll_qrcode_status(qrcode)
                self._json_response({
                    "status": status_data.get("status"),
                    "logged_in": client.logged_in,
                })
            except Exception as e:
                self._json_response({"error": str(e)}, 500)

        elif path == "/api/send":
            if not self._check_api_token(): return
            if not client.logged_in:
                self._json_response({"ok": False, "error": "未登录"}, 401)
                return

            to = params.get("to", [""])[0]
            title = params.get("title", [""])[0]
            text = params.get("text", [""])[0] or params.get("content", [""])[0]
            
            if title and text:
                text = f"【{title}】\n{text}"
            elif title and not text:
                text = title

            # to 不传时自动取第一个联系人
            if not to and bridge.contacts:
                to = list(bridge.contacts.values())[0]

            if not to:
                self._json_response({"ok": False, "error": "无可用联系人，请指定 to 参数"}, 400)
                return
            if not text:
                self._json_response({"ok": False, "error": "缺少 text 参数"}, 400)
                return

            # Markdown 降级
            if params.get("markdown", [""])[0] in ("1", "true", "yes"):
                text = markdown_to_plain(text)

            # 多播发送
            result = _multicast_send(to, text)
            status = 200 if result.get("ok") else 400
            self._json_response(result, status)

        elif path == "/api/push":
            if not self._check_api_token(): return
            if not client.logged_in:
                self._json_response({"ok": False, "error": "未登录"}, 401)
                return
            
            to = params.get("to", [""])[0]
            if not to and bridge.contacts:
                to = list(bridge.contacts.values())[0]
                
            title = params.get("title", [""])[0]
            text = params.get("text", [""])[0] or params.get("content", [""])[0]
            
            if title and text:
                final_text = f"【{title}】\n{text}"
            elif title:
                final_text = title
            else:
                final_text = text

            if not to or not final_text:
                self._json_response({"ok": False, "error": "需要 to 和 text 或 content 参数"}, 400)
                return

            # Markdown 降级
            if params.get("markdown", [""])[0] in ("1", "true", "yes"):
                final_text = markdown_to_plain(final_text)

            # 多播发送
            result = _multicast_send(to, final_text)
            status = 200 if result.get("ok") else 400
            self._json_response(result, status)

        elif path.startswith("/media/"):
            # 代理媒体文件（图片等）
            filename = path[len("/media/"):]
            filepath = media_mod.get_media_path(filename)
            if filepath:
                content_type = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
                try:
                    with open(filepath, "rb") as f:
                        file_data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(file_data)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(file_data)
                except Exception as e:
                    logger.error("读取媒体文件失败: %s", e)
                    self._json_response({"error": "read error"}, 500)
            else:
                self._json_response({"error": "file not found"}, 404)

        else:
            self._json_response({"error": "not found"}, 404)

    def _do_POST_internal(self):
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        if path == "/api/send":
            if not self._check_api_token(): return
            if not client.logged_in:
                self._json_response({"ok": False, "error": "未登录"}, 401)
                return

            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                self._json_response({"ok": False, "error": "无效 JSON"}, 400)
                return

            to = data.get("to", "")
            text = data.get("text", "") or data.get("content", "")

            # to 不传时自动取第一个联系人
            if not to and bridge.contacts:
                to = list(bridge.contacts.values())[0]

            if not to:
                self._json_response({"ok": False, "error": "无可用联系人，请指定 to 参数"}, 400)
                return
            if not text:
                self._json_response({"ok": False, "error": "缺少 text 参数"}, 400)
                return

            # Markdown 降级
            if data.get("markdown") in (True, 1, "1", "true", "yes"):
                text = markdown_to_plain(text)

            # 多播发送
            result = _multicast_send(to, text)
            status = 200 if result.get("ok") else 400
            self._json_response(result, status)

        elif path == "/api/typing":
            if not client.logged_in:
                self._json_response({"ok": False, "error": "未登录"}, 401)
                return
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                self._json_response({"ok": False, "error": "无效 JSON"}, 400)
                return

            to = data.get("to", "")
            if not to:
                self._json_response({"ok": False, "error": "缺少 to 参数"}, 400)
                return

            result = bridge.send_typing(to)
            status = 200 if result.get("ok") else 400
            self._json_response(result, status)

        elif path == "/api/ai_config":
            if not client.logged_in:
                self._json_response({"ok": False, "error": "未登录"}, 401)
                return
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                self._json_response({"ok": False, "error": "无效 JSON"}, 400)
                return

            import config as cfg
            original = cfg.load_config()
            current = original.copy()
            # 更新允许的非敏感字段
            for key in ("enabled", "provider", "model", "base_url",
                         "system_prompt", "max_history", "keepalive_remind_minutes"):
                if key in data:
                    current[key] = data[key]
            # API Key 安全覆盖：不含脱敏星号时无条件覆盖（支持清空）
            if "api_key" in data:
                new_key = data["api_key"].strip()
                if "*" not in new_key:
                    current["api_key"] = new_key
            cfg.save_config(current)
            # 切换配置时清除历史对话
            if bridge.ai_manager:
                bridge.ai_manager.clear_all_histories()
            self._json_response({"ok": True})

        elif path == "/api/ag_inbox":
            messages = bridge.ag_inbox.copy()
            bridge.ag_inbox.clear()
            self._json_response({"ok": True, "messages": messages})

        elif path == "/api/logout":
            client.clear_token()
            global _qr_data
            _qr_data = None
            # 重定向到首页
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()

        elif path == "/api/push":
            if not self._check_api_token(): return
            if not client.logged_in:
                self._json_response({"ok": False, "error": "未登录"}, 401)
                return
            
            to = ""
            text = ""
            # 支持 JSON
            if self.headers.get("Content-Type", "").startswith("application/json"):
                try:
                    data = json.loads(body) if body else {}
                    to = data.get("to", "")
                    text = data.get("text", "") or data.get("content", "")
                except:
                    pass
            else:
                # 尝试解析 Form urlencoded
                form_data = parse_qs(body.decode("utf-8"))
                to = form_data.get("to", [""])[0]
                text = form_data.get("text", [""])[0] or form_data.get("content", [""])[0]
            
            # 回退到 Query 参数
            parsed_qs = parse_qs(parsed.query)
            to = to or parsed_qs.get("to", [""])[0]
            text = text or parsed_qs.get("text", [""])[0] or parsed_qs.get("content", [""])[0]

            # 兜底：如果只有一个联系人或未指定则发给第一个
            if not to and bridge.contacts:
                to = list(bridge.contacts.values())[0]

            title = parsed_qs.get("title", [""])[0]
            if title and text:
                final_text = f"【{title}】\n{text}"
            elif title:
                final_text = title
            else:
                final_text = text

            if not to or not final_text:
                self._json_response({"ok": False, "error": "需要 to 和 text 或 content 参数"}, 400)
                return

            # Markdown 降级
            if parsed_qs.get("markdown", [""])[0] in ("1", "true", "yes"):
                final_text = markdown_to_plain(final_text)

            # 多播发送
            result = _multicast_send(to, final_text)
            status = 200 if result.get("ok") else 400
            self._json_response(result, status)

        elif path == "/api/webhook" or path.startswith("/api/webhook/"):
            # ── 改进3: 通用 Webhook 适配器 ──
            if not self._check_api_token(): return
            if not client.logged_in:
                self._json_response({"ok": False, "error": "未登录"}, 401)
                return

            # schema 从 URL 路径或 query 参数获取
            # /api/webhook/grafana 或 /api/webhook?type=grafana
            schema = ""
            if path.startswith("/api/webhook/"):
                schema = path[len("/api/webhook/"):].strip("/")
            parsed_qs = parse_qs(parsed.query)
            schema = schema or parsed_qs.get("type", [""])[0]

            to = parsed_qs.get("to", [""])[0]
            if not to and bridge.contacts:
                to = list(bridge.contacts.values())[0]
            if not to:
                self._json_response({"ok": False, "error": "无可用联系人"}, 400)
                return

            try:
                data = json.loads(body) if body else {}
            except:
                self._json_response({"ok": False, "error": "无效 JSON"}, 400)
                return

            text = _parse_webhook_payload(data, schema)
            if not text:
                self._json_response({"ok": False, "error": "无法解析 Webhook 内容"}, 400)
                return

            result = _multicast_send(to, text)
            status = 200 if result.get("ok") else 400
            self._json_response(result, status)

        elif path == "/api/send_image":
            if not self._check_api_token(): return
            if not client.logged_in:
                self._json_response({"ok": False, "error": "未登录"}, 401)
                return

            parsed_qs = parse_qs(parsed.query)
            to = ""
            image_data = None

            content_type = self.headers.get("Content-Type", "")

            if "multipart/form-data" in content_type:
                # multipart/form-data 文件上传
                to, image_data = self._parse_multipart(body, content_type)
            elif content_type.startswith("application/json"):
                # JSON body: {"to": "...", "image": "<base64图片数据>"}
                try:
                    data = json.loads(body) if body else {}
                    to = data.get("to", "")
                    img_b64 = data.get("image", "")
                    if img_b64:
                        image_data = base64.b64decode(img_b64)
                except Exception as e:
                    self._json_response({"ok": False, "error": f"JSON 解析失败: {e}"}, 400)
                    return
            elif content_type.startswith("application/octet-stream"):
                # 裸二进制流: POST body 就是图片数据, to 从 query 参数取
                image_data = body
            else:
                # 兜底：尝试当裸二进制处理
                image_data = body

            # 从 query 参数补充 to
            to = to or parsed_qs.get("to", [""])[0]

            if not to:
                # 兜底：发给第一个联系人
                if bridge.contacts:
                    to = list(bridge.contacts.values())[0]

            if not to:
                self._json_response({"ok": False, "error": "缺少 to 参数且无联系人"}, 400)
                return
            if not image_data or len(image_data) < 100:
                self._json_response({"ok": False, "error": "缺少图片数据或数据过小"}, 400)
                return

            # 图片大小限制 (10 MB)
            if len(image_data) > 10 * 1024 * 1024:
                self._json_response({"ok": False, "error": "图片大小不能超过 10MB"}, 400)
                return

            result = bridge.send_image(to, image_data)
            status = 200 if result.get("ok") else 400
            self._json_response(result, status)

        else:
            self._json_response({"error": "not found"}, 404)

    def do_POST(self):
        try:
            self._do_POST_internal()
        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.error("do_POST error: %s", e)
            try:
                self._json_response({"ok": False, "error": f"Internal error: {e}"}, 500)
            except:
                pass

    def do_OPTIONS(self):
        """CORS preflight"""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """多线程 HTTP 服务器，防止长轮询阻塞其他请求"""
    daemon_threads = True


def run_server(host: str = "0.0.0.0", port: int = 5200):
    """启动 HTTP 服务器"""
    server = ThreadingHTTPServer((host, port), BridgeHandler)
    display_host = "localhost" if host == "0.0.0.0" else host
    logger.info("HTTP 服务监听: %s:%d (绑定: %s)", display_host, port, host)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
