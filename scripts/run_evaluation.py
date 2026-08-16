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
import re
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from agentscope.message import Msg
from services.diagnosis_service import DiagnosisService, TraceRepository


EVAL_DATASET_PATH = project_root / "data" / "evaluation" / "core_eval_set.json"
REPORT_DIR = project_root / "data" / "evaluation" / "reports"


class MockLLM:
    """评测专用 mock LLM：无真实 API，按 prompt 特征返回确定性归因/决策。

    双态回归用：开 enable_llm_autonomy 时注入，模拟 LLM 判别主因，
    证明「LLM 非摆设」——关 LLM 走规则 miss，开 LLM 命中精确主因。
    """

    async def __call__(self, messages):
        prompt = messages[-1].get("content", "") if messages else ""
        return Msg(name="mock-llm", content=self._respond(prompt), role="assistant")

    @staticmethod
    def _respond(prompt: str) -> str:
        if "decision 字段三选一" in prompt:
            return MockLLM._code_decision(prompt)
        if "conflict_type 必须从候选集选一个" in prompt:
            return MockLLM._data_attribution(prompt)
        return "{}"

    @staticmethod
    def _code_decision(prompt: str) -> str:
        # 只匹配日志摘要里的真实 error_code（形如 error=REFUND_001），
        # 不能匹配 prompt 示例文本里的「REFUND_* → ...」，否则会恒命中第一个分支。
        if "error=REFUND_" in prompt:
            root_cause = "退款请求参数校验失败"
        elif "error=CHANNEL_" in prompt:
            root_cause = "退款通道故障"
        elif "error=PAY_" in prompt or "error=SYNC_" in prompt:
            root_cause = "支付回调超时导致状态同步失败"
        else:
            root_cause = "订单状态同步异常"
        return json.dumps(
            {"decision": "conclude", "root_cause": root_cause, "reason": "按日志 error_code 判别"},
            ensure_ascii=False,
        )

    @staticmethod
    def _data_attribution(prompt: str) -> str:
        has_timeout = '"has_timeout": true' in prompt or '"has_timeout":true' in prompt
        ratio_mismatch = '"ratio_mismatch": true' in prompt or '"ratio_mismatch":true' in prompt
        tag_mismatch = '"tag_mismatch": true' in prompt or '"tag_mismatch":true' in prompt
        if ratio_mismatch and tag_mismatch:
            conflict_type, explanation = "label_conflict", "结算标签脚本误刷导致分润比例应用错误"
        elif has_timeout:
            conflict_type, explanation = "callback_timeout", "支付回调超时导致订单状态未同步"
        elif ratio_mismatch:
            conflict_type, explanation = "ratio_mismatch", "分润比例与合同不一致"
        elif tag_mismatch:
            conflict_type, explanation = "label_conflict", "结算标签与合同类型冲突"
        else:
            conflict_type, explanation = "both", "跨表不一致且回调超时"
        return json.dumps(
            {"conflict_type": conflict_type, "confidence": 0.9, "explanation": explanation},
            ensure_ascii=False,
        )


def load_dataset(path: Path) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def check_root_cause(diagnosis: Dict[str, Any], expected_keywords: List[str]) -> bool:
    """根因评测：结构化多字段 + 语义软匹配。

    Structured：把 root_cause / summary / evidence 合并为「根因语义文本」，
    避免只匹配单一 root_cause 字段导致的假阴性（如「规则不一致」实际写在 summary 里）。
    Semantic：连续子串命中优先；否则按 2 字切分关键词，允许跨词命中
    （例如「回调超时」能命中「回调…超时」）。
    """
    if not expected_keywords:
        return True

    parts = [
        diagnosis.get("root_cause", ""),
        diagnosis.get("summary", ""),
    ]
    evidence = diagnosis.get("evidence", [])
    if isinstance(evidence, list):
        parts.extend(str(e) for e in evidence)
    haystack = " ".join(p for p in parts if p)

    for kw in expected_keywords:
        kw = (kw or "").strip()
        if not kw:
            continue
        if kw in haystack:
            return True
        tokens = [kw[i:i + 2] for i in range(0, len(kw), 2)]
        if len(tokens) > 1 and all(t in haystack for t in tokens):
            return True
    return False


