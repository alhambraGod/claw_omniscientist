"""
Worker Pool — 并发任务处理核心（v2.0 架构）

架构设计：
┌─────────────────────────────────────────────────────────┐
│  用户请求 → push_task() → Redis Stream（共享入队）           │
│                              │                           │
│           Maintainer 调度器（单点读取，负载均衡路由）         │
│           ├── 查询每个 Worker 当前负载                      │
│           ├── 选择最空闲的 Worker                           │
│           └── push_task_to_worker(target_id, task)         │
│                              │                           │
│           Worker-N（私有队列 + 并发 Semaphore）              │
│           ├── 从私有 Redis List 读取任务                     │
│           ├── Semaphore(WORKER_CONCURRENCY=5) 控制并发      │
│           └── asyncio.create_task() 非阻塞并发执行           │
└─────────────────────────────────────────────────────────┘

关键设计决策：
1. Maintainer 是唯一从共享 Stream 读消息的入口 → 消除重复执行
2. Worker 私有队列 → 每个任务只在一个 Worker 中执行
3. Semaphore 控制并发 → 每个 Worker 最多同时处理 N 个任务（非阻塞）
4. 在进程内 _active_task_ids 兜底去重 → 双重保障
5. STREAM_PENDING_TIMEOUT_MS 设为 1800000ms(30min) >> TASK_TIMEOUT(600s)
   → 避免正在执行中的任务被误判为"崩溃任务"被重复认领
"""
import asyncio
import logging
import time
import uuid
from typing import Optional

from config.settings import settings, now
from core import cache as cache_store
from core import notifier
from core.logging_config import get_logger

logger = get_logger(__name__)


