"""
OpenClaw 流式任务执行路由（SSE）
支持工作流步骤进度事件，提供逐步反馈
"""
import asyncio
import json
from typing import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter()

# ─── 工作流步骤定义（前端用于渲染进度条）────────────────────────────────────
WORKFLOW_STEPS = {
    "wf-literature-review": [
        {"id": "vanguard_scan",   "name": "Vanguard 扫描前沿", "agent": "vanguard-01"},
        {"id": "survey_read",     "name": "Survey 系统精读",   "agent": "clawer-survey-01"},
        {"id": "critical_review", "name": "Review 批判评审",   "agent": "clawer-review-01"},
        {"id": "writing_polish",  "name": "Writing 整理报告",  "agent": "clawer-writing-01"},
    ],
    "wf-research-design": [
        {"id": "parallel_bg",     "name": "文献调研 + 实验设计（并行）", "agent": "clawer-survey-01"},
        {"id": "data_analysis",   "name": "数据可行性分析",              "agent": "clawer-data-01"},
        {"id": "integration",     "name": "整合研究方案",                "agent": "clawer-writing-01"},
    ],
    "wf-frontier-discovery": [
        {"id": "vanguard_deep",   "name": "Vanguard 深度探索",  "agent": "vanguard-01"},
        {"id": "tech_analysis",   "name": "AI/CS 技术深析",     "agent": "clawer-cs-02"},
    ],
    "wf-data-analysis": [
        {"id": "data_process",    "name": "数据 Clawer 处理",   "agent": "clawer-data-01"},
        {"id": "stats",           "name": "统计检验",            "agent": "clawer-data-01"},
    ],
    "wf-paper-writing": [
        {"id": "outline",         "name": "生成提纲",            "agent": "clawer-writing-01"},
        {"id": "writing",         "name": "Writing 撰写",       "agent": "clawer-writing-01"},
        {"id": "review",          "name": "Review 校审",        "agent": "clawer-review-01"},
    ],
    "wf-code-task": [
        {"id": "code_impl",       "name": "CS Clawer 实现",     "agent": "clawer-cs-01"},
        {"id": "code_review",     "name": "Review 代码审查",    "agent": "clawer-review-01"},
    ],
    "wf-content-promotion": [
        {"id": "content_gen",     "name": "Promoter 内容生成",  "agent": "promoter-01"},
        {"id": "guardian_check",  "name": "Guardian 合规审核",  "agent": "guardian-01"},
    ],
}


