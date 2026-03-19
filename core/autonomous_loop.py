"""
OpenClaw 自主运行调度器 — 驱动全体功能型 Agent 自动运转

调度计划（北京时间）：
┌─────────────────────────────────────────────────────────────────┐
│  Maintainer  健康检查       每 10 分钟                           │
│  Maintainer  每日健康报告   每天 23:30                           │
│  Vanguard    晨间前沿扫描   每天 08:10（AI/CS 领域）             │
│  Vanguard    晚间前沿扫描   每天 20:10（生命科学/材料/交叉领域）  │
│  Wellspring  知识合成       每天 02:30                           │
│  Wellspring  周摘要         每周一 01:00                         │
│  Promoter    内容生成       每周二 10:00                         │
│  Guardian    模式审查       每周日 23:00                         │
└─────────────────────────────────────────────────────────────────┘

多实例场景：通过 Redis 分布式锁确保每个 Job 全局只执行一次。
"""
import asyncio
import os
import time
from config.settings import settings, now
from core.logging_config import get_logger

logger = get_logger(__name__)

_INSTANCE_ID = os.getenv("HOSTNAME", os.getenv("INSTANCE_NAME", "local"))

# 晨间扫描领域
_VANGUARD_MORNING_DOMAINS = [
    "artificial intelligence", "machine learning", "deep learning",
    "large language models", "computer vision", "natural language processing",
    "robotics", "reinforcement learning",
]
# 晚间扫描领域
_VANGUARD_EVENING_DOMAINS = [
    "bioinformatics", "computational biology", "materials science",
    "quantum computing", "climate science", "digital twin",
    "interdisciplinary research",
]


def _get_scheduler():
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    return AsyncIOScheduler(timezone="Asia/Shanghai")


