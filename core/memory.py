"""
OpenClaw 统一记忆管理器 — 三层记忆架构

┌────────────────────────────────────────────────────────────┐
│  Working Memory  (Redis, TTL=2h)                           │
│  单次任务生命周期内的上下文：推理链、工具结果、中间状态          │
├────────────────────────────────────────────────────────────┤
│  Episodic Memory  (MySQL, 永久)                            │
│  任务历史、用户交互记录、结构化查询                             │
├────────────────────────────────────────────────────────────┤
│  Semantic Memory  (ChromaDB, 永久)                         │
│  知识条目、论文库、用户兴趣向量、长期技能模式                    │
└────────────────────────────────────────────────────────────┘

使用方式：
    from core.memory import get_memory_manager
    mem = get_memory_manager()

    # 存储一条知识（语义层）
    await mem.remember("Transformer attention 复杂度是 O(n²)", layer="semantic", category="method")

    # 跨层检索
    results = await mem.recall("attention mechanism efficiency")

    # Working Memory（当前任务上下文）
    await mem.set_working_context("worker-01", "task-abc", {"step": 3, "partial_result": "..."})
    ctx = await mem.get_working_context("worker-01")
"""
import json
import time
import uuid
from typing import Optional

from config.settings import settings
from core.logging_config import get_logger

logger = get_logger(__name__)

# Working Memory TTL（秒）
_WORKING_MEMORY_TTL = 7200  # 2 hours