class StreamRequest(BaseModel):
    task: str
    agent_id: str | None = None   # None = 智能路由
    user_id: str = "anonymous"


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream_agent(
    task: str,
    agent_id: str | None = None,
    user_id: str = "anonymous",
) -> AsyncGenerator[str, None]:
    """SSE 事件生成器：路由 → Guardian → 执行（含工作流步骤） → 输出审核"""

    try:
        from core.registry import registry
        from core.orchestrator import Orchestrator
        from core.router import TaskRouter

        if not registry._initialized:
            registry.initialize()

        yield _sse("start", {"message": "任务已接收，正在路由..."})
        await asyncio.sleep(0.05)

        # ── 路由决策 ──────────────────────────────────────────────────────
        router_inst = TaskRouter(registry)
        route = router_inst.route(task)
        primary = registry.get(route["primary_agent"])
        agent_name = primary.name if primary else route["primary_agent"]
        exec_mode = route["execution_mode"]
        workflow_id = route.get("workflow_id") or ""

        yield _sse("routing", {
            "agent_id": route["primary_agent"],
            "agent_name": agent_name,
            "mode": exec_mode,
            "workflow_id": workflow_id,
        })
        await asyncio.sleep(0.05)

        # 工作流时先告知前端步骤列表（用于渲染进度条）
        steps = WORKFLOW_STEPS.get(workflow_id, [])
        if steps:
            yield _sse("workflow_start", {
                "workflow_id": workflow_id,
                "steps": steps,
                "total": len(steps),
            })
            await asyncio.sleep(0.05)

        # ── Guardian 输入审核 ─────────────────────────────────────────────
        guardian = registry.get_guardian()
        if guardian:
            yield _sse("guardian_input", {"message": "Guardian 正在审核输入..."})
            verdict = await guardian.review_input(task, user_id)
            if verdict.get("verdict") == "rejected":
                yield _sse("rejected", verdict)
                yield _sse("done", {"status": "rejected"})
                return
            yield _sse("guardian_passed", {
                "verdict": verdict.get("verdict"),
                "risk_score": verdict.get("risk_score", 0),
            })
            await asyncio.sleep(0.05)

        # ── 执行 ──────────────────────────────────────────────────────────
        final_text = ""
        result = {}

        if steps and exec_mode == "workflow":
            # 工作流：逐步 yield 进度事件，通过 _wf_steps_generator 执行
            yield _sse("executing", {"agent_name": agent_name, "message": "工作流开始执行..."})
            async for ev_str in _wf_steps_generator(task, route, registry, workflow_id):
                if ev_str.startswith("event: workflow_result"):
                    try:
                        data_part = ev_str.split("data:", 1)[1].strip()
                        final_text = json.loads(data_part).get("result", "")
                    except Exception:
                        pass
                else:
                    yield ev_str
            result = {"status": "success", "result": final_text}
        else:
            yield _sse("executing", {"agent_name": agent_name, "message": f"{agent_name} 正在处理..."})
            if agent_id:
                agent = registry.get(agent_id)
                result = await agent.run(task) if agent else {"status": "error", "error": "Agent 未找到"}
            else:
                orch = Orchestrator(registry)
                result = await orch.execute(task, user_id=user_id)
            final_text = result.get("result", "")

        # ── 流式输出结果 ───────────────────────────────────────────────────
        if final_text:
            paragraphs = [p for p in final_text.split("\n\n") if p.strip()]
            for para in paragraphs:
                yield _sse("chunk", {"text": para + "\n\n"})
                await asyncio.sleep(0.03)

        # ── Guardian 输出审核 ─────────────────────────────────────────────
        if guardian and final_text:
            yield _sse("guardian_output", {"message": "Guardian 正在审核输出..."})
            out_verdict = await guardian.review_output(final_text[:2000])
            result["guardian_verdict"] = out_verdict.get("verdict")

        yield _sse("complete", {
            "status": result.get("status", "success"),
            "agent_name": result.get("agent_name", agent_name),
            "guardian_verdict": result.get("guardian_verdict", "approved"),
            "iterations": result.get("iterations", 0),
            "workflow_id": workflow_id,
        })

    except Exception as e:
        yield _sse("error", {"message": str(e)})
    finally:
        yield _sse("done", {"status": "finished"})


