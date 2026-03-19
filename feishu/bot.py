"""
飞书 WebSocket 长连接机器人 — omniscientist_claw
作为 Channel Adapter 集成入 FastAPI 主进程（lifespan 启动）

处理逻辑：
  - P2P 私聊：所有消息均响应
  - 群聊：仅响应 @机器人 的消息
  - 立即回复「处理中」，任务推入 Redis 队列，Worker 完成后通过 Notifier 回调
"""
import asyncio
import json
import logging
from core.logging_config import get_logger
import re
import sys
import os
import threading
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest, CreateMessageRequestBody,
    ReplyMessageRequest, ReplyMessageRequestBody,
)
from config.settings import settings

logger = get_logger(__name__)

# ── 主事件循环引用（用于从 lark 线程跨越到 asyncio） ──────────────────
_main_loop: asyncio.AbstractEventLoop = None


def set_main_loop(loop: asyncio.AbstractEventLoop):
    global _main_loop
    _main_loop = loop


def _get_main_loop() -> asyncio.AbstractEventLoop:
    return _main_loop


# ── 飞书客户端 ──────────────────────────────────────────────────────
_lark_client: lark.Client = None


def _get_client() -> lark.Client:
    global _lark_client
    if _lark_client is None:
        _lark_client = (
            lark.Client.builder()
            .app_id(settings.FEISHU_APP_ID)
            .app_secret(settings.FEISHU_APP_SECRET)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )
    return _lark_client


# ── 消息发送工具 ─────────────────────────────────────────────────────
def _wrap_text(text: str) -> str:
    return json.dumps({"text": text}, ensure_ascii=False)


def _to_lark_md(text: str) -> str:
    """将标准 Markdown 转换为飞书 lark_md 兼容格式。

    lark_md 不支持 # 标题语法，需转换：
      # ~ ###### 标题  →  **标题内容**（加粗）
    其余语法（粗体/斜体/代码块/列表/链接/引用）lark_md 原生支持，无需转换。
    """
    lines = text.split("\n")
    out = []
    for line in lines:
        stripped = line.lstrip()
        # 匹配 1-6 级标题（行首 #，后跟空格）— 从多到少匹配
        if stripped.startswith("#"):
            for level in range(6, 0, -1):
                prefix = "#" * level + " "
                if stripped.startswith(prefix):
                    out.append(f"**{stripped[level + 1:].strip()}**")
                    break
            else:
                out.append(line)
        else:
            out.append(line)
    return "\n".join(out)


# 飞书 lark_md 单卡片安全内容长度（字符数）
# 飞书 API 限制 JSON body ≤ 30KB，留余量后实测单 div 约 4800 字符可稳定渲染
_CARD_CHUNK_SIZE = 4800
# 超过此长度时触发多卡片分段发送
_LONG_CONTENT_THRESHOLD = 4800
# 超过此长度时追加邮箱收集卡片（论文场景内容通常 > 5000 字符）
_EMAIL_PROMPT_THRESHOLD = 4000


def _make_card(title: str, content: str, color: str = "blue") -> str:
    """构建飞书卡片消息 JSON（lark_md 格式，不截断 — 由调用方控制长度）"""
    content = _to_lark_md(content)
    card = {
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": content,
                },
            }
        ],
        "header": {
            "title": {
                "tag": "plain_text",
                "content": title,
            },
            "template": color,
        },
    }
    return json.dumps(card, ensure_ascii=False)


