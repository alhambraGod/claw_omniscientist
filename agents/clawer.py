"""
OpenClaw Worker Clawer — 通用科研工作者（v3.3 架构）

每个 Worker 都是一个完全自洽的科研处理单元：
- 外部视角：Worker-00 / Worker-01 / Worker-02，并发处理任务
- 内部实现：通过 run_with_user() 委托给 LeadResearcher 执行
  （LeadResearcher 是内置的智能执行引擎，不对外暴露为独立 Agent）

这样设计的好处：
1. UI 中只有 Worker-N 系列，不会出现令人困惑的 "LeadResearcher" 条目
2. 每个 Worker 都具备完整的用户画像加载 + 深度研究能力
3. Maintainer 只路由到 Worker-N，架构清晰
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional
from agents.base import BaseAgent
from config.settings import settings

if TYPE_CHECKING:
    from agents.lead_researcher import LeadResearcher

_WORKER_SYSTEM_PROMPT = """你是 OpenClaw 科研智能体社区的全能工作者（Worker Clawer）。
你的职责是为学生、研究员和课题组提供高质量的科研支持。

## 核心能力
你拥有以下所有工具，请根据任务需求自主判断并调用：
- **信息检索**：web_search, arxiv_search, semantic_scholar_search, github_search, url_fetch
- **文献处理**：pdf_extract, text_summarize, citation_format, knowledge_extract, translation
- **学术分析**：research_outline, abstract_generate, hypothesis_generate, experiment_design,
  peer_review, gap_analysis, trend_analysis, paper_compare, methodology_eval, literature_gap
- **数学与代码**：math_solve, code_execute, code_review, data_analysis, statistical_test
- **写作润色**：writing_polish, report_generate, reading_notes, response_letter
- **知识发现**：concept_explain, mind_map, timeline_generate, knowledge_graph
- **通知推送**：send_email（仅在明确要求时使用）

## 工作原则
1. **自主选择工具**：根据任务性质自主决定使用哪些工具，不需要用户指定
2. **证据优先**：关键结论必须有充分证据支撑；若缺乏证据，明确声明
3. **置信度分级**：用 [高置信度] / [中置信度] / [低置信度] 标注核心结论
4. **可追踪**：尽量引用具体文献、数据集或实验来源
5. **多方观点**：对存在争议的问题，呈现不同视角
6. **简洁高效**：避免冗余，直接给出高价值的结论和建议

## 响应规范
- 回答要结构清晰，有段落层次
- 关键数据用粗体或列表呈现
- 如使用了工具，在结尾简要说明引用来源
- 中文用户优先用中文回答，英文任务用英文回答
"""


class WorkerClawer(BaseAgent):
    """
    通用科研工作者 — 自主选择技能完成任意任务。

    通过 set_lead_researcher() 注入 LeadResearcher 执行引擎后，
    run_with_user() 会使用画像驱动的智能执行路径；
    未注入时降级为自身的简单执行路径（BaseAgent.run）。
    """

    def __init__(self, agent_id: str, worker_index: int = 0):
        super().__init__(
            agent_id=agent_id,
            name=f"工作者 Worker-{worker_index:02d}",
            role="clawer",
            system_prompt=_WORKER_SYSTEM_PROMPT,
            model=settings.DEFAULT_MODEL,
            tools=None,  # None = 允许全部技能
        )
        self.worker_index = worker_index
        self._lead: Optional["LeadResearcher"] = None

    def set_lead_researcher(self, lead: "LeadResearcher") -> None:
        """
        注入 LeadResearcher 执行引擎（由 Registry 在初始化时调用）。
        注入后 run_with_user() 将使用用户画像驱动的高质量执行路径。
        """
        self._lead = lead

    async def run_with_user(
        self,
        task: str,
        user_id: str = "anonymous",
        context: dict = None,
    ) -> dict:
        """
        带用户上下文的任务执行入口（WorkerPool 主调用路径）。

        优先使用 LeadResearcher（用户画像 + 深度研究模式）；
        若未注入，降级到自身 BaseAgent.run()（无画像版本）。
        """
        if self._lead is not None:
            return await self._lead.run(task, user_id=user_id, context=context)
        # 降级：LeadResearcher 不可用时直接执行
        return await self.run(task, context=context)


def build_worker_pool(n: int = None) -> list[WorkerClawer]:
    """创建 N 个同质化工作者实例"""
    count = n or settings.WORKER_COUNT
    return [WorkerClawer(agent_id=f"worker-{i:02d}", worker_index=i) for i in range(count)]