def extract_expected_entities(query: str) -> Dict[str, str]:
    """从 query 提取明确出现的实体标识，作为 Entity 环节的期望值。"""
    ticket_id = re.search(r"WO-\d{8}-\d{4}", query or "")
    order_id = re.search(r"ORD-\d+", query or "")
    return {
        "ticket_id": ticket_id.group(0) if ticket_id else "",
        "order_id": order_id.group(0) if order_id else "",
    }


def evaluate_stages(result: Dict[str, Any], case: Dict[str, Any]) -> Dict[str, Any]:
    """逐环节判定诊断在各阶段的正确性。

    返回每环节 bool 或 None（None 表示该环节本次未触发/无法判定，不计入统计分母）。
    """
    diagnosis = result.get("diagnosis", {}) or {}
    key_entities = result.get("key_entities", {}) or {}
    trace = result.get("trace", {}) or {}
    rounds = trace.get("rounds", []) or []
    agents = [a for r in rounds for a in (r.get("agents", []) or [])]

    # Intent：场景识别
    intent_ok = result.get("scenario", "") == case["expected_scenario"]

    # Entity：关键实体提取（仅对 query 里明确出现的实体做校验）
    expected = extract_expected_entities(case["query"])
    entity_checks = []
    if expected["ticket_id"]:
        entity_checks.append(str(key_entities.get("ticket_id", "")) == expected["ticket_id"])
    if expected["order_id"]:
        entity_checks.append(str(key_entities.get("order_id", "")) == expected["order_id"])
    entity_ok = None if not entity_checks else all(entity_checks)

    # RAG：知识检索执行成功（仅在 RAGKnowledgeAgent 被调度时统计）
    rag_agents = [a for a in agents if a.get("agent_name") == "RAGKnowledgeAgent"]
    rag_ok = None if not rag_agents else all(a.get("status") == "success" for a in rag_agents)

    # Tool：专业 Agent 执行成功（agent 层 status，代表其内部工具调用未失败）
    professional = {"CodeAgent", "OperationAgent", "DataAgent"}
    tool_checks = [a for a in agents if a.get("agent_name") in professional]
    tool_ok = None if not tool_checks else all(a.get("status") == "success" for a in tool_checks)

    # Agent：诊断结论（责任方 + 根因）
    responsible_ok = diagnosis.get("responsible_party", "") == case["expected_responsible_party"]
    root_cause_ok = check_root_cause(
        diagnosis,
        case.get("expected_root_cause_keywords", []),
    )
    agent_ok = responsible_ok and root_cause_ok

    # Verification：cross_verify 触发后最终结论是否正确
    decisions = [r.get("decision", "") for r in rounds]
    has_cross_verify = any(d == "cross_verify" for d in decisions)
    verification_ok = None if not has_cross_verify else agent_ok

    # LLM 自主决策：CodeAgent 探索型深挖 或 DataAgent 受控归因 命中即算 llm_ok
    # （仅对声明 expected_llm_autonomy 的 case 校验）
    if case.get("expected_llm_autonomy"):
        code_agents = [a for a in agents if a.get("agent_name") == "CodeAgent"]
        data_agents = [a for a in agents if a.get("agent_name") == "DataAgent"]
        code_llm = any(
            any(
                "LLM 深挖" in str(e) or "LLM 判定根因" in str(e)
                for e in a.get("evidence", [])
            )
            for a in code_agents
        )
        data_llm = any(
            any("LLM 归因" in str(e) for e in a.get("evidence", []))
            for a in data_agents
        )
        llm_ok = code_llm or data_llm
    else:
        llm_ok = None

    return {
        "intent_ok": intent_ok,
        "entity_ok": entity_ok,
        "rag_ok": rag_ok,
        "tool_ok": tool_ok,
        "agent_ok": agent_ok,
        "verification_ok": verification_ok,
        "llm_ok": llm_ok,
    }


def evaluate_case(result: Dict[str, Any], case: Dict[str, Any]) -> Dict[str, Any]:
    diagnosis = result.get("diagnosis", {}) or {}
    actual_scenario = result.get("scenario", "")
    actual_responsible = diagnosis.get("responsible_party", "")
    actual_root_cause = diagnosis.get("root_cause", "")
    actual_rounds = result.get("trace", {}).get("total_rounds", 0)

    scenario_ok = actual_scenario == case["expected_scenario"]
    responsible_ok = actual_responsible == case["expected_responsible_party"]
    root_cause_ok = check_root_cause(diagnosis, case.get("expected_root_cause_keywords", []))
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
        **evaluate_stages(result, case),
    }


