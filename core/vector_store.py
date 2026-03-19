"""
OpenClaw 向量存储层 — ChromaDB 异步封装

三个 Collection：
- knowledge  : Wellspring 知识条目（研究方法、实验经验、社区共识）
- papers     : Vanguard 从 arXiv / Semantic Scholar 获取的学术论文
- user_interests : 用户研究兴趣聚合向量（供 Evolution Loop 匹配）

设计原则：
- ChromaDB PersistentClient 是同步 API，用 run_in_executor 包装为异步
- 降级友好：ChromaDB 不可用时所有方法静默降级，不阻断主流程
- 嵌入模型：默认使用 ChromaDB 内置（sentence-transformers all-MiniLM-L6-v2）
  可通过 CHROMA_EMBEDDING_MODEL=openai 切换到 OpenAI text-embedding-3-small
"""
import asyncio
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from config.settings import settings
from core.logging_config import get_logger

logger = get_logger(__name__)

# 全局线程池（ChromaDB 同步操作在此运行）
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="chromadb")


def _run_sync(fn, *args, **kwargs):
    """在线程池中运行同步函数，返回 awaitable"""
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(_EXECUTOR, lambda: fn(*args, **kwargs))


class VectorStore:
    """ChromaDB 三集合向量存储（异步接口）"""

    def __init__(self):
        self._client = None
        self._knowledge = None
        self._papers = None
        self._user_interests = None
        self._ef = None
        self._ready = False

    def _init_sync(self):
        """同步初始化（在线程池中运行）"""
        try:
            import chromadb
            from chromadb.utils import embedding_functions

            # 选择嵌入模型
            if settings.CHROMA_EMBEDDING_MODEL == "openai" and settings.OPENAI_API_KEY:
                self._ef = embedding_functions.OpenAIEmbeddingFunction(
                    api_key=settings.OPENAI_API_KEY,
                    model_name="text-embedding-3-small",
                    api_base=settings.OPENROUTER_BASE_URL or None,
                )
                logger.info("[VectorStore] 使用 OpenAI text-embedding-3-small")
            else:
                self._ef = embedding_functions.DefaultEmbeddingFunction()
                logger.info("[VectorStore] 使用内置嵌入模型（all-MiniLM-L6-v2）")

            self._client = chromadb.PersistentClient(path=settings.CHROMA_DB_PATH)

            self._knowledge = self._client.get_or_create_collection(
                name="knowledge",
                embedding_function=self._ef,
                metadata={"description": "Wellspring 知识条目", "hnsw:space": "cosine"},
            )
            self._papers = self._client.get_or_create_collection(
                name="papers",
                embedding_function=self._ef,
                metadata={"description": "arXiv/SS 学术论文", "hnsw:space": "cosine"},
            )
            self._user_interests = self._client.get_or_create_collection(
                name="user_interests",
                embedding_function=self._ef,
                metadata={"description": "用户兴趣向量", "hnsw:space": "cosine"},
            )

            self._ready = True
            counts = {
                "knowledge": self._knowledge.count(),
                "papers": self._papers.count(),
                "user_interests": self._user_interests.count(),
            }
            logger.info(f"[VectorStore] 初始化完成 | 路径={settings.CHROMA_DB_PATH} | 条目={counts}")
        except Exception as e:
            logger.warning(f"[VectorStore] 初始化失败（降级模式）: {e}")
            self._ready = False

    async def initialize(self):
        """异步初始化入口（在 api/main.py lifespan 中调用）"""
        await _run_sync(self._init_sync)

    # ── 知识条目管理 ────────────────────────────────────────────────────

    async def upsert_knowledge(
        self,
        entry_id: str,
        content: str,
        metadata: dict = None,
    ) -> bool:
        """写入/更新知识条目（Wellspring 调用）"""
        if not self._ready:
            return False
        meta = {
            "source": metadata.get("source", "") if metadata else "",
            "category": metadata.get("category", "general") if metadata else "general",
            "quality": float(metadata.get("quality_score", 0.5) if metadata else 0.5),
            "ts": int(time.time()),
        }
        try:
            def _sync():
                self._knowledge.upsert(
                    ids=[entry_id],
                    documents=[content[:2000]],
                    metadatas=[meta],
                )
            await _run_sync(_sync)
            logger.debug(f"[VectorStore] knowledge upsert | id={entry_id[:12]}")
            return True
        except Exception as e:
            logger.warning(f"[VectorStore] knowledge upsert 失败: {e}")
            return False

    async def search_knowledge(
        self,
        query: str,
        top_k: int = 5,
        min_quality: float = 0.0,
    ) -> list[dict]:
        """语义搜索知识条目"""
        if not self._ready:
            return []
        try:
            def _sync():
                where = {"quality": {"$gte": min_quality}} if min_quality > 0 else None
                kwargs = dict(query_texts=[query[:500]], n_results=min(top_k, max(1, self._knowledge.count())))
                if where:
                    kwargs["where"] = where
                return self._knowledge.query(**kwargs)

            results = await _run_sync(_sync)
            items = []
            if results and results.get("ids") and results["ids"][0]:
                for i, doc_id in enumerate(results["ids"][0]):
                    items.append({
                        "id": doc_id,
                        "content": results["documents"][0][i] if results.get("documents") else "",
                        "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
                        "distance": results["distances"][0][i] if results.get("distances") else 1.0,
                    })
            return items
        except Exception as e:
            logger.debug(f"[VectorStore] search_knowledge 失败: {e}")
            return []

    async def knowledge_count(self) -> int:
        if not self._ready:
            return 0
        try:
            return await _run_sync(self._knowledge.count)
        except Exception:
            return 0

    # ── 论文管理 ──────────────────────────────────────────────────────

    async def upsert_paper(
        self,
        paper_id: str,
        title: str,
        abstract: str,
        metadata: dict = None,
    ) -> bool:
        """写入/更新论文（Vanguard 调用）"""
        if not self._ready:
            return False
        doc = f"{title}\n\n{abstract}"
        meta = {
            "title": title[:200],
            "authors": metadata.get("authors", "") if metadata else "",
            "year": metadata.get("year", "") if metadata else "",
            "domain": metadata.get("domain", "") if metadata else "",
            "url": metadata.get("url", "") if metadata else "",
            "ts": int(time.time()),
        }
        try:
            def _sync():
                self._papers.upsert(
                    ids=[paper_id],
                    documents=[doc[:3000]],
                    metadatas=[meta],
                )
            await _run_sync(_sync)
            logger.debug(f"[VectorStore] paper upsert | id={paper_id[:24]}")
            return True
        except Exception as e:
            logger.warning(f"[VectorStore] paper upsert 失败: {e}")
            return False

    async def paper_exists(self, paper_id: str) -> bool:
        """检查论文是否已存在（去重）"""
        if not self._ready:
            return False
        try:
            def _sync():
                return self._papers.get(ids=[paper_id])
            result = await _run_sync(_sync)
            return bool(result and result.get("ids"))
        except Exception:
            return False

    async def search_papers(
        self,
        query: str,
        top_k: int = 10,
        domain_filter: str = None,
    ) -> list[dict]:
        """语义搜索论文"""
        if not self._ready:
            return []
        try:
            def _sync():
                count = self._papers.count()
                if count == 0:
                    return None
                kwargs = dict(
                    query_texts=[query[:500]],
                    n_results=min(top_k, count),
                )
                if domain_filter:
                    kwargs["where"] = {"domain": {"$eq": domain_filter}}
                return self._papers.query(**kwargs)

            results = await _run_sync(_sync)
            if not results:
                return []
            items = []
            if results.get("ids") and results["ids"][0]:
                for i, doc_id in enumerate(results["ids"][0]):
                    meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                    items.append({
                        "id": doc_id,
                        "title": meta.get("title", ""),
                        "abstract": results["documents"][0][i] if results.get("documents") else "",
                        "metadata": meta,
                        "distance": results["distances"][0][i] if results.get("distances") else 1.0,
                    })
            return items
        except Exception as e:
            logger.debug(f"[VectorStore] search_papers 失败: {e}")
            return []

    async def papers_count(self) -> int:
        if not self._ready:
            return 0
        try:
            return await _run_sync(self._papers.count)
        except Exception:
            return 0

    # ── 用户兴趣管理 ───────────────────────────────────────────────────

    async def upsert_user_interest(
        self,
        user_id: str,
        interest_text: str,
    ) -> bool:
        """更新用户兴趣向量（interest_extractor 调用）"""
        if not self._ready:
            return False
        # 规范化 user_id 为向量 ID（ChromaDB ID 不允许特殊字符）
        vec_id = hashlib.md5(user_id.encode()).hexdigest()
        try:
            def _sync():
                self._user_interests.upsert(
                    ids=[vec_id],
                    documents=[interest_text[:1000]],
                    metadatas=[{
                        "user_id": user_id[:128],
                        "updated_at": int(time.time()),
                    }],
                )
            await _run_sync(_sync)
            logger.debug(f"[VectorStore] user_interest upsert | uid={user_id[:16]}")
            return True
        except Exception as e:
            logger.warning(f"[VectorStore] user_interest upsert 失败: {e}")
            return False

    async def find_matching_users(
        self,
        content: str,
        top_k: int = 30,
        max_distance: float = 0.7,
    ) -> list[str]:
        """
        根据内容找到兴趣最相关的用户列表（Evolution Loop 使用）
        替代原有的 LLM 语义匹配，10x 提速，零 API 消耗
        """
        if not self._ready:
            return []
        try:
            def _sync():
                count = self._user_interests.count()
                if count == 0:
                    return None
                return self._user_interests.query(
                    query_texts=[content[:500]],
                    n_results=min(top_k, count),
                )
            results = await _run_sync(_sync)
            if not results:
                return []

            user_ids = []
            if results.get("ids") and results["ids"][0]:
                for i, _ in enumerate(results["ids"][0]):
                    dist = results["distances"][0][i] if results.get("distances") else 1.0
                    if dist <= max_distance:
                        meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                        uid = meta.get("user_id", "")
                        if uid:
                            user_ids.append(uid)
            logger.debug(f"[VectorStore] find_matching_users | 命中={len(user_ids)}/{top_k}")
            return user_ids
        except Exception as e:
            logger.debug(f"[VectorStore] find_matching_users 失败: {e}")
            return []

    async def users_count(self) -> int:
        if not self._ready:
            return 0
        try:
            return await _run_sync(self._user_interests.count)
        except Exception:
            return 0

    # ── 管理接口 ─────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        return self._ready

    async def stats(self) -> dict:
        if not self._ready:
            return {"ready": False}
        try:
            k = await _run_sync(self._knowledge.count)
            p = await _run_sync(self._papers.count)
            u = await _run_sync(self._user_interests.count)
            return {
                "ready": True,
                "path": settings.CHROMA_DB_PATH,
                "embedding_model": settings.CHROMA_EMBEDDING_MODEL,
                "collections": {
                    "knowledge": k,
                    "papers": p,
                    "user_interests": u,
                },
            }
        except Exception as e:
            return {"ready": False, "error": str(e)}

    async def sync_from_mysql(self, limit: int = 500) -> int:
        """
        将 MySQL KnowledgeEntry 中尚未嵌入的条目批量同步到 ChromaDB
        启动时或手动触发，实现冷启动数据预热
        """
        if not self._ready:
            return 0
        synced = 0
        try:
            from core.database import get_session, KnowledgeEntry, VectorSyncLog
            from sqlalchemy import select, not_, exists

            async with await get_session() as session:
                # 找出还没有同步记录的 KnowledgeEntry
                synced_subq = select(VectorSyncLog.entry_id).where(
                    VectorSyncLog.entry_type == "knowledge"
                )
                stmt = (
                    select(KnowledgeEntry)
                    .where(not_(KnowledgeEntry.id.in_(synced_subq)))
                    .order_by(KnowledgeEntry.created_at.desc())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                entries = result.scalars().all()

                for entry in entries:
                    ok = await self.upsert_knowledge(
                        entry_id=entry.id,
                        content=f"{entry.title}\n\n{entry.content or ''}",
                        metadata={
                            "source": entry.source or "",
                            "category": entry.category or "general",
                            "quality_score": float(entry.quality_score or 0.5),
                        },
                    )
                    if ok:
                        session.add(VectorSyncLog(
                            entry_type="knowledge",
                            entry_id=entry.id,
                        ))
                        synced += 1

                if synced:
                    await session.commit()

        except Exception as e:
            logger.warning(f"[VectorStore] sync_from_mysql 失败: {e}")

        logger.info(f"[VectorStore] 冷启动同步完成 | synced={synced}")
        return synced


# ── 全局单例 ──────────────────────────────────────────────────────────
_store: Optional[VectorStore] = None


def get_vector_store() -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore()
    return _store
