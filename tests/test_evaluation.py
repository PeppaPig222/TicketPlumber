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

from scripts.run_evaluation import load_dataset, run_case, compute_metrics


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


def test_core_eval_dataset_has_44_cases():
    dataset = load_dataset(EVAL_DATASET)
    assert len(dataset) == 44
    categories = {case["category"] for case in dataset}
    assert categories == {"intent", "root_cause", "attribution"}