class AutonomousLoop:
    """自主调度器 — 统一管理所有自主运行 Agent"""

    def __init__(self):
        self._scheduler = None
        self._agents: dict = {}
        self._running = False

    def setup(self, agents: dict):
        """
        注册 Agent 实例映射。
        期望 key：maintainer, vanguard, wellspring, promoter, guardian
        """
        self._agents = {k: v for k, v in agents.items() if v is not None}
        logger.info(f"[AutonomousLoop] 注册 Agent: {list(self._agents.keys())}")

    def start(self):
        if not settings.AUTONOMOUS_ENABLED:
            logger.info("[AutonomousLoop] 自主模式已禁用（AUTONOMOUS_ENABLED=false）")
            return

        self._scheduler = _get_scheduler()
        self._running = True

        # AsyncIOScheduler 直接接受 async 函数，不需要任何 sync 包装
        # ── Maintainer ──────────────────────────────────────────────────
        self._scheduler.add_job(
            self._maintainer_watchdog_job,
            "interval", seconds=60,
            id="maintainer_watchdog",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._maintainer_health_job,
            "interval", minutes=10,
            id="maintainer_health",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._maintainer_daily_report_job,
            "cron", hour=23, minute=30,
            id="maintainer_daily_report",
            replace_existing=True,
        )

        # ── Vanguard ────────────────────────────────────────────────────
        self._scheduler.add_job(
            self._vanguard_morning_job,
            "cron", hour=8, minute=10,
            id="vanguard_morning",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._vanguard_evening_job,
            "cron", hour=20, minute=10,
            id="vanguard_evening",
            replace_existing=True,
        )

        # ── Wellspring ──────────────────────────────────────────────────
        self._scheduler.add_job(
            self._wellspring_synthesis_job,
            "cron", hour=2, minute=30,
            id="wellspring_synthesis",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._wellspring_weekly_digest_job,
            "cron", day_of_week="mon", hour=1, minute=0,
            id="wellspring_weekly_digest",
            replace_existing=True,
        )

        # ── Promoter ────────────────────────────────────────────────────
        self._scheduler.add_job(
            self._promoter_content_job,
            "cron", day_of_week="tue", hour=10, minute=0,
            id="promoter_content",
            replace_existing=True,
        )

        # ── Guardian ────────────────────────────────────────────────────
        self._scheduler.add_job(
            self._guardian_pattern_review_job,
            "cron", day_of_week="sun", hour=23, minute=0,
            id="guardian_pattern_review",
            replace_existing=True,
        )

        self._scheduler.start()
        logger.info(
            "[AutonomousLoop] 已启动自主调度器\n"
            "  Maintainer 任务看门狗：每60秒（超时任务自动kill+通知）\n"
            "  Maintainer 健康检查：  每10分钟\n"
            "  Vanguard   晨间扫描：  08:10 CST（AI/CS）\n"
            "  Vanguard   晚间扫描：  20:10 CST（生命科学/材料/交叉）\n"
            "  Wellspring 知识合成：  每天02:30 CST\n"
            "  Wellspring 周摘要：    每周一01:00 CST\n"
            "  Promoter   内容生成：  每周二10:00 CST\n"
            "  Guardian   模式审查：  每周日23:00 CST"
        )

    def stop(self):
        self._running = False
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        logger.info("[AutonomousLoop] 已停止")

    # ── Maintainer Jobs ─────────────────────────────────────────────────

    async def _maintainer_watchdog_job(self):
        """
        每60s：任务超时看门狗。
        由 Maintainer 负责巡检所有正在执行的任务，对卡死任务强制超时 + 通知用户。
        这是 asyncio.wait_for 的双重保障，应对极端的 event loop 阻塞场景。
        """
        from core import cache as cache_store
        from core.task_watchdog import _check_once
        from config.settings import settings

        try:
            await cache_store.set_agent_run_status("maintainer", "running:watchdog")
            await _check_once(settings, cache_store, __import__("core.notifier", fromlist=["notify"]))
        except Exception as e:
            logger.error(f"[Maintainer/Watchdog] 看门狗巡检异常: {e}", exc_info=True)
        finally:
            await cache_store.set_agent_run_status("maintainer", "idle")

    async def _maintainer_health_job(self):
        """每10分钟：采集系统指标，检测异常并写入告警"""
        from core import cache as cache_store

        maintainer = self._agents.get("maintainer")
        lock_key = f"auto:maintainer_health:{int(time.time() // 600)}"
        if not await cache_store.acquire_lock(lock_key, ttl_seconds=580, instance_id=_INSTANCE_ID):
            return

        await cache_store.set_agent_run_status("maintainer", "running:health_check")
        try:
            # 采集系统指标
            metrics = {}
            if maintainer:
                metrics = maintainer.collect_system_metrics()

            # 检查 Redis 队列健康
            q_len = await cache_store.queue_length()
            active_workers = await cache_store.get_active_workers()
            worker_count = len(active_workers)

            if q_len > settings.MAINTAINER_ALERT_QUEUE_THRESHOLD:
                msg = (
                    f"⚠️ 任务队列积压：{q_len} 条等待处理，活跃 Worker: {worker_count} 个。"
                    f" 考虑增加 WORKER_COUNT 或检查 Worker 是否正常运行。"
                )
                logger.warning(f"[Maintainer] P2 队列积压 | q_len={q_len} workers={worker_count}")
                await cache_store.push_alert("P2", msg, "maintainer")
                await _notify_admin(msg)

            if worker_count == 0 and q_len > 0:
                msg = "🚨 P0告警：无活跃 Worker 但队列中有任务！请检查 Worker Pool 是否崩溃。"
                logger.error(f"[Maintainer] P0 无Worker | q_len={q_len}")
                await cache_store.push_alert("P0", msg, "maintainer")
                await _notify_admin(msg)

            # CPU/内存告警
            cpu = metrics.get("cpu_percent", 0)
            mem = metrics.get("memory_percent", 0)
            if cpu > 90:
                msg = f"⚠️ CPU 使用率过高：{cpu}%"
                await cache_store.push_alert("P2", msg, "maintainer")
            if mem > 90:
                msg = f"⚠️ 内存使用率过高：{mem}%"
                await cache_store.push_alert("P2", msg, "maintainer")

            logger.debug(
                f"[Maintainer] 健康检查 | cpu={cpu}% mem={mem}%"
                f" q_len={q_len} workers={worker_count}"
            )
        except Exception as e:
            logger.error(f"[Maintainer] 健康检查异常: {e}", exc_info=True)
        finally:
            await cache_store.set_agent_run_status("maintainer", "idle")

    async def _maintainer_daily_report_job(self):
        """每天23:30：生成系统日报"""
        from core import cache as cache_store

        maintainer = self._agents.get("maintainer")
        if not maintainer:
            return

        today = now().strftime("%Y%m%d")
        lock_key = f"auto:maintainer_report:{today}"
        if not await cache_store.acquire_lock(lock_key, ttl_seconds=3600, instance_id=_INSTANCE_ID):
            return

        await cache_store.set_agent_run_status("maintainer", "running:daily_report")
        try:
            metrics = maintainer.collect_system_metrics()
            active_workers = await cache_store.get_active_workers()
            q_len = await cache_store.queue_length()
            recent_alerts = await cache_store.get_recent_alerts(20)

            agent_health = {}
            try:
                from core.registry import registry
                agent_health = maintainer.check_agent_health(registry._agents)
            except Exception:
                pass

            result = await maintainer.generate_health_report(
                metrics={**metrics, "queue_length": q_len, "active_workers": len(active_workers)},
                agent_health=agent_health,
            )

            report_content = result.get("result", "")
            if report_content:
                alert_summary = f"今日告警：{len(recent_alerts)} 条" if recent_alerts else "今日无告警"
                full_report = f"📊 **OpenClaw 系统日报 — {now().strftime('%m月%d日')}**\n\n{report_content}\n\n{alert_summary}"
                await _notify_admin(full_report)
                logger.info(f"[Maintainer] 日报生成完毕（{len(report_content)} 字）")
        except Exception as e:
            logger.error(f"[Maintainer] 日报生成失败: {e}", exc_info=True)
        finally:
            await cache_store.set_agent_run_status("maintainer", "idle")

    # ── Vanguard Jobs ───────────────────────────────────────────────────

    async def _vanguard_scan_domains(self, domains: list, session_name: str):
        """
        通用前沿扫描：遍历领域列表，结果写入 Wellspring + 向量匹配个性化推送。

        完整闭环：
          Vanguard 扫描 → Redis 缓存 + ChromaDB papers
            → Wellspring 知识沉淀（MySQL + ChromaDB knowledge）
            → 向量匹配感兴趣用户 → 飞书主动推送
        """
        from core import cache as cache_store

        vanguard = self._agents.get("vanguard")
        if not vanguard:
            logger.debug(f"[Vanguard] {session_name}: Vanguard 未注册，跳过")
            return

        today = now().strftime("%Y%m%d")
        lock_key = f"auto:vanguard:{session_name}:{today}"
        if not await cache_store.acquire_lock(lock_key, ttl_seconds=7200, instance_id=_INSTANCE_ID):
            logger.debug(f"[Vanguard] {session_name}: 已由其他实例执行，跳过")
            return

        await cache_store.set_agent_run_status("vanguard", f"running:{session_name}")
        wellspring = self._agents.get("wellspring")
        logger.info(f"[Vanguard] {session_name} 扫描开始 | 领域数={len(domains)}")

        success_count = 0
        for domain in domains:
            try:
                logger.info(f"[Vanguard] 扫描领域：{domain}")
                result = await vanguard.explore_frontier(domain)
                frontier_text = result.get("result", "")

                if frontier_text and len(frontier_text) > 100:
                    # ① Redis 缓存（供快速检索）
                    cache_key = f"frontier:{domain.replace(' ', '_')}:{today}"
                    await cache_store.cache_knowledge(cache_key, frontier_text[:4000])
                    await cache_store.set_knowledge_index(
                        cache_key,
                        tags=domain.split() + [domain, today],
                    )

                    # ② Wellspring 知识沉淀（异步，不阻塞）
                    if wellspring:
                        asyncio.create_task(
                            wellspring.ingest_task_result({
                                "task": f"[Vanguard] {session_name} 前沿扫描：{domain}",
                                "result": frontier_text,
                                "agent_name": "vanguard",
                                "agent_id": "vanguard-01",
                                "role": "vanguard",
                                "quality_score": 0.85,
                                "status": "success",
                            }),
                            name=f"ws-ingest-{domain[:20].replace(' ', '_')}",
                        )

                    # ③ 向量匹配 → 个性化飞书推送（异步，不阻塞扫描流程）
                    asyncio.create_task(
                        self._push_frontier_to_users(domain, frontier_text, today),
                        name=f"frontier-push-{domain[:20].replace(' ', '_')}",
                    )
                    success_count += 1

            except Exception as e:
                logger.warning(f"[Vanguard] {domain} 扫描失败: {e}")
            await asyncio.sleep(2)  # 避免 API 限速

        logger.info(f"[Vanguard] {session_name} 扫描完成 | 成功={success_count}/{len(domains)}")
        await cache_store.set_agent_run_status("vanguard", "idle")

    async def _push_frontier_to_users(self, domain: str, frontier_text: str, today: str):
        """
        向量匹配 + 个性化飞书推送核心逻辑。

        流程：
          ChromaDB user_interests 语义检索
            → 过滤已推送用户（Redis 分布式锁去重，每用户每领域每天一次）
            → 飞书主动推送个性化前沿摘要
        """
        from core import cache as cache_store
        from core import notifier as _notifier

        # ── Step 1: 向量匹配感兴趣用户 ──────────────────────────────────────
        try:
            from core.vector_store import get_vector_store
            vs = get_vector_store()
            if not vs.is_ready():
                logger.debug(f"[Vanguard/Push] ChromaDB 未就绪，跳过推送")
                return
            # 用 domain + 报告摘要 作为查询向量
            query_text = f"{domain}\n{frontier_text[:400]}"
            matching_users = await vs.find_matching_users(
                content=query_text,
                top_k=30,
                max_distance=0.55,   # cosine distance ≤ 0.55 ≈ 相似度 ≥ 45%，足够精准
            )
        except Exception as e:
            logger.debug(f"[Vanguard/Push] 向量匹配异常: {e}")
            return

        if not matching_users:
            logger.debug(f"[Vanguard/Push] {domain}: 无兴趣匹配用户，跳过")
            return

        logger.info(f"[Vanguard/Push] {domain}: 向量匹配到 {len(matching_users)} 位用户")

        # ── Step 2: 生成简洁推送摘要 ─────────────────────────────────────────
        summary = _build_frontier_push_message(domain, frontier_text)

        # ── Step 3: 逐用户去重推送 ───────────────────────────────────────────
        pushed, skipped = 0, 0
        domain_key = domain.replace(" ", "_")[:30]
        for user_id in matching_users:
            try:
                # 目前只处理飞书渠道
                if not user_id.startswith("feishu:"):
                    continue
                open_id = user_id[len("feishu:"):]
                if not open_id:
                    continue

                # 分布式去重锁：同一用户 × 同一领域 × 同一天只推一次
                lock_key = f"push:frontier:{domain_key}:{open_id[:20]}:{today}"
                if not await cache_store.acquire_lock(
                    lock_key, ttl_seconds=86400, instance_id=_INSTANCE_ID
                ):
                    skipped += 1
                    continue

                ok = await _notifier.send_proactive_feishu(
                    open_id,
                    f"🔭 {domain} · 今日前沿速递",
                    summary,
                )
                if ok:
                    pushed += 1
                    logger.info(
                        f"[Vanguard/Push] ✓ 推送成功 | domain={domain}"
                        f" | open_id={open_id[:16]}…"
                    )
                else:
                    logger.debug(
                        f"[Vanguard/Push] 推送失败（adapter返回False）"
                        f" | open_id={open_id[:16]}…"
                    )

                await asyncio.sleep(0.5)   # 飞书 API 限速保护

            except Exception as e:
                logger.debug(f"[Vanguard/Push] 处理用户 {user_id[:20]} 异常: {e}")

        if pushed > 0 or skipped > 0:
            logger.info(
                f"[Vanguard/Push] {domain} 推送完毕 | "
                f"推送={pushed} 跳过（已推）={skipped} / 匹配={len(matching_users)}"
            )

    async def _vanguard_morning_job(self):
        await self._vanguard_scan_domains(_VANGUARD_MORNING_DOMAINS, "morning")

    async def _vanguard_evening_job(self):
        await self._vanguard_scan_domains(_VANGUARD_EVENING_DOMAINS, "evening")

    # ── Wellspring Jobs ─────────────────────────────────────────────────

    async def _wellspring_synthesis_job(self):
        """每天02:30：从数据库中提取当日任务结果，合成社区知识"""
        from core import cache as cache_store

        wellspring = self._agents.get("wellspring")
        if not wellspring:
            return

        today = now().strftime("%Y%m%d")
        lock_key = f"auto:wellspring_synthesis:{today}"
        if not await cache_store.acquire_lock(lock_key, ttl_seconds=7200, instance_id=_INSTANCE_ID):
            return

        await cache_store.set_agent_run_status("wellspring", "running:synthesis")
        try:
            logger.info("[Wellspring] 知识合成开始…")

            # 从数据库拉取今日高质量任务结果
            recent_tasks = []
            try:
                from core.database import get_recent_successful_tasks
                recent_tasks = await get_recent_successful_tasks(hours=24, limit=20)
            except Exception as e:
                logger.warning(f"[Wellspring] 无法读取近期任务: {e}")

            # 主动生成当前热点知识条目（不依赖历史任务）
            hot_topics = ["大语言模型前沿进展", "科研方法论创新", "跨学科交叉研究"]
            for topic in hot_topics:
                try:
                    result = await wellspring.generate_community_knowledge(
                        topic, focus="实践应用与工程落地"
                    )
                    if result.get("status") == "success":
                        content = result.get("result", "")
                        cache_key = f"community:{topic.replace(' ', '_')}:{today}"
                        await cache_store.cache_knowledge(cache_key, content[:4000])
                        await cache_store.set_knowledge_index(
                            cache_key,
                            tags=topic.split() + ["community", "hot"],
                        )
                except Exception as e:
                    logger.warning(f"[Wellspring] 生成知识条目失败（{topic}）: {e}")
                await asyncio.sleep(3)

            # 处理历史任务结果
            if recent_tasks:
                for task_result in recent_tasks[:5]:  # 限制数量避免过载
                    try:
                        await wellspring.ingest_task_result(task_result)
                    except Exception as e:
                        logger.debug(f"[Wellspring] ingest 失败: {e}")

            logger.info("[Wellspring] 知识合成完成")
        except Exception as e:
            logger.error(f"[Wellspring] 知识合成异常: {e}", exc_info=True)
        finally:
            await cache_store.set_agent_run_status("wellspring", "idle")

    async def _wellspring_weekly_digest_job(self):
        """每周一01:00：生成社区知识摘要并推送"""
        from core import cache as cache_store

        wellspring = self._agents.get("wellspring")
        if not wellspring:
            return

        week = now().strftime("%Y_W%W")
        lock_key = f"auto:wellspring_digest:{week}"
        if not await cache_store.acquire_lock(lock_key, ttl_seconds=7200, instance_id=_INSTANCE_ID):
            return

        await cache_store.set_agent_run_status("wellspring", "running:weekly_digest")
        try:
            logger.info("[Wellspring] 周摘要生成…")
            result = await wellspring.generate_community_digest()
            digest_text = result.get("result", "")
            if digest_text:
                await _notify_admin(
                    f"📚 **OpenClaw 社区知识周报 — {now().strftime('%m月%d日')}**\n\n{digest_text[:2000]}"
                )
                logger.info(f"[Wellspring] 周摘要完成（{len(digest_text)} 字）")
        except Exception as e:
            logger.error(f"[Wellspring] 周摘要异常: {e}", exc_info=True)
        finally:
            await cache_store.set_agent_run_status("wellspring", "idle")

    # ── Promoter Jobs ───────────────────────────────────────────────────

    async def _promoter_content_job(self):
        """每周二10:00：基于 Wellspring 最新知识生成推广内容草稿"""
        from core import cache as cache_store

        promoter = self._agents.get("promoter")
        if not promoter:
            return

        week = now().strftime("%Y_W%W")
        lock_key = f"auto:promoter_content:{week}"
        if not await cache_store.acquire_lock(lock_key, ttl_seconds=7200, instance_id=_INSTANCE_ID):
            return

        await cache_store.set_agent_run_status("promoter", "running:content_cycle")
        try:
            logger.info("[Promoter] 周内容生成…")

            # 读取本周 Vanguard 前沿摘要
            today = now().strftime("%Y%m%d")
            frontier_summary = ""
            for domain in _VANGUARD_MORNING_DOMAINS[:3]:
                cache_key = f"frontier:{domain.replace(' ', '_')}:{today}"
                text = await cache_store.get_cached_knowledge(cache_key)
                if text:
                    frontier_summary += f"\n### {domain}\n{text[:500]}"
                if len(frontier_summary) > 1500:
                    break

            if not frontier_summary:
                frontier_summary = "本周 AI/ML 领域前沿进展综述（含大模型、多模态、强化学习等方向）"

            for platform in ["微信公众号", "知乎"]:
                try:
                    result = await promoter.create_content(
                        frontier_summary, platform=platform, audience="科研人员/研究生"
                    )
                    if result.get("status") == "success":
                        content = result.get("result", "")
                        cache_key = f"promo_draft:{platform}:{week}"
                        await cache_store.cache_knowledge(cache_key, content[:3000])
                        logger.info(f"[Promoter] {platform} 草稿生成完毕")
                except Exception as e:
                    logger.warning(f"[Promoter] {platform} 内容生成失败: {e}")
                await asyncio.sleep(2)

        except Exception as e:
            logger.error(f"[Promoter] 内容生成异常: {e}", exc_info=True)
        finally:
            await cache_store.set_agent_run_status("promoter", "idle")

    # ── Guardian Jobs ───────────────────────────────────────────────────

    async def _guardian_pattern_review_job(self):
        """每周日23:00：分析近一周的拒绝/升级模式，更新 Guardian 内部策略"""
        from core import cache as cache_store

        guardian = self._agents.get("guardian")
        if not guardian:
            return

        week = now().strftime("%Y_W%W")
        lock_key = f"auto:guardian_review:{week}"
        if not await cache_store.acquire_lock(lock_key, ttl_seconds=3600, instance_id=_INSTANCE_ID):
            return

        await cache_store.set_agent_run_status("guardian", "running:pattern_review")
        try:
            logger.info("[Guardian] 模式审查…")

            # 拉取本周的拒绝/升级记录
            try:
                from core.database import get_guardian_verdicts
                verdicts = await get_guardian_verdicts(days=7)
                rejected = [v for v in verdicts if v.get("verdict") == "rejected"]
                escalated = [v for v in verdicts if v.get("verdict") == "escalated"]
                logger.info(
                    f"[Guardian] 本周统计 | 拒绝={len(rejected)} 升级={len(escalated)}"
                )

                if rejected or escalated:
                    hasattr(guardian, "update_patterns") and await guardian.update_patterns(
                        rejected_samples=rejected[:5],
                        escalated_samples=escalated[:5],
                    )
            except Exception as e:
                logger.debug(f"[Guardian] 读取审查记录失败（可能表未创建）: {e}")

            logger.info("[Guardian] 模式审查完成")
        except Exception as e:
            logger.error(f"[Guardian] 模式审查异常: {e}", exc_info=True)
        finally:
            await cache_store.set_agent_run_status("guardian", "idle")


