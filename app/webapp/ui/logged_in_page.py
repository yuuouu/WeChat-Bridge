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
        <div class="status-badge status-online" id="connBadge">
          <span class="dot dot-green"></span> 已连接
        </div>
        <button class="ai-settings-btn" onclick="openAISettings()">⚙️ 设置</button>
        <form action="/api/logout" method="POST" style="margin:0;">
          <button type="submit" class="logout-btn">退出登录</button>
        </form>
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
        <label class="form-label">Webhook 地址</label>
        <input class="form-input" id="webhookUrl" placeholder="https://example.com/webhook">
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
    let webhookEnabled = false;
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

        setKAFromMinutes(cfg.keepalive_remind_minutes || 0);

        document.getElementById('aiProvider').value = cfg.provider || 'openai';
        updateModels();
        document.getElementById('aiModel').value = cfg.model || '';
        document.getElementById('aiKey').value = cfg.api_key || '';
        document.getElementById('aiBaseUrl').value = cfg.base_url || '';
        document.getElementById('aiPrompt').value = cfg.system_prompt || '';
        document.getElementById('aiHistory').value = cfg.max_history || 10;
        document.getElementById('webhookUrl').value = cfg.webhook_url || '';
        document.getElementById('webhookMode').value = cfg.webhook_mode || 'unknown_command';
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

    const msgsEl = document.getElementById('msgs');
    const contactIpt = document.getElementById('contact');
    const contactList = document.getElementById('contact_list');
    const textIpt = document.getElementById('ipt');
    const sendBtn = document.getElementById('sendBtn');
    const connBadge = document.getElementById('connBadge');

    let knownMsgIds = new Set();
    let isScrolledToBottom = true;
    let initialLoad = true;
    let contactMap = {};
    let deliveryStateMap = {};
    let sendQueue = [];
    let sendInFlight = false;
    let latestServiceStatus = {
      pending_total: 0,
      active_sessions: 0,
      buffering_users: 0,
    };
    const deliveryPanel = document.getElementById('deliveryPanel');

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
        const res = await fetch('/api/status?_t=' + Date.now());
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
        const res = await fetch('/api/contacts?_t=' + Date.now());
        const data = await res.json();
        contactMap = data.contacts || {};
        deliveryStateMap = data.delivery_states || {};
        contactList.innerHTML = '';
        const entries = Object.entries(contactMap);
        for (let [uid, name] of entries) {
          const opt = document.createElement('option');
          opt.value = name;
          contactList.appendChild(opt);
        }
        // 如果联系人有且仅有一个，且输入框为空，则默认选中它
        if (entries.length === 1 && !contactIpt.value) {
          contactIpt.value = entries[0][1];
        }
        renderDeliveryStatus();
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
            const date = formatMessageTime(m.time);

            // 系统级别通知（仅对方发来且网页开启通知且不是首次加载的历史消息时）
            let isNotifyOn = localStorage.getItem('notifyEnabled') === 'true';
            if (!initialLoad && !isSend && isNotifyOn && Notification.permission === 'granted') {
                let notifyText = m.text;
                if (m.media) {
                    if (/\\.(mp4|mov|webm|3gp|avi|ts|flv)$/i.test(m.media)) notifyText = "[视频]";
                    else notifyText = "[图片]";
                }
                new Notification('WeChat Bridge - ' + m.contact, { body: notifyText, icon: "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>💬</text></svg>" });
            }

            const div = document.createElement('div');
            div.className = `msg ${m.type}`;

            // 渲染消息内容（支持图片/视频内联显示）
            let bubbleContent = m.text.replace(/</g, "&lt;");
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
              const mediaUrl = '/media/' + encodeURIComponent(m.media);
              const isVideo = /\\.(mp4|mov|webm|3gp|avi|ts|flv)$/i.test(m.media);
              const scrollJs = "document.getElementById('msgs').scrollTop = document.getElementById('msgs').scrollHeight";
              if (isVideo) {
                bubbleContent = bubbleContent.replace(
                  /\\[视频:[^\\]]*\\]/g,
                  `<video class="chat-video" src="${mediaUrl}" controls preload="metadata" playsinline onloadedmetadata="${scrollJs}"></video>`
                );
              } else {
                bubbleContent = bubbleContent.replace(
                  /\\[图片:[^\\]]*\\]/g,
                  `<img class="chat-img" src="${mediaUrl}" alt="图片" onclick="openLightbox('${mediaUrl}')" loading="lazy" onload="${scrollJs}">`
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

    async function flushSendQueue() {
      if (sendInFlight || sendQueue.length === 0) return;
      sendInFlight = true;
      sendBtn.disabled = true;
      const current = sendQueue[0];
      try {
        const res = await fetch('/api/send', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(current)
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
        showDialog('请先输入收件人名称\\n\\niLink API 限制：用户需要先给你发一条消息，系统才能获取其 user_id。\\n请在左侧联系人列表选择，或输入已经给你发过消息的联系人名称', 'warning');
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
    contactIpt.addEventListener('input', renderDeliveryStatus);

    textIpt.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        sendMsg();
      }
    });

    // 监听输入和焦点变化，触发正在输入状态
    textIpt.addEventListener('input', sendTypingStatus);
    textIpt.addEventListener('focus', sendTypingStatus);

    fetchServiceStatus();
    fetchContacts();
    fetchMsgs();
    setInterval(fetchMsgs, 2000);
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
        showToast('请先选择或输入收件人', 'error');
        return;
      }
      const formData = new FormData();
      formData.append('to', to);
      formData.append('image', file);
      showToast('正在发送剪贴板图片...');
      try {
        const res = await fetch('/api/send_image', { method: 'POST', body: formData });
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