def _make_rich_card(title: str, content: str, result: dict = None, color: str = "blue") -> str:
    """构建增强版飞书卡片：含 footer 元信息 + 追问建议（不截断）"""
    content = _to_lark_md(content)

    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": content,
            },
        }
    ]

    result = result or {}

    # 追问建议
    suggestions = result.get("follow_up_suggestions", [])
    if suggestions:
        elements.append({"tag": "hr"})
        sug_text = "**💡 推荐追问：**\n" + "\n".join(f"• {s}" for s in suggestions[:3])
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": sug_text},
        })

    # Footer 元信息
    iterations = result.get("iterations", 0)
    agent_name = result.get("agent_name", "")
    timestamp = result.get("timestamp", "")
    footer_parts = []
    if agent_name:
        footer_parts.append(f"Agent: {agent_name}")
    if iterations:
        footer_parts.append(f"迭代: {iterations}轮")
    if timestamp:
        try:
            from datetime import datetime
            t = datetime.fromisoformat(timestamp)
            footer_parts.append(f"完成: {t.strftime('%H:%M')}")
        except Exception:
            pass

    if footer_parts:
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "note",
            "elements": [
                {"tag": "plain_text", "content": " · ".join(footer_parts)}
            ],
        })

    card = {
        "elements": elements,
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": color,
        },
    }
    return json.dumps(card, ensure_ascii=False)


def reply_message(message_id: str, text: str) -> bool:
    """回复指定消息（纯文本，用于状态提示）"""
    logger.info(f"[Feishu↑] 文本回复 | msg_id={message_id[:16]} | {text[:80].replace(chr(10), ' ')}")
    client = _get_client()
    req = (
        ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            ReplyMessageRequestBody.builder()
            .msg_type("text")
            .content(_wrap_text(text))
            .build()
        )
        .build()
    )
    resp = client.im.v1.message.reply(req)
    if not resp.success():
        logger.error(f"[Bot] 回复失败 msg_id={message_id}: {resp.code} {resp.msg}")
    return resp.success()


def reply_message_card(message_id: str, title: str, content: str, color: str = "blue") -> bool:
    """以卡片（Markdown）格式回复消息，支持标题/代码块/列表/粗体等"""
    _preview = content[:120].replace('\n', ' ')
    logger.info(f"[Feishu↑] 卡片回复 | msg_id={message_id[:16]} | title={title} | {_preview}")
    client = _get_client()
    req = (
        ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            ReplyMessageRequestBody.builder()
            .msg_type("interactive")
            .content(_make_card(title, content, color))
            .build()
        )
        .build()
    )
    resp = client.im.v1.message.reply(req)
    if not resp.success():
        logger.error(f"[Bot] 卡片回复失败 msg_id={message_id}: {resp.code} {resp.msg}")
    return resp.success()


def reply_rich_card(message_id: str, title: str, content: str, result: dict = None, color: str = "blue") -> bool:
    """以增强卡片格式回复消息（含追问建议 + footer 元信息）"""
    _preview = content[:120].replace('\n', ' ')
    logger.info(f"[Feishu↑] 卡片回复 | msg_id={message_id[:16]} | title={title} | {_preview}")
    client = _get_client()
    req = (
        ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            ReplyMessageRequestBody.builder()
            .msg_type("interactive")
            .content(_make_rich_card(title, content, result, color))
            .build()
        )
        .build()
    )
    resp = client.im.v1.message.reply(req)
    if not resp.success():
        logger.error(f"[Bot] 增强卡片回复失败 msg_id={message_id}: {resp.code} {resp.msg}")
    return resp.success()


def send_message(receive_id: str, receive_id_type: str, text: str) -> bool:
    """主动发送消息（纯文本）"""
    client = _get_client()
    req = (
        CreateMessageRequest.builder()
        .receive_id_type(receive_id_type)
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(receive_id)
            .msg_type("text")
            .content(_wrap_text(text))
            .build()
        )
        .build()
    )
    resp = client.im.v1.message.create(req)
    if not resp.success():
        logger.error(f"[Bot] 发送失败 to={receive_id}: {resp.code} {resp.msg}")
    return resp.success()


def send_message_card(receive_id: str, receive_id_type: str, title: str, content: str, color: str = "blue") -> bool:
    """主动以卡片格式发送消息"""
    client = _get_client()
    req = (
        CreateMessageRequest.builder()
        .receive_id_type(receive_id_type)
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(receive_id)
            .msg_type("interactive")
            .content(_make_card(title, content, color))
            .build()
        )
        .build()
    )
    resp = client.im.v1.message.create(req)
    if not resp.success():
        logger.error(f"[Bot] 卡片发送失败 to={receive_id}: {resp.code} {resp.msg}")
    return resp.success()


