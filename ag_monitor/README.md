# Antigravity Output Monitor

## 功能

纯只读监听 Antigravity (Electron IDE) 聊天面板的 AI 输出，检测到完整回复后自动通过 `wechat-bridge` API 转发到微信。

## 架构

```
 Antigravity (Electron)     ag_monitor.py      wechat-bridge       WeChat
 ┌──────────────────┐      ┌────────────┐      ┌────────────┐      ┌────────┐
 │ Chat Panel (DOM) │─CDP─▶│  轮询检测    │─HTTP─▶│ /api/send  │─iLink─▶│  用户   │
 │ --remote-debug   │      │  文本稳定性  │      │            │      │        │
 │ :9001            │      │  判定        │      │ :5200      │      │        │
 └──────────────────┘      └────────────┘      └────────────┘      └────────┘
```

- **无侵入**: 不修改 Antigravity 或 wechat-bridge 任何源码
- **纯只读**: 仅监听 DOM 文本变化，不往输入框注入内容
- **跨平台**: Windows / macOS 通用

## 前置条件

1. Antigravity 启动参数包含 `--remote-debugging-port=9001`
2. wechat-bridge 运行中且已登录 (`http://192.168.100.1:5200`)
3. Python 依赖: `pip install playwright httpx && playwright install chromium`

## 用法

```bash
# 监听 Antigravity 并转发到指定微信用户
python ag_monitor.py --to "o9cq80359MNwxcorf0e_A7qIaKDQ"

# 仅监控不发送 (dry-run)
python ag_monitor.py

# 指定 CDP 端口和 Bridge 地址
python ag_monitor.py --cdp http://127.0.0.1:9001 --bridge http://192.168.100.1:5200 --to "用户ID"
```

## 工作原理

1. 通过 CDP 协议连接到 Antigravity 的 Electron 内核
2. 定位聊天面板的滚动容器 (`.h-full.overflow-y-auto.min-h-0`)
3. 每 1.5 秒轮询一次 `inner_text()`，检测文本增量
4. 当增量文本连续 4 秒不再变化时，判定为 AI 回复完成
5. 通过 `wechat-bridge` 的 `/api/send` 接口将回复转发到微信

## 注意事项

- 此模块与 `app/` 下的 wechat-bridge 核心代码完全独立
- 运行在 Antigravity 宿主机上（需要能访问 CDP 端口）
- 日志输出到同目录下的 `ag_monitor.log`
