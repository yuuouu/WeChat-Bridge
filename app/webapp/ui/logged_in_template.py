from __future__ import annotations

"""已登录页面 LOGGED_IN_CONTENT。"""

LOGGED_IN_CONTENT = """
  <div class="card logged-in">
    <div class="header">
      <div class="brand">
        <div class="brand-icon" aria-hidden="true">
          <svg class="icon" viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M2.992 16.342a2 2 0 0 1 .094 1.167l-1.065 3.29a1 1 0 0 0 1.236 1.168l3.413-.998a2 2 0 0 1 1.099.092 10 10 0 1 0-4.777-4.719"></path>
            <path d="M8 12h.01"></path>
            <path d="M12 12h.01"></path>
            <path d="M16 12h.01"></path>
          </svg>
        </div>
        <h1>WeChat Bridge</h1>
      </div>
      <div class="conversation-heading">
        <div class="conversation-title" id="currentContactName">等待联系人</div>
        <div class="conversation-meta" id="currentContactMeta">当前会话</div>
      </div>
      <div class="header-actions">
        <select class="form-select account-select" id="accountSelect" aria-label="选择账号"></select>
        <div class="status-badge status-online" id="connBadge">
          <span class="dot dot-green"></span> 已连接
        </div>
        <button class="icon-btn icon-only" onclick="toggleSearchBar()" title="搜索消息" aria-label="搜索消息">
          <svg class="icon" viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="11" cy="11" r="8"></circle><path d="m21 21-4.3-4.3"></path>
          </svg>
        </button>
        <div class="more-menu" id="moreMenu">
          <button class="icon-btn icon-only" onclick="toggleMoreMenu(event)" title="更多操作" aria-label="更多操作" aria-haspopup="true" aria-expanded="false" id="moreMenuBtn">
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="1"></circle><circle cx="19" cy="12" r="1"></circle><circle cx="5" cy="12" r="1"></circle>
            </svg>
          </button>
          <div class="more-popover" id="morePopover">
            <button class="menu-item" onclick="setDefaultAccount(); closeMoreMenu();" type="button">
              <svg class="icon" viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 2l3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21 7 14.2 2 9.3l6.9-1L12 2z"></path>
              </svg>
              <span>设为默认</span>
            </button>
            <button class="menu-item" onclick="openRemarkModal(); closeMoreMenu();" type="button">
              <svg class="icon" viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 20h9"></path><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"></path>
              </svg>
              <span>备注账号</span>
            </button>
            <button class="menu-item" onclick="openAccountLogin(); closeMoreMenu();" type="button">
              <svg class="icon" viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"></path><circle cx="9" cy="7" r="4"></circle><path d="M19 8v6"></path><path d="M22 11h-6"></path>
              </svg>
              <span>添加账号</span>
            </button>
            <button class="menu-item" onclick="openAISettings(); closeMoreMenu();" type="button">
              <svg class="icon" viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1A2 2 0 1 1 4.2 17l.1-.1A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.6-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1A2 2 0 1 1 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6 1.7 1.7 0 0 0 10 3V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1A2 2 0 1 1 19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"></path>
              </svg>
              <span>系统设置</span>
            </button>
            <button class="menu-item danger" onclick="logoutCurrentAccount(); closeMoreMenu();" type="button">
              <svg class="icon" viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path><path d="M16 17l5-5-5-5"></path><path d="M21 12H9"></path>
              </svg>
              <span>退出账号</span>
            </button>
          </div>
        </div>
      </div>
    </div>

    <div class="delivery-panel is-hidden" id="deliveryPanel">
      <div class="delivery-compact">
        <span class="delivery-alert-icon">
          <svg class="icon" viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"></path><path d="M12 9v4"></path><path d="M12 17h.01"></path>
          </svg>
        </span>
        <strong id="deliveryHeadline">消息已暂存</strong>
        <span class="delivery-subline" id="deliverySubline">
          <span id="deliveryHint">等待恢复后可补拉</span> · 缓存 <span id="pendingTotal">0</span> 条 · 受阻 <span id="bufferingUsers">0</span>
          <span class="delivery-tech"> · 会话 <span id="activeSessions">0</span></span>
        </span>
      </div>
      <details class="delivery-more">
        <summary>详情</summary>
        <div class="delivery-detail">
          <h3>当前联系人状态</h3>
          <div class="delivery-detail-line">
            <span>状态</span>
            <strong id="currentDeliveryStatus">正常</strong>
          </div>
          <div class="delivery-detail-line">
            <span>连续发送</span>
            <strong id="currentConsecutiveCount">0/10</strong>
          </div>
          <div class="delivery-detail-line">
            <span>原因</span>
            <strong id="currentBlockedReason">无</strong>
          </div>
          <div class="delivery-detail-line">
            <span>待拉取</span>
            <strong id="currentPendingCount">0 条</strong>
          </div>
          <div class="delivery-detail-line delivery-tech">
            <span>Session</span>
            <strong id="currentSessionId">-</strong>
          </div>
          <div class="delivery-state-pill" id="currentDeliveryBadge">等待联系人</div>
        </div>
      </details>
    </div>

    <div class="chat-container">
      <div class="search-bar" id="searchBar">
        <div class="search-box">
          <svg class="search-icon icon" viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="11" cy="11" r="8"></circle><path d="m21 21-4.3-4.3"></path>
          </svg>
          <input type="text" id="searchInput" placeholder="搜索消息..." autocomplete="off">
        </div>
        <span class="search-count" id="searchCount"></span>
        <button class="search-clear" id="searchClear" title="清除搜索" aria-label="清除搜索">
          <svg class="icon" viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M18 6 6 18"></path><path d="m6 6 12 12"></path>
          </svg>
        </button>
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

        <label for="mediaUpload" class="img-upload-btn" title="发送媒体" aria-label="发送媒体">
          <svg class="icon" viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <rect x="3" y="3" width="18" height="18" rx="2"></rect><circle cx="9" cy="9" r="2"></circle><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"></path>
          </svg>
        </label>
        <input type="file" id="mediaUpload" style="display: none;">

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
        <div class="toggle-track on" id="telemetryToggle"><div class="toggle-knob"></div></div>
        <span id="telemetryToggleLabel">匿名统计已开启</span>
      </div>
      <div style="color:#888; font-size:12px; margin-top:6px;">开启后，启动与运行时发送匿名技术指标（版本号、操作系统、架构、Python 版本、部署方式、运行天数、绑定账号数、AI提供商、插件数、Webhook状态、启用功能），帮助开发者了解兼容性需求。<strong>不含任何个人信息。</strong></div>
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
