"""
钉钉渠道适配器 — 基于 dingtalk-stream SDK (Stream 模式)
无需公网 IP / 回调域名，与飞书 WebSocket 长连接模式对称
"""
import asyncio
import json
import logging
from core.logging_config import get_logger
import uuid
from typing import Optional

from channels.base import ChannelAdapter
from config.settings import settings

logger = get_logger(__name__)

# 主事件循环引用（用于从钉钉 SDK 线程跨越到 asyncio）
_main_loop: Optional[asyncio.AbstractEventLoop] = None


def _normalize_markdown(text: str) -> str:
    """将标准 Markdown 标题转换为钉钉兼容格式。

    钉钉 Markdown 仅可靠支持 # 和 ##，3-6 级标题渲染不稳定，
    统一转换为加粗文本以确保显示正常。
    """
    lines = text.split("\n")
    out = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#"):
            for level in range(6, 0, -1):
                prefix = "#" * level + " "
                if stripped.startswith(prefix):
                    title_text = stripped[level + 1:].strip()
                    if level <= 2:
                        # 1-2 级保留原生标题语法
                        out.append(f"{'#' * level} {title_text}")
                    else:
                        # 3-6 级转为加粗
                        out.append(f"**{title_text}**")
                    break
            else:
                out.append(line)
        else:
            out.append(line)
    return "\n".join(out)


def _reply_text_via_webhook(webhook_url: str, text: str) -> bool:
    """通过 session_webhook 回复纯文本"""
    try:
        import requests
        resp = requests.post(webhook_url, json={
            "msgtype": "text",
            "text": {"content": text},
        }, timeout=5)
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"[DingTalk] webhook reply failed: {e}")
        return False


def _reply_markdown_via_webhook(webhook_url: str, title: str, content: str) -> bool:
    """通过 session_webhook 回复 Markdown"""
    try:
        import requests
        content = _normalize_markdown(content)
        resp = requests.post(webhook_url, json={
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": content,
            },
        }, timeout=5)
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"[DingTalk] webhook markdown reply failed: {e}")
        return False


def _format_card_markdown(title: str, content: str, result: dict = None) -> str:
    """将结果格式化为钉钉 Markdown 格式（含追问建议 + footer）"""
    result = result or {}
    md_parts = [content]

    # 追问建议
    suggestions = result.get("follow_up_suggestions", [])
    if suggestions:
        md_parts.append("\n---\n**💡 推荐追问：**")
        for s in suggestions[:3]:
            md_parts.append(f"- {s}")

    # Footer 元信息
    agent_name = result.get("agent_name", "")
    iterations = result.get("iterations", 0)
    footer_parts = []
    if agent_name:
        footer_parts.append(f"Agent: {agent_name}")
    if iterations:
        footer_parts.append(f"迭代: {iterations}轮")
    if footer_parts:
        md_parts.append(f"\n---\n> {' · '.join(footer_parts)}")

    return "\n".join(md_parts)


def _handle_incoming_message(incoming_message) -> None:
    """处理收到的钉钉消息（在 SDK 线程中执行）"""
    try:
        text = ""
        if incoming_message.text:
            text = incoming_message.text.get("content", "").strip()
        if not text:
            return

        sender_id = incoming_message.sender_staff_id or incoming_message.sender_id or "unknown"
        sender_nick = incoming_message.sender_nick or ""
        conversation_id = incoming_message.conversation_id or ""
        session_webhook = incoming_message.session_webhook or ""
        message_id = incoming_message.message_id or ""

        logger.info(
            f"[DingTalk] 收到消息 from={sender_nick}({sender_id[:12]}…) text={text[:60]}"
        )

        # 立即回复「处理中」
        if session_webhook:
            _reply_text_via_webhook(session_webhook, "🤔 已收到，正在为您处理，请稍候…")

        # 将任务推入 Redis 队列（跨线程安全）
        loop = _main_loop
        if loop and loop.is_running():
            from core.cache import push_task
            task_id = str(uuid.uuid4())
            asyncio.run_coroutine_threadsafe(
                push_task({
                    "task_id": task_id,
                    "task": text,
                    "user_id": f"dingtalk:{sender_id}",
                    "reply_info": {
                        "channel": "dingtalk",
                        "session_webhook": session_webhook,
                        "sender_id": sender_id,
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                    },
                }),
                loop,
            )
            logger.debug(f"[DingTalk] Task {task_id[:12]}… enqueued to Redis")
        else:
            logger.warning("[DingTalk] Main event loop not available, cannot enqueue task")

    except Exception as e:
        logger.error(f"[DingTalk] _handle_incoming_message 异常: {e}", exc_info=True)


