"""
Channel Adapter 抽象基类 + 全局适配器注册表
每个 IM 渠道（飞书/钉钉/Slack/...）实现此接口即可接入 OpenClaw
"""
import asyncio
import logging
from core.logging_config import get_logger
from abc import ABC, abstractmethod
from typing import Optional

logger = get_logger(__name__)

# ── 全局注册表 ─────────────────────────────────────────────────────────
_adapters: dict[str, "ChannelAdapter"] = {}


def register_adapter(adapter: "ChannelAdapter") -> None:
    _adapters[adapter.name] = adapter
    logger.info(f"[Channels] Registered adapter: {adapter.name}")


def get_adapter(channel: str) -> Optional["ChannelAdapter"]:
    return _adapters.get(channel)


def get_all_adapters() -> list["ChannelAdapter"]:
    return list(_adapters.values())


# ── 抽象基类 ───────────────────────────────────────────────────────────

class ChannelAdapter(ABC):
    """IM 渠道适配器统一接口"""

    name: str  # "feishu" / "dingtalk" / ...

    @abstractmethod
    def start(self, main_loop: asyncio.AbstractEventLoop) -> None:
        """在独立线程中启动长连接（阻塞调用）
        main_loop: FastAPI 主事件循环，用于跨线程提交 coroutine
        """

    @abstractmethod
    def reply_text(self, reply_info: dict, text: str) -> bool:
        """回复纯文本消息"""

    @abstractmethod
    def reply_card(self, reply_info: dict, title: str, content: str, result: dict = None) -> bool:
        """回复富文本卡片（Markdown + 元信息 + 追问建议）"""

    @abstractmethod
    def send_proactive(self, receive_id: str, title: str, content: str) -> bool:
        """主动推送消息给指定用户（用于日报等场景）"""
