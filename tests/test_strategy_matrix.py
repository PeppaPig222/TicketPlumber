#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
调度策略矩阵单元测试
"""
import pytest

from agents.scheduling.strategy_matrix import (
    AgentTask,
    BasicInfoParallelRule,
    CrossDomainDependencyRule,
    DeepLogConditionalRule,
    RAGBusinessParallelRule,
    ScenarioScheduleBuilder,
    SchedulingContext,
    StrategyMatrix,
)


class TestSchedulingContext:
    def test_has_entity_from_key_entities(self):
        ctx = SchedulingContext(key_entities={"order_id": "123"})
        assert ctx.has_entity("order_id") is True
        assert ctx.has_entity("missing") is False

    def test_has_entity_from_facts(self):
        ctx = SchedulingContext(collected_data={"facts": {"merchant_id": "M1"}})
        assert ctx.has_entity("merchant_id") is True

    def test_facts_property(self):
        ctx = SchedulingContext(collected_data={"facts": {"a": 1}})
        assert ctx.facts == {"a": 1}


class TestAgentTask:
    def test_to_dict_roundtrip(self):
        task = AgentTask(
            agent_name="CodeAgent",
            priority=1,
            reason="test",
            expected_output="output",
            strategy="base",
            depends_on=["DataAgent"],
            required_entities=["order_id"],
            skip_if_missing=True,
        )
        data = task.to_dict()
        restored = AgentTask.from_dict(data)
        assert restored == task


class TestScenarioScheduleBuilder:
    def test_generic_round1(self):
        ctx = SchedulingContext(scenario="generic_ticket_diagnosis", round_num=1)
        tasks = ScenarioScheduleBuilder().build(ctx)
        names = [t.agent_name for t in tasks]
        assert "OperationAgent" in names
        assert "DataAgent" in names

    def test_order_status_anomaly_round2(self):
        ctx = SchedulingContext(scenario="order_status_anomaly", round_num=2)
        tasks = ScenarioScheduleBuilder().build(ctx)
        names = [t.agent_name for t in tasks]
        assert "CodeAgent" in names
        assert "OperationAgent" in names
        assert "DataAgent" in names

    def test_unknown_scenario(self):
        ctx = SchedulingContext(scenario="not_exist", round_num=1)
        tasks = ScenarioScheduleBuilder().build(ctx)
        assert tasks == []


class TestBasicInfoParallelRule:
    def test_marks_first_round_basic_agents(self):
        ctx = SchedulingContext(scenario="generic_ticket_diagnosis", round_num=1)
        tasks = ScenarioScheduleBuilder().build(ctx)
        rule = BasicInfoParallelRule()
        meta = {}
        result = rule.apply(ctx, tasks, meta)
        assert meta["basic_info_parallel"] is True
        for task in result:
            if task.agent_name in {"CodeAgent", "OperationAgent", "DataAgent"}:
                assert task.strategy == "basic_info_parallel"

    def test_disabled_does_nothing(self, monkeypatch):
        from agents.scheduling import strategy_matrix

        monkeypatch.setitem(
            strategy_matrix.SCHEDULING_CONFIG,
            "enable_basic_info_parallel",
            False,
        )
        ctx = SchedulingContext(round_num=1)
        tasks = [AgentTask("DataAgent", 1, "", "")]
        rule = BasicInfoParallelRule()
        meta = {}
        result = rule.apply(ctx, tasks, meta)
        assert result[0].strategy == "base"


class TestDeepLogConditionalRule:
    def test_injects_deep_log_when_entity_present(self):
        ctx = SchedulingContext(
            scenario="order_status_anomaly",
            round_num=2,
            key_entities={"order_id": "123"},
        )
        tasks = ScenarioScheduleBuilder().build(ctx)
        rule = DeepLogConditionalRule()
        meta = {}
        result = rule.apply(ctx, tasks, meta)
        assert meta["deep_log_enabled"] is True
        names = [t.agent_name for t in result]
        assert "CodeAgent" in names
        deep_log = [t for t in result if t.strategy == "deep_log_conditional"]
        assert len(deep_log) == 1
        assert deep_log[0].depends_on == ["DataAgent"]
        assert deep_log[0].skip_if_missing is True

    def test_skips_when_entity_missing(self):
        ctx = SchedulingContext(scenario="order_status_anomaly", round_num=2)
        tasks = ScenarioScheduleBuilder().build(ctx)
        rule = DeepLogConditionalRule()
        meta = {}
        result = rule.apply(ctx, tasks, meta)
        assert meta["deep_log_enabled"] is False
        assert meta["deep_log_skipped_reason"] == "缺少触发实体"
        assert not any(t.strategy == "deep_log_conditional" for t in result)


class TestCrossDomainDependencyRule:
    def test_adds_resolution_dependencies(self):
        ctx = SchedulingContext(scenario="order_status_anomaly", round_num=3)
        tasks = ScenarioScheduleBuilder().build(ctx)
        rule = CrossDomainDependencyRule()
        meta = {}
        result = rule.apply(ctx, tasks, meta)
        assert meta["cross_domain_validation"] is True
        resolution = [t for t in result if t.agent_name == "ResolutionAgent"][0]
        assert "CodeAgent" in resolution.depends_on
        assert "OperationAgent" in resolution.depends_on
        assert "DataAgent" in resolution.depends_on


class TestRAGBusinessParallelRule:
    def test_injects_rag_task_when_available(self):
        ctx = SchedulingContext(
            scenario="order_status_anomaly", round_num=2, rag_available=True
        )
        tasks = ScenarioScheduleBuilder().build(ctx)
        rule = RAGBusinessParallelRule()
        meta = {}
        result = rule.apply(ctx, tasks, meta)
        assert meta["rag_parallel_enabled"] is True
        names = [t.agent_name for t in result]
        assert "RAGKnowledgeAgent" in names

    def test_skips_when_rag_unavailable(self):
        ctx = SchedulingContext(scenario="order_status_anomaly", round_num=2)
        tasks = ScenarioScheduleBuilder().build(ctx)
        rule = RAGBusinessParallelRule()
        meta = {}
        result = rule.apply(ctx, tasks, meta)
        assert meta["rag_parallel_enabled"] is False
        assert "rag_parallel_skipped_reason" in meta
        assert "RAGKnowledgeAgent" not in [t.agent_name for t in result]


class TestStrategyMatrix:
    def test_default_rules(self):
        matrix = StrategyMatrix()
        assert len(matrix.rules) == 4

    def test_build_returns_metadata(self):
        ctx = SchedulingContext(
            scenario="order_status_anomaly",
            round_num=2,
            key_entities={"order_id": "123"},
            rag_available=True,
        )
        matrix = StrategyMatrix()
        tasks, meta = matrix.build(ctx)
        assert meta["strategy_matrix_enabled"] is True
        assert meta["basic_info_parallel"] is True
        assert meta["deep_log_enabled"] is True
        assert meta["cross_domain_validation"] is True
        assert meta["rag_parallel_enabled"] is True
        names = [t.agent_name for t in tasks]
        assert "RAGKnowledgeAgent" in names

    def test_disabled_returns_base_schedule(self):
        ctx = SchedulingContext(scenario="order_status_anomaly", round_num=2)
        matrix = StrategyMatrix(config={"enable_strategy_matrix": False})
        tasks, meta = matrix.build(ctx)
        assert meta["strategy_matrix_enabled"] is False
        assert "RAGKnowledgeAgent" not in [t.agent_name for t in tasks]
