"""
OpenClaw 任务路由器
根据任务类型智能分发到合适的 Agent 或工作流
"""
import re
import uuid
from datetime import datetime
from typing import Optional
from core.registry import AgentRegistry


WORKFLOW_PATTERNS = {
    "literature_review": r"(文献综述|systematic review|literature review|meta.analysis|综述|survey)",
    "research_design": r"(研究设计|研究方案|研究方案设计|实验方案|research design|实验设计|methodology)",
    "frontier_discovery": r"(前沿|趋势|新方向|frontier|emerging|最新进展|研究热点)",
    "data_analysis": r"(数据分析|统计分析|data analysis|statistical|分析数据)",
    "paper_writing": r"(写论文|写作|abstract|摘要|论文|paper writing|学术写作)",
    "code_task": r"(代码|编程|code|python|algorithm|算法|调试|debug)",
    "content_promotion": r"(推广|传播|发布|social media|公众号|宣传)",
}

ROLE_PATTERNS = {
    "vanguard": r"(前沿|趋势|最新|新方向|热点|emerging|frontier|新论文|新工具|数据集推荐)",
    "maintainer": r"(系统问题|报错|修复|健康检查|监控|运维|故障|error|bug report)",
    "promoter": r"(推广|传播|宣传|发布|校园|社交媒体|内容策划)",
    "guardian": r"(审核|审查|合规|风险|安全检查)",
    "wellspring": r"(知识沉淀|社区共识|经验总结|最佳实践|知识库)",
}


class TaskRouter:
    """任务路由器"""

    def __init__(self, registry: AgentRegistry):
        self.registry = registry

    def route(self, task: str, task_type: str = "auto") -> dict:
        """路由决策，返回执行计划"""
        task_id = str(uuid.uuid4())[:8]

        if task_type == "auto":
            task_type = self._detect_type(task)

        # 职能型任务直接路由
        for role, pattern in ROLE_PATTERNS.items():
            if re.search(pattern, task, re.IGNORECASE):
                agent = self._get_functional_agent(role)
                if agent:
                    return {
                        "task_id": task_id,
                        "task_type": "single",
                        "execution_mode": "direct",
                        "primary_agent": agent.agent_id,
                        "agent_name": agent.name,
                        "workflow_id": None,
                        "needs_guardian": role == "promoter",  # Promoter 需要 Guardian 审核
                    }

        # 复杂工作流
        for wf_id, pattern in WORKFLOW_PATTERNS.items():
            if re.search(pattern, task, re.IGNORECASE):
                return {
                    "task_id": task_id,
                    "task_type": "workflow",
                    "execution_mode": "workflow",
                    "primary_agent": self._get_workflow_lead(wf_id),
                    "workflow_id": f"wf-{wf_id.replace('_', '-')}",
                    "needs_guardian": True,
                }

        # 默认：选任意一个 Worker（同质化，无需匹配）
        best = self.registry.get_any_worker()
        return {
            "task_id": task_id,
            "task_type": "single",
            "execution_mode": "direct",
            "primary_agent": best.agent_id if best else "worker-00",
            "agent_name": best.name if best else "Worker",
            "workflow_id": None,
            "needs_guardian": False,
        }

    def _detect_type(self, task: str) -> str:
        """检测任务类型"""
        for wf_id, pattern in WORKFLOW_PATTERNS.items():
            if re.search(pattern, task, re.IGNORECASE):
                return "workflow"
        return "single"

    def _get_functional_agent(self, role: str):
        agents = self.registry.get_by_role(role)
        return agents[0] if agents else None

    def _get_workflow_lead(self, workflow_id: str) -> str:
        """获取工作流主导 Agent"""
        leads = {
            "literature_review": "clawer-survey-01",
            "research_design": "clawer-experiment-01",
            "frontier_discovery": "vanguard-01",
            "data_analysis": "clawer-data-01",
            "paper_writing": "clawer-writing-01",
            "code_task": "clawer-cs-01",
            "content_promotion": "promoter-01",
        }
        return leads.get(workflow_id, "clawer-cs-01")
