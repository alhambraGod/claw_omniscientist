"""
OpenClaw 数据库模型与初始化
主存储切换为 MySQL，新增用户画像、会话、主动推送表
"""
import json
import logging
from core.logging_config import get_logger
from typing import Optional
from sqlalchemy import (
    Column, String, Text, Float, Integer, Boolean,
    DateTime, JSON, Index, BigInteger
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from config.settings import settings, now

logger = get_logger(__name__)


class Base(DeclarativeBase):
    pass


# ── 原有表（保留，适配 MySQL） ─────────────────────────────────────────

class AgentRecord(Base):
    __tablename__ = "agents"
    id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False)
    role = Column(String(32), nullable=False)
    tribe = Column(String(64), nullable=True)
    subdomain = Column(String(128), nullable=True)
    capabilities = Column(JSON, default=list)
    model_profile = Column(String(64), default=lambda: settings.DEFAULT_MODEL)
    tool_profile = Column(JSON, default=list)
    risk_level = Column(String(16), default="medium")
    status = Column(String(16), default="active")
    version = Column(String(16), default="1.0.0")
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)
    metadata_ = Column("metadata", JSON, default=dict)


class TaskRecord(Base):
    __tablename__ = "tasks"
    id = Column(String(64), primary_key=True)
    title = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    task_type = Column(String(32), default="single")
    status = Column(String(16), default="pending")
    risk_level = Column(String(16), default="low")
    assigned_agent = Column(String(64), nullable=True)
    assigned_agents = Column(JSON, default=list)
    user_id = Column(String(128), nullable=True)
    session_id = Column(String(64), nullable=True)
    # 新增：来源渠道和回调信息
    channel = Column(String(32), default="api")          # feishu / web / cli / api
    reply_info = Column(JSON, nullable=True)             # {channel, message_id, open_id, ...}
    worker_id = Column(String(64), nullable=True)        # 执行本任务的 worker
    input_data = Column(JSON, default=dict)
    output_data = Column(JSON, nullable=True)
    guardian_verdict = Column(String(16), nullable=True)
    quality_score = Column(Float, nullable=True)
    created_at = Column(DateTime, default=now)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    __table_args__ = (
        Index("ix_tasks_status", "status"),
        Index("ix_tasks_user", "user_id"),
        Index("ix_tasks_channel", "channel"),
    )


class KnowledgeEntry(Base):
    __tablename__ = "knowledge"
    id = Column(String(64), primary_key=True)
    title = Column(String(256), nullable=False)
    content = Column(Text, nullable=False)
    source = Column(String(256), nullable=True)
    category = Column(String(64), nullable=True)
    quality_score = Column(Float, default=0.0)
    contributor_agent = Column(String(64), nullable=True)
    tags = Column(JSON, default=list)
    citation_count = Column(Integer, default=0)
    verified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=now)
    __table_args__ = (Index("ix_knowledge_category", "category"),)


class VectorSyncLog(Base):
    """
    MySQL ↔ ChromaDB 向量同步追踪表

    记录哪些 MySQL 条目已经嵌入到 ChromaDB，
    用于冷启动数据预热（VectorStore.sync_from_mysql）和故障恢复。
    """
    __tablename__ = "vector_sync_log"
    id = Column(BigInteger, autoincrement=True, primary_key=True)
    entry_type = Column(String(32), nullable=False)   # "knowledge" / "paper" / "user_interest"
    entry_id = Column(String(128), nullable=False)    # MySQL 记录 ID
    collection = Column(String(64), default="knowledge")  # ChromaDB collection 名称
    synced_at = Column(DateTime, default=now)
    __table_args__ = (
        Index("ix_vsync_entry", "entry_type", "entry_id", unique=True),
    )


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"
    id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False)
    role = Column(String(32), nullable=False)
    template = Column(Text, nullable=False)
    version = Column(String(16), default="1.0.0")
    success_rate = Column(Float, default=0.0)
    use_count = Column(Integer, default=0)
    quality_score = Column(Float, default=0.0)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now)


class WorkflowTemplate(Base):
    __tablename__ = "workflow_templates"
    id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    steps = Column(JSON, default=list)
    trigger_pattern = Column(String(256), nullable=True)
    success_rate = Column(Float, default=0.0)
    use_count = Column(Integer, default=0)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=now)


