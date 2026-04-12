#!/usr/bin/env python3
"""
WeChat Bridge 入口
启动 HTTP API 服务 + 消息长轮询循环
"""

import os
import sys
import signal
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("wechat-bridge")

# 确保能 import 同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ilink import ILinkClient
from bridge import WeChatBridge
import web


def main():
    port = int(os.environ.get("PORT", "5200"))

    logger.info("=" * 50)
    logger.info("WeChat Bridge 启动中...")
    logger.info("端口: %d", port)
    logger.info("Token 文件: %s", os.environ.get("TOKEN_FILE", "./data/token.json"))
    webhook = os.environ.get("WEBHOOK_URL", "")
    logger.info("Webhook: %s", webhook if webhook else "(未配置)")
    logger.info("API Token: %s", "已设置" if os.environ.get("API_TOKEN") else "(未设置，接口无鉴权)")
    logger.info("=" * 50)

    # 初始化客户端
    ilink_client = ILinkClient()
    wechat_bridge = WeChatBridge(ilink_client)

    # ==== 新增：注入 AI 模块 ====
    try:
        import config as cfg
        from ai_chat import AIChatManager
        ai_manager = AIChatManager(cfg.load_config, cfg.save_config)
        wechat_bridge.ai_manager = ai_manager
        logger.info("✅ AI 模块已挂载")
    except Exception as e:
        logger.error("❌ AI 模块加载失败: %s", e)
    # ==========================

    # 注入全局引用供 web 模块使用
    web.client = ilink_client
    web.bridge = wechat_bridge

    # 启动消息轮询（后台线程）
    wechat_bridge.start()

    # 优雅退出
    def shutdown(signum, frame):
        logger.info("收到退出信号，正在关闭...")
        wechat_bridge.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # 启动 HTTP 服务（阻塞主线程）
    web.run_server(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
