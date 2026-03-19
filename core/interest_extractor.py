"""
用户兴趣自动提取器
每次任务完成后，用 FAST_MODEL 轻量 prompt 提取用户研究领域和关键词，持久化到 MySQL
"""
import json
import logging
from core.logging_config import get_logger

import openai
from config.settings import settings

logger = get_logger(__name__)


async def extract_and_update_interests(user_id: str, task_text: str, result_text: str) -> None:
    """从任务文本和结果中提取用户兴趣并写入数据库"""
    if not task_text or not settings.OPENAI_API_KEY:
        return

    try:
        client = openai.AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            **({"base_url": settings.OPENROUTER_BASE_URL} if settings.OPENROUTER_BASE_URL else {}),
        )

        prompt = f"""分析以下用户提问和系统回复，提取用户的研究兴趣。

用户提问：{task_text[:500]}

系统回复摘要：{result_text[:800]}

请以 JSON 格式返回（不要返回其他内容）：
{{"domain": "研究领域（如：AI、生物信息学、数学、计算机科学等）", "keywords": ["关键词1", "关键词2", "关键词3"]}}

注意：
- domain 用简洁的中文领域名
- keywords 提取 3-5 个具体的研究关键词
- 只返回 JSON，不要解释"""

        resp = await client.chat.completions.create(
            model=settings.FAST_MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = resp.choices[0].message.content.strip()
        # 提取 JSON（兼容 markdown code block、前后文字包裹、尾随逗号等非标准格式）
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        # 提取第一个 { ... } 块，兼容 LLM 在 JSON 前后输出多余文字
        import re as _re
        m = _re.search(r'\{[^{}]+\}', raw, _re.DOTALL)
        if m:
            raw = m.group(0)
        # 去除尾随逗号（常见 LLM 输出问题）
        raw = _re.sub(r',\s*([}\]])', r'\1', raw)
        data = json.loads(raw)

        domain = data.get("domain", "").strip()
        keywords = data.get("keywords", [])
        if not domain or not keywords:
            return

        from core.database import upsert_interest_profile
        await upsert_interest_profile(user_id, domain, keywords)
        logger.info(f"[InterestExtractor] Updated profile for {user_id[:12]}…: domain={domain}, keywords={keywords}")

        # 同步更新 ChromaDB 用户兴趣向量（供 Evolution Loop 向量匹配使用）
        try:
            from core.vector_store import get_vector_store
            vs = get_vector_store()
            if vs.is_ready():
                interest_text = f"{domain}: {', '.join(keywords)}\n{task_text[:200]}"
                await vs.upsert_user_interest(user_id, interest_text)
                logger.debug(f"[InterestExtractor] 兴趣向量已更新 | uid={user_id[:12]}")
        except Exception as ve:
            logger.debug(f"[InterestExtractor] 兴趣向量更新失败（忽略）: {ve}")

    except Exception as e:
        logger.warning(f"[InterestExtractor] Failed to extract interests: {e}")