class EvaluationRecord(Base):
    __tablename__ = "evaluations"
    id = Column(String(64), primary_key=True)
    task_id = Column(String(64), nullable=False)
    agent_id = Column(String(64), nullable=False)
    accuracy = Column(Float, nullable=True)
    citation_quality = Column(Float, nullable=True)
    novelty = Column(Float, nullable=True)
    reproducibility = Column(Float, nullable=True)
    user_satisfaction = Column(Float, nullable=True)
    overall_score = Column(Float, nullable=True)
    feedback = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now)
    __table_args__ = (Index("ix_eval_agent", "agent_id"),)


class CommunityConsensus(Base):
    __tablename__ = "consensus"
    id = Column(String(64), primary_key=True)
    topic = Column(String(256), nullable=False)
    main_position = Column(Text, nullable=False)
    minority_positions = Column(JSON, default=list)
    supporting_agents = Column(JSON, default=list)
    confidence = Column(Float, default=0.0)
    verified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=now)


# ── 新增表：用户体系 ──────────────────────────────────────────────────

class User(Base):
    """已交互用户，支持多渠道识别"""
    __tablename__ = "users"
    id = Column(String(64), primary_key=True)          # uuid
    feishu_open_id = Column(String(128), nullable=True, unique=True)
    dingtalk_user_id = Column(String(128), nullable=True, unique=True)
    email = Column(String(256), nullable=True)
    name = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)
    __table_args__ = (
        Index("ix_users_feishu", "feishu_open_id"),
        Index("ix_users_dingtalk", "dingtalk_user_id"),
    )


class UserSession(Base):
    """用户会话记录，追踪跨渠道对话上下文"""
    __tablename__ = "user_sessions"
    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False)
    session_id = Column(String(64), nullable=False, unique=True)
    channel = Column(String(32), default="feishu")     # feishu/web/cli
    created_at = Column(DateTime, default=now)
    last_active_at = Column(DateTime, default=now, onupdate=now)
    __table_args__ = (
        Index("ix_sessions_user", "user_id"),
        Index("ix_sessions_sid", "session_id"),
    )


class UserInterestProfile(Base):
    """用户科研兴趣画像，由任务历史自动提炼，驱动自进化推送"""
    __tablename__ = "user_interest_profiles"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False)
    domain = Column(String(128), nullable=False)       # 领域：AI/数学/生物...
    keywords = Column(JSON, default=list)              # 关键词列表
    weight = Column(Float, default=1.0)               # 兴趣强度
    source_task_ids = Column(JSON, default=list)      # 来源任务 ID
    updated_at = Column(DateTime, default=now, onupdate=now)
    __table_args__ = (
        Index("ix_profiles_user", "user_id"),
        Index("ix_profiles_domain", "domain"),
    )


class ProactiveNotification(Base):
    """主动推送记录，防止重复发送"""
    __tablename__ = "proactive_notifications"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False)
    paper_id = Column(String(128), nullable=True)     # arXiv ID 等
    content_hash = Column(String(64), nullable=True)  # 内容 hash，去重
    notification_type = Column(String(32), default="daily_digest")
    sent_at = Column(DateTime, default=now)
    __table_args__ = (
        Index("ix_notif_user_date", "user_id", "sent_at"),
    )


class TaskMetrics(Base):
    """P3: 任务执行指标，用于价值量化仪表盘"""
    __tablename__ = "task_metrics"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    task_id = Column(String(64), nullable=False, unique=True)
    user_id = Column(String(128), nullable=True)
    channel = Column(String(32), default="api")
    status = Column(String(16), default="success")
    duration_seconds = Column(Float, nullable=True)
    tools_used = Column(Integer, default=0)
    iterations = Column(Integer, default=0)
    created_at = Column(DateTime, default=now)
    __table_args__ = (
        Index("ix_metrics_user", "user_id"),
        Index("ix_metrics_created", "created_at"),
    )


class TaskFeedback(Base):
    """P6: 用户对任务结果的评分反馈"""
    __tablename__ = "task_feedback"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    task_id = Column(String(64), nullable=False)
    user_id = Column(String(128), nullable=True)
    rating = Column(Integer, nullable=False)  # 1-5
    created_at = Column(DateTime, default=now)
    __table_args__ = (
        Index("ix_feedback_task", "task_id"),
        Index("ix_feedback_user", "user_id"),
    )


