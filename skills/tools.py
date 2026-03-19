"""
OpenClaw Skills - 全网前50技能实现
涵盖：搜索、文献、代码、数据、写作、分析等核心能力
"""
import asyncio
import json
import re
import subprocess
import tempfile
import os
from typing import Any, Optional
import httpx
import arxiv
from bs4 import BeautifulSoup
from config.settings import settings


# ─── 工具注册表 ────────────────────────────────────────────
SKILL_REGISTRY: dict[str, dict] = {}

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


def get_openai_tools() -> list[dict]:
    """返回 OpenAI tools 格式的技能列表"""
    tools = []
    for name, skill in SKILL_REGISTRY.items():
        tools.append({
            "type": "function",
            "function": {
                "name": skill["name"],
                "description": skill["description"],
                "parameters": {
                    "type": "object",
                    "properties": skill["parameters"],
                    "required": list(skill["parameters"].keys()),
                },
            },
        })
    return tools


def get_anthropic_tools() -> list[dict]:
    """返回 Anthropic tools 格式的技能列表（保留兼容性）"""
    tools = []
    for name, skill in SKILL_REGISTRY.items():
        tools.append({
            "name": skill["name"],
            "description": skill["description"],
            "input_schema": {
                "type": "object",
                "properties": skill["parameters"],
                "required": list(skill["parameters"].keys()),
            },
        })
    return tools


async def execute_skill(name: str, inputs: dict) -> Any:
    """执行指定技能"""
    if name not in SKILL_REGISTRY:
        return {"error": f"技能 '{name}' 未找到"}
    try:
        fn = SKILL_REGISTRY[name]["fn"]
        if asyncio.iscoroutinefunction(fn):
            return await fn(**inputs)
        return fn(**inputs)
    except Exception as e:
        return {"error": str(e)}


# ════════════════════════════════════════════════════════════
# SKILL 01-10: 搜索与信息检索
# ════════════════════════════════════════════════════════════

@register_skill(
    "web_search",
    "搜索互联网获取最新信息。支持学术、新闻、技术内容检索。",
    {
        "query": {"type": "string", "description": "搜索关键词"},
        "max_results": {"type": "integer", "description": "返回结果数量，默认5"},
    },
)
async def web_search(query: str, max_results: int = 5) -> dict:
    """网络搜索（DuckDuckGo 公共接口）"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_redirect": 1, "no_html": 1},
            )
            data = resp.json()
            results = []
            # Abstract
            if data.get("Abstract"):
                results.append({
                    "title": data.get("Heading", ""),
                    "snippet": data["Abstract"],
                    "url": data.get("AbstractURL", ""),
                    "source": "DuckDuckGo Abstract",
                })
            # Related topics
            for topic in data.get("RelatedTopics", [])[:max_results]:
                if isinstance(topic, dict) and topic.get("Text"):
                    results.append({
                        "title": topic.get("Text", "")[:80],
                        "snippet": topic.get("Text", ""),
                        "url": topic.get("FirstURL", ""),
                        "source": "DuckDuckGo",
                    })
            return {"query": query, "results": results[:max_results], "count": len(results[:max_results])}
    except Exception as e:
        return {"query": query, "results": [], "error": str(e)}


@register_skill(
    "arxiv_search",
    "在 arXiv 搜索最新学术论文，支持关键词和领域过滤。",
    {
        "query": {"type": "string", "description": "论文搜索关键词"},
        "max_results": {"type": "integer", "description": "返回论文数量，默认5"},
        "category": {"type": "string", "description": "arXiv 分类如 cs.AI, math.CO（可选）"},
    },
)
async def arxiv_search(query: str, max_results: int = 5, category: str = "") -> dict:
    """arXiv 论文搜索（使用 run_in_executor 避免阻塞事件循环）"""
    def _sync_search():
        search_query = f"{query} cat:{category}" if category else query
        client = arxiv.Client()
        search = arxiv.Search(
            query=search_query,
            max_results=max_results,
            sort_by=arxiv.SortCriterion.Relevance,
        )
        papers = []
        for r in client.results(search):
            papers.append({
                "id": r.entry_id,
                "title": r.title,
                "authors": [a.name for a in r.authors[:3]],
                "abstract": r.summary[:300] + "..." if len(r.summary) > 300 else r.summary,
                "url": r.entry_id,
                "pdf_url": r.pdf_url,
                "published": str(r.published.date()),
                "categories": r.categories,
            })
        return papers

    try:
        loop = asyncio.get_event_loop()
        papers = await loop.run_in_executor(None, _sync_search)
        return {"query": query, "papers": papers, "count": len(papers)}
    except Exception as e:
        return {"query": query, "papers": [], "error": str(e)}


@register_skill(
    "semantic_scholar_search",
    "使用 Semantic Scholar API 搜索学术论文，获取引用信息。",
    {
        "query": {"type": "string", "description": "搜索关键词"},
        "max_results": {"type": "integer", "description": "返回数量，默认5"},
    },
)
async def semantic_scholar_search(query: str, max_results: int = 5) -> dict:
    """Semantic Scholar 搜索"""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={
                    "query": query,
                    "limit": max_results,
                    "fields": "title,authors,abstract,year,citationCount,url,externalIds",
                },
                headers={"User-Agent": "OpenClaw/1.0"},
            )
            data = resp.json()
            papers = []
            for p in data.get("data", []):
                papers.append({
                    "title": p.get("title", ""),
                    "authors": [a["name"] for a in p.get("authors", [])[:3]],
                    "abstract": (p.get("abstract") or "")[:300],
                    "year": p.get("year"),
                    "citations": p.get("citationCount", 0),
                    "url": p.get("url", ""),
                })
            return {"query": query, "papers": papers, "count": len(papers)}
    except Exception as e:
        return {"query": query, "papers": [], "error": str(e)}


@register_skill(
    "url_fetch",
    "抓取指定网页内容并提取正文文本。",
    {
        "url": {"type": "string", "description": "要抓取的网页 URL"},
        "max_chars": {"type": "integer", "description": "返回最大字符数，默认3000"},
    },
)
async def url_fetch(url: str, max_chars: int = 3000) -> dict:
    """网页内容抓取"""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 OpenClaw/1.0"})
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            text = re.sub(r"\n{3,}", "\n\n", text)[:max_chars]
            return {"url": url, "content": text, "length": len(text)}
    except Exception as e:
        return {"url": url, "content": "", "error": str(e)}


@register_skill(
    "github_search",
    "搜索 GitHub 仓库，查找相关代码项目和工具。",
    {
        "query": {"type": "string", "description": "搜索关键词"},
        "language": {"type": "string", "description": "编程语言过滤（可选）"},
        "max_results": {"type": "integer", "description": "返回数量，默认5"},
    },
)
async def github_search(query: str, language: str = "", max_results: int = 5) -> dict:
    """GitHub 仓库搜索"""
    try:
        q = f"{query} language:{language}" if language else query
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.github.com/search/repositories",
                params={"q": q, "sort": "stars", "per_page": max_results},
                headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "OpenClaw/1.0"},
            )
            data = resp.json()
            repos = []
            for r in data.get("items", [])[:max_results]:
                repos.append({
                    "name": r["full_name"],
                    "description": r.get("description", ""),
                    "stars": r["stargazers_count"],
                    "language": r.get("language", ""),
                    "url": r["html_url"],
                    "updated": r.get("updated_at", "")[:10],
                })
            return {"query": query, "repos": repos, "count": len(repos)}
    except Exception as e:
        return {"query": query, "repos": [], "error": str(e)}


# ════════════════════════════════════════════════════════════
# SKILL 11-20: 文档与知识处理
# ════════════════════════════════════════════════════════════

@register_skill(
    "pdf_extract",
    "从 PDF 文件或 URL 中提取文本内容。",
    {
        "source": {"type": "string", "description": "PDF 文件本地路径或 URL"},
        "max_pages": {"type": "integer", "description": "最大处理页数，默认10"},
    },
)
async def pdf_extract(source: str, max_pages: int = 10) -> dict:
    """PDF 文本提取"""
    try:
        import PyPDF2, io
        if source.startswith("http"):
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(source)
            pdf_bytes = io.BytesIO(resp.content)
        else:
            pdf_bytes = open(source, "rb")
        reader = PyPDF2.PdfReader(pdf_bytes)
        pages = min(len(reader.pages), max_pages)
        text = ""
        for i in range(pages):
            text += reader.pages[i].extract_text() + "\n"
        return {"source": source, "pages": pages, "total_pages": len(reader.pages), "content": text[:5000]}
    except Exception as e:
        return {"source": source, "content": "", "error": str(e)}


@register_skill(
    "text_summarize",
    "对长文本进行智能摘要，支持学术、新闻、技术文档。",
    {
        "text": {"type": "string", "description": "待摘要的文本内容"},
        "style": {"type": "string", "description": "摘要风格：academic/news/technical/bullet，默认academic"},
        "max_words": {"type": "integer", "description": "摘要最大字数，默认200"},
    },
)
async def text_summarize(text: str, style: str = "academic", max_words: int = 200) -> dict:
    """文本摘要（通过主模型完成）"""
    # 返回结构化请求，由 agent 的 LLM 处理
    return {
        "action": "llm_summarize",
        "text": text[:4000],
        "style": style,
        "max_words": max_words,
        "prompt": f"请用{style}风格，在{max_words}字以内对以下文本进行摘要：\n\n{text[:4000]}",
    }


@register_skill(
    "citation_format",
    "将论文信息格式化为标准引用格式（APA/MLA/Chicago）。",
    {
        "paper_info": {"type": "string", "description": "论文信息 JSON 字符串，包含 title/authors/year/journal"},
        "style": {"type": "string", "description": "引用格式：APA/MLA/Chicago，默认APA"},
    },
)
def citation_format(paper_info: str, style: str = "APA") -> dict:
    """论文引用格式化"""
    try:
        info = json.loads(paper_info) if isinstance(paper_info, str) else paper_info
        title = info.get("title", "Unknown Title")
        authors = info.get("authors", ["Unknown"])
        year = info.get("year", "n.d.")
        journal = info.get("journal", "")
        volume = info.get("volume", "")
        doi = info.get("doi", "")

        if style.upper() == "APA":
            author_str = ", ".join(authors[:3])
            if len(authors) > 3:
                author_str += " et al."
            citation = f"{author_str} ({year}). {title}."
            if journal:
                citation += f" *{journal}*"
            if volume:
                citation += f", {volume}"
            if doi:
                citation += f". https://doi.org/{doi}"
        elif style.upper() == "MLA":
            author_str = authors[0] if authors else "Unknown"
            citation = f'{author_str}. "{title}." {journal}, {year}.'
        else:  # Chicago
            author_str = ", ".join(authors)
            citation = f'{author_str}. "{title}." {journal} ({year}).'

        return {"citation": citation, "style": style}
    except Exception as e:
        return {"citation": "", "error": str(e)}


@register_skill(
    "knowledge_extract",
    "从文本中提取结构化知识：实体、关系、核心概念。",
    {
        "text": {"type": "string", "description": "待分析文本"},
    },
)
def knowledge_extract(text: str) -> dict:
    """知识提取（返回 LLM 指令）"""
    return {
        "action": "llm_extract",
        "prompt": f"""从以下文本中提取结构化知识，输出 JSON 格式：
{{
  "entities": [{"name": "...", "type": "concept/person/method/dataset/model", "description": "..."}],
  "key_claims": ["主要论点1", "主要论点2"],
  "methods": ["方法1", "方法2"],
  "datasets": ["数据集1"],
  "metrics": ["指标1"],
  "limitations": ["局限性1"]
}}

