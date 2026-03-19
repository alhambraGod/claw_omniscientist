"""
OpenClaw Agent 管理路由
"""
from fastapi import APIRouter, HTTPException
from core.registry import registry

router = APIRouter()


@router.get("/")
async def list_agents():
    """列出所有 Agent"""
    return {"agents": registry.list_all(), "summary": registry.summary()}


@router.get("/{agent_id}")
async def get_agent(agent_id: str):
    """获取单个 Agent 信息"""
    agent = registry.get(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} 未找到")
    return {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "role": agent.role,
        "model": agent.model,
        "tools": agent.allowed_tools,
        "status": "active" if agent.client else "degraded (API key missing)",
    }


@router.get("/role/{role}")
async def get_agents_by_role(role: str):
    """按角色获取 Agent 列表"""
    agents = registry.get_by_role(role)
    return {"role": role, "agents": [a.to_dict() for a in agents]}
