# openclaw_research 开发指南

> 版本：v2.1 | 最后更新：2026-03-19

openclaw_research 是运行在 Mac Studio 上的多进程 AI 智能体科研系统。本文档为在此系统基础上开发或贡献代码的工程师提供完整参考。

---

## 目录

1. [项目概览](#1-项目概览)
2. [开发环境搭建](#2-开发环境搭建)
3. [项目结构](#3-项目结构)
4. [核心概念](#4-核心概念)
5. [创建新 Agent](#5-创建新-agent)
6. [创建新技能](#6-创建新技能)
7. [工作流开发](#7-工作流开发)
8. [API 开发](#8-api-开发)
9. [添加新实例类型（v2.0）](#9-添加新实例类型v20)
10. [instances 模块开发指南（v2.0）](#10-instances-模块开发指南v20)
11. [数据库 Schema](#11-数据库-schema)
12. [测试](#12-测试)
13. [前端架构](#13-前端架构)
14. [贡献规范](#14-贡献规范)
15. [故障排查](#15-故障排查)

---

## 1. 项目概览

### 系统是什么

openclaw_research 是专为科研社区打造的多智能体 AI 系统，运行在 Mac Studio 上。它由一个**编排层**（端口 10101）和 15 个**专项子实例**（端口 10110-10134）组成，协同处理文献综述、实验设计、前沿探索、学术写作、知识沉淀等复杂科研任务。

### v2.0 相对于 v1.0 的主要变化

| 维度 | v1.0 | v2.0 |
|------|------|------|
| 进程数 | 单进程 | 1 编排层 + 最多 15 子进程 |
| 端口 | 8000/8001 | 10101（编排层），10110-10134（子实例） |
| AI 提供商 | Anthropic Claude | OpenAI（gpt-5.3-chat-latest） |
| 配置方式 | 仅 .env | .env + config/instance_types.yaml |
| 新增模块 | — | instances/（types/registry/manager/client） |
| 新增 API | — | /api/instances/（实例 CRUD） |
| 系统名称 | OpenClaw 科研智能体社区 | openclaw_research |

### 技术栈

| 层 | 技术 |
|----|------|
| Web 框架 | FastAPI 0.110+ |
| AI SDK | OpenAI Python SDK |
| 数据库 | SQLite via SQLAlchemy 2.0 + aiosqlite |
| 流式输出 | Server-Sent Events (SSE) |
| 配置 | python-dotenv + 自定义 Settings 类 + YAML |
| CLI | Typer + Rich |
| 搜索 | arXiv SDK, httpx, BeautifulSoup4 |
| 数据 | Pandas, NumPy |
| 运行时 | Python 3.11, conda 环境 `claw` |
| 实例管理 | subprocess + asyncio |

---

## 2. 开发环境搭建

### 前提条件

- Mac Studio（推荐）或 macOS 13+
- Miniconda（路径：`/opt/homebrew/Caskroom/miniconda/base/`）
- OpenAI API Key
- Git

### 步骤 1：克隆仓库

```bash
git clone <repo-url>
cd openclaw
```

### 步骤 2：创建 conda 环境

项目要求 Python 3.11，conda 环境名称固定为 `claw`（已硬编码在 `start.sh` 中）。

```bash
conda create -n claw python=3.11 -y
conda activate claw
pip install -r requirements.txt
```

完整依赖列表（`requirements.txt`）：

```
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
openai>=1.0.0
aiohttp>=3.9.0
httpx>=0.27.0
python-dotenv>=1.0.0
pydantic>=2.6.0
pydantic-settings>=2.2.0
rich>=13.7.0
typer>=0.12.0
click>=8.1.0
sqlalchemy>=2.0.0
aiosqlite>=0.20.0
arxiv>=2.1.0
beautifulsoup4>=4.12.0
PyPDF2>=3.0.0
pandas>=2.1.0
numpy>=1.26.0
requests>=2.31.0
PyYAML>=6.0.1
psutil>=5.9.0
```

### 步骤 3：配置环境变量

```bash
cp .env.example .env
# 编辑 .env，至少设置：
# OPENAI_API_KEY=sk-proj-your-key-here
# PROJECT_NAME=openclaw_research
# INSTANCE_TYPE=orchestrator
# PORT=10101
```

`config/settings.py` 中的 `Settings` 类在导入时读取所有配置，并自动创建 `data/` 下的子目录。

### 步骤 4：验证安装

```bash
```

期望输出：所有依赖可导入、API Key 已配置、15 个 Agent 已注册、51 个技能已加载。

### 步骤 5：启动服务

```bash
# 仅启动编排层
bash start.sh web

# 访问：
# http://localhost:10101        — Web UI
# http://localhost:10101/docs   — Swagger API 文档
# http://localhost:10101/health — 健康检查

# （可选）启动所有子实例
bash start.sh instances
```

---

## 3. 项目结构

```
openclaw/
├── .env                        # 本地环境变量（git 忽略）
├── .env.example                # .env 模板
├── requirements.txt            # Python 依赖
├── start.sh                    # 启动脚本（web/instances/status/stop-instances/cli/demo）
│
├── config/
│   ├── settings.py             # Settings 类，读取 .env，暴露路径常量
│   ├── instance_types.yaml     # 15 种实例类型定义（v2.0 新增）
│   └── __init__.py
│
├── instances/                  # 多进程实例管理模块（v2.0 新增）
│   ├── types.py                # 从 instance_types.yaml 加载类型定义
│   ├── registry.py             # 实例注册表（SQLite 持久化）
│   ├── manager.py              # 进程生命周期管理（启动/停止/状态查询）
│   ├── client.py               # 实例间 HTTP 通信（编排层 → 子实例）
│   └── __init__.py
│
├── agents/
│   ├── base.py                 # BaseAgent：OpenAI 客户端，tool_use 循环
│   ├── clawer.py               # 10 个 Clawer 实例（create_clawer() 工厂）
│   ├── guardian.py             # GuardianAgent：review_input/output/publish
│   ├── vanguard.py             # VanguardAgent：前沿探索
│   ├── maintainer.py           # MaintainerAgent：系统健康监控
│   ├── promoter.py             # PromoterAgent：内容推广
│   ├── wellspring.py           # WellspringAgent：知识沉淀
│   └── __init__.py
│
├── core/
│   ├── registry.py             # AgentRegistry 单例：存储和索引所有 Agent
│   ├── orchestrator.py         # Orchestrator：任务生命周期管理
│   ├── router.py               # TaskRouter：正则路由决策
│   ├── database.py             # SQLAlchemy 模型，异步引擎，DB seed
│   └── __init__.py
│
├── skills/
│   ├── tools.py                # SKILL_REGISTRY, @register_skill, execute_skill（51 个技能）
│   └── __init__.py
│
├── api/
│   ├── main.py                 # FastAPI app，lifespan，CORS，静态文件
│   └── routes/
│       ├── agents.py           # GET /api/agents, /api/agents/{id}
│       ├── tasks.py            # POST /api/tasks/execute, /ask, /route, /multi-agent
│       ├── stream.py           # POST /api/tasks/stream（SSE）
│       ├── wellspring.py       # GET/POST /api/wellspring
│       ├── system.py           # GET /api/system/status, /health
│       ├── instances.py        # GET/POST /api/instances/（v2.0 新增）
│       └── __init__.py
│
├── cli/
│   └── main.py                 # Typer CLI：run/chat/agents/skills/status
│
├── web/
│   ├── index.html              # 单页前端（HTML/CSS/JS 合一）
│   └── static/                 # 静态资源占位
│
├── data/                       # 运行时自动创建
│   ├── openclaw.db             # SQLite 数据库（含实例注册表）
│   ├── memory/                 # Agent 对话记忆
│   ├── knowledge/              # 知识库文件
│   ├── prompts/                # Prompt 模板文件
│   ├── workflows/              # 工作流定义文件
│   └── logs/                   # 日志（含 <instance-name>.log 子实例日志）
│
└── docs/
    ├── architecture.md
    ├── api-reference.md
    ├── deployment.md
    ├── skills-reference.md
    └── development-guide.md    # 本文档
```

---

## 4. 核心概念

### 4.1 BaseAgent 与 tool_use 循环

所有 Agent 继承自 `BaseAgent`（`agents/base.py`）。Agent 执行的核心是 OpenAI API 的 function calling 机制：

```python
class BaseAgent:
    def __init__(self, agent_id, name, role, system_prompt, model=None, tools=None):
        self.agent_id = agent_id
        self.model = model or settings.DEFAULT_MODEL  # gpt-5.3-chat-latest
        self.allowed_tools = tools or list(SKILL_REGISTRY.keys())

    async def run(self, task: str) -> dict:
        messages = [{"role": "user", "content": task}]
        for _ in range(self.max_iterations):  # 最多 10 轮
            response = await openai_client.chat.completions.create(
                model=self.model,
                system=self.system_prompt,
                messages=messages,
                tools=self._get_tools(),  # 按 allowed_tools 过滤
            )
            if response.choices[0].finish_reason == "stop":
                return {"status": "success", "result": response.choices[0].message.content}
            # 处理 tool_calls → 执行技能 → 追加 tool 消息 → 继续循环
```

### 4.2 AgentRegistry

`core/registry.py` 实现了全局单例注册中心：

```python
registry = AgentRegistry()
registry.register(agent)              # 注册 Agent
agent = registry.get("clawer-cs-01") # 按 ID 获取
clawers = registry.get_by_role("clawer")  # 按角色获取
best = registry.best_clawer_for_task(task_text)  # 最优匹配
```

### 4.3 TaskRouter

`core/router.py` 通过正则模式匹配实现任务路由，优先级从高到低：

```
1. 职能型 Agent 关键词（综述/文献/数据分析/实验...）
2. 复杂工作流关键词（研究设计/前沿探索...）
3. 最优 Clawer 自动选择（关键词重叠度评分）
```

### 4.4 技能系统

`skills/tools.py` 维护全局 `SKILL_REGISTRY`，共注册 **51 个技能**。

- **直接执行型**：立即返回结果，如 `web_search`、`code_execute`、`arxiv_search`
- **LLM Action 型**：返回 `{"action": "llm_xxx", "prompt": "..."}` 结构，由 BaseAgent 拦截后传给 LLM 二次处理，如 `text_summarize`、`code_review`

### 4.5 INSTANCE_TYPE 感知的启动逻辑（v2.0）

`api/main.py` 的 lifespan 函数根据 `INSTANCE_TYPE` 环境变量决定注册哪些 Agent：

```python
# api/main.py（伪代码）
instance_type = settings.INSTANCE_TYPE  # 来自 INSTANCE_TYPE 环境变量

if instance_type == "orchestrator":
    # 注册全部 15 个 Agent 到 AgentRegistry
    register_all_agents()
elif instance_type == "clawer-ai":
    # 只注册 AI/ML 研究 Clawer
    register_single_agent("clawer-cs-02")
elif instance_type == "guardian":
    # 只注册 Guardian
    register_single_agent("guardian-01")
# ... 其他类型类推
```

`ALLOWED_SKILLS` 环境变量进一步约束该进程能使用的技能集：

```python
# ALLOWED_SKILLS=arxiv_search,paper_compare,experiment_design,...
# 空字符串表示不过滤，允许全部 51 个技能
allowed = settings.ALLOWED_SKILLS
if allowed:
    filtered_skills = [s for s in SKILL_REGISTRY if s in allowed.split(",")]
```

---

## 5. 创建新 Agent

### 方法一：继承 BaseAgent 创建自定义 Agent

```python
# agents/my_agent.py
from agents.base import BaseAgent
from config.settings import settings


class MyResearchAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id="my-agent-01",
            name="My Research Agent",
            role="clawer",
            system_prompt="""你是一位专注于 XXX 领域的科研助手。
你的职责是 YYY。
请使用可用工具完成任务。""",
            model=settings.DEFAULT_MODEL,
            tools=[
                "arxiv_search",
                "web_search",
                "text_summarize",
                "research_outline",
            ],
        )
```

### 方法二：使用 Clawer 工厂函数

```python
# agents/clawer.py 中已有 create_clawer() 工厂
from agents.clawer import create_clawer

my_clawer = create_clawer(
    agent_id="clawer-quantum-01",
    name="量子计算 Clawer",
    tribe="quantum_computing",
    system_prompt="你是量子计算领域的专家...",
    extra_tools=["arxiv_search", "math_solve", "hypothesis_generate"],
)
```

### 在 AgentRegistry 中注册

```python
# core/registry.py 或 api/main.py 的 lifespan 中
from agents.my_agent import MyResearchAgent
from core.registry import registry

agent = MyResearchAgent()
registry.register(agent)
```

---

## 6. 创建新技能

### 步骤 1：在 `skills/tools.py` 末尾添加技能函数

```python
@register_skill(
    "my_new_skill",                          # 全局唯一技能名（小写+下划线）
    "简洁描述技能功能，不超过50字。",            # LLM 根据此描述决定何时调用
    {
        "input_text": {
            "type": "string",
            "description": "输入文本内容"
        },
        "mode": {
            "type": "string",
            "description": "处理模式：fast/thorough，默认 fast"
        },
    },
)
async def my_new_skill(input_text: str, mode: str = "fast") -> dict:
    if mode == "thorough":
        # 直接执行型：立即返回计算结果
        return {"result": some_computation(input_text), "mode": mode}
    else:
        # LLM Action 型：交给上层 LLM 处理
        return {
            "action": "llm_my_skill",
            "prompt": f"请处理以下内容：\n\n{input_text[:2000]}",
        }
```

### 步骤 2：验证注册成功

```python
python3 -c "from skills.tools import SKILL_REGISTRY; print('my_new_skill' in SKILL_REGISTRY)"
# 输出: True
```

### 步骤 3：将技能分配给实例类型

编辑 `config/instance_types.yaml`，将新技能加入对应实例类型的 `skills` 列表：

```yaml
clawer-cs:
  port: 10111
  skills:
    - code_execute
    - code_review
    - my_new_skill    # 新增
```

这样，`clawer-cs` 类型的子实例在启动时，`ALLOWED_SKILLS` 中就会包含 `my_new_skill`。

### 步骤 4：测试

```python
import asyncio
from skills.tools import execute_skill

result = asyncio.run(execute_skill("my_new_skill", {
    "input_text": "测试内容",
    "mode": "fast"
}))
print(result)
```

### 注意事项

- 技能名在 `SKILL_REGISTRY` 中全局唯一，重名会覆盖已有技能
- 所有参数必须在 `params` 字典中声明
- 技能函数同时支持 `async def` 和 `def`，`execute_skill()` 自动适配
- 工具返回值会被截断为 8000 字符，避免超出 LLM 上下文窗口

---

## 7. 工作流开发

### 现有工作流

工作流逻辑实现在 `core/orchestrator.py` 中：

```python
# 工作流 1：文献综述（4步串行）
async def _wf_literature_review(self, task, route):
    # Step 1: Vanguard 前沿扫描
    vanguard_result = await vanguard.explore_frontier(...)
    # Step 2: Survey Clawer 系统综述
    survey_result = await survey_clawer.run(...)
    # Step 3: Review Clawer 批判性评审
    review_result = await review_clawer.run(...)
    # Step 4: Writing Clawer 最终报告
    return await writing_clawer.run(...)

# 工作流 2：研究设计（并行后串行）
async def _wf_research_design(self, task, route):
    # Survey + Experiment 并行
    survey_r, exp_r = await asyncio.gather(
        survey_clawer.run(...), experiment_clawer.run(...)
    )
    # Data Agent 汇总
    return await data_clawer.run(...)

# 工作流 3：前沿发现（2步串行）
async def _wf_frontier_discovery(self, task, route):
    frontier = await vanguard.explore_frontier(...)
    return await cs_clawer.run(...)
```

### 添加新工作流

**步骤 1**：在 `core/orchestrator.py` 中添加工作流方法：

```python
async def _wf_my_workflow(self, task: str, route: dict) -> dict:
    """自定义工作流：步骤1 → 步骤2"""
    step1_result = await self.registry.get("clawer-math-01").run(task)
    step2_result = await self.registry.get("clawer-data-01").run(
        f"{task}\n\n前序结果：{step1_result['result']}"
    )
    return step2_result
```

**步骤 2**：在 `core/router.py` 的 `WORKFLOW_PATTERNS` 中添加触发关键词：

```python
WORKFLOW_PATTERNS = {
    "wf-literature-review": [r"综述", r"文献.*调研"],
    "wf-my-workflow": [r"我的新工作流关键词"],  # 新增
}
```

**步骤 3**：在 `core/orchestrator.py` 的 `execute()` 方法中添加分支：

```python
elif workflow_id == "wf-my-workflow":
    result = await self._wf_my_workflow(task, route)
```

---

## 8. API 开发

### 添加新路由

**步骤 1**：在 `api/routes/` 中创建新路由文件：

```python
# api/routes/my_feature.py
from fastapi import APIRouter

router = APIRouter()

@router.get("/my-feature")
async def get_my_feature():
    return {"feature": "data"}

@router.post("/my-feature/action")
async def do_action(body: dict):
    return {"result": "ok"}
```

**步骤 2**：在 `api/main.py` 中注册路由：

```python
from api.routes import my_feature

app.include_router(
    my_feature.router,
    prefix="/api/my-feature",
    tags=["My Feature"]
)
```

### 标准响应模式

```python
from pydantic import BaseModel

class TaskResponse(BaseModel):
    task_id: str
    status: str
    result: str | None = None
    error: str | None = None
    timestamp: str

# 在路由函数中
@router.post("/execute", response_model=TaskResponse)
async def execute_task(req: TaskRequest):
    ...
```

---

## 9. 添加新实例类型（v2.0）

这是 v2.0 最重要的开发特性：**只需修改一个 YAML 文件，无需改动任何 Python 代码**，即可定义新的实例类型。

### 步骤 1：在 `config/instance_types.yaml` 中添加新类型

```yaml
instance_types:

  # 在现有定义之后追加：
  clawer-quantum:
    port: 10120                        # 选择 10120-10129 范围内的空闲端口
    description: "量子计算 Clawer：量子算法、量子纠错、量子模拟"
    system_role: clawer
    agent_config: clawer-quantum-01    # Agent 配置 ID（在 registry.py 中定义）
    skills:
      - arxiv_search
      - math_solve
      - hypothesis_generate
      - experiment_design
      - concept_explain
      - research_outline
      - benchmark_design
      - fact_check
```

**字段说明**：

| 字段 | 说明 |
|------|------|
| `port` | 该类型子实例的默认端口。Clawer 用 10110-10129，功能性 Agent 用 10130-10139 |
| `description` | 人类可读的类型描述，在 API `/api/instances/types` 中展示 |
| `system_role` | Agent 角色，如 `clawer`、`guardian`、`vanguard` 等 |
| `agent_config` | Agent 配置 key，对应 `registry.py` 中注册的 Agent（控制 system prompt、模型等） |
| `skills` | 该类型允许使用的技能列表。使用 `"*"` 表示不过滤（允许全部 51 个技能） |

### 步骤 3：验证类型加载

```bash
conda run -n claw python3 -c "
from instances.types import list_types
for t in list_types():
    print(t['type'], '->', t['port'], '|', t['skill_count'], 'skills')
"
```

### 步骤 4：启动新类型实例

```bash
# 通过 API 启动
curl -X POST http://localhost:10101/api/instances/start \
  -H "Content-Type: application/json" \
  -d '{"name": "research-quantum", "instance_type": "clawer-quantum"}'

# 或通过 start.sh 批量脚本（需在 INSTANCE_SPECS 列表中添加该条目）
```

### 完整示例：添加一个"法律研究 Clawer"

```yaml
# 在 config/instance_types.yaml 中添加：
clawer-law:
  port: 10121
  description: "法律研究 Clawer：法律检索、案例分析、合规评估"
  system_role: clawer
  agent_config: clawer-cs-01        # 复用已有 CS Clawer 配置（可自定义）
  skills:
    - web_search
    - url_fetch
    - text_summarize
    - fact_check
    - peer_review
    - research_outline
    - report_generate
    - concept_explain
```

添加后无需重启编排层，即可通过 API 启动该类型的子实例：

```bash
curl -X POST http://localhost:10101/api/instances/start \
  -H "Content-Type: application/json" \
  -d '{"name": "research-law", "instance_type": "clawer-law"}'
```

---

## 10. instances 模块开发指南（v2.0）

### 模块结构

```
instances/
├── types.py      # YAML 类型定义加载器（只读，纯函数）
├── registry.py   # SQLite 持久化注册表（增删查改）
├── manager.py    # 进程管理（start_instance / stop_instance）
└── client.py     # 实例间 HTTP 通信（编排层 → 子实例）
```

### types.py — 类型定义

`instances/types.py` 从 `config/instance_types.yaml` 加载类型定义，带内存缓存：

```python
from instances.types import (
    load_types,          # 返回所有类型字典
    get_type("clawer-ai"),       # 返回单类型配置
    get_allowed_skills("clawer-ai"),  # 返回技能列表或 "*"
    get_port("clawer-ai"),       # 返回端口号
    list_types(),        # 返回所有类型摘要列表
)
```

### registry.py — 注册表

注册表使用 SQLite 持久化，异步操作：

```python
from instances.registry import (
    init_registry_db,          # 初始化表（幂等）
    register_instance(name, type, port, path),  # 注册
    get_instance(name),        # 查询单个
    list_instances(),          # 列出全部
    update_instance_status(name, status, pid),  # 更新状态
    remove_instance(name),     # 删除
)
```

### manager.py — 进程管理

```python
from instances.manager import (
    start_instance(name, instance_type, path=None, port=None),
    stop_instance(name),
    get_instance_status(name),
    list_all_status(),
)
```

`start_instance` 的核心逻辑：

1. 从 `instance_types.yaml` 读取类型配置（端口、技能列表）
2. 检测端口是否可用
3. 构建包含 `INSTANCE_TYPE`、`ALLOWED_SKILLS`、`PORT`、`PYTHONPATH` 的环境变量字典
4. 使用 `subprocess.Popen` 以独立会话（`start_new_session=True`）启动 `uvicorn api.main:app`
5. 等待 1.5 秒后检测进程是否存活
6. 写入注册表

### client.py — 实例间通信

```python
from instances.client import (
    execute_on_instance(name, task, context, timeout),  # 按名称委托
    call_type(instance_type, task, context, timeout),   # 按类型委托（自动找第一个运行中的）
    ping_instance(name),                                 # 健康检查
)
```

`call_type` 是编排层委托任务的主要方式：

```python
# 在 Orchestrator 中的用法（伪代码）
from instances.client import call_type

result = await call_type("clawer-ai", task, context)
if result is None:
    # 无可用子实例，回退到本地 Agent
    result = await registry.get("clawer-cs-02").run(task)
```

---

## 11. 数据库 Schema

使用 SQLAlchemy 2.0 异步模式 + aiosqlite。

### 数据表

| 表名 | 用途 |
|------|------|
| `agents` | Agent 元数据注册（id, role, tribe, capabilities, status） |
| `tasks` | 任务执行记录（id, title, status, assigned_agent, guardian_verdict, quality_score） |
| `knowledge` | 知识库条目（id, title, content, category, quality_score, verified） |
| `prompt_templates` | Prompt 模板（id, role, template, version, success_rate） |
| `workflow_templates` | 工作流模板（id, steps/JSON, trigger_pattern, success_rate） |
| `evaluations` | 任务评估记录（task_id, agent_id, accuracy, novelty, overall_score） |
| `consensus` | 社区共识（topic, main_position, confidence, supporting_agents） |
| `instances` | 实例注册表（name, type, port, pid, status, path）**v2.0 新增** |

### 访问模式

```python
from core.database import async_session

async with async_session() as session:
    # 查询
    result = await session.execute(select(Task).where(Task.status == "success"))
    tasks = result.scalars().all()

    # 插入
    new_task = Task(id="...", title="...", status="running")
    session.add(new_task)
    await session.commit()
```

---

## 12. 测试

### 运行测试

```bash
# 运行所有测试
conda run -n claw python -m pytest tests/ -v

# 运行单个模块测试
conda run -n claw python -m pytest tests/test_skills.py -v

# 带覆盖率报告
conda run -n claw python -m pytest tests/ --cov=. --cov-report=term-missing
```

### 手动测试实例管理

```bash
# 测试实例类型 API
curl http://localhost:10101/api/instances/types

# 测试启动单个实例
curl -X POST http://localhost:10101/api/instances/start \
  -H "Content-Type: application/json" \
  -d '{"name": "test-ai", "instance_type": "clawer-ai"}'

# 等待 3 秒后测试健康
sleep 3
curl http://localhost:10112/health

# 委托任务
curl -X POST http://localhost:10112/api/tasks/execute \
  -H "Content-Type: application/json" \
  -d '{"task": "简述 Transformer 架构"}'

# 清理
curl -X POST http://localhost:10101/api/instances/stop/test-ai
```

### 技能单元测试

```python
import asyncio
from skills.tools import execute_skill

# 测试搜索技能
result = asyncio.run(execute_skill("arxiv_search", {
    "query": "vision transformer",
    "max_results": 3,
    "category": "cs.CV"
}))
assert result["count"] > 0
assert "papers" in result

# 测试代码执行
result = asyncio.run(execute_skill("code_execute", {
    "code": "print(1 + 1)",
    "timeout": 5
}))
assert result["success"] is True
assert "2" in result["output"]
```

---

## 13. 前端架构

前端是一个 **单文件单页应用**（`web/index.html`），包含 HTML、CSS、JavaScript，无构建步骤。

### 主要组件

- 任务输入面板（支持切换 Agent、工作流模式）
- SSE 流式输出显示区
- Agent 状态卡片列表
- 系统状态仪表盘
- 实例管理面板（v2.0 新增，调用 `/api/instances/` 系列接口）

### 本地开发

直接启动编排层后打开浏览器即可：

```bash
bash start.sh web
# 访问 http://localhost:10101
```

前端通过相对路径调用 API（`/api/...`），由编排层的 FastAPI 应用同时服务静态文件。

---

## 14. 贡献规范

### 代码风格

- Python 3.11+，使用类型注解
- 异步函数优先（`async def`），避免阻塞调用
- 每个函数/类提供 docstring
- 变量命名：snake_case；类命名：PascalCase

### 提交信息格式

```
<type>(<scope>): <subject>

<body>（可选）

类型：feat / fix / docs / refactor / test / chore
范围：agents / skills / instances / api / core / config
```

示例：
```
feat(instances): 添加 clawer-quantum 实例类型定义

在 config/instance_types.yaml 中新增量子计算专项 Clawer，
端口 10120，配置 8 个相关技能。
```

### Pull Request 清单

- [ ] 新增实例类型已在 `instance_types.yaml` 中定义
- [ ] 新增技能已通过 `execute_skill()` 单测
- [ ] 新增 API 路由已包含 Pydantic 模型和 OpenAPI 文档注释
- [ ] 端口未与现有 10100-10199 段冲突
- [ ] 更新了相关文档（架构图、API 参考、技能列表）

---

## 15. 故障排查

### 子实例无法启动

```bash
# 查看详细错误
cat data/logs/research-ai.log

# 常见原因：
# 1. PYTHONPATH 未包含 openclaw 根目录 → 检查 manager.py 中的 env 设置
# 2. 端口已被占用 → lsof -i :10112
# 3. conda 环境 claw 中缺少依赖 → conda run -n claw pip install -r requirements.txt
```

### 编排层无法委托任务给子实例

```bash
# 1. 确认子实例已注册
curl http://localhost:10101/api/instances/status/research-ai

# 2. 直接访问子实例确认其可用
curl http://localhost:10112/health

# 3. 检查 instances/client.py 的 timeout 设置（默认 120s）
```

### OpenAI API 相关错误

```bash
# 检查 API Key
grep OPENAI_API_KEY .env

# 检查模型可用性
conda run -n claw python3 -c "
import openai
client = openai.OpenAI()
models = client.models.list()
print([m.id for m in models.data if 'gpt' in m.id])
"
```

### 实例注册表不一致

```bash
# 强制清空注册表，重新开始
./start.sh stop-instances
conda run -n claw python3 -c "
import asyncio, sys
sys.path.insert(0, '.')
import aiosqlite

async def clear():
    async with aiosqlite.connect('data/openclaw.db') as db:
        await db.execute('DELETE FROM instances')
        await db.commit()
    print('Done')

asyncio.run(clear())
"
./start.sh instances
```

### 端口规划冲突检查

```bash
# 检查 10100-10199 端口占用情况
for port in $(seq 10100 10199); do
  if lsof -i :$port > /dev/null 2>&1; then
    echo "PORT $port: $(lsof -i :$port | tail -1 | awk '{print $1, $2}')"
  fi
done
```

---

### Python 代码修改后容器不生效

**现象**：修改了 Python 文件，执行 `docker restart omni-research` 后变更没有生效。

**原因**：`omni-research` 镜像在构建时通过 `COPY . .` 把代码打包进镜像层，`restart` 只是重启容器，不会重新打包。

**正确做法**：
```bash
# 代码修改后，重建镜像并重启（增量构建，通常 15~40 秒）
make -C claw up

# requirements.txt 有变动时，强制无缓存重建
make -C claw up-fresh
```

---

### 飞书用户出现配对码提示

**现象**：飞书用户发消息收到：
```
OpenClaw: access not configured.
Pairing code: XXXXXXXX
Ask the bot owner to approve with: openclaw pairing approve feishu XXXXXXXX
```

**原因**：`openclaw.json` 中的飞书访问策略（`dmPolicy`/`allowFrom`）不在正确位置（需在 `accounts.main` 中），Gateway 回退为严格配对模式。

**修复**：
```bash
# 自动校验并修复策略配置
make -C claw fix-config

# 重启 Gateway 生效
docker restart omni-gateway

# 如需临时批准某个用户
make -C claw feishu-pair CODE=XXXXXXXX
```

**长期预防**：需要运行 `openclaw doctor --fix` 时，使用安全封装命令：
```bash
make -C claw doctor   # 内部自动修正 dmPolicy 位置
```

---

### InterestExtractor JSON 解析警告

**现象**：日志中出现：
```
[InterestExtractor] Failed to extract interests: Expecting property name enclosed in double quotes
```

**原因**：LLM 返回的 JSON 包含尾随逗号（如 `["k1", "k2",]`），`json.loads()` 标准解析失败。

**状态**：已在 `core/interest_extractor.py` 中修复，增加尾随逗号清理和正则提取，不影响主流程，重新部署后不再出现。
