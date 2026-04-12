"""
Antigravity Output Monitor
==========================
纯只读监听 Antigravity 聊天面板的 AI 输出，
检测到新的完整回复后自动通过 wechat-bridge /api/send 发送给指定微信用户。

架构: 独立进程，通过 CDP 监听 + HTTP API 发送，不侵入 wechat-bridge 现有代码。

用法:
  python ag_monitor.py                                             # 使用默认配置
  python ag_monitor.py --to "o9cq80359MNwxcorf0e_A7qIaKDQ"        # 指定发送目标
  python ag_monitor.py --bridge http://192.168.100.1:5200          # 指定 bridge 地址
  python ag_monitor.py --cdp http://127.0.0.1:9001                 # 指定 CDP 端口

依赖:
  pip install playwright httpx
  playwright install chromium

跨平台: Windows / macOS 通用 (需要 Antigravity 开启 --remote-debugging-port)
"""
import asyncio
import argparse
import json
import time
import sys
import io
import logging
import os
import httpx
from playwright.async_api import async_playwright

# Windows 下强制 UTF-8 输出
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ag_monitor.log")
log = logging.getLogger("ag-monitor")
log.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)
log.addHandler(_sh)
_fh = logging.FileHandler(LOG_FILE, encoding='utf-8', mode='a')
_fh.setFormatter(_fmt)
log.addHandler(_fh)

# ===== 默认配置 =====
DEFAULT_CDP_URL = "http://127.0.0.1:9001"
DEFAULT_BRIDGE_URL = "http://192.168.100.1:5200"
DEFAULT_SEND_TO = ""               # 留空则不发送，仅监控
POLL_INTERVAL_MS = 1500            # 轮询间隔（毫秒）
STABLE_SECONDS = 4                 # 文本稳定多少秒后认为回复完成
MAX_WX_MSG_LEN = 4000              # 微信单条消息最大长度


async def send_typing_to_wechat(bridge_url: str, to: str):
    """通过 wechat-bridge /api/typing 发送"正在输入"状态"""
    if not to:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                f"{bridge_url}/api/typing",
                json={"to": to},
            )
            if resp.status_code == 200:
                log.info("=> WeChat [%s]: typing...", to[:16])
            else:
                log.warning("WeChat typing failed: %d %s",
                            resp.status_code, resp.text[:100])
    except Exception as e:
        log.warning("WeChat typing error: %s", e)


async def send_to_wechat(bridge_url: str, to: str, text: str):
    """通过 wechat-bridge /api/send 发送消息到微信"""
    if not text.strip() or not to:
        return
    # 超长消息分段发送
    chunks = []
    while text:
        if len(text) <= MAX_WX_MSG_LEN:
            chunks.append(text)
            break
        # 尝试在换行处截断
        cut = text.rfind('\n', 0, MAX_WX_MSG_LEN)
        if cut <= 0:
            cut = MAX_WX_MSG_LEN
        chunks.append(text[:cut])
        text = text[cut:].lstrip('\n')

    for i, chunk in enumerate(chunks):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{bridge_url}/api/send",
                    json={"to": to, "text": chunk},
                )
                if resp.status_code == 200:
                    prefix = f"({i+1}/{len(chunks)}) " if len(chunks) > 1 else ""
                    log.info("=> WeChat [%s]: %s%s",
                             to[:16], prefix, chunk[:50].replace('\n', ' '))
                else:
                    log.warning("WeChat send failed: %d %s",
                                resp.status_code, resp.text[:200])
        except Exception as e:
            log.error("WeChat send error: %s", e)