class WorkerPool:
    """
    管理 N 个 WorkerClawer 并发协程。

    包含两类协程：
    - Maintainer 调度器（1个）：从共享 Redis Stream 读任务，路由到最空闲 Worker
    - Worker 循环（N个）：从各自私有队列读任务，用 Semaphore 控制并发
    """

    def __init__(self):
        self._workers: list = []
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._guardian = None
        self._wellspring = None
        # 进程内任务去重集合（防止极端情况下同一 task_id 被重复执行）
        self._active_task_ids: set[str] = set()
        self._active_task_ids_lock: Optional[asyncio.Lock] = None

    def setup(self, workers: list, guardian=None, wellspring=None):
        self._workers = workers
        self._guardian = guardian
        self._wellspring = wellspring
        concurrency = getattr(settings, "WORKER_CONCURRENCY", 5)
        logger.info(
            f"[WorkerPool] 初始化完成 | workers={len(workers)} | concurrency_per_worker={concurrency}"
            f" | total_slots={len(workers) * concurrency}"
            f" | guardian={'✓' if guardian else '✗'}"
            f" | wellspring={'✓' if wellspring else '✗'}"
        )

    async def start(self):
        if not self._workers:
            logger.warning("[WorkerPool] 无可用 Worker，跳过启动")
            return
        self._running = True
        self._active_task_ids_lock = asyncio.Lock()

        # 启动 Maintainer 调度器（单一协程，是唯一从共享 Stream 取消息的入口）
        t_maintainer = asyncio.create_task(
            self._maintainer_dispatch_loop(),
            name="maintainer-dispatcher",
        )
        self._tasks.append(t_maintainer)

        # 启动各 Worker 循环（从私有队列读，并发执行）
        for worker in self._workers:
            t = asyncio.create_task(
                self._worker_loop(worker),
                name=f"worker-loop-{worker.agent_id}",
            )
            self._tasks.append(t)

        logger.info(
            f"[WorkerPool] 已启动 Maintainer + {len(self._workers)} 个 Worker 协程"
        )

    async def stop(self):
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await cache_store.close_redis()
        logger.info("[WorkerPool] 所有协程已停止")

    # ── Maintainer 调度器 ──────────────────────────────────────────────────

    async def _maintainer_dispatch_loop(self):
        """
        Maintainer 职责：
        1. 从共享 Redis Stream（Consumer Group）读取新任务
        2. 根据 Worker 当前私有队列长度，选择最空闲的 Worker
        3. 将任务 push 到该 Worker 的私有 Redis List
        4. 立即 XACK 共享 Stream（消除 Pending 状态，防止重复认领）

        这是整个系统中唯一读取共享 Stream 的入口，从根本上消除重复执行。
        """
        logger.info("[Maintainer] 调度器启动，监听任务流...")
        worker_ids = [w.agent_id for w in self._workers]

        while self._running:
            try:
                # 从共享 Stream 取一条新任务（以 "maintainer" 为 Consumer ID）
                popped = await cache_store.pop_task(
                    worker_id="maintainer", timeout=5
                )
                if not popped:
                    continue

                msg_id, task_data = popped
                task_id = task_data.get("task_id", "?")

                # 进程内去重：极端情况下防止 Maintainer 重启后重复分发
                async with self._active_task_ids_lock:
                    if task_id in self._active_task_ids:
                        logger.warning(
                            f"[Maintainer] ⚠️ 任务已在处理中，跳过重复: {task_id[:12]}"
                        )
                        await cache_store.ack_task(msg_id)
                        continue
                    self._active_task_ids.add(task_id)

                # 负载感知路由：选择私有队列最短的 Worker
                loads = await cache_store.get_all_worker_queue_lengths(worker_ids)
                target_id = min(worker_ids, key=lambda wid: loads.get(wid, 0))

                channel = (task_data.get("reply_info") or {}).get("channel") or task_data.get("channel", "api")
                logger.info(
                    f"[Maintainer] ✓ 路由任务 | task_id={task_id[:12]}"
                    f" → worker={target_id}"
                    f" | loads={loads}"
                    f" | channel={channel}"
                )

                # 推入 Worker 私有队列（Worker 将从此 List 取任务）
                await cache_store.push_task_to_worker(target_id, task_data)

                # 立即 ACK 共享 Stream（任务已安全交给 Worker，不再 Pending）
                await cache_store.ack_task(msg_id)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Maintainer] 调度异常: {e}", exc_info=True)
                await asyncio.sleep(1)

        logger.info("[Maintainer] 调度器已退出")

    # ── Worker 循环（并发执行）────────────────────────────────────────────

    async def _worker_loop(self, worker):
        """
        Worker 职责：
        1. 从自己的私有 Redis List 阻塞等待取任务（Maintainer 分配过来的）
        2. 通过 Semaphore 控制并发上限（WORKER_CONCURRENCY，默认 5）
        3. 用 asyncio.create_task() 非阻塞地启动任务执行
        4. 不等待任务完成就继续取下一个任务 → 真正的并发处理
        """
        concurrency = getattr(settings, "WORKER_CONCURRENCY", 5)
        semaphore = asyncio.Semaphore(concurrency)
        logger.info(
            f"[Worker] {worker.agent_id} 启动 | 并发槽位={concurrency}"
        )

        while self._running:
            try:
                await cache_store.set_worker_heartbeat(
                    worker.agent_id,
                    f"waiting(slots={concurrency - semaphore._value}busy/{concurrency})",
                )

                # 等待 Semaphore 槽位（如果已达并发上限则阻塞，直到有任务完成）
                await semaphore.acquire()

                # 从私有队列阻塞取任务（timeout=5s，超时后重新检查 _running）
                task_data = await cache_store.pop_task_from_worker(
                    worker.agent_id, timeout=5
                )
                if task_data is None:
                    # 超时无任务，释放 semaphore 继续等待
                    semaphore.release()
                    continue

                task_id = task_data.get("task_id", str(uuid.uuid4()))

                # 启动并发任务（非阻塞，立即返回继续取下一个任务）
                asyncio.create_task(
                    self._run_task_with_cleanup(semaphore, worker, task_data),
                    name=f"task-{task_id[:8]}",
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    f"[Worker] {worker.agent_id} 循环异常: {e}", exc_info=True
                )
                await asyncio.sleep(1)

        logger.info(f"[Worker] {worker.agent_id} 已退出")

    async def _run_task_with_cleanup(
        self, semaphore: asyncio.Semaphore, worker, task_data: dict
    ):
        """包装任务执行 + Semaphore 释放 + 去重集合清理"""
        task_id = task_data.get("task_id", "?")
        try:
            await self._process_task(worker, task_data)
        except Exception as e:
            logger.error(
                f"[Worker] _run_task_with_cleanup 未捕获异常: {e}", exc_info=True
            )
        finally:
            semaphore.release()
            async with self._active_task_ids_lock:
                self._active_task_ids.discard(task_id)

    # ── 核心任务处理逻辑 ──────────────────────────────────────────────────

    async def _process_task(self, worker, task_data: dict):
        """
        完整的任务处理流程：
        解包任务数据 → 持久化记录 → 执行（Guardian + LeadResearcher/Worker）→ 通知用户
        """
        task_id = task_data.get("task_id", str(uuid.uuid4()))
        task_text = task_data.get("task", "")
        user_id = task_data.get("user_id", "anonymous")
        reply_info = task_data.get("reply_info")
        channel = reply_info.get("channel", "api") if reply_info else task_data.get("channel", "api")
        enqueued_at = task_data.get("created_at", "?")

        # ── 记录任务开始执行 ────────────────────────────────────────────────
        try:
            await cache_store.set_task_running(task_id, worker.agent_id, {
                "user_id": user_id,
                "channel": channel,
                "reply_info": reply_info,
                "task_text": task_text[:200],
                "enqueued_at": enqueued_at,
            })
        except Exception:
            pass

        logger.info(
            f"[Worker] ▶ 开始执行 | worker={worker.agent_id}"
            f" | task_id={task_id[:12]} | channel={channel}"
            f" | uid={user_id} | enqueued_at={enqueued_at}"
        )
        logger.debug(
            f"[Worker] 任务内容 ↓\n{'─' * 60}\n{task_text}\n{'─' * 60}"
        )

        await cache_store.set_worker_heartbeat(worker.agent_id, f"busy:{task_id[:8]}")

        if channel in ("feishu", "dingtalk"):
            logger.info(
                f"[{channel.upper()}↓] 用户提问"
                f" | uid={user_id} | task_id={task_id[:12]}"
                f" | len={len(task_text)}"
            )

        # ── 邮箱拦截：任务文本是邮箱地址 + Redis 有待发全文 → 直接发邮件 ──
        if await _maybe_send_pending_email(task_text.strip(), user_id, reply_info):
            await cache_store.clear_task_running(task_id)
            await cache_store.set_worker_heartbeat(worker.agent_id, "idle")
            return

        # ── 持久化初始任务记录 ──────────────────────────────────────────────
        try:
            from core.database import save_task_record, log_task_progress
            asyncio.create_task(save_task_record(
                task_id=task_id,
                title=task_text[:256],
                user_id=user_id,
                channel=channel,
                reply_info=reply_info,
                input_data={"task": task_text},
                status="pending",
                started_at=now(),
            ))
            asyncio.create_task(log_task_progress(
                task_id=task_id,
                event_type="queued",
                message=f"任务出队，分配给 Worker {worker.agent_id}",
                metadata={"worker_id": worker.agent_id, "user_id": user_id, "channel": channel, "enqueued_at": enqueued_at},
            ))
        except Exception:
            pass

        # ── 保存工作上下文到 Working Memory ───────────────────────────────
        try:
            from core.memory import get_memory_manager
            await get_memory_manager().set_working_context(
                worker.agent_id, task_id,
                {"task": task_text[:200], "user_id": user_id, "channel": channel}
            )
        except Exception:
            pass

        # ── 执行任务 ────────────────────────────────────────────────────────
        _t0 = time.monotonic()
        result = await self._execute_task(
            worker, task_text, user_id, task_id, reply_info=reply_info
        )
        _duration = time.monotonic() - _t0

        # ── 清理运行记录 ────────────────────────────────────────────────────
        try:
            await cache_store.clear_task_running(task_id)
        except Exception:
            pass

        asyncio.create_task(
            cache_store.record_task_duration(_duration),
            name=f"duration-{task_id[:8]}",
        )

        status = result.get("status", "error")
        logger.info(
            f"[Worker] ✔ 任务完成 | task_id={task_id[:12]}"
            f" | status={status} | duration={_duration:.1f}s"
            f" | agent={result.get('agent_name', '?')}"
            f" | iterations={result.get('iterations', '?')}"
        )

        # ── 更新任务记录 ────────────────────────────────────────────────────
        try:
            from core.database import save_task_record, log_task_progress
            asyncio.create_task(save_task_record(
                task_id=task_id,
                title=task_text[:256],
                user_id=user_id,
                channel=channel,
                reply_info=reply_info,
                input_data={"task": task_text},
                output_data=result,
                status=status,
                worker_id=worker.agent_id,
                error_message=result.get("error"),
                completed_at=now(),
            ))
            _evt = "completed" if status == "success" else status
            _status_label = {
                "success": "完成", "timeout": "超时终止",
                "rejected": "被Guardian拒绝", "error": "执行出错",
            }.get(status, "结束")
            asyncio.create_task(log_task_progress(
                task_id=task_id,
                event_type=_evt,
                message=f"任务{_status_label} | 用时 {_duration:.1f}s",
                metadata={
                    "duration_seconds": round(_duration, 2),
                    "agent": result.get("agent_name", ""),
                    "iterations": result.get("iterations", 0),
                },
            ))
        except Exception:
            pass

        # ── 存结果到 Redis，回调原始渠道 ────────────────────────────────────
        await cache_store.store_result(task_id, result)
        await notifier.notify(reply_info, result)

        # ── 任务超时：主动通知 ───────────────────────────────────────────────
        if status == "timeout":
            elapsed = result.get("elapsed_seconds", settings.TASK_TIMEOUT)
            timeout_msg = (
                f"你的科研任务已运行 {elapsed}s，超过系统限制 {settings.TASK_TIMEOUT}s，已自动终止。\n\n"
                f"**建议**：简化任务描述或拆分为子任务后重新提交。\n"
                f"> 任务编号：`{task_id[:16]}`"
            )
            logger.warning(
                f"[Worker] ⏱️ 任务超时通知 | task_id={task_id[:12]}"
                f" | channel={channel} | uid={user_id}"
            )
            open_id = (reply_info or {}).get("open_id") or (reply_info or {}).get("sender_id")
            if channel == "feishu" and open_id:
                asyncio.create_task(
                    notifier.send_proactive_feishu(open_id, "⏱️ 科研任务超时", timeout_msg),
                    name=f"timeout-notify-{task_id[:8]}",
                )
            asyncio.create_task(
                cache_store.push_alert(
                    "warning",
                    f"任务超时终止 | task_id={task_id[:12]} | uid={user_id} | channel={channel}",
                    source="worker_pool",
                ),
                name=f"timeout-alert-{task_id[:8]}",
            )

        # ── 飞书/钉钉回复日志 ────────────────────────────────────────────────
        if channel in ("feishu", "dingtalk") and status == "success":
            reply_text = result.get("result", "")
            logger.info(
                f"[{channel.upper()}↑] 系统回复"
                f" | uid={user_id} | task_id={task_id[:12]}"
                f" | agent={result.get('agent_name', '?')}"
                f" | reply_len={len(reply_text)}"
            )
            logger.debug(
                f"[{channel.upper()}↑] 回复内容 ↓\n{reply_text[:600]}"
                + ("…（截断）" if len(reply_text) > 600 else "")
            )

        # ── 任务指标记录 ─────────────────────────────────────────────────────
        try:
            from core.database import record_task_metrics
            asyncio.create_task(record_task_metrics(
                task_id=task_id,
                user_id=user_id,
                channel=reply_info.get("channel", "api") if reply_info else "api",
                status=status,
                duration_seconds=round(_duration, 2),
                iterations=result.get("iterations", 0),
            ))
        except Exception:
            pass

        # ── 长论文自动发送邮件（任务完成后）─────────────────────────────────
        if status == "success":
            paper_text = result.get("result", "")
            if len(paper_text) > 3000 and user_id and user_id != "anonymous":
                asyncio.create_task(
                    _auto_email_paper(user_id, paper_text, task_id, reply_info),
                    name=f"auto-email-{task_id[:8]}",
                )

        # ── Wellspring 沉淀 ──────────────────────────────────────────────────
        if self._wellspring and status == "success":
            asyncio.create_task(
                self._wellspring.ingest_task_result(result),
                name=f"wellspring-{task_id[:8]}",
            )

        # ── 自动提取用户兴趣画像 ─────────────────────────────────────────────
        if user_id and user_id != "anonymous" and status == "success":
            from core.interest_extractor import extract_and_update_interests
            asyncio.create_task(
                extract_and_update_interests(user_id, task_text, result.get("result", "")),
                name=f"interest-{task_id[:8]}",
            )

    async def _execute_task(
        self,
        worker,
        task_text: str,
        user_id: str,
        task_id: str,
        reply_info: dict = None,
    ) -> dict:
        """
        执行策略：优先 LeadResearcher（画像驱动），降级到 WorkerClawer
        """
        # ── 暗语检测：hi omni 前缀触发受信任模式，跳过 Guardian ─────────────
        PASSPHRASE_PREFIX = "hi omni"
        trusted = task_text.lower().lstrip().startswith(PASSPHRASE_PREFIX)
        if trusted:
            stripped = task_text.lstrip()
            task_text = stripped[len(PASSPHRASE_PREFIX):].lstrip(" ,，:：\n")
            logger.info(
                f"[Worker] 🔓 受信任模式（hi omni）| task_id={task_id[:12]}"
                f" | 跳过 Guardian | task_len={len(task_text)}"
            )

        # ── Step 1: Guardian 输入审核（trusted 模式跳过）──────────────────────
        if self._guardian and not trusted:
            try:
                logger.debug(f"[Worker] Guardian 输入审核 | task_id={task_id[:12]}")
                from core.database import log_task_progress
                asyncio.create_task(log_task_progress(task_id, "guardian_checking", "Guardian 安全审核中…"))
                verdict = await self._guardian.review_input(task_text, user_id)
                verdict_val = verdict.get("verdict", "approved")
                logger.debug(
                    f"[Worker] Guardian 审核结果: {verdict_val}"
                    f" | risk={verdict.get('risk_score', 0)}"
                    f" | issues={verdict.get('issues', [])}"
                )
                if verdict_val == "rejected":
                    logger.warning(
                        f"[Worker] ✗ 任务被 Guardian 拒绝 | task_id={task_id[:12]}"
                        f" | issues={verdict.get('issues', [])}"
                    )
                    asyncio.create_task(log_task_progress(
                        task_id, "guardian_reject",
                        f"Guardian 拒绝：{', '.join(verdict.get('issues', []))}",
                        {"risk_score": verdict.get("risk_score", 0), "issues": verdict.get("issues", [])},
                    ))
                    return {
                        "task_id": task_id, "status": "rejected",
                        "issues": verdict.get("issues", []),
                        "recommendation": verdict.get("recommendation", ""),
                    }
                if verdict_val == "escalated":
                    asyncio.create_task(log_task_progress(task_id, "guardian_escalate", "Guardian 上报：需人工审核"))
                    return {
                        "task_id": task_id, "status": "escalated",
                        "recommendation": verdict.get("recommendation", ""),
                    }
                asyncio.create_task(log_task_progress(
                    task_id, "guardian_pass",
                    f"Guardian 通过 | 风险评分 {verdict.get('risk_score', 0)}",
                    {"risk_score": verdict.get("risk_score", 0)},
                ))
            except Exception as e:
                logger.warning(f"[Worker] Guardian 审核失败（放行）: {e}")
        elif trusted:
            from core.database import log_task_progress
            asyncio.create_task(log_task_progress(task_id, "trusted_mode", "hi omni 受信任模式，跳过 Guardian 审核"))

        context = {"user_id": user_id, "task_id": task_id, "trusted": trusted}
        if reply_info:
            context["reply_info"] = reply_info

        # ── 长任务心跳：每 60s 向飞书用户发送进度提示 ────────────────────────
        channel = reply_info.get("channel", "api") if reply_info else "api"
        heartbeat_task = None
        if channel == "feishu":
            open_id = (reply_info or {}).get("open_id") or (reply_info or {}).get("sender_id")
            if open_id:
                async def _heartbeat(oid: str, tid: str):
                    interval = 60
                    elapsed = 0
                    while True:
                        await asyncio.sleep(interval)
                        elapsed += interval
                        try:
                            await notifier.send_proactive_feishu(
                                oid, "⏳ 科研进行中",
                                f"任务仍在深度处理中，已用时约 {elapsed}s，请稍候…\n`task_id: {tid}`"
                            )
                        except Exception:
                            pass

                heartbeat_task = asyncio.create_task(
                    _heartbeat(open_id, task_id),
                    name=f"heartbeat-{task_id[:8]}",
                )

        try:
            # Worker 通过 run_with_user() 执行任务：
            #   - 若已注入 LeadResearcher（由 Registry 完成），走画像驱动的智能路径
            #   - 否则降级为 BaseAgent.run()（无画像简单路径）
            logger.debug(
                f"[Worker] {worker.agent_id} → run_with_user | task_id={task_id[:12]}"
            )
            from core.database import log_task_progress
            asyncio.create_task(log_task_progress(
                task_id, "executing",
                f"Worker {worker.agent_id} 开始深度分析",
                {"executor": worker.agent_id, "worker_id": worker.agent_id},
            ))
            result = await asyncio.wait_for(
                worker.run_with_user(task_text, user_id=user_id, context=context),
                timeout=settings.TASK_TIMEOUT,
            )
        except asyncio.TimeoutError:
            if heartbeat_task:
                heartbeat_task.cancel()
            elapsed_sec = int(settings.TASK_TIMEOUT)
            logger.error(
                f"[Worker] ⏱️ 任务超时，已自动终止 | task_id={task_id[:12]}"
                f" | timeout={settings.TASK_TIMEOUT}s"
            )
            return {
                "task_id": task_id,
                "status": "timeout",
                "error": (
                    f"任务执行超时（运行 {elapsed_sec}s，超过系统限制 {settings.TASK_TIMEOUT}s）。"
                    "请尝试简化任务描述后重新提交。"
                ),
                "elapsed_seconds": elapsed_sec,
            }
        except Exception as e:
            if heartbeat_task:
                heartbeat_task.cancel()
            logger.error(
                f"[Worker] ✗ 执行异常 | task_id={task_id[:12]}: {e}",
                exc_info=True
            )
            return {"task_id": task_id, "status": "error", "error": str(e)}
        finally:
            if heartbeat_task:
                heartbeat_task.cancel()

        result["task_id"] = task_id

        # ── Step 3: Guardian 输出审核（trusted 模式跳过）──────────────────────
        if self._guardian and result.get("result") and not trusted:
            try:
                logger.debug(f"[Worker] Guardian 输出审核 | task_id={task_id[:12]}")
                out_verdict = await self._guardian.review_output(result["result"], "general")
                result["guardian_verdict"] = out_verdict.get("verdict")
                result["risk_score"] = out_verdict.get("risk_score", 0.0)
                logger.debug(
                    f"[Worker] Guardian 输出审核: {out_verdict.get('verdict')}"
                    f" | risk={out_verdict.get('risk_score', 0)}"
                )
            except Exception as e:
                logger.warning(f"[Worker] Guardian 输出审核失败: {e}")

        # ── Step 4: 智能追问建议（后台异步，不阻塞主流程）──────────────────
        if result.get("status") == "success" and result.get("result"):
            async def _bg_follow_ups():
                try:
                    from core.follow_up import generate_follow_ups
                    suggestions = await generate_follow_ups(task_text, result["result"])
                    if suggestions:
                        result["follow_up_suggestions"] = suggestions
                        logger.debug(f"[Worker] 追问建议生成 | {suggestions}")
                        ch = reply_info.get("channel") if reply_info else None
                        if ch == "feishu":
                            oid = (reply_info or {}).get("open_id") or (reply_info or {}).get("sender_id")
                            if oid:
                                hints = "\n".join(f"• {s}" for s in suggestions[:3])
                                await notifier.send_proactive_feishu(oid, "💡 延伸思考", hints)
                except Exception as e:
                    logger.debug(f"[Worker] 追问建议生成失败: {e}")

            asyncio.create_task(_bg_follow_ups(), name=f"follow-up-{task_id[:8]}")

        return result

    def status(self) -> dict:
        concurrency = getattr(settings, "WORKER_CONCURRENCY", 5)
        return {
            "worker_count": len(self._workers),
            "concurrency_per_worker": concurrency,
            "total_slots": len(self._workers) * concurrency,
            "active_tasks": len(self._active_task_ids),
            "running": self._running,
            "workers": [w.agent_id for w in self._workers],
        }


