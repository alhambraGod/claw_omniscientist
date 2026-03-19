"""
OpenClaw 配置模块
"""
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# 北京时间 UTC+8
TZ_CST = timezone(timedelta(hours=8))


def now() -> datetime:
    """返回当前北京时间（UTC+8），全系统统一使用此函数"""
    return datetime.now(TZ_CST)


class Settings:
    # 项目标识
    PROJECT_NAME: str = os.getenv("PROJECT_NAME", "openclaw_research")
    VERSION: str = os.getenv("VERSION", "1.0.0")
    INSTANCE_TYPE: str = os.getenv("INSTANCE_TYPE", "orchestrator")
    INSTANCE_NAME: str = os.getenv("INSTANCE_NAME", "openclaw_research")
    # 逗号分隔的允许技能列表，空字符串表示允许全部技能
    ALLOWED_SKILLS: str = os.getenv("ALLOWED_SKILLS", "")

    # API Keys
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # OpenRouter / 自定义推理端点
    OPENROUTER_BASE_URL: str = os.getenv("OPENROUTER_BASE_URL", "")

    # 模型
    DEFAULT_MODEL: str = os.getenv("DEFAULT_MODEL", "gemini-3.1-pro-preview-thinking")
    FAST_MODEL: str = os.getenv("FAST_MODEL", "gemini-3.1-pro-preview-thinking")

    # 飞书集成
    FEISHU_APP_ID: str = os.getenv("FEISHU_APP_ID", "")
    FEISHU_APP_SECRET: str = os.getenv("FEISHU_APP_SECRET", "")
    FEISHU_BOT_NAME: str = os.getenv("FEISHU_BOT_NAME", "omniscientist_claw")
    FEISHU_BOT_OPEN_ID: str = os.getenv("FEISHU_BOT_OPEN_ID", "")
    FEISHU_WEBHOOK: str = os.getenv("FEISHU_WEBHOOK", "")   # 自定义机器人 Webhook（通知层，可选）
    # 是否启动 FastAPI 内置的 Feishu WebSocket 监听（FeishuAdapter）。
    # 使用 OpenClaw Gateway 作为飞书入口时，必须设为 false，避免双连接冲突。
    # 凭据（APP_ID/SECRET）可继续保留，用于 Worker 向飞书主动推送结果（HTTP API）。
    FEISHU_ADAPTER_ENABLED: bool = os.getenv("FEISHU_ADAPTER_ENABLED", "true").lower() not in ("false", "0", "no", "off")

    # 服务
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "10101"))
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    # ── 端口规划（统一在 10100-10199 段）──────────────────────────
    #
    # 入口层 10100-10109
    PORT_GATEWAY: int = int(os.getenv("PORT_GATEWAY", "10100"))   # OpenClaw Gateway Web UI（omni-gateway）
    PORT_WEB: int = int(os.getenv("PORT_WEB", "10101"))           # Research API + Web 控制台（主入口）
    PORT_DEBUG: int = int(os.getenv("PORT_DEBUG", "10102"))        # Research API 调试暴露（容器模式，127.0.0.1）
    PORT_SCHEDULER: int = int(os.getenv("PORT_SCHEDULER", "10103"))  # 任务调度 / Orchestrator（预留拆分）
    PORT_AUTH: int = int(os.getenv("PORT_AUTH", "10104"))          # 认证服务（预留）
    PORT_WS: int = int(os.getenv("PORT_WS", "10105"))              # WebSocket 服务（预留）
    # 10106-10108 预留
    PORT_BRIDGE: int = int(os.getenv("PORT_BRIDGE", "10109"))      # OpenClaw WebSocket Bridge（omni-gateway）

    # Clawer 智能体层 10110-10129（每个 Clawer 独立进程时使用）
    PORT_CLAWER_MATH: int = int(os.getenv("PORT_CLAWER_MATH", "10110"))
    PORT_CLAWER_CS1: int = int(os.getenv("PORT_CLAWER_CS1", "10111"))
    PORT_CLAWER_CS2: int = int(os.getenv("PORT_CLAWER_CS2", "10112"))
    PORT_CLAWER_BIO: int = int(os.getenv("PORT_CLAWER_BIO", "10113"))
    PORT_CLAWER_EDU: int = int(os.getenv("PORT_CLAWER_EDU", "10114"))
    PORT_CLAWER_SURVEY: int = int(os.getenv("PORT_CLAWER_SURVEY", "10115"))
    PORT_CLAWER_WRITING: int = int(os.getenv("PORT_CLAWER_WRITING", "10116"))
    PORT_CLAWER_DATA: int = int(os.getenv("PORT_CLAWER_DATA", "10117"))
    PORT_CLAWER_EXPERIMENT: int = int(os.getenv("PORT_CLAWER_EXPERIMENT", "10118"))
    PORT_CLAWER_REVIEW: int = int(os.getenv("PORT_CLAWER_REVIEW", "10119"))
    # 10120-10129 预留给未来 Clawer

    # 功能性智能体层 10130-10139
    PORT_GUARDIAN: int = int(os.getenv("PORT_GUARDIAN", "10130"))    # 风控守卫
    PORT_VANGUARD: int = int(os.getenv("PORT_VANGUARD", "10131"))    # 前沿探索
    PORT_MAINTAINER: int = int(os.getenv("PORT_MAINTAINER", "10132"))  # 健康维护
    PORT_PROMOTER: int = int(os.getenv("PORT_PROMOTER", "10133"))    # 内容推广
    PORT_WELLSPRING: int = int(os.getenv("PORT_WELLSPRING", "10134"))  # 集体知识
    # 10135-10139 预留

    # 数据服务层 10140-10149
    PORT_DB: int = int(os.getenv("PORT_DB", "10140"))             # 数据库服务（外置时使用）
    PORT_VECTOR_DB: int = int(os.getenv("PORT_VECTOR_DB", "10141"))  # ChromaDB 向量库
    PORT_CACHE: int = int(os.getenv("PORT_CACHE", "10142"))       # 缓存（Redis，预留）
    # 10143-10149 预留

    # 工具 / 集成服务层 10150-10159
    PORT_SEARCH: int = int(os.getenv("PORT_SEARCH", "10151"))     # 搜索服务（Tavily）
    PORT_STORAGE: int = int(os.getenv("PORT_STORAGE", "10152"))   # 文件 / 存储服务
    PORT_NOTIFY: int = int(os.getenv("PORT_NOTIFY", "10153"))     # 通知服务（邮件 / 钉钉）
    # 10154-10159 预留

    # 基础设施层 10160-10179
    PORT_BROKER: int = int(os.getenv("PORT_BROKER", "10160"))     # 消息队列（预留）
    PORT_METRICS: int = int(os.getenv("PORT_METRICS", "10161"))   # Prometheus 指标
    PORT_LOGS: int = int(os.getenv("PORT_LOGS", "10162"))          # 日志聚合
    PORT_EVOLUTION: int = int(os.getenv("PORT_EVOLUTION", "10163"))  # Evolution Loop 独立服务（预留）
    # 10164-10199 扩展预留

    # 数据库
    # 主存储：MySQL（生产必用）
    MYSQL_URL: str = os.getenv(
        "MYSQL_URL", "mysql+aiomysql://root:agent%211234@127.0.0.1:3306/openclaw_research"
    )
    # SQLite 已废弃（v3.0 起完全使用 MySQL），仅供本地单机不含 MySQL 的临时测试
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", f"sqlite+aiosqlite:///{BASE_DIR}/data/openclaw.db"
    )
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    # 共享 Redis 时用 key 前缀区分不同系统，避免与宿主机其他 openclaw 实例冲突
    REDIS_KEY_PREFIX: str = os.getenv("REDIS_KEY_PREFIX", "omni_research")
    CHROMA_DB_PATH: str = os.getenv("CHROMA_DB_PATH", str(BASE_DIR / "data/chroma"))
    # 向量嵌入模型：默认使用 ChromaDB 内置（all-MiniLM-L6-v2，本地无需 API）
    # 可选 "openai" 使用 OpenAI text-embedding-3-small（需要 OPENAI_API_KEY）
    CHROMA_EMBEDDING_MODEL: str = os.getenv("CHROMA_EMBEDDING_MODEL", "default")
    # Redis Streams：Pending 消息超时认领阈值（毫秒）
    # 必须远大于 TASK_TIMEOUT（1200s=1200000ms），避免正在执行的任务被其他Worker误判为"崩溃任务"重复认领
    # 默认 3600000ms = 60分钟（TASK_TIMEOUT 的 3倍），确保20分钟论文生成不会被重复执行
    STREAM_PENDING_TIMEOUT_MS: int = int(os.getenv("STREAM_PENDING_TIMEOUT_MS", "3600000"))

    # 日志
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # 短信
    ALIYUN_ACCESS_KEY_ID: str = os.getenv("ALIYUN_ACCESS_KEY_ID", "")
    ALIYUN_ACCESS_KEY_SECRET: str = os.getenv("ALIYUN_ACCESS_KEY_SECRET", "")
    ALIYUN_SMS_SIGN_NAME: str = os.getenv("ALIYUN_SMS_SIGN_NAME", "OpenClaw")
    ALIYUN_SMS_TEMPLATE_CODE: str = os.getenv("ALIYUN_SMS_TEMPLATE_CODE", "")

    # 钉钉
    DINGTALK_APP_KEY: str = os.getenv("DINGTALK_APP_KEY", "")
    DINGTALK_APP_SECRET: str = os.getenv("DINGTALK_APP_SECRET", "")
    DINGTALK_ROBOT_CODE: str = os.getenv("DINGTALK_ROBOT_CODE", "")
    DINGTALK_WEBHOOK: str = os.getenv("DINGTALK_WEBHOOK", "")

    # 邮件 SMTP
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "465"))
    SMTP_USE_TLS: bool = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
    SMTP_AUTHORIZATION_CODE: str = os.getenv("SMTP_AUTHORIZATION_CODE", "")
    SMTP_FROM: str = os.getenv("SMTP_FROM", "")
    SMTP_USER: str = os.getenv("SMTP_USER", "")

    # 搜索
    TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")

    # 社区
    MAX_CLAWER_INSTANCES: int = int(os.getenv("MAX_CLAWER_INSTANCES", "10"))
    MAX_CONCURRENT_TASKS: int = int(os.getenv("MAX_CONCURRENT_TASKS", "50"))
    TASK_TIMEOUT: int = int(os.getenv("TASK_TIMEOUT", "1200"))
    # Worker 数量：3个Worker × 5并发 = 15个并发处理槽，避免过多Worker竞争同一Stream消息
    WORKER_COUNT: int = int(os.getenv("WORKER_COUNT", "3"))
    # 每个Worker最大并发任务数（每个Worker可同时处理N个任务，非阻塞）
    WORKER_CONCURRENCY: int = int(os.getenv("WORKER_CONCURRENCY", "5"))
    EVOLUTION_EMAIL_HOUR: int = int(os.getenv("EVOLUTION_EMAIL_HOUR", "8"))

    # 自主运行（Autonomous Loop）
    AUTONOMOUS_ENABLED: bool = os.getenv("AUTONOMOUS_ENABLED", "true").lower() == "true"
    # Maintainer 告警：队列积压阈值（超过此值触发 P2 告警）
    MAINTAINER_ALERT_QUEUE_THRESHOLD: int = int(os.getenv("MAINTAINER_ALERT_QUEUE_THRESHOLD", "20"))
    # Maintainer 告警推送目标（管理员飞书 open_id，留空则只写日志）
    MAINTAINER_ALERT_FEISHU_OPEN_ID: str = os.getenv("MAINTAINER_ALERT_FEISHU_OPEN_ID", "")
    # Vanguard 额外扫描领域（逗号分隔，追加到默认领域列表）
    VANGUARD_EXTRA_DOMAINS: str = os.getenv("VANGUARD_EXTRA_DOMAINS", "")

    # 路径
    BASE_DIR: Path = BASE_DIR
    DATA_DIR: Path = BASE_DIR / "data"
    MEMORY_DIR: Path = BASE_DIR / "data/memory"       # 预留（当前未启用，主存储用 MySQL）
    KNOWLEDGE_DIR: Path = BASE_DIR / "data/knowledge"  # 预留（知识库文件缓存）
    PROMPTS_DIR: Path = BASE_DIR / "data/prompts"      # 预留（Prompt 模板本地缓存）
    WORKFLOWS_DIR: Path = BASE_DIR / "data/workflows"  # 预留（工作流定义文件）
    LOGS_DIR: Path = BASE_DIR / "data/logs"            # 日志统一目录（已启用）

    def __init__(self):
        # 确保目录存在
        for d in [
            self.DATA_DIR,
            self.MEMORY_DIR,
            self.KNOWLEDGE_DIR,
            self.PROMPTS_DIR,
            self.WORKFLOWS_DIR,
            self.LOGS_DIR,
        ]:
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
