"""
Worker Pool 状态监控 API（/api/instances/）

v3.0 架构说明：
  旧版的「多子实例进程管理」已被废弃，改为单进程内的 asyncio Worker Pool 并发模型。
  本路由保留 /api/instances/ 路径（前端/Web UI 兼容），但实际返回的是 Worker Pool 状态。

端点：
  GET  /api/instances/            Worker Pool 整体状态
  GET  /api/instances/workers     所有 Worker 的详细心跳状态
  GET  /api/instances/queue       Redis 任务队列长度
  POST /api/instances/scale       动态调整 Worker 数量（热扩缩容）
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


@router.get("/")
async def api_pool_status():
    """Worker Pool 整体状态（兼容旧版 /api/instances/ 接口）"""
    from core.worker_pool import get_worker_pool
    from core import cache as cache_store

    pool = get_worker_pool()
    workers = pool._workers if hasattr(pool, "_workers") else []
    queue_len = await cache_store.queue_length()
    active_workers = await cache_store.get_active_workers()

    return {
        "mode": "worker_pool",
        "worker_count": len(workers),
        "active_workers": len(active_workers),
        "queue_length": queue_len,
        "status": "running" if pool._running else "stopped",
        "note": "v3.0 架构使用 Worker Pool 替代多子实例进程管理",
    }


@router.get("/workers")
async def api_workers_detail():
    """所有 Worker 的心跳状态（来自 Redis）"""
    from core import cache as cache_store
    from core.worker_pool import get_worker_pool

    pool = get_worker_pool()
    active_workers = await cache_store.get_active_workers()
    registered = [w.agent_id for w in (pool._workers or [])]

    return {
        "total": len(registered),
        "registered": registered,
        "active_heartbeats": active_workers,
    }


@router.get("/queue")
async def api_queue_status():
    """Redis 任务队列状态"""
    from core import cache as cache_store

    queue_len = await cache_store.queue_length()
    return {
        "queue_length": queue_len,
        "status": "healthy" if queue_len < 50 else "backlogged",
    }


class ScaleRequest(BaseModel):
    worker_count: int


@router.post("/scale")
async def api_scale_workers(req: ScaleRequest):
    """Worker 数量建议（实际扩容需重启服务并修改 .env WORKER_COUNT）"""
    if req.worker_count < 1 or req.worker_count > 50:
        raise HTTPException(status_code=400, detail="worker_count 范围：1-50")
    from core.worker_pool import get_worker_pool
    pool = get_worker_pool()
    current = len(pool._workers) if hasattr(pool, "_workers") else 0
    return {
        "success": True,
        "current": current,
        "requested": req.worker_count,
        "message": f"请修改 .env 中的 WORKER_COUNT={req.worker_count} 后重启服务生效",
    }
