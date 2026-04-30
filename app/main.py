#!/usr/bin/env python3
"""
WeChat Bridge 入口
启动 HTTP API 服务 + 消息长轮询循环
"""

import logging
import os
import signal
import sys

# 配置日志
log_format = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
log_datefmt = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    datefmt=log_datefmt,
    stream=sys.stdout,
)

# 确保 CWD 为项目根目录（app/ 的上级），使所有 ./data/ 路径正确
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_project_root)

# 同时输出到文件 data/run.log
_log_dir = os.environ.get("LOG_DIR", os.path.join(_project_root, "data"))
os.makedirs(_log_dir, exist_ok=True)
_log_file = os.path.join(_log_dir, "run.log")
from logging.handlers import RotatingFileHandler

_file_handler = RotatingFileHandler(_log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
_file_handler.setFormatter(logging.Formatter(log_format, datefmt=log_datefmt))
logging.getLogger().addHandler(_file_handler)
logger = logging.getLogger("wechat-bridge")

# 确保能 import 同目录模块
sys.path.insert(0, os.path.join(_project_root, "app"))

import config as cfg
import web
from bridge import WeChatBridge
from ilink import ILinkClient
from version import __version__


def main():
    port = int(os.environ.get("PORT", "5200"))
    # Docker 容器内不应打开浏览器
    auto_open = os.environ.get("NO_BROWSER", "").lower() not in ("1", "true", "yes")

    logger.info("=" * 50)
    logger.info("WeChat Bridge 启动中...")
    logger.info("版本: %s", __version__)
    logger.info("端口: %d", port)
    logger.info("Token 文件: %s", os.environ.get("TOKEN_FILE", "./data/token.json"))
    runtime_cfg = cfg.load_config()
    webhook_url = runtime_cfg.get("webhook_url", "").strip()
    webhook_enabled = bool(runtime_cfg.get("webhook_enabled")) and bool(webhook_url)
    webhook_mode = runtime_cfg.get("webhook_mode", "unknown_command")
    if webhook_enabled:
        logger.info("Webhook: %s (%s)", webhook_url, webhook_mode)
    elif webhook_url:
        logger.info("Webhook: %s (已配置未启用)", webhook_url)
    else:
        logger.info("Webhook: (未配置)")
    logger.info("API Token: %s", "已设置" if os.environ.get("API_TOKEN") else "(未设置，接口无鉴权)")
    logger.info("日志文件: %s", _log_file)
    logger.info("=" * 50)

    # 初始化客户端
    ilink_client = ILinkClient()
    wechat_bridge = WeChatBridge(ilink_client)

    # ==== 检测更新 ====
    def check_for_updates():
        try:
            import json
            import subprocess
            import urllib.request

            # 尝试获取本地 Git Commit
            try:
                local_commit = (
                    subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.STDOUT, cwd=_project_root)
                    .decode("utf-8")
                    .strip()
                )
            except Exception:
                local_commit = None

            req = urllib.request.Request(
                "https://api.github.com/repos/yuuouu/WeChat-Bridge/commits/main",
                headers={"User-Agent": "WeChat-Bridge-Updater"},
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode())
                remote_commit = data.get("sha")

                if local_commit and remote_commit:
                    if local_commit != remote_commit:
                        logger.warning("🎉 【发现新版本】当前运行的版本较旧！")
                        logger.warning("👉 更新方式 1 (推荐): 在项目目录下运行 'git pull' 后重启服务")
                        logger.warning("👉 更新方式 2 (一键): 重新运行 Windows PowerShell 一键安装命令")
                        logger.warning("👉 更新方式 3 (Docker): 运行 'docker compose pull && docker compose up -d'")
                        logger.warning("查看更新日志: https://github.com/yuuouu/WeChat-Bridge/commits/main")
                    else:
                        logger.info("✅ 更新检查: 当前已是最新版本")
                else:
                    logger.info("✅ 更新检查: 最新远程版本为 %s", remote_commit[:7] if remote_commit else "未知")
        except Exception as e:
            logger.debug("检测更新失败: %s", e)

    import threading

    threading.Thread(target=check_for_updates, daemon=True).start()

    # ==== 新增：注入 AI 模块 ====
    try:
        from ai_chat import AIChatManager

        ai_manager = AIChatManager(cfg.load_config, cfg.save_config)
        wechat_bridge.ai_manager = ai_manager
        logger.info("✅ AI 模块已挂载")
    except Exception as e:
        logger.error("❌ AI 模块加载失败: %s", e)
    # ==========================

    # 注入 Web 层运行上下文
    web.set_context(ilink_client, wechat_bridge, os.environ.get("API_TOKEN", ""))

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
