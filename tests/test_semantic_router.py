#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SemanticRouter 单元测试：验证双阈值 + margin 三分类逻辑。"""
from unittest.mock import MagicMock

import numpy as np

from agents.semantic_router import SemanticRouter


def _make_router(query_vec):
    """构造语义路由器，mock encoder 精确控制各场景与 query 的向量。"""
    routes = [
        {"name": "A", "descriptions": ["a1", "a2"]},
        {"name": "B", "descriptions": ["b1", "b2"]},
    ]
    encoder = MagicMock()
    # 调用顺序：A 场景锚点 → B 场景锚点 → query
    encoder.side_effect = [
        np.array([[1.0, 0.0, 0.0]] * 3, dtype=np.float32),  # A
        np.array([[0.0, 1.0, 0.0]] * 3, dtype=np.float32),  # B
        np.array([query_vec], dtype=np.float32),  # query
    ]
    return SemanticRouter(
        encoder=encoder,
        routes=routes,
        high_threshold=0.6,
        low_threshold=0.45,
        margin=0.08,
    )


def test_known_high_confidence():
    """query 与 A 高度相似且与 B 差异大 → known。"""
    result = _make_router([1.0, 0.0, 0.0]).route("订单状态异常")
    assert result["status"] == "known"
    assert result["scenario"] == "A"
    assert result["margin"] >= 0.08


def test_ambiguous_small_margin():
    """query 与 A、B 相似度接近（margin 小）→ ambiguous，即使分数超过高阈值。"""
    result = _make_router([0.707, 0.707, 0.0]).route("模糊问题")
    assert result["status"] == "ambiguous"
    # 候选场景集：两个场景都过 low 阈值，进入并集调度
    assert set(result["candidate_scenarios"]) == {"A", "B"}


def test_ambiguous_between_thresholds():
    """best 分数落在 [low, high) 区间 → ambiguous。"""
    # A 分数 0.5，B 分数 0
    result = _make_router([0.5, 0.0, 0.866]).route("介于阈值之间")
    assert result["status"] == "ambiguous"
    assert 0.45 <= result["confidence"] < 0.6


def test_unknown_below_low_threshold():
    """best 分数低于低阈值 → unknown，回退 generic。"""
    result = _make_router([0.0, 0.0, 1.0]).route("完全无关的问题")
    assert result["status"] == "unknown"
    assert result["scenario"] == "generic_ticket_diagnosis"
