---
name: omniscientist-research
description: "全知科研小龙虾 — 深度科研助手。通过 LeadResearcher（用户画像驱动）处理所有科研任务。支持：文献综述、论文搜索(arXiv/Semantic Scholar)、实验设计、数据分析、学术写作、同行评审、趋势分析、科研数据库查询（自然语言转SQL）。系统会自动记住用户研究方向，无需重复介绍背景。"
metadata:
  {
        "openclaw":
      {
        "emoji": "🦞",
        "requires": { "bins": ["curl", "python3"] },
        "homepage": "http://127.0.0.1:10101",
      },
  }
---

# Omniscientist Research — 全知科研小龙虾

科研专属 AI 助手，由 **LeadResearcher**（画像驱动）统一处理所有请求。

## 核心优势

- **记住你的研究方向**：无需每次重新介绍，系统自动累积用户科研兴趣画像
- **数据库直查**：可用自然语言查询系统中的科研数据（任务历史、热门领域等）
- **并行工具调用**：同时搜索多个来源，速度比串行快 3-5 倍
- **全流程覆盖**：从文献调研到论文写作，28+ 专业科研工具

## 使用方法

```bash
# 基础调用（自动检测用户画像）
$SKILLS_DIR/omniscientist-research/scripts/research.sh "任务描述"

# 带用户上下文（飞书消息必须传入，实现个性化）
$SKILLS_DIR/omniscientist-research/scripts/research.sh \
  "任务描述" \
  --user "feishu:{sender_open_id}" \
  --channel "feishu"

# 数据库查询（直接自然语言）
$SKILLS_DIR/omniscientist-research/scripts/research.sh \
  "最近一周有多少用户提交了科研任务？主要是哪些领域？" \
  --user "feishu:{sender_open_id}"
```

## 适用场景

✅ **使用此 skill：**
- 文献综述、论文检索（arXiv / Semantic Scholar）
- 实验设计、方法论评估
- 数据分析、统计结果解读
- 学术写作、摘要生成、语言润色
- 同行评审、研究空白分析
- 领域趋势分析、前沿发现
- 查询科研数据库（"最近用户在研究什么"）
- 假设生成、研究方案设计

❌ **不适用：**
- 简单事实问题（直接回答）
- 非科研任务（使用其他 skill）
- 实时股票/天气（使用专用 skill）

## 重要提示

- **飞书场景必须传 `--user`**：这样 LeadResearcher 才能加载用户画像，实现个性化
- 超时默认 300 秒，复杂综述任务可用 `--timeout 600`
- 结果会自动沉淀到知识库，越用越聪明

## 返回格式

```
[LeadResearcher] · N 次迭代 · task_id: xxx

{完整的科研分析内容，Markdown 格式，飞书卡片渲染友好}
```
