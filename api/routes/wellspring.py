"""
OpenClaw Wellspring 路由
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

router = APIRouter()


class IngestRequest(BaseModel):
    task_result: dict


class ConsensusRequest(BaseModel):
    topic: str
    agent_opinions: list[dict]


class DigestRequest(BaseModel):
    pass


@router.post("/ingest")
async def ingest_knowledge(req: IngestRequest):
    """向 Wellspring 注入任务结果"""
    from core.registry import registry
    ws = registry.get_wellspring()
    if not ws:
        return {"error": "Wellspring 未初始化"}
    return await ws.ingest_task_result(req.task_result)


@router.post("/consensus")
async def form_consensus(req: ConsensusRequest):
    """形成社区共识"""
    from core.registry import registry
    ws = registry.get_wellspring()
    if not ws:
        return {"error": "Wellspring 未初始化"}
    return await ws.form_consensus(req.topic, req.agent_opinions)


@router.get("/stats")
async def get_stats():
    """获取 Wellspring 统计数据"""
    from core.registry import registry
    ws = registry.get_wellspring()
    if not ws:
        return {"error": "Wellspring 未初始化"}
    return ws.get_stats()


@router.get("/digest")
async def get_digest():
    """获取社区知识摘要"""
    from core.registry import registry
    ws = registry.get_wellspring()
    if not ws:
        return {"error": "Wellspring 未初始化"}
    return await ws.generate_community_digest()


class GenerateKnowledgeRequest(BaseModel):
    topic: str
    focus: str = ""


@router.post("/generate")
async def generate_knowledge(req: GenerateKnowledgeRequest):
    """让 Wellspring 主动生成社区知识"""
    from core.registry import registry
    ws = registry.get_wellspring()
    if not ws:
        return {"error": "Wellspring 未初始化"}
    return await ws.generate_community_knowledge(req.topic, req.focus)


@router.get("/knowledge")
async def list_knowledge(limit: int = 20, offset: int = 0, category: str = ""):
    """获取知识库条目列表"""
    from sqlalchemy import select, desc
    from core.database import get_session, KnowledgeEntry
    async with await get_session() as session:
        q = select(KnowledgeEntry).order_by(desc(KnowledgeEntry.created_at))
        if category:
            q = q.where(KnowledgeEntry.category == category)
        q = q.offset(offset).limit(limit)
        rows = (await session.execute(q)).scalars().all()
        return {
            "entries": [
                {
                    "id": e.id,
                    "title": e.title,
                    "content": e.content[:500] if e.content else "",
                    "category": e.category,
                    "source": e.source,
                    "quality_score": e.quality_score,
                    "tags": e.tags or [],
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in rows
            ],
            "total": len(rows),
        }