# ── 长内容分段发送 ────────────────────────────────────────────────────

def _split_content(text: str, chunk_size: int = _CARD_CHUNK_SIZE) -> list[str]:
    """按段落边界切割长文本，避免在句中断开。"""
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    remaining = text
    while len(remaining) > chunk_size:
        cut = remaining.rfind("\n\n", 0, chunk_size)
        if cut < chunk_size // 2:
            cut = remaining.rfind("\n", 0, chunk_size)
        if cut < chunk_size // 3:
            cut = chunk_size
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def send_long_content(
    receive_id: str,
    receive_id_type: str,
    title: str,
    content: str,
    color: str = "blue",
) -> bool:
    """
    发送长内容：自动分段，每段一张卡片。
    - content ≤ _LONG_CONTENT_THRESHOLD：单卡片
    - content > _LONG_CONTENT_THRESHOLD：多卡片，标题附 (1/N)
    - content > _EMAIL_PROMPT_THRESHOLD：再追加邮箱收集卡片，并把全文存 Redis
    """
    import time as _time
    chunks = _split_content(content)
    total = len(chunks)
    all_ok = True

    for i, chunk in enumerate(chunks, 1):
        part_title = f"{title} ({i}/{total})" if total > 1 else title
        ok = send_message_card(receive_id, receive_id_type, part_title, chunk, color)
        if not ok:
            all_ok = False
        if total > 1 and i < total:
            _time.sleep(0.4)   # 防止乱序

    # 超出阈值时追加邮箱收集提示
    if len(content) > _EMAIL_PROMPT_THRESHOLD:
        word_count = len(content)
        prompt = (
            f"📄 **完整论文共约 {word_count:,} 字**，以上已分 {total} 条消息完整发出。\n\n"
            "如需保存为 **Markdown 文档**，直接回复你的 **邮箱地址**，"
            "系统将把完整论文自动发送到邮箱。\n\n"
            "> 示例：`yourname@example.com`"
        )
        # 将全文写入 Redis，key = pending_email:{open_id}，TTL 24h
        try:
            import asyncio as _asyncio, json as _json
            loop = _get_main_loop()
            if loop and loop.is_running():
                from core.cache import get_redis

                async def _store_pending():
                    r = await get_redis()
                    key = f"pending_email:{receive_id}"
                    await r.set(key, _json.dumps({"title": title, "content": content}), ex=86400)

                _asyncio.run_coroutine_threadsafe(_store_pending(), loop)
        except Exception as e:
            logger.debug(f"[Bot] 存储待发邮件内容失败: {e}")

        send_message_card(receive_id, receive_id_type, "📬 获取完整文档", prompt, "yellow")

    return all_ok


# ── 消息内容解析 ─────────────────────────────────────────────────────
def _parse_text(content_str: str) -> str:
    try:
        content = json.loads(content_str)
        text = content.get("text", "")
        text = re.sub(r'@\S+', '', text)
        return text.strip()
    except Exception:
        return ""


def _is_bot_mentioned(message) -> bool:
    bot_open_id = settings.FEISHU_BOT_OPEN_ID
    if not bot_open_id:
        try:
            content = json.loads(message.content or "{}")
            text = content.get("text", "")
            return f"@{settings.FEISHU_BOT_NAME}" in text
        except Exception:
            return False
    mentions = getattr(message, "mentions", None) or []
    return any(
        getattr(getattr(m, "id", None), "open_id", None) == bot_open_id
        for m in mentions
    )


# ── 邮件发送辅助 ─────────────────────────────────────────────────────

