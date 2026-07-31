#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DiagBot 工单智能诊断助手 - CLI 交互界面
"""
import asyncio
import os
import sys

project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from config import APP_CONFIG, LLM_CONFIG, RESILIENCE_CONFIG
from config_agentscope import init_agentscope
from services.diagnosis_service import DiagnosisService
from utils.llm_resilience import run_health_check as check_llm_health


class DiagBotCLI:
    """工单智能诊断 CLI。"""

    def __init__(self):
        self.console = Console()
        self.user_id = None
        self.diagnosis_service = None

    def print_banner(self):
        self.console.print(
            f"\n[bold cyan]{APP_CONFIG['app_name']}[/bold cyan] - 帮你更快定位工单责任归属\n",
            style="bold",
        )

    def print_help(self):
        table = Table(title="命令列表", show_header=True, header_style="bold magenta")
        table.add_column("命令", style="cyan", width=18)
        table.add_column("说明", style="white")

        table.add_row("help", "显示帮助信息")
        table.add_row("status", "查看诊断统计")
        table.add_row("history", "查看最近诊断记录")
        table.add_row("health", "检查 LLM 服务可用性")
        table.add_row("clear", "清空本地诊断历史")
        table.add_row("exit", "退出程序")
        table.add_row("", "")
        table.add_row("[自然语言]", "直接输入工单描述，例如：")
        table.add_row("", "  - 请诊断工单 WO-20260815-0421")
        table.add_row("", "  - 商户2037反馈订单ORD-8823状态异常")
        table.add_row("", "  - 工单 WO-20260817-0611 结算金额不符")
        self.console.print(table)

    async def initialize_system(self):
        self.user_id = Prompt.ask("用户ID", default="diagbot_user")

        with self.console.status("初始化诊断系统...", spinner="dots"):
            init_agentscope()
            self.diagnosis_service = DiagnosisService(user_id=self.user_id)

        self.console.print(f"✓ 就绪 (用户: {self.user_id}) - 输入 help 查看帮助\n", style="green")

    async def process_query(self, user_input: str):
        with self.console.status("诊断中...", spinner="dots"):
            result = await self.diagnosis_service.diagnose(user_input)
        self._display_results(result)

    def _display_results(self, result_data: dict):
        diagnosis = result_data.get("diagnosis", {}) or {}
        trace = result_data.get("trace", {}) or {}

        summary = "\n".join([
            f"[bold]工单:[/bold] {result_data.get('ticket_id') or '未识别'}",
            f"[bold]场景:[/bold] {result_data.get('scenario', '未知')}",
            f"[bold]归属:[/bold] {diagnosis.get('responsible_party', '待判定')}",
            f"[bold]摘要:[/bold] {diagnosis.get('summary', '无')}",
            f"[bold]根因:[/bold] {diagnosis.get('root_cause', '无')}",
        ])
        self.console.print(Panel(summary, title="诊断结论", border_style="cyan"))

        recommendations = diagnosis.get("recommendations", [])
        if recommendations:
            table = Table(title="处理建议", show_header=True, header_style="bold magenta")
            table.add_column("#", style="cyan", width=4)
            table.add_column("建议", style="white")
            for idx, item in enumerate(recommendations, 1):
                table.add_row(str(idx), item)
            self.console.print(table)

        rounds = trace.get("rounds", [])
        if rounds:
            trace_table = Table(title="诊断 Trace", show_header=True, header_style="bold blue")
            trace_table.add_column("轮次", style="cyan", width=6)
            trace_table.add_column("Agent", style="white", width=24)
            trace_table.add_column("状态", style="green", width=10)
            trace_table.add_column("摘要", style="white")
            for round_data in rounds:
                round_num = str(round_data.get("round_num", ""))
                for agent in round_data.get("agents", []):
                    trace_table.add_row(
                        round_num,
                        agent.get("agent_name", ""),
                        agent.get("status", ""),
                        agent.get("output_summary", ""),
                    )
                    round_num = ""
            self.console.print(trace_table)

    def show_status(self):
        stats = self.diagnosis_service.long_term_memory.get_statistics()
        table = Table(title="诊断状态", show_header=True, header_style="bold magenta")
        table.add_column("类型", style="cyan")
        table.add_column("状态", style="white")
        table.add_row("累计诊断数", str(stats.get("total_diagnoses", 0)))
        table.add_row("累计消息数", str(stats.get("total_messages", 0)))
        issue_types = self.diagnosis_service.long_term_memory.get_common_issue_types(3)
        issue_summary = "、".join([f"{name}({count})" for name, count in issue_types]) if issue_types else "暂无"
        table.add_row("高频问题类型", issue_summary)
        self.console.print(table)

    def show_history(self):
        history = self.diagnosis_service.long_term_memory.get_diagnosis_history(10)
        if not history:
            self.console.print("暂无诊断历史", style="yellow")
            return

        table = Table(title="最近诊断记录", show_header=True, header_style="bold magenta")
        table.add_column("ID", style="cyan", width=14)
        table.add_column("工单", style="white", width=18)
        table.add_column("问题类型", style="white", width=16)
        table.add_column("责任归属", style="white", width=18)
        table.add_column("摘要", style="white")

        for item in history:
            table.add_row(
                item.get("diagnosis_id", ""),
                item.get("ticket_id", ""),
                item.get("issue_type", ""),
                item.get("responsible_party", ""),
                item.get("summary", ""),
            )
        self.console.print(table)

    async def run_health_check(self):
        ok, msg = await check_llm_health(
            base_url=LLM_CONFIG["base_url"],
            api_key=LLM_CONFIG["api_key"],
            model_name=LLM_CONFIG["model_name"],
            timeout_sec=RESILIENCE_CONFIG.get("health_check_timeout_sec", 10.0),
        )
        if ok:
            self.console.print("LLM 服务: [green]正常[/green]", style="bold")
        else:
            self.console.print(f"LLM 服务: [red]不可用[/red] - {msg}", style="bold")

    def clear_history(self):
        if Confirm.ask("确认清空本地诊断历史？", default=False):
            self.diagnosis_service.long_term_memory.clear_history()
            self.console.print("✓ 已清空诊断历史", style="green")

    async def run(self):
        self.print_banner()
        await self.initialize_system()

        while True:
            try:
                user_input = Prompt.ask("\n[cyan]>[/cyan]")
                if not user_input.strip():
                    continue

                command = user_input.strip().lower()
                if command == "exit":
                    self.console.print("再见！", style="cyan")
                    break
                if command == "help":
                    self.print_help()
                    continue
                if command == "status":
                    self.show_status()
                    continue
                if command == "history":
                    self.show_history()
                    continue
                if command == "health":
                    await self.run_health_check()
                    continue
                if command == "clear":
                    self.clear_history()
                    continue

                await self.process_query(user_input)
            except KeyboardInterrupt:
                self.console.print("\n使用 'exit' 退出", style="dim")
            except Exception as exc:
                self.console.print(f"\n错误: {exc}", style="red")


# 兼容旧引用
AligoCLI = DiagBotCLI


def run_health_check_standalone() -> int:
    init_agentscope()
    ok, msg = asyncio.run(check_llm_health(
        base_url=LLM_CONFIG["base_url"],
        api_key=LLM_CONFIG["api_key"],
        model_name=LLM_CONFIG["model_name"],
        timeout_sec=RESILIENCE_CONFIG.get("health_check_timeout_sec", 10.0),
    ))
    if ok:
        print("OK")
        return 0
    print(f"FAIL: {msg}")
    return 1


def main():
    if len(sys.argv) > 1 and sys.argv[1].strip().lower() == "health":
        raise SystemExit(run_health_check_standalone())
    cli = DiagBotCLI()
    asyncio.run(cli.run())


if __name__ == "__main__":
    main()