async def monitor_loop(cdp_url: str, bridge_url: str, send_to: str):
    """主监听循环"""
    async with async_playwright() as p:
        log.info("Connecting to Antigravity CDP: %s", cdp_url)
        browser = await p.chromium.connect_over_cdp(cdp_url)
        ctx = browser.contexts[0]

        # 找到主工作台页面
        main_page = None
        for page in ctx.pages:
            t = await page.title()
            if "Antigravity" in t and "Launchpad" not in t:
                main_page = page
                break
        if not main_page:
            main_page = ctx.pages[0]

        log.info("Target page: %s", await main_page.title())
        if send_to:
            log.info("Monitoring AI output -> WeChat [%s]", send_to)
        else:
            log.info("Monitoring AI output (dry-run, no --to specified)")
        log.info("Bridge: %s", bridge_url)
        log.info("Press Ctrl+C to stop\n")

        # 状态变量
        last_known_text = ""       # 上一次已知的完整消息区域文本
        pending_delta = ""         # 正在生长的增量文本
        last_change_time = 0.0     # 上次文本变化的时间戳
        is_generating = False      # AI 是否正在生成中

        # 初始化基线
        scroll_sel = '.h-full.overflow-y-auto.min-h-0'
        try:
            scroll_container = main_page.locator(scroll_sel)
            last_known_text = await scroll_container.inner_text()
            log.info("Baseline captured: %d chars", len(last_known_text))
        except Exception as e:
            log.warning("Failed to get baseline: %s", e)

        while True:
            try:
                await main_page.wait_for_timeout(POLL_INTERVAL_MS)

                # 1. 拉取微信发来的未读消息并填充到输入框
                try:
                    async with httpx.AsyncClient(timeout=3) as client:
                        inbox_resp = await client.post(f"{bridge_url}/api/ag_inbox", json={})
                        if inbox_resp.status_code == 200:
                            msgs = inbox_resp.json().get("messages", [])
                            for m in msgs:
                                combo_text = f"微信用户 [{m['from']}] 说：\n{m['text']}"
                                log.info("<= 拟填充: %s", combo_text.replace('\n', ' ')[:40])
                                input_loc = main_page.locator('[contenteditable="true"]').first
                                if await input_loc.count() > 0:
                                    await input_loc.fill(combo_text)
                                    await asyncio.sleep(0.5)
                except Exception as e:
                    pass  # 忽略偶尔的网络请求错误

                # 2. 读取当前消息区域全文，检测 AI 输出状态
                current_text = await scroll_container.inner_text()

                if len(current_text) > len(last_known_text):
                    delta = current_text[len(last_known_text):].strip()
                    if delta and delta != pending_delta:
                        # 文本仍在变化 (AI 正在流式输出)
                        pending_delta = delta
                        last_change_time = time.time()
                        if not is_generating:
                            is_generating = True
                            log.info("[DETECT] AI output started...")
                            # 立即发送"正在输入"状态
                            if send_to:
                                await send_typing_to_wechat(bridge_url, send_to)

                elif is_generating and pending_delta:
                    # 文本长度没增长但之前检测到了增量
                    pass

                # 检查稳定性: 如果有 pending_delta 且已经 N 秒没变化 -> 发送
                if pending_delta and is_generating:
                    elapsed = time.time() - last_change_time
                    if elapsed >= STABLE_SECONDS:
                        log.info("[STABLE] AI output complete (%d chars, stable %.1fs)",
                                 len(pending_delta), elapsed)

                        # 发送到微信
                        if send_to:
                            await send_to_wechat(bridge_url, send_to, pending_delta)
                        else:
                            log.info("[DRY-RUN] Would send: %s...", pending_delta[:80])

                        # 重置状态
                        last_known_text = current_text
                        pending_delta = ""
                        is_generating = False

            except KeyboardInterrupt:
                log.info("Stopped by user")
                break
            except Exception as e:
                log.warning("Poll error: %s", e)
                await asyncio.sleep(3)


def main():
    parser = argparse.ArgumentParser(
        description="Antigravity Output Monitor -> WeChat Bridge",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 监听 Antigravity 输出并发送给指定微信用户
  python ag_monitor.py --to "o9cq80359MNwxcorf0e_A7qIaKDQ"

  # 仅监控不发送 (dry-run)
  python ag_monitor.py

  # 指定 CDP 和 Bridge 地址
  python ag_monitor.py --cdp http://127.0.0.1:9001 --bridge http://192.168.100.1:5200 --to "用户ID"
""")
    parser.add_argument("--cdp", default=DEFAULT_CDP_URL,
                        help="Antigravity CDP URL (default: %(default)s)")
    parser.add_argument("--bridge", default=DEFAULT_BRIDGE_URL,
                        help="WeChat Bridge URL (default: %(default)s)")
    parser.add_argument("--to", default=DEFAULT_SEND_TO,
                        help="WeChat recipient (user_id or display name)")
    parser.add_argument("--interval", type=int, default=POLL_INTERVAL_MS,
                        help="Poll interval ms (default: %(default)s)")
    parser.add_argument("--stable", type=int, default=STABLE_SECONDS,
                        help="Stable seconds before send (default: %(default)s)")
    args = parser.parse_args()

    try:
        asyncio.run(monitor_loop(args.cdp, args.bridge, args.to))
    except KeyboardInterrupt:
        log.info("Bye")


if __name__ == "__main__":
    main()
