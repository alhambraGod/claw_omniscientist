"""
OpenClaw 任务执行路由
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

router = APIRouter()


class TaskRequest(BaseModel):
    task: str
    user_id: str = "anonymous"
    agent_id: Optional[str] = None    # 指定 Agent（可选）
    context: Optional[dict] = None


class QuickAskRequest(BaseModel):
    question: str
    domain: str = "general"
    user_id: str = "anonymous"


class FeedbackRequest(BaseModel):
    task_id: str
    user_id: str = "anonymous"
    rating: int  # 1-5


@router.post("/execute")
async def execute_task(req: TaskRequest, request: Request):
    """执行科研任务（核心接口）"""
    from api.main import get_orchestrator
    orch = get_orchestrator()
    if not orch:
        raise HTTPException(503, "编排器未就绪")

    if req.agent_id:
        from core.registry import registry
        agent = registry.get(req.agent_id)
        if not agent:
            raise HTTPException(404, f"Agent {req.agent_id} 未找到")
        result = await agent.run(req.task, context=req.context)
    else:
        result = await orch.execute(req.task, user_id=req.user_id, context=req.context)

    return result


@router.post("/ask")
async def quick_ask(req: QuickAskRequest):
    """快速问答（自动选择最合适的 Clawer）"""
    from api.main import get_orchestrator
    from core.registry import registry
    orch = get_orchestrator()
    if not orch:
        raise HTTPException(503, "编排器未就绪")

    best = registry.get_any_worker()
    if not best:
        raise HTTPException(503, "无可用 Clawer")
    result = await best.run(req.question)
    return result


@router.get("/history")
async def get_history(limit: int = 20):
    """获取任务历史"""
    from api.main import get_orchestrator
    orch = get_orchestrator()
    if not orch:
        return {"history": []}
    return {"history": orch.get_history(limit)}


@router.get("/running")
async def get_running():
    """获取正在运行的任务"""
    from api.main import get_orchestrator
    orch = get_orchestrator()
    if not orch:
        return {"tasks": []}
    return {"tasks": orch.get_running_tasks()}


@router.post("/route")
async def preview_route(req: TaskRequest):
    """预览任务路由（不执行）"""
    from core.router import TaskRouter
    from core.registry import registry
    router_inst = TaskRouter(registry)
    route = router_inst.route(req.task)
    return {"route": route}


@router.post("/multi-agent")
async def multi_agent_task(req: TaskRequest):
    """通过工作流模式执行任务（LeadResearcher 自动选择最佳 Agent 组合）"""
    from api.main import get_orchestrator
    orch = get_orchestrator()
    if not orch:
        raise HTTPException(503, "编排器未就绪")
    result = await orch.execute(req.task, user_id=req.user_id or "anonymous", context=req.context)
    return result


@router.get("/user-profile/{user_id}")
async def get_user_profile(user_id: str):
    """获取用户累积兴趣画像"""
    from sqlalchemy import select
    from core.database import get_session, UserInterestProfile
    async with await get_session() as session:
        result = await session.execute(
            select(UserInterestProfile).where(UserInterestProfile.user_id == user_id)
        )
        profiles = result.scalars().all()
        return {
            "user_id": user_id,
            "profiles": [
                {
                    "domain": p.domain,
                    "keywords": p.keywords or [],
                    "weight": p.weight,
                    "updated_at": p.updated_at.isoformat() if p.updated_at else None,
                }
                for p in profiles
            ],
            "total_domains": len(profiles),
        }


class QueueSubmitRequest(BaseModel):
    task: str
    user_id: str = "anonymous"
    channel: str = "api"        # "feishu" / "api" etc. — used for conversation tracking
    context: Optional[dict] = None


def _build_reply_info(channel: str, user_id: str, context: dict = None) -> dict:
    """
    构建任务回调信息（reply_info）。
    飞书渠道：从 user_id（格式 feishu:ou_xxx）或 context 中提取 open_id，
    确保 Worker 可以完成主动推送，无论任务通过哪条路径入队。
    """
    reply_info = {"channel": channel}
    if channel == "feishu":
        open_id = None
        # 优先从 context 取（直接 FeishuAdapter 路径会带 message_id/open_id）
        if context:
            open_id = context.get("open_id") or context.get("sender_id")
        # 降级：从 user_id 提取（OpenClaw Gateway → research.sh 路径）
        if not open_id and user_id and user_id.startswith("feishu:"):
            open_id = user_id[len("feishu:"):]
        if open_id:
            reply_info["open_id"] = open_id
        # 保留 message_id，用于线程回复（直接 FeishuAdapter 路径）
        if context and context.get("message_id"):
            reply_info["message_id"] = context["message_id"]
    return reply_info


@router.post("/queue/submit")
async def queue_submit(req: QueueSubmitRequest, request: Request):
    """异步提交任务到 Redis 队列，立即返回 task_id + 排队位置 + 预计等待时间"""
    import uuid, math, asyncio
    from config.settings import now
    from core.cache import (
        push_task, get_queue_position, get_queue_eta,
        get_active_workers, queue_length,
    )
    from core import notifier

    task_id = str(uuid.uuid4())
    created_at = now().isoformat()
    reply_info = _build_reply_info(req.channel, req.user_id, req.context)
    await push_task({
        "task_id": task_id,
        "task": req.task,
        "user_id": req.user_id,
        "channel": req.channel,
        "reply_info": reply_info,
        "created_at": created_at,
        **({"context": req.context} if req.context else {}),
    })

    # 获取排队位置和 ETA
    position = await get_queue_position(task_id)
    active = await get_active_workers()
    eta_seconds = await get_queue_eta(position, len(active)) if position > 0 else 0

    if eta_seconds < 30:
        wait_text = "约30秒内"
    elif eta_seconds < 60:
        wait_text = "约1分钟内"
    elif eta_seconds < 120:
        wait_text = "约1-2分钟"
    else:
        minutes = math.ceil(eta_seconds / 60)
        wait_text = f"约{minutes}分钟"

    # Feishu 渠道：主动推送排队通知
    open_id = reply_info.get("open_id")
    if req.channel == "feishu" and open_id:
        lines = [
            "✅ **任务已接收，正在排队处理**",
            f"📋 任务ID：`{task_id[:16]}`",
            f"⏱️ 预计等待：{wait_text}",
        ]
        if position > 1:
            lines.append(f"📊 当前排队：第 {position} 位（共 {len(active)} 个处理器）")
        lines.append("\n💡 任务完成后将自动推送结果，无需等待。")
        asyncio.create_task(
            notifier.send_proactive_feishu(open_id, "🦞 科研任务入队", "\n".join(lines)),
            name=f"queue-notify-{task_id[:8]}",
        )

    return {
        "task_id": task_id,
        "status": "queued",
        "queue_position": position,
        "estimated_wait_seconds": eta_seconds,
        "estimated_wait_text": wait_text,
    }


@router.post("/lead/execute")
async def lead_execute(req: QueueSubmitRequest, request: Request):
    """
    LeadResearcher 直接执行端点（专属 OpenClaw skill 使用）

    与 /queue/submit 的区别：
    - 立即把任务推入 Redis 队列，但明确标记为 lead_researcher 模式
    - WorkerPool 会优先使用 LeadResearcher（加载用户画像）处理此类任务
    - 返回 task_id，客户端通过 /queue/result/{task_id} 轮询

    该端点是专属 OpenClaw 内 research.sh 的首选调用路径。
    飞书渠道：从 user_id（feishu:ou_xxx 格式）自动提取 open_id，确保 Worker 可主动推送结果。
    """
    import uuid, math
    from config.settings import now
    from core.cache import (
        push_task, get_queue_position, get_queue_eta, get_active_workers,
    )

    task_id = str(uuid.uuid4())
    reply_info = _build_reply_info(req.channel, req.user_id, req.context)
    await push_task({
        "task_id": task_id,
        "task": req.task,
        "user_id": req.user_id,
        "channel": req.channel,
        "use_lead_researcher": True,
        "created_at": now().isoformat(),
        "reply_info": reply_info,
    })

    position = await get_queue_position(task_id)
    active = await get_active_workers()
    eta_seconds = await get_queue_eta(position, len(active)) if position > 0 else 0
    if eta_seconds < 60:
        wait_text = "约1分钟内"
    else:
        wait_text = f"约{math.ceil(eta_seconds / 60)}分钟"

    return {
        "task_id": task_id,
        "status": "queued",
        "mode": "queued",
        "executor": "lead_researcher",
        "queue_position": position,
        "estimated_wait_seconds": eta_seconds,
        "estimated_wait_text": wait_text,
    }


@router.get("/queue/result/{task_id}")
async def queue_result(task_id: str):
    """轮询 Redis 查询任务结果（配合 /queue/submit 和 /lead/execute 使用）"""
    from core.cache import get_result
    result = await get_result(task_id)
    if result is None:
        return {"task_id": task_id, "status": "pending"}
    return result


class EmailSendRequest(BaseModel):
    email: str
    user_id: str = "anonymous"
    channel: str = "feishu"


@router.post("/email/send-pending")
async def email_send_pending(req: EmailSendRequest):
    """
    用户回复邮箱地址时，检查 Redis 中是否有该用户的待发论文并通过 SMTP 发送。
    由 research.sh 在检测到邮箱输入时调用（绕过 LLM 处理）。
    """
    import re, json, logging
    from core.cache import get_redis
    from core import notifier

    logger = logging.getLogger(__name__)
    email_re = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
    if not email_re.match(req.email.strip()):
        raise HTTPException(400, "无效的邮箱地址")

    open_id = None
    if req.user_id and req.user_id.startswith("feishu:"):
        open_id = req.user_id[len("feishu:"):]
    if not open_id:
        return {"success": False, "error": "无法识别用户身份（需要 feishu:ou_xxx 格式的 user_id）"}

    r = await get_redis()
    key = f"pending_email:{open_id}"
    raw = await r.get(key)
    if not raw:
        logger.info(f"[EmailAPI] Redis 无 pending_email | key={key}")
        return {"success": False, "reason": "no_pending", "message": "当前没有待发送的论文内容，请先提交科研任务。"}

    data = json.loads(raw)
    title = data.get("title", "OpenClaw 科研论文")
    content = data.get("content", "")
    logger.info(f"[EmailAPI] ✓ 找到待发论文 | key={key} | content_len={len(content)}")

    try:
        from skills.tools import send_email
        result = await send_email(
            to=req.email.strip(),
            subject=f"【OpenClaw】{title}",
            body=f"# {title}\n\n{content}\n\n---\n*由 OpenClaw 智能科研系统生成*",
        )
    except Exception as e:
        logger.warning(f"[EmailAPI] SMTP 异常: {e}")
        return {"success": False, "error": str(e)}

    if result.get("success"):
        await r.delete(key)
        logger.info(f"[EmailAPI] ✅ 论文邮件已发送 | to={req.email} | open_id={open_id[:12]}…")
        if req.channel == "feishu" and open_id:
            try:
                await notifier.send_proactive_feishu(
                    open_id,
                    "📬 论文已发送至邮箱",
                    f"完整论文已发送至 **{req.email}**，请查收！\n\n"
                    f"> 邮件主题：【OpenClaw】{title}\n\n"
                    "如未收到，请检查垃圾邮件文件夹。",
                )
            except Exception:
                pass
        return {"success": True, "message": f"论文已发送至 {req.email}"}
    else:
        err = result.get("error", "未知错误")
        logger.warning(f"[EmailAPI] ❌ 邮件发送失败: {err}")
        return {"success": False, "error": err}


@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest):
    """提交任务评分反馈"""
    if not 1 <= req.rating <= 5:
        raise HTTPException(400, "评分范围 1-5")
    from core.database import record_feedback
    await record_feedback(req.task_id, req.user_id, req.rating)
    return {"status": "ok", "task_id": req.task_id, "rating": req.rating}


@router.get("/recent")
async def get_recent_tasks(limit: int = 50, status: str = "all", channel: str = "all"):
    """获取最近任务列表（监控面板使用）"""
    from core.database import get_recent_tasks as db_recent
    from core.cache import get_running_tasks
    tasks = await db_recent(limit=limit, status_filter=status, channel_filter=channel)
    # 合并 Redis 中正在执行的任务（比 MySQL 更实时）
    running = await get_running_tasks()
    running_ids = {t.get("task_id") for t in running}
    for t in tasks:
        if t["task_id"] in running_ids:
            t["status"] = "running"
    return {"tasks": tasks, "total": len(tasks)}


@router.get("/detail/{task_id}")
async def get_task_detail(task_id: str):
    """获取单个任务完整详情（含执行进度事件列表）"""
    from core.database import get_task_detail as db_detail
    from core.cache import get_result, get_running_tasks
    detail = await db_detail(task_id)
    if not detail:
        # MySQL 无记录时，尝试从 Redis 取正在运行的状态
        running = await get_running_tasks()
        for t in running:
            if t.get("task_id") == task_id:
                return {
                    "task_id": task_id,
                    "status": "running",
                    "title": t.get("task_text", "")[:80],
                    "user_id": t.get("user_id", ""),
                    "channel": t.get("channel", ""),
                    "worker_id": t.get("worker_id", ""),
                    "started_at": t.get("started_at", None),
                    "progress": [{"event_type": "executing", "message": "任务正在执行中…", "created_at": t.get("started_at")}],
                }
        # 再从 Redis result 取
        redis_result = await get_result(task_id)
        if redis_result:
            return {
                "task_id": task_id,
                "status": redis_result.get("status", "unknown"),
                "title": redis_result.get("result", "")[:80],
                "progress": [{"event_type": "completed", "message": "任务已完成（结果来自缓存）", "created_at": None}],
            }
        raise HTTPException(404, f"任务 {task_id} 未找到")
    return detail