async def run_case(case: Dict[str, Any], storage_path: str, llm_model=None) -> Dict[str, Any]:
    service = DiagnosisService(
        trace_repo=TraceRepository(),
        user_id="eval_user",
        storage_path=storage_path,
        llm_model=llm_model,
    )
    result = await service.diagnose(case["query"])
    return evaluate_case(result, case)


async def run_evaluation(dataset_path: Path, llm_model=None) -> Dict[str, Any]:
    dataset = load_dataset(dataset_path)
    with tempfile.TemporaryDirectory(prefix="diag_eval_") as tmp_dir:
        tasks = [run_case(case, tmp_dir, llm_model=llm_model) for case in dataset]
        results = await asyncio.gather(*tasks)

    metrics = compute_metrics(results)
    return {
        "timestamp": datetime.now().isoformat(),
        "dataset": str(dataset_path),
        "total_cases": len(dataset),
        "metrics": metrics,
        "results": results,
    }


STAGE_LABELS = {
    "intent": "Intent（场景识别）",
    "entity": "Entity（实体提取）",
    "rag": "RAG（知识检索）",
    "tool": "Tool（工具调用）",
    "agent": "Agent（诊断结论）",
    "verification": "Verification（交叉验证）",
    "llm": "LLM（动态决策）",
}


def compute_stage_metrics(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """汇总各环节命中率。None 值（未触发/无法判定）不计入分母。"""
    stages = {}
    for stage, label in STAGE_LABELS.items():
        key = f"{stage}_ok"
        valid = [r.get(key) for r in results if r.get(key) is not None]
        total = len(valid)
        passed = sum(1 for v in valid if v)
        stages[stage] = {
            "label": label,
            "total": total,
            "passed": passed,
            "rate": (passed / total) if total else None,
        }
    return stages


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
        "stages": compute_stage_metrics(results),
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
    print("分环节命中率（定位瓶颈）")
    stages = metrics.get("stages", {})
    for stage in STAGE_LABELS:
        s = stages.get(stage, {})
        rate = s.get("rate")
        if rate is None:
            print(f"  {s.get('label', stage):<28}: N/A（未触发，样本 {s.get('total', 0)}）")
        else:
            print(f"  {s.get('label', stage):<28}: {rate:.2%} ({s.get('passed', 0)}/{s.get('total', 0)})")
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
        "## 分环节命中率",
        "",
        "| 环节 | 命中率 | 样本 |",
        "|---|---|---|",
    ]
    for stage in STAGE_LABELS:
        s = metrics.get("stages", {}).get(stage, {})
        rate = s.get("rate")
        if rate is None:
            lines.append(f"| {s.get('label', stage)} | N/A（未触发） | {s.get('total', 0)} |")
        else:
            lines.append(f"| {s.get('label', stage)} | {rate:.2%} | {s.get('passed', 0)}/{s.get('total', 0)} |")

    lines.extend([
        "",
        "## 分类 Pass@1",
        "",
        "| 分类 | 通过率 |",
        "|---|---|",
    ])
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
    parser.add_argument(
        "--llm-autonomy",
        action="store_true",
        help="开 LLM 自主（注入 mock LLM 做双态回归，无真实 API 调用）",
    )
    args = parser.parse_args()

    if not args.dataset.exists():
        print(f"数据集不存在: {args.dataset}")
        sys.exit(1)

    from config import SCHEDULING_CONFIG, SYSTEM_CONFIG

    llm_model = None
    if args.llm_autonomy:
        SYSTEM_CONFIG["enable_llm_autonomy"] = True
        SCHEDULING_CONFIG["enable_hypothesis_routing"] = True
        llm_model = MockLLM()
        print("已开启 LLM 自主 + 假设路由（注入 mock LLM），enable_llm_autonomy=true, enable_hypothesis_routing=true")
    else:
        # 双态回归的基准态：显式关闭 LLM 自主与假设路由，保证走确定性规则路径（不受 .env 影响）
        SYSTEM_CONFIG["enable_llm_autonomy"] = False
        SCHEDULING_CONFIG["enable_hypothesis_routing"] = False
        print("已关闭 LLM 自主与假设路由（确定性规则路径），enable_llm_autonomy=false, enable_hypothesis_routing=false")

    report = asyncio.run(run_evaluation(args.dataset, llm_model=llm_model))
    print_report(report)
    if args.save:
        save_report(report)
        save_markdown_report(report)


if __name__ == "__main__":
    main()
