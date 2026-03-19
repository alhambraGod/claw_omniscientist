"""
OpenClaw Maintainer - 维护者智能体
负责系统健康监控、故障诊断、自动修复、告警管理
"""
import asyncio
import json
import time
from config.settings import now
from agents.base import BaseAgent

MAINTAINER_SYSTEM = """你是 OpenClaw 的 **Maintainer（维护者）**，系统运行稳定性中枢。

## 核心职责
- 监控所有 Agent 的运行状态
- 诊断任务失败原因
- 提供修复建议与自动化处理
- 分析系统性能趋势
- 维护运维知识库

## 诊断原则
1. **数据驱动**：基于监控指标做判断
2. **根因分析**：不只报告症状，要找根本原因
3. **最小干预**：优先用最小代价的修复方案
4. **预防为主**：主动发现潜在问题

## 告警级别
- P0（Critical）：服务完全中断
- P1（High）：核心功能受损
- P2（Medium）：性能下降
- P3（Low）：需要关注但不紧急

你的诊断报告将进入 Wellspring，形成运维最佳实践。"""


class MaintainerAgent(BaseAgent):
    """Maintainer 维护者"""

    def __init__(self):
        super().__init__(
            agent_id="maintainer-01",
            name="维护者 Maintainer",
            role="maintainer",
            system_prompt=MAINTAINER_SYSTEM,
            tools=["code_execute", "web_search", "report_generate"],
        )
        self._metrics: list[dict] = []
        self._alerts: list[dict] = []

    def collect_system_metrics(self) -> dict:
        """采集系统指标"""
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=0.5)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            metrics = {
                "timestamp": now().isoformat(),
                "cpu_percent": cpu,
                "memory_percent": mem.percent,
                "memory_used_gb": round(mem.used / 1e9, 2),
                "memory_total_gb": round(mem.total / 1e9, 2),
                "disk_percent": disk.percent,
                "disk_free_gb": round(disk.free / 1e9, 2),
            }
        except ImportError:
            metrics = {
                "timestamp": now().isoformat(),
                "note": "psutil 未安装，无法采集系统指标",
            }
        self._metrics.append(metrics)
        if len(self._metrics) > 1000:
            self._metrics = self._metrics[-1000:]
        return metrics

    def check_agent_health(self, agent_registry: dict) -> dict:
        """检查所有 Agent 健康状态"""
        health = {}
        for agent_id, agent in agent_registry.items():
            status = "healthy" if getattr(agent, "client", None) else "degraded"
            health[agent_id] = {
                "agent_id": agent_id,
                "name": getattr(agent, "name", agent_id),
                "role": getattr(agent, "role", "unknown"),
                "status": status,
                "model": getattr(agent, "model", "unknown"),
                "checked_at": now().isoformat(),
            }
        return health

    def add_alert(self, level: str, message: str, agent_id: str = "") -> dict:
        alert = {
            "id": f"alert-{int(time.time())}",
            "level": level,
            "message": message,
            "agent_id": agent_id,
            "timestamp": now().isoformat(),
            "resolved": False,
        }
        self._alerts.append(alert)
        if len(self._alerts) > 500:
            self._alerts = self._alerts[-500:]
        return alert

    def get_recent_alerts(self, limit: int = 20) -> list[dict]:
        return self._alerts[-limit:]

    async def diagnose_failure(self, task_id: str, error: str, context: dict = None) -> dict:
        """诊断任务失败"""
        task = f"""请分析以下任务失败原因并提供修复建议：

任务 ID：{task_id}
错误信息：{error}
上下文：{json.dumps(context or {}, ensure_ascii=False)}

请输出：
1. 根本原因分析
2. 短期修复方案（立即可执行）
3. 长期预防措施
4. 相似问题的预防建议
5. 是否需要人工介入"""
        return await self.run(task)

    async def generate_health_report(self, metrics: dict, agent_health: dict) -> dict:
        """生成系统健康报告"""
        task = f"""请根据以下监控数据生成简洁的系统健康日报：

**系统指标**：
{json.dumps(metrics, ensure_ascii=False, indent=2)}

**Agent 健康状态**：
{json.dumps(agent_health, ensure_ascii=False, indent=2)}

输出结构：
## 系统状态评级（绿色/黄色/红色）
## 关键指标摘要
## 异常项与建议（如有）
## 明日关注点"""
        return await self.run(task)

    async def update_patterns(
        self,
        rejected_samples: list[dict],
        escalated_samples: list[dict],
    ) -> dict:
        """基于近期 Guardian 审查记录更新风险模式认知"""
        task = f"""请分析近一周的内容审查记录，提炼风险模式：

**被拒绝的请求样本**（{len(rejected_samples)} 条）：
{json.dumps(rejected_samples[:3], ensure_ascii=False, indent=2)}

**被升级处理的请求样本**（{len(escalated_samples)} 条）：
{json.dumps(escalated_samples[:3], ensure_ascii=False, indent=2)}

请输出：
1. 主要风险类型归纳（Top 3）
2. 常见触发词/场景
3. 是否有误判情况
4. Guardian 策略优化建议
5. 需要新增的白名单/豁免规则"""
        return await self.run(task)

    def get_metrics_summary(self, last_n: int = 10) -> dict:
        """获取最近 N 条指标的统计摘要"""
        recent = self._metrics[-last_n:]
        if not recent:
            return {}
        cpus = [m.get("cpu_percent", 0) for m in recent if "cpu_percent" in m]
        mems = [m.get("memory_percent", 0) for m in recent if "memory_percent" in m]
        return {
            "samples": len(recent),
            "cpu_avg": round(sum(cpus) / len(cpus), 1) if cpus else 0,
            "cpu_max": max(cpus) if cpus else 0,
            "mem_avg": round(sum(mems) / len(mems), 1) if mems else 0,
            "mem_max": max(mems) if mems else 0,
            "last_check": recent[-1].get("timestamp", "") if recent else "",
        }

    async def watch_running_tasks(self) -> dict:
        """
        任务超时看门狗 — Maintainer 子职能。

        职责：
        - 扫描所有正在执行的任务
        - 对超过 TASK_TIMEOUT + GRACE_PERIOD 仍无结果的任务强制标记超时
        - 存入 Redis 结果（让轮询客户端感知）
        - 主动通知用户（飞书 / IM / webchat 轮询均可感知）
        - 写入系统告警供 Dashboard 展示

        返回：本次检查结果摘要 {checked, killed, alerts}
        """
        from core.task_watchdog import _check_once
        from config.settings import settings
        import core.cache as cache_store
        import core.notifier as notifier

        # 复用 task_watchdog 的单次检查逻辑
        class _FakeSettings:
            TASK_TIMEOUT = settings.TASK_TIMEOUT

        try:
            await _check_once(_FakeSettings(), cache_store, notifier)
            running = await cache_store.get_running_tasks()
            return {
                "status": "ok",
                "running_tasks": len(running),
                "checked_at": now().isoformat(),
            }
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"[Maintainer] watch_running_tasks 异常: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}
