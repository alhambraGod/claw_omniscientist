"""
OpenClaw 系统管理路由
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

router = APIRouter()


@router.get("/status")
async def system_status():
    """系统整体状态"""
    from core.registry import registry
    maintainer = registry.get_maintainer()
    all_agents = registry.list_all()
    health = {}
    if maintainer:
        health = maintainer.check_agent_health(
            {a["agent_id"]: registry.get(a["agent_id"]) for a in all_agents}
        )
    metrics = {}
    if maintainer:
        metrics = maintainer.collect_system_metrics()
    return {
        "status": "running",
        "agents": registry.summary(),
        "metrics": metrics,
        "agent_health": health,
    }


@router.get("/alerts")
async def get_alerts(limit: int = 20):
    """获取系统告警（内存 + Redis 双源）"""
    from core.registry import registry
    from core.cache import get_recent_alerts as redis_alerts
    maintainer = registry.get_maintainer()
    in_memory = maintainer.get_recent_alerts(limit) if maintainer else []
    redis_list = await redis_alerts(limit)
    # 合并去重（Redis 优先，更新更即时）
    combined = redis_list + [
        a for a in in_memory
        if not any(r.get("message") == a.get("message") for r in redis_list)
    ]
    return {"alerts": combined[-limit:]}


@router.post("/diagnose")
async def diagnose(task_id: str, error: str):
    """诊断任务失败"""
    from core.registry import registry
    maintainer = registry.get_maintainer()
    if not maintainer:
        return {"error": "Maintainer 未初始化"}
    return await maintainer.diagnose_failure(task_id, error)


@router.get("/skills")
async def list_skills():
    """列出所有可用技能"""
    from skills.tools import SKILL_REGISTRY
    return {
        "skills": [
            {"name": k, "description": v["description"]}
            for k, v in SKILL_REGISTRY.items()
        ],
        "count": len(SKILL_REGISTRY),
    }


@router.post("/guardian/review")
async def guardian_review(content: str, review_type: str = "output"):
    """手动触发 Guardian 审核"""
    from core.registry import registry
    guardian = registry.get_guardian()
    if not guardian:
        return {"error": "Guardian 未初始化"}
    if review_type == "input":
        return await guardian.review_input(content)
    elif review_type == "publish":
        return await guardian.review_publish(content)
    else:
        return await guardian.review_output(content)


@router.post("/vanguard/explore")
async def vanguard_explore(domain: str, focus: str = ""):
    """触发 Vanguard 前沿探索"""
    from core.registry import registry
    vanguard = registry.get_vanguard()
    if not vanguard:
        return {"error": "Vanguard 未初始化"}
    return await vanguard.explore_frontier(domain, focus)


@router.get("/autonomous/status")
async def autonomous_status():
    """获取所有自主 Agent 的运行状态及调度计划"""
    from core.cache import get_all_agent_run_statuses, get_recent_alerts
    agent_statuses = await get_all_agent_run_statuses()
    recent_alerts = await get_recent_alerts(10)
    return {
        "autonomous_enabled": __import__("config.settings", fromlist=["settings"]).settings.AUTONOMOUS_ENABLED,
        "agent_statuses": agent_statuses,
        "recent_alerts": recent_alerts,
        "schedule": {
            "maintainer_health": "每10分钟",
            "maintainer_daily_report": "每天 23:30 CST",
            "vanguard_morning": "每天 08:10 CST（AI/CS 领域）",
            "vanguard_evening": "每天 20:10 CST（生命科学/材料/交叉领域）",
            "wellspring_synthesis": "每天 02:30 CST",
            "wellspring_weekly_digest": "每周一 01:00 CST",
            "promoter_content": "每周二 10:00 CST",
            "guardian_pattern_review": "每周日 23:00 CST",
        },
    }


@router.get("/memory/stats")
async def memory_stats():
    """三层记忆架构状态（Working/Episodic/Semantic）"""
    from core.memory import get_memory_manager
    from core.vector_store import get_vector_store
    mm = get_memory_manager()
    vs = get_vector_store()
    return {
        "memory_layers": await mm.stats(),
        "vector_store": await vs.stats(),
    }


@router.get("/memory/search")
async def memory_search(query: str, top_k: int = 5):
    """语义检索社区知识（测试/调试用）"""
    from core.memory import get_memory_manager
    mm = get_memory_manager()
    results = await mm.recall(query, layers=["semantic"], top_k=top_k)
    return {"query": query, "results": results}


@router.get("/memory/papers")
async def paper_search(query: str, top_k: int = 8):
    """语义搜索论文库"""
    from core.memory import get_memory_manager
    mm = get_memory_manager()
    results = await mm.search_papers(query, top_k=top_k)
    return {"query": query, "papers": results}


@router.post("/autonomous/trigger/{job_name}")
async def trigger_autonomous_job(job_name: str):
    """手动触发自主任务（用于测试/调试）"""
    try:
        from core.autonomous_loop import trigger_now
        await trigger_now(job_name)
        return {"status": "triggered", "job": job_name}
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(400, str(e))
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(500, f"触发失败: {e}")


@router.get("/tasks/running")
async def running_tasks():
    """
    实时查看正在执行的任务（超时监控面板）。

    返回：
    - running: 每个任务的 task_id、user_id、channel、已运行时长、超时阈值
    - timeout_threshold: 当前系统超时配置（秒）
    - watchdog_threshold: 看门狗介入阈值（秒）
    """
    import time
    from core.cache import get_running_tasks
    from config.settings import settings

    running = await get_running_tasks()
    now = time.time()
    timeout_threshold = settings.TASK_TIMEOUT
    watchdog_threshold = timeout_threshold + 60

    tasks_list = []
    for task_id, info in running.items():
        started_at = info.get("started_at", now)
        elapsed = int(now - started_at)
        tasks_list.append({
            "task_id": task_id,
            "task_id_short": task_id[:16],
            "user_id": info.get("user_id", "?"),
            "channel": info.get("channel", "?"),
            "worker_id": info.get("worker_id", "?"),
            "elapsed_seconds": elapsed,
            "started_at": info.get("started_at"),
            "task_preview": info.get("task_text", "")[:80],
            "status": (
                "⚠️ 即将超时" if elapsed >= timeout_threshold - 30
                else ("🔴 超时（watchdog 待处理）" if elapsed >= watchdog_threshold else "🟢 运行中")
            ),
        })

    tasks_list.sort(key=lambda x: x["elapsed_seconds"], reverse=True)

    return {
        "running_count": len(tasks_list),
        "timeout_threshold_seconds": timeout_threshold,
        "watchdog_threshold_seconds": watchdog_threshold,
        "tasks": tasks_list,
    }
