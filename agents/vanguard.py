"""
OpenClaw Vanguard - 拓荒者智能体
负责前沿探索、趋势发现、机会识别、跨领域连接
"""
from agents.base import BaseAgent

VANGUARD_SYSTEM = """你是 OpenClaw 的 **Vanguard（拓荒者）**，前沿探索与机会发现引擎。

## 核心使命
发现科研领域的前沿动态，为社区提供：
- 最新研究趋势（近3-6个月）
- 新兴数据集、工具、基准
- 高潜力研究机会
- 值得深入探索的方向
- 跨学科交叉创新点

## 探索原则
1. **新颖优先**：重点关注近期出现的内容，而非已广为人知的成果
2. **多源验证**：同一方向要从多个来源（arXiv、GitHub、会议）确认
3. **可操作性**：推荐的方向要具备实际可行性
4. **连接现有**：将新发现与社区已知知识关联
5. **批判思维**：区分炒作与实质进展

## 输出规范
- 趋势摘要需标注信息来源和时间
- 标注置信度：[已确认趋势/新兴信号/弱信号]
- 区分短期热点和长期方向
- 每个方向给出"为什么值得关注"的理由

你的探索成果将进入 Wellspring 知识库，供全体 Clawer 使用，也将用于每日个性化科研推送。"""


class VanguardAgent(BaseAgent):
    """Vanguard 拓荒者"""

    def __init__(self):
        super().__init__(
            agent_id="vanguard-01",
            name="拓荒者 Vanguard",
            role="vanguard",
            system_prompt=VANGUARD_SYSTEM,
            tools=["web_search", "arxiv_search", "semantic_scholar_search",
                   "github_search", "url_fetch", "trend_analysis",
                   "dataset_discover", "knowledge_extract", "report_generate"],
        )

    async def explore_frontier(self, domain: str, focus: str = "") -> dict:
        """探索指定领域前沿，结果同步嵌入到 ChromaDB 向量库"""
        from config.settings import now
        current_month = now().strftime("%Y年%m月")

        task = f"""请对 **{domain}** 领域进行{current_month}前沿探索。
{'重点关注：' + focus if focus else ''}

执行步骤（按顺序）：
1. 用 arxiv_search 搜索该领域过去3个月内的新论文（关键词：{domain}，按时间排序）
2. 用 github_search 搜索相关高星项目（近6个月活跃）
3. 综合分析，生成结构化前沿报告

## 报告格式
### 🔥 前沿趋势（Top 5，每条说明来源和时间）
### 🛠️ 新兴工具 / 数据集
### 💡 高潜力研究机会（可直接立项）
### 📌 推荐重点关注（论文/项目/团队）
### 🔮 3个月内预判"""
        result = await self.run(task)

        # 将前沿报告写入 ChromaDB（论文集合）
        if result.get("status") == "success":
            frontier_text = result.get("result", "")
            if frontier_text and len(frontier_text) > 100:
                import asyncio as _asyncio
                async def _embed_frontier():
                    try:
                        from core.vector_store import get_vector_store
                        vs = get_vector_store()
                        if vs.is_ready():
                            import hashlib
                            from config.settings import now as _now
                            paper_id = hashlib.md5(
                                f"{domain}:{_now().strftime('%Y%m%d')}".encode()
                            ).hexdigest()
                            await vs.upsert_paper(
                                paper_id=paper_id,
                                title=f"[Vanguard前沿报告] {domain} — {_now().strftime('%Y-%m-%d')}",
                                abstract=frontier_text[:800],
                                metadata={
                                    "domain": domain[:64],
                                    "year": _now().strftime("%Y"),
                                    "authors": "Vanguard Agent",
                                    "url": "",
                                },
                            )
                    except Exception as e:
                        from core.logging_config import get_logger as _gl
                        _gl(__name__).debug(f"[Vanguard] ChromaDB 写入失败: {e}")

                _asyncio.create_task(_embed_frontier(), name=f"vg-embed-{domain[:20]}")

        return result

    async def discover_datasets(self, task_description: str) -> dict:
        """发现适合任务的数据集"""
        task = f"请为以下研究任务发现并推荐最合适的公开数据集：\n{task_description}"
        return await self.run(task)

    async def trend_report(self, domains: list[str]) -> dict:
        """生成多领域趋势报告"""
        domains_str = "、".join(domains)
        from config.settings import now
        task = f"""请生成以下领域的综合趋势报告（{now().strftime('%Y年%m月')}）：{domains_str}

要求：
1. 每个领域的 Top 3 热点（标注具体论文/项目来源）
2. 跨领域交叉方向（最具创新价值）
3. 本月最值得关注的论文/项目（≤5篇）
4. 对未来3个月的预判
5. 对科研人员的行动建议"""
        return await self.run(task)

    async def find_research_gaps(self, domain: str) -> dict:
        """发现领域内的研究空白（待填补的 gap）"""
        task = f"""请分析 **{domain}** 领域当前的研究空白：

1. 用 arxiv_search 检索该领域近期综述论文（survey/review）
2. 分析"已解决"与"待解决"的问题边界
3. 找出：
   - 技术缺口（现有方法的局限）
   - 数据缺口（缺少基准/数据集的问题）
   - 应用缺口（理论成熟但应用落地不足）
   - 跨学科机会（与其他领域结合的潜力）
4. 为每个 gap 评估：难度/影响力/可操作性（各1-5分）
5. 给出最推荐的3个切入点"""
        return await self.run(task)

    async def weekly_innovation_brief(self, user_interests: list[str]) -> dict:
        """根据用户兴趣生成个性化创新简报"""
        interests_str = "、".join(user_interests[:5])
        task = f"""请为对以下方向感兴趣的科研人员生成本周创新简报：
研究兴趣：{interests_str}

简报内容：
1. **本周亮点**（最值得一读的1篇论文，附摘要翻译）
2. **新工具速递**（1个新发布的工具/数据集）
3. **思维拓展**（1个跨学科灵感，与你的方向意外相关）
4. **下周关注**（即将截止的会议/值得跟进的预印本）
5. **行动建议**（本周可以做的一件具体事情）"""
        return await self.run(task)