def _try_send_pending_email(open_id: str, email: str, message_id: str) -> None:
    """
    检测 Redis 中是否有该用户待发的完整内容。
    若有，通过 SMTP 发送完整 Markdown 文档，并清除 Redis 记录。
    """
    import asyncio as _asyncio, json as _json

    logger.info(f"[Bot] 📧 邮箱收集触发 | email={email} | open_id={open_id[:12]}…")

    loop = _get_main_loop()
    if not loop or not loop.is_running():
        logger.warning("[Bot] 主事件循环不可用，尝试 fallback 发送")
        # fallback：同步 Redis 检查 + SMTP 发送
        _try_send_pending_email_sync(open_id, email, message_id)
        return

    async def _do_send():
        try:
            from core.cache import get_redis
            r = await get_redis()
            key = f"pending_email:{open_id}"
            raw = await r.get(key)
            if not raw:
                logger.info(f"[Bot] Redis 无 pending_email | key={key}")
                reply_message(message_id, "⚠️ 未找到待发送的完整内容（可能已过期，请重新提交任务）。")
                return

            data = _json.loads(raw)
            title = data.get("title", "OpenClaw 科研论文")
            content = data.get("content", "")
            logger.info(f"[Bot] ✓ 找到待发论文 | key={key} | content_len={len(content)}")

            md_body = f"# {title}\n\n{content}\n\n---\n*由 OpenClaw 智能科研系统生成*"

            from skills.tools import send_email as _send_email
            result = await _send_email(
                to=email,
                subject=f"【OpenClaw】{title}",
                body=md_body,
            )
            if result.get("success"):
                await r.delete(key)
                reply_message(message_id, f"✅ 完整论文已发送至 **{email}**，请查收邮件！")
                logger.info(f"[Bot] ✅ 论文已邮件发送 | to={email} | open_id={open_id[:12]}…")
            else:
                err = result.get("error", "未知错误")
                reply_message(message_id, f"❌ 邮件发送失败：{err}\n\n请确认邮箱地址是否正确后重试。")
                logger.warning(f"[Bot] 邮件发送失败 | to={email} | error={err}")
        except Exception as e:
            logger.error(f"[Bot] 发送待发邮件失败: {e}", exc_info=True)
            reply_message(message_id, f"❌ 邮件发送异常：{e}")

    _asyncio.run_coroutine_threadsafe(_do_send(), loop)


def _try_send_pending_email_sync(open_id: str, email: str, message_id: str) -> None:
    """Fallback：当主事件循环不可用时，用独立 event loop 同步发送"""
    import asyncio as _asyncio, json as _json

    loop = _asyncio.new_event_loop()
    try:
        async def _do():
            from core.cache import get_redis
            r = await get_redis()
            key = f"pending_email:{open_id}"
            raw = await r.get(key)
            if not raw:
                reply_message(message_id, "⚠️ 未找到待发送的完整内容（可能已过期，请重新提交任务）。")
                return
            data = _json.loads(raw)
            title = data.get("title", "OpenClaw 科研论文")
            content = data.get("content", "")
            md_body = f"# {title}\n\n{content}\n\n---\n*由 OpenClaw 智能科研系统生成*"

            from skills.tools import send_email as _send_email
            result = await _send_email(to=email, subject=f"【OpenClaw】{title}", body=md_body)
            if result.get("success"):
                await r.delete(key)
                reply_message(message_id, f"✅ 完整论文已发送至 **{email}**，请查收邮件！")
                logger.info(f"[Bot] ✅ 论文邮件发送(sync) | to={email}")
            else:
                reply_message(message_id, f"❌ 邮件发送失败：{result.get('error')}")

        loop.run_until_complete(_do())
    except Exception as e:
        logger.error(f"[Bot] sync 邮件发送异常: {e}", exc_info=True)
        reply_message(message_id, f"❌ 邮件发送异常：{e}")
    finally:
        loop.close()


