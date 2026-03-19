# Omniscientist Claw — 部署文档

> 本文档覆盖当前系统支持的全部部署模式，包含 Docker 目录结构、启动命令速查表、异步执行说明及常见问题排查。

---

## 目录

1. [架构概览](#1-架构概览)
2. [目录结构](#2-目录结构)
3. [环境变量配置](#3-环境变量配置)
4. [四种启动模式](#4-四种启动模式)
5. [命令速查表](#5-命令速查表)
6. [访问地址](#6-访问地址)
7. [日志管理](#7-日志管理)
8. [异步执行说明](#8-异步执行说明)
9. [常见问题排查](#9-常见问题排查)
10. [关键配置说明](#10-关键配置说明)

---

## 1. 架构概览

```
用户（飞书/Web）
    │
    ▼
omni-gateway (OpenClaw Gateway, 端口 10100)
    │  配置: claw/config/openclaw.json
    │  接收消息 → 调用 omniscientist-research skill
    │
    ▼
omni-research (Python FastAPI, 端口 10101/10102)
    │
    ├── Maintainer 调度器（单一协程，v3.3 新增）
    │     唯一读 Redis Stream 的入口 → 负载感知路由到 Worker 私有队列
    │     防止多 Worker 重复执行同一任务
    │
    └── Worker Pool（3个Worker × 5并发 = 15并发槽位）
          每个 Worker 从私有队列读任务，Semaphore 控制并发
          └── LeadResearcher → LLM 推理（arXiv / 飞书推送 / 数据库）
```

> **v3.3 架构亮点**：`WORKER_COUNT=3, WORKER_CONCURRENCY=5`，总并发 15 槽位。Maintainer 单点路由，每条消息只交给一个 Worker，彻底消除用户收到多条重复回复的问题。

**端口规划（10100-10199 范围内）：**

| 端口  | 服务          | 说明                                   |
|-------|---------------|----------------------------------------|
| 10100 | omni-gateway  | OpenClaw Gateway Web UI（对外访问）    |
| 10101 | omni-research | Python 科研 API（宿主机或容器内部端口）|
| 10101 | omni-research | Research API（容器/宿主机，仅本机 127.0.0.1） |
| 10109 | omni-gateway  | WebSocket Bridge（对外）              |

---

## 2. 目录结构

```
openclaw_research/
├── claw/                              ← Docker 配置中心（对标 teamlab 最佳实践）
│   ├── config/                       ← OpenClaw Gateway 配置（原 omni-openclaw-config/）
│   │   ├── openclaw.json             ← Gateway 主配置（模型、飞书、认证）
│   │   ├── agents/main/boot.md       ← Agent 行为指令
│   │   └── skills/omniscientist-research/scripts/research.sh
│   ├── workspace/                    ← Gateway 工作空间（原 omni-openclaw-workspace/）
│   ├── docker-compose.yml            ← 完整 Docker（Gateway + Agent）
│   ├── docker-compose.gateway.yml    ← 仅 omni-gateway（连宿主机后端）
│   ├── docker-compose.agent.yml      ← 仅 omni-research
│   └── Makefile                      ← Docker 管理命令入口
├── Dockerfile.research               ← Python 后端镜像（构建上下文需要在根目录）
├── docker-compose.omniscientist.yml  ← 兼容旧版完整部署（volumes 已更新至 claw/）
├── start.sh                          ← 统一管理脚本（本地 + Docker 命令）
├── .env.omniscientist                ← 环境变量（不提交 Git）
├── agents/                           ← LeadResearcher 核心逻辑
├── core/                             ← Worker Pool / Notifier / 数据库
├── skills/                           ← 工具技能（arXiv / 飞书 / 搜索等）
├── api/                              ← FastAPI 路由
└── data/                             ← 数据目录（日志 / 知识库 / 工作流）
    └── logs/                         ← 统一日志目录
```

---

## 3. 环境变量配置

`.env.omniscientist`（项目根目录，Docker 模式使用）：

```bash
# AI 推理
OPENAI_API_KEY=sk-xxx
OPENROUTER_BASE_URL=http://endpoint/v1
DEFAULT_MODEL=gemini-3.1-pro-preview-thinking

# 数据库
MYSQL_URL=mysql+aiomysql://root:agent%211234@localhost:3306/openclaw_research
REDIS_URL=redis://localhost:6379/0

# 飞书（omni-gateway 使用）
FEISHU_APP_ID=cli_a939102f4cb81bcc
FEISHU_APP_SECRET=your_secret

# Worker 调度架构（v3.3：Maintainer 单点路由 + Worker 并发）
# WORKER_COUNT=3    : 3个Worker进程，每个并发处理5任务，总槽位=15
# WORKER_CONCURRENCY=5 : 每Worker最大并发数（Semaphore控制）
# STREAM_PENDING_TIMEOUT_MS=1800000 : 必须远大于TASK_TIMEOUT，防止重复执行
WORKER_COUNT=3
WORKER_CONCURRENCY=5
TASK_TIMEOUT=600
STREAM_PENDING_TIMEOUT_MS=1800000
```

`.env`（项目根目录，本地 Python 模式使用）：
内容与 `.env.omniscientist` 相同，用于 `conda run` 启动的本地服务。

> **注意**：`claw/config/openclaw.json` 中的 `apiKey` 需与 `OPENAI_API_KEY` 保持一致。

---

## 4. 四种启动模式

### 模式 A — 完整 Docker 部署（推荐生产）

所有服务均在容器内运行：Gateway + Python 后端。

```bash
# 推荐：使用 Makefile（会自动重建 omni-research 镜像，确保最新代码生效）
make -C claw up

# 或使用 start.sh
./start.sh docker-up

# 或使用旧版 docker-compose
docker compose -f docker-compose.omniscientist.yml --env-file .env.omniscientist up -d
```

> **重要**：`make -C claw up` 内置 `--build` 参数，**每次启动都会自动重建 `omni-research` 镜像**。Python 代码修改后无需单独执行 `rebuild`，直接 `make -C claw up` 即可生效。
>
> 得益于 `.dockerignore` 的精准排除，构建上下文仅约 326KB（排除了 `data/`、`claw/`、`.git/` 等），增量构建通常只需 **15~40 秒**。

---

### 模式 B — 仅 Gateway Docker + 宿主机 Python 后端（开发/调试）

Python 后端以 conda 环境在宿主机运行，只有 Gateway 容器化。适合频繁修改后端代码时使用。

```bash
# Step 1: 启动宿主机 Python 后端
./start.sh start -d

# Step 2: 启动 Gateway Docker（连接宿主机后端）
make -C claw up-gateway
# 或
./start.sh docker-gateway-only
```

---

### 模式 C — 仅后端 Docker（后端容器化，Gateway 使用宿主机 openclaw）

```bash
make -C claw up-agent
# 或
./start.sh docker-agent-up
```

---

### 模式 D — 混合一键启动（模式 B 的快捷方式）

一条命令完成：宿主机 Python 后端启动 + Gateway Docker 启动。

```bash
make -C claw up-all
# 或
./start.sh up-all
```

---

## 5. 命令速查表

### Makefile 命令（在项目根目录执行 `make -C claw <命令>`）

#### 启动 / 停止

| 命令 | 说明 |
|------|------|
| `make -C claw up` | 完整 Docker（Gateway + Agent，**自动重建镜像**确保代码最新） |
| `make -C claw up-gateway` | 仅启动 Gateway（连宿主机后端） |
| `make -C claw up-agent` | 仅启动 Python 后端（同样自动重建镜像） |
| `make -C claw up-all` | 混合：宿主机后端 + Gateway Docker |
| `make -C claw up-fresh` | 强制无缓存重建并重启（依赖变更后使用） |
| `make -C claw down` | 停止所有容器（Gateway + Agent） |
| `make -C claw rebuild` | 仅重建后端镜像（不重启，用于提前缓存） |
| `make -C claw status` | 查看容器状态 |

#### 日志 / Shell

| 命令 | 说明 |
|------|------|
| `make -C claw logs` | 查看所有容器日志（跟随） |
| `make -C claw logs-gw` | 仅查看 Gateway 日志 |
| `make -C claw logs-agent` | 仅查看后端日志 |
| `make -C claw shell-gw` | 进入 Gateway 容器 Shell |
| `make -C claw shell-agent` | 进入 Research 容器 Shell |
| `make -C claw shell-oc` | 进入 OpenClaw Gateway 容器（同 `shell-gw`） |

#### 飞书配对 & 设备管理

| 命令 | 说明 |
|------|------|
| `make -C claw feishu-pair CODE=xxx` | 批准指定配对码（CODE 必填） |
| `make -C claw feishu-pair-list` | 查看已配对设备列表 |
| `make -C claw approve-device` | 批准 Control UI 配对请求 |
| `make -C claw reset-sessions` | 清除所有会话历史（保留配置） |
| `make -C claw reset-session ID=xxx` | 清除指定 session 历史（ID 为 UUID） |

#### 维护

| 命令 | 说明 |
|------|------|
| `make -C claw pull` | 更新 OpenClaw Gateway 镜像并重启 |
| `make -C claw doctor` | 安全执行 `openclaw doctor --fix`（自动校正飞书策略，避免配对问题） |
| `make -C claw fix-config` | 仅校验/修复 `openclaw.json` 飞书策略（不重启） |
| `make -C claw clean-logs` | 清理 30 天前旧日志 |
| `make -C claw clean-logs DAYS=7` | 清理 7 天前旧日志 |
| `make -C claw backend-start` | 宿主机后台启动 Python Agent |
| `make -C claw backend-stop` | 停止宿主机 Python Agent |

### start.sh 命令速查

| 命令                              | 说明                                              |
|-----------------------------------|---------------------------------------------------|
| `./start.sh start [-d]`           | 本地前台/后台启动 Python 服务                    |
| `./start.sh stop`                 | 停止本地 Python 服务                              |
| `./start.sh restart [-d]`         | 重启本地 Python 服务                              |
| `./start.sh status`               | 本地服务状态                                     |
| `./start.sh logs [模块]`          | 实时日志（app/feishu/worker/evolution/error）    |
| `./start.sh docker-up`            | 完整 Docker 部署（模式 A）                       |
| `./start.sh docker-gateway-only`  | 仅 Gateway Docker（模式 B）                      |
| `./start.sh docker-agent-up`      | 仅后端 Docker（模式 C）                          |
| `./start.sh up-all`               | 混合一键启动（模式 D）                            |
| `./start.sh docker-gateway-up`    | （旧版兼容）仅启动 Gateway Docker                |
| `./start.sh docker-down`          | 停止所有 Docker 容器（omni-gateway + omni-research） |
| `./start.sh docker-restart`       | 重启 Docker 套件                                 |
| `./start.sh docker-status`        | Docker 服务状态                                  |
| `./start.sh docker-logs [服务]`   | Docker 日志（omni-gateway/omni-research）         |
| `./start.sh docker-shell-gw`      | 进入 Gateway 容器                                |
| `./start.sh docker-shell-agent`   | 进入 Research 容器                               |
| `./start.sh docker-devices-approve` | 批准 Control UI 配对请求                       |
| `./start.sh docker-build`         | 重建镜像并启动                                   |
| `./start.sh status-all`           | 本地 + Docker 综合状态                           |
| `./start.sh help`                 | 显示帮助                                         |

---

## 6. 访问地址

| 服务                     | URL                                                        |
|--------------------------|------------------------------------------------------------|
| OpenClaw Gateway Web UI  | http://localhost:10100/chat?session=main                   |
| Gateway（带 token 直连） | http://localhost:10100/#token=tony_research                |
| Research API             | http://localhost:10101（宿主机 / 容器均使用此端口）               |
| Research API 文档        | http://localhost:10101/docs                                |
| WebSocket Bridge         | ws://localhost:10109                                       |

**Gateway 认证**：
- Token：`tony_research`
- Password：`tony_research`
- 访问飞书聊天界面：http://localhost:10100/chat?session=main

---

## 7. 日志管理

日志统一输出到 `./data/logs/` 目录：

| 日志文件           | 内容                                   |
|--------------------|----------------------------------------|
| `all.log`          | 全局汇总日志（所有模块）               |
| `app.log`          | 应用主日志（按天滚动，保留 30 天）     |
| `agent.log`        | LeadResearcher / WorkerPool 执行日志  |
| `feishu.log`       | 飞书 Bot 收发消息日志                  |
| `worker.log`       | Worker Pool 任务调度日志               |
| `schedule.log`     | 定时任务日志（每日科研日报等）         |
| `evolution.log`    | 进化循环 / 知识沉淀日志               |
| `error.log`        | 全局错误日志（ERROR 级别汇总）        |

```bash
# 查看实时日志
./start.sh logs             # 主日志
./start.sh logs feishu      # 飞书日志
./start.sh logs worker      # Worker 任务日志

# Docker 日志
./start.sh docker-logs omni-gateway
./start.sh docker-logs omni-research

# 清理历史日志（默认 30 天）
./start.sh clean-logs
./start.sh clean-logs 7     # 清理 7 天前
```

---

## 8. 异步执行说明

### Fire-and-Forget 架构（飞书渠道）

飞书消息由 Worker 后台异步处理，**用户不需要等待**：

```
用户发飞书消息
    │
    ▼
omni-gateway → 调用 research.sh（< 2s 返回）
    │
    ▼
"✅ 已提交科研任务，后台处理中，完成后飞书通知"（立即回复）
    │
    ▼（后台 Worker 执行，最长 600s）
    │
    ▼
Worker 完成 → 直接推送飞书消息给用户
```

**长任务心跳**：执行超过 60s 的任务，系统每 60s 向飞书用户发送进度提示，用户无需担心任务丢失。

**追问建议**：任务完成后，系统后台生成延伸思考建议，完成后以第二条飞书消息推送。

### 任务复杂度自适应

LeadResearcher 根据任务内容自动选择执行策略：

| 任务类型                              | 模式     | 最大迭代 | max_tokens | 热点预取 |
|---------------------------------------|----------|----------|------------|---------|
| 含 NeurIPS/ICML/论文/综述/投稿 等关键词 | 深度模式 | 30 轮    | 16384      | 是      |
| 含分析/研究/设计 等关键词 / 任务 >200 字 | 中等模式 | 20 轮    | 8192       | 是      |
| 简单问答                              | 快速模式 | 10 轮    | 4096       | 否      |

**热点研究洞察**：中等及深度任务在执行前，系统自动从 arXiv 预取该领域最近 7 天的热点论文，注入 LLM 上下文，使回答更贴合当前研究前沿。

### 超时策略

| 场景                  | 超时设置                                    |
|-----------------------|---------------------------------------------|
| 飞书渠道 research.sh  | 2s 内返回（fire-and-forget，不等待结果）     |
| Web UI / CLI 同步轮询  | `--timeout 600`（综述/论文），默认 300s      |
| 后端 TASK_TIMEOUT     | 600s（Docker 环境变量 `TASK_TIMEOUT=600`）  |
| 深度研究任务          | boot.md 可传 `--timeout 900`                |

---

## 9. 常见问题排查

### Control UI 报 "pairing required"

**原因**：Docker NAT 网络中，Gateway 收到的请求来自 Docker 网桥 IP（非 localhost），绕过了本地认证判断。

**解决方案**（按优先级）：

```bash
# 1. 使用带 token 的 URL 直接访问（推荐）
open "http://localhost:10100/#token=tony_research"

# 2. 确认 openclaw.json 中 gateway 已配置 trustedProxies
#    claw/config/openclaw.json 应包含：
#    "gateway": { "trustedProxies": ["0.0.0.0/0"], ... }
#    修改后重启 Gateway：
./start.sh docker-restart
# 或
make -C claw down && make -C claw up

# 3. 手动批准待配对设备
./start.sh docker-devices-approve
```

---

### No API key found for provider "openai-proxy"

**检查点**：

1. 确认 `claw/config/openclaw.json` 中 `models.providers.openai-proxy.apiKey` 已填写
2. 确认 `.env.omniscientist` 中 `OPENAI_API_KEY` 已配置
3. 重启 Gateway 使配置生效

```json
// claw/config/openclaw.json 片段
{
  "models": {
    "providers": {
      "openai-proxy": {
        "type": "openai-compat",
        "apiKey": "sk-xxx",  // ← 确认此处已填写
        "baseUrl": "http://endpoint/v1"
      }
    }
  }
}
```

---

### 飞书用户发消息收到"配对码"提示

**现象**：
```
OpenClaw: access not configured.
Your Feishu user id: ou_xxx
Pairing code: KDYN4GRT
Ask the bot owner to approve with: openclaw pairing approve feishu KDYN4GRT
```

**根本原因**：`openclaw doctor --fix` 会把 `dmPolicy / allowFrom / groupPolicy` 从正确位置（`accounts.main`）迁移到 `accounts.default`，而 OpenClaw 运行时只读 `accounts.main`，导致访问策略失效，回退为"需要逐用户配对"模式。

**永久修复**（推荐）：
```bash
# 校验并自动修复策略配置，重启 Gateway 生效
make -C claw fix-config
docker restart omni-gateway
```

**临时应急**（当前用户无法等待时）：
```bash
# 批准指定配对码（CODE 替换为实际值）
make -C claw feishu-pair CODE=KDYN4GRT
```

**预防**：需要执行 OpenClaw 版本升级时，使用安全封装命令而非直接调用：
```bash
# ✅ 安全方式（自动校正策略）
make -C claw doctor

# ❌ 危险方式（会破坏 dmPolicy 配置）
docker exec omni-gateway openclaw doctor --fix
```

**正确的飞书配置结构**（`claw/config/openclaw.json`）：
```json
"channels": {
  "feishu": {
    "enabled": true,
    "connectionMode": "websocket",
    "defaultAccount": "main",
    "accounts": {
      "main": {
        "appId": "cli_xxx",
        "appSecret": "your_secret",
        "botName": "Omniscientist Research",
        "dmPolicy": "open",
        "allowFrom": ["*"],
        "groupPolicy": "open"
      }
    }
  }
}
```

> **关键**：`dmPolicy / allowFrom / groupPolicy` 必须在 `accounts.main` 内，不能在顶层或 `accounts.default` 中。

---

### 飞书 Bot 不响应

**排查清单**：

1. **飞书开放平台**：
   - 事件订阅 → 订阅方式选「长连接（WebSocket）」
   - 已订阅 `im.message.receive_v1` 事件
   - 应用权限：`im:message:readonly`（接收消息）、`im:message`（发送消息）

2. **`claw/config/openclaw.json` 飞书配置**：参见上方"正确的飞书配置结构"，确保 `dmPolicy` 在 `accounts.main` 内。

3. **检查 Gateway 日志**中是否有 `[feishu] WebSocket connected` 及消息接收记录：
   ```bash
   ./start.sh docker-logs omni-gateway
   ```

4. **API Key 一致性**：确认 `claw/config/openclaw.json` 中 `models.providers.openai-proxy.apiKey`、`claw/config/agents/main/agent/models.json` 中的 `apiKey`，以及 `.env.omniscientist` 的 `OPENAI_API_KEY` **三处保持一致**，否则 LLM 调用失败，Bot 无法响应。

5. **"duplicate plugin id" 问题**：确认 `openclaw.json` 中没有 `plugins.entries.feishu`（会与内建飞书插件冲突）

---

### 复杂任务执行慢 / 超时

**v3.0 已修复的根本原因**：

| 问题                        | 修复内容                                            |
|-----------------------------|-----------------------------------------------------|
| `arxiv_search` 阻塞事件循环 | 改用 `run_in_executor` 包装同步迭代器               |
| `generate_follow_ups` 阻塞  | 改为 `asyncio.create_task` 后台执行                 |
| TASK_TIMEOUT 仅 300s        | 提升至 600s（可通过 `TASK_TIMEOUT` 环境变量调整）   |
| OpenClaw 同步等待结果       | 飞书渠道 research.sh 改为 fire-and-forget 模式      |
| 用户长时间等待无反馈        | 长任务每 60s 发送飞书进度心跳                        |

**当前推荐配置**：飞书消息 → 系统立即回复「任务已提交」→ Worker 后台处理（最长 600s）→ 完成后直接推送飞书。

---

### 其他服务管理

```bash
# 查看所有状态
./start.sh status-all

# 修改 Python 代码后使变更生效（直接 up 即可，已内置 --build）
make -C claw up

# 仅重建镜像（不重启，适合提前缓存）
make -C claw rebuild
# 或
./start.sh docker-build

# 查看工作目录（进入 Gateway 容器调试）
./start.sh docker-shell-gw
./start.sh docker-shell-agent
```

---

## 10. 关键配置说明

### Docker 镜像构建机制

`omni-research` 使用本地 `Dockerfile.research` 构建（非拉取镜像），代码变更需要重建镜像才能生效。

| 命令 | 重建镜像 | 适用场景 |
|------|----------|----------|
| `make -C claw up` | ✅ 自动（增量） | **日常使用**，修改 Python 代码后直接运行 |
| `make -C claw up-fresh` | ✅ 强制无缓存 | `requirements.txt` 变更后使用 |
| `make -C claw rebuild` | ✅ 仅构建不启动 | 提前缓存镜像 |
| `docker restart omni-research` | ❌ | 只重启容器，代码变更**不会生效** |

**构建速度**：`.dockerignore` 排除了 `data/`、`claw/`、`.git/` 等目录，构建上下文仅 ~326KB。依赖层全部走缓存，增量构建通常 **15~40 秒**完成。

---

### openclaw.json 关键配置规范

#### 飞书策略配置（必须在 `accounts.main` 中）

```json
{
  "channels": {
    "feishu": {
      "enabled": true,
      "connectionMode": "websocket",
      "defaultAccount": "main",
      "accounts": {
        "main": {
          "appId": "cli_xxx",
          "appSecret": "xxx",
          "botName": "Omniscientist Research",
          "dmPolicy": "open",     ← 必须在这里，不能在顶层或 accounts.default
          "allowFrom": ["*"],     ← 允许任何人直接对话
          "groupPolicy": "open"   ← 群组中 @ 即可响应
        }
      }
    }
  }
}
```

> ⚠️ **注意**：`openclaw doctor --fix` 会将上述策略字段错误迁移到 `accounts.default`，导致所有飞书用户触发配对流程。执行版本升级时请使用 `make -C claw doctor`（安全封装版本）。

#### API Key 一致性

系统有三处需要同步 API Key：

| 文件 | 字段 |
|------|------|
| `.env.omniscientist` | `OPENAI_API_KEY` |
| `claw/config/openclaw.json` | `models.providers.openai-proxy.apiKey` |
| `claw/config/agents/main/agent/models.json` | `providers.openai-proxy.apiKey` |

三处不一致会导致 Gateway LLM 调用失败，表现为飞书或 Web 消息无响应，或报 `HTTP 400 API Key not found`。

---

### Worker 调度架构配置（v3.3）

#### 关键参数说明

| 参数 | 推荐值 | 说明 |
|------|--------|------|
| `WORKER_COUNT` | `3` | Worker 进程数。每个 Worker 运行一个 asyncio 协程循环 |
| `WORKER_CONCURRENCY` | `5` | 每个 Worker 最大并发任务数（Semaphore 控制） |
| `TASK_TIMEOUT` | `600` | 任务最大执行时间（秒）。写论文等复杂任务约需 5-10 分钟 |
| `STREAM_PENDING_TIMEOUT_MS` | `1800000` | **必须远大于 TASK_TIMEOUT x 1000**。防止执行中的任务被误判为崩溃后重复认领，设为 30 分钟（3 倍 TASK_TIMEOUT） |

#### 为什么 STREAM_PENDING_TIMEOUT_MS 很重要

旧版 Bug 场景（STREAM_PENDING_TIMEOUT_MS=120000，即 2 分钟）：
1. 用户发送"写一篇 NeurIPS 论文"请求
2. Worker-00 开始执行（预计需要 8 分钟）
3. 2 分钟后，任务仍在 Redis Stream Pending 状态（未 XACK）
4. 其他 14 个空闲 Worker 检测到"超时未确认的任务"
5. 多个 Worker 同时 XCLAIM 认领该任务 → 任务被执行 N 次 → 用户收到 N 篇论文

v3.3 解决方案（双重保障）：
1. 架构根治：Maintainer 立即 XACK，任务不进入 Pending，XCLAIM 永远找不到重复任务
2. 参数兜底：STREAM_PENDING_TIMEOUT_MS=1800000（30min）>> TASK_TIMEOUT（600s=10min）

#### 并发能力规划

- `WORKER_COUNT=3` x `WORKER_CONCURRENCY=5` = **15 个并发任务槽位**
- Worker-00/01/02 各自最多同时运行 5 个任务（Semaphore 控制）
- 队列满时自动排队，并向用户发送 ETA 提示（支持 50+ 用户并发使用）

---

### claw/scripts/ 维护脚本

| 脚本 | 用途 |
|------|------|
| `scripts/fix-openclaw-config.py` | 校验并修复 `openclaw.json` 的飞书策略配置；可单独运行，也由 `make -C claw fix-config` 调用 |

```bash
# 手动执行（可在任何环境下运行，无需 Docker）
python3 claw/scripts/fix-openclaw-config.py

# 通过 Makefile 执行（同上，更推荐）
make -C claw fix-config
```