class DingTalkAdapter(ChannelAdapter):
    name = "dingtalk"

    def start(self, main_loop: asyncio.AbstractEventLoop) -> None:
        """启动钉钉 Stream 长连接（阻塞，需在独立线程中调用）"""
        global _main_loop
        _main_loop = main_loop

        try:
            import dingtalk_stream
            from dingtalk_stream import AckMessage
        except ImportError:
            logger.error("[DingTalk] dingtalk-stream 未安装，请运行: pip install dingtalk-stream")
            return

        credential = dingtalk_stream.Credential(
            settings.DINGTALK_APP_KEY,
            settings.DINGTALK_APP_SECRET,
        )

        client = dingtalk_stream.DingTalkStreamClient(credential)

        class _Handler(dingtalk_stream.ChatbotHandler):
            """继承 SDK ChatbotHandler，重写 process 方法"""

            async def process(self, callback: dingtalk_stream.CallbackMessage):
                incoming_message = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
                _handle_incoming_message(incoming_message)
                return AckMessage.STATUS_OK, "ok"

        client.register_callback_handler(
            dingtalk_stream.chatbot.ChatbotMessage.TOPIC,
            _Handler(),
        )

        logger.info("[DingTalk] Stream 长连接建立，等待消息…")
        client.start_forever()

    def reply_text(self, reply_info: dict, text: str) -> bool:
        webhook = reply_info.get("session_webhook")
        if not webhook:
            return False
        return _reply_text_via_webhook(webhook, text)

    def reply_card(self, reply_info: dict, title: str, content: str, result: dict = None) -> bool:
        webhook = reply_info.get("session_webhook")
        if not webhook:
            return False
        md_content = _format_card_markdown(title, content, result)
        return _reply_markdown_via_webhook(webhook, title, md_content)

    def send_proactive(self, receive_id: str, title: str, content: str) -> bool:
        """通过钉钉 OpenAPI 主动推送单聊消息"""
        robot_code = settings.DINGTALK_ROBOT_CODE
        if not robot_code:
            logger.warning("[DingTalk] DINGTALK_ROBOT_CODE 未配置，无法主动推送")
            return False

        try:
            import requests

            # 获取 access_token
            token_resp = requests.post(
                "https://api.dingtalk.com/v1.0/oauth2/accessToken",
                json={
                    "appKey": settings.DINGTALK_APP_KEY,
                    "appSecret": settings.DINGTALK_APP_SECRET,
                },
                timeout=10,
            )
            token_data = token_resp.json()
            access_token = token_data.get("accessToken", "")
            if not access_token:
                logger.error(f"[DingTalk] 获取 accessToken 失败: {token_data}")
                return False

            # 发送单聊消息
            resp = requests.post(
                "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend",
                headers={
                    "x-acs-dingtalk-access-token": access_token,
                    "Content-Type": "application/json",
                },
                json={
                    "robotCode": robot_code,
                    "userIds": [receive_id],
                    "msgKey": "sampleMarkdown",
                    "msgParam": json.dumps({
                        "title": title,
                        "text": _normalize_markdown(content[:4000]),
                    }),
                },
                timeout=10,
            )

            ok = resp.status_code == 200 and resp.json().get("processQueryKey")
            if ok:
                logger.info(f"[DingTalk] Proactive message sent to {receive_id[:12]}…")
            else:
                logger.warning(f"[DingTalk] Proactive send failed: {resp.text}")
            return ok

        except Exception as e:
            logger.error(f"[DingTalk] Proactive send error: {e}", exc_info=True)
            return False

    @staticmethod
    def is_configured() -> bool:
        return bool(settings.DINGTALK_APP_KEY and settings.DINGTALK_APP_SECRET)
