"""
24h 保活提醒循环

从 bridge.py 拆分而来，通过 Mixin 注入 WeChatBridge。
每 60 秒检查一次各联系人的最后活跃时间，必要时发送保活提醒。
"""

import logging
import time

import config as cfg
from delivery import WINDOW_DEADLINE_SECONDS

logger = logging.getLogger(__name__)


class KeepaliveMixin:
    """保活提醒 Mixin，注入 WeChatBridge。"""

    def _keepalive_loop(self):
        logger.info("断线提醒检查循环已启动")

        while self._running:
            try:
                time.sleep(60)
                if not self.client.logged_in:
                    continue

                cfg_data = cfg.load_config()
                remind_minutes = cfg_data.get("keepalive_remind_minutes", 0)
                if not remind_minutes or remind_minutes <= 0:
                    continue

                remind_seconds = remind_minutes * 60
                now_ts = time.time()
                for user_id, activity in list(self.activity_tracker.items()):
                    last_time = activity.get("last_receive_time", 0)
                    if not last_time:
                        continue

                    elapsed = now_ts - last_time
                    if elapsed >= WINDOW_DEADLINE_SECONDS:
                        continue

                    if not activity.get("reminded") and elapsed >= remind_seconds:
                        activity["reminded"] = True
                        self._save_contacts()

                        remaining = WINDOW_DEADLINE_SECONDS - elapsed
                        remain_h = int(remaining // 3600)
                        remain_m = int((remaining % 3600) // 60)

                        self.send(
                            user_id,
                            (
                                f"【⏰ 通道保活提醒】\n"
                                f"您已超过 {remind_minutes // 60} 小时 {remind_minutes % 60} 分钟未发送消息。\n"
                                f"微信通道将在约 {remain_h}h{remain_m}m 后自动休眠。\n"
                                f"回复任意内容即可保持连接。"
                            ),
                            source="keepalive",
                        )
            except Exception as exc:
                logger.error("保活检查异常: %s", exc)
