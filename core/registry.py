"""
OpenClaw 社区注册中心
管理所有 Agent 实例（Worker Pool + Coordinator Agents）

v3.3 架构：
- 所有 Agent 运行在同一进程内（orchestrator 模式）
- LeadResearcher 是内置执行引擎，由 Registry 创建并注入给每个 WorkerClawer
  不对外暴露为独立 Agent（不出现在 UI Agent 列表）
- 每个 WorkerClawer 通过 run_with_user() 使用 LeadResearcher 的智能能力
- Registry 不再提供 get_lead_researcher()，Worker 即为唯一调度目标
"""
import logging
from core.logging_config import get_logger
from typing import Optional
from agents.base import BaseAgent
from agents.clawer import build_worker_pool
from agents.guardian import GuardianAgent
from agents.vanguard import VanguardAgent
from agents.maintainer import MaintainerAgent
from agents.promoter import PromoterAgent
from agents.wellspring import WellspringAgent
from agents.lead_researcher import LeadResearcher

logger = get_logger(__name__)


class AgentRegistry:
    """Agent 注册中心 — Worker Pool + 协调型 Agent（LeadResearcher 为内部引擎）"""

    def __init__(self):
        self._agents: dict[str, BaseAgent] = {}
        self._role_index: dict[str, list[str]] = {}
        self._initialized = False

    def initialize(self):
        if self._initialized:
            return

        from config.settings import settings

        # LeadResearcher：内置执行引擎，创建后注入每个 Worker
        # 不注册到 _agents，不出现在 UI Agent 列表
        lead = LeadResearcher(registry=self)
        logger.info("[Registry] LeadResearcher 内置引擎初始化完成（用户画像驱动）")

        # Worker Pool：N 个同质化工作者，每个内嵌 LeadResearcher 执行能力
        workers = build_worker_pool(settings.WORKER_COUNT)
        for w in workers:
            w.set_lead_researcher(lead)  # 注入执行引擎
            self.register(w)
        logger.info(
            f"[Registry] Worker pool: {len(workers)} workers"
            f"（每个 Worker 已内嵌 LeadResearcher 执行引擎）"
        )

        # 协调型 Agent（单例）
        self.register(GuardianAgent())
        self.register(VanguardAgent())
        self.register(MaintainerAgent())
        self.register(PromoterAgent())
        self.register(WellspringAgent())

        self._initialized = True
        logger.info(f"[Registry] Initialized: {self.summary()}")

    def register(self, agent: BaseAgent):
        self._agents[agent.agent_id] = agent
        role = agent.role
        if role not in self._role_index:
            self._role_index[role] = []
        if agent.agent_id not in self._role_index[role]:
            self._role_index[role].append(agent.agent_id)

    def get(self, agent_id: str) -> Optional[BaseAgent]:
        return self._agents.get(agent_id)

    def get_by_role(self, role: str) -> list[BaseAgent]:
        ids = self._role_index.get(role, [])
        return [self._agents[i] for i in ids if i in self._agents]

    def get_guardian(self) -> Optional[GuardianAgent]:
        agents = self.get_by_role("guardian")
        return agents[0] if agents else None

    def get_wellspring(self) -> Optional[WellspringAgent]:
        agents = self.get_by_role("wellspring")
        return agents[0] if agents else None

    def get_vanguard(self) -> Optional[VanguardAgent]:
        agents = self.get_by_role("vanguard")
        return agents[0] if agents else None

    def get_maintainer(self) -> Optional[MaintainerAgent]:
        agents = self.get_by_role("maintainer")
        return agents[0] if agents else None

    def get_promoter(self) -> Optional[PromoterAgent]:
        agents = self.get_by_role("promoter")
        return agents[0] if agents else None

    def get_workers(self) -> list[BaseAgent]:
        """返回所有 Worker Clawer 实例（含内嵌 LeadResearcher 引擎）"""
        return self.get_by_role("clawer")

    def get_any_worker(self) -> Optional[BaseAgent]:
        """同步获取任意一个 worker（用于直接调用，非队列模式）"""
        workers = self.get_workers()
        return workers[0] if workers else None

    def list_all(self) -> list[dict]:
        """
        返回所有对外可见的 Agent 列表（不含 LeadResearcher，它是内部引擎）。
        Worker-00/01/02 已内嵌 LeadResearcher 能力，无需单独展示。
        """
        agents_list = []
        for a in self._agents.values():
            agents_list.append({
                "agent_id": a.agent_id,
                "name": a.name,
                "role": a.role,
                "model": a.model,
                "tool_count": len(a.allowed_tools),
                "status": "active" if a.client else "degraded",
            })
        return agents_list

    def summary(self) -> dict:
        total = len(self._agents)
        by_role = {role: len(ids) for role, ids in self._role_index.items()}
        active = sum(1 for a in self._agents.values() if a.client)
        return {
            "total_agents": total,
            "active_agents": active,
            "degraded_agents": total - active,
            "by_role": by_role,
            # LeadResearcher 作为内部引擎，通过 worker 的 _lead 属性反映
            "lead_engine": "embedded_in_workers",
        }


# 全局单例
registry = AgentRegistry()