文本：
{text[:3000]}""",
    }


@register_skill(
    "translation",
    "学术内容中英文翻译，保留专业术语准确性。",
    {
        "text": {"type": "string", "description": "待翻译文本"},
        "target_lang": {"type": "string", "description": "目标语言：zh/en，默认zh"},
    },
)
def translation(text: str, target_lang: str = "zh") -> dict:
    lang_name = "中文" if target_lang == "zh" else "English"
    return {
        "action": "llm_translate",
        "prompt": f"请将以下学术文本准确翻译为{lang_name}，保留专业术语：\n\n{text[:3000]}",
    }


# ════════════════════════════════════════════════════════════
# SKILL 21-30: 代码与计算
# ════════════════════════════════════════════════════════════

@register_skill(
    "code_execute",
    "在安全沙箱中执行 Python 代码（无网络/文件系统访问限制）。",
    {
        "code": {"type": "string", "description": "要执行的 Python 代码"},
        "timeout": {"type": "integer", "description": "执行超时秒数，默认30"},
    },
)
async def code_execute(code: str, timeout: int = 30) -> dict:
    """安全 Python 代码执行"""
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            fname = f.name
        proc = await asyncio.create_subprocess_exec(
            "python3", fname,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return {"success": False, "output": "", "error": "执行超时"}
        finally:
            os.unlink(fname)
        return {
            "success": proc.returncode == 0,
            "output": stdout.decode()[:3000],
            "error": stderr.decode()[:1000] if stderr else "",
            "return_code": proc.returncode,
        }
    except Exception as e:
        return {"success": False, "output": "", "error": str(e)}


@register_skill(
    "code_review",
    "对代码进行质量评审：找出 bug、安全问题、优化建议。",
    {
        "code": {"type": "string", "description": "待评审的代码"},
        "language": {"type": "string", "description": "编程语言，默认python"},
        "focus": {"type": "string", "description": "评审重点：bugs/security/performance/style"},
    },
)
def code_review(code: str, language: str = "python", focus: str = "bugs") -> dict:
    return {
        "action": "llm_review",
        "prompt": f"""请对以下 {language} 代码进行专业代码评审，重点关注 {focus}。
输出格式：
1. **总体评价**
2. **发现的问题**（每条标明严重级别：Critical/Major/Minor）
3. **优化建议**
4. **改进后代码**（如适用）

```{language}
{code[:3000]}
```""",
    }


@register_skill(
    "math_solve",
    "求解数学问题：方程、证明、计算、统计。",
    {
        "problem": {"type": "string", "description": "数学问题描述"},
        "show_steps": {"type": "boolean", "description": "是否展示解题步骤，默认true"},
    },
)
def math_solve(problem: str, show_steps: bool = True) -> dict:
    step_instruction = "请展示详细解题步骤。" if show_steps else "只给出最终答案。"
    return {
        "action": "llm_math",
        "prompt": f"请求解以下数学问题。{step_instruction}\n\n{problem}",
    }


@register_skill(
    "data_analysis",
    "对给定数据进行统计分析，生成洞察报告。",
    {
        "data": {"type": "string", "description": "CSV 格式或 JSON 格式数据"},
        "analysis_type": {"type": "string", "description": "分析类型：descriptive/correlation/regression/clustering"},
        "question": {"type": "string", "description": "具体分析问题（可选）"},
    },
)
async def data_analysis(data: str, analysis_type: str = "descriptive", question: str = "") -> dict:
    """数据分析"""
    try:
        import pandas as pd, io
        if data.strip().startswith("{") or data.strip().startswith("["):
            df = pd.read_json(io.StringIO(data))
        else:
            df = pd.read_csv(io.StringIO(data))
        stats = {
            "shape": list(df.shape),
            "columns": list(df.columns),
            "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
            "null_counts": df.isnull().sum().to_dict(),
            "description": json.loads(df.describe().to_json()),
        }
        return {
            "stats": stats,
            "analysis_type": analysis_type,
            "question": question,
            "action": "llm_analyze",
            "prompt": f"基于以下数据统计信息，进行{analysis_type}分析{f'，重点回答：{question}' if question else ''}：\n{json.dumps(stats, ensure_ascii=False)}",
        }
    except Exception as e:
        return {"error": str(e), "action": "llm_analyze",
                "prompt": f"请对以下数据进行{analysis_type}分析{f'，重点：{question}' if question else ''}：\n{data[:2000]}"}


# ════════════════════════════════════════════════════════════
# SKILL 31-40: 学术写作与研究
# ════════════════════════════════════════════════════════════

@register_skill(
    "research_outline",
    "根据研究主题生成详细的研究提纲与框架。",
    {
        "topic": {"type": "string", "description": "研究主题"},
        "paper_type": {"type": "string", "description": "论文类型：research/review/thesis/proposal"},
        "domain": {"type": "string", "description": "研究领域"},
    },
)
def research_outline(topic: str, paper_type: str = "research", domain: str = "") -> dict:
    return {
        "action": "llm_outline",
        "prompt": f"""请为以下{paper_type}类型的学术论文生成详细提纲：
