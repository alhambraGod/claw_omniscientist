# 🦞 OpenClaw 科研智能体社区系统

> **面向科研、教育、学术创新场景的群体智能操作系统**
> v1.0 | Academic AI Agent Community OS

---

## 目录
- [系统概述](#系统概述)
- [快速开始](#快速开始)
- [Agent 社区成员](#agent-社区成员)
- [技能库（28个核心技能）](#技能库)
- [Web 界面使用](#web-界面使用)
- [CLI 命令行使用](#cli-命令行使用)
- [API 接口文档](#api-接口文档)
- [工作流说明](#工作流说明)
- [配置说明](#配置说明)
- [架构说明](#架构说明)

---

## 系统概述

OpenClaw 是一个**科研智能体社区操作系统**，由 15 个 OpenClaw 智能体组成，分 6 种角色协同工作：

| 角色 | 数量 | 职责 |
|------|------|------|
| 🦞 **Clawer** (工作者) | 10个 | 科研生产力执行：文献综述、实验设计、数据分析、论文写作 |
| 🛡️ **Guardian** (守护者) | 1个 | 风险治理：输入审查、输出复核、合规执行 |
| 🔭 **Vanguard** (拓荒者) | 1个 | 前沿探索：趋势发现、数据集挖掘、机会识别 |
| 🔧 **Maintainer** (维护者) | 1个 | 运行维护：健康监控、故障诊断、性能优化 |
| 📣 **Promoter** (推广者) | 1个 | 影响力扩展：内容草稿、校园推广、反馈分析 |
| 💧 **Wellspring** (源泉) | 1个 | 群体进化：知识沉淀、共识形成、能力演化 |

---

## 快速开始

### 1. 环境准备

```bash
# 已在 conda claw 环境中安装完毕
# 如需重新安装：
conda create -n claw python=3.11 -y
conda run -n claw pip install -r requirements.txt
```

### 2. 配置 API Key

```bash
# 编辑 .env 文件（已从 .env.example 自动创建）
nano .env

# 必须配置：
ANTHROPIC_API_KEY=sk-ant-xxxxxxxx   # Claude API Key
```

> 💡 **无 API Key 也可运行**：系统架构、路由、技能库全部可用，AI 推理功能需要 Key

### 3. 启动系统

```bash
# 方式一：Web 界面（推荐）
bash start.sh web 8001

# 方式二：CLI 交互模式
bash start.sh cli

# 方式三：直接命令
conda run -n claw uvicorn api.main:app --port 8001 --reload
```

### 4. 验证安装

```bash
```

---

## Agent 社区成员

### Clawer 工作者池（10个）

| Agent ID | 专业领域 | 核心能力 |
|----------|---------|---------|
| `clawer-math-01` | 数学建模与证明 | 数学推理、定理证明、方程求解 |
| `clawer-cs-01` | 计算机科学 | 代码评审、算法分析、系统设计 |
| `clawer-cs-02` | AI/ML 研究 | 模型评估、论文精读、实验设计 |
| `clawer-bio-01` | 生命科学 | 生物文献、序列分析、通路分析 |
| `clawer-edu-01` | 教育研究 | 课程设计、学习分析、教学法评估 |
| `clawer-survey-01` | 文献综述 | 系统综述、引用分析、研究空白识别 |
| `clawer-writing-01` | 学术写作 | 论文写作、摘要生成、语言润色 |
| `clawer-data-01` | 数据分析 | 统计分析、可视化、结果解读 |
| `clawer-experiment-01` | 实验设计 | 实验方案、方法论评估、协议生成 |
| `clawer-review-01` | 同行评审 | 批判评审、逻辑分析、改进建议 |

### 职能型 Agent（5个）

| Agent ID | 角色 | 核心职责 |
|----------|------|---------|
| `guardian-01` | 守护者 | 风险识别、内容审核、合规执行 |
| `vanguard-01` | 拓荒者 | 前沿探索、趋势发现、机会识别 |
| `maintainer-01` | 维护者 | 健康监控、故障诊断、性能优化 |
| `promoter-01` | 推广者 | 内容传播、校园推广、反馈分析 |
| `wellspring-01` | 源泉 | 知识沉淀、共识形成、能力进化 |

---

## 技能库

系统内置 **28 个核心技能**，覆盖前50技能类别：

### 搜索与信息检索（5个）
| 技能 | 描述 |
|------|------|
| `web_search` | DuckDuckGo 网络搜索，获取最新信息 |
| `arxiv_search` | arXiv 学术论文搜索，支持分类过滤 |
| `semantic_scholar_search` | Semantic Scholar 搜索，含引用数据 |
| `url_fetch` | 抓取网页正文内容 |
| `github_search` | GitHub 仓库搜索 |

### 文档与知识处理（5个）
| 技能 | 描述 |
|------|------|
| `pdf_extract` | PDF 文本提取（本地/URL） |
| `text_summarize` | 智能文本摘要（学术/新闻/技术） |
| `citation_format` | 论文引用格式化（APA/MLA/Chicago） |
| `knowledge_extract` | 结构化知识提取 |
| `translation` | 学术内容中英文翻译 |

### 代码与计算（4个）
| 技能 | 描述 |
|------|------|
| `code_execute` | 安全 Python 代码执行 |
| `code_review` | 代码质量评审 |
| `math_solve` | 数学问题求解 |
| `data_analysis` | 统计数据分析 |

### 学术研究（7个）
| 技能 | 描述 |
|------|------|
| `research_outline` | 研究提纲生成 |
| `abstract_generate` | 学术摘要生成 |
| `hypothesis_generate` | 科学假设生成 |
| `experiment_design` | 实验方案设计 |
| `peer_review` | 同行评审意见 |
| `gap_analysis` | 研究空白分析 |
| `trend_analysis` | 领域趋势分析 |

### 系统与运维（7个）
| 技能 | 描述 |
|------|------|
| `task_decompose` | 复杂任务分解 |
| `quality_score` | 输出质量评分 |
| `dataset_discover` | 数据集发现推荐 |
| `report_generate` | 研究报告生成 |
| `fact_check` | 事实核查 |
| `mind_map` | 思维导图生成 |
| `content_plan` | 内容传播规划 |

---

## Web 界面使用

访问 **http://localhost:8001**

### 科研对话
1. 在底部输入框输入科研任务
2. 可选择指定 Agent 或让系统自动路由
3. 勾选「多 Agent 模式」启用工作流
4. `Ctrl+Enter` 快速发送

**示例任务：**
```
帮我综述 Transformer 架构的最新发展，重点关注效率优化方向
设计一个研究实验：探究大语言模型在数学推理任务上的思维链效果
分析当前 AI for Science 领域的前沿趋势和研究机会
```

### 前沿探索
1. 点击左侧「前沿探索」
2. 输入研究领域（如：大语言模型）
3. 可选输入重点方向（如：推理能力）
4. 点击「探索」按钮

### Agent 社区
- 查看所有 15 个 Agent 的状态
- 点击「与 TA 对话」直接指定 Agent

### 工作流
三个标准工作流：
- **文献综述**：5步骤，多 Clawer 协作
- **研究设计**：并行执行 + 汇总
- **前沿发现**：Vanguard + Clawer + Wellspring

---

## CLI 命令行使用

```bash
# 进入项目目录
cd /Users/antonio/openclaws/openclaw_research

# 所有命令通过 conda run 执行
alias claw="conda run -n claw python cli/main.py"
```

### 核心命令

#### 执行任务
```bash
# 自动路由
claw run "帮我综述 Transformer 架构的最新发展"

# 指定 Agent
claw run "分析这段代码的安全漏洞" --agent clawer-cs-01

# 多 Agent 工作流
claw run "设计联邦学习的实验方案" --multi

# 输出 JSON
claw run "介绍注意力机制" --json
```

#### 快速问答
```bash
claw ask "Transformer 的核心创新是什么？"
claw ask "什么是对比学习？" --domain ai
```

#### 查看 Agent
```bash
claw agents                    # 列出所有 Agent
claw agents --role clawer      # 按角色过滤
```

#### 前沿探索
```bash
claw explore "大语言模型"
claw explore "蛋白质结构预测" --focus "AlphaFold 后续进展"
```

#### 交互对话
```bash
claw chat                      # 自动路由对话
claw chat --agent clawer-cs-02 # 与指定 Agent 对话

# 对话中的命令：
# /agents   查看所有 Agent
# /status   系统状态
# /explore <领域>  前沿探索
# /quit     退出
```

#### 系统管理
```bash
claw status                    # 系统状态
claw skills                    # 所有技能列表
claw wellspring                # Wellspring 统计
```

---

## API 接口文档

交互式文档：`http://localhost:8001/docs`

### 核心接口

#### 执行任务
```http
POST /api/tasks/execute
Content-Type: application/json

{
  "task": "帮我综述 Transformer 架构",
  "user_id": "researcher-001",
  "agent_id": null,        // 可选，指定 Agent
  "context": {}            // 可选，上下文
}
```

**响应示例：**
```json
{
  "task_id": "a1b2c3d4",
  "status": "success",
  "agent_name": "AI/ML 研究 Clawer",
  "role": "clawer",
  "result": "## Transformer 架构综述\n\n...",
  "iterations": 3,
  "guardian_verdict": "approved"
}
```

#### 快速问答
```http
POST /api/tasks/ask
{
  "question": "什么是注意力机制？",
  "domain": "ai"
}
```

#### 多 Agent 工作流
```http
POST /api/tasks/multi-agent
{
  "task": "为 RAG 系统设计完整实验方案"
}
```

#### 预览路由
```http
POST /api/tasks/route
{
  "task": "帮我做文献综述"
}
# 返回路由决策，不执行任务
```

#### Vanguard 探索
```http
POST /api/system/vanguard/explore?domain=大语言模型&focus=推理能力
```

#### Guardian 审核
```http
POST /api/system/guardian/review?content=待审核内容&review_type=output
```

#### 系统状态
```http
GET /api/system/status
GET /api/agents/
GET /api/system/skills
```

---

## 工作流说明

### 工作流 1：文献综述
**触发词**：文献综述、systematic review、literature review

```
Vanguard (前沿扫描)
    ↓
Survey Clawer (系统检索)
    ↓
Review Clawer (批判评审)
    ↓
Writing Clawer (整理成稿)
    ↓
Guardian (输出审核)
```

### 工作流 2：研究方案设计
**触发词**：研究设计、实验方案、experiment design

```
Survey Clawer (背景调研) ──┐
                           ├→ 汇总 → Guardian
Experiment Clawer (方案设计)┘
    ↓
Data Clawer (数据可行性分析)
```

### 工作流 3：前沿方向发现
**触发词**：前沿、趋势、最新进展、emerging

```
Vanguard (多源扫描: arXiv + GitHub)
    ↓
CS Clawer (深度技术分析)
    ↓
Wellspring (知识沉淀)
    ↓
Promoter (传播内容草稿) → Guardian 审核
```

---

## 配置说明

### .env 配置文件

```env
# 必须配置
ANTHROPIC_API_KEY=sk-ant-xxxxx    # Claude API Key

# 可选配置
OPENAI_API_KEY=sk-xxxxx           # OpenAI 备用（可选）
TAVILY_API_KEY=tvly-xxxxx         # 高质量搜索（可选）
DINGTALK_WEBHOOK=https://...      # 钉钉集成（可选）

# 服务配置
PORT=8001                         # 服务端口
DEFAULT_MODEL=claude-sonnet-4-6   # 默认模型
```

### 申请 Anthropic API Key
1. 访问 https://console.anthropic.com
2. 注册并登录
3. 在 API Keys 页面创建新 Key
4. 填入 `.env` 文件的 `ANTHROPIC_API_KEY`

---

## 架构说明

```
openclaw/
├── agents/          # 智能体实现
│   ├── base.py      # 基类（LLM + tool_use 循环）
│   ├── clawer.py    # 10个工作者 Clawer
│   ├── guardian.py  # 守护者
│   ├── vanguard.py  # 拓荒者
│   ├── maintainer.py # 维护者
│   ├── promoter.py  # 推广者
│   └── wellspring.py # 源泉
├── skills/
│   └── tools.py     # 28个核心技能实现
├── core/
│   ├── registry.py  # Agent 注册中心
│   ├── router.py    # 智能任务路由
│   ├── orchestrator.py # 任务编排器
│   └── database.py  # SQLite 持久化
├── api/
│   ├── main.py      # FastAPI 应用入口
│   └── routes/      # API 路由
├── web/
│   └── index.html   # Web 控制台
├── cli/
│   └── main.py      # CLI 工具
├── config/
│   ├── settings.py  # 全局配置
├── start.sh         # 启动脚本
└── .env             # 密钥配置
```

### 技术栈
- **AI 引擎**: Anthropic Claude (claude-sonnet-4-6)
- **后端**: FastAPI + Python 3.11
- **数据库**: SQLite (aiosqlite)
- **前端**: HTML + TailwindCSS + Vanilla JS
- **CLI**: Typer + Rich
- **部署**: conda claw 环境

---

## 社区文明协议

所有 OpenClaw Agent 遵循以下协议：
1. **证据优先** - 关键结论必须有充分依据
2. **置信度标注** - 用 [高/中/低] 标注核心结论
3. **可追踪** - 尽量引用具体文献、数据集
4. **允许异议** - 存在争议时呈现多方观点
5. **风险可治理** - 高风险内容必须经 Guardian 审核

---

## 常见问题

**Q: API Key 没有，可以使用吗？**
A: 可以运行系统、查看 Agent、测试路由。AI 推理功能需要 Key。

**Q: 如何切换使用的模型？**
A: 修改 `.env` 中的 `DEFAULT_MODEL`，支持所有 Anthropic 模型。

**Q: 如何添加新的 Clawer？**
A: 在 `agents/clawer.py` 的 `build_all_clawers()` 中添加新条目，重启即可。

**Q: 如何接入钉钉？**
A: 在 `.env` 中配置 `DINGTALK_WEBHOOK`，后续版本将完善钉钉集成。

---

*OpenClaw v1.0 | 构建中的科研智能体文明 🦞*
