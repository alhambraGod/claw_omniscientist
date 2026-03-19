"""
飞书渠道适配器 — 封装 feishu/bot.py 现有函数
"""
import asyncio
import logging
from core.logging_config import get_logger
import threading

from channels.base import ChannelAdapter
from config.settings import settings

logger = get_logger(__name__)


class FeishuAdapter(ChannelAdapter):
    name = "feishu"

    def start(self, main_loop: asyncio.AbstractEventLoop) -> None:
        """在独立线程中启动飞书 WebSocket 长连接（阻塞）"""
        from feishu import bot as feishu_bot
        feishu_bot.set_main_loop(main_loop)

        # lark_oapi/ws/client.py 在 import 时捕获 loop = asyncio.get_event_loop()
        # 必须 patch 模块变量，替换 uvloop
        from asyncio import SelectorEventLoop, set_event_loop
        import lark_oapi.ws.client as _lark_ws
        new_loop = SelectorEventLoop()
        set_event_loop(new_loop)
        _lark_ws.loop = new_loop
        try:
            feishu_bot.start()
        finally:
            new_loop.close()

    def reply_text(self, reply_info: dict, text: str) -> bool:
        message_id = reply_info.get("message_id")
        if not message_id:
            return False
        from feishu.bot import reply_message
        return reply_message(message_id, text)

    def reply_card(self, reply_info: dict, title: str, content: str, result: dict = None) -> bool:
        message_id = reply_info.get("message_id")
        if not message_id:
            return False
        from feishu.bot import reply_rich_card
        return reply_rich_card(message_id, title, content, result=result)

    def send_proactive(self, receive_id: str, title: str, content: str) -> bool:
        from feishu.bot import send_message_card
        return send_message_card(receive_id, "open_id", title, content, color="purple")

    @staticmethod
    def is_configured() -> bool:
        """
        当 FEISHU_ADAPTER_ENABLED=false 时，即使配置了凭据也不启动 WebSocket 监听。
        这允许在使用 OpenClaw Gateway 作为飞书入口时，仍保留凭据用于主动推送（HTTP API），
        同时避免双连接冲突（同一 App ID 只能有一个持久 WebSocket 连接）。
        """
        if not (settings.FEISHU_APP_ID and settings.FEISHU_APP_SECRET):
            return False
        adapter_enabled = getattr(settings, "FEISHU_ADAPTER_ENABLED", True)
        if isinstance(adapter_enabled, str):
            adapter_enabled = adapter_enabled.lower() not in ("false", "0", "no", "off")
        return bool(adapter_enabled)