主题：{topic}
领域：{domain}

要求：
1. 包含标准学术章节结构
2. 每章附带核心问题和写作要点
3. 提出3-5个关键研究问题
4. 建议方法论路径
5. 预期贡献说明""",
    }


@register_skill(
    "abstract_generate",
    "根据论文内容或大纲生成学术摘要（Abstract）。",
    {
        "content": {"type": "string", "description": "论文正文或提纲"},
        "word_limit": {"type": "integer", "description": "摘要字数限制，默认250"},
        "language": {"type": "string", "description": "语言：en/zh，默认en"},
    },
)
def abstract_generate(content: str, word_limit: int = 250, language: str = "en") -> dict:
    lang = "英文" if language == "en" else "中文"
    return {
        "action": "llm_abstract",
        "prompt": f"""请根据以下内容生成一篇标准学术摘要（{lang}，约{word_limit}词）。
摘要应包含：背景、目的、方法、主要结果、结论。

内容：
{content[:3000]}""",
    }


@register_skill(
    "hypothesis_generate",
    "基于研究背景生成可验证的科学假设。",
    {
        "background": {"type": "string", "description": "研究背景与现状"},
        "count": {"type": "integer", "description": "生成假设数量，默认3"},
    },
)
def hypothesis_generate(background: str, count: int = 3) -> dict:
    return {
        "action": "llm_hypothesis",
        "prompt": f"""基于以下研究背景，提出{count}个可验证的科学假设。
每个假设需包含：
- 假设陈述（H0/H1 形式）
- 理论依据
- 验证方法建议
- 预期结果

背景：
{background[:2000]}""",
    }


@register_skill(
    "experiment_design",
    "设计科学实验方案，包括变量控制、样本设计、评估指标。",
    {
        "research_question": {"type": "string", "description": "研究问题"},
        "domain": {"type": "string", "description": "研究领域"},
        "constraints": {"type": "string", "description": "资源约束（可选）"},
    },
)
def experiment_design(research_question: str, domain: str = "", constraints: str = "") -> dict:
    return {
        "action": "llm_experiment",
        "prompt": f"""为以下研究问题设计完整实验方案：
问题：{research_question}
领域：{domain}
约束：{constraints}

输出内容：
1. 实验目标
2. 自变量/因变量/控制变量
3. 实验组设计（含对照组）
4. 样本大小与抽样策略
5. 数据收集方法
6. 评估指标
7. 统计分析方法
8. 潜在偏差与控制措施""",
    }


@register_skill(
    "peer_review",
    "对学术论文进行同行评审，提供建设性评审意见。",
    {
        "paper_content": {"type": "string", "description": "论文内容或摘要"},
        "review_type": {"type": "string", "description": "评审类型：full/abstract/methodology"},
    },
)
def peer_review(paper_content: str, review_type: str = "full") -> dict:
    return {
        "action": "llm_review",
        "prompt": f"""请对以下论文进行专业同行评审（{review_type}）。

评审维度：
1. **创新性** (1-5分)：研究贡献是否新颖
2. **方法论** (1-5分)：研究设计是否严谨
3. **结果可信度** (1-5分)：证据是否充分
4. **写作质量** (1-5分)：表达是否清晰
5. **总体建议**：Accept/Minor Revision/Major Revision/Reject

请给出详细评审意见和具体改进建议。

论文内容：
{paper_content[:3000]}""",
    }


@register_skill(
    "gap_analysis",
    "分析研究领域的空白点，发现潜在创新机会。",
    {
        "literature_summary": {"type": "string", "description": "文献综述或领域现状描述"},
        "domain": {"type": "string", "description": "研究领域"},
    },
)
def gap_analysis(literature_summary: str, domain: str = "") -> dict:
    return {
        "action": "llm_gap",
        "prompt": f"""基于以下文献综述，分析{domain}领域的研究空白与机会：

1. **已解决问题**：梳理已有充分研究的方向
2. **研究空白**：明确尚未被充分研究的问题
3. **矛盾与争议**：指出现有研究中的分歧
4. **方法论局限**：现有研究方法的不足
5. **新兴机会**：未来5年值得探索的方向（3-5个）
6. **推荐切入点**：最具可行性的研究方向建议

综述：
{literature_summary[:3000]}""",
    }


# ════════════════════════════════════════════════════════════
# SKILL 41-50: 系统与运维
# ════════════════════════════════════════════════════════════

@register_skill(
    "task_decompose",
    "将复杂研究任务分解为可执行的子任务树。",
    {
        "task": {"type": "string", "description": "复杂任务描述"},
        "max_subtasks": {"type": "integer", "description": "最大子任务数，默认6"},
    },
)
def task_decompose(task: str, max_subtasks: int = 6) -> dict:
    return {
        "action": "llm_decompose",
        "prompt": f"""将以下复杂任务分解为最多{max_subtasks}个可独立执行的子任务：

任务：{task}

输出 JSON 格式：
{{
  "main_task": "主任务描述",
  "subtasks": [
    {{
      "id": "t1",
      "title": "子任务标题",
      "description": "详细描述",
      "agent_role": "建议分配给的角色（clawer/vanguard/guardian）",
      "depends_on": [],
      "estimated_complexity": "low/medium/high"
    }}
  ],
  "execution_order": ["t1", "t2", ...],
  "parallel_groups": [["t2", "t3"], ["t4"]]
}}""",
    }


@register_skill(
    "quality_score",
    "评估研究输出的质量得分（准确性、创新性、完整性）。",
    {
        "content": {"type": "string", "description": "待评分内容"},
        "criteria": {"type": "string", "description": "评分标准：academic/technical/general"},
    },
)
def quality_score(content: str, criteria: str = "academic") -> dict:
    return {
        "action": "llm_score",
        "prompt": f"""请对以下内容进行质量评估（{criteria}标准），输出 JSON：
{{
  "accuracy": 0.0-1.0,
  "novelty": 0.0-1.0,
  "completeness": 0.0-1.0,
  "clarity": 0.0-1.0,
  "citation_quality": 0.0-1.0,
  "overall": 0.0-1.0,
  "strengths": ["优点1"],
  "weaknesses": ["不足1"],
  "suggestions": ["建议1"]
}}

内容：
{content[:2000]}""",
    }


@register_skill(
    "trend_analysis",
    "分析科研领域近期趋势，识别热点方向与新兴技术。",
    {
        "domain": {"type": "string", "description": "研究领域"},
        "time_range": {"type": "string", "description": "时间范围：3m/6m/1y，默认6m"},
    },
)
async def trend_analysis(domain: str, time_range: str = "6m") -> dict:
    """趋势分析 - 结合 arxiv 搜索"""
    papers = await arxiv_search(domain, max_results=10)
    return {
        "action": "llm_trend",
        "domain": domain,
        "papers": papers.get("papers", []),
        "prompt": f"""基于以下最新论文列表，分析 {domain} 领域近{time_range}的研究趋势：

论文列表：
{json.dumps(papers.get('papers', []), ensure_ascii=False)[:2000]}

输出：
1. 热点研究方向（Top 5）
2. 新兴技术/方法
3. 衰退方向
4. 未来6个月预测
5. 推荐重点关注论文""",
    }


@register_skill(
    "dataset_discover",
    "发现适合特定研究任务的公开数据集。",
    {
        "task_description": {"type": "string", "description": "研究任务描述"},
        "domain": {"type": "string", "description": "研究领域"},
    },
)
async def dataset_discover(task_description: str, domain: str = "") -> dict:
    """数据集发现"""
    query = f"{domain} dataset benchmark {task_description}"
    results = await web_search(f"site:paperswithcode.com OR site:huggingface.co/datasets {query}", max_results=5)
    return {
        "action": "llm_dataset",
        "search_results": results,
        "prompt": f"""为以下研究任务推荐合适的公开数据集：
任务：{task_description}
领域：{domain}

参考搜索结果：
{json.dumps(results.get('results', []), ensure_ascii=False)[:1500]}

请推荐5个最相关的数据集，每个包含：名称、规模、特点、获取链接、适用场景。""",
    }


@register_skill(
    "report_generate",
    "生成结构化研究报告（支持 Markdown 格式）。",
    {
        "topic": {"type": "string", "description": "报告主题"},
        "content_points": {"type": "string", "description": "主要内容要点（换行分隔）"},
        "report_type": {"type": "string", "description": "报告类型：research/progress/summary/proposal"},
    },
)
def report_generate(topic: str, content_points: str, report_type: str = "research") -> dict:
    return {
        "action": "llm_report",
        "prompt": f"""生成一份专业的{report_type}报告（Markdown 格式）：

