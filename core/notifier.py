"""
统一通知路由 — 根据 reply_info.channel 分发消息到对应渠道
支持：feishu / dingtalk / email / websocket / api（轮询）

IM 渠道通过 Channel Adapter 注册表统一分发
"""
import logging
from core.logging_config import get_logger
from typing import Optional

from config.settings import settings

logger = get_logger(__name__)


def _format_result_text(result: dict) -> str:
    """将任务结果格式化为纯文本（email / fallback 使用）"""
    status = result.get("status", "error")
    if status == "success":
        agent_name = result.get("agent_name", "")
        content = result.get("result", "（无输出）")
        if len(content) > 2800:
            content = content[:2800] + "\n\n…（内容过长，已截断）"
        return f"【{agent_name}】\n\n{content}" if agent_name else content
    elif status == "timeout":
        elapsed = result.get("elapsed_seconds", 0)
        tid = result.get("task_id", "")[:16]
        killer = "watchdog" if result.get("killed_by") == "watchdog" else "系统"
        return (
            f"⏱️ 任务超时已自动终止（{killer}触发）\n\n"
            f"运行时长：{elapsed}s | 任务编号：{tid}\n\n"
            "建议：简化任务描述，或拆分为多个子任务后重新提交。"
        )
    elif status == "rejected":
        issues = "；".join(result.get("issues", []))
        return f"⚠️ 请求被系统拦截：{issues}\n\n{result.get('recommendation', '')}"
    elif status == "escalated":
        return f"⏳ 任务已提交人工审核\n{result.get('recommendation', '')}"
    else:
        return f"❌ 执行失败：{result.get('error', '未知错误')}"


def _build_card_content(result: dict) -> tuple[str, str, str]:
    """从 result 构建 (title, content, color) 用于卡片回复"""
    from config.settings import settings
    status = result.get("status", "error")
    if status == "success":
        agent_name = result.get("agent_name", "OpenClaw 助理")
        content = result.get("result", "（无输出）")
        # 不在此处截断，由底层 send_long_content 自动分段处理
        return f"🦞 {agent_name}", content, "blue"
    elif status == "timeout":
        elapsed = result.get("elapsed_seconds", 0)
        tid = result.get("task_id", "")[:16]
        killer = "watchdog 强制终止" if result.get("killed_by") == "watchdog" else "系统自动终止"
        content = (
            f"你的科研任务已运行 **{elapsed}s**，超过系统限制 "
            f"**{settings.TASK_TIMEOUT}s**，已被{killer}。\n\n"
            "**可能原因：**\n"
            "- 任务过于复杂，需要更多分析时间\n"
            "- 外部 API（LLM/文献库）响应缓慢\n\n"
            "**建议：**\n"
            "- 将任务拆分为更小的子问题分步提交\n"
            "- 对复杂综述任务，可添加 `--timeout 600` 参数延长超时\n\n"
            f"> 任务编号：`{tid}`"
        )
        return "⏱️ 任务超时已自动终止", content, "orange"
    elif status == "rejected":
        issues = "\n".join(f"- {i}" for i in result.get("issues", []))
        rec = result.get("recommendation", "")
        content = f"**拦截原因：**\n{issues}"
        if rec:
            content += f"\n\n**建议：**\n{rec}"
        return "⚠️ 安全审核拦截", content, "orange"
    elif status == "escalated":
        rec = result.get("recommendation", "")
        content = f"您的任务已进入人工审核队列，请耐心等待。\n\n{rec}".strip()
        return "⏳ 等待人工审核", content, "yellow"
    else:
        error = result.get("error", "未知错误")
        return "❌ 执行失败", error, "red"


async def notify(reply_info: Optional[dict], result: dict) -> None:
    """根据 reply_info 路由通知"""
    if not reply_info:
        return

    channel = reply_info.get("channel", "api")

    # IM 渠道通过 Channel Adapter 统一分发
    if channel in ("feishu", "dingtalk"):
        await _notify_im(channel, reply_info, result)
    elif channel == "email":
        await _notify_email(reply_info, _format_result_text(result))
    elif channel == "websocket":
        await _notify_websocket(reply_info, result)
    # channel == "api"：结果已由 worker_pool 存入 Redis，客户端轮询取结果


