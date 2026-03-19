"""
Redis 缓存层 — 任务队列、结果存储、会话缓存、限流

任务队列升级：Redis Lists (BLPOP) → Redis Streams (XREADGROUP)
  - 可靠交付：Worker 崩溃后任务不丢失（消息留在 Pending 状态）
  - ACK 机制：处理完成后 XACK 移除，未 ACK 消息自动被其他 Worker 认领
  - 审计日志：Stream 保留最近 10000 条历史（MAXLEN）
  - Consumer Groups：多 Worker 自动负载均衡

所有 key 统一以 REDIS_KEY_PREFIX 为前缀，共享 Redis 时区分不同系统
"""
import json
import logging
from core.logging_config import get_logger
from typing import Any, Optional

import redis.asyncio as aioredis

from config.settings import settings

logger = get_logger(__name__)

_PREFIX = settings.REDIS_KEY_PREFIX
_redis: Optional[aioredis.Redis] = None
_redis_blpop: Optional[aioredis.Redis] = None  # 专用于阻塞操作，无 socket_timeout

# ── Streams 常量 ──────────────────────────────────────────────────────
_STREAM_SUFFIX = "task_stream"
_STREAM_GROUP = "workers"
_STREAM_MAXLEN = 10000  # 保留最近 N 条消息用于审计


def _k(suffix: str) -> str:
    return f"{_PREFIX}:{suffix}"


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = await aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
    return _redis


async def _get_blpop_redis() -> aioredis.Redis:
    """专用于 BLPOP 的连接，无 socket_timeout（否则会与 BLPOP 阻塞冲突）"""
    global _redis_blpop
    if _redis_blpop is None:
        _redis_blpop = await aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
        )
    return _redis_blpop


async def close_redis():
    global _redis, _redis_blpop
    for r in [_redis, _redis_blpop]:
        if r:
            await r.aclose()
    _redis = None
    _redis_blpop = None


# ── 任务队列 — Redis Streams ──────────────────────────────────────────

async def init_task_stream() -> None:
    """
    初始化 Streams Consumer Group（幂等，应用启动时调用一次）。
    MKSTREAM：Stream 不存在时自动创建。
    """
    r = await get_redis()
    stream_key = _k(_STREAM_SUFFIX)
    try:
        await r.xgroup_create(stream_key, _STREAM_GROUP, id="0", mkstream=True)
        logger.info(f"[Cache] Streams group '{_STREAM_GROUP}' 创建成功 | stream={stream_key}")
    except Exception as e:
        if "BUSYGROUP" in str(e):
            logger.debug(f"[Cache] Streams group '{_STREAM_GROUP}' 已存在，跳过")
        else:
            logger.warning(f"[Cache] init_task_stream 失败: {e}")


async def push_task(task_data: dict) -> str:
    """
    将任务推入 Redis Stream，返回消息 ID（可用于追踪）。
    MAXLEN ~10000：自动修剪旧消息，避免无限增长。
    """
    r = await get_redis()
    stream_key = _k(_STREAM_SUFFIX)
    msg_id = await r.xadd(
        stream_key,
        {"data": json.dumps(task_data, ensure_ascii=False)},
        maxlen=_STREAM_MAXLEN,
        approximate=True,
    )
    logger.debug(f"[Cache] task pushed to stream | task_id={task_data.get('task_id')} msg_id={msg_id}")
    return msg_id


async def pop_task(worker_id: str = "worker-00", timeout: int = 5) -> Optional[tuple]:
    """
    从 Consumer Group 取出一条待处理消息（Exactly-Once 语义）。

    返回 (msg_id, task_data) 或 None（超时/无消息）。
    消息进入 Pending 状态，必须在处理完成后调用 ack_task(msg_id)。

    超时后自动尝试认领其他 Worker 遗留的 Pending 消息（崩溃恢复）。
    """
    r = await _get_blpop_redis()
    stream_key = _k(_STREAM_SUFFIX)
    try:
        results = await r.xreadgroup(
            _STREAM_GROUP,
            worker_id,
            {stream_key: ">"},  # ">" = 只取新消息
            count=1,
            block=timeout * 1000,
        )
        if results:
            _, messages = results[0]
            msg_id, fields = messages[0]
            task_data = json.loads(fields["data"])
            logger.debug(f"[Cache] task popped | worker={worker_id} msg_id={msg_id} task_id={task_data.get('task_id', '?')[:12]}")
            return msg_id, task_data

    except Exception as e:
        logger.debug(f"[Cache] pop_task xreadgroup 失败: {e}")

    # 超时后尝试认领 Pending 消息（其他 Worker 崩溃遗留）
    return await reclaim_stale_tasks(worker_id)


