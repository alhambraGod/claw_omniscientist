"""
OpenClaw Base Agent - 所有智能体的基类（OpenAI 驱动）
"""
import uuid
import json
import logging
from datetime import datetime
from config.settings import now
from typing import Any, Optional
import openai
from config.settings import settings
from skills.tools import SKILL_REGISTRY, execute_skill, get_openai_tools
from core.logging_config import get_logger

logger = get_logger(__name__)


class BaseAgent:
    """OpenClaw 智能体基类"""

    def __init__(
        self,
        agent_id: str,
        name: str,
        role: str,
        system_prompt: str,
        model: str = None,
        tools: list[str] = None,
    ):
        self.agent_id = agent_id
        self.name = name
        self.role = role
        self.system_prompt = system_prompt
        self.model = model or settings.DEFAULT_MODEL

        base_tools = tools or list(SKILL_REGISTRY.keys())
        if settings.ALLOWED_SKILLS:
            allowed_set = set(settings.ALLOWED_SKILLS.split(","))
            self.allowed_tools = [t for t in base_tools if t in allowed_set]
        else:
            self.allowed_tools = base_tools
        kwargs = {"api_key": settings.OPENAI_API_KEY}
        if settings.OPENROUTER_BASE_URL:
            kwargs["base_url"] = settings.OPENROUTER_BASE_URL
        self.client = openai.AsyncOpenAI(**kwargs) if settings.OPENAI_API_KEY else None
        self._conversation_history: list[dict] = []

    def _get_tools(self) -> list[dict]:
        all_tools = get_openai_tools()
        return [t for t in all_tools if t["function"]["name"] in self.allowed_tools]

    async def run(self, task: str, context: dict = None, stream: bool = False) -> dict:
        """执行任务，支持 tool_calls 循环"""
        if not self.client:
            return {
                "agent_id": self.agent_id,
                "agent_name": self.name,
                "status": "error",
                "error": "OPENAI_API_KEY 未配置，请在 .env 文件中设置",
                "task": task,
            }

        # ── 日志：任务开始 ─────────────────────────────────────────────────────
        logger.info(
            f"[{self.name}] ▶ 任务开始 | model={self.model} | tools={len(self.allowed_tools)}"
        )
        logger.debug(
            f"[{self.name}] 用户输入 ↓\n{'─'*60}\n{task}\n{'─'*60}"
        )

        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(self._conversation_history)

        user_message = task
        if context:
            ctx_str = json.dumps(context, ensure_ascii=False, indent=2)
            user_message = f"{task}\n\n[上下文]\n{ctx_str}"

        messages.append({"role": "user", "content": user_message})

        tools = self._get_tools()
        iterations = 0
        max_iterations = 10
        final_text = ""

        while iterations < max_iterations:
            iterations += 1
            try:
                kwargs = dict(
                    model=self.model,
                    max_tokens=4096,
                    messages=messages,
                )
                if tools:
                    kwargs["tools"] = tools

                # ── 日志：发送模型请求 ──────────────────────────────────────────
                logger.debug(
                    f"[{self.name}] → 请求模型 | iter={iterations}/{max_iterations}"
                    f" | messages={len(messages)} | model={self.model}"
                )

                response = await self.client.chat.completions.create(**kwargs)

            except openai.APIError as e:
                logger.error(f"[{self.name}] ✗ API 错误: {e}")
                return {"agent_id": self.agent_id, "status": "error", "error": str(e), "task": task}

            msg = response.choices[0].message
            finish_reason = response.choices[0].finish_reason

            # ── 日志：模型响应 ──────────────────────────────────────────────────
            usage = response.usage
            usage_str = (
                f"in={usage.prompt_tokens} out={usage.completion_tokens}"
                if usage else "usage=unknown"
            )
            logger.debug(
                f"[{self.name}] ← 模型响应 | finish={finish_reason} | {usage_str}"
                f" | tool_calls={len(msg.tool_calls) if msg.tool_calls else 0}"
            )
            if msg.content:
                logger.debug(
                    f"[{self.name}] 模型文本输出 ↓\n{msg.content[:500]}"
                    + ("…（截断）" if len(msg.content or "") > 500 else "")
                )

            assistant_entry: dict = {"role": "assistant", "content": msg.content or ""}
            if msg.tool_calls:
                assistant_entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            messages.append(assistant_entry)

            if finish_reason == "stop":
                final_text = msg.content or ""
                break

            if finish_reason == "tool_calls" and msg.tool_calls:
                if msg.content:
                    final_text += msg.content

                for tc in msg.tool_calls:
                    tool_name = tc.function.name
                    try:
                        tool_args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        tool_args = {}

                    # ── 日志：工具调用 ──────────────────────────────────────────
                    logger.info(
                        f"[{self.name}] ⚙ 工具调用: {tool_name}"
                        f" | 参数: {json.dumps(tool_args, ensure_ascii=False)[:120]}"
                    )
                    result = await execute_skill(tool_name, tool_args)

                    # ── 日志：工具返回 ──────────────────────────────────────────
                    result_preview = json.dumps(result, ensure_ascii=False)[:300]
                    logger.debug(
                        f"[{self.name}] ✓ 工具返回: {tool_name}"
                        f" | {result_preview}"
                        + ("…（截断）" if len(json.dumps(result, ensure_ascii=False)) > 300 else "")
                    )

                    if isinstance(result, dict) and result.get("action", "").startswith("llm_"):
                        prompt = result.get("prompt", str(result))
                        result = {"result": f"[需要 LLM 处理]\n{prompt}"}

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, ensure_ascii=False)[:8000],
                    })
            else:
                final_text = msg.content or ""
                break

        # ── 日志：任务完成 ──────────────────────────────────────────────────────
        logger.info(
            f"[{self.name}] ✔ 任务完成 | iterations={iterations}"
            f" | output_len={len(final_text)}"
        )
        logger.debug(
            f"[{self.name}] 最终输出 ↓\n{'─'*60}\n{final_text[:800]}"
            + ("…（截断）" if len(final_text) > 800 else "")
            + f"\n{'─'*60}"
        )

        return {
            "agent_id": self.agent_id,
            "agent_name": self.name,
            "role": self.role,
            "status": "success",
            "result": final_text,
            "task": task,
            "iterations": iterations,
            "timestamp": now().isoformat(),
        }

    def reset_conversation(self):
        self._conversation_history = []

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "role": self.role,
            "model": self.model,
            "allowed_tools": self.allowed_tools,
        }
