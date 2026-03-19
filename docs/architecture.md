# Omniscientist Claw 系统架构设计文档

> 版本：v3.3 | 更新日期：2026-03-19 | 架构：三层独立部署 + 三层语义存储 + 自主进化与个性化推送 + Maintainer 单点路由 + Worker 并发执行

---

## 目录

1. [架构演进背景](#1-架构演进背景)
2. [v3.0 总体架构概览](#2-v30-总体架构概览)
3. [三层架构详解](#3-三层架构详解)
4. [数据流说明](#4-数据流说明)
5. [LeadResearcher：画像驱动的智能分发](#5-leadresearcher画像驱动的智能分发)
6. [Agent 角色体系](#6-agent-角色体系)
7. [~~MCP Server~~（已移除）](#7-mcp-server已移除)
8. [扩容设计：分布式锁与无状态 Worker](#8-扩容设计分布式锁与无状态-worker)
9. [日志架构](#9-日志架构)
10. [端口规划总表](#10-端口规划总表)
11. [数据库设计](#11-数据库设计)
12. [技术选型理由](#12-技术选型理由)
13. [三层语义存储架构（v3.1 新增）](#13-三层语义存储架构v31-新增)
14. [任务调度架构详解（v3.3 升级：Maintainer 单点路由）](#14-任务调度架构详解v33-升级maintainer-单点路由)
15. [知识检索流程](#15-知识检索流程)
16. [自主进化机制总览（v3.2 新增）](#16-自主进化机制总览v32-新增)
17. [个性化前沿推送：向量匹配 → 飞书主动送达（v3.2 新增）](#17-个性化前沿推送向量匹配--飞书主动送达v32-新增)

---

## 1. 架构演进背景

### 1.1 v2.0 的结构性限制（Skill 集成模式）

v2.0 将整套多 Agent 系统作为 Skill 嵌入到宿主机 OpenClaw 实例（端口 10001）。这种集成存在根本性天花板：

| 问题 | 根因 | 影响 |
|------|------|------|
| **能力倒挂** | OpenClaw 本体是全功能平台，Skill 只是原子工具；将完整编排系统塞进 Skill，架构错配 | 功能受限、调度复杂 |
| **上下文截断** | 飞书消息经 OpenClaw 路由 → 触发 Skill，消息在 OpenClaw 层被处理过一次，科研语义信息大量丢失 | "不够智能" |
| **记忆孤岛** | 用户兴趣画像在本系统 MySQL，OpenClaw 的记忆系统感知不到；两套记忆并行不互通 | 无个性化 |
| **Prompt 堆砌** | 缺少上下文 → 用 Prompt 补充背景 → Prompt 越来越长 → 成本升、效果降 | 运营成本高 |

### 1.2 v3.0 架构目标

- **独立专属**：为科研场景部署一个完全独立的 OpenClaw 实例，专用飞书 App ID，不依赖宿主机
- **画像驱动**：LeadResearcher 每次处理前自动加载用户兴趣画像，让系统"记得用户的研究方向"
- **服务化扩展**：REST API（`http://localhost:10101`）直接对外开放，支持第三方 AI 工具接入
- **横向扩容**：Redis 分布式锁 + 无状态 Worker，支持多机器水平扩展，不重复推送

---

## 2. v3.0 总体架构概览

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                         外部接入层                                            ║
║  ┌──────────────────────────────────────────────────────────────────────┐   ║
║  │     专属 OpenClaw Gateway (Docker: omni-gateway)  Port: 10100        │   ║
║  │   ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │   ║
║  │   │ 飞书 App Bot  │  │  Web UI      │  │  CLI / REST API          │  │   ║
║  │   │ (专属 App ID) │  │  localhost   │  │  /api/*                  │  │   ║
║  │   └──────┬───────┘  └──────┬───────┘  └──────────┬───────────────┘  │   ║
║  │          │  omniscientist-research skill            │                 │   ║
║  └──────────┼──────────────────────────────────────────┼─────────────────┘  ║
╚═════════════╪══════════════════════════════════════════╪════════════════════╝
              │  HTTP (Docker 内网)                       │
              ▼                                           ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║                  LeadResearcher 编排层 (omni-research: 10101 内网)           ║
║                                                                              ║
║  ┌────────────────────────────────────────────────────────────────────────┐  ║
║  │                      LeadResearcher Agent                              │  ║
║  │  ① 加载用户兴趣画像（user_interest_profiles）                            │  ║
║  │  ② 动态构建 System Prompt（画像注入）                                    │  ║
║  │  ③ 并行工具调用（inspect_db_schema / execute_readonly_sql / ...）        │  ║
║  │  ④ 任务分发给 Worker Pool                                               │  ║
║  └──────────────────────────────┬─────────────────────────────────────────┘  ║
║                                 │  Redis 任务队列 (BLPUSH/BLPOP)              ║
╚═════════════════════════════════╪════════════════════════════════════════════╝
                                  │
╔═════════════════════════════════╪════════════════════════════════════════════╗
║              Worker Pool 执行层（v3.3：Maintainer 单点路由）                    ║
║                                 ▼                                            ║
║  ┌────────────────────────────────────────────────────────────────────────┐  ║
║  │  Maintainer 调度器（单一协程，唯一从 Redis Stream 读消息的入口）           │  ║
║  │  ├── 查询 Worker-0/1/2 私有队列长度（负载感知）                          │  ║
║  │  ├── 选择最空闲 Worker                                                  │  ║
║  │  ├── push_task_to_worker(target_id, task)                               │  ║
║  │  └── 立即 XACK（任务已安全交付，消除 Pending，彻底杜绝重复执行）          │  ║
║  └────────────┬───────────────┬──────────────────┬──────────────────────────┘  ║
║               ▼               ▼                  ▼                          ║
║  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐                   ║
║  │  Worker-00    │  │  Worker-01    │  │  Worker-02    │  ... (WORKER_COUNT)║
║  │  私有 List    │  │  私有 List    │  │  私有 List    │                   ║
║  │  Semaphore(5) │  │  Semaphore(5) │  │  Semaphore(5) │                   ║
║  │  5并发槽位    │  │  5并发槽位    │  │  5并发槽位    │                   ║
║  └───────────────┘  └───────────────┘  └───────────────┘                   ║
║  总并发槽位 = WORKER_COUNT × WORKER_CONCURRENCY = 3 × 5 = 15               ║
║                                                                             ║
║  其他自主 Agent（自有调度，不占用 Worker 槽位）                                ║
║  Guardian   Vanguard   Wellspring   Promoter                                ║
║                                                                             ║
║  ┌──────────────┐  ┌───────────────────────────────────────────────────┐   ║
║  │  MySQL       │  │  Redis                                            │   ║
║  │  (任务/画像) │  │  共享 Stream（intake）+ Worker 私有 List（路由后） │   ║
║  └──────────────┘  └───────────────────────────────────────────────────┘   ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

---

## 3. 三层架构详解

### 3.1 第一层：专属 OpenClaw Gateway（omni-gateway）

**职责**：统一接收所有用户输入（飞书、Web、CLI），负责消息路由和初步意图理解。

| 属性 | 说明 |
|------|------|
| 镜像 | `ghcr.io/openclaw/openclaw:latest` |
| 宿主机端口 | `10100`（Gateway Web UI） / `10109`（WebSocket Bridge） |
| 配置目录 | `omni-openclaw-config/` |
| 专属飞书 App | `cli_a939102f4cb81bcc`（与宿主机 openclaw 独立隔离） |
| 核心 Skill | `omniscientist-research`（调用 omni-research API） |

**关键设计**：主 agent 的系统提示通过 `boot-md` hook 注入，来源为 `agents/main/boot.md`。该文件要求 Gateway 在接到科研任务时，始终调用 `omniscientist-research` skill 并传入 `--user feishu:{open_id}` 参数，确保用户身份传递到 LeadResearcher 层实现个性化。

```text
// omni-openclaw-config/agents/main/boot.md（摘要）
- 身份：Omniscientist Claw 科研助手
- 规则：科研任务 → 调用 research.sh，必须传 --user "feishu:{sender_open_id}"
- 简单问候/非科研问题 → 直接回答，不调用 skill
```

### 3.2 第二层：LeadResearcher 编排层（omni-research）

**职责**：画像注入、任务理解、工具调用、任务分发。

| 属性 | 说明 |
|------|------|
| 运行方式 | Python FastAPI（Docker 内网端口 10101） |
| 宿主机暴露 | `127.0.0.1:10102`（仅调试用） |
| 核心 Agent | `LeadResearcher`（详见第 5 节） |
| API 入口 | `POST /api/tasks/lead/execute`（画像驱动模式） |
| 回退 API | `POST /api/tasks/queue/submit`（兜底） |

**数据流**：`omni-gateway 的 research.sh` → `POST http://omni-research:10101/api/tasks/lead/execute` → `LeadResearcher.run(task, user_id)` → Worker Pool

### 3.3 第三层：Worker Pool 执行层（v3.3 重构）

**职责**：Maintainer 单点路由 + Worker 并发执行科研任务，彻底消除重复执行，实现真正的非阻塞并发。

**核心架构变化（v3.3 vs v3.1）**：

| 维度 | v3.1（旧） | v3.3（当前） |
|------|-----------|-------------|
| 任务调度 | 所有 Worker 盲目竞争同一 Redis Stream | **Maintainer 单点调度**，唯一读 Stream 的入口 |
| 重复执行风险 | `STREAM_PENDING_TIMEOUT_MS=2min` << `TASK_TIMEOUT=10min`，同一任务被多 Worker 认领 | **零重复**：Maintainer 立即 XACK，任务交由单一 Worker 私有队列 |
| Worker 执行模式 | 串行：一个 Worker 同时只处理 1 个任务 | **并发**：每个 Worker 通过 Semaphore 同时处理最多 N 个任务 |
| Worker 数量 | 15 个（默认） | **3 个**（每个 5 并发，总槽位 15）|

**各组件职责**：

| 组件 | 实例数 | 职责 |
|------|--------|------|
| Maintainer 调度器 | 1（协程） | 读 Redis Stream → 负载感知路由 → push 到 Worker 私有 List → 立即 XACK |
| WorkerClawer | WORKER_COUNT=3 | 从私有 List 读任务，Semaphore(WORKER_CONCURRENCY=5) 控制并发 |
| Guardian | 1 | 输入/输出安全审核（在每个任务执行时同步调用） |
| Vanguard | 1 | 科研前沿探索，每日推送数据来源（APScheduler 独立调度） |
| Wellspring | 1 | 知识沉淀，任务完成后自动触发（异步后台任务） |
| Promoter | 1 | 成果推广，每日日报生成（APScheduler 独立调度） |

**并发能力**：`WORKER_COUNT(3) × WORKER_CONCURRENCY(5) = 15 个并发槽位`，支持 50+ 用户并发，任务排队时有 ETA 提示。

---

## 4. 数据流说明

### 4.1 飞书消息处理流（v3.3 主路径）

> v3.3 架构升级要点：
> - **Maintainer 单点路由**：不再由所有 Worker 竞争 Stream，彻底消除重复执行
> - **Worker 并发执行**：Semaphore 控制，同时处理多任务而非串行阻塞
> - **STREAM_PENDING_TIMEOUT_MS = 30 分钟**（远大于 TASK_TIMEOUT），防止正在执行的任务被误判为崩溃而重复认领

```
用户在飞书发送消息
      │
      ▼
[1] omni-gateway（专属 OpenClaw）
      │  识别用户意图，提取 open_id
      │  调用 omniscientist-research skill
      │  research.sh → POST /api/tasks/lead/execute
      ▼
[2] LeadResearcher.run(task, user_id=open_id)
      │
      ├─ [2a] _load_user_profile(user_id)
      │        → 查询 user_interest_profiles 表（MySQL）
      │        → 获取研究领域、关键词、历史任务统计
      │
      ├─ [2b] _build_system_prompt(profile)
      │        → 将用户画像注入 System Prompt
      │        → "用户专注于 {domains}，关键词 {keywords}..."
      │
      ├─ [2c] 并行工具调用（asyncio.gather）
      │        → inspect_db_schema / execute_readonly_sql
      │        → get_user_research_profile / arxiv_search
      │        → web_search / ...
      │
      └─ [2d] XADD → Redis Stream（task_stream，共享入队点）
                  │   消息ID = Stream自动生成的时间戳ID
                  ▼
[3] Maintainer 调度器（单一协程，唯一消费 Stream 的入口）
      │  msg_id, task_data = await pop_task(worker_id="maintainer")
      │
      ├─ 查询所有 Worker 私有队列长度（负载感知）
      │     loads = {worker-00: 2, worker-01: 0, worker-02: 3}
      │
      ├─ 选择最空闲 Worker（worker-01，队列最短）
      │
      ├─ push_task_to_worker("worker-01", task_data)
      │     → RPUSH omni_research:worker_queue:worker-01 {...}
      │
      └─ XACK(msg_id)  ← 立即确认，任务已安全交付（不再 Pending）
                  ▼
[4] Worker-01（从私有队列读，Semaphore 控制并发）
      │  task_data = await pop_task_from_worker("worker-01")
      │
      │  await semaphore.acquire()  ← 获取并发槽位（最多5个同时运行）
      │  asyncio.create_task(_run_task())  ← 非阻塞，立即取下一个任务
      │
      ├─ MemoryManager.set_working_context(agent_id, task_id, ctx)
      │     → 保存推理上下文到 Redis Hash（TTL=2h）
      │
      ├─ Guardian 审核输入（trusted 模式/hi omni 跳过）
      ├─ LeadResearcher 深度执行（tool_use 循环，最多 TASK_TIMEOUT 秒）
      ├─ Guardian 审核输出
      ├─ Wellspring 知识沉淀（异步 asyncio.create_task，不阻塞主流程）
      │
      ├─ 结果通过飞书主动推送给用户（send_proactive_feishu）
      └─ semaphore.release()  ← 释放槽位，Worker 可接受新任务
```

### 4.2 工具调用循环（Tool Use Loop）

```
LeadResearcher / WorkerClawer 执行框架：
   while iterations < max_iterations(10):
      │
      ├─ 调用 OpenAI/OpenRouter API（chat.completions）
      │      system_prompt（含用户画像）+ messages + tools
      │
      ├─ finish_reason == "stop" → 提取文本，结束
      │
      └─ finish_reason == "tool_calls" →
            ├─ 遍历 tool_calls
            ├─ execute_skill(name, args)
            ├─ 结果截断为 8000 字符（防 context overflow）
            └─ 追加 tool 消息，继续下一轮
```

### 4.3 进化循环（每日自动推送）

> v3.1 升级点：用户匹配从**逐用户 LLM 语义判断**升级为**ChromaDB 向量批量匹配**，速度提升 10x，API 消耗降为 0。

```
APScheduler（每日 EVOLUTION_EMAIL_HOUR:07 CST）
      │
      ├─ [分布式锁] acquire_lock("evolution_daily_lock", ttl=3600s)
      │     若已被其他实例获取 → 跳过（防重推）
      │
      ├─ Vanguard 扫描全球科研前沿（arXiv / GitHub / 学术社区）
      │     └─ 同步写入 ChromaDB papers 集合（upsert_paper）
      │
      ├─ [v3.1 新] vector_store.find_matching_users(frontier_content)
      │     → ChromaDB user_interests 集合余弦相似度检索
      │     → 返回 distance ≤ 0.7 的用户列表（毫秒级，无 LLM 调用）
      │     → 降级：若 ChromaDB 不可用，回退到 MySQL 领域关键词匹配
      │
      ├─ 对每个匹配用户：
      │     ├─ [用户级锁] is_notification_sent(user_id, content_hash)
      │     │    已发过 → 跳过
      │     ├─ 发送飞书消息 + 邮件日报
      │     └─ mark_notification_sent(user_id, content_hash)
      │
      └─ release_lock("evolution_daily_lock")

APScheduler（每日 01:00 CST）
      └─ cleanup_old_logs(retain_days=30)  清理历史日志
```

---

## 5. LeadResearcher：画像驱动的智能分发

### 5.1 设计动机

过去每次对话都需要用户重新介绍自己的研究背景，是从"问答工具"到"科研伙伴"的最大障碍。LeadResearcher 将用户画像前置注入，让系统"记得你的研究方向"。

### 5.2 用户画像加载（_load_user_profile）

```python
async def _load_user_profile(self, user_id: str) -> dict:
    # 从 user_interest_profiles 表加载
    profiles = await 查询(UserInterestProfile, user_id=user_id)
    domains   = [p.domain for p in profiles]          # 研究领域
    keywords  = 去重合并([p.keywords for p in profiles])  # 关键词
    task_count = await 统计(TaskMetric, user_id=user_id)   # 历史任务数
    return {
        "user_id":   user_id,
        "domains":   domains[:5],        # 最多 5 个领域
        "keywords":  keywords[:15],      # 最多 15 个关键词
        "task_count": task_count,
        "recent_interest": domains[0],   # 最近活跃领域
        "has_profile": len(profiles) > 0,
    }
```

### 5.3 动态 System Prompt 构建

```
基础 System Prompt（角色定义 + 工具使用规范）
           +
用户画像注入（如画像存在）：
   "## 当前用户科研背景
    - 研究领域：{domains}
    - 关键词：{keywords}
    - 历史任务：{task_count} 次
    - 最近活跃：{recent_interest}
    请据此个性化回应，无需用户重复介绍背景。"
```

### 5.4 并行工具调用

LeadResearcher 使用 `asyncio.gather` 并行调用多个工具，减少等待时间：

```python
results = await asyncio.gather(
    arxiv_search(keywords),
    execute_readonly_sql("SELECT ... FROM tasks WHERE user_id = ?"),
    get_user_research_profile(user_id),
    return_exceptions=True
)
```

---

## 6. Agent 角色体系

### 6.1 角色分工

| 角色 | 模块 | 职责 | 调度方式 |
|------|------|------|----------|
| **Maintainer 调度器** | `core/worker_pool.py` | **单点路由**：唯一读共享 Stream，负载感知分配任务到 Worker 私有队列 | WorkerPool 内协程 |
| LeadResearcher | `agents/lead_researcher.py` | 画像注入 + 任务编排 + 工具调用 | 每个任务执行时调用 |
| WorkerClawer | `agents/worker_clawer.py` | 通用科研任务执行（54 种技能），Semaphore 控制并发 | Maintainer 路由 + Semaphore(5) |
| Guardian | `agents/guardian.py` | 输入/输出安全审核（每个任务同步调用） | 任务执行流程内 |
| Vanguard | `agents/vanguard.py` | 科研前沿主动探索 | APScheduler 每日调度 |
| Wellspring | `agents/wellspring.py` | 知识沉淀与社区共识 | 任务完成后异步触发 |
| Promoter | `agents/promoter.py` | 成果推广 + 每日日报 | APScheduler 每周调度 |

### 6.2 技能分类（54 种）

| 分类 | 技能示例 |
|------|----------|
| 搜索类 | `web_search`, `arxiv_search`, `github_search` |
| 文献类 | `paper_read`, `paper_compare`, `citation_trace` |
| 数据库类 | `inspect_db_schema`, `execute_readonly_sql`, `get_user_research_profile` |
| 代码类 | `code_execute`, `code_review`, `algorithm_analyze` |
| 学术类 | `experiment_design`, `data_analyze`, `hypothesis_generate` |
| 写作类 | `abstract_write`, `paper_outline`, `academic_polish` |
| 系统类 | `system_check`, `send_email`, `feishu_notify` |
| 通知类 | `proactive_notify`, `research_digest` |

---

## 7. ~~MCP Server~~（已移除）

> MCP Server（`mcp_server/`，端口 10150）已于 v3.1 从代码库中移除。
>
> **原因**：系统通信主路为 `omni-gateway → research.sh(curl) → FastAPI(:10101)`，MCP Server 是独立附加服务，在实际业务中无流量接入，保留只增加维护成本。
>
> 如需将科研能力对接 Cursor / Claude Desktop，可直接调用 `http://localhost:10101` 的 REST API，或通过飞书 Bot 入口访问。

---

## 8. 扩容设计：分布式锁与无状态 Worker

### 8.1 多实例扩容模式

```
负载均衡 / Nginx
      │
      ├── omni-research 实例 A（Mac Studio 主机）
      ├── omni-research 实例 B（扩容机器）
      └── omni-research 实例 C（扩容机器）
            │
            ▼ 共享
      Redis（任务队列 + 分布式锁 + 心跳）
      MySQL（任务记录 + 用户画像 + 知识库）
```

### 8.2 无状态 Worker 设计（v3.3 更新）

- **任务状态**：全部持久化到 MySQL + Redis，Worker 本身不保存任何状态
- **任务分发**：~~Redis BLPOP（v3.0，已废弃）~~ → **Maintainer 单点路由到 Worker 私有 Redis List**，每个任务只被一个 Worker 处理
- **并发控制**：每个 Worker 使用 `asyncio.Semaphore(WORKER_CONCURRENCY)` 控制最大并发任务数，`asyncio.create_task()` 实现非阻塞并发执行
- **心跳机制**：每个 Worker 轮询时向 Redis 写入心跳（TTL=30s），Maintainer 可感知 Worker 存活状态
- **Key 前缀**：`REDIS_KEY_PREFIX`（默认 `omni_research`）区分不同系统，共享宿主机 Redis 时避免 key 冲突
- **进程内去重**：`WorkerPool._active_task_ids` Set 提供兜底去重，防止极端情况下同一 task_id 被重复执行

### 8.3 分布式锁防重推

```python
# core/cache.py
async def acquire_lock(key: str, ttl: int = 60) -> bool:
    """Redis SET NX EX 原子操作"""
    return await redis.set(f"lock:{key}", INSTANCE_ID, nx=True, ex=ttl)

async def release_lock(key: str) -> None:
    await redis.delete(f"lock:{key}")

# 防止重复发送通知
async def is_notification_sent(user_id: str, content_hash: str) -> bool:
    key = f"notif:{user_id}:{content_hash}"
    return await redis.exists(key)

async def mark_notification_sent(user_id: str, content_hash: str, ttl: int = 86400 * 7):
    key = f"notif:{user_id}:{content_hash}"
    await redis.set(key, "1", ex=ttl)
```

---

## 9. 日志架构

### 9.1 统一日志设计

所有模块日志统一输出到 `data/logs/`（项目目录内，不分散到 `~/.openclaw/`）。

```
data/logs/
├── app.log               # 应用主日志（orchestrator 实例）
├── app.2026-03-16.log    # 历史日志（自动命名，保留 30 天）
├── feishu.log            # 飞书 Bot 专属日志
├── worker.log            # Worker Pool 任务执行日志
├── evolution.log         # 进化循环 + 每日推送日志
├── error.log             # 全局 ERROR 级别（所有模块）
├── access.log            # HTTP 访问日志（uvicorn.access）
└── startup.log           # 启动日志（nohup 输出）
```

### 9.2 日志级别分配

| 模块 | 文件级别 | 控制台级别 | 原因 |
|------|----------|------------|------|
| 所有模块 | `INFO`（默认） / `DEBUG`（DEBUG=true） | `INFO` | 标准运营 |
| `core.evolution_loop` | `INFO` | `INFO` | 推送记录完整保留 |
| `channels.*`（飞书/钉钉） | `INFO` | `INFO` | 消息收发完整记录 |
| 第三方库（httpx/sqlalchemy 等） | `WARNING` | `WARNING` | 降噪 |
| 全局 error.log | `ERROR` | — | 快速定位问题 |

### 9.3 日志滚动与清理

- **滚动周期**：每日午夜（`when="midnight"`）自动滚动
- **历史命名**：`app.2026-03-16.log`（日期后缀格式）
- **保留策略**：`backupCount=30`（30 天）
- **自动清理**：APScheduler 每日 01:00 调用 `cleanup_old_logs(30)`（双重保障）
- **手动清理**：`./start.sh clean-logs [天数]`

### 9.4 使用方式

```python
# 所有模块统一使用
from core.logging_config import get_logger
logger = get_logger(__name__)

# 示例输出格式
# 2026-03-17 14:23:01 | INFO     | agents.lead_researcher        | [LeadResearcher] 用户 ou_xxx 画像加载完成: domains=3 keywords=12
```

---

## 10. 端口规划总表

### 10.1 本系统端口（10100-10199 专用段）

| 端口 | 服务 | 部署方式 | 说明 |
|------|------|----------|------|
| **10100** | 专属 OpenClaw Gateway Web UI | Docker `omni-gateway` | 飞书 Bot 入口 / 控制台页面 |
| **10101** | Research API（本地 / Docker 内网） | Local / Docker 内网 | Web UI + 任务 API + Worker Pool |
| **10102** | Research API 调试暴露 | Docker → 宿主机 | 仅 `127.0.0.1:10102`，调试用 |
| **10109** | OpenClaw Bridge（WebSocket） | Docker `omni-gateway` | WebSocket 长连接 Bridge |
| ~~10150~~ | ~~MCP Server~~（已移除） | — | v3.1 移除，无实际流量 |
| 10110-10134 | Worker 子实例（v2.0 遗留，v3.0 废弃） | — | v3.0 已改为单进程 Worker Pool |

### 10.2 宿主机其他服务（不干扰）

| 端口 | 服务 | 说明 |
|------|------|------|
| 3306 | MySQL | 本系统使用（host.docker.internal） |
| 6379 | Redis | 本系统使用（host.docker.internal） |

---

## 11. 数据库设计

使用 MySQL（`host.docker.internal:3306`），SQLAlchemy 异步引擎。ChromaDB 作为独立向量库并行存储，通过 `VectorSyncLog` 追踪同步状态。

### 11.1 核心数据表

| 表名 | 用途 | 关键字段 |
|------|------|----------|
| `users` | 用户基础信息 | id, name, feishu_open_id, email |
| `user_interest_profiles` | 用户科研兴趣画像 | user_id, domain, keywords, weight |
| `tasks` | 任务执行记录 | id, user_id, title, status, assigned_agent, result |
| `task_metrics` | 任务统计指标 | user_id, task_count, avg_duration |
| `knowledge` | 知识库条目（结构化主存储） | id, title, content, category, quality_score |
| `proactive_notifications` | 主动推送记录（防重） | user_id, content_hash, sent_at |
| `prompt_templates` | Prompt 模板管理 | id, role, template, version |
| `vector_sync_log` | **v3.1 新增**：MySQL→ChromaDB 同步追踪 | entry_type, entry_id, collection, synced_at |

### 11.2 user_interest_profiles 表（核心）

LeadResearcher 画像注入的数据来源，是系统"懂用户"的核心数据资产。v3.1 起，用户兴趣同步双写到 ChromaDB `user_interests` 集合，供向量检索使用。

```sql
CREATE TABLE user_interest_profiles (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    user_id     VARCHAR(128) NOT NULL,           -- 飞书 open_id 或系统 user_id
    domain      VARCHAR(128) NOT NULL,           -- 研究领域（如 "深度学习", "材料科学"）
    keywords    TEXT,                            -- JSON 数组，关键词列表
    weight      FLOAT DEFAULT 1.0,              -- 兴趣权重（越高越活跃）
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user_id (user_id),
    INDEX idx_domain (domain)
);
```

### 11.3 vector_sync_log 表（v3.1 新增）

追踪 MySQL 条目与 ChromaDB 向量库的同步状态，支持增量同步和冷启动数据预热。

```sql
CREATE TABLE vector_sync_log (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    entry_type  VARCHAR(32)  NOT NULL,           -- 条目类型：'knowledge' | 'paper' | 'user'
    entry_id    VARCHAR(128) NOT NULL,           -- 对应 MySQL 表的主键 ID
    collection  VARCHAR(64)  DEFAULT 'knowledge',-- ChromaDB 集合名
    synced_at   DATETIME     DEFAULT CURRENT_TIMESTAMP,
    UNIQUE INDEX ix_vsync_entry (entry_type, entry_id)  -- 防重复同步
);
```

**使用场景**：
- 应用启动时，`VectorStore.sync_from_mysql()` 查询未在此表中记录的 `KnowledgeEntry` 条目，批量嵌入到 ChromaDB（冷启动预热，最多 500 条/次）
- 新知识写入时，同步写入此表，避免重复嵌入

---

## 12. 技术选型理由

| 技术 | 选型 | 版本 | 理由 |
|------|------|------|------|
| 接入层 | OpenClaw（Docker） | latest | 原生支持飞书/钉钉/Web 多渠道；自带记忆与 Prompt 系统 |
| 编排框架 | Python FastAPI + asyncio | ≥0.110 | 原生异步；Pydantic 数据校验；OpenAPI 文档自动生成 |
| AI 调用 | OpenAI API + OpenRouter | — | 直接 function calling 协议；多模型路由 |
| 任务队列 | **Redis Streams** (v3.1 升级) | ≥7.0 | 可靠交付 + ACK 机制 + Consumer Group 负载均衡；崩溃恢复（见第 14 节） |
| ~~任务队列~~ | ~~Redis BLPOP~~ (v3.0 已废弃) | — | ~~原子性；无序列化日志；Worker 崩溃任务丢失~~ |
| 向量存储 | **ChromaDB** (v3.1 新增) | ≥0.5 | 本地持久化；内置 all-MiniLM-L6-v2 嵌入；余弦相似度检索；无需独立服务 |
| 结构化数据库 | MySQL + SQLAlchemy async | ≥8.0 | 异步；可无缝切换；与宿主机共用已有实例 |
| 分布式锁 | Redis SET NX EX | — | 原子操作；轻量；天然过期（防死锁） |
| 记忆抽象 | MemoryManager (`core/memory.py`) | v3.1 新增 | 统一 Working/Episodic/Semantic 三层接口，各层降级友好 |
| ~~服务化~~ | ~~MCP Server（FastAPI）~~（已移除） | — | v3.1 移除；REST API(:10101) 可直接对接 |
| 日志 | Python logging + TimedRotatingFileHandler | — | 标准库；日滚动；按模块分文件；30 天自动清理 |
| 容器化 | Docker Compose | — | 环境隔离；三服务独立扩缩；端口管理清晰 |
| 启动管理 | start.sh（本地 + Docker 统一） | — | 一脚本管两种模式；端口冲突检测；日志路径统一 |

---

## 13. 三层语义存储架构（v3.1 新增）

### 13.1 升级动机

v3.0 的存储层存在三个系统性瓶颈：

| 问题 | 根因 | v3.1 解法 |
|------|------|-----------|
| **任务可靠性低** | Redis BLPOP：Worker 崩溃消息永久丢失 | Redis Streams + Consumer Group ACK 机制 |
| **知识检索语义缺失** | MySQL 只能关键词过滤，无法理解语义相近的概念 | ChromaDB 向量存储 + 余弦相似度检索 |
| **用户匹配成本高** | Evolution Loop 逐用户调用 LLM 判断兴趣相关性 | ChromaDB `user_interests` 集合向量批量匹配 |

### 13.2 架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                     OpenClaw 三层语义存储架构                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Layer 1 · Working Memory（工作记忆）                         │   │
│  │  存储：Redis Hash    TTL：2 小时                              │   │
│  │  用途：单次任务生命周期内的推理链、工具结果、中间状态             │   │
│  │  Key：{prefix}:wm:{agent_id}                                 │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Layer 2 · Episodic Memory（情景记忆）                        │   │
│  │  存储：MySQL（TaskRecord 等表）  TTL：永久                     │   │
│  │  用途：任务历史、用户交互记录、可结构化查询的业务数据              │   │
│  │  查询：关键词 LIKE + 时间倒序                                  │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Layer 3 · Semantic Memory（语义记忆）                        │   │
│  │  存储：ChromaDB（本地持久化，data/chroma/）TTL：永久           │   │
│  │  三个 Collection：                                            │   │
│  │    ● knowledge     ← Wellspring 知识条目（研究方法/实验经验）  │   │
│  │    ● papers        ← Vanguard 学术论文（arXiv/Semantic Scholar）│  │
│  │    ● user_interests← 用户兴趣聚合向量（Evolution Loop 匹配）  │   │
│  │  查询：余弦相似度向量检索（all-MiniLM-L6-v2 嵌入）             │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  MemoryManager（core/memory.py）                             │   │
│  │  统一三层访问接口，各层独立降级，互不影响                       │   │
│  │   remember(content, layer)  →  写入指定层                     │   │
│  │   recall(query, layers)     →  跨层语义检索                   │   │
│  │   set/get_working_context   →  Working Memory 管理            │   │
│  │   search_papers(query)      →  论文向量检索                   │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 13.3 各层职责与生命周期

#### Working Memory（工作记忆）
- **载体**：Redis Hash，Key = `{prefix}:wm:{agent_id}`
- **TTL**：2 小时（任务结束后自动过期，也可主动清除）
- **写入时机**：Worker 取到任务后，`MemoryManager.set_working_context()` 立即写入
- **读取时机**：Worker 多轮推理中途需要恢复上下文时读取
- **存储内容**：`{ task_id, step, partial_result, tool_outputs, ... }`

#### Episodic Memory（情景记忆）
- **载体**：MySQL `TaskRecord` 表等业务表
- **TTL**：永久保留（业务级别可按需清理）
- **写入时机**：任务完成/失败时，Orchestrator 写入任务记录
- **读取时机**：`MemoryManager.recall(include_episodic=True)` 追加历史任务上下文
- **存储内容**：任务标题、执行状态、完成时间、输出摘要

#### Semantic Memory（语义记忆）
- **载体**：ChromaDB PersistentClient，路径 `data/chroma/`
- **TTL**：永久保留
- **写入时机**：
  - Wellspring `ingest_task_result` → 知识条目双写（MySQL + ChromaDB `knowledge`）
  - Vanguard `explore_frontier` → 论文嵌入（ChromaDB `papers`）
  - `interest_extractor.upsert_user_interest` → 用户兴趣更新（ChromaDB `user_interests`）
- **读取时机**：知识检索、论文搜索、用户匹配（见第 15 节）
- **嵌入模型**：默认 `all-MiniLM-L6-v2`（本地，零 API 成本），可切换 `text-embedding-3-small`（OpenAI）

### 13.4 MySQL 与 ChromaDB 的数据关系

```
MySQL (结构化主存储)          ChromaDB (语义向量索引)
─────────────────────        ─────────────────────────
KnowledgeEntry               ← → knowledge 集合
  id, title, content               document = title + content
  category, quality_score          metadata = {source, category, quality, ts}

user_interest_profiles       ← → user_interests 集合
  user_id, domain, keywords        document = 领域+关键词聚合文本
  weight                           metadata = {user_id, updated_at}

（论文来源于 Vanguard）       ← → papers 集合
  （无对应 MySQL 表，仅向量）       document = title + abstract
                                   metadata = {title, authors, year, domain, url}

VectorSyncLog                       ↑ 追踪同步状态
  entry_type + entry_id       确认已嵌入，防止重复同步
```

### 13.5 冷启动同步机制

应用每次启动（`api/main.py` lifespan）依次执行：

```
1. init_task_stream()         ← 确保 Redis Streams + Consumer Group 存在
2. VectorStore.initialize()   ← 初始化 ChromaDB 三集合（幂等）
3. VectorStore.sync_from_mysql(limit=500)
       ↓
   SELECT * FROM knowledge_entries
   WHERE id NOT IN (SELECT entry_id FROM vector_sync_log WHERE entry_type='knowledge')
   ORDER BY created_at DESC LIMIT 500
       ↓
   对每条记录：upsert_knowledge() + INSERT INTO vector_sync_log
```

**设计意图**：即使 ChromaDB 数据目录被清空（如迁移服务器），重启后系统能自动从 MySQL 重建向量索引，无需人工干预。

---

## 14. 任务调度架构详解（v3.3 升级：Maintainer 单点路由）

### 14.1 架构演进对比

| 特性 | v3.0 BLPOP | v3.1 Redis Streams（旧） | v3.3 Maintainer 路由（当前） |
|------|-----------|------------------------|---------------------------|
| 任务调度入口 | 所有 Worker 竞争 BLPOP | 所有 Worker 竞争 XREADGROUP | **Maintainer 单一协程，唯一入口** |
| 重复执行风险 | 无（BLPOP 原子性） | **高**：STREAM_PENDING_TIMEOUT(2min) << TASK_TIMEOUT(10min)，多 Worker 误认领同一任务 | **零**：Maintainer 立即 XACK，消息不进入 Pending |
| Worker 执行模式 | 串行（1任务/Worker） | 串行（1任务/Worker） | **并发**：Semaphore 控制，N任务/Worker |
| 负载均衡策略 | BLPOP 天然轮询 | Consumer Group 轮询 | **负载感知**：Maintainer 查询队列长度，选最空闲 Worker |
| Worker 崩溃恢复 | 消息丢失 | XPENDING + XCLAIM | Maintainer XACK 后任务在 Worker 私有 List，容器级崩溃才丢失 |
| 任务历史审计 | 无 | Stream MAXLEN 10,000 条 | Stream + MySQL TaskRecord 双重持久化 |

### 14.2 Redis 数据结构

**共享 Stream（任务入队）**：
```
Key：{prefix}:task_stream
Consumer Group：workers
Consumer（Maintainer）：maintainer

消息格式（XADD）：
{
  "task_id":    "uuid-xxxx",
  "task":       "给我做一个深度研究...",
  "user_id":    "feishu:ou_xxxx",
  "channel":    "feishu",
  "reply_info": {"channel": "feishu", "open_id": "ou_xxxx"},
  "created_at": "2026-03-19T10:00:00+08:00"
}
```

**Worker 私有队列（Maintainer 路由后）**：
```
Key：{prefix}:worker_queue:{worker_id}   （Redis List，RPUSH/BLPOP）
示例：omni_research:worker_queue:worker-01

Maintainer 路由时执行：
  RPUSH omni_research:worker_queue:worker-01 {task_json}

Worker 取任务时执行：
  BLPOP omni_research:worker_queue:worker-01 5  (阻塞等待 5秒)
```

### 14.3 完整消息生命周期（v3.3）

```
[入队] LeadResearcher / API 调用 push_task()
      │
      │  XADD {prefix}:task_stream MAXLEN ~ 10000 * {task_data}
      ▼
  共享 Stream（已投递，等待 Maintainer 消费）
      │
      │  Maintainer: XREADGROUP GROUP workers maintainer COUNT 1 BLOCK 5000
      ▼
  Maintainer 调度器
      ├── 查询所有 Worker 私有队列长度
      │     GET llen(worker_queue:worker-00) = 2
      │     GET llen(worker_queue:worker-01) = 0  ← 最空闲
      │     GET llen(worker_queue:worker-02) = 3
      │
      ├── RPUSH worker_queue:worker-01 {task_data}  ← 路由到最空闲 Worker
      │
      └── XACK {msg_id}  ← 立即确认（消息不进入 Pending 状态）
                  ▼
  Worker-01 私有队列（Redis List，安全等待执行）
      │
      │  Worker-01: BLPOP worker_queue:worker-01 5
      ▼
  await semaphore.acquire()  ← 获取并发槽位（Semaphore(5)，最多5个同时运行）
      │
      │  asyncio.create_task(_run_task())  ← 非阻塞，立即继续取下一个任务
      ▼
  [并发任务执行]
      ├─ Guardian 审核 → LeadResearcher 执行 → 结果推送
      └─ semaphore.release()  ← 释放槽位
```

### 14.4 重复执行问题根因与修复记录

> **历史 Bug（v3.1）**：用户提交写论文任务，收到多篇重复结果。

**根因**：
```
STREAM_PENDING_TIMEOUT_MS = 120,000ms（2分钟）
TASK_TIMEOUT = 600s（10分钟）

写论文任务执行 2 分钟后：
  → 任务仍在 PENDING（Worker 正在处理，未 XACK）
  → 14 个空闲 Worker 每 5 秒调用 reclaim_stale_tasks()
  → 全部尝试 XCLAIM 同一条 Pending 消息
  → 多个 Worker 成功认领 → 同一任务被执行 N 次
  → 用户收到 N 篇论文
```

**v3.3 修复**：
```
1. 架构根治：Maintainer 立即 XACK → 任务永远不进入 Pending 状态
             → reclaim_stale_tasks() 永远找不到需要认领的消息

2. 参数兜底：STREAM_PENDING_TIMEOUT_MS = 1,800,000ms（30分钟）
             远大于 TASK_TIMEOUT（600s），即使 Maintainer 崩溃也安全

3. 进程内去重：WorkerPool._active_task_ids Set 兜底去重
               防止极端情况下同一 task_id 被重复执行
```

### 14.5 Worker 并发模型

```python
# core/worker_pool.py — _worker_loop()
async def _worker_loop(self, worker):
    """
    每个 Worker 独立运行此循环：
    - Semaphore 控制最大并发任务数
    - asyncio.create_task 实现非阻塞并发（不等上一个任务完成）
    """
    semaphore = asyncio.Semaphore(settings.WORKER_CONCURRENCY)  # 默认 5

    while self._running:
        await semaphore.acquire()  # 等待空闲槽位（如果5个都忙则阻塞）

        task_data = await pop_task_from_worker(worker.agent_id, timeout=5)
        if not task_data:
            semaphore.release()  # 无任务，释放槽位
            continue

        # 非阻塞：立即启动任务协程，不等待执行完成
        asyncio.create_task(
            self._run_task_with_cleanup(semaphore, worker, task_data)
        )
        # 继续循环，可立即取下一个任务（只要 semaphore > 0）
```

**并发容量规划**：

| 配置 | 值 | 说明 |
|------|-----|------|
| `WORKER_COUNT` | 3 | Worker 进程数（`.env.omniscientist`） |
| `WORKER_CONCURRENCY` | 5 | 每个 Worker 最大并发任务数（Semaphore） |
| 总并发槽位 | 15 | `3 × 5`，支持 15 个任务同时执行 |
| 支持用户数 | 50+ | 超过 15 个任务时队列排队，有 ETA 提示 |

### 14.6 队列状态监控 API

```
GET /api/queue/status

返回：
{
  "queue": {
    "stream_length":  150,          // 共享 Stream 中总消息数（含已处理的审计日志）
    "pending_count":   0,           // Pending（待 ACK）消息数（正常应接近 0）
    "consumer_group": "workers"
  },
  "vector_store": {
    "ready": true,
    "collections": {
      "knowledge":      2048,       // 知识条目数
      "papers":         512,        // 论文数
      "user_interests": 87          // 用户向量数
    }
  }
}
```

> **监控提示**：`pending_count` 在 v3.3 中正常应接近 0（Maintainer 立即 XACK）。如果持续 > 0 说明 Maintainer 调度器可能异常，需检查 `[Maintainer]` 相关日志。

---

## 15. 知识检索流程

### 15.1 检索架构总览

知识检索遵循"**语义优先、关键词兜底、内存缓存最后**"三级策略，确保在任意组件不可用时系统依然可用。

```
检索请求（query）
      │
      ▼
[1] ChromaDB 语义检索（主路径）
      │  VectorStore.search_knowledge(query, top_k=5)
      │  → 文本嵌入 → 余弦相似度 → Top-K 结果
      │
      ├─ 结果 ≥ min_threshold → 直接返回
      │
      └─ 结果不足 OR ChromaDB 不可用
               │
               ▼
          [2] Redis 标签检索（二级）
               │  cache.get_knowledge_by_tags(keywords)
               │  → 从 Redis Hash 中按标签精确匹配
               │
               └─ 仍不足 OR Redis 不可用
                        │
                        ▼
                   [3] 内存关键词匹配（保底）
                        → 对 LLM 上下文中已有知识碎片做 in-memory 过滤
```

### 15.2 Wellspring 知识双写流程

每次 `ingest_task_result()` 调用时，知识同时写入 MySQL 和 ChromaDB：

```
任务完成 → Wellspring.ingest_task_result(task_result)
      │
      ├─ [写入 MySQL] INSERT INTO knowledge (title, content, category, quality_score)
      │
      ├─ [异步] VectorStore.upsert_knowledge(entry_id, content, metadata)
      │          → ThreadPoolExecutor 中运行 ChromaDB upsert（不阻塞主线程）
      │          → document = title + "\n\n" + content（截断至 2000 字符）
      │          → metadata = {source, category, quality, ts}
      │
      └─ [返回] 无论 ChromaDB 是否成功，MySQL 写入已完成，不影响主流程
```

### 15.3 论文知识检索流程

```
用户查询 / LeadResearcher 工具调用
      │
      ▼
MemoryManager.search_papers(query, top_k=8, domain=None)
      │
      └─ VectorStore.search_papers(query, domain_filter)
              │  ChromaDB papers 集合 → 余弦相似度检索
              │  可按 domain 过滤（如 "machine_learning"）
              │
              ▼
         返回：[{title, abstract[:400], score, metadata}]
              │  score = 1.0 - cosine_distance（越高越相关）
              │
              └─ LeadResearcher 将论文摘要注入 System Prompt
                   → 辅助生成更高质量的科研内容
```

### 15.4 用户兴趣向量更新流程

每次用户提交任务后，`interest_extractor` 自动更新用户画像：

```
任务完成 → interest_extractor.extract_and_update(user_id, task_content)
      │
      ├─ LLM 提取研究领域和关键词
      │
      ├─ [写入 MySQL] UPSERT user_interest_profiles
      │
      └─ VectorStore.upsert_user_interest(user_id, interest_text)
              │  interest_text = 领域 + 关键词聚合文本
              │  向量 ID = MD5(user_id)（规避特殊字符）
              ▼
         ChromaDB user_interests 集合更新
              ↓
         下次 Evolution Loop 运行时，find_matching_users() 使用新向量匹配
```

### 15.5 跨层记忆检索（MemoryManager.recall）

```python
# 使用示例
mem = get_memory_manager()

# 仅语义层（默认）
results = await mem.recall("Transformer 注意力机制优化", top_k=5)
# → ChromaDB knowledge 集合余弦检索

# 语义 + 情景双层
results = await mem.recall("用户上次数字孪生实验结果", include_episodic=True)
# → ChromaDB 结果 + MySQL TaskRecord 关键词匹配
# → 按 score 合并排序返回

# 结果结构
# [
#   {"content": "...", "source": "semantic", "score": 0.87, "metadata": {...}},
#   {"content": "...", "source": "episodic", "score": 0.60, "metadata": {...}},
# ]
```

### 15.6 降级策略与容错

| 组件 | 不可用时的降级行为 | 影响 |
|------|-------------------|------|
| ChromaDB 初始化失败 | `_ready=False`，所有方法返回空列表/False | 语义检索功能降级为关键词匹配 |
| ChromaDB upsert 失败 | 记录警告日志，主流程继续 | 该条知识未向量化，不影响 MySQL 写入 |
| Redis 不可用 | Working Memory 操作静默失败 | 当次任务无上下文缓存，LLM 从零开始推理 |
| MySQL 不可用 | 抛出异常，任务标记失败 | 核心业务中断（MySQL 是主存储，不可跳过）|

---

## 16. 自主进化机制总览（v3.2 新增）

OpenClaw 的多 Agent 系统设计上不是"工具集"，而是一个具备**持续自主进化**能力的闭合系统。
以下五个维度协同工作，让系统随使用时间推移变得越来越聪明、越来越懂用户。

### 16.1 五维自主进化矩阵

| 维度 | 主责 Agent | 触发时机 | 作用 |
|------|-----------|---------|------|
| **知识库自增长** | Vanguard + Wellspring | 每日 08:10 / 20:10 CST | 将全球最新 arXiv/GitHub 前沿写入 ChromaDB `papers`，并沉淀到 `knowledge` |
| **用户画像静默学习** | InterestExtractor | 每次任务成功后异步触发 | 提取任务输入/输出的研究领域和关键词，累积写入 MySQL `user_interest_profiles` + ChromaDB `user_interests` |
| **Guardian 策略自演化** | Guardian | 每周日 23:00 CST | 分析上周所有 rejected/escalated 任务模式，自动调整拦截策略，减少误判 |
| **Promoter 内容自生产** | Promoter | 每周二 10:00 CST | 读取 Vanguard 前沿报告，自动起草科普推文（存入 Redis），供运营直接使用 |
| **Maintainer 系统自愈** | Maintainer + TaskWatchdog | 每 60s / 每 10min / 23:30 CST | 超时任务强制终止 + 飞书告警、系统健康检查、每日报告 |

### 16.2 进化闭环数据流

```
用户提交任务
    │
    ▼
WorkerPool 执行 → LeadResearcher 完成
    │                   │
    │                   ▼
    │         InterestExtractor.extract_and_update_interests()
    │             │ 分析任务 input/output
    │             ↓
    │         MySQL user_interest_profiles（累积权重）
    │         ChromaDB user_interests（更新向量）
    │
    ▼
任务结果存入 MySQL TaskRecord
    │
    ▼
Wellspring.ingest_task_result()（异步）
    │ 沉淀成社区知识
    ▼
ChromaDB knowledge + MySQL KnowledgeEntry
    │
    ▼（每日 08:10 / 20:10）
Vanguard.explore_frontier(domain)
    │ arXiv / GitHub 最新成果
    ▼
ChromaDB papers（向量化论文）
Redis frontier:{domain}:{date}（快速缓存）
    │
    ▼  ← 本版本新增打通的环节
_push_frontier_to_users(domain, frontier_text)
    │ ChromaDB user_interests 向量检索
    │ 找到兴趣匹配用户（cosine distance ≤ 0.55）
    │ Redis 去重锁（每用户每领域每天一次）
    ▼
飞书主动推送个性化前沿摘要 → 用户"无感惊喜"
```

### 16.3 定时任务完整排期

| Job | Cron（CST）| 锁 TTL | 说明 |
|-----|----------|--------|-----|
| `vanguard_morning` | 每日 08:10 | 7200s | AI/CS/量子/机器人 领域前沿扫描 |
| `vanguard_evening` | 每日 20:10 | 7200s | 生命科学/材料/交叉学科 前沿扫描 |
| `wellspring_synthesis` | 每日 02:30 | 3600s | 知识蒸馏与社区共识提炼 |
| `wellspring_digest` | 每周一 01:00 | 3600s | 生成周报摘要（管理员飞书推送）|
| `promoter_content` | 每周二 10:00 | 3600s | 自动生成科普推文草稿 |
| `guardian_review` | 每周日 23:00 | 3600s | 拦截策略自演化（分析拒绝/升级模式）|
| `maintainer_watchdog` | 每 60s | — | 超时任务强制终止 + 用户通知 |
| `maintainer_health` | 每 10min | — | 系统健康度检查 + 异常告警 |
| `maintainer_report` | 每日 23:30 | — | 系统日报（管理员）|

---

## 17. 个性化前沿推送：向量匹配 → 飞书主动送达（v3.2 新增）

这是当前系统**价值最高的"惊喜功能"**：用户什么都不用做，系统每晚扫描完前沿后自动识别"谁对这个方向感兴趣"，并把最新进展直接推送到用户飞书。

### 17.1 完整实现链路

```
_vanguard_scan_domains()
  ├─ vanguard.explore_frontier(domain)   → frontier_text
  ├─ Redis cache_knowledge(...)          → 缓存供搜索
  ├─ wellspring.ingest_task_result(...)  → 知识沉淀（异步）
  └─ asyncio.create_task(
       _push_frontier_to_users(domain, frontier_text, today)
     )                                   → 推送（异步，不阻塞扫描主流程）

_push_frontier_to_users(domain, frontier_text, today)
  ├─ VectorStore.find_matching_users(
  │    content = f"{domain}\n{frontier_text[:400]}",
  │    top_k = 30,
  │    max_distance = 0.55           # cosine距离阈值，只取高度相关用户
  │  )
  │   → ChromaDB user_interests 语义检索 → [user_id, ...]
  │
  ├─ for user_id in matching_users:
  │     open_id = user_id[len("feishu:"):]   # 提取飞书 open_id
  │     lock_key = f"push:frontier:{domain}:{open_id}:{today}"
  │     cache.acquire_lock(lock_key, ttl=86400)   # 去重：当天同领域只推一次
  │     notifier.send_proactive_feishu(open_id, title, summary)
  │
  └─ 记录推送统计日志（推送数 / 跳过数 / 匹配总数）
```

### 17.2 用户兴趣向量的建立过程

用户首次使用即开始自动建立兴趣画像，无需任何手动配置：

```
用户提交任务（飞书/Web/CLI）
    ↓
LeadResearcher 执行成功
    ↓
extract_and_update_interests(user_id, task_input, task_output)
    ↓ FastModel 推断领域关键词（约 1s，完全异步）
    ↓
MySQL user_interest_profiles：domain=xxx, keywords=[...], weight+=0.1
    ↓
ChromaDB user_interests：user_id=feishu:ou_xxx → 兴趣文本向量化 upsert
    ↓
下次 Vanguard 扫描时即可匹配到该用户
```

### 17.3 推送去重机制

采用 Redis 分布式锁实现多维度去重，完全无需额外数据库表：

| 去重维度 | Redis Key 格式 | TTL |
|---------|--------------|-----|
| 用户 × 领域 × 日期 | `push:frontier:{domain}:{open_id_prefix}:{YYYYMMDD}` | 86400s（一天）|
| Vanguard 扫描本身 | `auto:vanguard:{session_name}:{YYYYMMDD}` | 7200s（防重复执行）|

### 17.4 推送消息格式

```
标题：🔭 {domain} · 今日前沿速递

正文：
[从 frontier_text 提取的核心趋势段落，≤ 900 字]

---
💡 基于你的研究兴趣，OpenClaw 自动为你筛选了 **{domain}** 领域最新进展。
   有任何问题，直接回复即可提问。
```

### 17.5 阈值与调参说明

| 参数 | 默认值 | 含义 | 调整建议 |
|------|-------|------|---------|
| `max_distance` | `0.55` | ChromaDB cosine distance 阈值（越小越严格）| 推送过多可调低至 0.45；推送过少可调高至 0.65 |
| `top_k` | `30` | 最多匹配 30 位用户 | 用户规模大时可提高 |
| `asyncio.sleep(0.5)` | 500ms | 推送间隔（飞书 API 限速保护）| QPS 限制松时可调低 |
| 摘要截取长度 | `900` 字 | 推送正文最大长度 | 飞书卡片实测上限约 3000 字 |

### 17.6 系统已就位的基础设施

> 此功能完全依赖已有基础设施，无需引入任何新组件或新依赖。

| 基础设施 | 状态 | 负责位置 |
|---------|------|---------|
| ChromaDB `user_interests` 集合 | ✅ 已就位 | `core/vector_store.py` |
| `find_matching_users()` 向量检索 | ✅ 已就位 | `VectorStore.find_matching_users()` |
| Vanguard 前沿报告 | ✅ 每日产出 | `agents/vanguard.py` |
| 飞书主动推送 API | ✅ 已就位 | `core/notifier.send_proactive_feishu()` |
| Redis 分布式去重锁 | ✅ 已就位 | `core/cache.acquire_lock()` |
| 用户兴趣向量写入 | ✅ 任务完成后自动触发 | `core/interest_extractor.py` |
| 推送逻辑 `_push_frontier_to_users()` | ✅ **v3.2 新增打通** | `core/autonomous_loop.py` |
