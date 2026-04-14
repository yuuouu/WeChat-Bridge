# 🏗️ 工作原理

> 返回 [README](../README.md)

---

## 架构时序图

```mermaid
sequenceDiagram
    participant User as 📱 微信用户
    participant API as 🌐 iLink Bot API
    participant Bridge as 🖳️ WeChat Bridge
    participant Ext as 🔔 外部服务

    Note over User,Ext: 1. 扫码登录
    Bridge->>API: GET /get_bot_qrcode
    API-->>Bridge: 二维码 URL
    Bridge-->>Bridge: Web UI 显示二维码
    User->>API: 微信扫码确认
    API-->>Bridge: bot_token + baseurl
    Note over Bridge: 登录成功，凭证已保存

    Note over User,Ext: 2. 收发消息循环
    loop 长轮询 (≤ 35s)
        Bridge->>API: POST /getupdates
        User->>API: 发消息 "你好"
        API-->>Bridge: 消息 + context_token
        Bridge-->>Ext: Webhook POST (可选)
        Bridge->>API: POST /sendtyping
        Note over User: 显示"对方正在输入中"
        Bridge->>API: POST /sendmessage (带 context_token)
        API-->>User: 推送回复
    end

    Note over User,Ext: 3. 外部 API 调用
    Ext->>Bridge: POST /api/send
    Bridge->>API: POST /sendmessage
    API-->>User: 推送消息
```

---

## 核心流程说明

### 1. 扫码登录
Bridge 向 iLink API 请求二维码 URL，通过 Web UI 展示给用户。用户使用微信扫码后，API 返回 `bot_token` 和 `baseurl`，凭证持久化至 `/data/token.json`，后续启动自动复登。

### 2. 消息收发循环
采用 **长轮询** 机制（单次最长 35 秒），持续向 iLink API 拉取新消息。收到消息后：
- 解析文本/图片/视频内容
- 检测是否为内置指令（`/status`、`/help` 等）
- 若配置了 Webhook，将消息 POST 转发至外部服务
- 若启用了 AI 助手，调用对应模型生成回复
- 通过 `context_token` 回复消息（确保在 24h 窗口内）

### 3. 外部 API 调用
外部服务（青龙面板、自动化脚本等）通过 RESTful API 向 Bridge 发送消息请求，Bridge 转发至 iLink API 完成微信消息发送。
