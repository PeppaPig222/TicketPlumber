#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""语义路由器 SemanticRouter

用 embedding 对用户 query 做「场景分类 + 置信度」，与规则匹配互补：
- 规则命中（高频闭集）→ 直接走确定性调度，不触发 embedding；
- 规则未命中（长尾/模糊）→ 语义路由按双阈值 + margin 分成 known / ambiguous / unknown。

职责边界：仅负责「场景分类」，不做知识检索（RAG）与假设验证（假设状态机）。
"""
import logging
from typing import Any, Callable, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


# 意图场景路由：name 对应 scenario 枚举，descriptions 为典型用户表述（embedding 锚点）
DEFAULT_INTENT_ROUTES: List[Dict[str, Any]] = [
    {
        "name": "order_status_anomaly",
        "descriptions": [
            "订单状态异常",
            "订单一直显示处理中",
            "订单支付成功但状态没更新",
            "订单回调超时",
            "订单卡单",
            "退款没有到账",
            "订单状态不一致",
        ],
    },
    {
        "name": "asset_allocation_failure",
        "descriptions": [
            "资产分配失败",
            "免时长没有生效",
            "资产没有到账",
            "资源分配异常",
            "资产池分配失败",
            "用户绑定资产失败",
            "保护期资产回收异常",
        ],
    },
    {
        "name": "settlement_amount_mismatch",
        "descriptions": [
            "结算金额不符",
            "账单金额对不上",
            "结算少钱了",
            "对账不平",
            "结算周期金额错误",
            "发票金额不一致",
            "结算状态异常",
        ],
    },
]


class SemanticRouter:
    """基于 embedding 余弦相似度的场景路由器（双阈值 + margin 三分类）。"""

    def __init__(
        self,
        encoder: Callable[[List[str]], Any],
        routes: Optional[List[Dict[str, Any]]] = None,
        high_threshold: float = 0.6,
        low_threshold: float = 0.45,
        margin: float = 0.08,
    ):
        if encoder is None:
            raise ValueError("SemanticRouter 需要注入 encoder（embedding 编码函数）")
        self.encoder = encoder
        self.routes = routes or DEFAULT_INTENT_ROUTES
        self.high_threshold = high_threshold
        self.low_threshold = low_threshold
        self.margin = margin
        # 预计算每个场景锚点的归一化向量
        self._route_embeddings: Dict[str, np.ndarray] = {}
        self._build_route_embeddings()

    def _encode(self, texts: List[str]) -> np.ndarray:
        vecs = np.asarray(self.encoder(texts), dtype=np.float32)
        if vecs.ndim == 1:
            vecs = vecs.reshape(1, -1)
        # 归一化，保证余弦相似度 = 点积
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms

    def _build_route_embeddings(self) -> None:
        for route in self.routes:
            examples = [route["name"], *route.get("descriptions", [])]
            self._route_embeddings[route["name"]] = self._encode(examples)

    def route(self, query: str) -> Dict[str, Any]:
        """返回 {status, scenario, confidence, margin, candidates}。

        status: known（高置信且 margin 大）/ ambiguous（置信度在双阈值之间或 margin 小）
                / unknown（低于低阈值，走 RAG/Explore 或转人工）。
        """
        q = self._encode([query])[0]

        candidates: List[Dict[str, Any]] = []
        for route in self.routes:
            sims = self._route_embeddings[route["name"]] @ q  # 点积 = cosine
            score = float(sims.max())
            candidates.append({"scenario": route["name"], "score": score})

        candidates.sort(key=lambda c: c["score"], reverse=True)
        best = candidates[0]
        second = candidates[1] if len(candidates) > 1 else {"scenario": "", "score": 0.0}
        margin_val = round(best["score"] - second["score"], 4)

        if best["score"] >= self.high_threshold and margin_val >= self.margin:
            status = "known"
        elif best["score"] >= self.low_threshold:
            status = "ambiguous"
        else:
            status = "unknown"

        # ambiguous 时输出候选场景集（过 low 阈值的 top2），供多候选场景并集调度
        candidate_scenarios: List[str] = []
        if status == "ambiguous":
            candidate_scenarios = [
                c["scenario"] for c in candidates[:2]
                if c["score"] >= self.low_threshold
            ]

        return {
            "status": status,
            "scenario": (
                best["scenario"] if status != "unknown" else "generic_ticket_diagnosis"
            ),
            "confidence": round(best["score"], 4),
            "margin": margin_val,
            "candidates": candidates,
            "candidate_scenarios": candidate_scenarios,
        }
