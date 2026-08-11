#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
离线评测脚本：批量跑诊断并输出指标报告。

用法：
    python scripts/run_evaluation.py
    python scripts/run_evaluation.py --dataset data/evaluation/core_eval_set.json
"""
import argparse
import asyncio
import json
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from services.diagnosis_service import DiagnosisService, TraceRepository


EVAL_DATASET_PATH = project_root / "data" / "evaluation" / "core_eval_set.json"
REPORT_DIR = project_root / "data" / "evaluation" / "reports"


def load_dataset(path: Path) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def check_root_cause(actual: str, expected_keywords: List[str]) -> bool:
    """只要命中任意一个关键词即认为根因正确。"""
    if not expected_keywords:
        return True
    actual = actual or ""
    return any(kw in actual for kw in expected_keywords)


def evaluate_case(result: Dict[str, Any], case: Dict[str, Any]) -> Dict[str, Any]:
    diagnosis = result.get("diagnosis", {}) or {}
    actual_scenario = result.get("scenario", "")
    actual_responsible = diagnosis.get("responsible_party", "")
    actual_root_cause = diagnosis.get("root_cause", "")
    actual_rounds = result.get("trace", {}).get("total_rounds", 0)

    scenario_ok = actual_scenario == case["expected_scenario"]
    responsible_ok = actual_responsible == case["expected_responsible_party"]
    root_cause_ok = check_root_cause(actual_root_cause, case.get("expected_root_cause_keywords", []))
    rounds_ok = actual_rounds == case.get("expected_rounds", actual_rounds)
    all_ok = scenario_ok and responsible_ok and root_cause_ok

    return {
        "id": case["id"],
        "category": case["category"],
        "query": case["query"],
        "scenario_ok": scenario_ok,
        "responsible_ok": responsible_ok,
        "root_cause_ok": root_cause_ok,
        "rounds_ok": rounds_ok,
        "all_ok": all_ok,
        "actual_scenario": actual_scenario,
        "actual_responsible": actual_responsible,
        "actual_root_cause": actual_root_cause,
        "actual_rounds": actual_rounds,
        "expected_scenario": case["expected_scenario"],
        "expected_responsible": case["expected_responsible_party"],
        "expected_root_cause_keywords": case.get("expected_root_cause_keywords", []),
        "expected_rounds": case.get("expected_rounds"),
    }


async def run_case(case: Dict[str, Any], storage_path: str) -> Dict[str, Any]:
    service = DiagnosisService(
        trace_repo=TraceRepository(),
        user_id="eval_user",
        storage_path=storage_path,
    )
    result = await service.diagnose(case["query"])
    return evaluate_case(result, case)


async def run_evaluation(dataset_path: Path) -> Dict[str, Any]:
    dataset = load_dataset(dataset_path)
    with tempfile.TemporaryDirectory(prefix="diag_eval_") as tmp_dir:
        tasks = [run_case(case, tmp_dir) for case in dataset]
        results = await asyncio.gather(*tasks)

    metrics = compute_metrics(results)
    return {
        "timestamp": datetime.now().isoformat(),
        "dataset": str(dataset_path),
        "total_cases": len(dataset),
        "metrics": metrics,
        "results": results,
    }


def compute_metrics(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(results)
    if total == 0:
        return {}

    overall = {
        "scenario_accuracy": sum(r["scenario_ok"] for r in results) / total,
        "responsible_party_accuracy": sum(r["responsible_ok"] for r in results) / total,
        "root_cause_hit_rate": sum(r["root_cause_ok"] for r in results) / total,
        "rounds_accuracy": sum(r["rounds_ok"] for r in results) / total,
        "pass_at_1": sum(r["all_ok"] for r in results) / total,
    }

    by_category = defaultdict(lambda: {"count": 0, "pass": 0})
    for r in results:
        cat = r["category"]
        by_category[cat]["count"] += 1
        if r["all_ok"]:
            by_category[cat]["pass"] += 1

    category_pass = {
        cat: stats["pass"] / stats["count"]
        for cat, stats in by_category.items()
    }

    return {
        "overall": overall,
        "by_category": category_pass,
        "failure_count": total - sum(r["all_ok"] for r in results),
    }


def print_report(report: Dict[str, Any]):
    metrics = report["metrics"]
    overall = metrics["overall"]

    print("\n" + "=" * 60)
    print("小哈工单智能诊断助手 — 离线评测报告")
    print("=" * 60)
    print(f"数据集: {report['dataset']}")
    print(f"用例数: {report['total_cases']}")
    print(f"时间: {report['timestamp']}")
    print("-" * 60)
    print("总体指标")
    print(f"  场景准确率 (Scenario Accuracy):     {overall['scenario_accuracy']:.2%}")
    print(f"  责任方准确率 (Attribution Acc):     {overall['responsible_party_accuracy']:.2%}")
    print(f"  根因命中率 (Root Cause Hit Rate):   {overall['root_cause_hit_rate']:.2%}")
    print(f"  轮次准确率 (Rounds Accuracy):       {overall['rounds_accuracy']:.2%}")
    print(f"  端到端通过率 (Pass@1):              {overall['pass_at_1']:.2%}")
    print("-" * 60)
    print("分类 Pass@1")
    for cat, rate in metrics["by_category"].items():
        print(f"  {cat:<12}: {rate:.2%}")
    print("-" * 60)
    print(f"失败用例数: {metrics['failure_count']}")

    failures = [r for r in report["results"] if not r["all_ok"]]
    if failures:
        print("\n失败明细（前 10 条）:")
        for r in failures[:10]:
            print(f"  - {r['id']} ({r['category']}): {r['query']}")
            print(f"    scenario: {r['actual_scenario']} (expected {r['expected_scenario']})")
            print(f"    responsible: {r['actual_responsible']} (expected {r['expected_responsible']})")
            print(f"    root_cause: {r['actual_root_cause']}")
    print("=" * 60 + "\n")


def save_report(report: Dict[str, Any]):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORT_DIR / f"eval_report_{timestamp}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"详细报告已保存: {report_path}")


def save_markdown_report(report: Dict[str, Any]):
    """保存 Markdown 格式评测报告"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = REPORT_DIR / f"eval_report_{timestamp}.md"

    metrics = report["metrics"]
    overall = metrics["overall"]
    failures = [r for r in report["results"] if not r["all_ok"]]

    lines = [
        "# 小哈工单智能诊断助手 — 离线评测报告",
        "",
        "## 概览",
        "",
        f"- **数据集**: `{report['dataset']}`",
        f"- **用例数**: {report['total_cases']}",
        f"- **评测时间**: {report['timestamp']}",
        f"- **失败用例数**: {metrics['failure_count']}",
        "",
        "## 总体指标",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| 场景准确率 (Scenario Accuracy) | {overall['scenario_accuracy']:.2%} |",
        f"| 责任方准确率 (Attribution Acc) | {overall['responsible_party_accuracy']:.2%} |",
        f"| 根因命中率 (Root Cause Hit Rate) | {overall['root_cause_hit_rate']:.2%} |",
        f"| 轮次准确率 (Rounds Accuracy) | {overall['rounds_accuracy']:.2%} |",
        f"| 端到端通过率 (Pass@1) | {overall['pass_at_1']:.2%} |",
        "",
        "## 分类 Pass@1",
        "",
        "| 分类 | 通过率 |",
        "|---|---|",
    ]
    for cat, rate in metrics["by_category"].items():
        lines.append(f"| {cat} | {rate:.2%} |")

    lines.extend([
        "",
        "## 失败明细",
        "",
    ])

    if failures:
        lines.append("| 用例ID | 分类 | 查询 | 实际场景 | 期望场景 | 实际责任方 | 期望责任方 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for r in failures[:20]:
            lines.append(
                f"| {r['id']} | {r['category']} | {r['query'][:40]}{'...' if len(r['query']) > 40 else ''} | "
                f"{r['actual_scenario']} | {r['expected_scenario']} | {r['actual_responsible']} | {r['expected_responsible']} |"
            )
    else:
        lines.append("全部用例通过，无失败明细。")

    # 关键结论
    pass_rate = overall["pass_at_1"]
    if pass_rate >= 0.9:
        conclusion = "🟢 系统表现优秀，端到端通过率达到 90% 以上。"
    elif pass_rate >= 0.75:
        conclusion = "🟡 系统表现良好，但仍有优化空间。"
    elif pass_rate >= 0.6:
        conclusion = "🟠 系统表现一般，建议针对失败用例重点优化。"
    else:
        conclusion = "🔴 系统表现较差，需要全面排查链路问题。"

    lines.extend([
        "",
        "## 结论",
        "",
        conclusion,
        "",
    ])

    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Markdown 报告已保存: {md_path}")


def main():
    parser = argparse.ArgumentParser(description="小哈工单智能诊断助手离线评测")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=EVAL_DATASET_PATH,
        help="评测数据集路径",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        default=True,
        help="保存详细报告到 data/evaluation/reports/",
    )
    args = parser.parse_args()

    if not args.dataset.exists():
        print(f"数据集不存在: {args.dataset}")
        sys.exit(1)

    report = asyncio.run(run_evaluation(args.dataset))
    print_report(report)
    if args.save:
        save_report(report)
        save_markdown_report(report)


if __name__ == "__main__":
    main()