# ── 消息事件处理入口 ─────────────────────────────────────────────────
def _handle_message(data) -> None:
    """lark-oapi 消息事件回调（同步，在 lark 线程中执行）"""
    try:
        event = data.event
        msg = event.message
        sender = event.sender

        if getattr(sender, "sender_type", "") != "user":
            return

        chat_type = getattr(msg, "chat_type", "p2p")
        if chat_type == "group" and not _is_bot_mentioned(msg):
            return

        text = _parse_text(getattr(msg, "content", "{}"))
        if not text:
            return

        message_id = msg.message_id
        sender_id = getattr(getattr(sender, "sender_id", None), "open_id", "unknown")

        logger.info(f"[Bot] 收到消息 chat_type={chat_type} from={sender_id[:12]}… text={text[:60]}")
        _preview = text[:100].replace('\n', ' ')
        logger.info(f"[Feishu↓] 用户消息 | uid={sender_id[:16]} | chat={chat_type} | {_preview}")

        # ── 邮箱收集检测：如果用户回复的是邮箱地址，检查是否有待发内容 ──
        import re as _re
        if _re.fullmatch(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text.strip()):
            _try_send_pending_email(sender_id, text.strip(), message_id)
            return

        # 立即回复「处理中」
        reply_message(message_id, "🤔 已收到，正在为您处理，请稍候…")

        # 将任务推入 Redis 队列（跨线程安全）
        loop = _get_main_loop()
        if loop and loop.is_running():
            from core.cache import push_task
            task_id = str(uuid.uuid4())
            asyncio.run_coroutine_threadsafe(
                push_task({
                    "task_id": task_id,
                    "task": text,
                    "user_id": f"feishu:{sender_id}",
                    "reply_info": {
                        "channel": "feishu",
                        "message_id": message_id,
                        "open_id": sender_id,
                    },
                }),
                loop,
            )
            logger.debug(f"[Bot] Task {task_id[:12]}… enqueued to Redis")
        else:
            # fallback：没有主循环时直接同步处理（用于独立进程模式）
            threading.Thread(
                target=_run_task_fallback,
                args=(text, sender_id, message_id),
                daemon=True,
            ).start()

    except Exception as e:
        logger.error(f"[Bot] _handle_message 异常: {e}", exc_info=True)


def _run_task_fallback(text: str, sender_open_id: str, message_id: str):
    """独立进程模式的 fallback：直接调用编排器（保持向后兼容）"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        from core.registry import registry
        from core.orchestrator import Orchestrator
        if not registry._initialized:
            registry.initialize()
        orch = Orchestrator(registry)
        result = loop.run_until_complete(
            orch.execute(text, user_id=f"feishu:{sender_open_id}")
        )
        status = result.get("status", "error")
        if status == "success":
            agent_name = result.get("agent_name", "")
            content = result.get("result", "（无输出）")
            if len(content) > 2800:
                content = content[:2800] + "\n\n…（内容过长，已截断）"
            reply_text = f"【{agent_name}】\n\n{content}" if agent_name else content
        elif status == "rejected":
            issues = "；".join(result.get("issues", []))
            reply_text = f"⚠️ 请求被系统拦截：{issues}\n\n{result.get('recommendation', '')}"
        elif status == "escalated":
            reply_text = f"⏳ 任务已提交人工审核\n{result.get('recommendation', '')}"
        else:
            reply_text = f"❌ 执行失败：{result.get('error', '未知错误')}"
        reply_message(message_id, reply_text)
    except Exception as e:
        logger.error(f"[Bot] fallback 任务异常: {e}", exc_info=True)
        reply_message(message_id, f"❌ 系统内部错误：{e}")
    finally:
        loop.close()


# ── WebSocket 长连接启动 ─────────────────────────────────────────────
def start():
    """启动飞书 WebSocket 长连接，阻塞运行（自动重连）
    可在主进程中以线程方式调用，也可独立运行
    """
    if not settings.FEISHU_APP_ID or not settings.FEISHU_APP_SECRET:
        logger.error("[Bot] 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET")
        return

    logger.info(f"[Bot] 启动机器人: {settings.FEISHU_BOT_NAME}  app_id={settings.FEISHU_APP_ID}")

    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(_handle_message)
        .build()
    )

    ws_client = lark.ws.Client(
        settings.FEISHU_APP_ID,
        settings.FEISHU_APP_SECRET,
        event_handler=event_handler,
        log_level=lark.LogLevel.WARNING,
    )

    logger.info("[Bot] WebSocket 长连接建立，等待消息…")
    ws_client.start()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    start()
