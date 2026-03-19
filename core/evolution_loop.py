"""
Vanguard 自进化每日推送
每天定时扫描科研前沿，与用户兴趣画像语义匹配，发送个性化邮件+飞书日报

扩容改造：
- 使用 Redis 分布式锁确保多实例场景下只有一个实例执行每日扫描
- 使用 Redis 快速去重（is_notification_sent）+ MySQL 持久化（record_notification）
- 锁粒度：全局每日扫描锁（evolution_daily_lock） + 用户级推送锁（notify:{user_id}:{hash}）
"""
import asyncio
import logging
from typing import Optional
from datetime import datetime

from config.settings import settings, now
from core.logging_config import get_logger, cleanup_old_logs

logger = get_logger(__name__)

_vanguard = None
_scheduler = None

# 当前实例 ID（用于分布式锁 debug，多容器部署时区分）
import os as _os
_INSTANCE_ID = _os.getenv("HOSTNAME", _os.getenv("INSTANCE_NAME", "local"))


def _get_scheduler():
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    return AsyncIOScheduler(timezone="Asia/Shanghai")


def start(vanguard_agent=None):
    """启动自进化定时任务"""
    global _vanguard, _scheduler
    _vanguard = vanguard_agent

    _scheduler = _get_scheduler()
    hour = settings.EVOLUTION_EMAIL_HOUR

    _scheduler.add_job(
        _daily_scan_job,
        "cron",
        hour=hour,
        minute=7,
        id="daily_evolution",
        replace_existing=True,
    )

    # 每天凌晨 1:00 清理超过 30 天的历史日志
    _scheduler.add_job(
        _cleanup_logs_job,
        "cron",
        hour=1,
        minute=0,
        id="daily_log_cleanup",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info(f"[EvolutionLoop] Started — daily scan at {hour:02d}:07 CST (instance={_INSTANCE_ID})")


def stop():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    logger.info("[EvolutionLoop] Stopped")


def _daily_scan_job():
    """APScheduler 调用的同步入口，转发到 asyncio"""
    loop = asyncio.get_event_loop()
    if loop.is_running():
        asyncio.ensure_future(_daily_scan())
    else:
        loop.run_until_complete(_daily_scan())


async def _daily_scan():
    """主体逻辑：分布式锁保护 → 扫描前沿 → 用户语义匹配 → 去重推送"""
    from core.cache import acquire_lock, release_lock, is_notification_sent, mark_notification_sent

    # ── 分布式锁：确保多容器场景下只有一个实例执行每日扫描 ──
    today_str = now().strftime("%Y%m%d")
    global_lock_key = f"evolution_daily:{today_str}"

    lock_acquired = await acquire_lock(
        lock_key=global_lock_key,
        ttl_seconds=3600,  # 1小时内不重复执行
        instance_id=_INSTANCE_ID,
    )
    if not lock_acquired:
        logger.info(f"[EvolutionLoop] Daily scan already running on another instance, skipping (instance={_INSTANCE_ID})")
        return

    logger.info(f"[EvolutionLoop] Starting daily frontier scan… (instance={_INSTANCE_ID})")
    try:
        from core.database import (
            get_all_user_interest_profiles, already_notified_today,
            record_notification,
        )
        from core.notifier import send_proactive_digest, send_proactive_feishu

        profiles = await get_all_user_interest_profiles()
        if not profiles:
            logger.info("[EvolutionLoop] No user profiles found, skipping")
            return

        logger.info(f"[EvolutionLoop] {len(profiles)} users to check")

        frontier_text = await _scan_frontier()
        if not frontier_text:
            logger.info("[EvolutionLoop] No frontier content retrieved, skipping")
            return

        frontier_hash = str(abs(hash(frontier_text[:200])))[:12]

        # ── 向量匹配：找出与前沿内容最相关的用户（替代逐用户 LLM 匹配）──────────
        # 使用 ChromaDB user_interests 集合，单次向量查询替代 N 次 LLM 调用
        matched_user_ids = set()
        try:
            from core.vector_store import get_vector_store
            vs = get_vector_store()
            if vs.is_ready():
                matched_user_ids = set(
                    await vs.find_matching_users(frontier_text[:500], top_k=50, max_distance=0.65)
                )
                logger.info(f"[EvolutionLoop] 向量匹配用户 | 前沿内容匹配到 {len(matched_user_ids)} 位用户")
        except Exception as e:
            logger.warning(f"[EvolutionLoop] 向量匹配失败，降级到逐用户匹配: {e}")

        sent = 0
        for profile in profiles:
            user_id = profile["user_id"]

            # 双重去重：Redis 快速检查（毫秒级）+ MySQL 持久化检查（备份）
            redis_checked = await is_notification_sent(user_id, frontier_hash)
            if redis_checked:
                logger.debug(f"[EvolutionLoop] User {user_id[:8]}… already notified (Redis cache)")
                continue

            if await already_notified_today(user_id):
                logger.debug(f"[EvolutionLoop] User {user_id[:8]}… already notified today (DB)")
                await mark_notification_sent(user_id, frontier_hash)
                continue

            # 用户级分布式锁（防止并发场景下同一用户被多次推送）
            user_lock_key = f"notify_user:{user_id}:{frontier_hash}"
            user_lock = await acquire_lock(user_lock_key, ttl_seconds=300, instance_id=_INSTANCE_ID)
            if not user_lock:
                logger.debug(f"[EvolutionLoop] User {user_id[:8]}… notification in progress by another worker")
                continue

            try:
                # 如果向量匹配可用，只推送给匹配用户；否则降级到原始 LLM 匹配
                if matched_user_ids:
                    if user_id not in matched_user_ids:
                        logger.debug(f"[EvolutionLoop] User {user_id[:8]}… 向量未命中，跳过")
                        await release_lock(user_lock_key)
                        continue
                    matched_content = frontier_text  # 已通过向量筛选
                else:
                    matched_content = await _match_to_user(frontier_text, profile)
                if not matched_content:
                    continue

                digest = _format_digest(profile, matched_content)

                email = profile.get("email")
                feishu_open_id = profile.get("feishu_open_id")
                dingtalk_user_id = profile.get("dingtalk_user_id")
                any_sent = False

                if email:
                    ok = await send_proactive_digest(email, profile.get("name", ""), digest)
                    if ok:
                        any_sent = True

                if feishu_open_id:
                    title = f"🔬 科研前沿日报 — {now().strftime('%m月%d日')}"
                    ok = await send_proactive_feishu(feishu_open_id, title, matched_content[:3000])
                    if ok:
                        any_sent = True

                if dingtalk_user_id:
                    from core.notifier import send_proactive_im
                    title = f"🔬 科研前沿日报 — {now().strftime('%m月%d日')}"
                    ok = await send_proactive_im("dingtalk", dingtalk_user_id, title, matched_content[:3000])
                    if ok:
                        any_sent = True

                if any_sent:
                    # 双写：Redis 快速缓存 + MySQL 持久化
                    await mark_notification_sent(user_id, frontier_hash)
                    await record_notification(user_id, content_hash=frontier_hash)
                    sent += 1
            finally:
                await release_lock(user_lock_key)

        logger.info(f"[EvolutionLoop] Daily scan complete — sent {sent} digests")

    except Exception as e:
        logger.error(f"[EvolutionLoop] Daily scan failed: {e}", exc_info=True)
        # 异常时释放全局锁，允许后续重试
        await release_lock(global_lock_key)



async def _scan_frontier() -> str:
    """调用 Vanguard 扫描过去 24h 前沿"""
    current_year = now().strftime("%Y")

    if _vanguard is None:
        # 直接用 skill 做轻量扫描
        try:
            from skills.tools import execute_skill
            result = await execute_skill("arxiv_search", {
                "query": f"AI machine learning research {current_year}",
                "max_results": 10,
            })
            return str(result)
        except Exception as e:
            logger.warning(f"[EvolutionLoop] Fallback arxiv search failed: {e}")
            return ""

    try:
        result = await _vanguard.run(
            "请快速扫描今天（过去24小时）发布的科研前沿论文，重点关注 AI、计算机科学、生命科学领域，"
            "每个领域列出 3-5 篇最值得关注的论文，给出标题、摘要要点和为什么值得关注。"
        )
        return result.get("result", "")
    except Exception as e:
        logger.warning(f"[EvolutionLoop] Vanguard scan failed: {e}")
        return ""


async def _match_to_user(frontier_text: str, profile: dict) -> str:
    """用 LLM 语义匹配判断前沿内容与用户兴趣的相关性"""
    keywords = profile.get("keywords", [])
    domains = profile.get("domains", [])

    # 先做快速关键词预筛
    all_terms = [k.lower() for k in keywords + domains]
    frontier_lower = frontier_text.lower()
    quick_match = [t for t in all_terms if t in frontier_lower]

    if len(quick_match) >= 2:
        # 关键词命中较多，直接通过
        return frontier_text

    # 用 LLM 语义匹配
    if not settings.OPENAI_API_KEY:
        return ""

    try:
        import openai
        import json
        client = openai.AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            **({"base_url": settings.OPENROUTER_BASE_URL} if settings.OPENROUTER_BASE_URL else {}),
        )

        prompt = f"""评估以下前沿科研内容与用户兴趣的相关度。

用户兴趣领域：{', '.join(domains)}
用户关键词：{', '.join(keywords[:10])}

前沿内容摘要（前500字）：
{frontier_text[:500]}

请返回 JSON（不要返回其他内容）：
{{"relevance_score": 0-10, "reason": "简要原因"}}

评分标准：0-3不相关，4-6略相关，7-10高度相关"""

        resp = await client.chat.completions.create(
            model=settings.FAST_MODEL,
            max_tokens=128,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = resp.choices[0].message.content.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        score = data.get("relevance_score", 0)

        if score >= 5:
            logger.info(f"[EvolutionLoop] Semantic match score={score} for user {profile['user_id'][:8]}…")
            return frontier_text
        return ""

    except Exception as e:
        logger.debug(f"[EvolutionLoop] Semantic matching failed, falling back: {e}")
        # 降级：只要有至少 1 个关键词匹配就通过
        return frontier_text if len(quick_match) >= 1 else ""


def _format_digest(profile: dict, content: str) -> str:
    """格式化为 HTML 邮件内容"""
    domains = "、".join(profile.get("domains", ["科研"]))
    keywords = "、".join(profile.get("keywords", [])[:5])
    today = now().strftime("%Y年%m月%d日")

    truncated = content[:3000] + "\n\n…（更多内容请访问 arXiv）" if len(content) > 3000 else content
    # 简单 markdown → HTML 转换
    body_html = truncated.replace("\n", "<br>")

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"/></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:680px;margin:0 auto;padding:20px;color:#333">
<div style="background:linear-gradient(135deg,#6366f1,#b06aff);padding:24px;border-radius:12px;color:white;margin-bottom:24px">
  <h1 style="margin:0;font-size:22px">🦞 OpenClaw 科研前沿日报</h1>
  <p style="margin:8px 0 0;opacity:.9;font-size:14px">{today}</p>
</div>
<p>你好！根据你在 <strong>{domains}</strong> 领域的研究兴趣（关键词：{keywords}），
OpenClaw 发现了今天的相关科研新动态：</p>
<div style="background:#f8f9fa;padding:16px;border-radius:8px;border-left:4px solid #6366f1;margin:16px 0;font-size:14px;line-height:1.8">
{body_html}
</div>
<hr style="border:none;border-top:1px solid #eee;margin:24px 0"/>
<p style="font-size:12px;color:#999">此邮件由 OpenClaw 智能体社区自动生成。如不希望接收此类邮件，请回复"退订"。</p>
</body>
</html>"""


async def trigger_now():
    """手动触发（用于测试）"""
    logger.info("[EvolutionLoop] Manually triggered")
    await _daily_scan()


async def _cleanup_logs_job():
    """每日凌晨清理超过 30 天的历史日志（APScheduler job）"""
    try:
        deleted = cleanup_old_logs(retain_days=30)
        logger.info(f"[EvolutionLoop] 日志清理完成，删除 {deleted} 个过期文件")
    except Exception as e:
        logger.warning(f"[EvolutionLoop] 日志清理失败: {e}")
