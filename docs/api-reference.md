# openclaw_research API 接口参考

> 版本：v2.0 | Base URL：`http://localhost:10101`
>
> 交互式文档：`http://localhost:10101/docs`（Swagger UI）

---

## 目录

1. [全局说明](#1-全局说明)
2. [系统接口](#2-系统接口)
3. [Agent 管理接口](#3-agent-管理接口)
4. [任务执行接口](#4-任务执行接口)
5. [流式执行接口（SSE）](#5-流式执行接口sse)
6. [Wellspring 知识接口](#6-wellspring-知识接口)
7. [系统管理接口](#7-系统管理接口)
8. [实例管理 API（v2.0 新增）](#8-实例管理-apiv20-新增)
9. [认证机制](#9-认证机制)
10. [错误码说明](#10-错误码说明)

---

## 1. 全局说明

### 1.1 基础信息

| 项目 | 说明 |
|------|------|
| 系统名称 | openclaw_research |
| 编排层端口 | 10101 |
| 协议 | HTTP/HTTPS |
| 数据格式 | JSON（除 SSE 接口） |
| 字符编码 | UTF-8 |
| 请求头 | `Content-Type: application/json` |
| 响应头 | `Content-Type: application/json` |

### 1.2 端口规划

| 端口 | 用途 |
|------|------|
| 10101 | 编排层（Web UI + API 网关）—— 所有外部请求入口 |
| 10110-10119 | 10 个 Clawer 子实例（直接访问或由编排层转发） |
| 10130-10134 | 5 个功能性 Agent 子实例 |

通常情况下，所有 API 调用均通过编排层（10101）进行，编排层负责将请求路由到合适的子实例。

### 1.3 通用响应结构

成功响应（任务执行类）：

```json
{
  "task_id": "a1b2c3d4",
  "agent_id": "clawer-cs-02",
  "agent_name": "AI/ML 研究 Clawer",
  "role": "clawer",
  "status": "success",
  "result": "任务执行结果文本...",
  "task": "用户提交的原始任务",
  "iterations": 3,
  "timestamp": "2026-03-12T08:30:00.000000",
  "guardian_verdict": "approved",
  "risk_score": 0.05
}
```

被拒绝响应：

```json
{
  "task_id": "a1b2c3d4",
  "status": "rejected",
  "message": "任务被 Guardian 拒绝",
  "issues": ["包含不当内容"],
  "recommendation": "请修改任务描述后重试",
  "risk_level": "high"
}
```

错误响应（HTTP 4xx/5xx）：

```json
{
  "detail": "错误描述信息"
}
```

---

## 2. 系统接口

### 2.1 健康检查

```
GET /health
```

**说明**：服务健康状态检查，用于负载均衡器和监控系统轮询。

**响应示例**：

```json
{
  "status": "ok",
  "service": "openclaw_research",
  "version": "2.0.0"
}
```

**curl 示例**：

```bash
curl http://localhost:10101/health

# 也可以直接检查各子实例
curl http://localhost:10112/health   # clawer-ai
curl http://localhost:10130/health   # guardian
```

---

## 3. Agent 管理接口

### 3.1 获取所有 Agent 列表

```
GET /api/agents/
```

**说明**：返回社区中所有 15 个 Agent 的信息摘要，包括角色、状态、技能数量。

**响应示例**：

```json
{
  "agents": [
    {
      "agent_id": "clawer-cs-01",
      "name": "计算机科学 Clawer",
      "role": "clawer",
      "model": "gpt-5.3-chat-latest",
      "tool_count": 11,
      "status": "active"
    },
    {
      "agent_id": "guardian-01",
      "name": "守护者 Guardian",
      "role": "guardian",
      "model": "gpt-5.3-chat-latest",
      "tool_count": 6,
      "status": "active"
    }
  ],
  "summary": {
    "total_agents": 15,
    "active_agents": 15,
    "degraded_agents": 0,
    "by_role": {
      "clawer": 10,
      "guardian": 1,
      "vanguard": 1,
      "maintainer": 1,
      "promoter": 1,
      "wellspring": 1
    }
  }
}
```

**curl 示例**：

```bash
curl http://localhost:10101/api/agents/
```

---

### 3.2 获取单个 Agent 详情

```
GET /api/agents/{agent_id}
```

**路径参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `agent_id` | string | Agent ID，如 `clawer-cs-01`、`guardian-01` |

**响应示例**：

```json
{
  "agent_id": "clawer-cs-01",
  "name": "计算机科学 Clawer",
  "role": "clawer",
  "model": "gpt-5.3-chat-latest",
  "tools": [
    "code_execute", "code_review", "code_explain", "code_document",
    "github_search", "arxiv_search", "web_search",
    "benchmark_design", "gap_analysis", "peer_review", "text_summarize"
  ],
  "status": "active"
}
```

**curl 示例**：

```bash
curl http://localhost:10101/api/agents/clawer-cs-01
```

---

### 3.3 按角色获取 Agent 列表

```
GET /api/agents/role/{role}
```

**路径参数**：

| 参数 | 类型 | 可选值 |
|------|------|--------|
| `role` | string | `clawer`、`guardian`、`vanguard`、`maintainer`、`promoter`、`wellspring` |

**curl 示例**：

```bash
curl http://localhost:10101/api/agents/role/clawer
curl http://localhost:10101/api/agents/role/guardian
```

---

## 4. 任务执行接口

### 4.1 执行科研任务（核心接口）

```
POST /api/tasks/execute
```

**说明**：openclaw_research 的核心接口。提交科研任务，系统自动路由到最合适的 Agent 或触发多 Agent 工作流。执行流程包含 Guardian 输入/输出双重审核。编排层会优先将任务委托给对应的子实例执行。

**请求体**：

```json
{
  "task": "请帮我综述近三年 Transformer 在计算机视觉中的应用进展",
  "user_id": "user-123",
  "agent_id": null,
  "context": {
    "research_area": "computer_vision",
    "target_length": "3000字"
  }
}
```

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| `task` | string | 是 | 任务描述，自然语言，支持中英文 |
| `user_id` | string | 否 | 用户标识，默认 `"anonymous"` |
| `agent_id` | string | 否 | 指定特定 Agent ID；为 null 时自动路由 |
| `context` | object | 否 | 附加上下文信息，会追加到任务描述中 |

**响应示例**：

```json
{
  "task_id": "3f8a1b2c",
  "agent_id": "clawer-cs-02",
  "agent_name": "AI/ML 研究 Clawer",
  "role": "clawer",
  "status": "success",
  "result": "## Transformer 在计算机视觉中的应用综述\n\n...",
  "task": "请帮我综述近三年 Transformer 在计算机视觉中的应用进展",
  "iterations": 4,
  "timestamp": "2026-03-12T08:30:00.000000",
  "guardian_verdict": "approved",
  "risk_score": 0.03
}
```

**curl 示例**：

```bash
# 自动路由执行
curl -X POST http://localhost:10101/api/tasks/execute \
  -H "Content-Type: application/json" \
  -d '{
    "task": "请分析 attention mechanism 的数学原理",
    "user_id": "researcher-001"
  }'

# 指定特定 Agent
curl -X POST http://localhost:10101/api/tasks/execute \
  -H "Content-Type: application/json" \
  -d '{
    "task": "检查这段 Python 代码的性能问题",
    "agent_id": "clawer-cs-01"
  }'
```

---

### 4.2 快速问答

```
POST /api/tasks/ask
```

**说明**：简化版任务接口，自动选择最合适的 Clawer 直接回答问题，不经过完整工作流编排（无 Guardian 审核）。适用于简单查询场景。

**请求体**：

```json
{
  "question": "什么是 p-value？如何正确解读？",
  "domain": "statistics",
  "user_id": "student-001"
}
```

**curl 示例**：

```bash
curl -X POST http://localhost:10101/api/tasks/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "BERT 和 GPT 的主要区别是什么？", "domain": "nlp"}'
```

---

### 4.3 获取任务历史

```
GET /api/tasks/history?limit=20
```

**curl 示例**：

```bash
curl "http://localhost:10101/api/tasks/history?limit=10"
```

---

### 4.4 获取正在运行的任务

```
GET /api/tasks/running
```

**curl 示例**：

```bash
curl http://localhost:10101/api/tasks/running
```

---

### 4.5 预览任务路由（不执行）

```
POST /api/tasks/route
```

**说明**：分析任务文本并返回路由决策，**不实际执行任务**。用于调试和了解系统如何分配任务。

**响应示例**：

```json
{
  "route": {
    "task_id": "preview-x1y2",
    "task_type": "workflow",
    "execution_mode": "workflow",
    "primary_agent": "clawer-survey-01",
    "workflow_id": "wf-literature-review",
    "needs_guardian": true
  }
}
```

**curl 示例**：

```bash
curl -X POST http://localhost:10101/api/tasks/route \
  -H "Content-Type: application/json" \
  -d '{"task": "帮我做一个关于量子计算的文献综述"}'
```

---

### 4.6 强制多 Agent 工作流

```
POST /api/tasks/multi-agent
```

**说明**：强制以工作流模式执行任务。

**curl 示例**：

```bash
curl -X POST http://localhost:10101/api/tasks/multi-agent \
  -H "Content-Type: application/json" \
  -d '{"task": "分析深度学习在医学影像诊断中的研究现状与挑战"}'
```

---

## 5. 流式执行接口（SSE）

### 5.1 流式任务执行

```
POST /api/tasks/stream
```

**说明**：使用 Server-Sent Events（SSE）流式返回任务执行过程。

**请求体**：

```json
{
  "task": "帮我分析 Mamba 模型与 Transformer 的性能对比",
  "agent_id": null,
  "user_id": "user-001"
}
```

**响应格式**：`text/event-stream`

**SSE 事件类型**：

| 事件名 | 说明 | 数据示例 |
|--------|------|----------|
| `start` | 任务已接收 | `{"message": "任务已接收，正在路由..."}` |
| `routing` | 路由决策完成 | `{"agent_id": "clawer-cs-02", "agent_name": "AI/ML 研究 Clawer", "mode": "direct"}` |
| `guardian_input` | Guardian 正在审核输入 | `{"message": "Guardian 正在审核输入..."}` |
| `guardian_passed` | Guardian 审核通过 | `{"verdict": "approved", "risk_score": 0.05}` |
| `rejected` | Guardian 拒绝任务 | `{"verdict": "rejected", "risk_score": 0.95, "issues": [...]}` |
| `executing` | Agent 开始执行 | `{"agent_name": "AI/ML 研究 Clawer", "message": "正在处理..."}` |
| `chunk` | 结果片段（多次触发） | `{"text": "## 性能对比分析\n\n"}` |
| `guardian_output` | Guardian 审核输出 | `{"message": "Guardian 正在审核输出..."}` |
| `complete` | 执行完成 | `{"status": "success", "agent_name": "...", "guardian_verdict": "approved", "iterations": 3}` |
| `error` | 执行出错 | `{"message": "错误描述"}` |
| `done` | 连接即将关闭 | `{"status": "finished"}` |

**curl 示例**：

```bash
curl -N -X POST http://localhost:10101/api/tasks/stream \
  -H "Content-Type: application/json" \
  -d '{"task": "简述 Diffusion Model 的工作原理"}'
```

**JavaScript 客户端示例**：

```javascript
async function streamTask(task) {
  const response = await fetch('/api/tasks/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ task, user_id: 'web-user' })
  });

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let eventType = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop();

    for (const line of lines) {
      if (line.startsWith('event: ')) {
        eventType = line.slice(7).trim();
      } else if (line.startsWith('data: ')) {
        const data = JSON.parse(line.slice(6));
        if (eventType === 'chunk') {
          document.getElementById('result').innerHTML += data.text;
        } else if (eventType === 'complete') {
          console.log('完成:', data);
        } else if (eventType === 'rejected') {
          console.warn('被拒绝:', data.issues);
        }
      }
    }
  }
}
```

**Python 客户端示例**：

```python
import httpx
import json

def stream_task(task: str):
    with httpx.Client(timeout=300) as client:
        with client.stream(
            "POST",
            "http://localhost:10101/api/tasks/stream",
            json={"task": task, "user_id": "python-client"}
        ) as response:
            buffer = ""
            event_type = ""
            for line in response.iter_lines():
                if line.startswith("event: "):
                    event_type = line[7:].strip()
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
                    if event_type == "chunk":
                        print(data["text"], end="", flush=True)
                    elif event_type == "complete":
                        print(f"\n\n[完成] Agent: {data['agent_name']}")
                    elif event_type == "done":
                        break

stream_task("分析 GPT-5 的架构设计")
```

---

## 6. Wellspring 知识接口

### 6.1 注入任务结果到知识库

```
POST /api/wellspring/ingest
```

**请求体**：

```json
{
  "task_result": {
    "task": "分析 BERT 的预训练策略",
    "status": "success",
    "result": "BERT 使用掩码语言模型（MLM）和下一句预测（NSP）...",
    "agent_name": "AI/ML 研究 Clawer",
    "quality_score": 0.85
  }
}
```

**curl 示例**：

```bash
curl -X POST http://localhost:10101/api/wellspring/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "task_result": {
      "task": "总结 LoRA 技术的核心思想",
      "status": "success",
      "result": "LoRA 通过低秩分解减少可训练参数...",
      "quality_score": 0.9
    }
  }'
```

---

### 6.2 形成社区共识

```
POST /api/wellspring/consensus
```

**curl 示例**：

```bash
curl -X POST http://localhost:10101/api/wellspring/consensus \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "大模型 fine-tuning 与 RAG 的适用场景比较",
    "agent_opinions": [
      {"agent_id": "clawer-cs-01", "opinion": "RAG 适合知识频繁更新的场景..."},
      {"agent_id": "clawer-cs-02", "opinion": "Fine-tuning 对特定领域任务效果更好..."}
    ]
  }'
```

---

### 6.3 获取 Wellspring 统计信息

```
GET /api/wellspring/stats
```

**响应示例**：

```json
{
  "shared_memory_count": 42,
  "knowledge_hub_count": 18,
  "prompt_hub_count": 3,
  "workflow_hub_count": 5,
  "consensus_count": 7
}
```

**curl 示例**：

```bash
curl http://localhost:10101/api/wellspring/stats
```

---

### 6.4 获取社区知识摘要

```
GET /api/wellspring/digest
```

**curl 示例**：

```bash
curl http://localhost:10101/api/wellspring/digest
```

---

## 7. 系统管理接口

### 7.1 系统整体状态

```
GET /api/system/status
```

**curl 示例**：

```bash
curl http://localhost:10101/api/system/status
```

---

### 7.2 获取系统告警

```
GET /api/system/alerts?limit=20
```

**curl 示例**：

```bash
curl "http://localhost:10101/api/system/alerts?limit=10"
```

---

### 7.3 诊断任务失败

```
POST /api/system/diagnose?task_id={task_id}&error={error}
```

**curl 示例**：

```bash
curl -X POST "http://localhost:10101/api/system/diagnose?task_id=3f8a1b2c&error=API+timeout+after+300s"
```

---

### 7.4 列出所有技能

```
GET /api/system/skills
```

**响应示例**：

```json
{
  "skills": [
    {
      "name": "web_search",
      "description": "搜索互联网获取最新信息。"
    },
    {
      "name": "arxiv_search",
      "description": "在 arXiv 搜索最新学术论文。"
    }
  ],
  "count": 51
}
```

**curl 示例**：

```bash
curl http://localhost:10101/api/system/skills
```

---

### 7.5 手动触发 Guardian 审核

```
POST /api/system/guardian/review?content={content}&review_type={type}
```

| 参数 | 类型 | 可选值 |
|------|------|--------|
| `content` | string | 待审核内容 |
| `review_type` | string | `input`、`output`、`publish` |

**curl 示例**：

```bash
curl -X POST "http://localhost:10101/api/system/guardian/review?content=这是一段学术分析文字&review_type=output"
```

---

### 7.6 触发 Vanguard 前沿探索

```
POST /api/system/vanguard/explore?domain={domain}&focus={focus}
```

**curl 示例**：

```bash
curl -X POST "http://localhost:10101/api/system/vanguard/explore?domain=量子计算&focus=量子纠错"
```

---

## 8. 实例管理 API（v2.0 新增）

实例管理 API 由 `api/routes/instances.py` 实现，路由前缀为 `/api/instances`。

### 8.1 列出所有实例类型定义

```
GET /api/instances/types
```

**说明**：返回 `config/instance_types.yaml` 中定义的所有 15 种实例类型摘要（不含编排层自身）。

**响应示例**：

```json
{
  "types": [
    {
      "type": "orchestrator",
      "port": 10101,
      "description": "系统总编排者，管理并协调所有专项实例，拥有全部技能",
      "system_role": "orchestrator",
      "skill_count": "all"
    },
    {
      "type": "clawer-ai",
      "port": 10112,
      "description": "AI/ML 研究 Clawer：深度学习、模型评估、实验设计",
      "system_role": "clawer",
      "skill_count": 11
    },
    {
      "type": "guardian",
      "port": 10130,
      "description": "风控守卫：内容合规、风险评估、质量把关",
      "system_role": "guardian",
      "skill_count": 6
    }
  ]
}
```

**curl 示例**：

```bash
curl http://localhost:10101/api/instances/types
```

---

### 8.2 获取指定类型的完整配置

```
GET /api/instances/types/{type_name}
```

**路径参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `type_name` | string | 实例类型名称，如 `clawer-ai`、`guardian`、`vanguard` |

**响应示例**：

```json
{
  "type": "clawer-ai",
  "config": {
    "port": 10112,
    "description": "AI/ML 研究 Clawer：深度学习、模型评估、实验设计",
    "system_role": "clawer",
    "agent_config": "clawer-cs-02",
    "skills": [
      "arxiv_search",
      "paper_compare",
      "experiment_design",
      "data_analysis",
      "statistical_test",
      "methodology_eval",
      "benchmark_design",
      "hypothesis_generate",
      "trend_analysis",
      "fact_check",
      "dataset_discover"
    ]
  }
}
```

**错误响应**（类型不存在）：

```json
{"detail": "类型 'clawer-xyz' 不存在"}
```
HTTP 状态码：`404`

**curl 示例**：

```bash
curl http://localhost:10101/api/instances/types/clawer-ai
curl http://localhost:10101/api/instances/types/guardian
curl http://localhost:10101/api/instances/types/vanguard
```

---

### 8.3 列出所有已注册实例及状态

```
GET /api/instances/
```

**说明**：返回所有已在数据库中注册的实例，并实时检测每个进程是否存活（通过 `os.kill(pid, 0)`）。

**响应示例**：

```json
{
  "instances": [
    {
      "name": "research-math",
      "type": "clawer-math",
      "port": 10110,
      "status": "running",
      "pid": 12345,
      "alive": true,
      "path": "/Users/antonio/openclaws/openclaw_research"
    },
    {
      "name": "research-ai",
      "type": "clawer-ai",
      "port": 10112,
      "status": "running",
      "pid": 12346,
      "alive": true,
      "path": "/Users/antonio/openclaws/openclaw_research"
    },
    {
      "name": "research-guardian",
      "type": "guardian",
      "port": 10130,
      "status": "stopped",
      "pid": null,
      "alive": false,
      "path": "/Users/antonio/openclaws/openclaw_research"
    }
  ]
}
```

**curl 示例**：

```bash
curl http://localhost:10101/api/instances/
```

---

### 8.4 启动实例

```
POST /api/instances/start
```

**说明**：启动一个 openclaw 子实例进程。`instances/manager.py` 会自动注入 `INSTANCE_TYPE`、`ALLOWED_SKILLS`、`PORT` 等环境变量，无需手动配置。若端口已被占用或实例已在运行，会返回错误。

**请求体**：

```json
{
  "name": "research-ai",
  "instance_type": "clawer-ai",
  "path": null,
  "port": null
}
```

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 实例唯一名称，如 `research-ai` |
| `instance_type` | string | 是 | 实例类型，必须是 `instance_types.yaml` 中定义的 key |
| `path` | string | 否 | 代码根目录，默认使用编排层相同目录 |
| `port` | integer | 否 | 监听端口，默认使用类型定义中的端口 |

**成功响应**：

```json
{
  "success": true,
  "name": "research-ai",
  "type": "clawer-ai",
  "port": 10112,
  "pid": 12346,
  "url": "http://localhost:10112",
  "log": "/Users/antonio/openclaws/openclaw_research/data/logs/research-ai.log"
}
```

**失败响应示例**：

```json
{"detail": "端口 10112 已被占用，无法启动 research-ai"}
```

**curl 示例**：

```bash
# 使用默认端口启动 AI 研究实例
curl -X POST http://localhost:10101/api/instances/start \
  -H "Content-Type: application/json" \
  -d '{"name": "research-ai", "instance_type": "clawer-ai"}'

# 启动 guardian 实例
curl -X POST http://localhost:10101/api/instances/start \
  -H "Content-Type: application/json" \
  -d '{"name": "research-guardian", "instance_type": "guardian"}'

# 启动 vanguard 并自定义端口
curl -X POST http://localhost:10101/api/instances/start \
  -H "Content-Type: application/json" \
  -d '{"name": "research-vanguard", "instance_type": "vanguard", "port": 10131}'
```

---

### 8.5 停止实例

```
POST /api/instances/stop/{name}
```

**说明**：向目标实例进程发送 SIGTERM 信号，并在数据库中将状态更新为 `stopped`。

**路径参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `name` | string | 实例名称，如 `research-ai` |

**成功响应**：

```json
{
  "success": true,
  "name": "research-ai",
  "pid": 12346
}
```

**curl 示例**：

```bash
curl -X POST http://localhost:10101/api/instances/stop/research-ai
curl -X POST http://localhost:10101/api/instances/stop/research-guardian
```

---

### 8.6 查询实例实时状态

```
GET /api/instances/status/{name}
```

**说明**：查询指定实例的当前状态，包含实时进程存活检测。

**响应示例**：

```json
{
  "name": "research-ai",
  "type": "clawer-ai",
  "port": 10112,
  "status": "running",
  "pid": 12346,
  "alive": true,
  "path": "/Users/antonio/openclaws/openclaw_research"
}
```

**curl 示例**：

```bash
curl http://localhost:10101/api/instances/status/research-ai
curl http://localhost:10101/api/instances/status/research-guardian
```

---

### 8.7 注销实例

```
DELETE /api/instances/{name}
```

**说明**：先停止实例进程，再从注册表中删除记录。

**响应示例**：

```json
{
  "success": true,
  "name": "research-ai"
}
```

**curl 示例**：

```bash
curl -X DELETE http://localhost:10101/api/instances/research-ai
```

---

### 8.8 实例管理完整工作流示例

```bash
# 1. 查看所有可用类型
curl http://localhost:10101/api/instances/types | python3 -m json.tool

# 2. 启动所有 Clawer 子实例
for type in clawer-math clawer-cs clawer-ai clawer-bio clawer-edu \
            clawer-survey clawer-writing clawer-data clawer-experiment clawer-review; do
  name="research-${type#clawer-}"
  curl -s -X POST http://localhost:10101/api/instances/start \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"$name\", \"instance_type\": \"$type\"}" | python3 -m json.tool
done

# 3. 启动功能性 Agent 子实例
for type in guardian vanguard maintainer promoter wellspring; do
  curl -s -X POST http://localhost:10101/api/instances/start \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"research-$type\", \"instance_type\": \"$type\"}" | python3 -m json.tool
done

# 4. 查看所有实例状态
curl http://localhost:10101/api/instances/ | python3 -m json.tool

# 5. 停止某个实例
curl -X POST http://localhost:10101/api/instances/stop/research-ai

# 6. 重启该实例
curl -X POST http://localhost:10101/api/instances/start \
  -H "Content-Type: application/json" \
  -d '{"name": "research-ai", "instance_type": "clawer-ai"}'
```

---

## 9. 认证机制

### 9.1 当前版本（v2.0）

当前版本**不启用认证机制**，所有接口均可直接调用。适用于：
- 本地开发和测试
- 内网部署（通过网络隔离保证安全）
- Mac Studio 单机科研环境

### 9.2 生产环境安全建议

```nginx
# 方案一：Nginx Basic Auth
location /api/ {
    auth_basic "openclaw_research API";
    auth_basic_user_file /etc/nginx/.htpasswd;
    proxy_pass http://127.0.0.1:10101;
}

# 方案二：IP 白名单
location /api/ {
    allow 192.168.1.0/24;
    deny all;
    proxy_pass http://127.0.0.1:10101;
}
```

---

## 10. 错误码说明

### 10.1 HTTP 状态码

| 状态码 | 说明 | 常见原因 |
|--------|------|----------|
| `200` | 成功 | 请求正常处理 |
| `400` | 请求错误 | 实例启动失败（端口占用、类型不存在） |
| `404` | 资源不存在 | Agent ID 不存在，实例类型不存在 |
| `422` | 请求参数验证失败 | 缺少必填字段，字段类型错误 |
| `503` | 服务不可用 | 编排器未就绪（启动中），OpenAI API 不可用 |
| `500` | 服务器内部错误 | 未预期的异常 |

### 10.2 业务状态码

| status 值 | 说明 | 处理建议 |
|-----------|------|----------|
| `success` | 任务成功执行 | 读取 `result` 字段 |
| `error` | 执行出错 | 读取 `error` 字段，检查 API Key 配置 |
| `rejected` | Guardian 拒绝 | 读取 `issues` 和 `recommendation`，修改任务后重试 |
| `escalated` | 需要人工审核 | 任务已被标记，等待人工处理 |

### 10.3 Guardian verdict 说明

| verdict 值 | 风险评分范围 | 处理方式 |
|-----------|--------------|----------|
| `approved` | 0.0 - 0.7 | 正常通过，任务继续执行 |
| `needs_revision` | 0.5 - 0.8 | 建议修改，但允许继续 |
| `escalated` | 0.7 - 0.9 | 转人工审核，任务暂停 |
| `rejected` | 0.9 - 1.0 | 直接拒绝，任务终止 |

### 10.4 实例 API 特有错误

| 错误信息 | 原因 | 处理方式 |
|----------|------|----------|
| `未知实例类型: xxx` | `instance_types.yaml` 中没有该类型定义 | 检查类型名称拼写，或先在 yaml 中添加该类型 |
| `端口 xxxxx 已被占用` | 目标端口已有进程监听 | 先停止占用该端口的进程，或指定其他端口 |
| `实例 xxx 已在运行` | 同名实例进程仍在运行 | 先调用 stop，再重新 start |
| `实例 xxx 未注册` | 注册表中无此记录 | 先调用 start 注册并启动 |
| `进程启动后立即退出` | 子进程崩溃（依赖缺失/配置错误） | 检查 `data/logs/<name>.log` |
