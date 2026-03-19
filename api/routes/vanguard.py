"""
OpenClaw Vanguard 路由
"""
from fastapi import APIRouter, Query
from typing import Optional

router = APIRouter()


@router.get("/explore")
async def explore_frontier(
    domain: str = Query(..., description="研究领域"),
    focus: str = Query("", description="重点方向（可选）"),
):
    """Vanguard 前沿探索"""
    from core.registry import registry
    vg = registry.get_vanguard()
    if not vg:
        return {"status": "error", "result": "Vanguard 未初始化", "error": "vanguard not ready"}
    return await vg.explore_frontier(domain, focus)
