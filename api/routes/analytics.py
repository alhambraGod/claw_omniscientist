"""
OpenClaw 数据分析路由 — 价值量化仪表盘
"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/overview")
async def analytics_overview():
    """总体分析指标：任务数、成功率、活跃用户、知识条目、估算节省时间"""
    from core.database import get_analytics_overview, get_feedback_stats
    overview = await get_analytics_overview()
    feedback = await get_feedback_stats()
    overview.update(feedback)
    return overview


@router.get("/timeline")
async def analytics_timeline(days: int = 30):
    """近 N 天每日任务量"""
    from core.database import get_daily_task_counts
    daily = await get_daily_task_counts(days)
    return {"daily": daily, "days": days}
