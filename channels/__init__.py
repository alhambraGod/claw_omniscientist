"""
channels 包 — 多渠道适配器注册表
"""
from channels.base import (
    ChannelAdapter,
    register_adapter,
    get_adapter,
    get_all_adapters,
)

__all__ = [
    "ChannelAdapter",
    "register_adapter",
    "get_adapter",
    "get_all_adapters",
]
