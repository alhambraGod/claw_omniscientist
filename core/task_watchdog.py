"""
TaskWatchdog — 任务超时看门狗（核心逻辑模块）

架构说明：
    本模块是纯逻辑层，不独立启动，由 Maintainer 维护者通过 AutonomousLoop 调度执行。

    调度路径：
        AutonomousLoop._maintainer_watchdog_job()  [每60s]
            └─▶ _check_once()                      [单次巡检]

    MaintainerAgent.watch_running_tasks() 也可直接调用 _check_once() 实现按需检查。

两道防线设计：
    第一道：asyncio.wait_for(timeout=TASK_TIMEOUT)   — Worker 内部，准时触发
    第二道：_check_once() 由 Maintainer 每60s巡检    — 应对 event loop 阻塞/worker crash

避免双重通知：
    若 Redis 中已有任务结果（第一道已处理），看门狗跳过，不重复通知。
    宽限期 GRACE_PERIOD=60s：给第一道处理路径留够写入时间后再介入。
"""
import asyncio
import time
import logging

from core.logging_config import get_logger

logger = get_logger(__name__)

WATCHDOG_INTERVAL = 60   # 看门狗检查间隔（秒）
GRACE_PERIOD = 60        # 超过 TASK_TIMEOUT 后的宽限期（秒），避免与 asyncio.wait_for 竞争


async def task_watchdog_loop() -> None:
    """
    备用：看门狗主循环（独立 asyncio.Task 模式）。

    正常生产环境中，看门狗由 Maintainer/AutonomousLoop 调度（每60s）。
    本函数仅用于：单机调试、无 AutonomousLoop 的轻量部署，或单元测试。
    """
    from config.settings import settings
    import core.cache as cache_store
    import core.notifier as notifier

    logger.info(
        f"[Watchdog] 🐾 任务超时监控已启动 | 检查间隔={WATCHDOG_INTERVAL}s"
        f" | 触发阈值={settings.TASK_TIMEOUT + GRACE_PERIOD}s"
    )

    while True:
        try:
            await asyncio.sleep(WATCHDOG_INTERVAL)
            await _check_once(settings, cache_store, notifier)
        except asyncio.CancelledError:
            logger.info("[Watchdog] 任务超时监控已停止（CancelledError）")
            break
        except Exception as e:
            logger.error(f"[Watchdog] 看门狗循环异常（已忽略，下次继续）: {e}", exc_info=True)


async def _check_once(settings, cache_store, notifier) -> None:
    """单次检查所有正在执行的任务"""
    running = await cache_store.get_running_tasks()
    if not running:
        return

    now = time.time()
    deadline = settings.TASK_TIMEOUT + GRACE_PERIOD

    for task_id, task_info in running.items():
        started_at = task_info.get("started_at", now)
        elapsed = now - started_at

        if elapsed <= deadline:
            continue

        # 检查是否已有结果（asyncio.wait_for 已处理则跳过）
        try:
            existing = await cache_store.get_result(task_id)
        except Exception:
            existing = None

        if existing is not None:
            # 已有结果，清理残留运行记录
            await cache_store.clear_task_running(task_id)
            continue

        # ── 强制超时处理 ──────────────────────────────────────────────────
        logger.warning(
            f"[Watchdog] 🔴 强制超时 | task_id={task_id[:12]}"
            f" | elapsed={int(elapsed)}s > deadline={int(deadline)}s"
            f" | worker={task_info.get('worker_id', '?')}"
            f" | user={task_info.get('user_id', '?')}"
            f" | channel={task_info.get('channel', '?')}"
        )

        timeout_result = {
            "task_id": task_id,
            "status": "timeout",
            "error": (
                f"任务执行超时（运行 {int(elapsed)}s，超过系统限制 {settings.TASK_TIMEOUT}s）。"
                "请尝试简化任务描述后重新提交。"
            ),
            "elapsed_seconds": int(elapsed),
            "killed_by": "watchdog",
        }

        # 存入 Redis（让轮询客户端感知结果）
        try:
            await cache_store.store_result(task_id, timeout_result)
        except Exception as e:
            logger.error(f"[Watchdog] 存储超时结果失败: {e}")
            continue

        # 通知用户（飞书主动推送 / IM / email 等）
        reply_info = task_info.get("reply_info") or {}
        channel = task_info.get("channel", reply_info.get("channel", "api"))
        user_id = task_info.get("user_id", "?")

        try:
            await notifier.notify(reply_info, timeout_result)
        except Exception as e:
            logger.error(f"[Watchdog] 超时通知失败 | task_id={task_id[:12]}: {e}")

        # 飞书：额外主动推送（open_id 路径）
        if channel == "feishu":
            open_id = reply_info.get("open_id") or reply_info.get("sender_id")
            if open_id:
                msg = (
                    f"你的科研任务已运行 {int(elapsed)}s，超过系统限制 {settings.TASK_TIMEOUT}s，"
                    "已由系统自动终止。\n\n"
                    "**建议**：简化任务描述，或分步提交子任务。\n\n"
                    f"> 任务编号：`{task_id[:16]}`"
                )
                try:
                    await notifier.send_proactive_feishu(open_id, "⏱️ 科研任务超时（看门狗）", msg)
                except Exception as e:
                    logger.warning(f"[Watchdog] 飞书主动推送失败: {e}")

        # 系统告警
        try:
            await cache_store.push_alert(
                "warning",
                f"[Watchdog] 任务超时强制终止 | task={task_id[:12]} | uid={user_id} | elapsed={int(elapsed)}s",
                source="task_watchdog",
            )
        except Exception:
            pass

        # 清理运行记录
        await cache_store.clear_task_running(task_id)