_pool: Optional[WorkerPool] = None


def get_worker_pool() -> WorkerPool:
    global _pool
    if _pool is None:
        _pool = WorkerPool()
    return _pool


# ── 邮箱拦截：在任务进入 LLM 流程前，检查是否是"邮箱回复"行为 ────────────────

import re as _re

_EMAIL_RE = _re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


async def _maybe_send_pending_email(task_text: str, user_id: str, reply_info: dict | None) -> bool:
    """
    若任务文本是合法邮箱地址，且 Redis 中存有该用户待发的全文（pending_email:{open_id}），
    则直接发送邮件并通过飞书回复结果，返回 True（跳过 LLM 处理）。
    否则返回 False（继续正常流程）。
    """
    if not _EMAIL_RE.match(task_text):
        return False

    logger.info(f"[Email] 📧 检测到邮箱地址: {task_text} | uid={user_id}")

    open_id = None
    if reply_info:
        open_id = reply_info.get("open_id") or reply_info.get("sender_id")
    if not open_id and user_id and user_id.startswith("feishu:"):
        open_id = user_id[len("feishu:"):]
    if not open_id:
        logger.info(f"[Email] 无法获取 open_id，跳过邮箱拦截 | uid={user_id}")
        return False

    try:
        import json as _json
        from core import cache as _cache
        r = await _cache.get_redis()
        key = f"pending_email:{open_id}"
        raw = await r.get(key)
        if not raw:
            logger.info(
                f"[Email] Redis 中无 pending_email | key={key}"
                f" | 邮箱将作为普通消息处理"
            )
            return False
        logger.info(f"[Email] ✓ 发现待发论文 | key={key} | raw_len={len(raw)}")

        data = _json.loads(raw)
        title = data.get("title", "OpenClaw 科研论文")
        content = data.get("content", "")
        email = task_text

        from skills.tools import send_email
        result = await send_email(
            to=email,
            subject=f"【OpenClaw】{title}",
            body=f"# {title}\n\n{content}\n\n---\n*由 OpenClaw 智能科研系统生成*",
        )

        channel = (reply_info or {}).get("channel", "api")
        if result.get("success"):
            await r.delete(key)
            logger.info(f"[Email] ✅ 论文已发送 | to={email} | open_id={open_id[:12]}…")
            if channel == "feishu":
                from core import notifier as _notifier
                await _notifier.send_proactive_feishu(
                    open_id,
                    "✅ 论文已发送至邮箱",
                    f"完整论文已发送至 **{email}**，请查收邮件！\n\n"
                    f"> 邮件主题：{title}\n\n"
                    "如未收到，请检查垃圾邮件文件夹。",
                )
        else:
            err = result.get("error", "未知错误")
            logger.warning(f"[Email] ❌ 邮件发送失败: {err}")
            if channel == "feishu":
                from core import notifier as _notifier
                await _notifier.send_proactive_feishu(
                    open_id,
                    "❌ 邮件发送失败",
                    f"发送至 **{email}** 时出错：{err}\n\n请确认邮箱地址是否正确后重试。",
                )
        return True

    except Exception as e:
        logger.warning(f"[Email] 邮箱拦截异常: {e}")
        return False