async def ack_task(msg_id: str) -> None:
    """
    确认任务处理完成，从 Pending 列表移除。
    必须在 _worker_loop 处理完成后调用，否则消息会被其他 Worker 重新认领。
    """
    try:
        r = await get_redis()
        await r.xack(_k(_STREAM_SUFFIX), _STREAM_GROUP, msg_id)
        logger.debug(f"[Cache] task ACK | msg_id={msg_id}")
    except Exception as e:
        logger.warning(f"[Cache] ack_task 失败 | msg_id={msg_id}: {e}")


async def reclaim_stale_tasks(
    worker_id: str,
    idle_ms: int = None,
) -> Optional[tuple]:
    """
    认领超时未 ACK 的 Pending 消息（崩溃恢复机制）。
    idle_ms 默认使用 settings.STREAM_PENDING_TIMEOUT_MS。
    """
    timeout_ms = idle_ms or settings.STREAM_PENDING_TIMEOUT_MS
    try:
        r = await get_redis()
        stream_key = _k(_STREAM_SUFFIX)

        # 查询 Pending 列表中超时的消息
        pending = await r.xpending_range(
            stream_key, _STREAM_GROUP,
            min="-", max="+", count=1,
            idle=timeout_ms,
        )
        if not pending:
            return None

        stale_msg_id = pending[0]["message_id"]
        claimed = await r.xclaim(
            stream_key, _STREAM_GROUP, worker_id,
            min_idle_time=timeout_ms,
            message_ids=[stale_msg_id],
        )
        if claimed:
            msg_id, fields = claimed[0]
            task_data = json.loads(fields["data"])
            logger.info(
                f"[Cache] 认领超时任务 | worker={worker_id} msg_id={msg_id}"
                f" task_id={task_data.get('task_id', '?')[:12]}"
            )
            return msg_id, task_data

    except Exception as e:
        logger.debug(f"[Cache] reclaim_stale_tasks 失败: {e}")
    return None


async def queue_length() -> int:
    """队列待处理数量（Stream 长度 + Pending 消息数）"""
    try:
        r = await get_redis()
        stream_key = _k(_STREAM_SUFFIX)
        stream_len = await r.xlen(stream_key)
        # Pending 数量：Consumer Group 中尚未 ACK 的消息
        try:
            info = await r.xpending(stream_key, _STREAM_GROUP)
            pending_count = info.get("pending", 0) if isinstance(info, dict) else 0
        except Exception:
            pending_count = 0
        return max(stream_len, pending_count)
    except Exception:
        return 0


async def get_stream_stats() -> dict:
    """获取 Stream 统计信息（用于监控 Dashboard）"""
    try:
        r = await get_redis()
        stream_key = _k(_STREAM_SUFFIX)
        stream_len = await r.xlen(stream_key)
        try:
            info = await r.xpending(stream_key, _STREAM_GROUP)
            pending = info.get("pending", 0) if isinstance(info, dict) else 0
        except Exception:
            pending = 0
        return {
            "stream_length": stream_len,
            "pending_ack": pending,
            "stream_key": stream_key,
            "group": _STREAM_GROUP,
        }
    except Exception as e:
        return {"error": str(e)}


# ── 任务运行状态跟踪（超时监控 / 看门狗）──────────────────────────────
async def set_task_running(task_id: str, worker_id: str, meta: dict) -> None:
    """
    记录任务开始执行的时刻和上下文（used by watchdog）。
    TTL = TASK_TIMEOUT * 3，确保即使 worker 崩溃也会自动过期。
    """
    import time as _time
    try:
        r = await get_redis()
        payload = json.dumps({
            "worker_id": worker_id,
            "started_at": _time.time(),
            "task_id": task_id,
            **{k: v for k, v in meta.items() if k != "task_id"},
        }, ensure_ascii=False)
        ttl = max(settings.TASK_TIMEOUT * 3, 900)
        await r.setex(_k(f"task_running:{task_id}"), ttl, payload)
    except Exception as e:
        logger.debug(f"[Cache] set_task_running failed: {e}")