# ── 辅助函数 ────────────────────────────────────────────────────────────


def _build_frontier_push_message(domain: str, frontier_text: str) -> str:
    """
    从 Vanguard 前沿报告中提取简洁摘要，生成适合飞书推送的个性化消息。

    策略：
    1. 优先截取含"前沿趋势"/"Top"/"🔥"/"##"标题 后续的正文段落（核心趋势区）
    2. 降级：直接取报告前 600 字
    3. 末尾追加个性化提示说明
    """
    lines = frontier_text.split("\n")

    # 尝试找到核心趋势段落
    trend_lines: list[str] = []
    capturing = False
    for line in lines:
        stripped = line.strip()
        # 触发采集：遇到趋势/Top/热点标题行
        if any(kw in stripped for kw in ("前沿趋势", "Top 5", "Top5", "🔥", "热点", "研究方向")):
            capturing = True
        # 遇到其他主标题行（且已有内容）则停止采集
        if capturing and stripped.startswith("##") and trend_lines:
            if not any(kw in stripped for kw in ("前沿趋势", "Top", "🔥", "热点", "研究方向")):
                break
        if capturing:
            trend_lines.append(line)
            if sum(len(l) for l in trend_lines) > 800:
                break

    body = "\n".join(trend_lines).strip() if trend_lines else frontier_text[:600].strip()
    # 截断保证消息长度
    if len(body) > 900:
        body = body[:900] + "\n…（更多内容已省略）"

    footer = (
        "\n\n---\n"
        f"💡 *基于你的研究兴趣，OpenClaw 自动为你筛选了 **{domain}** 领域最新进展。*\n"
        "_有任何问题，直接回复即可提问。_"
    )
    return body + footer


