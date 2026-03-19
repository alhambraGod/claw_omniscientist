"""
OpenClaw CLI - 命令行控制台
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import json
import typer
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown
from rich.prompt import Prompt, Confirm
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.syntax import Syntax
from rich import print as rprint

app = typer.Typer(
    name="openclaw",
    help="🦞 OpenClaw 科研智能体社区 CLI",
    rich_markup_mode="rich",
    no_args_is_help=True,
)
console = Console()

LOGO = """
[bold red]
  ██████╗ ██████╗ ███████╗███╗   ██╗ ██████╗██╗      █████╗ ██╗    ██╗
 ██╔═══██╗██╔══██╗██╔════╝████╗  ██║██╔════╝██║     ██╔══██╗██║    ██║
 ██║   ██║██████╔╝█████╗  ██╔██╗ ██║██║     ██║     ███████║██║ █╗ ██║
 ██║   ██║██╔═══╝ ██╔══╝  ██║╚██╗██║██║     ██║     ██╔══██║██║███╗██║
 ╚██████╔╝██║     ███████╗██║ ╚████║╚██████╗███████╗██║  ██║╚███╔███╔╝
  ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═══╝ ╚═════╝╚══════╝╚═╝  ╚═╝ ╚══╝╚══╝ 🦞
[/bold red]
"""


def get_registry_and_orch():
    """获取注册中心和编排器"""
    from core.registry import registry
    from core.orchestrator import Orchestrator
    if not registry._initialized:
        registry.initialize()
    orch = Orchestrator(registry)
    return registry, orch


# ─── 命令：run ────────────────────────────────────────────
@app.command()
def run(
    task: str = typer.Argument(..., help="科研任务描述"),
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="指定 Agent ID"),
    multi: bool = typer.Option(False, "--multi", "-m", help="使用多 Agent 工作流"),
    user: str = typer.Option("cli-user", "--user", "-u", help="用户 ID"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON 格式"),
):
    """🚀 执行科研任务"""
    console.print(Panel(f"[bold]任务:[/bold] {task}", title="[red]🦞 OpenClaw[/red]", border_style="red"))

    async def _run():
        registry, orch = get_registry_and_orch()
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            ptask = progress.add_task("[cyan]🦞 OpenClaw 正在工作...", total=None)

            if agent:
                ag = registry.get(agent)
                if not ag:
                    console.print(f"[red]❌ Agent {agent} 未找到[/red]")
                    return
                progress.update(ptask, description=f"[cyan]{ag.name} 执行中...")
                result = await ag.run(task)
            elif multi:
                progress.update(ptask, description="[cyan]多 Agent 工作流执行中...")
                from core.router import TaskRouter
                router = TaskRouter(registry)
                route = router.route(task)
                route["execution_mode"] = "workflow"
                result = await orch._run_workflow(task, route)
                result["task_id"] = route["task_id"]
            else:
                result = await orch.execute(task, user_id=user)

        return result

    result = asyncio.run(_run())

    if json_output:
        console.print_json(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        status = result.get("status", "unknown")
        if status == "success":
            console.print(f"\n[green]✅ 执行成功[/green] | Agent: [cyan]{result.get('agent_name', result.get('agent_id', '?'))}[/cyan]")
            console.print(Markdown(result.get("result", "无输出")))
        elif status == "rejected":
            console.print(Panel(
                f"[bold red]任务被 Guardian 拒绝[/bold red]\n\n原因: {', '.join(result.get('issues', []))}\n\n建议: {result.get('recommendation', '')}",
                title="⛔ Guardian 拦截", border_style="red",
            ))
        elif status == "escalated":
            console.print(Panel(f"任务已升级人工审核\n{result.get('recommendation', '')}", title="⚠️ 需要人工", border_style="yellow"))
        else:
            console.print(f"[red]❌ 错误: {result.get('error', '未知错误')}[/red]")


# ─── 命令：ask ────────────────────────────────────────────
@app.command()
def ask(
    question: str = typer.Argument(..., help="科研问题"),
    domain: str = typer.Option("general", "--domain", "-d", help="研究领域"),
):
    """💬 快速问答（自动选择最合适的 Clawer）"""
    async def _ask():
        registry, orch = get_registry_and_orch()
        best = registry.get_any_worker()
        if not best:
            console.print("[red]❌ 无可用 Clawer[/red]")
            return None
        console.print(f"[dim]→ 分配给: {best.name}[/dim]")
        with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"), console=console) as p:
            p.add_task(f"{best.name} 思考中...", total=None)
            result = await best.run(question)
        return result

    result = asyncio.run(_ask())
    if result and result.get("result"):
        console.print(Markdown(result["result"]))


# ─── 命令：agents ─────────────────────────────────────────
def _show_agents(role=None):
    registry, _ = get_registry_and_orch()
    all_agents = registry.list_all()
    if role:
        all_agents = [a for a in all_agents if a["role"] == role]

    table = Table(title="🦞 OpenClaw Agent 社区", border_style="red")
    table.add_column("ID", style="dim", width=22)
    table.add_column("名称", style="bold white", width=22)
    table.add_column("角色", width=12)
    table.add_column("模型", style="dim", width=20)
    table.add_column("技能数", justify="center", width=8)
    table.add_column("状态", width=10)

    role_colors = {
        "clawer": "blue", "guardian": "green", "vanguard": "purple",
        "maintainer": "yellow", "promoter": "magenta", "wellspring": "cyan",
    }
    for a in all_agents:
        color = role_colors.get(a["role"], "white")
        status_str = "[green]●活跃[/green]" if a["status"] == "active" else "[yellow]●降级[/yellow]"
        table.add_row(
            a["agent_id"], a["name"],
            f"[{color}]{a['role']}[/{color}]",
            a["model"], str(a["tool_count"]), status_str,
        )

    console.print(table)
    summary = registry.summary()
    console.print(f"\n[dim]共 {summary['total_agents']} 个 Agent | 活跃: {summary['active_agents']} | 降级: {summary['degraded_agents']}[/dim]")


@app.command()
def agents(
    role: Optional[str] = typer.Option(None, "--role", "-r", help="按角色过滤"),
):
    """🤖 列出社区 Agent"""
    _show_agents(role=role)


# ─── 命令：explore ────────────────────────────────────────
@app.command()
def explore(
    domain: str = typer.Argument(..., help="探索的研究领域"),
    focus: str = typer.Option("", "--focus", "-f", help="重点方向"),
):
    """🔭 Vanguard 前沿探索"""
    async def _explore():
        registry, _ = get_registry_and_orch()
        vanguard = registry.get_vanguard()
        if not vanguard:
            console.print("[red]❌ Vanguard 未初始化[/red]")
            return None
        console.print(f"[purple]🔭 Vanguard 正在探索: {domain}...[/purple]")
        with Progress(SpinnerColumn(), TextColumn("[purple]{task.description}"), console=console) as p:
            p.add_task("扫描前沿文献与趋势...", total=None)
            result = await vanguard.explore_frontier(domain, focus)
        return result

    result = asyncio.run(_explore())
    if result and result.get("result"):
        console.print(Panel(Markdown(result["result"]), title=f"[purple]🔭 {domain} 前沿探索报告[/purple]", border_style="purple"))


# ─── 命令：status ─────────────────────────────────────────
def _show_status():
    registry, _ = get_registry_and_orch()
    maintainer = registry.get_maintainer()
    summary = registry.summary()

    table = Table(title="⚙️ OpenClaw 系统状态", border_style="blue")
    table.add_column("指标", style="bold")
    table.add_column("值", style="green")
    table.add_row("总 Agent 数", str(summary["total_agents"]))
    table.add_row("活跃 Agent", str(summary["active_agents"]))
    table.add_row("降级 Agent", str(summary["degraded_agents"]))
    for role, count in summary.get("by_role", {}).items():
        table.add_row(f"  {role}", str(count))

    if maintainer:
        metrics = maintainer.collect_system_metrics()
        table.add_row("CPU", f"{metrics.get('cpu_percent', '--')}%")
        table.add_row("内存", f"{metrics.get('memory_percent', '--')}%")
        table.add_row("磁盘", f"{metrics.get('disk_percent', '--')}%")

    console.print(table)


@app.command()
def status():
    """⚙️ 查看系统状态"""
    _show_status()


# ─── 命令：chat (交互模式) ────────────────────────────────
@app.command()
def chat(
    agent_id: Optional[str] = typer.Option(None, "--agent", "-a", help="指定 Agent"),
):
    """💬 进入交互对话模式"""
    from config.settings import settings
    console.print(LOGO)
    console.print(f"[dim]科研智能体社区 v{settings.VERSION} | Academic AI Agent Community[/dim]\n")
    console.print("[dim]输入 /quit 退出 · /agents 列出 Agent · /status 查看状态 · /explore <领域> 前沿探索[/dim]\n")

    registry, orch = get_registry_and_orch()

    if agent_id:
        agent = registry.get(agent_id)
        if not agent:
            console.print(f"[red]Agent {agent_id} 未找到[/red]")
            return
        console.print(f"[green]已连接: {agent.name}[/green]\n")
    else:
        agent = None
        console.print("[dim]自动路由模式 - 将为每个任务选择最合适的 Agent[/dim]\n")

    while True:
        try:
            console.print("[bold red]你[/bold red]: ", end="")
            task = sys.stdin.readline()
            if task == "":          # EOF
                console.print("\n[dim]再见！[/dim]")
                break
            task = task.rstrip("\n")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]再见！[/dim]")
            break

        if not task.strip():
            continue
        if task.strip() == "/quit":
            console.print("[dim]再见！[/dim]")
            break
        if task.strip() == "/agents":
            _show_agents()
            continue
        if task.strip() == "/status":
            _show_status()
            continue
        if task.strip().startswith("/explore "):
            domain = task.strip()[9:]
            asyncio.run(_explore_inline(registry, domain))
            continue

        with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"), console=console, transient=True) as p:
            p.add_task("🦞 思考中...", total=None)
            if agent:
                result = asyncio.run(agent.run(task))
            else:
                result = asyncio.run(orch.execute(task, user_id="chat-user"))

        if result.get("status") == "success":
            agent_label = result.get('agent_name', result.get('agent_id', '?'))
            console.print(f"\n[dim]── {agent_label} ──[/dim]")
            console.print(Markdown(result.get("result", "无输出")))
        elif result.get("status") == "rejected":
            console.print(f"[red]⛔ Guardian 拒绝: {', '.join(result.get('issues', []))}[/red]")
        else:
            console.print(f"[red]❌ {result.get('error', '未知错误')}[/red]")
        console.print()


async def _explore_inline(registry, domain):
    vanguard = registry.get_vanguard()
    if not vanguard:
        console.print("[red]Vanguard 未初始化[/red]")
        return
    result = await vanguard.explore_frontier(domain)
    if result.get("result"):
        console.print(Markdown(result["result"]))


# ─── 命令：skills ─────────────────────────────────────────
@app.command()
def skills():
    """🔧 列出所有可用技能"""
    from skills.tools import SKILL_REGISTRY
    table = Table(title="🔧 OpenClaw 技能列表 (Top 50)", border_style="cyan")
    table.add_column("#", style="dim", width=4)
    table.add_column("技能名称", style="bold cyan", width=25)
    table.add_column("描述", style="white")
    for i, (name, skill) in enumerate(SKILL_REGISTRY.items(), 1):
        table.add_row(str(i), name, skill["description"][:70])
    console.print(table)
    console.print(f"\n[dim]共 {len(SKILL_REGISTRY)} 个技能[/dim]")


# ─── 命令：wellspring ─────────────────────────────────────
@app.command()
def wellspring():
    """💧 Wellspring 知识源泉状态"""
    registry, _ = get_registry_and_orch()
    ws = registry.get_wellspring()
    if not ws:
        console.print("[red]Wellspring 未初始化[/red]")
        return
    stats = ws.get_stats()
    table = Table(title="💧 Wellspring 知识源泉", border_style="cyan")
    table.add_column("存储类型", style="bold")
    table.add_column("条目数", style="green", justify="right")
    table.add_row("共享记忆", str(stats["shared_memory_count"]))
    table.add_row("知识库", str(stats["knowledge_hub_count"]))
    table.add_row("Prompt 模板", str(stats["prompt_hub_count"]))
    table.add_row("工作流模板", str(stats["workflow_hub_count"]))
    table.add_row("社区共识", str(stats["consensus_count"]))
    console.print(table)


# ─── 入口 ─────────────────────────────────────────────────
@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        console.print(LOGO)
        console.print("使用 [bold]openclaw --help[/bold] 查看所有命令\n")
        console.print("快速开始:")
        console.print("  [cyan]python cli/main.py chat[/cyan]              # 交互对话")
        console.print("  [cyan]python cli/main.py run '帮我综述...'[/cyan]  # 执行任务")
        console.print("  [cyan]python cli/main.py explore '大语言模型'[/cyan] # 前沿探索")
        console.print("  [cyan]python cli/main.py agents[/cyan]             # 查看所有 Agent")


if __name__ == "__main__":
    app()
