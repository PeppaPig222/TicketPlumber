#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
调度策略矩阵包

提供可配置的 Agent 调度策略抽象，用于把 DiagnosisIntentionAgent 里硬编码的
scenario × round → agent_schedule 逻辑抽离成可扩展的规则矩阵。
"""
from agents.scheduling.strategy_matrix import (
    AgentTask,
    SchedulingContext,
    StrategyMatrix,
    ScenarioScheduleBuilder,
    BasicInfoParallelRule,
    DeepLogConditionalRule,
    CrossDomainDependencyRule,
    RAGBusinessParallelRule,
)

__all__ = [
    "AgentTask",
    "SchedulingContext",
    "StrategyMatrix",
    "ScenarioScheduleBuilder",
    "BasicInfoParallelRule",
    "DeepLogConditionalRule",
    "CrossDomainDependencyRule",
    "RAGBusinessParallelRule",
]