async def clear_task_running(task_id: str) -> None:
    """任务完成（成功/超时/错误）后清理运行记录"""
    try:
        r = await get_redis()
        await r.delete(_k(f"task_running:{task_id}"))
    except Exception as e:
        logger.debug(f"[Cache] clear_task_running failed: {e}")


async def get_running_tasks() -> dict:
    """
    获取所有正在执行的任务（用于看门狗和监控面板）。
    返回 {task_id: {worker_id, started_at, user_id, channel, ...}}
    """
    try:
        r = await get_redis()
        keys = await r.keys(_k("task_running:*"))
        result = {}
        for k in keys:
            raw = await r.get(k)
            if raw:
                try:
                    data = json.loads(raw)
                    tid = data.get("task_id") or k.split("task_running:")[-1]
                    result[tid] = data
                except Exception:
                    pass
        return result
    except Exception as e:
        logger.debug(f"[Cache] get_running_tasks failed: {e}")
        return {}


# ── 任务结果 ───────────────────────────────────────────────────────────
async def store_result(task_id: str, result: dict, ttl: int = 86400) -> None:
    r = await get_redis()
    await r.setex(_k(f"result:{task_id}"), ttl, json.dumps(result, ensure_ascii=False))


async def get_result(task_id: str) -> Optional[dict]:
    r = await get_redis()
    raw = await r.get(_k(f"result:{task_id}"))
    return json.loads(raw) if raw else None


# ── Worker 心跳 ────────────────────────────────────────────────────────
async def set_worker_heartbeat(worker_id: str, status: str = "idle") -> None:
    """Worker 每 10s 更新一次心跳，TTL=30s（超时则视为宕机）"""
    r = await get_redis()
    await r.setex(_k(f"worker:{worker_id}:hb"), 30, status)


async def get_active_workers() -> list[str]:
    r = await get_redis()
    keys = await r.keys(_k("worker:*:hb"))
    result = []
    for k in keys:
        # key format: openclaw:{project}:worker:{worker_id}:hb
        # Split and extract worker_id (everything between "worker:" and ":hb")
        parts = k.split(":")
        if len(parts) >= 4:
            worker_id = parts[-2]  # second-to-last part before ":hb"
            result.append(worker_id)
    return result


# ── 会话缓存 ───────────────────────────────────────────────────────────
async def get_session(session_id: str) -> dict:
    r = await get_redis()
    raw = await r.hgetall(_k(f"session:{session_id}"))
    return raw or {}


async def set_session(session_id: str, data: dict, ttl: int = 3600) -> None:
    r = await get_redis()
    key = _k(f"session:{session_id}")
    if data:
        await r.hset(key, mapping=data)
        await r.expire(key, ttl)


# ── 限流 ──────────────────────────────────────────────────────────────
async def check_rate_limit(user_id: str, max_per_minute: int = 10) -> bool:
    """返回 True 表示允许请求，False 表示超限"""
    r = await get_redis()
    key = _k(f"ratelimit:{user_id}")
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, 60)
    return count <= max_per_minute


# ── 健康检查 ──────────────────────────────────────────────────────────
async def ping() -> bool:
    try:
        r = await get_redis()
        return await r.ping()
    except Exception as e:
        logger.warning(f"[Cache] Redis ping failed: {e}")
        return False


# ── 分布式锁（扩容防重推核心） ────────────────────────────────────────
async def acquire_lock(lock_key: str, ttl_seconds: int = 30, instance_id: str = "") -> bool:
    """
    获取分布式锁（Redis SET NX EX 原语）
    
    用途：
    - 防止多个容器实例同时发送相同的主动推送
    - 防止 Evolution Loop 在多实例场景下重复执行
    - 确保任何时刻只有一个实例处理同一个任务
    
    Args:
        lock_key: 锁的唯一标识（如 "notify:{user_id}:{content_hash}"）
        ttl_seconds: 锁的过期时间（秒），防止死锁
        instance_id: 当前实例标识（用于 debug），可留空
    
    Returns:
        True: 成功获取锁（本实例可以执行操作）
        False: 锁已被其他实例持有（跳过操作）
    """
    try:
        r = await get_redis()
        full_key = _k(f"lock:{lock_key}")
        value = instance_id or "1"
        # SET NX EX：只有 key 不存在时才设置，原子操作
        result = await r.set(full_key, value, nx=True, ex=ttl_seconds)
        return result is True
    except Exception as e:
        logger.warning(f"[Cache] acquire_lock failed for '{lock_key}': {e}")
        # 锁获取失败时降级为允许执行（避免功能完全中断）
        return True