async def _notify_im(channel: str, reply_info: dict, result: dict) -> None:
    """
    通过 Channel Adapter 统一处理 IM 回复。

    回复策略：
    1. 优先：线程回复（需要 message_id）— 直接接FeishuAdapter 路径
    2. 降级：主动推送（需要 open_id）— OpenClaw Gateway → research.sh 路径
    """
    from channels import get_adapter
    adapter = get_adapter(channel)
    if not adapter:
        logger.warning(f"[Notifier] No adapter registered for channel={channel}")
        return
    try:
        status = result.get("status", "error")
        title, content, color = _build_card_content(result)

        _preview = content[:100].replace('\n', ' ')
        logger.info(
            f"[{channel.upper()}↑] 回复 | status={status} | title={title}"
            f" | content_len={len(content)} | {_preview}"
        )

        ok = False
        is_long = len(content) > 4800
        open_id = reply_info.get("open_id") or reply_info.get("sender_id")

        # 飞书长内容（>4800字符）：跳过单卡片线程回复，直接走 send_long_content 分段发送
        # 单卡片受飞书 30KB JSON 限制，长论文会被截断
        if channel == "feishu" and is_long and open_id:
            from feishu.bot import send_long_content
            ok = send_long_content(open_id, "open_id", title, content, color)
            logger.info(
                f"[Notifier] feishu send_long_content (long paper) | open_id={open_id[:12]}…"
                f" | content_len={len(content)} | ok={ok}"
            )
        else:
            # 短内容：优先线程回复（直接 FeishuAdapter 路径，有 message_id）
            if reply_info.get("message_id"):
                if status in ("error", "timeout"):
                    text = _format_result_text(result)
                    ok = adapter.reply_text(reply_info, text)
                else:
                    ok = adapter.reply_card(reply_info, title, content, result=result)

            # 降级：主动推送
            if not ok and open_id:
                if channel == "feishu":
                    from feishu.bot import send_long_content
                    ok = send_long_content(open_id, "open_id", title, content, color)
                    logger.info(
                        f"[Notifier] feishu send_long_content | open_id={open_id[:12]}…"
                        f" | content_len={len(content)} | ok={ok}"
                    )
                else:
                    ok = adapter.send_proactive(open_id, title, content)

        # 飞书渠道：在 async 上下文中直接存 pending_email 到 Redis
        # send_long_content 内部的同步存储在某些调用路径下会失败（_get_main_loop 返回 None），
        # 因此在这里用 async Redis 可靠地写入，确保用户回复邮箱时能找到待发内容
        if channel == "feishu" and open_id and ok and len(content) > 4000:
            try:
                import json as _json
                from core.cache import get_redis
                r = await get_redis()
                key = f"pending_email:{open_id}"
                await r.set(
                    key,
                    _json.dumps({"title": title, "content": content}, ensure_ascii=False),
                    ex=86400,
                )
                logger.info(
                    f"[Notifier] ✅ pending_email 已存入 Redis"
                    f" | key={key} | content_len={len(content)}"
                )
            except Exception as pe:
                logger.warning(f"[Notifier] pending_email 存储失败: {pe}")

        if ok:
            logger.info(f"[Notifier] {channel} push sent | content_len={len(content)}")
        elif not ok and not open_id:
            logger.warning(f"[Notifier] {channel} reply failed: no message_id and no open_id in reply_info")
        elif not ok:
            logger.warning(f"[Notifier] {channel} push failed | open_id={(open_id or '?')[:12]}…")
    except Exception as e:
        logger.error(f"[Notifier] {channel} notify error: {e}", exc_info=True)


async def _notify_email(reply_info: dict, text: str) -> None:
    email = reply_info.get("email")
    subject = reply_info.get("subject", "OpenClaw 任务完成")
    if not email:
        logger.warning("[Notifier] Email notify: missing email address")
        return
    try:
        from skills.tools import execute_skill
        await execute_skill("send_email", {
            "recipient": email,
            "subject": subject,
            "body": text,
        })
        logger.info(f"[Notifier] Email sent to {email}")
    except Exception as e:
        logger.error(f"[Notifier] Email notify error: {e}", exc_info=True)


async def _notify_websocket(reply_info: dict, result: dict) -> None:
    client_id = reply_info.get("client_id")
    if not client_id:
        return
    try:
        from api.websocket import ws_manager
        await ws_manager.send_json(client_id, result)
        logger.info(f"[Notifier] WebSocket pushed to client_id={client_id}")
    except Exception as e:
        logger.debug(f"[Notifier] WebSocket notify error (may not be connected): {e}")


async def send_proactive_digest(email: str, user_name: str, digest: str, subject: str = "") -> bool:
    """发送主动科研日报 / 完整论文邮件"""
    if not email:
        return False
    subject = subject or f"【OpenClaw】{user_name or '科研新动态'} — 今日前沿精选"
    try:
        from skills.tools import send_email   # 直接调用，避免 execute_skill 参数映射问题
        result = await send_email(to=email, subject=subject, body=digest)
        if result.get("success"):
            logger.info(f"[Notifier] Proactive digest sent to {email}")
            return True
        else:
            logger.warning(f"[Notifier] Proactive digest failed: {result.get('error')}")
            return False
    except Exception as e:
        logger.error(f"[Notifier] Proactive digest exception: {e}", exc_info=True)
        return False


async def send_proactive_im(channel: str, receive_id: str, title: str, content: str) -> bool:
    """通过 Channel Adapter 主动推送 IM 消息（飞书/钉钉通用）"""
    if not receive_id:
        return False
    from channels import get_adapter
    adapter = get_adapter(channel)
    if not adapter:
        logger.warning(f"[Notifier] No adapter for proactive push: {channel}")
        return False
    try:
        ok = adapter.send_proactive(receive_id, title, content)
        if ok:
            logger.info(f"[Notifier] Proactive {channel} sent to {receive_id[:12]}…")
        return ok
    except Exception as e:
        logger.error(f"[Notifier] Proactive {channel} failed: {e}", exc_info=True)
        return False


# 向后兼容
async def send_proactive_feishu(feishu_open_id: str, title: str, content: str) -> bool:
    return await send_proactive_im("feishu", feishu_open_id, title, content)
