from __future__ import annotations

"""已登录页面。"""

from webapp.ui.layout import HTML_TEMPLATE


def render_logged_in():
    """已登录聊天界面"""
    content = """
  <div class="card logged-in">
    <div class="header">
      <div style="display:flex; align-items:center;">
        <div class="logo">💬</div>
        <h1>WeChat Bridge</h1>
      </div>
      <div class="header-actions">
        <select class="form-select" id="accountSelect" style="width:auto; min-width:150px; height:32px; padding:4px 8px;"></select>
        <button class="ai-settings-btn" onclick="setDefaultAccount()">设为默认</button>
        <button class="ai-settings-btn" onclick="openRemarkModal()">备注</button>
        <button class="ai-settings-btn" onclick="openAccountLogin()">添加账号</button>
        <div class="status-badge status-online" id="connBadge">
          <span class="dot dot-green"></span> 已连接
        </div>
        <button class="ai-settings-btn" onclick="openAISettings()">⚙️ 设置</button>
        <button type="button" class="logout-btn" onclick="logoutCurrentAccount()">退出账号</button>
      </div>
    </div>

    <div class="delivery-panel is-hidden" id="deliveryPanel">
      <div class="delivery-summary">
        <div class="delivery-item">
          <div class="delivery-label">缓存总数</div>
          <div class="delivery-value" id="pendingTotal">0</div>
        </div>
        <div class="delivery-item">
          <div class="delivery-label">活动会话</div>
          <div class="delivery-value" id="activeSessions">0</div>
        </div>
        <div class="delivery-item">
          <div class="delivery-label">受阻联系人</div>
          <div class="delivery-value" id="bufferingUsers">0</div>
        </div>
      </div>
      <div class="delivery-detail">
        <h3>当前联系人状态</h3>
        <div class="delivery-detail-line">
          <span>投递状态</span>
          <strong id="currentDeliveryStatus">NORMAL</strong>
        </div>
        <div class="delivery-detail-line">
          <span>缓存原因</span>
          <strong id="currentBlockedReason">无</strong>
        </div>
        <div class="delivery-detail-line">
          <span>待拉取</span>
          <strong id="currentPendingCount">0 条</strong>
        </div>
        <div class="delivery-detail-line">
          <span>Session</span>
          <strong id="currentSessionId">-</strong>
        </div>
        <div class="delivery-state-pill" id="currentDeliveryBadge">等待联系人</div>
      </div>
    </div>

    <div class="chat-container">
      <div class="search-bar" id="searchBar">
        <div style="position:relative; flex:1; display:flex; align-items:center;">
          <span class="search-icon">🔍</span>
          <input type="text" id="searchInput" placeholder="搜索消息..." autocomplete="off">
        </div>
        <span class="search-count" id="searchCount"></span>
        <button class="search-clear" id="searchClear" title="清除搜索">✕</button>
      </div>
      <div class="contact-strip" id="contactStrip">
        <div class="contact-list" id="contactList"></div>
      </div>
      <div class="chat-messages" id="msgs">
        <!-- 动态加载消息 -->
        <div style="text-align:center; color:#666; font-size:12px; margin-top:20px;">服务启动，等待收发消息...</div>
      </div>
      <div class="chat-input-area">
        <input type="hidden" id="contact" value="">

        <label for="imgUpload" class="img-upload-btn" title="发送图片">🖼️</label>
        <input type="file" id="imgUpload" accept="image/*" style="display: none;">

        <textarea id="ipt" class="chat-input" placeholder="输入消息，Enter 发送，Shift+Enter 换行" rows="1" autocomplete="off"></textarea>
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

    <h3 style="margin-bottom: 15px; margin-top:10px; font-size:15px; color:#ddd;">🔗 连接保活提醒 (24h限制)</h3>
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

    <h3 style="margin-bottom: 15px; font-size:15px; color:#ddd;">🔔 浏览器后台通知</h3>
    <div class="form-group">
      <div class="toggle-switch" onclick="toggleNotify()">
        <div class="toggle-track" id="notifyToggle"><div class="toggle-knob"></div></div>
        <span id="notifyToggleLabel">后台新消息通知：已关闭</span>
      </div>
      <div style="color:#888; font-size:12px; margin-top:6px;">开启后，保持网页打开即可在收到新消息时收到系统屏幕通知</div>
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
        <input class="form-input" id="aiProvider" list="aiProviderList" placeholder="选择预设或输入自定义厂商（如 ollama）" autocomplete="off" oninput="updateModels()">
        <datalist id="aiProviderList">
          <option value="openai" label="OpenAI">
          <option value="gemini" label="Google Gemini">
          <option value="claude" label="Anthropic Claude">
          <option value="deepseek" label="DeepSeek">
          <option value="minimax" label="MiniMax">
        </datalist>
      </div>
      <div class="form-group">
        <label class="form-label">模型</label>
        <input class="form-input" id="aiModel" list="aiModelList" placeholder="选择预设或输入自定义模型名称（如 qwen3:8b）" autocomplete="off">
        <datalist id="aiModelList"></datalist>
      </div>
      <div class="form-group">
        <label class="form-label">API Key</label>
        <input class="form-input" id="aiKey" type="password" placeholder="输入你的 API Key">
      </div>
      <div class="form-group">
        <label class="form-label">自定义 Base URL（可选）</label>
        <input class="form-input" id="aiBaseUrl" placeholder="留空使用默认地址；自定义厂商填写 OpenAI-compatible /v1 地址">
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

    <div style="border-top: 1px solid #444; margin: 20px 0;"></div>

    <h3 style="margin-bottom: 15px; font-size:15px; color:#ddd;">🔗 外部 Webhook</h3>
    <div class="form-group">
      <div class="toggle-switch" onclick="toggleWebhook()">
        <div class="toggle-track" id="webhookToggle"><div class="toggle-knob"></div></div>
        <span id="webhookToggleLabel">Webhook 已关闭</span>
      </div>
      <div style="color:#888; font-size:12px; margin-top:6px;">开启后，消息会按配置模式异步转发到外部服务，外部服务可再调用 /api/send 回写微信。</div>
    </div>

    <div id="webhookSettingsGroup" style="display: none;">
      <div class="form-group">
        <label class="form-label">Webhook 地址 (已由 WebhookManager 接管)</label>
        <input class="form-input" id="webhookUrl" readonly style="background: #333; cursor: not-allowed; color: #888;">
        <div style="color:#666; font-size:11px; margin-top:4px;">插件系统强制启用，自动由 WebhookManager (端口 18082) 处理。</div>
      </div>
      <div class="form-group">
        <label class="form-label">转发模式</label>
        <select class="form-select" id="webhookMode">
          <option value="unknown_command">仅未知命令</option>
          <option value="all_messages">全部消息</option>
        </select>
      </div>
      <div class="form-group">
        <label class="form-label">请求超时（秒）</label>
        <input class="form-input" id="webhookTimeout" type="number" min="1" max="30" value="5">
      </div>
    </div>

    <div style="border-top: 1px solid #444; margin: 20px 0;"></div>

    <h3 style="margin-bottom: 15px; font-size:15px; color:#ddd;">📊 匿名使用统计</h3>
    <div class="form-group">
      <div class="toggle-switch" onclick="toggleTelemetry()">
        <div class="toggle-track" id="telemetryToggle"><div class="toggle-knob"></div></div>
        <span id="telemetryToggleLabel">匿名统计已关闭</span>
      </div>
      <div style="color:#888; font-size:12px; margin-top:6px;">开启后，启动时发送匿名技术指标（版本号、操作系统、架构、Python 版本、部署方式），帮助开发者了解兼容性需求。<strong>不含任何个人信息。</strong></div>
    </div>

    <div style="border-top: 1px solid #444; margin: 20px 0;"></div>

    <div style="text-align:center; color:#666; font-size:12px; line-height:1.8;">
      <a href="https://github.com/yuuouu/WeChat-Bridge" target="_blank" rel="noopener"
         style="color:#818cf8; text-decoration:none; transition:color 0.2s;"
         onmouseover="this.style.color='#a5b4fc'" onmouseout="this.style.color='#818cf8'">
        github.com/yuuouu/WeChat-Bridge
      </a><br>
      <span style="color:#555; font-size:11px;">基于 iLink Bot API · MIT License</span>
    </div>

    <div class="modal-actions">
      <button class="btn-cancel" onclick="closeAISettings()">取消</button>
      <button class="btn-save" onclick="saveAISettings()">保存设置</button>
    </div>
  </div>
</div>

<!-- Account Login Modal -->
<div class="modal-overlay" id="accountModal">
  <div class="modal" style="max-width:420px;">
    <h2>添加微信账号</h2>
    <div class="qr-container" id="accountQrBox" style="margin:18px auto; display:flex; justify-content:center;"></div>
    <p class="hint" id="accountQrHint">请使用微信扫描二维码并在手机端确认</p>
    <div class="modal-actions">
      <button class="btn-cancel" onclick="closeAccountLogin()">取消</button>
      <button class="btn-save" onclick="openAccountLogin()">刷新二维码</button>
    </div>
  </div>
</div>

<div class="modal-overlay" id="remarkModal">
  <div class="modal" style="max-width:380px;">
    <h2>账号备注</h2>
    <p class="hint" id="remarkBotIdHint" style="word-break:break-all; font-size:12px; color:#888; margin-bottom:4px;"></p>
    <p class="hint" id="remarkUidHint" style="word-break:break-all; font-size:12px; color:#888; margin-bottom:12px;"></p>
    <input type="text" id="remarkInput" class="form-input" placeholder="输入备注名称（留空则清除）" style="width:100%; box-sizing:border-box;" maxlength="40">
    <div class="modal-actions">
      <button class="btn-cancel" onclick="closeRemarkModal()">取消</button>
      <button class="btn-save" onclick="saveRemark()">保存</button>
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
      minimax: [{id:'MiniMax-M2.7',name:'MiniMax M2.7'},{id:'MiniMax-M2.7-highspeed',name:'MiniMax M2.7 Highspeed'},{id:'MiniMax-M2.5',name:'MiniMax M2.5'},{id:'MiniMax-M2.1',name:'MiniMax M2.1'}],
    };
    let aiEnabled = false;
    let webhookEnabled = false;
    let telemetryEnabled = false;
    let keepaliveMinutes = 0;
    let currentBotId = localStorage.getItem('currentBotId') || '';
    let accounts = [];
    let accountQrTimer = null;
    let evtSource = null;

    function apiUrl(path, extraParams={}) {
      const params = new URLSearchParams(extraParams);
      if (currentBotId) params.set('bot_id', currentBotId);
      const query = params.toString();
      return query ? `${path}?${query}` : path;
    }

    function resetAccountScopedState() {
      knownMsgIds = new Set();
      allMessages = [];
      oldestLoadedId = null;
      historyExhausted = false;
      initialLoad = true;
      contactMap = {};
      deliveryStateMap = {};
      contactIpt.value = '';
      renderContactList();
      msgsEl.innerHTML = '<div style="text-align:center; color:#666; font-size:12px; margin-top:20px;">正在加载账号消息...</div>';
    }

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
      const list = document.getElementById('aiModelList');
      const currentModel = modelEl.value;
      const presets = PROVIDER_MODELS[provider] || [];
      list.innerHTML = '';
      presets.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m.id; opt.label = m.name;
        list.appendChild(opt);
      });
      // 切换到预设厂商且当前无值时，填入该厂商第一个预设模型
      if (!currentModel && presets.length) modelEl.value = presets[0].id;
    }

    function toggleAI() {
      aiEnabled = !aiEnabled;
      document.getElementById('aiToggle').classList.toggle('on', aiEnabled);
      document.getElementById('aiToggleLabel').textContent = aiEnabled ? 'AI 已启用' : 'AI 已关闭';
      document.getElementById('aiSettingsGroup').style.display = aiEnabled ? 'block' : 'none';
    }

    function toggleTelemetry() {
      telemetryEnabled = !telemetryEnabled;
      document.getElementById('telemetryToggle').classList.toggle('on', telemetryEnabled);
      document.getElementById('telemetryToggleLabel').textContent = telemetryEnabled ? '匿名统计已开启' : '匿名统计已关闭';
    }

    function toggleWebhook() {
      webhookEnabled = !webhookEnabled;
      document.getElementById('webhookToggle').classList.toggle('on', webhookEnabled);
      document.getElementById('webhookToggleLabel').textContent = webhookEnabled ? 'Webhook 已启用' : 'Webhook 已关闭';
      document.getElementById('webhookSettingsGroup').style.display = webhookEnabled ? 'block' : 'none';
    }

    let notifyEnabled = localStorage.getItem('notifyEnabled') === 'true';

    async function toggleNotify() {
      if (!notifyEnabled) {
        if (!("Notification" in window)) {
          showToast('您的浏览器不支持系统通知', 'error');
          return;
        }
        if (Notification.permission !== 'granted') {
          const perm = await Notification.requestPermission();
          if (perm !== 'granted') {
            showToast('未获得通知权限', 'error');
            return;
          }
        }
        notifyEnabled = true;
      } else {
        notifyEnabled = false;
      }
      localStorage.setItem('notifyEnabled', notifyEnabled);
      renderNotifyToggle();
    }

    function renderNotifyToggle() {
      document.getElementById('notifyToggle').classList.toggle('on', notifyEnabled);
      document.getElementById('notifyToggleLabel').textContent = notifyEnabled ? '后台新消息通知：已开启' : '后台新消息通知：已关闭';
    }


    async function openAISettings() {
      try {
        const res = await fetch('/api/ai_config');
        const cfg = await res.json();

        aiEnabled = cfg.enabled || false;
        document.getElementById('aiToggle').classList.toggle('on', aiEnabled);
        document.getElementById('aiToggleLabel').textContent = aiEnabled ? 'AI 已启用' : 'AI 已关闭';
        document.getElementById('aiSettingsGroup').style.display = aiEnabled ? 'block' : 'none';

        webhookEnabled = !!cfg.webhook_enabled;
        document.getElementById('webhookToggle').classList.toggle('on', webhookEnabled);
        document.getElementById('webhookToggleLabel').textContent = webhookEnabled ? 'Webhook 已启用' : 'Webhook 已关闭';
        document.getElementById('webhookSettingsGroup').style.display = webhookEnabled ? 'block' : 'none';

        telemetryEnabled = !!cfg.telemetry_enabled;
        document.getElementById('telemetryToggle').classList.toggle('on', telemetryEnabled);
        document.getElementById('telemetryToggleLabel').textContent = telemetryEnabled ? '匿名统计已开启' : '匿名统计已关闭';

        setKAFromMinutes(cfg.keepalive_remind_minutes || 0);

        document.getElementById('aiProvider').value = cfg.provider || 'openai';
        updateModels();
        document.getElementById('aiModel').value = cfg.model || '';
        document.getElementById('aiKey').value = cfg.api_key || '';
        document.getElementById('aiBaseUrl').value = cfg.base_url || '';
        document.getElementById('aiPrompt').value = cfg.system_prompt || '';
        document.getElementById('aiHistory').value = cfg.max_history || 10;
        document.getElementById('webhookUrl').value = cfg.webhook_url || 'http://127.0.0.1:18082/webhook';
        document.getElementById('webhookMode').value = cfg.webhook_mode || 'all_messages';
        document.getElementById('webhookTimeout').value = cfg.webhook_timeout || 5;

        renderNotifyToggle();
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
        webhook_enabled: webhookEnabled,
        webhook_url: document.getElementById('webhookUrl').value.trim(),
        webhook_mode: document.getElementById('webhookMode').value,
        webhook_timeout: parseInt(document.getElementById('webhookTimeout').value) || 5,
        telemetry_enabled: telemetryEnabled,
      };
      if (cfg.webhook_enabled && !cfg.webhook_url) {
        showToast('请先填写 Webhook 地址', 'error');
        return;
      }
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
    document.getElementById('remarkModal').addEventListener('click', e => {
      if (e.target.id === 'remarkModal') closeRemarkModal();
    });

    const msgsEl = document.getElementById('msgs');
    const contactIpt = document.getElementById('contact');
    const textIpt = document.getElementById('ipt');
    const sendBtn = document.getElementById('sendBtn');
    const connBadge = document.getElementById('connBadge');
    const contactList = document.getElementById('contactList');
    const searchInput = document.getElementById('searchInput');
    const searchCount = document.getElementById('searchCount');
    const searchClear = document.getElementById('searchClear');
    const accountSelect = document.getElementById('accountSelect');

    let knownMsgIds = new Set();
    let allMessages = [];  // sorted by time asc
    let isScrolledToBottom = true;
    let initialLoad = true;
    let contactMap = {};
    let deliveryStateMap = {};
    let sendQueue = [];
    let sendInFlight = false;
    let oldestLoadedId = null;
    let historyExhausted = false;
    let latestServiceStatus = {
      pending_total: 0,
      active_sessions: 0,
      buffering_users: 0,
    };
    const deliveryPanel = document.getElementById('deliveryPanel');

    accountSelect.addEventListener('change', () => {
      currentBotId = accountSelect.value;
      localStorage.setItem('currentBotId', currentBotId);
      resetAccountScopedState();
      connectEvents();
      fetchServiceStatus();
      fetchContacts();
      fetchMsgs();
    });

    async function loadAccounts() {
      try {
        const res = await fetch('/api/accounts?_t=' + Date.now());
        const data = await res.json();
        accounts = data.accounts || [];
        const loggedInAccounts = accounts.filter(a => a.logged_in);
        const defaultBotId = loggedInAccounts.some(a => a.bot_id === data.default_bot_id) ? data.default_bot_id : '';
        if (!loggedInAccounts.some(a => a.bot_id === currentBotId)) {
          currentBotId = defaultBotId || (loggedInAccounts[0] && loggedInAccounts[0].bot_id) || '';
        }
        if (currentBotId) {
          localStorage.setItem('currentBotId', currentBotId);
        } else {
          localStorage.removeItem('currentBotId');
        }
        accountSelect.innerHTML = '';
        loggedInAccounts.forEach(account => {
          const opt = document.createElement('option');
          opt.value = account.bot_id;
          const label = account.remark || (account.ilink_user_id ? account.ilink_user_id.substring(0, 16) : account.bot_id.substring(0, 16));
          const suffix = account.is_default ? ' · 默认' : '';
          opt.textContent = `${label}${suffix}`;
          opt.title = `Bot: ${account.bot_id}\nUID: ${account.ilink_user_id || '—'}`;
          accountSelect.appendChild(opt);
        });
        accountSelect.value = currentBotId;
        accountSelect.disabled = loggedInAccounts.length === 0;
      } catch(e) {}
    }

    async function setDefaultAccount() {
      if (!currentBotId) return;
      const res = await fetch('/api/accounts/default', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({bot_id: currentBotId})
      });
      if (res.ok) {
        showToast('默认账号已更新');
        await loadAccounts();
      } else {
        const data = await res.json();
        showToast(data.error || '设置默认账号失败', 'error');
      }
    }

    function openRemarkModal() {
      if (!currentBotId) return;
      const account = accounts.find(a => a.bot_id === currentBotId);
      document.getElementById('remarkBotIdHint').textContent = `Bot ID: ${currentBotId}`;
      document.getElementById('remarkUidHint').textContent = `UID: ${(account && account.ilink_user_id) || '—'}`;
      document.getElementById('remarkInput').value = (account && account.remark) || '';
      document.getElementById('remarkModal').classList.add('active');
      document.getElementById('remarkInput').focus();
    }

    function closeRemarkModal() {
      document.getElementById('remarkModal').classList.remove('active');
    }

    async function saveRemark() {
      if (!currentBotId) return;
      const remark = document.getElementById('remarkInput').value;
      const res = await fetch('/api/accounts/remark', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({bot_id: currentBotId, remark})
      });
      closeRemarkModal();
      if (res.ok) {
        showToast(remark.trim() ? '备注已保存' : '备注已清除');
        await loadAccounts();
      } else {
        const data = await res.json();
        showToast(data.error || '保存备注失败', 'error');
      }
    }

    async function logoutCurrentAccount() {
      if (!currentBotId) return;
      const res = await fetch('/api/accounts/logout', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({bot_id: currentBotId})
      });
      if (res.ok) {
        showToast('账号已退出');
        currentBotId = '';
        localStorage.removeItem('currentBotId');
        await loadAccounts();
        if (!currentBotId) {
          location.href = '/';
          return;
        }
        resetAccountScopedState();
        connectEvents();
        fetchServiceStatus();
        fetchContacts();
        fetchMsgs();
      } else {
        const data = await res.json();
        showToast(data.error || '退出账号失败', 'error');
      }
    }

    async function openAccountLogin() {
      const modal = document.getElementById('accountModal');
      const box = document.getElementById('accountQrBox');
      const hint = document.getElementById('accountQrHint');
      modal.classList.add('active');
      box.innerHTML = '<div style="color:#999; padding:32px;">正在获取二维码...</div>';
      hint.textContent = '请使用微信扫描二维码并在手机端确认';
      if (accountQrTimer) clearInterval(accountQrTimer);
      try {
        const res = await fetch('/api/accounts/qr', { method: 'POST' });
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || '获取二维码失败');
        box.innerHTML = `<img src="data:image/png;base64,${data.qr_image_base64}" alt="QR Code" style="width:240px; height:240px;">`;
        accountQrTimer = setInterval(async () => {
          try {
            const poll = await fetch('/api/accounts/qr_status?login_id=' + encodeURIComponent(data.login_id));
            const status = await poll.json();
            hint.textContent = status.message || '等待扫码';
            if (status.status === 'confirmed') {
              clearInterval(accountQrTimer);
              accountQrTimer = null;
              closeAccountLogin();
              currentBotId = status.bot_id || currentBotId;
              if (currentBotId) localStorage.setItem('currentBotId', currentBotId);
              await loadAccounts();
              resetAccountScopedState();
              connectEvents();
              fetchServiceStatus();
              fetchContacts();
              fetchMsgs();
              showToast('账号登录成功');
            } else if (status.status === 'expired') {
              clearInterval(accountQrTimer);
              accountQrTimer = null;
              hint.textContent = '二维码已过期，请刷新';
            }
          } catch(e) {}
        }, 3000);
      } catch(e) {
        box.innerHTML = `<div style="color:#ef4444; padding:24px;">${String(e.message || e).slice(0, 120)}</div>`;
      }
    }

    function closeAccountLogin() {
      document.getElementById('accountModal').classList.remove('active');
      if (accountQrTimer) clearInterval(accountQrTimer);
      accountQrTimer = null;
    }

    // === Contact List ===
    function selectContact(userId) {
      contactIpt.value = userId;
      renderContactList();
      renderDeliveryStatus();
      textIpt.focus();
    }
    function renderContactList() {
      contactList.innerHTML = '';
      const entries = Object.entries(contactMap);
      if (!entries.length) {
        contactIpt.value = '';
        const empty = document.createElement('div');
        empty.className = 'contact-empty';
        empty.textContent = '暂无联系人';
        contactList.appendChild(empty);
        return;
      }
      const currentUserId = contactIpt.value;
      if (!entries.some(([uid]) => uid === currentUserId)) {
        contactIpt.value = entries[0][0];
      }
      entries.forEach(([uid, name]) => {
        const ds = deliveryStateMap[uid];
        const status = ds ? ds.status : 'NORMAL';
        const pending = ds ? (ds.pending_count || 0) : 0;
        let dotColor = '#07c160'; // green = normal
        if (['WARNED','BUFFERING'].includes(status)) dotColor = '#fbbf24';
        else if (status === 'READY_PULL') dotColor = '#818cf8';
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'contact-item' + (uid === contactIpt.value ? ' active' : '');
        item.title = name;
        item.innerHTML = `<span class="ci-dot" style="background:${dotColor}"></span><span class="ci-name">${name}</span>${pending > 0 ? `<span class="ci-badge">${pending}条</span>` : ''}`;
        item.addEventListener('click', () => selectContact(uid));
        contactList.appendChild(item);
      });
    }

    // === Search ===
    let searchDebounce = null;
    searchInput.addEventListener('input', () => {
      clearTimeout(searchDebounce);
      searchDebounce = setTimeout(applySearch, 150);
    });
    searchClear.addEventListener('click', () => {
      searchInput.value = '';
      applySearch();
      searchInput.focus();
    });
    function applySearch() {
      const q = searchInput.value.trim().toLowerCase();
      const msgs = msgsEl.querySelectorAll('.msg');
      let matched = 0;
      msgs.forEach(el => {
        const bubble = el.querySelector('.msg-bubble');
        if (!bubble) { el.classList.remove('search-hidden'); return; }
        // restore original text (remove old highlights)
        bubble.querySelectorAll('.search-highlight').forEach(hl => {
          hl.replaceWith(hl.textContent);
        });
        bubble.normalize();
        if (!q) { el.classList.remove('search-hidden'); return; }
        const text = bubble.textContent.toLowerCase();
        if (text.includes(q)) {
          el.classList.remove('search-hidden');
          matched++;
          // highlight matches in text nodes only
          highlightTextNodes(bubble, q);
        } else {
          el.classList.add('search-hidden');
        }
      });
      // also hide/show date separators
      msgsEl.querySelectorAll('.date-separator').forEach(sep => {
        sep.classList.toggle('search-hidden', !!q);
      });
      searchCount.textContent = q ? `${matched} 条` : '';
    }
    function highlightTextNodes(node, query) {
      if (node.nodeType === Node.TEXT_NODE) {
        const idx = node.textContent.toLowerCase().indexOf(query);
        if (idx === -1) return;
        const before = node.textContent.slice(0, idx);
        const match = node.textContent.slice(idx, idx + query.length);
        const after = node.textContent.slice(idx + query.length);
        const frag = document.createDocumentFragment();
        if (before) frag.appendChild(document.createTextNode(before));
        const mark = document.createElement('span');
        mark.className = 'search-highlight';
        mark.textContent = match;
        frag.appendChild(mark);
        if (after) {
          const afterNode = document.createTextNode(after);
          frag.appendChild(afterNode);
        }
        node.parentNode.replaceChild(frag, node);
      } else if (node.nodeType === Node.ELEMENT_NODE && !node.classList.contains('search-highlight') && !['IMG','VIDEO','SVG'].includes(node.tagName)) {
        Array.from(node.childNodes).forEach(child => highlightTextNodes(child, query));
      }
    }

    // === Textarea auto-resize ===
    textIpt.addEventListener('input', () => {
      textIpt.style.height = 'auto';
      textIpt.style.height = Math.min(textIpt.scrollHeight, 120) + 'px';
    });

    // === Date separator helper ===
    function getDateLabel(tsSeconds) {
      const dt = new Date(tsSeconds * 1000);
      const today = new Date();
      const yesterday = new Date(today);
      yesterday.setDate(yesterday.getDate() - 1);
      const dtStr = `${dt.getFullYear()}-${dt.getMonth()}-${dt.getDate()}`;
      const todayStr = `${today.getFullYear()}-${today.getMonth()}-${today.getDate()}`;
      const yestStr = `${yesterday.getFullYear()}-${yesterday.getMonth()}-${yesterday.getDate()}`;
      if (dtStr === todayStr) return '今天';
      if (dtStr === yestStr) return '昨天';
      return `${dt.getMonth()+1}月${dt.getDate()}日`;
    }
    function insertDateSeparator(label) {
      const sep = document.createElement('div');
      sep.className = 'date-separator';
      sep.innerHTML = `<span>${label}</span>`;
      msgsEl.appendChild(sep);
    }

    // === Render a single message element ===
    function renderMessageEl(m) {
      const isSend = m.type === 'send';
      const date = formatMessageTime(m.time);
      const div = document.createElement('div');
      div.className = `msg ${m.type}`;
      let bubbleContent = m.text.replace(/</g, '&lt;');
      const tags = [];
      if (m.delivery_stage === 'buffered') tags.push('<span class="msg-tag buffered">已缓存</span>');
      if (m.delivery_stage === 'pulled') tags.push('<span class="msg-tag pulled">已补拉</span>');
      if (m.delivery_stage === 'discarded') tags.push('<span class="msg-tag discarded">已丢弃</span>');
      if (m.delivery_stage === 'uncertain') tags.push('<span class="msg-tag uncertain">可能已送达</span>');
      if (m.meta && m.meta.limit_warning) tags.push('<span class="msg-tag warning">系统提醒</span>');
      if (m.meta && m.meta.blocked_reason === 'window_24h') tags.push('<span class="msg-tag warning">24h失效</span>');
      if (m.meta && m.meta.blocked_reason === 'quota_10') tags.push('<span class="msg-tag warning">10条限制</span>');
      if (m.meta && m.meta.blocked_reason === 'api_limit') tags.push('<span class="msg-tag warning">上游限制</span>');
      if (m.media) {
        const mediaUrl = apiUrl('/media/' + encodeURIComponent(m.media));
        const isVideo = /\\.(mp4|mov|webm|3gp|avi|ts|flv)$/i.test(m.media);
        if (isVideo) {
          bubbleContent = bubbleContent.replace(
            /\\[视频:[^\\]]*\\]/g,
            `<video class="chat-video" src="${mediaUrl}" controls preload="metadata" playsinline></video>`
          );
        } else {
          bubbleContent = bubbleContent.replace(
            /\\[图片:[^\\]]*\\]/g,
            `<img class="chat-img" src="${mediaUrl}" alt="图片" onclick="openLightbox('${mediaUrl}')" loading="lazy">`
          );
        }
      }
      div.innerHTML = `
        <div class="msg-meta">
          <span>${isSend ? '我 ➞ ' + m.contact : m.contact}</span>
          <span>${date}</span>
        </div>
        ${tags.length ? `<div class="msg-tags">${tags.join('')}</div>` : ''}
        <div class="msg-bubble">${bubbleContent}</div>
      `;
      return div;
    }

    // === Full re-render all messages ===
    function renderAllMessages() {
      msgsEl.innerHTML = '';
      // Load-more button
      const loadWrap = document.createElement('div');
      loadWrap.className = 'load-more-wrap';
      const btnText = historyExhausted ? '已加载全部历史' : '⬆ 加载更多历史';
      loadWrap.innerHTML = `<button class="load-more-btn" id="loadMoreBtn" onclick="loadMoreHistory()" ${historyExhausted ? 'disabled' : ''}>${btnText}</button>`;
      msgsEl.appendChild(loadWrap);
      // Render messages with date separators
      let prevDateLabel = '';
      allMessages.forEach(m => {
        const dateLabel = getDateLabel(m.time);
        if (dateLabel !== prevDateLabel) {
          const sep = document.createElement('div');
          sep.className = 'date-separator';
          sep.innerHTML = `<span>${dateLabel}</span>`;
          msgsEl.appendChild(sep);
          prevDateLabel = dateLabel;
        }
        msgsEl.appendChild(renderMessageEl(m));
      });
    }

    // === Load More History ===
    async function loadMoreHistory() {
      if (!oldestLoadedId || historyExhausted) return;
      const btn = document.getElementById('loadMoreBtn');
      if (btn) { btn.disabled = true; btn.textContent = '加载中...'; }
      try {
        const res = await fetch(apiUrl('/api/messages', {before_id: oldestLoadedId, limit: 200, _t: Date.now()}));
        const data = await res.json();
        const older = data.messages.filter(m => !knownMsgIds.has(m.msg_id));
        if (older.length === 0) {
          historyExhausted = true;
          if (btn) { btn.textContent = '已加载全部历史'; btn.disabled = true; }
          return;
        }
        // Merge into allMessages (prepend older, keep sorted by time)
        older.forEach(m => { knownMsgIds.add(m.msg_id); });
        allMessages = [...older, ...allMessages];
        oldestLoadedId = Math.min(oldestLoadedId, ...older.map(m => m.id));
        // Full re-render
        const prevScrollH = msgsEl.scrollHeight;
        renderAllMessages();
        // Maintain scroll position (offset by new content height)
        msgsEl.scrollTop = msgsEl.scrollHeight - prevScrollH;
        // Re-apply search if active
        if (searchInput.value.trim()) applySearch();
      } catch(e) {
        if (btn) { btn.disabled = false; btn.textContent = '加载失败，点击重试'; }
      }
    }

    msgsEl.addEventListener('scroll', () => {
      isScrolledToBottom = msgsEl.scrollHeight - msgsEl.scrollTop - msgsEl.clientHeight < 50;
    });

    function resolveCurrentDeliverySummary() {
      const current = contactIpt.value.trim();
      if (!current) return null;
      const directUid = Object.keys(contactMap).find(uid => uid === current);
      if (directUid && deliveryStateMap[directUid]) return deliveryStateMap[directUid];
      const matchedUid = Object.entries(contactMap).find(([uid, name]) => name === current);
      if (!matchedUid) return null;
      return deliveryStateMap[matchedUid[0]] || null;
    }

    function renderDeliveryStatus() {
      const summary = resolveCurrentDeliverySummary();
      document.getElementById('currentDeliveryStatus').textContent = summary ? summary.status : 'NORMAL';
      document.getElementById('currentBlockedReason').textContent = summary ? (summary.blocked_reason_text || '无') : '无';
      document.getElementById('currentPendingCount').textContent = summary ? `${summary.pending_count || 0} 条` : '0 条';
      document.getElementById('currentSessionId').textContent = summary && summary.active_overflow_session_id ? summary.active_overflow_session_id.slice(0, 12) : '-';
      document.getElementById('currentDeliveryBadge').textContent = summary ? `${summary.contact || '当前联系人'} · ${summary.status}` : '等待联系人';
      const currentStatus = summary ? summary.status : '';
      const hasCurrentLimit = !!(
        summary &&
        (
          ['WARNED', 'BUFFERING', 'READY_PULL'].includes(currentStatus) ||
          (summary.pending_count || 0) > 0 ||
          !!summary.active_overflow_session_id
        )
      );
      const hasGlobalLimit = (
        (latestServiceStatus.pending_total || 0) > 0 ||
        (latestServiceStatus.active_sessions || 0) > 0 ||
        (latestServiceStatus.buffering_users || 0) > 0
      );
      deliveryPanel.classList.toggle('is-hidden', !(hasCurrentLimit || hasGlobalLimit));
    }

    function formatMessageTime(tsSeconds) {
      const dt = new Date(tsSeconds * 1000);
      const mm = dt.getMonth() + 1;
      const dd = dt.getDate();
      const hh = String(dt.getHours()).padStart(2, '0');
      const mi = String(dt.getMinutes()).padStart(2, '0');
      const ss = String(dt.getSeconds()).padStart(2, '0');
      return `${mm}-${dd} ${hh}:${mi}:${ss}`;
    }

    async function fetchServiceStatus() {
      try {
        const res = await fetch(apiUrl('/api/status', {_t: Date.now()}));
        const data = await res.json();
        latestServiceStatus = data;
        document.getElementById('pendingTotal').textContent = data.pending_total || 0;
        document.getElementById('activeSessions').textContent = data.active_sessions || 0;
        document.getElementById('bufferingUsers').textContent = data.buffering_users || 0;
        if (data.logged_in) {
          connBadge.className = 'status-badge status-online';
          connBadge.innerHTML = '<span class="dot dot-green"></span> 已连接';
        } else {
          connBadge.className = 'status-badge status-offline';
          connBadge.innerHTML = '<span class="dot dot-red"></span> 已断开';
        }
        renderDeliveryStatus();
      } catch (e) {}
    }

    async function fetchContacts() {
      try {
        const res = await fetch(apiUrl('/api/contacts', {_t: Date.now()}));
        const data = await res.json();
        contactMap = data.contacts || {};
        deliveryStateMap = data.delivery_states || {};
        renderContactList();
        renderDeliveryStatus();
      } catch (e) {}
    }

    async function fetchMsgs() {
      try {
        const res = await fetch(apiUrl('/api/messages', {_t: Date.now()}));
        const data = await res.json();
        let appended = false;

        data.messages.forEach(m => {
          if (!knownMsgIds.has(m.msg_id)) {
            if(initialLoad && knownMsgIds.size === 0) msgsEl.innerHTML = '';
            knownMsgIds.add(m.msg_id);
            allMessages.push(m);
            const isSend = m.type === 'send';

            // 系统级别通知
            let isNotifyOn = localStorage.getItem('notifyEnabled') === 'true';
            if (!initialLoad && !isSend && isNotifyOn && Notification.permission === 'granted') {
                let notifyText = m.text;
                if (m.media) {
                    if (/\\.(mp4|mov|webm|3gp|avi|ts|flv)$/i.test(m.media)) notifyText = "[视频]";
                    else notifyText = "[图片]";
                }
                new Notification('WeChat Bridge - ' + m.contact, { body: notifyText, icon: "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>💬</text></svg>" });
            }

            // Append to DOM (incremental for new real-time messages)
            // Date separator check against last element
            const dateLabel = getDateLabel(m.time);
            const existingSeps = msgsEl.querySelectorAll('.date-separator');
            const lastSep = existingSeps.length > 0 ? existingSeps[existingSeps.length - 1] : null;
            const lastSepLabel = lastSep ? lastSep.textContent.trim() : '';
            if (dateLabel !== lastSepLabel) {
              insertDateSeparator(dateLabel);
            }
            msgsEl.appendChild(renderMessageEl(m));
            appended = true;
          }
        });
        if (initialLoad) {
          // Insert load-more button at top
          const loadWrap = document.createElement('div');
          loadWrap.className = 'load-more-wrap';
          loadWrap.innerHTML = '<button class="load-more-btn" id="loadMoreBtn" onclick="loadMoreHistory()">⬆ 加载更多历史</button>';
          msgsEl.insertBefore(loadWrap, msgsEl.firstChild);
          if (data.messages.length > 0) {
            oldestLoadedId = Math.min(...data.messages.map(m => m.id));
          }
          msgsEl.scrollTop = msgsEl.scrollHeight;
          initialLoad = false;
        } else if (appended && isScrolledToBottom) {
          msgsEl.scrollTo({ top: msgsEl.scrollHeight, behavior: 'smooth' });
        }
      } catch(e) {}
    }

    async function flushSendQueue() {
      if (sendInFlight || sendQueue.length === 0) return;
      sendInFlight = true;
      sendBtn.disabled = true;
      const current = sendQueue[0];
      try {
        const res = await fetch('/api/send', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({...current, bot_id: currentBotId})
        });
        const data = await res.json();
        if (res.ok) {
          if (data.buffered) {
            showToast(data.message || '消息已进入缓存队列');
          } else if (data.uncertain) {
            showToast(data.message || '接口超时，消息可能已送达');
          }
          await fetchMsgs();
          msgsEl.scrollTo({ top: msgsEl.scrollHeight, behavior: 'smooth' });
        } else {
          showDialog(data.error, 'error');
        }
      } catch(e) {
        showDialog('无法连接到服务器，请检查网络', 'error');
      } finally {
        sendQueue.shift();
        sendInFlight = false;
        sendBtn.disabled = false;
        if (sendQueue.length > 0) {
          flushSendQueue();
        } else {
          textIpt.focus();
        }
      }
    }

    function sendMsg() {
      const to = contactIpt.value.trim();
      const text = textIpt.value.trim();
      if (!text) return;
      if (!to) {
        showDialog('请先在顶部联系人中选择收件人\\n\\niLink API 限制：用户需要先给你发一条消息，系统才能获取其 user_id。', 'warning');
        return;
      }
      sendQueue.push({to, text});
      textIpt.value = '';
      flushSendQueue();
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
          body: JSON.stringify({to, bot_id: currentBotId})
        });
      } catch (e) {}
    }

    const imgUpload = document.getElementById('imgUpload');
    imgUpload.addEventListener('change', async (e) => {
      const file = e.target.files[0];
      if (!file) return;

      const to = contactIpt.value.trim();
      if (!to) {
        showToast('请先在顶部联系人中选择收件人', 'error');
        imgUpload.value = ''; // 清除选择，以便可重复选同一张图
        return;
      }

      const formData = new FormData();
      formData.append('to', to);
      formData.append('image', file);
      formData.append('bot_id', currentBotId);

      // 显示上传中的状态，用 toast
      showToast('图片上传发送中...');

      try {
        const res = await fetch(apiUrl('/api/send_image'), {
          method: 'POST',
          body: formData
        });

        const data = await res.json();
        if (res.ok) {
          if (data.buffered) {
            showToast(data.message || '图片已进入缓存队列');
          } else {
            showToast('\u56fe\u7247\u53d1\u9001\u6210\u529f\uff01\u624b\u673a\u7aef\u53ef\u67e5\u770b');
          }
          await fetchMsgs(); // 立即刷新查看消息
          msgsEl.scrollTo({ top: msgsEl.scrollHeight, behavior: 'smooth' });
        } else {
          showToast('图片发送失败: ' + data.error, 'error');
        }
      } catch(error) {
        showToast('网络错误', 'error');
      }
      imgUpload.value = ''; // 重置 file input
    });

    sendBtn.addEventListener('click', sendMsg);

    textIpt.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMsg();
      }
    });

    // 监听输入和焦点变化，触发正在输入状态
    textIpt.addEventListener('input', sendTypingStatus);
    textIpt.addEventListener('focus', sendTypingStatus);

    async function initAccountView() {
      await loadAccounts();
      connectEvents();
      fetchServiceStatus();
      fetchContacts();
      fetchMsgs();
    }

    initAccountView();

    // SSE EventSource for real-time updates
    function connectEvents() {
      if (evtSource) evtSource.close();
      if (!currentBotId) return;
      evtSource = new EventSource(apiUrl('/api/events'));
      evtSource.onmessage = function(e) {
        if (e.data === ": keepalive") return;
        try {
          const payload = JSON.parse(e.data);
          if (['message_received', 'message_sent', 'ai_reply_ready'].includes(payload.event)) {
            fetchMsgs();
          }
        } catch (err) {}
      };
    }

    // Fallback polling (less frequent)
    setInterval(fetchMsgs, 15000);
    setInterval(fetchServiceStatus, 5000);
    setInterval(fetchContacts, 5000);

    // 图片全屏预览
    function openLightbox(url) {
      document.getElementById('lightboxImg').src = url;
      document.getElementById('imgLightbox').classList.add('active');
    }

    // Ctrl+V 剪贴板粘贴图片发送
    async function sendImageFile(file) {
      const to = contactIpt.value.trim();
      if (!to) {
        showToast('请先在顶部联系人中选择收件人', 'error');
        return;
      }
      const formData = new FormData();
      formData.append('to', to);
      formData.append('image', file);
      formData.append('bot_id', currentBotId);
      showToast('正在发送剪贴板图片...');
      try {
        const res = await fetch(apiUrl('/api/send_image'), { method: 'POST', body: formData });
        const data = await res.json();
        if (res.ok) {
          if (data.buffered) {
            showToast(data.message || '图片已进入缓存队列');
          } else {
            showToast('图片发送成功！');
          }
          await fetchMsgs();
          msgsEl.scrollTo({ top: msgsEl.scrollHeight, behavior: 'smooth' });
        } else {
          showToast('图片发送失败: ' + data.error, 'error');
        }
      } catch(e) { showToast('网络错误', 'error'); }
    }

    document.addEventListener('paste', (e) => {
      const items = e.clipboardData && e.clipboardData.items;
      if (!items) return;
      for (const item of items) {
        if (item.type.startsWith('image/')) {
          e.preventDefault();
          const file = item.getAsFile();
          if (file) sendImageFile(file);
          return;
        }
      }
    });
"""
    return HTML_TEMPLATE % (content, js)