async def release_lock(lock_key: str) -> None:
    """主动释放锁（任务完成后可提前释放）"""
    try:
        r = await get_redis()
        await r.delete(_k(f"lock:{lock_key}"))
    except Exception as e:
        logger.warning(f"[Cache] release_lock failed for '{lock_key}': {e}")


# ── 任务时长统计（ETA 估算）─────────────────────────────────────────────
async def record_task_duration(duration_seconds: float) -> None:
    """记录任务执行时长（保留最近 100 条样本，用于 ETA 估算）"""
    try:
        r = await get_redis()
        key = _k("task_duration_samples")
        await r.rpush(key, str(round(duration_seconds, 1)))
        await r.ltrim(key, -100, -1)
    except Exception as e:
        logger.debug(f"[Cache] record_task_duration failed: {e}")


async def get_avg_task_duration() -> float:
    """计算平均任务时长（秒），无样本时默认 120s"""
    try:
        r = await get_redis()
        samples = await r.lrange(_k("task_duration_samples"), 0, -1)
        if not samples:
            return 120.0
        durations = [float(s) for s in samples[-20:]]
        return round(sum(durations) / len(durations), 1)
    except Exception:
        return 120.0


async def get_queue_position(task_id: str) -> int:
    """获取任务在队列中的位置（1-based），不存在返回 0"""
    try:
        r = await get_redis()
        items = await r.lrange(_k("task_queue"), 0, -1)
        for i, raw in enumerate(items):
            try:
                data = json.loads(raw)
                if data.get("task_id") == task_id:
                    return i + 1
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"[Cache] get_queue_position failed: {e}")
    return 0


async def get_queue_eta(position: int, active_worker_count: int = 1) -> int:
    """根据队列位置估算等待时间（秒）"""
    import math
    avg = await get_avg_task_duration()
    workers = max(active_worker_count, 1)
    rounds = math.ceil(position / workers)
    return int(rounds * avg)


# ── 知识缓存（Wellspring 快速检索）──────────────────────────────────────
async def cache_knowledge(cache_key: str, content: str, ttl: int = 86400) -> None:
    """缓存知识条目（TTL 默认 24h）"""
    try:
        r = await get_redis()
        await r.setex(_k(f"knowledge:{cache_key}"), ttl, content)
    except Exception as e:
        logger.debug(f"[Cache] cache_knowledge failed: {e}")


async def get_cached_knowledge(cache_key: str) -> Optional[str]:
    """读取缓存的知识条目"""
    try:
        r = await get_redis()
        return await r.get(_k(f"knowledge:{cache_key}"))
    except Exception:
        return None


async def set_knowledge_index(entry_id: str, tags: list, ttl: int = 86400 * 7) -> None:
    """为知识条目建立标签索引（供关键词检索）"""
    try:
        r = await get_redis()
        for tag in tags:
            tag_key = _k(f"knowledge_tag:{tag.lower()[:40]}")
            await r.sadd(tag_key, entry_id)
            await r.expire(tag_key, ttl)
    except Exception as e:
        logger.debug(f"[Cache] set_knowledge_index failed: {e}")


async def search_knowledge_ids_by_tag(tag: str, limit: int = 10) -> list:
    """按标签检索知识条目 ID"""
    try:
        r = await get_redis()
        ids = await r.smembers(_k(f"knowledge_tag:{tag.lower()[:40]}"))
        return list(ids)[:limit]
    except Exception:
        return []


# ── 自主 Agent 运行状态 ───────────────────────────────────────────────
async def set_agent_run_status(agent_id: str, status: str, ttl: int = 7200) -> None:
    """记录自主 Agent 运行状态（空闲/运行中/告警）"""
    try:
        r = await get_redis()
        await r.setex(_k(f"agent_run:{agent_id}"), ttl, status)
    except Exception:
        pass


async def get_agent_run_status(agent_id: str) -> Optional[str]:
    try:
        r = await get_redis()
        return await r.get(_k(f"agent_run:{agent_id}"))
    except Exception:
        return None


