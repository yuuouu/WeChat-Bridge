"""二维码登录页面。"""

import base64
import io
import logging
import time

import qrcode

from webapp.context import WebAppContext
from webapp.ui.layout import HTML_TEMPLATE

logger = logging.getLogger(__name__)


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


def render_qr_page(ctx: WebAppContext):
    """二维码登录页面"""
    # 每 3 分钟刷新一次二维码
    if not ctx.qr_cache.data or (time.time() - ctx.qr_cache.updated_at > 180):
        try:
            ctx.qr_cache.data = ctx.client.get_qrcode()
            ctx.qr_cache.updated_at = time.time()
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

    qr_url = ctx.qr_cache.data.get("qrcode_img_content", "")
    qrcode_id = ctx.qr_cache.data.get("qrcode", "")

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
