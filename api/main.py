"""
OpenClaw FastAPI 主应用
lifespan 统一管理：MySQL/Redis 初始化、Worker Pool、Feishu Bot、Evolution Loop
"""
import sys
import os
import asyncio
import threading
import logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from config.settings import settings
from core.registry import registry
from core.orchestrator import Orchestrator
from core.worker_pool import get_worker_pool
from core.logging_config import setup_logging, get_logger

# 启动统一日志系统（优先于任何 logger 创建）
# file_level 固定 DEBUG，确保 all.log / agent.log / schedule.log 有完整追踪
setup_logging(
    console_level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    file_level=logging.DEBUG,   # 文件始终记录 DEBUG，控制台按 LOG_LEVEL 过滤
    instance_type=settings.INSTANCE_TYPE or "orchestrator",
)
logger = get_logger(__name__)

# 全局编排器
orchestrator: Orchestrator = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator
    logger.info("🦞 OpenClaw 社区系统启动中…")

    # 1. Agent 注册
    registry.initialize()
    orchestrator = Orchestrator(registry)
    logger.info(f"✅ Agent 注册完成: {registry.summary()}")

    # 2. 数据库初始化（MySQL）
    try:
        from core.database import init_db
        await init_db()
        logger.info("✅ MySQL 数据库初始化完成")
    except Exception as e:
        logger.warning(f"⚠️ 数据库初始化跳过（将降级运行）: {e}")

    # 3. Redis 连接检查 + Stream 初始化
    try:
        from core import cache as cache_store
        ok = await cache_store.ping()
        logger.info(f"✅ Redis 连接: {'OK' if ok else 'FAILED（将降级运行）'}")
        if ok:
            await cache_store.init_task_stream()
            logger.info("✅ Redis Stream 任务队列初始化完成（Consumer Group: workers）")
    except Exception as e:
        logger.warning(f"⚠️ Redis 连接/Stream 初始化失败（将降级运行）: {e}")

    # 3.5 ChromaDB 向量存储初始化（语义层）
    try:
        from core.vector_store import get_vector_store
        vs = get_vector_store()
        await vs.initialize()
        vstats = await vs.stats()
        if vstats.get("ready"):
            counts = vstats.get("collections", {})
            logger.info(
                f"✅ ChromaDB 向量存储就绪 | "
                f"knowledge={counts.get('knowledge', 0)} "
                f"papers={counts.get('papers', 0)} "
                f"user_interests={counts.get('user_interests', 0)}"
            )
            # 冷启动：将 MySQL 中未嵌入的知识条目同步到 ChromaDB
            synced = await vs.sync_from_mysql(limit=200)
            if synced:
                logger.info(f"✅ ChromaDB 冷启动同步 | 新嵌入 {synced} 条知识")
        else:
            logger.warning("⚠️ ChromaDB 初始化失败（语义检索降级为关键词匹配）")
    except Exception as e:
        logger.warning(f"⚠️ ChromaDB 初始化异常（将降级运行）: {e}")

    # 4. Worker Pool 启动
    pool = get_worker_pool()
    workers = registry.get_workers()
    pool.setup(
        workers=workers,
        guardian=registry.get_guardian(),
        wellspring=registry.get_wellspring(),
    )
    await pool.start()
    logger.info(f"✅ Worker Pool 启动: {len(workers)} 个 Worker")

    # 5. Channel Adapters — 统一注册并启动所有已配置的 IM 渠道
    from channels import register_adapter
    from channels.feishu_adapter import FeishuAdapter
    from channels.dingtalk_adapter import DingTalkAdapter

    adapters_to_start = []

    if FeishuAdapter.is_configured():
        # FEISHU_ADAPTER_ENABLED=true：注册 + 启动 WebSocket 监听
        feishu_adapter = FeishuAdapter()
        register_adapter(feishu_adapter)
        adapters_to_start.append(feishu_adapter)
    elif settings.FEISHU_APP_ID and settings.FEISHU_APP_SECRET:
        # FEISHU_ADAPTER_ENABLED=false：不启动 WebSocket（避免与 OpenClaw Gateway 双连接冲突）
        # 但必须注册 adapter，以保留 HTTP 主动推送能力（send_proactive / reply_card）
        feishu_adapter = FeishuAdapter()
        register_adapter(feishu_adapter)
        logger.info(
            "✅ Feishu HTTP 推送已就绪（FEISHU_ADAPTER_ENABLED=false，WebSocket 已跳过）"
            " — OpenClaw Gateway 负责接收消息，本适配器仅用于主动推送结果"
        )
    else:
        logger.info("⏭️  Feishu Bot 跳过（未配置 FEISHU_APP_ID / FEISHU_APP_SECRET）")

    if DingTalkAdapter.is_configured():
        dingtalk_adapter = DingTalkAdapter()
        register_adapter(dingtalk_adapter)
        adapters_to_start.append(dingtalk_adapter)
    else:
        logger.info("⏭️  DingTalk Bot 跳过（未配置 DINGTALK_APP_KEY/APP_SECRET）")

    main_loop = asyncio.get_event_loop()
    for adapter in adapters_to_start:
        t = threading.Thread(
            target=adapter.start,
            args=(main_loop,),
            name=f"{adapter.name}-bot",
            daemon=True,
        )
        t.start()
        logger.info(f"✅ {adapter.name.capitalize()} Bot 启动")

    # 6. Evolution Loop（每日科研推送）
    try:
        from core import evolution_loop
        evolution_loop.start(vanguard_agent=registry.get_vanguard())
        logger.info(f"✅ Evolution Loop 启动（每日 {settings.EVOLUTION_EMAIL_HOUR:02d}:07 CST）")
    except Exception as e:
        logger.warning(f"⚠️ Evolution Loop 启动失败: {e}")

    # 7. Autonomous Loop（全角色自主运行：Maintainer/Vanguard/Wellspring/Promoter/Guardian）
    try:
        from core.autonomous_loop import get_autonomous_loop
        auto_loop = get_autonomous_loop()
        auto_loop.setup({
            "maintainer": registry.get_maintainer(),
            "vanguard":   registry.get_vanguard(),
            "wellspring": registry.get_wellspring(),
            "promoter":   registry.get_promoter(),
            "guardian":   registry.get_guardian(),
        })
        auto_loop.start()
        if settings.AUTONOMOUS_ENABLED:
            logger.info(
                "✅ Autonomous Loop 启动\n"
                "   Maintainer 健康检查：每10分钟\n"
                "   Vanguard   前沿扫描：08:10 & 20:10 CST\n"
                "   Wellspring 知识合成：02:30 CST\n"
                "   Promoter   内容生成：每周二10:00 CST"
            )
    except Exception as e:
        logger.warning(f"⚠️ Autonomous Loop 启动失败: {e}")

    # 注：任务超时看门狗（TaskWatchdog）已集成到 Maintainer 自主调度，每60s运行一次。
    # 见 core/autonomous_loop.py → _maintainer_watchdog_job

    logger.info("🦞 OpenClaw 就绪！")
    logger.info(f"   Web 控制台: http://localhost:{settings.PORT}")
    logger.info(f"   API 文档:   http://localhost:{settings.PORT}/docs")
    workers = registry.get_workers()
    logger.info(f"   执行引擎: {'✅ LeadResearcher 已内嵌（画像驱动）' if workers and getattr(workers[0], '_lead', None) else '⚠️ LeadResearcher 未注入'}")
    logger.info(f"   专属 OpenClaw Gateway: http://localhost:10100/chat?session=main&token=omni-internal-token  (独立飞书 App: {settings.FEISHU_BOT_NAME})")
    yield

    # 关闭序列
    logger.info("OpenClaw 正在关闭…")
    try:
        from core import evolution_loop
        evolution_loop.stop()
    except Exception:
        pass
    try:
        from core.autonomous_loop import get_autonomous_loop
        get_autonomous_loop().stop()
    except Exception:
        pass
    await pool.stop()