async def _notify_admin(message: str):
    """向管理员推送系统通知（Feishu 或日志降级）"""
    admin_open_id = settings.MAINTAINER_ALERT_FEISHU_OPEN_ID
    if admin_open_id:
        try:
            from core.notifier import send_proactive_feishu
            await send_proactive_feishu(admin_open_id, "🔧 OpenClaw 系统通知", message[:3000])
        except Exception as e:
            logger.debug(f"[AutonomousLoop] 管理员通知发送失败: {e}")
    else:
        logger.info(f"[AutonomousLoop] 系统通知（无管理员ID）: {message[:200]}")


# ── 全局单例 ─────────────────────────────────────────────────────────────
_loop: AutonomousLoop = None


def get_autonomous_loop() -> AutonomousLoop:
    global _loop
    if _loop is None:
        _loop = AutonomousLoop()
    return _loop


async def trigger_now(job_name: str):
    """手动触发指定 Job（用于测试）"""
    loop = get_autonomous_loop()
    job_map = {
        "maintainer_watchdog": loop._maintainer_watchdog_job,
        "maintainer_health": loop._maintainer_health_job,
        "maintainer_report": loop._maintainer_daily_report_job,
        "vanguard_morning": loop._vanguard_morning_job,
        "vanguard_evening": loop._vanguard_evening_job,
        "wellspring_synthesis": loop._wellspring_synthesis_job,
        "wellspring_digest": loop._wellspring_weekly_digest_job,
        "promoter_content": loop._promoter_content_job,
        "guardian_review": loop._guardian_pattern_review_job,
    }
    fn = job_map.get(job_name)
    if fn:
        logger.info(f"[AutonomousLoop] 手动触发: {job_name}")
        await fn()
    else:
        raise ValueError(f"未知 job: {job_name}，可选：{list(job_map.keys())}")
