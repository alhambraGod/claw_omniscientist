"""
统一日志配置模块 — Omniscientist Claw v3.0

设计原则：
1. 所有日志统一写入 data/logs/，按天滚动，保留 30 天
2. 细粒度分层：用户输入 / 模型 I/O / 工具调用 / 调度过程 均有完整记录
3. 同时写 all.log（全量聚合）+ 各专项日志（按模块/类型），方便定向排查
4. 日志格式：时间 | 级别 | 模块 | 消息
5. DEBUG 级别写文件（完整追踪），控制台可配置

日志文件布局：
    data/logs/
    ├── all.log               # ★ 全量聚合（所有模块 DEBUG+，30天滚动）
    ├── app.log               # 应用主进程日志（INFO+）
    ├── agent.log             # Agent 执行日志（模型I/O、工具调用、推理迭代）
    ├── schedule.log          # 调度日志（Orchestrator、WorkerPool、任务生命周期）
    ├── feishu.log            # 飞书 Bot 消息收发
    ├── evolution.log         # 每日推送 / 进化循环
    ├── access.log            # HTTP 访问日志
    ├── error.log             # 全局 ERROR+（所有模块，快速定位）
    └── *.YYYY-MM-DD.log      # 历史日志（自动命名，30天后删除）
"""
import logging
import logging.handlers
import os
import sys
import time
from pathlib import Path


# ── 日志目录 ─────────────────────────────────────────────────────────────────
def _get_log_dir() -> Path:
    project_root = Path(__file__).resolve().parent.parent
    log_dir = project_root / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


LOG_DIR = _get_log_dir()

# ── 日志格式 ──────────────────────────────────────────────────────────────────
# 文件：详细格式，包含时间、级别（对齐8位）、模块名（对齐32位）
_FILE_FMT    = "%(asctime)s | %(levelname)-8s | %(name)-32s | %(message)s"
_DATE_FMT    = "%Y-%m-%d %H:%M:%S"
# 控制台：简洁格式
_CONSOLE_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# ── 已初始化标记（防止重复调用） ──────────────────────────────────────────────
_initialized = False


# ──────────────────────────────────────────────────────────────────────────────
# 带日期滚动的文件 Handler
# ──────────────────────────────────────────────────────────────────────────────
class _DailyRotatingHandler(logging.handlers.TimedRotatingFileHandler):
    """
    每天午夜滚动，保留 30 天。
    历史文件命名：<name>.YYYY-MM-DD.log（如 app.2026-03-16.log）
    """
    def __init__(self, filename: str, backup_count: int = 30, **kwargs):
        super().__init__(
            filename=filename,
            when="midnight",
            interval=1,
            backupCount=backup_count,
            encoding="utf-8",
            delay=False,
            **kwargs,
        )
        self.suffix = "%Y-%m-%d.log"
        self.namer = self._custom_namer

    @staticmethod
    def _custom_namer(default_name: str) -> str:
        """
        app.log.2026-03-16  →  app.2026-03-16.log
        """
        base_dir = os.path.dirname(default_name)
        base_file = os.path.basename(default_name)
        parts = base_file.split(".")
        if len(parts) >= 3:
            name = parts[0]
            date_suffix = parts[-1]
            new_name = f"{name}.{date_suffix}.log"
        else:
            new_name = base_file
        return os.path.join(base_dir, new_name)


def _make_handler(log_name: str, level: int = logging.DEBUG) -> _DailyRotatingHandler:
    """创建带日滚动的文件 handler"""
    log_path = LOG_DIR / f"{log_name}.log"
    handler = _DailyRotatingHandler(str(log_path), backup_count=30)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_FILE_FMT, datefmt=_DATE_FMT))
    return handler


def _make_console_handler(level: int = logging.INFO) -> logging.StreamHandler:
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_CONSOLE_FMT, datefmt=_DATE_FMT))
    return handler


