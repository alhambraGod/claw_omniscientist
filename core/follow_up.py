"""
智能追问建议生成器
每次任务完成后，用 FAST_MODEL 快速生成 2-3 个后续研究方向建议
"""
import json
import logging
from core.logging_config import get_logger

import openai
from config.settings import settings

logger = get_logger(__name__)


async def generate_follow_ups(task_text: str, result_text: str) -> list[str]:
    """根据任务和结果生成后续研究建议"""
    if not settings.OPENAI_API_KEY:
        return []

    try:
        client = openai.AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            **({"base_url": settings.OPENROUTER_BASE_URL} if settings.OPENROUTER_BASE_URL else {}),
        )

        prompt = f"""基于用户的科研问题和系统回复，生成 3 个高质量的后续追问建议。

用户问题：{task_text[:300]}

回复摘要：{result_text[:600]}

要求：
- 每个建议是一句简短的问题（15-30字）
- 从不同角度深入（如：方法论、应用场景、对比分析）
- 只返回 JSON 数组，不要解释

示例格式：["这个方法在小样本场景下效果如何？", "能否与 XXX 方法做个对比？", "实际应用中需要注意什么？"]"""

        resp = await client.chat.completions.create(
            model=settings.FAST_MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = resp.choices[0].message.content.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        suggestions = json.loads(raw)

        if isinstance(suggestions, list) and len(suggestions) > 0:
            return suggestions[:3]
        return []

    except Exception as e:
        logger.debug(f"[FollowUp] Generation failed: {e}")
        return []
