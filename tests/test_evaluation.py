#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证离线评测脚本能正常运行并产出合理指标。"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from scripts.run_evaluation import load_dataset, run_case, compute_metrics, MockLLM


EVAL_DATASET = Path(project_root) / "data" / "evaluation" / "core_eval_set.json"


@pytest.fixture
def mini_dataset():
    """取 3 条代表性数据用于快速回归测试。"""
    dataset = load_dataset(EVAL_DATASET)
    return [
        next(r for r in dataset if r["id"] == "intent_001"),
        next(r for r in dataset if r["id"] == "intent_008"),
        next(r for r in dataset if r["id"] == "intent_014"),
    ]


@pytest.mark.asyncio
async def test_evaluation_runs_three_representative_cases(mini_dataset):
    with tempfile.TemporaryDirectory(prefix="diag_eval_test_") as tmp_dir:
        results = []
        for case in mini_dataset:
            result = await run_case(case, tmp_dir)
            results.append(result)

    metrics = compute_metrics(results)
    overall = metrics["overall"]

    # 三个带工单号的高频场景应全部通过
    assert overall["scenario_accuracy"] == 1.0
    assert overall["pass_at_1"] == 1.0


def test_core_eval_dataset_has_48_cases():
    dataset = load_dataset(EVAL_DATASET)
    assert len(dataset) == 48
    categories = {case["category"] for case in dataset}
    assert categories == {"intent", "root_cause", "attribution", "llm_decision"}


@pytest.mark.asyncio
async def test_llm_autonomy_dual_state_regression_settlement():
    """双态回归：settlement 动态 case 关 LLM 走规则 miss、开 LLM 命中精确主因。

    证明 LLM 非摆设——同一 case 的根因关键词「应用错误」只出现在 mock LLM 的
    受控归因里，规则路径只输出「标签与比例不一致」这类症状描述。
    """
    from config import SYSTEM_CONFIG

    dataset = load_dataset(EVAL_DATASET)
    case = next(r for r in dataset if r["id"] == "llm_decision_004")
    original = SYSTEM_CONFIG.get("enable_llm_autonomy", False)

    # 关 LLM：显式关闭 autonomy，DataAgent 退回确定性规则路径
    SYSTEM_CONFIG["enable_llm_autonomy"] = False
    try:
        with tempfile.TemporaryDirectory(prefix="diag_eval_off_") as tmp_dir:
            off = await run_case(case, tmp_dir)
    finally:
        SYSTEM_CONFIG["enable_llm_autonomy"] = original
    assert off["root_cause_ok"] is False
    assert off["llm_ok"] is False
    assert off["all_ok"] is False

    # 开 LLM：注入 mock LLM 并打开 autonomy 开关
    SYSTEM_CONFIG["enable_llm_autonomy"] = True
    try:
        with tempfile.TemporaryDirectory(prefix="diag_eval_on_") as tmp_dir:
            on = await run_case(case, tmp_dir, llm_model=MockLLM())
    finally:
        SYSTEM_CONFIG["enable_llm_autonomy"] = original

    assert on["root_cause_ok"] is True
    assert on["llm_ok"] is True
    assert on["all_ok"] is True
