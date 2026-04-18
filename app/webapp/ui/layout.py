"""Web UI 公共布局模板。"""
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
  .delivery-panel {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 16px;
    padding: 14px 16px;
    margin-bottom: 16px;
    border-radius: 14px;
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.06);
  }
  .delivery-panel.is-hidden {
    display: none;
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
    min-width: 210px;
    padding: 10px 12px;
    border-radius: 12px;
    background: rgba(0,0,0,0.18);
    border: 1px solid rgba(255,255,255,0.04);
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