async def _auto_email_paper(
    user_id: str, paper_text: str, task_id: str, reply_info: dict = None
) -> None:
    """
    任务完成后自动发送论文邮件。
    查找用户邮箱来源：MySQL User 表。
    如果找不到邮箱且来源是飞书，主动提示用户回复邮箱。
    """
    email = None
    open_id = None

    if reply_info:
        open_id = reply_info.get("open_id") or reply_info.get("sender_id")

    try:
        from core.database import get_session, User
        from sqlalchemy import select

        raw_uid = user_id.replace("feishu:", "").replace("dingtalk:", "")

        async with await get_session() as session:
            result = await session.execute(
                select(User).where(
                    (User.id == raw_uid) |
                    (User.feishu_open_id == raw_uid) |
                    (User.dingtalk_user_id == raw_uid)
                )
            )
            user = result.scalar_one_or_none()
            if user:
                email = getattr(user, "email", None)
    except Exception as e:
        logger.info(f"[AutoEmail] 查询用户邮箱异常（非致命）: {e}")

    if not email:
        logger.info(
            f"[AutoEmail] 用户无已知邮箱 | uid={user_id}"
            f" | task_id={task_id[:12]} | paper_len={len(paper_text)}"
        )
        # 飞书渠道：论文已通过 send_long_content 分段推送，
        # 同时 pending_email 已由 send_long_content 存入 Redis，
        # 用户回复邮箱即可触发发送，无需额外处理
        return

    try:
        subject = f"【OpenClaw】科研论文 — {task_id[:8]}"
        body = f"{paper_text}\n\n---\n*由 OpenClaw 智能科研系统生成 | task_id: {task_id}*"

        from skills.tools import send_email
        email_result = await send_email(to=email, subject=subject, body=body)

        if email_result.get("success"):
            logger.info(
                f"[AutoEmail] ✅ 论文自动发送成功 | to={email}"
                f" | task_id={task_id[:12]} | paper_len={len(paper_text)}"
            )
            # 飞书渠道：通知用户邮件已发送
            if open_id:
                from core import notifier
                await notifier.send_proactive_feishu(
                    open_id, "📬 论文已发送至邮箱",
                    f"完整论文已自动发送至 **{email}**，请查收！\n\n"
                    f"> 任务编号：`{task_id[:16]}`\n\n"
                    "如未收到，请检查垃圾邮件文件夹。"
                )
        else:
            err = email_result.get("error", "未知错误")
            logger.warning(
                f"[AutoEmail] ❌ 论文邮件发送失败: {err}"
                f" | to={email} | task_id={task_id[:12]}"
            )
    except Exception as e:
        logger.warning(f"[AutoEmail] 邮件发送异常: {e}")