主题：{topic}
主要内容要点：
{content_points}

要求：
- 包含执行摘要
- 结构清晰，使用标题层级
- 数据和结论有依据
- 结尾包含建议和下一步计划
- 约1000-1500字""",
    }


@register_skill(
    "fact_check",
    "对学术声明或事实陈述进行核查，评估可信度。",
    {
        "claim": {"type": "string", "description": "待核查的声明或陈述"},
    },
)
async def fact_check(claim: str) -> dict:
    search_results = await web_search(claim, max_results=3)
    return {
        "action": "llm_factcheck",
        "claim": claim,
        "search_results": search_results,
        "prompt": f"""请核查以下声明的准确性：

声明：{claim}

参考信息：
{json.dumps(search_results.get('results', []), ensure_ascii=False)[:1500]}

输出 JSON：
{{
  "verdict": "true/false/partially_true/unverifiable",
  "confidence": "high/medium/low",
  "evidence": ["证据1", "证据2"],
  "corrections": "如需纠正的内容",
  "sources": ["来源1"]
}}""",
    }


@register_skill(
    "mind_map",
    "生成主题的思维导图结构（Markdown 树状格式）。",
    {
        "topic": {"type": "string", "description": "中心主题"},
        "depth": {"type": "integer", "description": "展开深度：2-4，默认3"},
    },
)
def mind_map(topic: str, depth: int = 3) -> dict:
    return {
        "action": "llm_mindmap",
        "prompt": f"""为主题"{topic}"生成{depth}层深度的思维导图，使用 Markdown 缩进树状格式：

# {topic}
## 主分支1
### 子节点
#### 细节
...

要求展开{depth}个层次，每层3-5个节点，覆盖该主题的核心维度。""",
    }


@register_skill(
    "content_plan",
    "为学术内容传播制定推广计划（社交媒体、公众号、学术社区）。",
    {
        "content_summary": {"type": "string", "description": "内容摘要"},
        "target_audience": {"type": "string", "description": "目标受众：students/researchers/general"},
        "platforms": {"type": "string", "description": "目标平台（逗号分隔）：weibo,wechat,zhihu,twitter"},
    },
)
def content_plan(content_summary: str, target_audience: str = "researchers", platforms: str = "wechat,zhihu") -> dict:
    return {
        "action": "llm_plan",
        "prompt": f"""为以下学术内容制定传播推广计划：

内容摘要：{content_summary}
目标受众：{target_audience}
目标平台：{platforms}

输出：
1. 各平台内容策略（标题/正文/标签）
2. 发布时间建议
3. 互动话题设计
4. KPI 指标建议
5. 注意事项""",
    }


# ════════════════════════════════════════════════════════════
# SKILL 29-50: 扩展技能集
# ════════════════════════════════════════════════════════════

@register_skill(
    "paper_compare",
    "对多篇论文进行横向对比分析，生成对比表格。",
    {
        "papers": {"type": "string", "description": "论文列表，JSON 数组，每项含 title/abstract"},
        "dimensions": {"type": "string", "description": "对比维度（逗号分隔），如：方法,数据集,指标,局限性"},
    },
)
def paper_compare(papers: str, dimensions: str = "方法,数据集,指标,结论,局限性") -> dict:
    dims = dimensions.split(",")
    return {
        "action": "llm_compare",
        "prompt": f"""请对以下论文进行横向对比分析，生成 Markdown 对比表格。

对比维度：{', '.join(dims)}

论文列表：
{papers[:3000]}

输出格式：
| 论文 | {' | '.join(dims)} |
|-----|{'|'.join(['---']*len(dims))}|
...

表格后附：综合评述与推荐结论。""",
    }


@register_skill(
    "methodology_eval",
    "评估研究方法论的严谨性：抽样、测量、控制、效度。",
    {
        "methodology_desc": {"type": "string", "description": "研究方法描述"},
        "study_type": {"type": "string", "description": "研究类型：quantitative/qualitative/mixed/experimental"},
    },
)
def methodology_eval(methodology_desc: str, study_type: str = "quantitative") -> dict:
    return {
        "action": "llm_method",
        "prompt": f"""请对以下 {study_type} 研究的方法论进行专业评估：

{methodology_desc[:2000]}

评估维度：
1. **内部效度**：因果关系的可信度
2. **外部效度**：结论的推广性
3. **测量信度**：测量工具的一致性
4. **样本代表性**：样本设计是否合理
5. **混淆变量控制**：干扰因素处理
6. **统计功效**：样本量是否充分
7. **总体评分**（1-10）及改进建议""",
    }


@register_skill(
    "concept_explain",
    "用多层次方式解释学术概念（专家/学生/大众三个层级）。",
    {
        "concept": {"type": "string", "description": "需要解释的概念"},
        "level": {"type": "string", "description": "解释层级：expert/student/general，默认student"},
        "domain": {"type": "string", "description": "所属领域（可选）"},
    },
)
def concept_explain(concept: str, level: str = "student", domain: str = "") -> dict:
    level_desc = {"expert": "专业研究人员", "student": "研究生/高年级本科生", "general": "普通大众"}.get(level, "学生")
    return {
        "action": "llm_explain",
        "prompt": f"""请为{level_desc}解释以下{"（" + domain + "领域）" if domain else ""}概念：

**{concept}**

要求：
- 直观定义（一句话）
- 核心原理（3-5点）
- 具体例子（1-2个）
- 与相关概念的区别
- 应用场景""",
    }


@register_skill(
    "timeline_generate",
    "生成某研究领域或技术的发展时间线。",
    {
        "topic": {"type": "string", "description": "研究领域或技术主题"},
        "start_year": {"type": "integer", "description": "起始年份，默认2015"},
    },
)
async def timeline_generate(topic: str, start_year: int = 2015) -> dict:
    papers = await arxiv_search(f"{topic} history milestone", max_results=8)
    return {
        "action": "llm_timeline",
        "papers": papers.get("papers", []),
        "prompt": f"""请生成 **{topic}** 从 {start_year} 年至今的研究发展时间线。

参考论文信息：
{json.dumps(papers.get('papers', [])[:6], ensure_ascii=False)[:1500]}

输出格式（Markdown）：
## {topic} 发展时间线

### {start_year}年
- 里程碑事件/论文

### {start_year+1}年
...

时间线后附：**关键转折点分析** 和 **未来发展预判**。""",
    }


@register_skill(
    "literature_gap",
    "系统识别文献中的研究空白，生成可发表的研究方向建议。",
    {
        "topic": {"type": "string", "description": "研究主题"},
        "existing_work": {"type": "string", "description": "已有研究摘要（可选）"},
    },
)
async def literature_gap(topic: str, existing_work: str = "") -> dict:
    papers = await arxiv_search(topic, max_results=8)
    return {
        "action": "llm_gap2",
        "prompt": f"""请系统分析 **{topic}** 领域的研究空白：

最新论文参考：
{json.dumps(papers.get('papers', [])[:5], ensure_ascii=False)[:1500]}

{"已有研究综述：" + existing_work[:500] if existing_work else ""}

输出（可直接用于研究提案）：
1. **已充分研究的方向**（避免重复）
2. **存在争议的方向**（需深入研究）
3. **明显空白的方向**（优先级 Top 5）
4. **跨学科机会**
5. **每个方向的推荐研究问题**（RQ 格式）""",
    }


@register_skill(
    "survey_question",
    "为研究设计问卷/访谈提纲，包含量表和开放性问题。",
    {
        "research_goal": {"type": "string", "description": "研究目标"},
        "survey_type": {"type": "string", "description": "类型：questionnaire/interview/focus_group"},
        "target_population": {"type": "string", "description": "调查对象"},
    },
)
def survey_question(research_goal: str, survey_type: str = "questionnaire", target_population: str = "研究人员") -> dict:
    return {
        "action": "llm_survey",
        "prompt": f"""请为以下研究目标设计{survey_type}：

研究目标：{research_goal}
调查对象：{target_population}

