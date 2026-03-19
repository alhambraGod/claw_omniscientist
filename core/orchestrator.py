"""
OpenClaw 任务编排器 — 重构版
两种执行模式：
1. 直调模式（direct）：LeadResearcher 加载用户画像后执行（CLI/API 直连）
2. 队列模式（queue）：任务推入 Redis，Worker Pool 异步处理（Feishu/高并发）
"""
import asyncio
import json
import uuid
import logging
from config.settings import now, settings
from core.registry import AgentRegistry
from core.logging_config import get_logger

logger = get_logger(__name__)


class Orchestrator:
    """任务编排器 - 支持直调和队列两种模式"""

    def __init__(self, registry: AgentRegistry):
        self.registry = registry
        self._task_history: list[dict] = []

    async def execute(self, task: str, user_id: str = "anonymous", context: dict = None) -> dict:
        """
        直调模式：LeadResearcher 加载用户画像后立即执行
        用于 CLI、API 直连、Web 界面场景
        """
        task_id = str(uuid.uuid4())

        # ── "hi omni" 受信任模式检测（与 WorkerPool 保持一致） ──────────────────
        PASSPHRASE_PREFIX = "hi omni"
        trusted = task.lower().lstrip().startswith(PASSPHRASE_PREFIX)
        if trusted:
            stripped = task.lstrip()
            task = stripped[len(PASSPHRASE_PREFIX):].lstrip(" ,，:：\n")
            logger.info(
                f"[Orchestrator] 🔓 受信任模式（hi omni）| task_id={task_id[:12]}"
                f" | 跳过 Guardian | task_len={len(task)}"
            )

        # ── 日志：编排开始 ──────────────────────────────────────────────────────
        logger.info(
            f"[Orchestrator] ▶ 任务编排（直调）| task_id={task_id[:12]}"
            f" | uid={user_id} | trusted={trusted}"
        )
        logger.debug(
            f"[Orchestrator] 任务内容 ↓\n{'─'*60}\n{task}\n{'─'*60}"
        )

        # 将 trusted 标志传入 context
        context = dict(context or {})
        context["trusted"] = trusted

        try:
            # Step 1: Guardian 输入审核（trusted 模式跳过）
            guardian = self.registry.get_guardian() if not trusted else None
            if guardian:
                logger.debug(f"[Orchestrator] Guardian 输入审核 | task_id={task_id[:12]}")
                verdict = await guardian.review_input(task, user_id)
                verdict_val = verdict.get("verdict", "approved")
                logger.debug(
                    f"[Orchestrator] Guardian 审核: {verdict_val}"
                    f" | risk={verdict.get('risk_score', 0)}"
                )
                if verdict_val == "rejected":
                    logger.warning(
                        f"[Orchestrator] ✗ 任务被拒绝 | task_id={task_id[:12]}"
                        f" | issues={verdict.get('issues', [])}"
                    )
                    return self._make_rejected(task_id, verdict)
                if verdict_val == "escalated":
                    return self._make_escalated(task_id, verdict)

            # Step 2: 通过 Worker 执行（内嵌了 LeadResearcher 画像驱动能力）
            worker = self.registry.get_any_worker()
            if not worker:
                logger.error(f"[Orchestrator] 无可用 Worker | task_id={task_id[:12]}")
                return {"task_id": task_id, "status": "error", "error": "无可用 Worker"}
            logger.info(
                f"[Orchestrator] → {worker.agent_id} | task_id={task_id[:12]}"
                f" | uid={user_id}"
            )
            result = await asyncio.wait_for(
                worker.run_with_user(task, user_id=user_id, context=context or {}),
                timeout=settings.TASK_TIMEOUT,
            )

            # Step 3: Guardian 输出审核（trusted 模式跳过）
            if guardian and result.get("result") and not trusted:
                out_verdict = await guardian.review_output(result["result"], "general")
                result["guardian_verdict"] = out_verdict.get("verdict")
                result["risk_score"] = out_verdict.get("risk_score", 0.0)
                logger.debug(
                    f"[Orchestrator] Guardian 输出审核: {out_verdict.get('verdict')}"
                )

            # 后台：追问建议
            if result.get("status") == "success" and result.get("result"):
                try:
                    from core.follow_up import generate_follow_ups
                    suggestions = await generate_follow_ups(task, result["result"])
                    if suggestions:
                        result["follow_up_suggestions"] = suggestions
                        logger.debug(
                            f"[Orchestrator] 追问建议: {suggestions}"
                        )
                except Exception as e:
                    logger.debug(f"[Orchestrator] 追问建议生成失败: {e}")

            # 后台：Wellspring 沉淀
            wellspring = self.registry.get_wellspring()
            if wellspring and result.get("status") == "success":
                asyncio.create_task(wellspring.ingest_task_result(result))

            # 后台：兴趣画像提取
            if user_id and user_id != "anonymous" and result.get("status") == "success":
                from core.interest_extractor import extract_and_update_interests
                asyncio.create_task(
                    extract_and_update_interests(user_id, task, result.get("result", ""))
                )

            result["task_id"] = task_id
            self._task_history.append(result)

            # ── 日志：编排完成 ──────────────────────────────────────────────────
            logger.info(
                f"[Orchestrator] ✔ 编排完成（直调）| task_id={task_id[:12]}"
                f" | status={result.get('status')}"
                f" | agent={result.get('agent_name', '?')}"
                f" | iterations={result.get('iterations', '?')}"
            )
            return result

        except asyncio.TimeoutError:
            logger.error(
                f"[Orchestrator] ✗ 超时 | task_id={task_id[:12]}"
                f" | timeout={settings.TASK_TIMEOUT}s"
            )
            err = {
                "task_id": task_id, "status": "error",
                "error": f"任务超时（>{settings.TASK_TIMEOUT}s）", "task": task,
            }
            self._task_history.append(err)
            return err
        except Exception as e:
            logger.error(
                f"[Orchestrator] ✗ 异常 | task_id={task_id[:12]}: {e}",
                exc_info=True,
            )
            err = {"task_id": task_id, "status": "error", "error": str(e), "task": task}
            self._task_history.append(err)
            return err

    async def enqueue(
        self,
        task: str,
        user_id: str = "anonymous",
        reply_info: dict = None,
    ) -> str:
        """
        队列模式：将任务推入 Redis，返回 task_id
        用于 Feishu Bot、高并发异步场景
        """
        from core.cache import push_task
        task_id = str(uuid.uuid4())
        payload = {
            "task_id": task_id,
            "task": task,
            "user_id": user_id,
            "reply_info": reply_info or {"channel": "api"},
            "created_at": now().isoformat(),
        }
        await push_task(payload)
        logger.info(
            f"[Orchestrator] ▶ 任务入队 | task_id={task_id[:12]}"
            f" | uid={user_id}"
            f" | channel={reply_info.get('channel', 'api') if reply_info else 'api'}"
        )
        logger.debug(
            f"[Orchestrator] 入队内容: task={task[:100]}"
            + ("…" if len(task) > 100 else "")
        )
        return task_id

    def get_history(self, limit: int = 20) -> list[dict]:
        return self._task_history[-limit:]

    def _make_rejected(self, task_id: str, verdict: dict) -> dict:
        return {
            "task_id": task_id,
            "status": "rejected",
            "issues": verdict.get("issues", []),
            "recommendation": verdict.get("recommendation", ""),
            "risk_level": verdict.get("risk_level", "high"),
        }

    def _make_escalated(self, task_id: str, verdict: dict) -> dict:
        return {
            "task_id": task_id,
            "status": "escalated",
            "issues": verdict.get("issues", []),
            "recommendation": verdict.get("recommendation", ""),
        }
