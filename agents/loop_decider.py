#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Agentic Loop 决策器
职责：在各轮诊断结束后，根据结果决定下一步动作

三态转移：
  - "done"         → 输出诊断结论 + 归属方判定，结束 Loop
  - "cross_verify" → 发现异常或矛盾 → 启动交叉验证轮
  - "need_info"    → 工单信息不足，要求补充
"""
import logging

logger = logging.getLogger(__name__)


class LoopDecider:
    """Agentic Loop 决策器"""

    def __init__(self, max_rounds: int = 3):
        self.max_rounds = max_rounds

    def decide(self, round_result: dict, round_num: int) -> str:
        """
        根据当前轮次结果决定下一步

        Args:
            round_result: 当前轮 Scheduler 的聚合结果
            round_num: 当前轮次编号 (1-based)

        Returns:
            "continue" | "cross_verify" | "need_info" | "done"
        """
        # 达到最大轮次 → 强制结束
        if round_num >= self.max_rounds:
            logger.info(f"Max rounds ({self.max_rounds}) reached, forcing done")
            return "done"

        if round_num == 1:
            return self._decide_round1(round_result)

        if round_num == 2:
            return self._decide_round2(round_result)

        if round_num == 3:
            return self._decide_round3(round_result)

        return "done"

    def _decide_round1(self, result: dict) -> str:
        """Round 1：信息收集后的决策"""
        # 检查是否有 Skill 返回了 missing_info 标记
        results = result.get("results", [])
        for r in results:
            data = r.get("data", {}) or {}
            inner = data.get("data", {}) or {}
            missing = data.get("missing_info") or inner.get("missing_info") or []
            if missing:
                logger.info(f"Round 1: found missing info → need_info")
                return "need_info"

        logger.info("Round 1: info complete → continue")
        return "continue"

    def _decide_round2(self, result: dict) -> str:
        """Round 2：三条排查路径并行后的决策"""
        results = result.get("results", [])

        # 检查是否有数据一致性异常标记
        for r in results:
            data = r.get("data", {}) or {}
            inner = data.get("data", {}) or {}

            # 只信任结构化 inconsistency_found / data_inconsistent 信号，
            # 不再对 summary 做 "不一致" 字符串包含判断（会误判"未发现不一致"等表述）。
            inconsistency = (
                data.get("inconsistency_found")
                or inner.get("inconsistency_found")
                or data.get("data_inconsistent")
                or inner.get("data_inconsistent")
            )

            if inconsistency:
                logger.info("Round 2: data inconsistency found → cross_verify")
                return "cross_verify"

        logger.info("Round 2: no inconsistency → done")
        return "done"

    def _decide_round3(self, result: dict) -> str:
        """Round 3：交叉验证完成 → 输出结论"""
        logger.info("Round 3: cross-verification complete → done")
        return "done"

    def has_missing_info(self, round_result: dict) -> bool:
        """检查是否需要补充信息"""
        return self.decide(round_result, 1) == "need_info"

    def has_data_inconsistency(self, round_result: dict) -> bool:
        """检查是否存在数据不一致"""
        return self.decide(round_result, 2) == "cross_verify"
