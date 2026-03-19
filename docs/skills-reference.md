# openclaw_research 技能库完整参考手册

> 版本：v2.0 | 最后更新：2026-03-12 | 技能总数：51

---

## 目录

1. [技能概述与架构](#1-技能概述与架构)
2. [技能注册机制](#2-技能注册机制)
3. [技能 → 实例类型映射表（v2.0 新增）](#3-技能--实例类型映射表v20-新增)
4. [技能分类参考](#4-技能分类参考)
   - [搜索类（6个）](#41-搜索类)
   - [文献与知识处理类（10个）](#42-文献与知识处理类)
   - [代码与计算类（4个）](#43-代码与计算类)
   - [数据分析类（3个）](#44-数据分析类)
   - [学术研究类（15个）](#45-学术研究类)
   - [分析与评估类（4个）](#46-分析与评估类)
   - [写作辅助类（4个）](#47-写作辅助类)
   - [系统与运营类（5个）](#48-系统与运营类)
5. [LLM Action Pattern 执行流程](#5-llm-action-pattern-执行流程)
6. [如何添加新技能](#6-如何添加新技能)
7. [技能调用链示例](#7-技能调用链示例)

---

## 1. 技能概述与架构

openclaw_research 技能库（`skills/tools.py`）是系统的核心能力层，为所有 Agent 提供可调用的原子操作。技能库共收录 **51 个技能**，涵盖搜索、文献处理、代码执行、数据分析、学术写作、通知等核心科研场景。

v2.0 新增 `send_email` 技能，由 `maintainer` 和 `promoter` 实例类型使用。

### 架构设计原则

- **注册式设计**：所有技能通过 `@register_skill` 装饰器统一注册到全局 `SKILL_REGISTRY` 字典，支持运行时动态发现。
- **双执行模式**：技能分为**直接执行型**（立即返回结果，如 `web_search`、`code_execute`）和 **LLM Action 型**（返回结构化提示词，由上层 LLM 处理，如 `text_summarize`、`code_review`）。
- **OpenAI Function Calling 兼容**：技能参数定义符合 OpenAI Chat Completions API 的 `tools` 格式，可直接用于 `client.chat.completions.create()` 的 `tools` 参数。
- **按需授权**：每个 Agent 只持有其 `allowed_tools` 列表中的技能；v2.0 起，子实例还受 `ALLOWED_SKILLS` 环境变量约束（由 `config/instance_types.yaml` 决定）。

### 核心组件

| 组件 | 位置 | 作用 |
|------|------|------|
| `SKILL_REGISTRY` | `skills/tools.py` | 全局技能注册表（字典） |
| `register_skill()` | `skills/tools.py` | 技能注册装饰器 |
| `get_openai_tools()` | `skills/tools.py` | 将注册表转换为 OpenAI tools 格式 |
| `execute_skill()` | `skills/tools.py` | 异步技能执行入口 |

---

## 2. 技能注册机制

### 注册装饰器

```python
# skills/tools.py
def register_skill(name: str, description: str, params: dict):
    def decorator(fn):
        SKILL_REGISTRY[name] = {
            "name": name,
            "description": description,
            "parameters": params,
            "fn": fn,
        }
        return fn
    return decorator
```

### 使用示例

```python
@register_skill(
    "my_skill",                        # 技能唯一标识符
    "技能功能描述，供 LLM 理解用途。",   # 自然语言描述
    {
        "param1": {"type": "string", "description": "参数说明"},
        "param2": {"type": "integer", "description": "整型参数说明"},
    },
)
async def my_skill(param1: str, param2: int = 10) -> dict:
    """技能实现"""
    return {"result": "..."}
```

### 执行入口

```python
# 异步执行技能
result = await execute_skill("web_search", {"query": "transformer architecture"})

# 获取 OpenAI 格式工具列表
tools = get_openai_tools()
# 返回格式：
# [{"type": "function", "function": {"name": "web_search", "description": "...", "parameters": {...}}}, ...]
```

---

## 3. 技能 → 实例类型映射表（v2.0 新增）

v2.0 引入了多进程子实例架构，每种实例类型只加载其在 `config/instance_types.yaml` 中声明的技能子集。下表展示每个技能被哪些实例类型使用。

**实例类型缩写对照**：

| 缩写 | 实例类型 | 端口 |
|------|----------|------|
| orch | orchestrator | 10101 |
| math | clawer-math | 10110 |
| cs | clawer-cs | 10111 |
| ai | clawer-ai | 10112 |
| bio | clawer-bio | 10113 |
| edu | clawer-edu | 10114 |
| surv | clawer-survey | 10115 |
| writ | clawer-writing | 10116 |
| data | clawer-data | 10117 |
| exp | clawer-experiment | 10118 |
| rev | clawer-review | 10119 |
| grd | guardian | 10130 |
| van | vanguard | 10131 |
| mnt | maintainer | 10132 |
| prom | promoter | 10133 |
| well | wellspring | 10134 |

> `orchestrator` 加载全部 51 个技能（`skills: "*"`），不单独列出。

### 完整映射表

| 技能名称 | math | cs | ai | bio | edu | surv | writ | data | exp | rev | grd | van | mnt | prom | well |
|----------|------|----|----|-----|-----|------|------|------|-----|-----|-----|-----|-----|------|------|
| **搜索类** | | | | | | | | | | | | | | | |
| web_search | | ✓ | | | | ✓ | | | | | | ✓ | | | |
| arxiv_search | ✓ | ✓ | ✓ | ✓ | | ✓ | | | | | | ✓ | | | |
| semantic_scholar_search | | | | ✓ | | ✓ | | | | | | ✓ | | | |
| url_fetch | | | | ✓ | | ✓ | | | | | | | | | |
| github_search | | ✓ | | | | | | | | | | ✓ | | | |
| patent_search | | | | | | | | | | | | ✓ | | | |
| **文献与知识处理类** | | | | | | | | | | | | | | | |
| pdf_extract | | | | ✓ | | ✓ | | | | | | | | | |
| text_summarize | ✓ | ✓ | | ✓ | ✓ | ✓ | ✓ | | | | | | | ✓ | ✓ |
| citation_format | | | | | | | ✓ | | | | | | | | |
| knowledge_extract | | | | | ✓ | | | | | | | | | | ✓ |
| translation | | | | | ✓ | | ✓ | | | | | | | ✓ | |
| acronym_expand | | | | | | | ✓ | | | | | | | | |
| reading_notes | | | | | ✓ | | ✓ | | | | | | | | ✓ |
| paper_compare | | | ✓ | | | ✓ | | | | ✓ | | | | | ✓ |
| concept_explain | ✓ | | | ✓ | ✓ | | ✓ | | | | | | | | ✓ |
| timeline_generate | | | | | | | | | ✓ | | | | ✓ | | |
| **代码与计算类** | | | | | | | | | | | | | | | |
| code_execute | | ✓ | | | | | | ✓ | | | | | | | |
| code_review | | ✓ | | | | | | | | | | | | | |
| code_document | | ✓ | | | | | | | | | | | | | |
| code_explain | | ✓ | | | | | | | | | | | | | |
| **数据分析类** | | | | | | | | | | | | | | | |
| math_solve | ✓ | | | | | | | ✓ | | | | | | | |
| data_analysis | ✓ | | ✓ | ✓ | | | | ✓ | | | | | | | |
| dataset_discover | | | ✓ | | | | | ✓ | | | | ✓ | | | |
| **学术研究类** | | | | | | | | | | | | | | | |
| research_outline | ✓ | | | ✓ | ✓ | ✓ | ✓ | | ✓ | | | | | | ✓ |
| abstract_generate | | | | | | | ✓ | | | | | | | ✓ | |
| hypothesis_generate | ✓ | | ✓ | | | | | | ✓ | | | | | | |
| experiment_design | | | ✓ | | | | | | ✓ | | | | | | |
| peer_review | | ✓ | | | | | | | | ✓ | ✓ | | | | |
| gap_analysis | | ✓ | | | | | | | ✓ | ✓ | | ✓ | | | |
| methodology_eval | | | ✓ | | | | | ✓ | ✓ | ✓ | ✓ | | | | |
| literature_gap | | | | ✓ | | ✓ | | | | ✓ | | ✓ | | | ✓ |
| survey_question | | | | | ✓ | | | | | | | | | | |
| statistical_test | ✓ | | ✓ | | | | | ✓ | ✓ | | | | | | |
| reproducibility_check | | | | | | | | ✓ | ✓ | ✓ | ✓ | | | | |
| grant_proposal | | | | | | | | | | | | | | | |
| benchmark_design | ✓ | ✓ | ✓ | | | | | ✓ | ✓ | | | | | | |
| response_letter | | | | | | | ✓ | | | ✓ | | | | | |
| related_work | | | | ✓ | | ✓ | | | | | | | | | ✓ |
| **分析与评估类** | | | | | | | | | | | | | | | |
| trend_analysis | | | ✓ | | | ✓ | | | | | | ✓ | | | |
| quality_score | | | | | | | | ✓ | | ✓ | ✓ | | | | |
| fact_check | ✓ | | ✓ | | | | | | | ✓ | ✓ | | | | |
| debate_moderator | | | | | | | | | | ✓ | ✓ | | | | |
| **写作辅助类** | | | | | | | | | | | | | | | |
| report_generate | | | | | | | ✓ | | | | | | ✓ | ✓ | |
| writing_polish | | | | | | | ✓ | | | | | | | ✓ | |
| figure_description | | | | | | | | ✓ | | | | | | | |
| **系统与运营类** | | | | | | | | | | | | | | | |
| task_decompose | | | | | | | | | ✓ | | | | ✓ | | |
| mind_map | | | | | ✓ | | | | | | | | | | ✓ |
| content_plan | | | | | ✓ | | | | | | | | | ✓ | |
| career_advice | | | | | ✓ | | | | | | | | | | |
| knowledge_graph | | | | ✓ | | ✓ | | | | | | ✓ | | | ✓ |
| send_email | | | | | | | | | | | | | ✓ | ✓ | |

> **注意**：`grant_proposal` 在当前版本的实例类型配置中未分配给任何子实例，仅在编排层（orchestrator，拥有全部技能）可用。

---

## 4. 技能分类参考

### 4.1 搜索类

共 **6 个**技能，负责从互联网、学术数据库、代码仓库等来源检索信息。

---

#### `web_search` — 网络搜索

**功能描述**：通过 DuckDuckGo 公共 API 搜索互联网，获取最新信息，支持学术、新闻、技术内容检索。

**参数**：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `query` | string | 是 | — | 搜索关键词 |
| `max_results` | integer | 是 | 5 | 返回结果数量 |

**返回值**：`{"query": str, "results": [{"title", "snippet", "url", "source"}], "count": int}`

**使用场景**：获取最新资讯、事实核查、初步了解某话题。

**分配给**：clawer-cs、clawer-survey、vanguard

```python
import asyncio
from skills.tools import execute_skill

result = asyncio.run(execute_skill("web_search", {
    "query": "large language model 2026 benchmark",
    "max_results": 5
}))
print(result["results"])
```

---

#### `arxiv_search` — arXiv 论文搜索

**功能描述**：使用官方 `arxiv` Python 库搜索预印本论文，支持关键词和领域（category）过滤。

**参数**：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `query` | string | 是 | — | 论文搜索关键词 |
| `max_results` | integer | 是 | 5 | 返回论文数量 |
| `category` | string | 是 | `""` | arXiv 分类（如 `cs.AI`、`math.CO`） |

**返回值**：`{"query": str, "papers": [{"id", "title", "authors", "abstract", "url", "pdf_url", "published", "categories"}], "count": int}`

**分配给**：clawer-math、clawer-cs、clawer-ai、clawer-bio、clawer-survey、vanguard

---

#### `semantic_scholar_search` — Semantic Scholar 搜索

**功能描述**：调用 Semantic Scholar Graph API，获取含引用次数的学术论文，适合评估论文影响力。

**参数**：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `query` | string | 是 | — | 搜索关键词 |
| `max_results` | integer | 是 | 5 | 返回数量 |

**返回值**：`{"query": str, "papers": [{"title", "authors", "abstract", "year", "citations", "url"}], "count": int}`

**分配给**：clawer-bio、clawer-survey、vanguard

---

#### `url_fetch` — 网页内容抓取

**功能描述**：抓取指定 URL 的网页内容，使用 BeautifulSoup 提取正文文本，过滤脚本、样式、导航等噪声。

**参数**：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `url` | string | 是 | — | 目标网页 URL |
| `max_chars` | integer | 是 | 3000 | 返回最大字符数 |

**返回值**：`{"url": str, "content": str, "length": int}`

**分配给**：clawer-bio、clawer-survey

---

#### `github_search` — GitHub 仓库搜索

**功能描述**：通过 GitHub REST API 搜索开源仓库，按 Star 数排序，支持编程语言过滤。

**参数**：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `query` | string | 是 | — | 搜索关键词 |
| `language` | string | 是 | `""` | 编程语言过滤（如 `python`） |
| `max_results` | integer | 是 | 5 | 返回数量 |

**返回值**：`{"query": str, "repos": [{"name", "description", "stars", "language", "url", "updated"}], "count": int}`

**分配给**：clawer-cs、vanguard

---

#### `patent_search` — 专利搜索

**功能描述**：通过 Google Patents 搜索相关专利，结合 LLM 分析技术新颖性和专利布局。

**参数**：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `technology` | string | 是 | — | 技术描述或关键词 |
| `applicant` | string | 是 | `""` | 申请人/机构（竞品分析） |

**返回值**：LLM Action — 包含专利概览、新颖性评估、风险点、保护策略。

**分配给**：vanguard（独占）

---

### 4.2 文献与知识处理类

共 **10 个**技能，负责从文档中提取、转换、整理知识。

---

#### `pdf_extract` — PDF 文本提取

**功能描述**：使用 PyPDF2 从本地路径或远程 URL 的 PDF 文件中提取文本，支持分页限制。

**参数**：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `source` | string | 是 | — | 本地路径或 PDF URL |
| `max_pages` | integer | 是 | 10 | 最大处理页数 |

**返回值**：`{"source": str, "pages": int, "total_pages": int, "content": str}`

**分配给**：clawer-bio、clawer-survey

```python
result = asyncio.run(execute_skill("pdf_extract", {
    "source": "https://arxiv.org/pdf/2303.08774",
    "max_pages": 5
}))
print(result["content"][:500])
```

---

#### `text_summarize` — 文本摘要

**功能描述**：对长文本生成智能摘要，支持学术（academic）、新闻（news）、技术（technical）、要点列表（bullet）四种风格。返回 LLM Action。

**参数**：`text` (string), `style` (string, 默认 `academic`), `max_words` (integer, 默认 200)

**分配给**：clawer-math、clawer-cs、clawer-bio、clawer-edu、clawer-survey、clawer-writing、promoter、wellspring（最广泛分配的技能，8 个实例类型）

---

#### `citation_format` — 引用格式化

**功能描述**：将 JSON 格式的论文信息转换为标准学术引用，支持 APA、MLA、Chicago 三种格式。

**参数**：`paper_info` (string, JSON), `style` (string, 默认 `APA`)

**分配给**：clawer-writing（独占）

---

#### `knowledge_extract` — 知识提取

**功能描述**：从文本中提取结构化知识，输出包括实体（含类型）、关键论点、方法、数据集、评估指标、局限性的 JSON 结构。返回 LLM Action。

**参数**：`text` (string)

**分配给**：clawer-edu、wellspring

---

#### `translation` — 学术翻译

**功能描述**：将学术内容在中英文之间互译，保留专业术语准确性。返回 LLM Action。

**参数**：`text` (string), `target_lang` (string, `zh` 或 `en`)

**分配给**：clawer-edu、clawer-writing、promoter

---

#### `acronym_expand` — 缩写展开

**功能描述**：识别并展开学术文本中的缩写，处理有歧义的缩写时列出所有可能含义。返回 LLM Action。

**参数**：`text` (string), `domain` (string, 帮助消歧义)

**分配给**：clawer-writing（独占）

---

#### `reading_notes` — 阅读笔记生成

**功能描述**：将论文内容整理为结构化阅读笔记，支持康奈尔（cornell）、结构化（structured）、批判性（critical）三种笔记风格。返回 LLM Action。

**参数**：`paper_content` (string), `note_style` (string, 默认 `structured`)

**分配给**：clawer-edu、clawer-writing、wellspring

---

#### `paper_compare` — 论文横向对比

**功能描述**：对多篇论文进行横向对比分析，生成 Markdown 对比表格。维度可自定义（方法/数据集/指标/结论/局限性等）。返回 LLM Action。

**参数**：`papers` (string, JSON 数组), `dimensions` (string, 逗号分隔)

**分配给**：clawer-ai、clawer-survey、clawer-review、wellspring

---

#### `concept_explain` — 概念解释

**功能描述**：针对三类受众（专家/学生/大众）分层次解释学术概念，包含直观定义、核心原理、具体例子和应用场景。返回 LLM Action。

**参数**：`concept` (string), `level` (string: `expert/student/general`), `domain` (string)

**分配给**：clawer-math、clawer-bio、clawer-edu、clawer-writing、wellspring

---

#### `timeline_generate` — 发展时间线

**功能描述**：结合 arXiv 搜索，生成某研究领域或技术从指定年份至今的发展时间线，包含关键转折点分析和未来预判。

**参数**：`topic` (string), `start_year` (integer, 默认 2015)

**分配给**：clawer-experiment、maintainer

---

### 4.3 代码与计算类

共 **4 个**技能，负责代码执行、审查、文档化和解释。

---

#### `code_execute` — Python 代码执行

**功能描述**：在临时文件沙箱中执行 Python 代码，通过 `asyncio.create_subprocess_exec` 隔离运行，支持超时控制，返回 stdout/stderr 和退出码。

**参数**：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `code` | string | 是 | — | 待执行的 Python 代码 |
| `timeout` | integer | 是 | 30 | 执行超时秒数 |

**返回值**：`{"success": bool, "output": str, "error": str, "return_code": int}`

**分配给**：clawer-cs、clawer-data

```python
result = asyncio.run(execute_skill("code_execute", {
    "code": "import numpy as np\nprint(np.mean([1,2,3,4,5]))",
    "timeout": 10
}))
print(result["output"])  # 3.0
```

---

#### `code_review` — 代码评审

**功能描述**：对代码进行专业质量评审，输出总体评价、问题列表（含严重级别）、优化建议。支持 bugs/security/performance/style 四种评审重点。返回 LLM Action。

**参数**：`code` (string), `language` (string, 默认 `python`), `focus` (string, 默认 `bugs`)

**分配给**：clawer-cs（独占）

---

#### `code_document` — 代码文档生成

**功能描述**：为代码自动生成文档注释（docstring）或 README，包含 Args/Returns/Raises/Example 规范格式。支持 docstring/readme/wiki 三种文档类型。返回 LLM Action。

**参数**：`code` (string), `doc_type` (string, 默认 `docstring`), `language` (string, 默认 `python`)

**分配给**：clawer-cs（独占）

---

#### `code_explain` — 代码解释

**功能描述**：针对不同受众（beginner/intermediate/expert）解释代码的功能逻辑，包含逐行注释和整体架构说明。返回 LLM Action。

**参数**：`code` (string), `language` (string, 默认 `python`), `audience` (string, 默认 `intermediate`)

**分配给**：clawer-cs（独占）

---

### 4.4 数据分析类

共 **3 个**技能，负责数学计算、数据统计分析和数据集发现。

---

#### `math_solve` — 数学问题求解

**功能描述**：求解数学问题（方程、证明、数值计算、统计），可选择是否展示详细解题步骤。返回 LLM Action。

**参数**：`problem` (string), `show_steps` (boolean, 默认 `true`)

**分配给**：clawer-math、clawer-data

---

#### `data_analysis` — 数据统计分析

**功能描述**：使用 pandas 解析 CSV/JSON 格式数据，生成描述性统计（均值、方差、缺失值等），结合 LLM 进行深度分析。支持 descriptive/correlation/regression/clustering 四种分析类型。

**参数**：`data` (string, CSV 或 JSON), `analysis_type` (string), `question` (string)

**返回值**：`{"stats": {...}, "analysis_type": str, "action": "llm_analyze", "prompt": str}`

**分配给**：clawer-math、clawer-ai、clawer-bio、clawer-data

```python
csv_data = "model,accuracy,latency\nGPT-5,0.95,1200\nClaude,0.93,980\nGemini,0.91,1100"
result = asyncio.run(execute_skill("data_analysis", {
    "data": csv_data,
    "analysis_type": "descriptive",
    "question": "哪个模型在精度和速度上综合表现最好？"
}))
```

---

#### `dataset_discover` — 数据集发现

**功能描述**：结合 web_search 搜索 Papers With Code 和 HuggingFace 数据集平台，推荐 5 个最相关的公开数据集，包含名称、规模、特点、获取链接和适用场景。

**参数**：`task_description` (string), `domain` (string)

**分配给**：clawer-ai、clawer-data、vanguard

---

### 4.5 学术研究类

共 **15 个**技能，覆盖从选题到投稿的完整学术研究流程。

---

#### `research_outline` — 研究提纲生成

**功能描述**：为指定研究主题生成详细学术论文提纲，包含标准章节结构、每章核心问题、3-5 个关键研究问题和方法论建议。支持 research/review/thesis/proposal 四种论文类型。

**参数**：`topic` (string), `paper_type` (string), `domain` (string)

**分配给**：clawer-math、clawer-bio、clawer-edu、clawer-survey、clawer-writing、clawer-experiment、wellspring

---

#### `abstract_generate` — 学术摘要生成

**功能描述**：根据论文正文或大纲生成标准学术摘要（背景+目的+方法+结果+结论），支持英文（en）和中文（zh）输出。

**参数**：`content` (string), `word_limit` (integer, 默认 250), `language` (string, 默认 `en`)

**分配给**：clawer-writing、promoter

---

#### `hypothesis_generate` — 科学假设生成

**功能描述**：基于研究背景提出可验证的科学假设，每个假设包含 H0/H1 形式陈述、理论依据、验证方法建议和预期结果。

**参数**：`background` (string), `count` (integer, 默认 3)

**分配给**：clawer-math、clawer-ai、clawer-experiment

---

#### `experiment_design` — 实验方案设计

**功能描述**：为研究问题设计完整实验方案，包含实验目标、变量定义（自/因/控）、实验组设计、样本量、数据收集方法、评估指标、统计分析方法和偏差控制措施。

**参数**：`research_question` (string), `domain` (string), `constraints` (string)

**分配给**：clawer-ai、clawer-experiment

---

#### `peer_review` — 同行评审

**功能描述**：模拟专业同行评审，从创新性、方法论、结果可信度、写作质量四个维度（各 1-5 分）评估论文，给出 Accept/Minor Revision/Major Revision/Reject 建议。

**参数**：`paper_content` (string), `review_type` (string: `full/abstract/methodology`)

**分配给**：clawer-cs、clawer-review、guardian

---

#### `gap_analysis` — 研究空白分析

**功能描述**：基于文献综述，系统梳理已解决问题、研究空白、现有矛盾、方法论局限，推荐未来 5 年值得探索的方向（3-5 个）。

**参数**：`literature_summary` (string), `domain` (string)

**分配给**：clawer-cs、clawer-experiment、clawer-review、vanguard

---

#### `methodology_eval` — 方法论评估

**功能描述**：从内部效度、外部效度、测量信度、样本代表性、混淆变量控制、统计功效六个维度评估研究方法论严谨性，给出总体评分（1-10）和改进建议。支持 quantitative/qualitative/mixed/experimental 研究类型。

**参数**：`methodology_desc` (string), `study_type` (string)

**分配给**：clawer-ai、clawer-data、clawer-experiment、clawer-review、guardian

---

#### `literature_gap` — 文献空白识别

**功能描述**：结合 arXiv 实时搜索，系统识别研究主题的文献空白，输出已充分研究方向、争议方向、明显空白方向（Top 5）、跨学科机会，以及每个方向的 RQ（研究问题）格式建议。

**参数**：`topic` (string), `existing_work` (string)

**分配给**：clawer-bio、clawer-survey、clawer-review、vanguard、wellspring

---

#### `survey_question` — 问卷/访谈设计

**功能描述**：为研究目标设计问卷或访谈提纲，包含背景信息题、Likert 5 点量表（含反向题）、开放性问题（3-5 个）、伦理声明和预计完成时间。

**参数**：`research_goal` (string), `survey_type` (string: `questionnaire/interview/focus_group`), `target_population` (string)

**分配给**：clawer-edu（独占）

---

#### `statistical_test` — 统计检验推荐

**功能描述**：根据研究设计和研究问题推荐最适合的统计检验方法（主选+备选），说明选择依据、假设检验步骤、前提条件检查、效应量计算，并提供 Python/R 代码片段。

**参数**：`study_design` (string), `research_question` (string)

**分配给**：clawer-math、clawer-ai、clawer-data、clawer-experiment

---

#### `reproducibility_check` — 可重复性检查

**功能描述**：生成研究可重复性评估清单（数据集公开性、代码开源性、超参数报告完整性、随机种子、实验环境记录等），输出可重复性评分（0-10）和改进建议。

**参数**：`paper_description` (string), `domain` (string)

**分配给**：clawer-data、clawer-experiment、clawer-review、guardian

---

#### `grant_proposal` — 基金申请书框架

**功能描述**：按照国家自然科学基金模板生成申请书框架，包含项目名称、立项依据、研究目标、研究内容、技术路线、创新点（3个）、预期成果和研究基础建议。支持青年/面上/重点项目类型。

**参数**：`research_topic` (string), `grant_type` (string: `nsfc_youth/nsfc_general/nsfc_key/other`), `background` (string)

**分配给**：当前版本未分配给任何子实例类型（仅编排层可用）

---

#### `benchmark_design` — 评估基准设计

**功能描述**：结合 arXiv 检索现有 Benchmark，设计新评估基准方案，包含基准定位、评估维度（5-8个指标）、数据集构建方案、基线模型列表、评分方法和排行榜设计。

**参数**：`task_description` (string), `domain` (string)

**分配给**：clawer-math、clawer-cs、clawer-ai、clawer-data、clawer-experiment

---

#### `related_work` — 相关工作章节生成

**功能描述**：结合 arXiv 实时搜索，为论文生成相关工作（Related Work）章节，按研究线索组织，覆盖与本文贡献的对比和差异化阐述。

**参数**：`topic` (string), `key_references` (string), `paper_contribution` (string)

**分配给**：clawer-bio、clawer-survey、wellspring

---

#### `response_letter` — 审稿回复信

**功能描述**：根据审稿意见和作者回应草稿，生成规范的审稿回复信（Response to Reviewers），逐条回应，格式专业，区分已修改内容和未采纳意见的说明。

**参数**：`reviews` (string), `authors_response` (string)

**分配给**：clawer-writing、clawer-review

---

### 4.6 分析与评估类

共 **4 个**技能，提供趋势分析、质量评分、事实核查和多视角辩论。

---

#### `trend_analysis` — 领域趋势分析

**功能描述**：结合 arXiv 最新论文（10篇），分析研究领域近期趋势，输出热点方向（Top 5）、新兴技术/方法、衰退方向、未来 6 个月预测和重点关注论文推荐。

**参数**：`domain` (string), `time_range` (string: `3m/6m/1y`, 默认 `6m`)

**分配给**：clawer-ai、clawer-survey、vanguard

---

#### `quality_score` — 研究质量评分

**功能描述**：从准确性、新颖性、完整性、清晰度、引用质量五个维度对研究内容进行量化评分（0-1），并给出优点、不足和改进建议。Wellspring 使用此技能过滤低质量结果。

**参数**：`content` (string), `criteria` (string: `academic/technical/general`)

**返回值**：JSON 格式评分，包含 `overall`（总分，用于 Wellspring 质量门控）。

**分配给**：clawer-data、clawer-review、guardian

---

#### `fact_check` — 事实核查

**功能描述**：对学术声明进行核查，结合 web_search 获取参考信息，输出核查结论（true/false/partially_true/unverifiable）、置信度、证据列表和来源。

**参数**：`claim` (string)

**分配给**：clawer-math、clawer-ai、clawer-review、guardian

---

#### `debate_moderator` — 多视角辩论

**功能描述**：对学术争议问题组织多视角辩论分析，每个立场包含核心论点、支持证据和反驳，最后给出调解人总结、主流观点和未解决问题。

**参数**：`controversial_question` (string), `perspectives` (integer, 默认 3)

**分配给**：clawer-review、guardian

---

### 4.7 写作辅助类

共 **4 个**技能，提供报告生成、英文润色、图表描述和摘要生成。

---

#### `report_generate` — 结构化报告生成

**功能描述**：根据主题和内容要点生成专业 Markdown 报告，包含执行摘要、结构化章节、数据支撑结论、建议和下一步计划（约 1000-1500 字）。支持 research/progress/summary/proposal 四种类型。

**参数**：`topic` (string), `content_points` (string), `report_type` (string)

**分配给**：clawer-writing、maintainer、promoter

---

#### `writing_polish` — 学术英文润色

**功能描述**：对学术英文写作进行专业润色，纠正语法错误、提升表达精确性和学术性、改善句子流畅度、统一术语，输出润色后全文和主要修改说明对照表。支持指定目标期刊/会议风格。

**参数**：`text` (string), `target_venue` (string, 如 `NeurIPS 2026`)

**分配给**：clawer-writing、promoter

---

#### `figure_description` — 图表描述生成

**功能描述**：为论文图表生成专业图题（Caption）和描述性文字，支持 chart/diagram/flowchart/table/heatmap 五种图表类型，结合论文上下文确保描述准确。

**参数**：`figure_description` (string), `figure_type` (string), `paper_context` (string)

**分配给**：clawer-data（独占）

---

### 4.8 系统与运营类

共 **5 个**技能，支持任务管理、知识可视化、社区运营和通知。

---

#### `task_decompose` — 任务分解

**功能描述**：将复杂研究任务分解为可执行子任务树，输出 JSON 格式结构，包含每个子任务的标题、描述、建议分配角色（clawer/vanguard/guardian）、依赖关系、估计复杂度、执行顺序和并行分组。

**参数**：`task` (string), `max_subtasks` (integer, 默认 6)

**分配给**：clawer-experiment、maintainer

---

#### `mind_map` — 思维导图生成

**功能描述**：为指定主题生成 Markdown 树状格式的思维导图，支持 2-4 层深度，每层 3-5 个节点，覆盖主题核心维度。

**参数**：`topic` (string), `depth` (integer, 默认 3)

**分配给**：clawer-edu、wellspring

---

#### `content_plan` — 传播推广计划

**功能描述**：为学术内容制定多平台传播推广计划，包含各平台内容策略（标题/正文/标签）、发布时间建议、互动话题设计和 KPI 指标。支持微博、微信、知乎、Twitter 等平台。

**参数**：`content_summary` (string), `target_audience` (string), `platforms` (string)

**分配给**：clawer-edu、promoter

---

#### `career_advice` — 职业发展建议

**功能描述**：根据个人背景和职业目标（博士申请/博士后/工业界/高校教职/创业）提供个性化职业规划，包含竞争力评估、差距分析、12 个月行动计划、资源推荐和风险备选方案。

**参数**：`profile` (string), `goal` (string: `phd/postdoc/industry/faculty/startup`)

**分配给**：clawer-edu（独占）

---

#### `knowledge_graph` — 知识图谱构建

**功能描述**：从文本中提取实体和关系，构建知识图谱结构，支持指定焦点实体，输出节点列表、边（关系）列表和图谱摘要。返回 LLM Action。

**参数**：`text` (string), `focus_entities` (string)

**分配给**：clawer-bio、clawer-survey、vanguard、wellspring

---

#### `send_email` — 邮件发送（v2.0 新增）

**功能描述**：通过 SMTP 发送邮件，支持 HTML 和纯文本格式，可发送系统告警、研究进展通知、任务完成报告等。邮件配置来自 `.env` 中的 SMTP 设置。

**参数**：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `to` | string | 是 | — | 收件人邮箱地址 |
| `subject` | string | 是 | — | 邮件主题 |
| `body` | string | 是 | — | 邮件正文（支持 HTML） |
| `is_html` | boolean | 是 | false | 是否为 HTML 格式 |

**返回值**：`{"success": bool, "message": str}`

**分配给**：maintainer、promoter

---

## 5. LLM Action Pattern 执行流程

许多技能（如 `text_summarize`、`code_review`、`research_outline`）不直接返回最终结果，而是返回一个包含 `"action"` 字段的字典，称为 **LLM Action**。

### 流程说明

```
用户请求
    ↓
BaseAgent.run(task)
    ↓
LLM 选择技能 → 调用 execute_skill()
    ↓
技能返回 {"action": "llm_xxx", "prompt": "..."}
    ↓
BaseAgent 检测到 action 字段，提取 prompt
    ↓
将 {"result": "[需要 LLM 处理]\n{prompt}"} 作为 tool_result 返回
    ↓
LLM 在下一轮迭代中处理 prompt，生成最终文本
    ↓
finish_reason == "stop"，输出最终结果
```

### 代码实现（`agents/base.py`）

```python
# tool_calls 循环中的 LLM Action 处理
if isinstance(result, dict) and result.get("action", "").startswith("llm_"):
    prompt = result.get("prompt", str(result))
    result = {"result": f"[需要 LLM 处理]\n{prompt}"}
```

### 两类技能对比

| 类型 | 返回格式 | 代表技能 | 执行路径 |
|------|---------|---------|---------|
| 直接执行型 | `{"data": ...}` | `web_search`、`code_execute`、`arxiv_search` | 立即返回，无需 LLM 二次处理 |
| LLM Action 型 | `{"action": "llm_xxx", "prompt": "..."}` | `text_summarize`、`code_review`、`math_solve` | BaseAgent 拦截，传递给 LLM 处理 |

---

## 6. 如何添加新技能

### 步骤一：在 `skills/tools.py` 末尾添加技能函数

```python
@register_skill(
    "my_new_skill",                          # 全局唯一技能名（小写+下划线）
    "简洁描述技能功能，不超过50字。",            # 功能描述（LLM 据此判断何时使用）
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
        result = some_computation(input_text)
        return {"result": result, "mode": mode}
    else:
        return {
            "action": "llm_my_skill",
            "prompt": f"请处理以下内容：\n\n{input_text[:2000]}",
        }
```

### 步骤二：验证注册成功

```python
python3 -c "from skills.tools import SKILL_REGISTRY; print('my_new_skill' in SKILL_REGISTRY)"
# 输出: True
```

### 步骤三：将技能分配给实例类型（v2.0）

编辑 `config/instance_types.yaml`，将新技能加入对应实例类型的 `skills` 列表：

```yaml
clawer-cs:
  port: 10111
  skills:
    - code_execute
    - code_review
    - my_new_skill    # 新增
```

保存后，下次启动 `clawer-cs` 类型的子实例时，`ALLOWED_SKILLS` 中会自动包含 `my_new_skill`。

### 步骤四：测试新技能

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
- 所有参数必须在 `params` 字典中定义，缺少声明的参数不会传递给函数
- `params` 中所有 key 都将被设为 `required`，如需可选参数，在函数签名中设置默认值即可
- 技能函数支持 `async def` 和 `def` 两种形式，`execute_skill()` 自动判断并适配
- 工具返回值截断为 8000 字符，避免超出 LLM 上下文窗口

---

## 7. 技能调用链示例

### 示例一：文献综述工作流（`wf-literature-review`）

```
用户："请做一个关于 Diffusion Model 的系统性文献综述"
    ↓
TaskRouter 匹配 "literature_review" 工作流
    ↓
Orchestrator._wf_literature_review() 依次调用：
    │
    ├── VanguardAgent（或 instances/client.py → clawer-vanguard:10131）
    │       └── arxiv_search("diffusion model", max_results=10)
    │               → 返回最新论文列表
    │
    ├── clawer-survey-01（或 → research-survey:10115）
    │       └── [LLM 决定调用]
    │           ├── semantic_scholar_search("diffusion model generation")
    │           ├── text_summarize(paper_text, style="academic")
    │           └── literature_gap(literature_summary)
    │
    ├── clawer-review-01（或 → research-review:10119）
    │       └── peer_review(survey_content, review_type="full")
    │
    └── clawer-writing-01（或 → research-writing:10116）
            └── report_generate(topic, content_points, report_type="review")
                    → 输出最终综述报告
```

### 示例二：代码任务单体执行（子实例模式）

```
用户："帮我分析这段 Python 代码的性能问题"
    ↓
TaskRouter 匹配 "code_task" → clawer-cs 类型
    ↓
instances/client.py → call_type("clawer-cs")
    → POST http://localhost:10111/api/tasks/execute
    ↓
research-cs(10111) 进入 tool_use 循环：
    │
    ├── 迭代1：LLM 调用 code_review(code, focus="performance")
    │       → 返回 LLM Action（action: "llm_review"）
    │       → BaseAgent 将 prompt 作为 tool_result 传回
    │
    ├── 迭代2：LLM 调用 code_execute(optimized_code)
    │       → 直接执行，返回 {"success": true, "output": "..."}
    │
    └── 迭代3：LLM finish_reason == "stop"
            → 输出最终性能分析报告 → 返回给编排层
```

### 示例三：跨技能数据分析

```python
# 完整的数据分析调用链
async def full_analysis_pipeline(dataset_url: str, research_question: str):
    # 1. 获取数据
    page = await execute_skill("url_fetch", {"url": dataset_url})

    # 2. 数据分析
    analysis = await execute_skill("data_analysis", {
        "data": page["content"],
        "analysis_type": "descriptive",
        "question": research_question
    })

    # 3. 统计检验推荐
    stats = await execute_skill("statistical_test", {
        "study_design": "两组独立样本，连续变量",
        "research_question": research_question
    })

    # 4. 生成报告
    report = await execute_skill("report_generate", {
        "topic": f"数据分析报告：{research_question}",
        "content_points": f"分析结果：{analysis}\n统计方法：{stats}",
        "report_type": "research"
    })

    return report
```

### 示例四：实例感知的技能分配验证

```python
# 验证某实例类型的技能配置
from instances.types import get_allowed_skills

skills = get_allowed_skills("clawer-ai")
print(f"AI 研究实例技能数量: {len(skills)}")
print(f"技能列表: {skills}")
# 输出：
# AI 研究实例技能数量: 11
# 技能列表: ['arxiv_search', 'paper_compare', 'experiment_design', ...]
```

---

*本文档基于 `skills/tools.py` v2.0 和 `config/instance_types.yaml` v2.0 生成。如发现技能描述不准确，请参考源代码注释。系统名称：openclaw_research，运行平台：Mac Studio。*
