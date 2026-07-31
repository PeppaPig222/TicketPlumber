"""
CLI 自动诊断问答测试 - 直接运行生成 QA 对文档
Usage: python tests/test_cli_qa.py
使用 cli.AligoCLI._display_results 统一打印结果，避免重复逻辑。
"""
import sys
import asyncio
from io import StringIO
from pathlib import Path
from datetime import datetime
from typing import List, Dict
import logging

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


def capture_display_results(result_data: dict) -> str:
    """调用 cli 的 _display_results，将输出捕获为字符串（无 ANSI 颜色）。"""
    from rich.console import Console
    from cli import AligoCLI

    capture = StringIO()
    console = Console(file=capture, force_terminal=False, no_color=True)
    cli = AligoCLI()
    cli.console = console
    cli._display_results(result_data)
    return capture.getvalue().strip()


# 测试问题 - 覆盖当前 3 个核心诊断场景
QUESTIONS = [
    "请诊断工单 WO-20260815-0421",
    "帮我看下工单 WO-20260816-0532 为什么资产分配失败",
    "请排查工单 WO-20260817-0611 的结算金额不符问题",
    "商户2037反馈订单ORD-8823状态异常",
]


async def main():
    """运行测试并生成文档"""
    print("="*70)
    print("CLI QA 测试 - 开始")
    print("="*70)

    # 初始化系统 - 按诊断 CLI 主链路方式
    print("\n[1/3] 初始化系统...")

    from config_agentscope import init_agentscope
    from services.diagnosis_service import DiagnosisService

    # 初始化 AgentScope
    init_agentscope()

    diagnosis_service = DiagnosisService(user_id="qa_test_user")

    print("✓ 系统初始化完成")

    # 运行测试
    print(f"\n[2/3] 运行 {len(QUESTIONS)} 个测试问题...")
    results = []

    for i, question in enumerate(QUESTIONS, 1):
        print(f"\n问题 {i}/{len(QUESTIONS)}: {question}")
        start = datetime.now()

        try:
            result_data = await diagnosis_service.diagnose(question)
            duration = (datetime.now() - start).total_seconds()
            answer = capture_display_results(result_data)

            results.append({
                "num": i,
                "question": question,
                "answer": answer,
                "status": "success",
                "duration": round(duration, 2)
            })
            print(f"✓ 完成 ({duration:.1f}s)")

        except Exception as e:
            duration = (datetime.now() - start).total_seconds()
            results.append({
                "num": i,
                "question": question,
                "answer": f"错误: {str(e)}",
                "status": "error",
                "duration": round(duration, 2)
            })
            print(f"✗ 失败: {e}")
            import traceback
            traceback.print_exc()

        await asyncio.sleep(0.2)

    # 保存结果
    print("\n[3/3] 保存结果...")
    save_results(results)
    print("✓ 完成")

    # 打印统计
    success = sum(1 for r in results if r["status"] == "success")
    total_time = sum(r["duration"] for r in results)
    print(f"\n{'='*70}")
    print(f"统计: {success}/{len(results)} 成功, 总耗时 {total_time:.1f}s")
    print(f"{'='*70}\n")


def save_results(results: List[Dict]):
    """保存结果为 Markdown"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = project_root / "tests" / "results" / f"qa_test_{timestamp}.md"
    output.parent.mkdir(parents=True, exist_ok=True)

    with open(output, 'w', encoding='utf-8') as f:
        # 标题
        f.write(f"# CLI QA 测试报告\n\n")
        f.write(f"**测试时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

        # 统计
        success = sum(1 for r in results if r["status"] == "success")
        total_time = sum(r["duration"] for r in results)
        f.write(f"## 统计\n\n")
        f.write(f"- 总问题: {len(results)}\n")
        f.write(f"- 成功: {success} ({success/len(results)*100:.1f}%)\n")
        f.write(f"- 失败: {len(results)-success}\n")
        f.write(f"- 总耗时: {total_time:.1f}秒\n")
        f.write(f"- 平均: {total_time/len(results):.1f}秒/问题\n\n")

        # QA 对
        f.write(f"## QA 对\n\n")
        for r in results:
            icon = "✅" if r["status"] == "success" else "❌"
            f.write(f"### {icon} Q{r['num']}: {r['question']}\n\n")
            f.write(f"**耗时**: {r['duration']}秒\n\n")
            f.write(f"**回答**:\n\n```\n{r['answer']}\n```\n\n")
            f.write(f"---\n\n")

    print(f"结果已保存: {output}")


if __name__ == "__main__":
    asyncio.run(main())