async def get_all_agent_run_statuses() -> dict:
    """获取所有自主 Agent 的运行状态"""
    try:
        r = await get_redis()
        keys = await r.keys(_k("agent_run:*"))
        result = {}
        for k in keys:
            suffix = k.split(f"{_PREFIX}:agent_run:")[-1]
            val = await r.get(k)
            result[suffix] = val
        return result
    except Exception:
        return {}


# ── 系统告警 ──────────────────────────────────────────────────────────
async def push_alert(level: str, message: str, source: str = "") -> None:
    """推送系统告警到 Redis 列表（Maintainer 写入，Dashboard 读取）"""
    try:
        r = await get_redis()
        import time
        alert = json.dumps({
            "level": level, "message": message,
            "source": source, "ts": time.time(),
        }, ensure_ascii=False)
        key = _k("system_alerts")
        await r.rpush(key, alert)
        await r.ltrim(key, -200, -1)
        await r.expire(key, 86400 * 3)
    except Exception as e:
        logger.debug(f"[Cache] push_alert failed: {e}")


async def get_recent_alerts(limit: int = 50) -> list:
    """获取最近的系统告警"""
    try:
        r = await get_redis()
        items = await r.lrange(_k("system_alerts"), -limit, -1)
        return [json.loads(i) for i in items]
    except Exception:
        return []


# ── Worker 私有任务队列（Maintainer 路由调度使用）────────────────────────

async def push_task_to_worker(worker_id: str, task_data: dict) -> None:
    """
    将任务推入指定 Worker 的私有 Redis List。
    由 Maintainer 调度循环调用，确保每个任务只路由给一个 Worker。
    """
    try:
        r = await get_redis()
        raw = json.dumps(task_data, ensure_ascii=False)
        await r.rpush(_k(f"worker_queue:{worker_id}"), raw)
        logger.debug(f"[Cache] task pushed to worker {worker_id} | task_id={task_data.get('task_id', '?')[:12]}")
    except Exception as e:
        logger.warning(f"[Cache] push_task_to_worker failed for {worker_id}: {e}")


async def pop_task_from_worker(worker_id: str, timeout: int = 5) -> Optional[dict]:
    """
    从 Worker 私有队列阻塞等待取出一条任务（BLPOP）。
    返回 task_data dict 或 None（超时/无任务）。
    """
    try:
        r = await _get_blpop_redis()
        result = await r.blpop(_k(f"worker_queue:{worker_id}"), timeout=timeout)
        if result:
            _, raw = result
            return json.loads(raw)
    except Exception as e:
        logger.debug(f"[Cache] pop_task_from_worker failed for {worker_id}: {e}")
    return None


async def get_worker_queue_length(worker_id: str) -> int:
    """获取 Worker 私有队列待处理任务数（用于 Maintainer 负载感知路由）"""
    try:
        r = await get_redis()
        return int(await r.llen(_k(f"worker_queue:{worker_id}")))
    except Exception:
        return 0


async def get_all_worker_queue_lengths(worker_ids: list) -> dict:
    """批量获取所有 Worker 队列长度（Maintainer 路由决策用）"""
    result = {}
    for wid in worker_ids:
        result[wid] = await get_worker_queue_length(wid)
    return result


async def is_notification_sent(user_id: str, content_hash: str) -> bool:
    """
    检查今日是否已向该用户发送过该内容的推送
    
    扩容场景：多个容器实例共享同一 Redis，通过此函数确保推送唯一性
    在 ProactiveNotification 数据库记录的基础上增加 Redis 快速检查层
    """
    try:
        r = await get_redis()
        key = _k(f"notified:{user_id}:{content_hash}")
        return await r.exists(key) > 0
    except Exception:
        return False


async def mark_notification_sent(user_id: str, content_hash: str, ttl_hours: int = 25) -> None:
    """
    标记已发送推送（Redis 缓存层，TTL 略超 24h 防边界问题）
    与数据库 record_notification 配合使用：Redis 快速去重，MySQL 持久化存档
    """
    try:
        r = await get_redis()
        key = _k(f"notified:{user_id}:{content_hash}")
        await r.setex(key, ttl_hours * 3600, "1")
    except Exception as e:
        logger.warning(f"[Cache] mark_notification_sent failed: {e}")
