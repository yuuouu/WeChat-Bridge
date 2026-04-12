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
log_format = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
log_datefmt = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    datefmt=log_datefmt,
    stream=sys.stdout,
)

# 同时输出到文件 data/run.log
_log_dir = os.environ.get("LOG_DIR", "./data")
os.makedirs(_log_dir, exist_ok=True)
_log_file = os.path.join(_log_dir, "run.log")
from logging.handlers import RotatingFileHandler
_file_handler = RotatingFileHandler(_log_file, maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
_file_handler.setFormatter(logging.Formatter(log_format, datefmt=log_datefmt))
logging.getLogger().addHandler(_file_handler)
logger = logging.getLogger("wechat-bridge")

# 确保能 import 同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ilink import ILinkClient
from bridge import WeChatBridge
import web


def main():
    port = int(os.environ.get("PORT", "5200"))
    # Docker 容器内不应打开浏览器
    auto_open = os.environ.get("NO_BROWSER", "").lower() not in ("1", "true", "yes")

    logger.info("=" * 50)
    logger.info("WeChat Bridge 启动中...")
    logger.info("端口: %d", port)
    logger.info("Token 文件: %s", os.environ.get("TOKEN_FILE", "./data/token.json"))
    webhook = os.environ.get("WEBHOOK_URL", "")
    logger.info("Webhook: %s", webhook if webhook else "(未配置)")
    logger.info("API Token: %s", "已设置" if os.environ.get("API_TOKEN") else "(未设置，接口无鉴权)")
    logger.info("日志文件: %s", _log_file)
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

    # 延迟自动打开浏览器（等待服务器就绪）
    url = f"http://localhost:{port}"
    if auto_open:
        import threading
        import webbrowser
        def _open_browser():
            logger.info("🌐 正在打开浏览器: %s", url)
            webbrowser.open(url)
        threading.Timer(1.5, _open_browser).start()

    logger.info("✅ 服务已就绪: %s", url)

    # 启动 HTTP 服务（阻塞主线程）
    web.run_server(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
