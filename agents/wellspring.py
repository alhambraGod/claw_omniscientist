"""
OpenClaw Wellspring - 源泉智能体
社区群体智慧源泉与进化引擎
"""
import json
import logging
from core.logging_config import get_logger
import uuid
from datetime import datetime
from config.settings import now
from typing import Optional
from agents.base import BaseAgent

logger = get_logger(__name__)

WELLSPRING_SYSTEM = """你是 OpenClaw 的 **Wellspring（源泉）**，社区群体认知源泉与进化引擎。

## 核心使命
你不是一个普通的执行者，而是整个 OpenClaw 社区的**集体智慧中枢**：
- 汇总和提炼所有 Agent 的经验
- 形成社区共识（同时保留少数派观点）
- 推动 Prompt 和 Workflow 的持续进化
- 维护社区知识的质量和可追溯性

## 五大内核
1. **共享记忆**：跨 Agent 的任务经验沉淀
2. **共享知识**：论文、数据集、实验经验
3. **Prompt 进化**：高质量模板管理与升级
4. **Workflow 进化**：可复用工作流优化
5. **共识引擎**：形成和管理社区共识

## 工作原则
1. **质量门槛**：只有经过验证的高质量内容才进入知识库
2. **允许分歧**：保留不同观点，不强制统一
3. **来源追踪**：每条知识记录其来源和贡献者
4. **版本管理**：知识条目支持版本迭代
5. **社区优先**：个人能力沉淀为社区财富"""