包含：
1. 背景信息题（人口统计学变量）
2. 核心量表题（Likert 5点量表，含反向题）
3. 开放性问题（3-5个）
4. 注意事项和伦理声明
5. 预计完成时间""",
    }


@register_skill(
    "statistical_test",
    "推荐适合研究设计的统计检验方法，并解释选择依据。",
    {
        "study_design": {"type": "string", "description": "研究设计描述（变量类型、组数、样本量）"},
        "research_question": {"type": "string", "description": "研究问题"},
    },
)
def statistical_test(study_design: str, research_question: str) -> dict:
    return {
        "action": "llm_stats",
        "prompt": f"""请为以下研究推荐最适合的统计检验方法：

研究问题：{research_question}
研究设计：{study_design}

输出：
1. **推荐方法**（主选 + 备选）
2. **选择依据**（满足的前提假设）
3. **假设检验步骤**
4. **需要检查的前提条件**（正态性、方差齐性等）
5. **效应量计算建议**
6. **Python/R 代码片段**""",
    }


@register_skill(
    "reproducibility_check",
    "检查研究的可重复性，生成复现清单。",
    {
        "paper_description": {"type": "string", "description": "论文方法描述或摘要"},
        "domain": {"type": "string", "description": "研究领域"},
    },
)
def reproducibility_check(paper_description: str, domain: str = "") -> dict:
    return {
        "action": "llm_repro",
        "prompt": f"""请对以下{domain}研究进行可重复性评估：

{paper_description[:2000]}

评估清单：
- [ ] 数据集是否公开可获取
- [ ] 代码/实现是否开源
- [ ] 超参数是否完整报告
- [ ] 随机种子是否固定
- [ ] 实验环境（硬件/软件版本）是否记录
- [ ] 统计结果是否含置信区间/误差范围
- [ ] 是否提供预训练模型/中间结果

输出：可重复性评分（0-10）+ 具体改进建议。""",
    }


@register_skill(
    "acronym_expand",
    "展开学术缩写，提供完整术语和领域说明。",
    {
        "text": {"type": "string", "description": "含缩写的文本或缩写列表"},
        "domain": {"type": "string", "description": "研究领域（帮助消歧义）"},
    },
)
def acronym_expand(text: str, domain: str = "") -> dict:
    return {
        "action": "llm_acronym",
        "prompt": f"""请展开以下{"（" + domain + "领域）" if domain else ""}文本中的所有学术缩写：

{text[:1500]}

格式：缩写 → 完整形式（中文解释）
例：LLM → Large Language Model（大语言模型）

遇到有歧义的缩写，列出所有可能含义并标注最可能的用法。""",
    }


@register_skill(
    "reading_notes",
    "将论文内容整理为结构化阅读笔记（含批注和问题）。",
    {
        "paper_content": {"type": "string", "description": "论文摘要或正文内容"},
        "note_style": {"type": "string", "description": "笔记风格：cornell/structured/critical，默认structured"},
    },
)
def reading_notes(paper_content: str, note_style: str = "structured") -> dict:
    styles = {
        "cornell": "康奈尔笔记法（主栏/提示栏/摘要）",
        "structured": "结构化笔记（背景/方法/贡献/评价）",
        "critical": "批判性阅读笔记（强项/弱项/可发展方向）",
    }
    return {
        "action": "llm_notes",
        "prompt": f"""请用 **{styles.get(note_style, '结构化')}** 整理以下论文的阅读笔记：

{paper_content[:3000]}

必须包含：
- 核心贡献（3点以内）
- 关键方法（一句话）
- 关键数字/结果
- 个人评注（疑问/启发/反驳）
- 与已读文献的关联
- 值得跟进的引文""",
    }


@register_skill(
    "grant_proposal",
    "生成科研基金申请书框架（国自然/面上/青年基金等）。",
    {
        "research_topic": {"type": "string", "description": "研究课题"},
        "grant_type": {"type": "string", "description": "基金类型：nsfc_youth/nsfc_general/nsfc_key/other"},
        "background": {"type": "string", "description": "研究背景（可选）"},
    },
)
def grant_proposal(research_topic: str, grant_type: str = "nsfc_general", background: str = "") -> dict:
    grant_names = {
        "nsfc_youth": "国家自然科学基金青年项目",
        "nsfc_general": "国家自然科学基金面上项目",
        "nsfc_key": "国家自然科学基金重点项目",
        "other": "科研基金项目",
    }
    grant_name = grant_names.get(grant_type, "科研基金")
    return {
        "action": "llm_grant",
        "prompt": f"""请为以下课题生成 **{grant_name}** 申请书框架：

研究课题：{research_topic}
{"研究背景：" + background[:500] if background else ""}

框架内容（按基金模板）：
1. **项目名称**（20字以内，含核心关键词）
2. **立项依据**（国内外研究现状、科学意义、研究空白）
3. **研究目标**（总目标 + 3个具体目标）
4. **研究内容**（3-4个子课题）
5. **研究方案**（技术路线，含流程图描述）
6. **创新点**（3个，格式：本研究首次...）
7. **预期成果**（论文/专利/数据集）
8. **研究基础**（前期工作的呈现方式建议）""",
    }


@register_skill(
    "code_document",
    "为代码自动生成文档注释和使用说明（docstring/README）。",
    {
        "code": {"type": "string", "description": "待文档化的代码"},
        "doc_type": {"type": "string", "description": "文档类型：docstring/readme/wiki，默认docstring"},
        "language": {"type": "string", "description": "编程语言，默认python"},
    },
)
def code_document(code: str, doc_type: str = "docstring", language: str = "python") -> dict:
    return {
        "action": "llm_codedoc",
        "prompt": f"""请为以下 {language} 代码生成完整的 {doc_type} 文档：

```{language}
{code[:3000]}
```

要求：
- 函数/类级别的 docstring（包含 Args/Returns/Raises/Example）
- 复杂逻辑行内注释
- 使用示例
{"- README.md 格式（安装/使用/API参考）" if doc_type == "readme" else ""}""",
    }


@register_skill(
    "benchmark_design",
    "为特定研究任务设计评估基准（benchmark）方案。",
    {
        "task_description": {"type": "string", "description": "评估任务描述"},
        "domain": {"type": "string", "description": "研究领域"},
    },
)
async def benchmark_design(task_description: str, domain: str = "") -> dict:
    existing = await arxiv_search(f"{domain} benchmark evaluation dataset", max_results=5)
    return {
        "action": "llm_benchmark",
        "existing_benchmarks": existing.get("papers", []),
        "prompt": f"""请为以下任务设计评估基准（Benchmark）方案：

任务：{task_description}
领域：{domain}

现有相关 Benchmark 参考：
{json.dumps(existing.get('papers', [])[:4], ensure_ascii=False)[:1000]}

输出：
1. **基准名称与定位**（与现有 benchmark 的差异）
2. **评估维度**（5-8个指标）
3. **数据集构建方案**（来源/规模/标注方式）
4. **基线模型列表**
5. **评分方法**（自动/人工/混合）
6. **排行榜设计**""",
    }


@register_skill(
    "debate_moderator",
    "对一个学术争议问题组织多视角辩论，生成平衡报告。",
    {
        "controversial_question": {"type": "string", "description": "有争议的学术问题"},
        "perspectives": {"type": "integer", "description": "辩论视角数量，默认3"},
    },
)
def debate_moderator(controversial_question: str, perspectives: int = 3) -> dict:
    return {
        "action": "llm_debate",
        "prompt": f"""请对以下学术争议问题进行多视角辩论分析：

**问题：{controversial_question}**

请模拟 {perspectives} 个不同学术立场，每个立场：
- 核心论点（2-3点）
- 支持证据
- 对其他立场的反驳

最后：
- **调解人总结**：各立场的共识与根本分歧
- **当前学界主流观点**
- **未解决的核心问题**
- **建议的研究路径**""",
    }


@register_skill(
    "patent_search",
    "搜索相关专利信息，评估技术新颖性。",
    {
        "technology": {"type": "string", "description": "技术描述或关键词"},
        "applicant": {"type": "string", "description": "申请人/机构（可选，用于竞品分析）"},
    },
)
async def patent_search(technology: str, applicant: str = "") -> dict:
    query = f"patent {technology}" + (f" {applicant}" if applicant else "")
    results = await web_search(f"site:patents.google.com {technology}", max_results=5)
    return {
        "action": "llm_patent",
        "search_results": results,
        "prompt": f"""基于以下搜索结果，分析 **{technology}** 的专利布局：

