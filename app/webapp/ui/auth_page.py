"""认证解锁页面。"""

from webapp.ui.layout import HTML_TEMPLATE


def render_auth_page():
    """Web UI 解锁页面（当 API_TOKEN 已设置但浏览器未认证时）"""
    content = """
  <div class="card" style="max-width: 420px; text-align: center;">
    <div class="logo">🔐</div>
    <h1 style="margin-bottom: 4px;">WeChat Bridge</h1>
    <div class="subtitle" style="margin-bottom: 24px;">请输入访问密码以解锁管理面板</div>
    <div style="margin-bottom: 16px; text-align: left;">
      <input class="form-input" id="authToken" type="password"
             placeholder="输入 API Token 密码" autocomplete="current-password"
             style="width: 100%%; font-size: 15px; padding: 12px 16px;">
    </div>
    <div id="authError" style="color: #ef4444; font-size: 13px; margin-bottom: 12px; display: none;"></div>
    <button class="btn-save" id="authBtn" style="width: 100%%; padding: 12px; font-size: 15px;" onclick="doAuth()">
      🔓 解锁
    </button>
    <div style="color: #666; font-size: 12px; margin-top: 16px;">
      密码即你设置的 API_TOKEN 环境变量
    </div>
  </div>
"""
    js = """
    const tokenIpt = document.getElementById('authToken');
    const authBtn = document.getElementById('authBtn');
    const authError = document.getElementById('authError');

    tokenIpt.addEventListener('keypress', (e) => {
      if (e.key === 'Enter') doAuth();
    });
    tokenIpt.focus();

    async function doAuth() {
      const token = tokenIpt.value.trim();
      if (!token) { authError.textContent = '请输入密码'; authError.style.display = 'block'; return; }
      authBtn.disabled = true;
      authBtn.textContent = '验证中...';
      try {
        const res = await fetch('/api/web_auth', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({token})
        });
        const data = await res.json();
        if (data.ok) {
          location.reload();
        } else {
          authError.textContent = data.error || '验证失败';
          authError.style.display = 'block';
          tokenIpt.select();
        }
      } catch(e) {
        authError.textContent = '网络错误';
        authError.style.display = 'block';
      }
      authBtn.disabled = false;
      authBtn.textContent = '🔓 解锁';
    }
"""
    return HTML_TEMPLATE % (content, js)