# ──────────────────────────────────────────────────────────────────────────────
# 主初始化函数
# ──────────────────────────────────────────────────────────────────────────────
def setup_logging(
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
    instance_type: str = "orchestrator",
) -> None:
    """
    初始化全系统日志，应在应用启动时调用一次。

    日志分层策略：
        all.log      — 全量 DEBUG+ 聚合，是完整的系统执行轨迹
        app.log      — 主进程 INFO+（启动、健康、关键事件）
        agent.log    — agents.* 模块 DEBUG+（模型I/O、工具调用、迭代过程）
        schedule.log — 调度层 DEBUG+（Orchestrator、WorkerPool、任务队列）
        feishu.log   — feishu.* / channels.* DEBUG+（消息收发）
        evolution.log— 进化循环 DEBUG+
        access.log   — uvicorn.access INFO+
        error.log    — 全局 ERROR+（所有模块）

    Args:
        console_level: 控制台输出级别（开发 INFO，生产 WARNING）
        file_level:    文件写入基础级别（建议 DEBUG 保留完整轨迹）
        instance_type: 影响 app.log 文件名
            orchestrator → app.log
            feishu_bot   → feishu.log（作为主日志，覆盖默认）
            worker_pool  → worker.log
            evolution    → evolution.log
    """
    global _initialized
    if _initialized:
        return

    # ── Root Logger：底层设为 DEBUG，让 handler 自己控制级别 ─────────────────
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    # ── 1. 控制台 ─────────────────────────────────────────────────────────────
    root.addHandler(_make_console_handler(console_level))

    # ── 2. all.log：全量聚合，DEBUG+ ─────────────────────────────────────────
    # 这是系统最完整的日志文件，包含所有模块的所有日志
    root.addHandler(_make_handler("all", logging.DEBUG))

    # ── 3. app.log / 实例主日志：INFO+ ────────────────────────────────────────
    _log_name_map = {
        "orchestrator": "app",
        "feishu_bot":   "feishu",
        "worker_pool":  "worker",
        "evolution":    "evolution",
    }
    main_log_name = _log_name_map.get(instance_type, "app")
    root.addHandler(_make_handler(main_log_name, logging.INFO))

    # ── 4. error.log：全局 ERROR+（所有模块，快速定位） ───────────────────────
    root.addHandler(_make_handler("error", logging.ERROR))

    # ── 5. agent.log：agents.* 模块（模型 I/O、工具调用、推理迭代）DEBUG+ ──────
    _add_module_handler("agents", "agent", logging.DEBUG)

    # ── 6. schedule.log：调度层（Orchestrator、WorkerPool）DEBUG+ ─────────────
    _add_module_handler("core.orchestrator", "schedule", logging.DEBUG)
    _add_module_handler("core.worker_pool",  "schedule", logging.DEBUG)
    _add_module_handler("core.router",       "schedule", logging.DEBUG)

    # ── 7. feishu.log：飞书 Bot 消息收发 DEBUG+ ───────────────────────────────
    _add_module_handler("feishu",   "feishu", logging.DEBUG)
    _add_module_handler("channels", "feishu", logging.DEBUG)

    # ── 8. evolution.log：进化循环 DEBUG+ ────────────────────────────────────
    _add_module_handler("core.evolution_loop", "evolution", logging.DEBUG)
    _add_module_handler("core.interest_extractor", "evolution", logging.DEBUG)

    # ── 9. access.log：HTTP 请求日志 INFO+ ───────────────────────────────────
    _add_module_handler("uvicorn.access", "access", logging.INFO)

    # ── 11. 降噪：第三方库只输出 WARNING+ ─────────────────────────────────────
    for lib in [
        "httpx", "httpcore", "urllib3", "asyncio", "hpack",
        "multipart", "sqlalchemy.engine", "sqlalchemy.pool",
        "sqlalchemy.dialects", "aiosqlite", "aiomysql",
    ]:
        logging.getLogger(lib).setLevel(logging.WARNING)

    # uvicorn 主进程日志设为 INFO（只保留启动信息，不要 DEBUG）
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)

    _initialized = True

    logger = logging.getLogger("core.logging_config")
    logger.info(
        f"日志系统初始化完成 | instance={instance_type} | main_log={main_log_name}.log"
        f" | all.log=data/logs/all.log | file_level={logging.getLevelName(file_level)}"
        f" | rotate=daily | retain=30days"
    )
    logger.debug(
        f"日志文件目录: {LOG_DIR} | "
        f"文件列表: all / {main_log_name} / agent / schedule / feishu / evolution / access / error"
    )


def _add_module_handler(
    logger_name: str,
    log_file: str,
    level: int = logging.DEBUG,
) -> None:
    """
    为指定模块配置专属文件 handler，同时保留向 root 传播
    （root 的 all.log 也能收到，不会丢失）
    """
    lg = logging.getLogger(logger_name)
    lg.addHandler(_make_handler(log_file, level))
    lg.propagate = True  # 继续向 root 传播 → all.log 也写入


# ──────────────────────────────────────────────────────────────────────────────
# 便捷函数
# ──────────────────────────────────────────────────────────────────────────────
def get_logger(name: str) -> logging.Logger:
    """
    获取命名 logger（所有模块统一使用此接口）

    用法：
        from core.logging_config import get_logger
        logger = get_logger(__name__)
    """
    return logging.getLogger(name)


def cleanup_old_logs(retain_days: int = 30) -> int:
    """
    清理超过 retain_days 天的历史日志文件。
    TimedRotatingFileHandler.backupCount 已自动管理，此函数作为双重保障。

    Returns:
        int: 删除的文件数量
    """
    cutoff = time.time() - retain_days * 86400
    deleted = 0
    for f in LOG_DIR.iterdir():
        if f.is_file() and f.name.endswith(".log") and f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                deleted += 1
            except OSError:
                pass
    if deleted > 0:
        logging.getLogger("core.logging_config").info(
            f"日志清理：已删除 {deleted} 个超过 {retain_days} 天的历史日志"
        )
    return deleted