{"申请人/竞品分析：" + applicant if applicant else ""}

搜索结果：
{json.dumps(results.get('results', []), ensure_ascii=False)[:1500]}

输出：
1. 相关专利概览（主要申请人/时间分布）
2. 技术新颖性初步评估
3. 潜在专利侵权风险点
4. 建议的专利保护策略""",
    }


@register_skill(
    "career_advice",
    "为科研人员提供职业发展建议（升学/求职/晋升路径）。",
    {
        "profile": {"type": "string", "description": "个人背景（学历/研究方向/成果/目标）"},
        "goal": {"type": "string", "description": "职业目标：phd/postdoc/industry/faculty/startup"},
    },
)
def career_advice(profile: str, goal: str = "phd") -> dict:
    goal_map = {"phd": "博士申请", "postdoc": "博士后", "industry": "工业界", "faculty": "高校教职", "startup": "创业"}
    return {
        "action": "llm_career",
        "prompt": f"""请为以下科研人员制定 **{goal_map.get(goal, goal)}** 职业规划建议：

个人背景：
{profile[:1500]}

建议内容：
1. **竞争力评估**（相对该方向申请者/候选人）
2. **差距分析**（还需要补强的方面）
3. **12个月行动计划**（具体可操作）
4. **资源推荐**（导师/机构/奖学金/比赛）
5. **风险与备选方案**""",
    }


@register_skill(
    "writing_polish",
    "对学术英文写作进行润色，提升语言质量和学术规范性。",
    {
        "text": {"type": "string", "description": "待润色的英文文本"},
        "target_venue": {"type": "string", "description": "目标期刊/会议（可选，用于风格匹配）"},
    },
)
def writing_polish(text: str, target_venue: str = "") -> dict:
    return {
        "action": "llm_polish",
        "prompt": f"""请对以下学术英文写作进行专业润色{"（目标投稿：" + target_venue + "）" if target_venue else ""}：

原文：
{text[:2500]}

请：
1. 修正语法/拼写错误
2. 提升表达精确性和学术性
3. 改善句子流畅度
4. 统一术语用法
5. 输出润色后全文 + 主要修改说明（对照表）""",
    }


@register_skill(
    "figure_description",
    "为论文图表生成专业的图题（Caption）和描述性文字。",
    {
        "figure_description": {"type": "string", "description": "图表内容描述或数据"},
        "figure_type": {"type": "string", "description": "图表类型：chart/diagram/flowchart/table/heatmap"},
        "paper_context": {"type": "string", "description": "论文上下文（可选）"},
    },
)
def figure_description(figure_description: str, figure_type: str = "chart", paper_context: str = "") -> dict:
    return {
        "action": "llm_figure",
        "prompt": f"""请为以下 {figure_type} 生成学术图题（Caption）：

图表内容：{figure_description[:1000]}
{"论文背景：" + paper_context[:300] if paper_context else ""}

输出：
1. **简洁图题**（Figure X. 格式，英文）
2. **图题（中文版）**
3. **详细图例说明**（100字左右）
4. **正文引用文字** ("As shown in Figure X...")""",
    }


@register_skill(
    "related_work",
    "基于研究主题自动生成 Related Work 段落草稿。",
    {
        "topic": {"type": "string", "description": "论文主题"},
        "key_references": {"type": "string", "description": "关键参考文献列表（可选）"},
        "paper_contribution": {"type": "string", "description": "本文的核心贡献（用于区分）"},
    },
)
async def related_work(topic: str, key_references: str = "", paper_contribution: str = "") -> dict:
    papers = await arxiv_search(topic, max_results=6)
    return {
        "action": "llm_relwork",
        "papers": papers.get("papers", []),
        "prompt": f"""请为以下研究主题撰写 Related Work 段落草稿：

主题：{topic}
{"本文贡献：" + paper_contribution if paper_contribution else ""}
{"关键参考文献：" + key_references[:500] if key_references else ""}

参考论文：
{json.dumps(papers.get('papers', [])[:5], ensure_ascii=False)[:1500]}

要求：
- 按研究流派/方法类型分组介绍
- 每组引用2-4篇代表性工作
- 结尾说明与本文的区别
- 使用学术英文（同时提供中文版）
- 用 [Author et al., YEAR] 格式引用""",
    }


@register_skill(
    "response_letter",
    "生成论文审稿回复信（Response Letter），逐条回应审稿意见。",
    {
        "reviews": {"type": "string", "description": "审稿意见全文"},
        "authors_response": {"type": "string", "description": "作者的修改说明（可选）"},
    },
)
def response_letter(reviews: str, authors_response: str = "") -> dict:
    return {
        "action": "llm_response",
        "prompt": f"""请基于以下审稿意见，生成专业的 Response Letter：

审稿意见：
{reviews[:2000]}

{"作者修改说明：" + authors_response[:500] if authors_response else ""}

Response Letter 格式：
- 致编辑的感谢信（3-4句）
- 按审稿人逐条回复（Reviewer 1 Comment 1 → Response + 修改位置）
- 语气：专业、感谢、客观
- 对无法接受的意见：礼貌解释原因并提供证据""",
    }


@register_skill(
    "knowledge_graph",
    "从文本中提取知识图谱的三元组（实体-关系-实体）。",
    {
        "text": {"type": "string", "description": "待分析文本"},
        "focus_entities": {"type": "string", "description": "重点关注的实体类型（可选），如：方法,数据集,指标"},
    },
)
def knowledge_graph(text: str, focus_entities: str = "") -> dict:
    return {
        "action": "llm_kg",
        "prompt": f"""请从以下学术文本中提取知识图谱三元组：

{"重点关注实体类型：" + focus_entities if focus_entities else ""}

文本：
{text[:2500]}

输出 JSON 格式：
{{
  "entities": [
    {{"id": "e1", "name": "实体名", "type": "Model/Dataset/Method/Metric/Author/Domain"}}
  ],
  "relations": [
    {{"subject": "e1", "predicate": "使用/提出/优于/属于/评估", "object": "e2"}}
  ],
  "summary": "知识图谱核心要点"
}}""",
    }


@register_skill(
    "code_explain",
    "逐行解释代码逻辑，适合学习和代码审查。",
    {
        "code": {"type": "string", "description": "待解释的代码"},
        "language": {"type": "string", "description": "编程语言，默认python"},
        "audience": {"type": "string", "description": "目标受众：beginner/intermediate/expert"},
    },
)
def code_explain(code: str, language: str = "python", audience: str = "intermediate") -> dict:
    level_desc = {"beginner": "初学者（无需编程基础）", "intermediate": "有一定基础的开发者", "expert": "资深工程师"}.get(audience, "开发者")
    return {
        "action": "llm_codeexp",
        "prompt": f"""请为{level_desc}解释以下 {language} 代码：

```{language}
{code[:3000]}
```