class WellspringAgent(BaseAgent):
    """Wellspring 源泉"""

    def __init__(self):
        super().__init__(
            agent_id="wellspring-01",
            name="源泉 Wellspring",
            role="wellspring",
            system_prompt=WELLSPRING_SYSTEM,
            tools=["web_search", "arxiv_search", "semantic_scholar_search",
                   "knowledge_extract", "text_summarize", "quality_score",
                   "research_outline", "report_generate", "mind_map"],
        )
        self._shared_memory: list[dict] = []
        self._knowledge_hub: list[dict] = []
        self._prompt_hub: list[dict] = []
        self._workflow_hub: list[dict] = []
        self._consensus: list[dict] = []
        self._db_loaded = False

    async def _ensure_db_loaded(self):
        """从 MySQL 加载已持久化的知识到内存（仅首次）"""
        if self._db_loaded:
            return
        try:
            from sqlalchemy import select
            from core.database import get_session, KnowledgeEntry
            async with await get_session() as session:
                result = await session.execute(
                    select(KnowledgeEntry).order_by(KnowledgeEntry.created_at.desc()).limit(500)
                )
                entries = result.scalars().all()
                for e in entries:
                    self._shared_memory.append({
                        "id": e.id,
                        "source_task": e.title,
                        "content": e.content,
                        "created_at": e.created_at.isoformat() if e.created_at else "",
                        "quality_score": e.quality_score or 0.5,
                    })
                self._db_loaded = True
                logger.info(f"[Wellspring] Loaded {len(entries)} knowledge entries from DB")
        except Exception as e:
            logger.warning(f"[Wellspring] Failed to load from DB: {e}")
            self._db_loaded = True  # 不再重试

    async def ingest_task_result(self, task_result: dict) -> dict:
        """从任务结果中提炼知识，写入内存 + MySQL + ChromaDB"""
        await self._ensure_db_loaded()

        task_str = json.dumps(task_result, ensure_ascii=False)
        task = f"""请从以下任务结果中提炼可沉淀的知识：

{task_str[:2000]}

分析维度：
1. 任务成功的关键因素
2. 使用了哪些有效的方法/工具
3. 可抽象为通用知识的内容
4. 是否有值得收录的文献/数据集引用
5. 这个经验能帮助未来哪些类似任务

输出结构化知识条目（JSON）：
{{
  "knowledge_type": "method/fact/pattern/failure",
  "title": "知识标题",
  "content": "知识内容",
  "applicable_to": ["适用场景"],
  "quality_score": 0.0-1.0,
  "should_persist": true/false
}}"""
        result = await self.run(task)
        if result.get("status") == "success":
            entry_id = str(uuid.uuid4())[:8]
            content_text = result.get("result", "")
            quality = task_result.get("quality_score", 0.5)
            source_task = task_result.get("task", "")
            category = task_result.get("role", "general")
            agent_name = task_result.get("agent_name", "")

            entry = {
                "id": entry_id,
                "source_task": source_task,
                "content": content_text,
                "created_at": now().isoformat(),
                "quality_score": quality,
            }
            self._shared_memory.append(entry)
            if len(self._shared_memory) > 2000:
                self._shared_memory = self._shared_memory[-2000:]

            # ① 持久化到 MySQL
            try:
                from core.database import get_session, KnowledgeEntry
                async with await get_session() as session:
                    session.add(KnowledgeEntry(
                        id=entry_id,
                        title=source_task[:256],
                        content=content_text[:4000],
                        source=agent_name,
                        category=category,
                        quality_score=quality,
                        contributor_agent=task_result.get("agent_id", ""),
                    ))
                    await session.commit()
                logger.info(f"[Wellspring] MySQL 知识条目已写入 | id={entry_id}")
            except Exception as e:
                logger.warning(f"[Wellspring] MySQL 写入失败: {e}")

            # ② 写入 ChromaDB 向量存储（异步后台，不阻塞主流程）
            async def _embed():
                try:
                    from core.vector_store import get_vector_store
                    vs = get_vector_store()
                    embed_text = f"{source_task}\n\n{content_text}"
                    await vs.upsert_knowledge(
                        entry_id=entry_id,
                        content=embed_text,
                        metadata={
                            "source": agent_name,
                            "category": category,
                            "quality_score": quality,
                        },
                    )
                    logger.debug(f"[Wellspring] ChromaDB 嵌入写入 | id={entry_id}")
                except Exception as e:
                    logger.debug(f"[Wellspring] ChromaDB 嵌入失败（忽略）: {e}")

            import asyncio as _asyncio
            _asyncio.create_task(_embed(), name=f"ws-embed-{entry_id}")

        return result

    async def form_consensus(self, topic: str, agent_opinions: list[dict]) -> dict:
        """从多个 Agent 意见中形成共识"""
        opinions_str = json.dumps(agent_opinions, ensure_ascii=False, indent=2)
        task = f"""请基于以下多个 Agent 的意见，形成社区共识：

主题：{topic}

各方意见：
{opinions_str[:3000]}

输出：
1. **主流共识**：多数 Agent 认同的核心观点
2. **少数派观点**：值得保留的不同意见
3. **争议点**：尚无定论的方面
4. **共识置信度**：0-1分
5. **下一步验证建议**

注意：保留有价值的少数意见，不强制统一。"""
        result = await self.run(task)
        if result.get("status") == "success":
            self._consensus.append({
                "id": str(uuid.uuid4())[:8],
                "topic": topic,
                "content": result.get("result", ""),
                "created_at": now().isoformat(),
                "contributors": [o.get("agent_id") for o in agent_opinions],
            })
        return result

    async def evolve_prompt(self, role: str, success_examples: list[str], failure_examples: list[str]) -> dict:
        """进化指定角色的 Prompt 模板"""
        task = f"""请基于以下成功/失败案例，优化 {role} 角色的 Prompt 模板：

成功案例（保留这些特征）：
{json.dumps(success_examples[:3], ensure_ascii=False)}

失败案例（规避这些问题）：
{json.dumps(failure_examples[:3], ensure_ascii=False)}

请提出：
1. 现有 Prompt 的改进点
2. 需要添加的约束或引导
3. 优化后的 Prompt 片段
4. 预期效果"""
        return await self.run(task)

    async def generate_community_knowledge(self, topic: str, focus: str = "") -> dict:
        """主动利用搜索技能生成社区知识条目"""
        await self._ensure_db_loaded()
        focus_hint = f"重点关注：{focus}" if focus else ""
        task = f"""请主动搜索并生成关于 **{topic}** 的高质量社区知识条目。
{focus_hint}

执行步骤：
1. 用 arxiv_search 搜索该主题最新论文（近3个月）
2. 用 web_search 搜索最新进展、工具、实践经验
3. 综合整理，生成 3-5 条高价值知识条目

每条知识条目格式：
### 知识条目 N：[标题]
**类型**：method / fact / pattern / tool / dataset
**内容**：具体知识内容（300-500字）
**适用场景**：[场景列表]
**来源**：[论文/网址引用]
**质量评分**：0.0-1.0

输出要求：内容真实、有引用来源、具有实际参考价值。"""
        result = await self.run(task)

        # 将结果持久化到知识库
        if result.get("status") == "success":
            entry_id = str(uuid.uuid4())[:8]
            try:
                from core.database import get_session, KnowledgeEntry
                async with await get_session() as session:
                    session.add(KnowledgeEntry(
                        id=entry_id,
                        title=f"[社区知识] {topic}" + (f" - {focus}" if focus else ""),
                        content=result.get("result", "")[:8000],
                        source="wellspring-01",
                        category=topic[:64],
                        quality_score=0.8,
                        contributor_agent="wellspring-01",
                    ))
                    await session.commit()
                logger.info(f"[Wellspring] Community knowledge '{topic}' persisted: {entry_id}")
            except Exception as e:
                logger.warning(f"[Wellspring] Failed to persist community knowledge: {e}")

        return result

    async def generate_community_digest(self) -> dict:
        """生成社区知识摘要"""
        memory_summary = f"共享记忆条目：{len(self._shared_memory)}条"
        knowledge_summary = f"知识库条目：{len(self._knowledge_hub)}条"
        consensus_summary = f"社区共识：{len(self._consensus)}条"

        task = f"""请生成 OpenClaw 社区本周知识摘要：

统计数据：
- {memory_summary}
- {knowledge_summary}
- {consensus_summary}

最新共识主题：
{json.dumps([c.get('topic') for c in self._consensus[-5:]], ensure_ascii=False)}

请生成：
1. 本周知识增长概览
2. 重要共识进展
3. 值得关注的新知识点
4. 社区能力进化建议"""
        return await self.run(task)

    async def query_relevant_knowledge(
        self, query: str, max_results: int = 5
    ) -> list[dict]:
        """
        语义检索社区知识（供 LeadResearcher 注入 prompt）。

        策略（优先级从高到低）：
        1. ChromaDB 向量语义搜索（最相关，O(log n)）
        2. 内存关键词匹配降级（ChromaDB 不可用时）
        3. Redis 标签索引补充（内存结果不足时）
        """
        # ① ChromaDB 语义检索（首选）
        try:
            from core.vector_store import get_vector_store
            vs = get_vector_store()
            if vs.is_ready():
                items = await vs.search_knowledge(query, top_k=max_results, min_quality=0.4)
                if items:
                    results = []
                    for item in items:
                        meta = item.get("metadata", {})
                        content = item.get("content", "")
                        # content 格式："{source_task}\n\n{knowledge_text}"
                        parts = content.split("\n\n", 1)
                        title = parts[0][:80] if parts else ""
                        snippet = parts[1][:300] if len(parts) > 1 else content[:300]
                        results.append({
                            "title": title,
                            "snippet": snippet,
                            "quality": float(meta.get("quality", 0.5)),
                            "source": "semantic",
                        })
                    logger.debug(f"[Wellspring] 向量知识检索 | query={query[:30]} | 命中={len(results)}")
                    return results
        except Exception as e:
            logger.debug(f"[Wellspring] ChromaDB 检索失败，降级到关键词: {e}")

        # ② 内存关键词匹配（降级）
        await self._ensure_db_loaded()
        query_lower = query.lower()
        keywords = [w for w in query_lower.split() if len(w) > 2]

        scored = []
        for entry in self._shared_memory[-500:]:
            content = (entry.get("content", "") + " " + entry.get("source_task", "")).lower()
            score = sum(1 for kw in keywords if kw in content)
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for _, entry in scored[:max_results]:
            results.append({
                "title": entry.get("source_task", "")[:80],
                "snippet": entry.get("content", "")[:300],
                "quality": entry.get("quality_score", 0.5),
                "source": "memory",
            })

        # ③ Redis 标签索引补充
        if len(results) < max_results:
            try:
                from core.cache import search_knowledge_ids_by_tag, get_cached_knowledge
                for kw in keywords[:3]:
                    ids = await search_knowledge_ids_by_tag(kw, limit=5)
                    for entry_id in ids:
                        if len(results) >= max_results:
                            break
                        text = await get_cached_knowledge(entry_id)
                        if text and not any(r.get("snippet", "")[:50] == text[:50] for r in results):
                            results.append({
                                "title": f"[社区知识] {kw}",
                                "snippet": text[:300],
                                "quality": 0.8,
                                "source": "redis_cache",
                            })
            except Exception as e:
                logger.debug(f"[Wellspring] Redis 知识检索失败: {e}")

        logger.debug(f"[Wellspring] 关键词知识检索 | query={query[:30]} | 命中={len(results)}")
        return results[:max_results]

    async def refresh_knowledge_cache(self) -> int:
        """将内存中高质量知识条目刷新到 Redis（供 Workers 快速检索）"""
        await self._ensure_db_loaded()
        count = 0
        try:
            from core.cache import cache_knowledge, set_knowledge_index
            high_quality = [
                e for e in self._shared_memory
                if e.get("quality_score", 0) >= 0.7
            ][-100:]

            for entry in high_quality:
                entry_id = entry.get("id", "")
                content = entry.get("content", "")
                source = entry.get("source_task", "")
                if not entry_id or not content:
                    continue

                await cache_knowledge(entry_id, content[:2000])
                # 提取标签（source_task 的主要词汇）
                tags = [w for w in source.lower().split() if len(w) > 3][:10]
                await set_knowledge_index(entry_id, tags)
                count += 1

        except Exception as e:
            logger.warning(f"[Wellspring] 知识缓存刷新失败: {e}")

        logger.info(f"[Wellspring] 知识缓存刷新完成 | 条目={count}")
        return count

    def get_stats(self) -> dict:
        return {
            "shared_memory_count": len(self._shared_memory),
            "knowledge_hub_count": len(self._knowledge_hub),
            "prompt_hub_count": len(self._prompt_hub),
            "workflow_hub_count": len(self._workflow_hub),
            "consensus_count": len(self._consensus),
            "db_loaded": self._db_loaded,
        }
