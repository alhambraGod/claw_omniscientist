"""
OpenClaw Promoter - 推广者智能体
负责内容传播、校园推广、社区增长
"""
from agents.base import BaseAgent

PROMOTER_SYSTEM = """你是 OpenClaw 的 **Promoter（推广者）**，社区影响力与传播中枢。

## 核心职责
- 将科研成果转化为易于传播的内容
- 设计校园推广策略
- 管理社交媒体内容（草稿级别）
- 分析用户反馈和增长数据

## 重要约束
⚠️ **你生成的所有对外内容必须经过 Guardian 审核才能发布**
⚠️ **不直接操作任何社交媒体账号**
⚠️ **不主动联系外部用户**

## 内容原则
1. **科学严谨**：不夸大研究成果
2. **受众友好**：根据平台调整语言风格
3. **完整归因**：保留原始研究者信息
4. **价值导向**：突出对学术社区的实际价值

## 平台风格指南
- 微信公众号：深度、图文、学术圈受众
- 知乎：专业、有深度、可讨论
- 微博：简洁、热点结合、大众化
- Twitter/X：英文、简洁、国际化"""


class PromoterAgent(BaseAgent):
    """Promoter 推广者"""

    def __init__(self):
        super().__init__(
            agent_id="promoter-01",
            name="推广者 Promoter",
            role="promoter",
            system_prompt=PROMOTER_SYSTEM,
            tools=["web_search", "text_summarize", "content_plan",
                   "report_generate", "trend_analysis", "mind_map"],
        )

    async def create_content(self, research_content: str, platform: str, audience: str = "researchers") -> dict:
        """为指定平台生成推广内容草稿"""
        task = f"""请将以下科研内容转化为适合 **{platform}** 平台的推广内容（草稿）：

目标受众：{audience}
科研内容：
{research_content[:2000]}

要求：
1. 适配 {platform} 的语言风格和格式
2. 突出核心价值和创新点
3. 使用适当的话题标签
4. 生成3个备选标题
5. 标注：[待 Guardian 审核后发布]"""
        return await self.run(task)

    async def campus_campaign(self, target_schools: list[str], topic: str) -> dict:
        """设计校园推广方案"""
        schools_str = "、".join(target_schools)
        task = f"""请为以下院校设计 OpenClaw 推广方案：

目标院校：{schools_str}
推广主题：{topic}

方案内容：
1. 推广活动设计（线上/线下）
2. 关键接触点（教授/学生会/研究生院）
3. 内容物料清单
4. 时间计划（4周）
5. 预期效果指标"""
        return await self.run(task)

    async def analyze_feedback(self, feedback_data: str) -> dict:
        """分析用户反馈"""
        task = f"""请分析以下用户反馈，提取关键洞察：

{feedback_data[:2000]}

输出：
1. 主要正面反馈主题
2. 主要投诉/建议类型
3. 优先改进项（Top 3）
4. 下一步行动建议"""
        return await self.run(task)

    async def create_research_highlight(self, knowledge_entries: list[dict]) -> dict:
        """将社区知识条目转化为易传播的科研亮点文章"""
        entries_str = "\n\n".join(
            f"**{e.get('title', '')}**\n{e.get('snippet', '')}"
            for e in knowledge_entries[:3]
        )
        task = f"""请将以下科研知识条目整合为一篇简短的科研亮点文章：

{entries_str}

文章要求：
- 面向科研人员/研究生读者
- 标题吸引人（不夸大，但突出价值）
- 正文500-800字，有逻辑层次
- 结尾给出"对科研的启发"
- 标注"由 OpenClaw 智能体整理，仅供参考"
- [待 Guardian 审核后发布]"""
        return await self.run(task)

    async def weekly_science_digest(self, frontier_summaries: list[str]) -> dict:
        """将 Vanguard 前沿扫描结果合成为周科学简报"""
        combined = "\n\n---\n\n".join(s[:600] for s in frontier_summaries[:4])
        task = f"""请将以下各领域前沿摘要整合为本周《OpenClaw 科学简报》：

{combined}

格式：
## 本周最值得关注
（1-2句话，点明最重要的进展）

## 各领域速览
（每个领域2-3句话，简洁有力）

## 跨领域灵感
（从上述进展中找出跨领域的连接点）

## 给研究者的建议
（具体可操作，不泛泛而谈）

字数控制在600字以内。[待 Guardian 审核后发布]"""
        return await self.run(task)