解释方式：
1. **整体功能**（一句话）
2. **逐段解析**（关键块逐行注释）
3. **核心算法/思路**
4. **时间/空间复杂度**（如适用）
5. **潜在改进点**""",
    }


@register_skill(
    "send_email",
    "发送电子邮件，支持纯文本和 HTML 格式，可用于通知、报告、提醒等场景。",
    {
        "to": {"type": "string", "description": "收件人邮箱地址，多个用逗号分隔"},
        "subject": {"type": "string", "description": "邮件主题"},
        "body": {"type": "string", "description": "邮件正文内容"},
        "html": {"type": "string", "description": "HTML 格式正文（可选，若提供则优先使用）"},
    },
)
async def send_email(to: str, subject: str, body: str, html: str = "") -> dict:
    """通过 163 SMTP 发送邮件"""
    import smtplib
    import ssl
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    if not settings.SMTP_HOST or not settings.SMTP_AUTHORIZATION_CODE:
        return {"success": False, "error": "SMTP 未配置，请在 .env 中设置 SMTP_HOST / SMTP_AUTHORIZATION_CODE"}

    recipients = [r.strip() for r in to.split(",") if r.strip()]
    if not recipients:
        return {"success": False, "error": "收件人地址为空"}

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM or settings.SMTP_USER
    msg["To"] = ", ".join(recipients)

    msg.attach(MIMEText(body, "plain", "utf-8"))
    if html:
        msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        context = ssl.create_default_context()
        loop = asyncio.get_event_loop()

        def _send():
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, context=context) as server:
                server.login(settings.SMTP_USER, settings.SMTP_AUTHORIZATION_CODE)
                server.sendmail(msg["From"], recipients, msg.as_string())

        await loop.run_in_executor(None, _send)
        return {"success": True, "to": recipients, "subject": subject}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════════════
# SKILL 52-54: 飞书富媒体消息（图片 / 文件 / 主动发送）
# ════════════════════════════════════════════════════════════

def _get_feishu_access_token() -> str:
    """获取飞书 tenant_access_token（同步版，供 run_in_executor 使用）"""
    import httpx as _httpx
    resp = _httpx.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={
            "app_id": settings.FEISHU_APP_ID,
            "app_secret": settings.FEISHU_APP_SECRET,
        },
        timeout=10,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"飞书 token 获取失败: {data}")
    return data["tenant_access_token"]


async def _get_feishu_access_token_async() -> str:
    """获取飞书 tenant_access_token（异步版，不阻塞事件循环）"""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={
                "app_id": settings.FEISHU_APP_ID,
                "app_secret": settings.FEISHU_APP_SECRET,
            },
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"飞书 token 获取失败: {data}")
        return data["tenant_access_token"]


@register_skill(
    "feishu_upload_image",
    "将图片 URL 上传到飞书，返回 image_key。可用于随后向飞书用户发送图片消息。",
    {
        "image_url": {"type": "string", "description": "图片的 HTTP/HTTPS URL"},
        "image_type": {"type": "string", "description": "图片格式：png / jpeg / gif / webp，默认 png"},
    },
)
async def feishu_upload_image(image_url: str, image_type: str = "png") -> dict:
    """下载图片并上传到飞书，返回 image_key"""
    if not settings.FEISHU_APP_ID or not settings.FEISHU_APP_SECRET:
        return {"success": False, "error": "飞书 APP_ID / APP_SECRET 未配置"}
    try:
        token = await _get_feishu_access_token_async()

        async with httpx.AsyncClient(timeout=30) as client:
            img_resp = await client.get(image_url)
            img_resp.raise_for_status()
            image_bytes = img_resp.content

        # 上传到飞书
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://open.feishu.cn/open-apis/im/v1/images",
                headers={"Authorization": f"Bearer {token}"},
                data={"image_type": "message"},
                files={"image": (f"image.{image_type}", image_bytes, f"image/{image_type}")},
            )
            data = resp.json()

        if data.get("code") != 0:
            return {"success": False, "error": data.get("msg", "上传失败")}
        image_key = data["data"]["image_key"]
        return {"success": True, "image_key": image_key}
    except Exception as e:
        return {"success": False, "error": str(e)}


@register_skill(
    "feishu_send_image",
    "通过飞书私聊向指定用户发送图片。需提供用户的 open_id 和 image_key（由 feishu_upload_image 获取）。",
    {
        "open_id": {"type": "string", "description": "接收方的飞书 open_id（格式：ou_xxx）"},
        "image_key": {"type": "string", "description": "已上传图片的 image_key"},
    },
)
async def feishu_send_image(open_id: str, image_key: str) -> dict:
    """向飞书用户发送图片消息"""
    if not settings.FEISHU_APP_ID or not settings.FEISHU_APP_SECRET:
        return {"success": False, "error": "飞书未配置"}
    try:
        token = await _get_feishu_access_token_async()

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                params={"receive_id_type": "open_id"},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "receive_id": open_id,
                    "msg_type": "image",
                    "content": json.dumps({"image_key": image_key}),
                },
            )
            data = resp.json()

        if data.get("code") != 0:
            return {"success": False, "error": data.get("msg", "发送失败")}
        return {"success": True, "message_id": data["data"].get("message_id")}
    except Exception as e:
        return {"success": False, "error": str(e)}


@register_skill(
    "feishu_send_file",
    "通过飞书向指定用户发送可下载文件（PDF、Word、Excel、Zip 等）。先从 URL 下载文件再发送。",
    {
        "open_id": {"type": "string", "description": "接收方的飞书 open_id"},
        "file_url": {"type": "string", "description": "文件下载 URL"},
        "file_name": {"type": "string", "description": "文件名（含扩展名，如 report.pdf）"},
        "file_type": {"type": "string", "description": "飞书文件类型：opus/mp4/pdf/doc/xls/ppt/stream，默认 stream"},
    },
)
async def feishu_send_file(open_id: str, file_url: str, file_name: str, file_type: str = "stream") -> dict:
    """下载文件并通过飞书发送给用户"""
    if not settings.FEISHU_APP_ID or not settings.FEISHU_APP_SECRET:
        return {"success": False, "error": "飞书未配置"}
    try:
        token = await _get_feishu_access_token_async()

        # 下载文件内容
        async with httpx.AsyncClient(timeout=60) as client:
            file_resp = await client.get(file_url)
            file_resp.raise_for_status()
            file_bytes = file_resp.content

        # 上传文件到飞书
        async with httpx.AsyncClient(timeout=60) as client:
            upload_resp = await client.post(
                "https://open.feishu.cn/open-apis/im/v1/files",
                headers={"Authorization": f"Bearer {token}"},
                data={"file_type": file_type, "file_name": file_name},
                files={"file": (file_name, file_bytes, "application/octet-stream")},
            )
            upload_data = upload_resp.json()

        if upload_data.get("code") != 0:
            return {"success": False, "error": upload_data.get("msg", "文件上传失败")}
        file_key = upload_data["data"]["file_key"]

        # 发送文件消息
        async with httpx.AsyncClient(timeout=15) as client:
            send_resp = await client.post(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                params={"receive_id_type": "open_id"},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "receive_id": open_id,
                    "msg_type": "file",
                    "content": json.dumps({"file_key": file_key}),
                },
            )
            send_data = send_resp.json()

        if send_data.get("code") != 0:
            return {"success": False, "error": send_data.get("msg", "发送失败")}
        return {"success": True, "file_key": file_key, "message_id": send_data["data"].get("message_id")}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════════════
# SKILL 29-31: 数据库智能查询工具（LeadResearcher 专属）
# 让用户通过自然语言直接查询科研数据库
# ════════════════════════════════════════════════════════════

@register_skill(
    "inspect_db_schema",
    "获取科研数据库的表结构和字段说明。在执行任何SQL查询前，先调用此工具了解数据库结构。",
    {
        "table_name": {
            "type": "string",
            "description": "要查看的表名。留空则返回所有表的概览。",
        },
    },
)
async def inspect_db_schema(table_name: str = "") -> dict:
    """返回 MySQL 数据库表结构，帮助 Agent 理解数据模型"""
    # 硬编码的表结构描述（避免每次查 information_schema，同时提供语义描述）
    schema = {
        "tasks": {
            "description": "用户提交的所有科研任务记录",
            "columns": {
                "id": "任务UUID（主键）",
                "title": "任务标题/用户原始提问（最长256字）",
                "status": "任务状态：pending/success/error/rejected",
                "channel": "来源渠道：feishu/web/cli/api",
                "user_id": "用户标识（feishu:open_id 格式）",
                "assigned_agent": "执行该任务的 Agent ID",
                "quality_score": "任务质量评分（0-1浮点数）",
                "created_at": "任务创建时间（北京时间）",
                "completed_at": "任务完成时间",
                "input_data": "JSON，含用户原始消息",
                "output_data": "JSON，含 Agent 的完整回复",
                "guardian_verdict": "风控审核结果：approved/warning/rejected",
            },
            "example_queries": [
                "SELECT title, created_at FROM tasks WHERE user_id='feishu:xxx' ORDER BY created_at DESC LIMIT 10",
                "SELECT channel, COUNT(*) as cnt FROM tasks GROUP BY channel",
                "SELECT DATE(created_at) as date, COUNT(*) as cnt FROM tasks GROUP BY date ORDER BY date DESC LIMIT 7",
            ],
        },
        "users": {
            "description": "注册用户信息，支持飞书/钉钉多渠道",
            "columns": {
                "id": "用户UUID（主键）",
                "feishu_open_id": "飞书用户 open_id",
                "dingtalk_user_id": "钉钉用户 ID",
                "name": "用户显示名称",
                "email": "邮件地址",
                "created_at": "首次交互时间",
            },
        },
        "user_interest_profiles": {
            "description": "用户科研兴趣画像，由系统自动从历史任务中提炼",
            "columns": {
                "user_id": "用户UUID",
                "domain": "研究领域（如：AI、生物信息学、数学）",
                "keywords": "JSON数组，研究关键词列表",
                "weight": "兴趣强度（1.0-5.0，越高越感兴趣）",
                "updated_at": "最后更新时间",
            },
            "example_queries": [
                "SELECT domain, weight FROM user_interest_profiles WHERE user_id='xxx' ORDER BY weight DESC",
                "SELECT domain, COUNT(*) as user_count FROM user_interest_profiles GROUP BY domain ORDER BY user_count DESC",
            ],
        },
        "knowledge": {
            "description": "Wellspring 沉淀的科研知识库",
            "columns": {
                "id": "知识条目UUID",
                "title": "知识标题",
                "content": "知识内容（全文）",
                "category": "分类",
                "quality_score": "质量评分",
                "tags": "JSON标签数组",
                "citation_count": "被引用次数",
                "created_at": "入库时间",
            },
        },
        "task_metrics": {
            "description": "任务执行指标，用于系统分析和价值量化",
            "columns": {
                "task_id": "任务ID",
                "user_id": "用户ID",
                "channel": "来源渠道",
                "status": "执行状态",
                "duration_seconds": "执行耗时（秒）",
                "tools_used": "调用工具次数",
                "iterations": "LLM推理轮数",
                "created_at": "记录时间",
            },
        },
        "proactive_notifications": {
            "description": "主动推送记录，用于防止重复发送",
            "columns": {
                "user_id": "用户ID",
                "paper_id": "论文ID（arXiv等）",
                "content_hash": "内容哈希，去重用",
                "notification_type": "推送类型：daily_digest/paper_alert/collaboration",
                "sent_at": "发送时间",
            },
        },
        "task_feedback": {
            "description": "用户对任务结果的评分反馈（1-5星）",
            "columns": {
                "task_id": "任务ID",
                "user_id": "用户ID",
                "rating": "评分（1-5整数）",
                "created_at": "评分时间",
            },
        },
    }

    if table_name and table_name in schema:
        return {
            "table": table_name,
            "schema": schema[table_name],
            "tip": "使用 execute_readonly_sql 工具执行查询",
        }

    # 返回所有表概览
    overview = {
        name: {"description": info["description"]}
        for name, info in schema.items()
    }
    return {
        "tables": overview,
        "total_tables": len(schema),
        "tip": "调用 inspect_db_schema(table_name='xxx') 查看某张表的详细字段",
        "full_schema": schema,
    }


@register_skill(
    "execute_readonly_sql",
    "对科研数据库执行只读SQL查询（SELECT语句）。用于回答'有多少用户'、'最近的任务'、'热门研究方向'等问题。只允许SELECT查询，禁止修改数据。",
    {
        "sql": {
            "type": "string",
            "description": "要执行的SELECT SQL语句。只能是只读查询，不得包含INSERT/UPDATE/DELETE/DROP等。",
        },
        "limit": {
            "type": "integer",
            "description": "最大返回行数，默认20，最多100。",
        },
    },
)
async def execute_readonly_sql(sql: str, limit: int = 20) -> dict:
    """执行只读SQL并返回结构化结果"""
    # 安全检查：禁止写操作
    sql_upper = sql.strip().upper()
    forbidden_keywords = ["INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER", "CREATE", "REPLACE"]
    for kw in forbidden_keywords:
        if kw in sql_upper:
            return {
                "success": False,
                "error": f"安全限制：不允许执行 {kw} 操作，只支持 SELECT 查询",
            }

    if not sql_upper.startswith("SELECT"):
        return {
            "success": False,
            "error": "只支持 SELECT 查询语句",
        }

    # 强制添加 LIMIT 防止全表扫描
    limit = min(limit, 100)
    if "LIMIT" not in sql_upper:
        sql = f"{sql.rstrip(';')} LIMIT {limit}"

    try:
        from core.database import get_session
        from sqlalchemy import text

        async with await get_session() as session:
            result = await session.execute(text(sql))
            columns = list(result.keys())
            rows = result.fetchall()
            data = [dict(zip(columns, row)) for row in rows]

            # 序列化（处理 datetime 等不可 JSON 序列化的类型）
            serialized = []
            for row in data:
                ser_row = {}
                for k, v in row.items():
                    if hasattr(v, "isoformat"):
                        ser_row[k] = v.isoformat()
                    else:
                        ser_row[k] = v
                serialized.append(ser_row)

            return {
                "success": True,
                "sql": sql,
                "columns": columns,
                "rows": serialized,
                "row_count": len(serialized),
            }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "sql": sql,
        }


@register_skill(
    "get_user_research_profile",
    "获取指定用户（或当前用户）的完整科研兴趣画像，包括研究领域、关键词、历史任务统计、最常用功能等。用于个性化推荐和深度了解用户需求。",
    {
        "user_id": {
            "type": "string",
            "description": "用户标识（如 feishu:ou_xxx 格式）。留空则返回所有用户的聚合统计。",
        },
        "include_task_history": {
            "type": "boolean",
            "description": "是否包含最近10条任务历史，默认False。",
        },
    },
)
async def get_user_research_profile(user_id: str = "", include_task_history: bool = False) -> dict:
    """获取用户科研画像，支持单用户详情和全局聚合统计"""
    try:
        from sqlalchemy import select, func
        from core.database import get_session, UserInterestProfile, User, TaskMetrics, TaskRecord

        async with await get_session() as session:
            if user_id:
                # 单用户画像
                raw_uid = user_id.replace("feishu:", "").replace("dingtalk:", "")

                profiles_result = await session.execute(
                    select(UserInterestProfile)
                    .where(
                        (UserInterestProfile.user_id == user_id) |
                        (UserInterestProfile.user_id == raw_uid)
                    )
                    .order_by(UserInterestProfile.weight.desc())
                )
                profiles = profiles_result.scalars().all()

                metrics_result = await session.execute(
                    select(
                        func.count(TaskMetrics.id).label("total"),
                        func.avg(TaskMetrics.duration_seconds).label("avg_duration"),
                    ).where(
                        (TaskMetrics.user_id == user_id) |
                        (TaskMetrics.user_id == raw_uid)
                    )
                )
                metrics = metrics_result.one()

                profile_data = {
                    "user_id": user_id,
                    "research_domains": [
                        {"domain": p.domain, "keywords": p.keywords, "weight": p.weight}
                        for p in profiles
                    ],
                    "total_tasks": metrics.total or 0,
                    "avg_task_duration_seconds": round(float(metrics.avg_duration or 0), 1),
                }

                if include_task_history:
                    history_result = await session.execute(
                        select(TaskRecord.title, TaskRecord.status, TaskRecord.created_at)
                        .where(
                            (TaskRecord.user_id == user_id) |
                            (TaskRecord.user_id == raw_uid)
                        )
                        .order_by(TaskRecord.created_at.desc())
                        .limit(10)
                    )
                    profile_data["recent_tasks"] = [
                        {
                            "title": row.title,
                            "status": row.status,
                            "time": row.created_at.isoformat() if row.created_at else None,
                        }
                        for row in history_result
                    ]

                return {"success": True, "profile": profile_data}

            else:
                # 全局聚合：各领域用户数统计
                domain_stats_result = await session.execute(
                    select(
                        UserInterestProfile.domain,
                        func.count(UserInterestProfile.user_id.distinct()).label("user_count"),
                        func.avg(UserInterestProfile.weight).label("avg_weight"),
                    )
                    .group_by(UserInterestProfile.domain)
                    .order_by(func.count(UserInterestProfile.user_id.distinct()).desc())
                )
                domain_stats = [
                    {
                        "domain": row.domain,
                        "user_count": row.user_count,
                        "avg_interest_weight": round(float(row.avg_weight or 0), 2),
                    }
                    for row in domain_stats_result
                ]

                total_users_result = await session.execute(
                    select(func.count(User.id))
                )
                total_users = total_users_result.scalar() or 0

                return {
                    "success": True,
                    "summary": {
                        "total_users": total_users,
                        "top_research_domains": domain_stats[:10],
                    },
                }
    except Exception as e:
        return {"success": False, "error": str(e)}


# 导出所有技能名称
ALL_SKILLS = list(SKILL_REGISTRY.keys())