# ─── 工作流步骤 generator（逐步 yield SSE 事件）──────────────────────────────
async def _wf_steps_generator(task, route, registry, workflow_id):
    """逐步 yield SSE 事件字符串的工作流执行器，直接调用各 Agent"""

    results = {}

    async def step_event(step_id, step_name, status, step_index, preview=""):
        return _sse("workflow_step", {
            "step_id": step_id,
            "name": step_name,
            "status": status,         # "running" | "done" | "error"
            "step_index": step_index,
            "preview": preview[:150] if preview else "",
        })

    if "research-design" in workflow_id:
        # Step 0: 并行文献调研 + 实验设计
        yield await step_event("parallel_bg", "文献调研 + 实验设计（并行）", "running", 0)
        surveyor = registry.get("clawer-survey-01")
        experimenter = registry.get("clawer-experiment-01")
        tasks = []
        if surveyor:
            tasks.append(surveyor.run(f"为以下研究问题进行背景文献调研：{task}"))
        if experimenter:
            tasks.append(experimenter.run(f"为以下研究问题设计实验方案：{task}"))
        parallel = await asyncio.gather(*tasks, return_exceptions=True)
        if surveyor:
            results["background"] = (parallel[0] if not isinstance(parallel[0], Exception) else {}).get("result", "")
        if experimenter:
            idx = 1 if surveyor else 0
            results["design"] = (parallel[idx] if not isinstance(parallel[idx], Exception) else {}).get("result", "")
        preview = results.get("background", "")[:150] or results.get("design", "")[:150]
        yield await step_event("parallel_bg", "文献调研 + 实验设计（并行）", "done", 0, preview)

        # Step 1: 数据可行性
        yield await step_event("data_analysis", "数据可行性分析", "running", 1)
        data_agent = registry.get("clawer-data-01")
        if data_agent:
            r = await data_agent.run(
                f"分析以下研究方案的数据收集和分析可行性：\n{task}\n背景：{results.get('background','')[:400]}"
            )
            results["data_plan"] = r.get("result", "")
        yield await step_event("data_analysis", "数据可行性分析", "done", 1, results.get("data_plan", ""))

        # Step 2: 整合
        yield await step_event("integration", "整合研究方案", "running", 2)
        combined = "\n\n".join(f"## {k}\n{v}" for k, v in results.items() if v)
        final = f"# 研究方案设计报告\n\n{combined}"
        yield await step_event("integration", "整合研究方案", "done", 2, "方案整合完成")
        yield _sse("workflow_result", {"result": final})

    elif "literature-review" in workflow_id:
        # Step 0: Vanguard 扫描
        yield await step_event("vanguard_scan", "Vanguard 扫描前沿", "running", 0)
        vanguard = registry.get_vanguard()
        if vanguard:
            r = await vanguard.run(f"请快速扫描与以下主题相关的最新论文（近3个月）：{task}")
            results["vanguard"] = r.get("result", "")
        yield await step_event("vanguard_scan", "Vanguard 扫描前沿", "done", 0, results.get("vanguard", ""))

        # Step 1: Survey 精读
        yield await step_event("survey_read", "Survey 系统精读", "running", 1)
        surveyor = registry.get("clawer-survey-01")
        if surveyor:
            r = await surveyor.run(f"请完成系统性文献综述：{task}", context={"vanguard_findings": results.get("vanguard", "")})
            results["survey"] = r.get("result", "")
        yield await step_event("survey_read", "Survey 系统精读", "done", 1, results.get("survey", ""))

        # Step 2: Review 评审
        yield await step_event("critical_review", "Review 批判评审", "running", 2)
        reviewer = registry.get("clawer-review-01")
        if reviewer and results.get("survey"):
            r = await reviewer.run(f"请对以下综述进行批判评审：\n\n{results['survey'][:2000]}")
            results["review"] = r.get("result", "")
        yield await step_event("critical_review", "Review 批判评审", "done", 2, results.get("review", ""))

        # Step 3: Writing 整理
        yield await step_event("writing_polish", "Writing 整理报告", "running", 3)
        writer = registry.get("clawer-writing-01")
        if writer:
            all_content = "\n\n".join(v for v in results.values() if v)
            r = await writer.run(f"请整理为结构化文献综述报告：\n\n{all_content[:3000]}")
            results["writing"] = r.get("result", all_content)
        yield await step_event("writing_polish", "Writing 整理报告", "done", 3, "报告整理完成")
        yield _sse("workflow_result", {"result": results.get("writing", "\n\n".join(results.values()))})

    elif "frontier-discovery" in workflow_id:
        yield await step_event("vanguard_deep", "Vanguard 深度探索", "running", 0)
        vanguard = registry.get_vanguard()
        if vanguard:
            r = await vanguard.run(f"请对以下方向进行深度前沿探索：{task}")
            results["frontier"] = r.get("result", "")
        yield await step_event("vanguard_deep", "Vanguard 深度探索", "done", 0, results.get("frontier", ""))

        yield await step_event("tech_analysis", "AI/CS 技术深析", "running", 1)
        cs_agent = registry.get("clawer-cs-02")
        if cs_agent and results.get("frontier"):
            r = await cs_agent.run(f"请对以下前沿发现进行深度技术分析：\n\n{results['frontier'][:2000]}")
            results["analysis"] = r.get("result", "")
        yield await step_event("tech_analysis", "AI/CS 技术深析", "done", 1, results.get("analysis", ""))
        final = "\n\n---\n\n".join(v for v in results.values() if v)
        yield _sse("workflow_result", {"result": final})

    else:
        # 通用工作流：单 Agent 执行
        lead = registry.get(route["primary_agent"])
        if lead:
            r = await lead.run(task)
            results["result"] = r.get("result", "")
        yield _sse("workflow_result", {"result": results.get("result", "")})


