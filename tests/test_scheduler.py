#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import os
import sys

import pytest
from agentscope.message import Msg

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from agents.code_agent import CodeAgent
from agents.data_agent import DataAgent
from agents.lazy_agent_registry import LazyAgentRegistry
from agents.operation_agent import OperationAgent
from agents.resolution_agent import ResolutionAgent
from agents.scheduler import Scheduler
from agents.scheduling import AgentTask
from skills.registry import SkillRegistry


@pytest.mark.asyncio
async def test_scheduler_uses_lazy_registry_with_professional_agents():
    skill_registry = SkillRegistry()
    lazy_registry = LazyAgentRegistry(
        model=None,
        cache={},
        custom_factories={
            "CodeAgent": lambda: CodeAgent(name="CodeAgent", skill_registry=skill_registry),
            "OperationAgent": lambda: OperationAgent(name="OperationAgent", skill_registry=skill_registry),
            "DataAgent": lambda: DataAgent(name="DataAgent", skill_registry=skill_registry),
            "ResolutionAgent": lambda: ResolutionAgent(name="ResolutionAgent", skill_registry=skill_registry),
        },
    )
    scheduler = Scheduler(name="Scheduler", agent_registry=lazy_registry)
    intention_data = {
        "scenario": "order_status_anomaly",
        "round_num": 3,
        "query": "请诊断工单 WO-20260815-0421",
        "ticket": {"ticket_id": "WO-20260815-0421", "merchant_id": "2037", "order_id": "ORD-8823", "issue_type": "订单状态异常"},
        "key_entities": {
            "ticket_id": "WO-20260815-0421",
            "merchant_id": "2037",
            "order_id": "ORD-8823",
            "issue_type": "订单状态异常",
            "scenario": "order_status_anomaly",
        },
        "collected_data": {
            "facts": {
                "ticket_id": "WO-20260815-0421",
                "merchant_id": "2037",
                "order_id": "ORD-8823",
                "issue_type": "订单状态异常",
                "scenario": "order_status_anomaly",
            }
        },
    }

    payload = await scheduler.run(intention_data)

    assert payload["status"] == "completed"
    agent_names = [item["agent_name"] for item in payload["results"]]
    assert agent_names == ["CodeAgent", "OperationAgent", "DataAgent", "ResolutionAgent"]
    assert payload["results"][-1]["data"]["responsible_party"] == "数据侧（后台脚本）"


def test_filter_by_permissions_drops_unauthorized():
    tasks = [
        AgentTask(agent_name="CodeAgent", priority=1, reason="r", expected_output="o"),
        AgentTask(agent_name="ResolutionAgent", priority=1, reason="r", expected_output="o"),
    ]
    filtered_r1 = Scheduler._filter_by_permissions(tasks, round_num=1)
    assert [t.agent_name for t in filtered_r1] == ["CodeAgent"]

    filtered_r3 = Scheduler._filter_by_permissions(tasks, round_num=3)
    assert [t.agent_name for t in filtered_r3] == ["CodeAgent", "ResolutionAgent"]


class _SlowAgent:
    def __init__(self, name: str):
        self.name = name

    async def reply(self, x):
        await asyncio.sleep(1)
        return Msg(name=self.name, content='{"status": "success"}', role="assistant")


@pytest.mark.asyncio
async def test_agent_timeout_marks_degraded():
    scheduler = Scheduler(agent_registry={"SlowAgent": _SlowAgent("SlowAgent")})
    scheduler.agent_timeout_sec = 0.05
    result = await scheduler._execute_agent(
        agent_name="SlowAgent",
        context={},
        reason="",
        expected_output="",
        previous_results=[],
    )
    assert result["status"] == "timeout"
    assert result["degraded"] is True
