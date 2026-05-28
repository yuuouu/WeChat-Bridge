from __future__ import annotations

"""Web UI 公共布局模板。"""

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WeChat Bridge</title>
<link rel="icon" href="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA2NCA2NCI+PHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiByeD0iMTYiIGZpbGw9IiMwN2MxNjAiLz48ZyB0cmFuc2Zvcm09InRyYW5zbGF0ZSg4IDgpIHNjYWxlKDIpIiBmaWxsPSJub25lIiBzdHJva2U9IiNmZmYiIHN0cm9rZS13aWR0aD0iMi40IiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik0yLjk5MiAxNi4zNDJhMiAyIDAgMCAxIC4wOTQgMS4xNjdsLTEuMDY1IDMuMjlhMSAxIDAgMCAwIDEuMjM2IDEuMTY4bDMuNDEzLS45OThhMiAyIDAgMCAxIDEuMDk5LjA5MiAxMCAxMCAwIDEgMC00Ljc3Ny00LjcxOSIvPjxwYXRoIGQ9Ik04IDEyaC4wMSIvPjxwYXRoIGQ9Ik0xMiAxMmguMDEiLz48cGF0aCBkPSJNMTYgMTJoLjAxIi8+PC9nPjwvc3ZnPg==">
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
    max-width: 1180px;
    width: min(1180px, calc(100vw - 48px));
    height: min(900px, calc(100vh - 48px));
    padding: 20px;
    text-align: left;
    display: flex;
    flex-direction: column;
    border-radius: 16px;
    background: linear-gradient(135deg, #171922 0%%, #101419 100%%);
  }
  .header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; border-bottom: 1px solid rgba(255,255,255,0.05); padding-bottom: 20px;}
  .brand { display:flex; align-items:center; gap:12px; min-width: 190px; }
  .brand-icon {
    width: 40px;
    height: 40px;
    border-radius: 12px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: rgba(7,193,96,0.12);
    border: 1px solid rgba(7,193,96,0.28);
    color: #07c160;
    flex-shrink: 0;
  }
  .brand-icon svg { width: 24px; height: 24px; }
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
  .dot-yellow { background: #fbbf24; animation: pulse 2s infinite; }
  @keyframes pulse { 0%%, 100%% { opacity: 1; } 50%% { opacity: 0.4; } }

  /* Search Bar */
  .search-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 16px;
    background: rgba(0,0,0,0.15);
    border-bottom: 1px solid rgba(255,255,255,0.04);
  }
  .search-bar input {
    flex: 1;
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.08);
    color: #e0e0ea;
    border-radius: 8px;
    padding: 7px 12px 7px 28px;
    font-size: 13px;
    outline: none;
    transition: all 0.2s;
  }
  .search-bar input:focus {
    border-color: rgba(7,193,96,0.5);
    background: rgba(255,255,255,0.08);
  }
  .search-bar input::placeholder { color: #555; }
  .search-icon {
    position: absolute;
    left: 8px;
    color: #555;
    font-size: 13px;
    pointer-events: none;
  }
  .search-bar .search-count {
    color: #888;
    font-size: 11px;
    white-space: nowrap;
    min-width: 48px;
    text-align: right;
  }
  .search-bar .search-clear {
    background: none;
    border: none;
    color: #666;
    font-size: 16px;
    cursor: pointer;
    padding: 2px 6px;
    border-radius: 4px;
    transition: all 0.15s;
  }
  .search-bar .search-clear:hover { color: #aaa; background: rgba(255,255,255,0.08); }
  .msg.search-hidden { display: none !important; }
  .msg-bubble .search-highlight {
    background: rgba(250,204,21,0.35);
    color: #fef9c3;
    border-radius: 2px;
    padding: 0 1px;
  }

  /* Contact List */
  .contact-strip {
    padding: 10px 16px;
    background: rgba(0,0,0,0.12);
    border-bottom: 1px solid rgba(255,255,255,0.04);
  }
  .contact-list {
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 40px;
    overflow-x: auto;
    overflow-y: hidden;
    scrollbar-width: thin;
  }
  .contact-list::-webkit-scrollbar { height: 4px; }
  .contact-list::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.16); border-radius: 999px; }
  .contact-empty {
    color: #666;
    font-size: 13px;
    padding: 8px 2px;
  }
  .contact-item {
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 150px;
    max-width: 220px;
    height: 40px;
    padding: 0 12px;
    background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.08);
    color: #e0e0ea;
    border-radius: 8px;
    cursor: pointer;
    transition: all 0.15s;
    font-size: 13px;
    font-family: inherit;
    text-align: left;
    flex-shrink: 0;
  }
  .contact-item:hover { background: rgba(255,255,255,0.08); border-color: rgba(255,255,255,0.14); }
  .contact-item.active { background: rgba(7,193,96,0.14); border-color: rgba(7,193,96,0.55); }
  .contact-item .ci-dot {
    width: 8px; height: 8px;
    border-radius: 50%%;
    flex-shrink: 0;
  }
  .contact-item .ci-name {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: #e0e0ea;
  }
  .contact-item .ci-badge {
    font-size: 10px;
    padding: 2px 6px;
    border-radius: 999px;
    background: rgba(245,158,11,0.15);
    color: #fbbf24;
    flex-shrink: 0;
  }

  /* Date Separator */
  .date-separator {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 4px 0;
    margin: 4px 0;
  }
  .date-separator::before, .date-separator::after {
    content: '';
    flex: 1;
    height: 1px;
    background: rgba(255,255,255,0.06);
  }
  .date-separator span {
    color: #666;
    font-size: 11px;
    white-space: nowrap;
    letter-spacing: 0.3px;
  }

  /* Load More History */
  .load-more-wrap {
    text-align: center;
    padding: 8px 0 4px;
  }
  .load-more-btn {
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.08);
    color: #888;
    padding: 6px 20px;
    border-radius: 999px;
    font-size: 12px;
    cursor: pointer;
    transition: all 0.2s;
  }
  .load-more-btn:hover { background: rgba(255,255,255,0.1); color: #bbb; }
  .load-more-btn:disabled { opacity: 0.4; cursor: default; }

  /* Chat UI Styles */
  .chat-container {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    background: rgba(0,0,0,0.25);
    border-radius: 12px;
    border: 1px solid rgba(255,255,255,0.03);
    min-width: 0;
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
    min-width: 0;
    animation: fadeIn 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);
  }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
  .msg.recv { align-self: flex-start; }
  .msg.send { align-self: flex-end; }
  .msg-meta { font-size: 11px; color: #888; margin-bottom: 6px; display: flex; gap: 8px; min-width: 0; flex-wrap: wrap; }
  .msg-meta span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .msg.send .msg-meta { justify-content: flex-end; }
  .msg-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 4px;
  }
  .msg-tag {
    display: inline-flex;
    align-items: center;
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 10px;
    font-weight: 600;
    background: rgba(255,255,255,0.08);
    color: #d4d4d8;
  }
  .msg-tag.buffered { background: rgba(245,158,11,0.18); color: #fbbf24; }
  .msg-tag.pulled { background: rgba(34,197,94,0.18); color: #4ade80; }
  .msg-tag.discarded { background: rgba(239,68,68,0.18); color: #f87171; }
  .msg-tag.uncertain { background: rgba(14,165,233,0.18); color: #7dd3fc; }
  .msg-tag.warning { background: rgba(99,102,241,0.18); color: #a5b4fc; }
  .msg-bubble {
    padding: 12px 16px;
    border-radius: 14px;
    font-size: 14px;
    line-height: 1.5;
    word-break: break-word;
    overflow-wrap: anywhere;
    white-space: pre-wrap;
    max-width: 100%%;
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
    padding: 12px 16px;
    background: rgba(20,20,35,0.9);
    border-top: 1px solid rgba(255,255,255,0.05);
    display: flex;
    gap: 10px;
    align-items: flex-end;
  }
  .chat-input {
    flex: 1;
    background: #1e1e2d;
    border: 1px solid rgba(255,255,255,0.08);
    color: white;
    border-radius: 10px;
    padding: 10px 14px;
    font-size: 14px;
    font-family: inherit;
    outline: none;
    transition: all 0.2s ease;
    resize: none;
    min-height: 40px;
    max-height: 120px;
    line-height: 1.4;
    overflow-y: auto;
  }
  .chat-input:focus { border-color: rgba(7,193,96,0.6); box-shadow: 0 0 0 2px rgba(7,193,96,0.15); background-color: #252538; }
  .chat-input::placeholder { color: #555; }
  .send-btn {
    background: linear-gradient(135deg, #07c160, #06ad56);
    color: white;
    border: none;
    border-radius: 10px;
    padding: 10px 22px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s;
    flex-shrink: 0;
    align-self: flex-end;
  }
  .send-btn:hover { opacity: 0.9; transform: translateY(-1px); box-shadow: 0 4px 15px rgba(7,193,96,0.3); }
  .send-btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; box-shadow: none;}

  .header-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
  .icon {
    width: 16px;
    height: 16px;
    flex-shrink: 0;
    stroke: currentColor;
  }
  .icon-btn {
    height: 34px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    background: rgba(99,102,241,0.12);
    color: #a5b4fc;
    border: 1px solid rgba(99,102,241,0.24);
    padding: 0 10px;
    border-radius: 8px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s;
    white-space: nowrap;
  }
  .icon-btn:hover { background: rgba(99,102,241,0.22); border-color: rgba(129,140,248,0.45); }
  .icon-btn.danger {
    background: rgba(255,107,107,0.1);
    color: #ff8585;
    border-color: rgba(255,107,107,0.22);
  }
  .icon-btn.danger:hover { background: rgba(255,107,107,0.18); }
  .delivery-panel {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    padding: 8px 12px;
    margin-bottom: 10px;
    border-radius: 10px;
    background: rgba(245,158,11,0.08);
    border: 1px solid rgba(245,158,11,0.18);
    box-shadow: 0 8px 24px rgba(0,0,0,0.16);
  }
  .delivery-panel.is-hidden {
    display: none;
  }
  .delivery-compact {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    color: #f5f5f4;
    font-size: 12px;
  }
  .delivery-alert-icon {
    width: 22px;
    height: 22px;
    border-radius: 999px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: rgba(245,158,11,0.16);
    color: #fbbf24;
    flex-shrink: 0;
  }
  .delivery-compact strong {
    color: #fde68a;
    font-size: 12px;
    white-space: nowrap;
  }
  .delivery-subline {
    color: #d4d4d8;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .delivery-hint {
    color: #a1a1aa;
  }
  .delivery-more {
    flex-shrink: 0;
    position: relative;
  }
  .delivery-more summary {
    list-style: none;
    cursor: pointer;
    color: #fbbf24;
    font-size: 12px;
    font-weight: 600;
    padding: 4px 8px;
    border-radius: 8px;
  }
  .delivery-more summary::-webkit-details-marker { display: none; }
  .delivery-more summary:hover { background: rgba(255,255,255,0.08); }
  .delivery-more[open] .delivery-detail {
    position: absolute;
    right: 0;
    top: 30px;
    z-index: 20;
    width: 260px;
    box-shadow: 0 16px 40px rgba(0,0,0,0.35);
  }
  .delivery-summary {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 12px;
    flex: 1;
  }
  .delivery-item { min-width: 0; }
  .delivery-label {
    color: #888;
    font-size: 11px;
    margin-bottom: 6px;
    text-transform: uppercase;
    letter-spacing: 0.4px;
  }
  .delivery-value {
    color: #f4f4f5;
    font-size: 14px;
    font-weight: 600;
    word-break: break-word;
  }
  .delivery-detail {
    min-width: 240px;
    padding: 10px 12px;
    border-radius: 12px;
    background: #111827;
    border: 1px solid rgba(255,255,255,0.08);
  }
  .delivery-detail h3 {
    font-size: 13px;
    margin-bottom: 10px;
    color: #d4d4d8;
  }
  .delivery-detail-line {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    font-size: 12px;
    color: #a1a1aa;
    margin-bottom: 6px;
  }
  .delivery-detail-line strong {
    color: #f4f4f5;
    font-weight: 600;
  }
  .delivery-state-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 600;
    background: rgba(99,102,241,0.15);
    color: #a5b4fc;
  }
  .delivery-tech { display: none; }
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

  /* Mobile responsive */
  @media (max-width: 600px) {
    body {
      height: 100dvh;
      align-items: stretch;
      justify-content: stretch;
    }
    .card.logged-in {
      width: 100vw;
      height: 100dvh;
      border-radius: 0;
      border: none;
      padding: 12px;
      box-shadow: none;
    }
    .header {
      padding-bottom: 10px;
      margin-bottom: 10px;
      align-items: flex-start;
      gap: 10px;
      flex-wrap: wrap;
    }
    .brand { min-width: 0; width: 100%%; gap: 10px; }
    .brand-icon { width: 36px; height: 36px; border-radius: 10px; }
    .brand-icon svg { width: 21px; height: 21px; }
    .header h1 { font-size: 16px; }
    .header .logo { font-size: 24px; }
    .header-actions {
      width: 100%%;
      display: grid;
      grid-template-columns: minmax(0, 1fr) repeat(3, 32px) 34px repeat(2, 32px);
      gap: 6px;
      justify-content: stretch;
      overflow: visible;
    }
    .header-actions .form-select {
      width: 100%% !important;
      min-width: 0 !important;
      height: 32px !important;
      padding: 0 24px 0 10px !important;
      font-size: 13px;
    }
    .icon-btn { width: 32px; height: 32px; padding: 0; gap: 0; font-size: 0; border-radius: 8px; }
    .icon-btn .icon { width: 15px; height: 15px; }
    .status-badge {
      width: 34px;
      height: 32px;
      padding: 0;
      justify-content: center;
      gap: 0;
      font-size: 0;
    }
    .dot { width: 7px; height: 7px; }
    .chat-container { border-radius: 10px; }
    .search-bar { padding: 8px 10px; gap: 6px; }
    .search-bar input { height: 32px; font-size: 13px; }
    .search-bar .search-count { display: none; }
    .contact-strip { padding: 8px 10px; }
    .contact-list { min-height: 34px; }
    .contact-item { min-width: 128px; max-width: 176px; height: 34px; padding: 0 10px; font-size: 12px; }
    .chat-messages { padding: 16px 12px; gap: 14px; }
    .msg { max-width: 88%%; }
    .msg.send { width: 88%%; }
    .msg.send .msg-bubble { width: 100%%; }
    .msg-bubble { padding: 10px 12px; font-size: 13px; line-height: 1.55; word-break: normal; overflow-wrap: anywhere; }
    .msg-bubble img.chat-img, .msg-bubble video.chat-video { max-width: 100%%; height: auto; }
    .chat-input-area {
      padding: 10px 12px calc(10px + env(safe-area-inset-bottom));
      gap: 8px;
      align-items: center;
    }
    .img-upload-btn { width: 40px; height: 38px; padding: 0; flex-shrink: 0; }
    .chat-input { min-height: 38px; max-height: 96px; padding: 9px 12px; font-size: 14px; }
    .send-btn { width: 56px; height: 38px; padding: 0; align-self: center; border-radius: 9px; }
    .delivery-summary { grid-template-columns: repeat(3, 1fr); gap: 8px; }
    .delivery-panel { flex-direction: column; align-items: stretch; padding: 8px 10px; margin-bottom: 8px; }
    .delivery-compact { width: 100%%; flex-wrap: wrap; }
    .delivery-subline { white-space: normal; }
    .delivery-more[open] .delivery-detail { position: static; width: 100%%; margin-top: 8px; }
    .delivery-detail { min-width: unset; }
    .modal {
      width: calc(100vw - 24px);
      max-height: calc(100dvh - 24px);
      padding: 20px;
      border-radius: 14px;
    }
  }
  @media (max-width: 380px) {
    .header-actions {
      grid-template-columns: minmax(0, 1fr) repeat(3, 32px);
    }
    .status-badge {
      grid-column: 1 / 2;
      width: 32px;
    }
    .delivery-compact strong {
      width: calc(100%% - 30px);
    }
    .msg { max-width: 90%%; }
    .msg.send { width: 90%%; }
  }

  /* Main chat refresh */
  .card.logged-in {
    max-width: 1120px;
    width: min(1120px, calc(100vw - 40px));
    height: min(860px, calc(100vh - 40px));
    padding: 18px;
    border-radius: 14px;
    background: #111318;
    border-color: rgba(255,255,255,0.07);
  }
  .header {
    align-items: center;
    gap: 16px;
    margin-bottom: 12px;
    padding-bottom: 14px;
    border-bottom-color: rgba(255,255,255,0.07);
  }
  .brand { min-width: 0; flex: 0 0 auto; }
  .brand-icon {
    width: 38px;
    height: 38px;
    border-radius: 10px;
    background: rgba(7,193,96,0.10);
    border-color: rgba(7,193,96,0.34);
  }
  .header h1 {
    font-size: 20px;
    letter-spacing: 0;
  }
  .conversation-heading {
    flex: 1;
    min-width: 120px;
    overflow: hidden;
  }
  .conversation-title {
    color: #f4f4f5;
    font-size: 15px;
    font-weight: 700;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .conversation-meta {
    margin-top: 3px;
    color: #8b929c;
    font-size: 12px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .header-actions {
    flex-wrap: nowrap;
    gap: 8px;
    position: relative;
  }
  .account-select {
    width: 170px !important;
    min-width: 140px !important;
    height: 34px !important;
    padding: 0 30px 0 11px !important;
    border-radius: 9px;
    background-color: #181b22;
    border-color: rgba(255,255,255,0.10);
    color: #f4f4f5;
  }
  .icon-btn {
    background: #181b22;
    color: #cbd5e1;
    border-color: rgba(255,255,255,0.10);
  }
  .icon-btn:hover {
    background: #20242c;
    border-color: rgba(7,193,96,0.42);
    color: #f8fafc;
  }
  .icon-btn.icon-only {
    width: 34px;
    padding: 0;
    gap: 0;
  }
  .more-menu {
    position: relative;
  }
  .more-popover {
    display: none;
    position: absolute;
    right: 0;
    top: 42px;
    z-index: 80;
    width: 176px;
    padding: 6px;
    border-radius: 10px;
    background: #171a21;
    border: 1px solid rgba(255,255,255,0.10);
    box-shadow: 0 18px 44px rgba(0,0,0,0.38);
  }
  .more-menu.open .more-popover {
    display: block;
  }
  .menu-item {
    width: 100%%;
    height: 36px;
    display: flex;
    align-items: center;
    gap: 9px;
    padding: 0 10px;
    border: 0;
    border-radius: 8px;
    background: transparent;
    color: #d4d4d8;
    font: inherit;
    font-size: 13px;
    cursor: pointer;
    text-align: left;
  }
  .menu-item:hover {
    background: rgba(255,255,255,0.07);
    color: #fff;
  }
  .menu-item.danger {
    color: #ff8585;
  }
  .menu-item.danger:hover {
    background: rgba(255,107,107,0.12);
  }
  .status-badge {
    height: 34px;
    padding: 0 12px;
    border-radius: 9px;
  }
  .chat-container {
    background: #0d0f14;
    border-color: rgba(255,255,255,0.06);
    border-radius: 10px;
  }
  .search-bar {
    display: none;
    padding: 10px 12px;
    background: #11141a;
    border-bottom-color: rgba(255,255,255,0.06);
  }
  .chat-container.search-open .search-bar {
    display: flex;
  }
  .search-box {
    position: relative;
    flex: 1;
    display: flex;
    align-items: center;
    min-width: 0;
  }
  .search-icon {
    width: 15px;
    height: 15px;
    left: 10px;
    color: #7b8491;
  }
  .search-bar input {
    height: 34px;
    padding-left: 34px;
    border-radius: 9px;
    background: #181b22;
  }
  .search-bar .search-clear {
    width: 32px;
    height: 32px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0;
  }
  .contact-strip {
    background: #11141a;
    border-bottom-color: rgba(255,255,255,0.06);
  }
  .contact-strip.is-hidden {
    display: none;
  }
  .chat-messages {
    padding: 22px;
    gap: 16px;
  }
  .msg { max-width: 78%%; }
  .msg-meta {
    color: #858b96;
    margin-bottom: 5px;
  }
  .msg-bubble {
    border-radius: 10px;
    box-shadow: none;
  }
  .msg.recv .msg-bubble {
    background: #20242c;
    color: #eef0f3;
  }
  .msg.send .msg-bubble {
    background: #07c160;
    color: #fff;
  }
  .delivery-panel {
    padding: 6px 10px;
    margin-bottom: 10px;
    border-radius: 8px;
    background: rgba(245,158,11,0.06);
    border-color: rgba(245,158,11,0.16);
    box-shadow: none;
  }
  .delivery-alert-icon {
    width: 20px;
    height: 20px;
    background: transparent;
  }
  .delivery-compact strong {
    color: #fcd34d;
  }
  .delivery-subline {
    color: #b6bcc6;
  }
  .delivery-more summary {
    color: #f6c85f;
  }
  .chat-input-area {
    padding: 12px;
    background: #11141a;
    border-top-color: rgba(255,255,255,0.07);
  }
  .chat-input {
    background: #181b22;
    border-color: rgba(255,255,255,0.09);
    border-radius: 9px;
  }
  .img-upload-btn {
    width: 40px;
    padding: 0;
    background: #181b22;
    border-color: rgba(255,255,255,0.10);
  }
  .send-btn {
    height: 40px;
    border-radius: 9px;
    background: #07c160;
    font-weight: 700;
  }
  .send-btn:hover {
    box-shadow: none;
    transform: none;
  }

  @media (max-width: 760px) {
    body {
      height: 100dvh;
      align-items: stretch;
      justify-content: stretch;
      background: #0d0f14;
    }
    .card.logged-in {
      width: 100vw;
      height: 100dvh;
      padding: 0;
      border: 0;
      border-radius: 0;
      box-shadow: none;
    }
    .header {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px 10px;
      padding: 12px 12px 10px;
      margin-bottom: 0;
      align-items: center;
    }
    .brand {
      width: auto;
      min-width: 0;
      gap: 9px;
    }
    .brand-icon {
      width: 34px;
      height: 34px;
      border-radius: 9px;
    }
    .brand-icon svg {
      width: 20px;
      height: 20px;
    }
    .header h1 {
      font-size: 17px;
    }
    .header-actions {
      grid-column: 1 / 3;
      width: 100%%;
      display: grid;
      grid-template-columns: minmax(0, 1fr) 34px 34px 34px;
      gap: 7px;
      justify-content: stretch;
    }
    .account-select {
      width: 100%% !important;
      min-width: 0 !important;
      height: 34px !important;
      font-size: 13px;
    }
    .status-badge {
      width: 34px;
      height: 34px;
      padding: 0;
      justify-content: center;
      font-size: 0;
      gap: 0;
    }
    .icon-btn.icon-only {
      width: 34px;
      height: 34px;
    }
    .conversation-heading {
      grid-column: 1 / 3;
      min-width: 0;
      padding: 0 2px;
    }
    .conversation-title {
      font-size: 13px;
    }
    .conversation-meta {
      display: none;
    }
    .more-popover {
      right: 0;
      top: 40px;
    }
    .chat-container {
      flex: 1;
      min-height: 0;
      border-radius: 0;
      border-left: 0;
      border-right: 0;
      border-bottom: 0;
    }
    .search-bar {
      padding: 8px 10px;
    }
    .contact-strip {
      padding: 8px 10px;
    }
    .contact-list {
      min-height: 34px;
    }
    .contact-item {
      min-width: 132px;
      max-width: 180px;
      height: 34px;
      padding: 0 10px;
      font-size: 12px;
    }
    .chat-messages {
      padding: 14px 12px;
      gap: 12px;
    }
    .msg,
    .msg.send {
      width: auto;
      max-width: 82%%;
    }
    .msg-bubble {
      width: auto;
      padding: 10px 12px;
      border-radius: 9px;
      font-size: 13px;
      line-height: 1.55;
      word-break: normal;
      overflow-wrap: anywhere;
    }
    .msg-bubble img.chat-img,
    .msg-bubble video.chat-video {
      max-width: 100%%;
      height: auto;
    }
    .chat-input-area {
      padding: 9px 10px calc(9px + env(safe-area-inset-bottom));
      gap: 8px;
      align-items: center;
    }
    .img-upload-btn {
      width: 38px;
      height: 38px;
      flex-shrink: 0;
    }
    .chat-input {
      min-height: 38px;
      max-height: 96px;
      padding: 9px 11px;
      font-size: 14px;
    }
    .send-btn {
      width: 54px;
      height: 38px;
      padding: 0;
      flex-shrink: 0;
      align-self: center;
    }
    .delivery-panel {
      margin: 8px 10px;
      padding: 6px 8px;
      flex-direction: column;
      align-items: stretch;
    }
    .delivery-compact {
      width: 100%%;
      flex-wrap: nowrap;
    }
    .delivery-subline {
      min-width: 0;
      white-space: nowrap;
    }
    .delivery-more[open] .delivery-detail {
      position: static;
      width: 100%%;
      margin-top: 8px;
    }
    .delivery-detail {
      min-width: 0;
    }
  }

  @media (max-width: 380px) {
    .header h1 { display: none; }
    .brand { flex: 0 0 auto; }
    .msg,
    .msg.send {
      max-width: 86%%;
    }
  }
</style>
</head>
<body>
%s
<script>
%s
</script>
</body>
</html>"""