app = FastAPI(
    title=f"OpenClaw — {settings.PROJECT_NAME}",
    description="面向科研、教育、学术创新的智能体操作系统",
    version=settings.VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── 挂载路由 ──────────────────────────────────────────────────────
from api.routes import agents, tasks, wellspring, system, stream, instances, analytics, conversations, vanguard

app.include_router(agents.router, prefix="/api/agents", tags=["Agents"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["Tasks"])
app.include_router(stream.router, prefix="/api/tasks", tags=["Tasks"])
app.include_router(wellspring.router, prefix="/api/wellspring", tags=["Wellspring"])
app.include_router(system.router, prefix="/api/system", tags=["System"])
app.include_router(instances.router, prefix="/api/instances", tags=["Instances"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["Analytics"])
app.include_router(conversations.router, prefix="/api/conversations", tags=["Conversations"])
app.include_router(vanguard.router, prefix="/api/vanguard", tags=["Vanguard"])

# ─── 静态文件 ──────────────────────────────────────────────────────
web_dir = Path(__file__).parent.parent / "web"
if web_dir.exists():
    app.mount("/static", StaticFiles(directory=str(web_dir / "static")), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_index():
        return FileResponse(str(web_dir / "index.html"))


@app.get("/health")
async def health():
    from core import cache as cache_store
    redis_ok = await cache_store.ping()
    return {
        "status": "ok",
        "project": settings.PROJECT_NAME,
        "instance": settings.INSTANCE_NAME,
        "type": settings.INSTANCE_TYPE,
        "version": settings.VERSION,
        "workers": len(registry.get_workers()),
        "redis": redis_ok,
    }


@app.get("/api/queue/status")
async def queue_status():
    from core import cache as cache_store
    qlen = await cache_store.queue_length()
    active = await cache_store.get_active_workers()
    stream_stats = await cache_store.get_stream_stats()
    from core.vector_store import get_vector_store
    vs_stats = await get_vector_store().stats()
    return {
        "queue_length": qlen,
        "active_workers": active,
        "worker_count": len(registry.get_workers()),
        "stream": stream_stats,
        "vector_store": vs_stats,
    }


def get_orchestrator() -> Orchestrator:
    return orchestrator