class MemoryManager:
    """
    统一三层记忆访问接口
    各层降级友好：某层不可用时不影响其他层
    """

    # ── Working Memory（Redis） ───────────────────────────────────────

    async def set_working_context(
        self,
        agent_id: str,
        task_id: str,
        context: dict,
        ttl: int = _WORKING_MEMORY_TTL,
    ) -> None:
        """
        保存 Agent 当前任务的工作上下文到 Redis。
        相同 agent_id 的旧上下文会被覆盖。
        """
        try:
            from core.cache import get_redis
            from config.settings import settings

            r = await get_redis()
            key = f"{settings.REDIS_KEY_PREFIX}:wm:{agent_id}"
            data = json.dumps({
                "task_id": task_id,
                "context": context,
                "updated_at": time.time(),
            }, ensure_ascii=False)
            await r.setex(key, ttl, data)
            logger.debug(f"[Memory] set_working_context | agent={agent_id} task={task_id[:12]}")
        except Exception as e:
            logger.debug(f"[Memory] set_working_context 失败: {e}")

    async def get_working_context(self, agent_id: str) -> dict:
        """获取 Agent 当前工作上下文，不存在或已过期返回 {}"""
        try:
            from core.cache import get_redis
            from config.settings import settings

            r = await get_redis()
            key = f"{settings.REDIS_KEY_PREFIX}:wm:{agent_id}"
            raw = await r.get(key)
            if raw:
                data = json.loads(raw)
                return data.get("context", {})
        except Exception as e:
            logger.debug(f"[Memory] get_working_context 失败: {e}")
        return {}

    async def update_working_context(self, agent_id: str, updates: dict) -> None:
        """部分更新工作上下文（merge 模式）"""
        try:
            from core.cache import get_redis
            from config.settings import settings

            r = await get_redis()
            key = f"{settings.REDIS_KEY_PREFIX}:wm:{agent_id}"
            raw = await r.get(key)
            if raw:
                existing = json.loads(raw)
                existing["context"].update(updates)
                existing["updated_at"] = time.time()
                await r.setex(key, _WORKING_MEMORY_TTL, json.dumps(existing, ensure_ascii=False))
        except Exception as e:
            logger.debug(f"[Memory] update_working_context 失败: {e}")

    async def clear_working_context(self, agent_id: str) -> None:
        """任务完成后清理工作上下文（可选，TTL 会自动过期）"""
        try:
            from core.cache import get_redis
            from config.settings import settings

            r = await get_redis()
            await r.delete(f"{settings.REDIS_KEY_PREFIX}:wm:{agent_id}")
        except Exception as e:
            logger.debug(f"[Memory] clear_working_context 失败: {e}")

    # ── Semantic Memory（ChromaDB） ────────────────────────────────────

    async def remember(
        self,
        content: str,
        layer: str = "semantic",
        entry_id: str = None,
        **metadata,
    ) -> str:
        """
        存储记忆到指定层。

        layer:
          "semantic"  → ChromaDB knowledge 集合
          "working"   → Redis（需要额外传 agent_id）
          "episodic"  → 直接写 MySQL（一般由各模块自行写入，此处为兜底）

        Returns: entry_id
        """
        eid = entry_id or str(uuid.uuid4())[:16]

        if layer == "semantic":
            try:
                from core.vector_store import get_vector_store
                vs = get_vector_store()
                await vs.upsert_knowledge(eid, content, metadata)
            except Exception as e:
                logger.debug(f"[Memory] semantic remember 失败: {e}")

        elif layer == "working":
            agent_id = metadata.get("agent_id", "unknown")
            task_id = metadata.get("task_id", eid)
            await self.set_working_context(agent_id, task_id, {"content": content, **metadata})

        return eid

    async def recall(
        self,
        query: str,
        layers: Optional[list] = None,
        top_k: int = 5,
        include_episodic: bool = False,
    ) -> list[dict]:
        """
        跨层语义检索记忆。

        默认查询 semantic 层（ChromaDB）。
        include_episodic=True 时追加 MySQL 任务历史（结构化关键词匹配）。

        Returns: [{"content": ..., "source": ..., "score": ...}, ...]
        """
        _layers = layers or ["semantic"]
        results = []

        if "semantic" in _layers:
            try:
                from core.vector_store import get_vector_store
                vs = get_vector_store()
                items = await vs.search_knowledge(query, top_k=top_k)
                for item in items:
                    results.append({
                        "content": item.get("content", ""),
                        "source": "semantic",
                        "score": 1.0 - item.get("distance", 1.0),
                        "metadata": item.get("metadata", {}),
                    })
            except Exception as e:
                logger.debug(f"[Memory] recall semantic 失败: {e}")

        if include_episodic or "episodic" in _layers:
            try:
                episodic = await self._recall_episodic(query, top_k=3)
                results.extend(episodic)
            except Exception as e:
                logger.debug(f"[Memory] recall episodic 失败: {e}")

        # 按相关度排序
        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return results[:top_k]

    async def _recall_episodic(self, query: str, top_k: int = 3) -> list[dict]:
        """从 MySQL 任务历史中关键词检索（Episodic Memory）"""
        try:
            from sqlalchemy import select
            from core.database import get_session, TaskRecord

            keywords = [w for w in query.lower().split() if len(w) > 3]
            if not keywords:
                return []

            async with await get_session() as session:
                stmt = (
                    select(TaskRecord)
                    .where(TaskRecord.status == "success")
                    .order_by(TaskRecord.completed_at.desc())
                    .limit(50)
                )
                result = await session.execute(stmt)
                tasks = result.scalars().all()

            matched = []
            for t in tasks:
                title = (t.title or "").lower()
                score = sum(1 for kw in keywords if kw in title)
                if score > 0:
                    output = t.output_data or {}
                    result_text = output.get("result", "") if isinstance(output, dict) else ""
                    matched.append((score, {
                        "content": f"[历史任务] {t.title}\n{result_text[:300]}",
                        "source": "episodic",
                        "score": score / max(len(keywords), 1),
                        "metadata": {"task_id": t.id, "user_id": t.user_id},
                    }))

            matched.sort(key=lambda x: x[0], reverse=True)
            return [item for _, item in matched[:top_k]]

        except Exception as e:
            logger.debug(f"[Memory] recall_episodic 失败: {e}")
            return []

    # ── 论文检索（语义层专用） ─────────────────────────────────────────

    async def search_papers(
        self,
        query: str,
        top_k: int = 8,
        domain: str = None,
    ) -> list[dict]:
        """语义搜索学术论文（供 LeadResearcher 使用）"""
        try:
            from core.vector_store import get_vector_store
            vs = get_vector_store()
            items = await vs.search_papers(query, top_k=top_k, domain_filter=domain)
            return [
                {
                    "title": item.get("title", ""),
                    "abstract": item.get("abstract", "")[:400],
                    "source": "papers",
                    "score": 1.0 - item.get("distance", 1.0),
                    "metadata": item.get("metadata", {}),
                }
                for item in items
            ]
        except Exception as e:
            logger.debug(f"[Memory] search_papers 失败: {e}")
            return []

    # ── 综合状态 ──────────────────────────────────────────────────────

    async def stats(self) -> dict:
        from core.vector_store import get_vector_store
        vs = get_vector_store()
        return {
            "semantic_layer": await vs.stats(),
            "working_layer": "Redis TTL=2h",
            "episodic_layer": "MySQL (TaskRecord)",
        }


# ── 全局单例 ──────────────────────────────────────────────────────────
_manager: Optional[MemoryManager] = None


def get_memory_manager() -> MemoryManager:
    global _manager
    if _manager is None:
        _manager = MemoryManager()
    return _manager