class TaskProgressLog(Base):
    """任务执行过程事件日志，支持按 task_id 查询执行轨迹"""
    __tablename__ = "task_progress_logs"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    task_id = Column(String(64), nullable=False)
    event_type = Column(String(32), nullable=False)   # queued/executing/guardian_pass/guardian_reject/completed/failed/timeout/heartbeat
    message = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSON, nullable=True)  # 额外上下文（worker_id, duration 等）
    created_at = Column(DateTime, default=now)
    __table_args__ = (
        Index("ix_progress_task", "task_id"),
        Index("ix_progress_created", "created_at"),
    )


# ── 数据库引擎 ────────────────────────────────────────────────────────

_engine = None
_session_factory = None


async def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.MYSQL_URL,
            echo=settings.DEBUG,
            future=True,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("[DB] MySQL engine initialized")
    return _engine


async def get_session() -> AsyncSession:
    global _session_factory
    if _session_factory is None:
        engine = await get_engine()
        _session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return _session_factory()


async def init_db():
    """初始化数据库和默认数据"""
    await get_engine()
    await _seed_default_prompts()
    await _seed_default_workflows()
    logger.info("[DB] Database initialized")


# ── 用户操作 ──────────────────────────────────────────────────────────

async def get_or_create_user(feishu_open_id: str) -> User:
    """根据 Feishu open_id 获取或创建用户"""
    import uuid
    from sqlalchemy import select
    async with await get_session() as session:
        result = await session.execute(
            select(User).where(User.feishu_open_id == feishu_open_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            user = User(
                id=str(uuid.uuid4()),
                feishu_open_id=feishu_open_id,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        return user


async def get_or_create_user_dingtalk(dingtalk_user_id: str) -> User:
    """根据钉钉 user_id 获取或创建用户"""
    import uuid
    from sqlalchemy import select
    async with await get_session() as session:
        result = await session.execute(
            select(User).where(User.dingtalk_user_id == dingtalk_user_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            user = User(
                id=str(uuid.uuid4()),
                dingtalk_user_id=dingtalk_user_id,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        return user


async def upsert_interest_profile(user_id: str, domain: str, keywords: list[str]) -> None:
    """更新或新增用户兴趣画像条目"""
    from sqlalchemy import select
    async with await get_session() as session:
        result = await session.execute(
            select(UserInterestProfile).where(
                UserInterestProfile.user_id == user_id,
                UserInterestProfile.domain == domain,
            )
        )
        profile = result.scalar_one_or_none()
        if profile:
            # 合并关键词（去重）
            existing = set(profile.keywords or [])
            profile.keywords = list(existing | set(keywords))
            profile.weight = min(profile.weight + 0.1, 5.0)
        else:
            profile = UserInterestProfile(
                user_id=user_id, domain=domain, keywords=keywords
            )
            session.add(profile)
        await session.commit()


async def get_all_user_interest_profiles() -> list[dict]:
    """获取所有用户兴趣画像（供 Vanguard 匹配使用）"""
    from sqlalchemy import select, distinct
    async with await get_session() as session:
        # 按用户聚合所有领域
        result = await session.execute(
            select(UserInterestProfile)
        )
        profiles = result.scalars().all()

        # 按 user_id 分组
        user_map: dict[str, dict] = {}
        for p in profiles:
            if p.user_id not in user_map:
                user_map[p.user_id] = {"user_id": p.user_id, "domains": [], "keywords": []}
            user_map[p.user_id]["domains"].append(p.domain)
            user_map[p.user_id]["keywords"].extend(p.keywords or [])

        # 补充用户邮箱和飞书 open_id
        user_ids = list(user_map.keys())
        if user_ids:
            uresult = await session.execute(
                select(User).where(User.id.in_(user_ids))
            )
            for u in uresult.scalars().all():
                if u.id in user_map:
                    user_map[u.id]["email"] = u.email
                    user_map[u.id]["feishu_open_id"] = u.feishu_open_id
                    user_map[u.id]["dingtalk_user_id"] = u.dingtalk_user_id
                    user_map[u.id]["name"] = u.name

        return list(user_map.values())


async def already_notified_today(user_id: str) -> bool:
    """今天是否已给该用户发过主动推送"""
    from sqlalchemy import select, func
    from datetime import date
    today_start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    async with await get_session() as session:
        result = await session.execute(
            select(func.count(ProactiveNotification.id)).where(
                ProactiveNotification.user_id == user_id,
                ProactiveNotification.sent_at >= today_start,
            )
        )
        return result.scalar() > 0


async def record_notification(user_id: str, content_hash: str = "") -> None:
    async with await get_session() as session:
        session.add(ProactiveNotification(
            user_id=user_id,
            content_hash=content_hash,
        ))
        await session.commit()


# ── 任务完整记录（对话历史） ──────────────────────────────────────────

async def save_task_record(
    task_id: str,
    title: str,
    user_id: str,
    channel: str,
    reply_info: dict = None,
    input_data: dict = None,
    output_data: dict = None,
    status: str = "pending",
    worker_id: str = None,
    error_message: str = None,
    started_at=None,
    completed_at=None,
) -> None:
    """保存或更新完整任务记录（对话历史持久化）"""
    from sqlalchemy import select
    try:
        async with await get_session() as session:
            existing = await session.execute(
                select(TaskRecord).where(TaskRecord.id == task_id)
            )
            record = existing.scalar_one_or_none()
            if record:
                record.status = status
                if output_data is not None:
                    record.output_data = output_data
                if worker_id:
                    record.worker_id = worker_id
                if error_message:
                    record.error_message = error_message
                if completed_at:
                    record.completed_at = completed_at
            else:
                record = TaskRecord(
                    id=task_id,
                    title=(title or "")[:256],
                    user_id=user_id,
                    channel=channel,
                    reply_info=reply_info,
                    input_data=input_data or {},
                    output_data=output_data,
                    status=status,
                    worker_id=worker_id,
                    error_message=error_message,
                    started_at=started_at,
                    completed_at=completed_at,
                )
                session.add(record)
            await session.commit()
    except Exception as e:
        logger.warning(f"[DB] save_task_record failed: {e}")


# ── P3: 任务指标记录与分析 ───────────────────────────────────────────

async def record_task_metrics(
    task_id: str, user_id: str, channel: str,
    status: str, duration_seconds: float,
    tools_used: int = 0, iterations: int = 0,
) -> None:
    """记录一次任务执行指标"""
    try:
        async with await get_session() as session:
            session.add(TaskMetrics(
                task_id=task_id,
                user_id=user_id,
                channel=channel,
                status=status,
                duration_seconds=duration_seconds,
                tools_used=tools_used,
                iterations=iterations,
            ))
            await session.commit()
    except Exception as e:
        logger.warning(f"[DB] record_task_metrics failed: {e}")


async def get_analytics_overview() -> dict:
    """获取总体分析指标"""
    from sqlalchemy import select, func, distinct
    async with await get_session() as session:
        # 总任务数
        total = (await session.execute(
            select(func.count(TaskMetrics.id))
        )).scalar() or 0

        # 成功率
        success = (await session.execute(
            select(func.count(TaskMetrics.id)).where(TaskMetrics.status == "success")
        )).scalar() or 0
        success_rate = (success / total * 100) if total > 0 else 0

        # 活跃用户
        active_users = (await session.execute(
            select(func.count(distinct(TaskMetrics.user_id)))
        )).scalar() or 0

        # 知识条目数
        knowledge_count = (await session.execute(
            select(func.count(KnowledgeEntry.id))
        )).scalar() or 0

        # 总耗时 → 估算节省小时（每个任务假设为人工 30 分钟）
        estimated_hours = total * 0.5

        # 平均响应时间
        avg_duration = (await session.execute(
            select(func.avg(TaskMetrics.duration_seconds)).where(TaskMetrics.status == "success")
        )).scalar() or 0

        return {
            "total_tasks": total,
            "success_rate": round(success_rate, 1),
            "active_users": active_users,
            "knowledge_entries": knowledge_count,
            "estimated_hours_saved": round(estimated_hours, 1),
            "avg_duration_seconds": round(avg_duration, 1) if avg_duration else 0,
        }


async def get_daily_task_counts(days: int = 30) -> list[dict]:
    """获取近 N 天每日任务数"""
    from sqlalchemy import select, func, cast, Date
    from datetime import timedelta
    cutoff = now() - timedelta(days=days)
    async with await get_session() as session:
        result = await session.execute(
            select(
                cast(TaskMetrics.created_at, Date).label("date"),
                func.count(TaskMetrics.id).label("count"),
            )
            .where(TaskMetrics.created_at >= cutoff)
            .group_by("date")
            .order_by("date")
        )
        return [{"date": str(row.date), "count": row.count} for row in result]


# ── P6: 用户反馈 ────────────────────────────────────────────────────

async def record_feedback(task_id: str, user_id: str, rating: int) -> None:
    """记录用户评分反馈"""
    async with await get_session() as session:
        session.add(TaskFeedback(
            task_id=task_id,
            user_id=user_id,
            rating=rating,
        ))
        await session.commit()


# ── 任务进度日志 ─────────────────────────────────────────────────────

async def log_task_progress(
    task_id: str,
    event_type: str,
    message: str = "",
    metadata: dict = None,
) -> None:
    """记录任务执行过程中的一个事件节点"""
    try:
        async with await get_session() as session:
            session.add(TaskProgressLog(
                task_id=task_id,
                event_type=event_type,
                message=message,
                metadata_=metadata or {},
            ))
            await session.commit()
    except Exception as e:
        logger.debug(f"[DB] log_task_progress failed: {e}")


async def get_task_progress(task_id: str) -> list[dict]:
    """获取指定任务的完整执行过程事件列表"""
    from sqlalchemy import select
    try:
        async with await get_session() as session:
            result = await session.execute(
                select(TaskProgressLog)
                .where(TaskProgressLog.task_id == task_id)
                .order_by(TaskProgressLog.created_at)
            )
            rows = result.scalars().all()
            return [
                {
                    "id": r.id,
                    "event_type": r.event_type,
                    "message": r.message or "",
                    "metadata": r.metadata_ or {},
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
    except Exception:
        return []


async def get_task_detail(task_id: str) -> dict | None:
    """获取单个任务完整详情（基础信息 + 进度日志）"""
    from sqlalchemy import select
    try:
        async with await get_session() as session:
            r = await session.execute(
                select(TaskRecord).where(TaskRecord.id == task_id)
            )
            task = r.scalar_one_or_none()
            if not task:
                return None
            progress = await get_task_progress(task_id)
            return {
                "task_id": task.id,
                "title": task.title or "",
                "user_id": task.user_id or "",
                "channel": task.channel or "api",
                "status": task.status or "pending",
                "worker_id": task.worker_id or "",
                "input": (task.input_data or {}).get("task", "") if isinstance(task.input_data, dict) else "",
                "output": (task.output_data or {}).get("result", "") if isinstance(task.output_data, dict) else "",
                "error": task.error_message or "",
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "started_at": task.started_at.isoformat() if task.started_at else None,
                "completed_at": task.completed_at.isoformat() if task.completed_at else None,
                "duration_seconds": (
                    round((task.completed_at - task.started_at).total_seconds(), 1)
                    if task.completed_at and task.started_at else None
                ),
                "progress": progress,
            }
    except Exception as e:
        logger.warning(f"[DB] get_task_detail failed: {e}")
        return None


async def get_recent_tasks(limit: int = 50, status_filter: str = "all", channel_filter: str = "all") -> list[dict]:
    """获取最近任务列表，用于监控面板"""
    from sqlalchemy import select
    try:
        async with await get_session() as session:
            q = select(TaskRecord).order_by(TaskRecord.created_at.desc()).limit(limit)
            if status_filter and status_filter != "all":
                q = q.where(TaskRecord.status == status_filter)
            if channel_filter and channel_filter != "all":
                q = q.where(TaskRecord.channel == channel_filter)
            result = await session.execute(q)
            tasks = result.scalars().all()
            return [
                {
                    "task_id": t.id,
                    "title": (t.title or "")[:80],
                    "user_id": t.user_id or "",
                    "channel": t.channel or "api",
                    "status": t.status or "pending",
                    "worker_id": t.worker_id or "",
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                    "started_at": t.started_at.isoformat() if t.started_at else None,
                    "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                    "duration_seconds": (
                        round((t.completed_at - t.started_at).total_seconds(), 1)
                        if t.completed_at and t.started_at else None
                    ),
                }
                for t in tasks
            ]
    except Exception as e:
        logger.warning(f"[DB] get_recent_tasks failed: {e}")
        return []


async def get_feedback_stats() -> dict:
    """获取反馈统计"""
    from sqlalchemy import select, func
    async with await get_session() as session:
        total = (await session.execute(
            select(func.count(TaskFeedback.id))
        )).scalar() or 0
        avg_rating = (await session.execute(
            select(func.avg(TaskFeedback.rating))
        )).scalar() or 0
        return {
            "total_feedback": total,
            "avg_rating": round(float(avg_rating), 2) if avg_rating else 0,
        }


async def get_recent_successful_tasks(hours: int = 24, limit: int = 20) -> list[dict]:
    """获取近 N 小时内成功完成的任务（供 Wellspring 知识合成使用）"""
    try:
        from sqlalchemy import select
        from datetime import datetime, timezone, timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        async with await get_session() as session:
            result = await session.execute(
                select(TaskRecord)
                .where(TaskRecord.status == "success")
                .where(TaskRecord.completed_at >= cutoff)
                .order_by(TaskRecord.completed_at.desc())
                .limit(limit)
            )
            tasks = result.scalars().all()
            return [
                {
                    "task": t.title or "",
                    "result": (t.output_data or {}).get("result", "") if isinstance(t.output_data, dict) else "",
                    "agent_name": t.worker_id or "",
                    "status": "success",
                    "quality_score": 0.7,
                }
                for t in tasks
                if t.output_data
            ]
    except Exception:
        return []


async def get_guardian_verdicts(days: int = 7) -> list[dict]:
    """获取近 N 天内 Guardian 审查记录（供自主模式分析使用）"""
    try:
        from sqlalchemy import select
        from datetime import datetime, timezone, timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        async with await get_session() as session:
            result = await session.execute(
                select(TaskRecord)
                .where(TaskRecord.status.in_(["rejected", "escalated"]))
                .where(TaskRecord.started_at >= cutoff)
                .order_by(TaskRecord.started_at.desc())
                .limit(50)
            )
            tasks = result.scalars().all()
            return [
                {
                    "verdict": t.status,
                    "task": t.title or "",
                    "user_id": t.user_id or "",
                }
                for t in tasks
            ]
    except Exception:
        return []


# ── 种子数据 ──────────────────────────────────────────────────────────

async def _seed_default_prompts():
    from sqlalchemy import select
    templates = [
        {
            "id": "pt-worker-base",
            "name": "Worker 通用科研助手",
            "role": "clawer",
            "template": "你是 OpenClaw 科研智能体社区的全能工作者。根据用户任务自主选择合适的工具完成工作。",
        },
        {
            "id": "pt-guardian-review",
            "name": "Guardian 内容审核",
            "role": "guardian",
            "template": "你是 OpenClaw 的 Guardian，负责风险识别和内容审核。返回 JSON verdict。",
        },
    ]
    async with await get_session() as session:
        for t in templates:
            result = await session.execute(
                select(PromptTemplate).where(PromptTemplate.id == t["id"])
            )
            if not result.scalar_one_or_none():
                session.add(PromptTemplate(**t))
        await session.commit()


async def _seed_default_workflows():
    from sqlalchemy import select
    workflows = [
        {
            "id": "wf-literature-review",
            "name": "系统性文献综述",
            "description": "多 Worker 协作完成系统性文献综述",
            "steps": [
                {"step": 1, "action": "前沿趋势扫描"},
                {"step": 2, "action": "文献检索与筛选"},
                {"step": 3, "action": "批判性评审"},
                {"step": 4, "action": "综述写作"},
            ],
            "trigger_pattern": "文献综述|systematic review|research survey",
        },
        {
            "id": "wf-research-design",
            "name": "研究方案设计",
            "description": "端到端研究方案设计与评审",
            "steps": [
                {"step": 1, "action": "背景文献调研"},
                {"step": 2, "action": "实验方案设计"},
                {"step": 3, "action": "数据可行性分析"},
            ],
            "trigger_pattern": "研究设计|实验方案|research design",
        },
    ]
    async with await get_session() as session:
        for w in workflows:
            result = await session.execute(
                select(WorkflowTemplate).where(WorkflowTemplate.id == w["id"])
            )
            if not result.scalar_one_or_none():
                session.add(WorkflowTemplate(**w))
        await session.commit()