@router.post("/stream")
async def stream_task(req: StreamRequest):
    """流式执行任务（SSE）"""

    async def _gen():
        try:
            from core.registry import registry
            from core.orchestrator import Orchestrator
            from core.router import TaskRouter

            if not registry._initialized:
                registry.initialize()

            yield _sse("start", {"message": "任务已接收，正在路由..."})
            await asyncio.sleep(0.05)

            router_inst = TaskRouter(registry)
            route = router_inst.route(req.task)
            primary = registry.get(route["primary_agent"])
            agent_name = primary.name if primary else route["primary_agent"]
            exec_mode = route["execution_mode"]
            workflow_id = route.get("workflow_id") or ""

            yield _sse("routing", {
                "agent_id": route["primary_agent"],
                "agent_name": agent_name,
                "mode": exec_mode,
                "workflow_id": workflow_id,
            })
            await asyncio.sleep(0.05)

            steps = WORKFLOW_STEPS.get(workflow_id, [])
            if steps:
                yield _sse("workflow_start", {
                    "workflow_id": workflow_id,
                    "steps": steps,
                    "total": len(steps),
                })
                await asyncio.sleep(0.05)

            # Guardian 输入审核
            guardian = registry.get_guardian()
            if guardian:
                yield _sse("guardian_input", {"message": "Guardian 正在审核输入..."})
                verdict = await guardian.review_input(req.task, req.user_id)
                if verdict.get("verdict") == "rejected":
                    yield _sse("rejected", verdict)
                    yield _sse("done", {"status": "rejected"})
                    return
                yield _sse("guardian_passed", {
                    "verdict": verdict.get("verdict"),
                    "risk_score": verdict.get("risk_score", 0),
                })
                await asyncio.sleep(0.05)

            # 执行
            final_text = ""
            result = {}

            if steps and exec_mode == "workflow":
                yield _sse("executing", {"agent_name": agent_name, "message": "工作流开始执行..."})
                # 逐步 yield 工作流步骤事件
                async for ev_str in _wf_steps_generator(req.task, route, registry, workflow_id):
                    if ev_str.startswith("event: workflow_result"):
                        # 提取 result 文本
                        import re
                        m = re.search(r'"result":\s*"(.*?)"(?:,|})', ev_str, re.DOTALL)
                        if m:
                            # 用 json 解析更可靠
                            try:
                                data_part = ev_str.split("data:", 1)[1].strip()
                                final_text = json.loads(data_part).get("result", "")
                            except Exception:
                                pass
                    else:
                        yield ev_str
                result = {"status": "success", "result": final_text}
            else:
                yield _sse("executing", {"agent_name": agent_name, "message": f"{agent_name} 正在处理..."})
                if req.agent_id:
                    agent = registry.get(req.agent_id)
                    result = await agent.run(req.task) if agent else {"status": "error", "error": "Agent 未找到"}
                else:
                    orch = Orchestrator(registry)
                    result = await orch.execute(req.task, user_id=req.user_id)
                final_text = result.get("result", "")

            # 流式输出结果
            if final_text:
                paragraphs = [p for p in final_text.split("\n\n") if p.strip()]
                for para in paragraphs:
                    yield _sse("chunk", {"text": para + "\n\n"})
                    await asyncio.sleep(0.03)

            # Guardian 输出审核
            if guardian and final_text:
                yield _sse("guardian_output", {"message": "Guardian 正在审核输出..."})
                out_verdict = await guardian.review_output(final_text[:2000])
                result["guardian_verdict"] = out_verdict.get("verdict")

            # P2: 生成追问建议
            follow_ups = []
            if final_text and result.get("status") == "success":
                try:
                    from core.follow_up import generate_follow_ups
                    follow_ups = await generate_follow_ups(req.task, final_text)
                except Exception:
                    pass

            yield _sse("complete", {
                "status": result.get("status", "success"),
                "agent_name": result.get("agent_name", agent_name),
                "guardian_verdict": result.get("guardian_verdict", "approved"),
                "iterations": result.get("iterations", 0),
                "workflow_id": workflow_id,
                "follow_up_suggestions": follow_ups,
            })

        except Exception as e:
            yield _sse("error", {"message": str(e)})
        finally:
            yield _sse("done", {"status": "finished"})

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
