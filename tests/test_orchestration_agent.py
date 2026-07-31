#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
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
from agents.orchestration_agent import OrchestrationAgent
from agents.resolution_agent import ResolutionAgent
from skills.registry import SkillRegistry


@pytest.mark.asyncio
async def test_orchestration_agent_uses_lazy_registry_with_professional_agents():
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
    orchestrator = OrchestrationAgent(name="OrchestrationAgent", agent_registry=lazy_registry)
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
        "agent_schedule": [
            {"agent_name": "CodeAgent", "priority": 1, "reason": "复核代码链路", "expected_output": "技术复核"},
            {"agent_name": "OperationAgent", "priority": 1, "reason": "复核业务流程", "expected_output": "操作复核"},
            {"agent_name": "DataAgent", "priority": 1, "reason": "复核数据异常", "expected_output": "数据复核"},
            {"agent_name": "ResolutionAgent", "priority": 2, "reason": "汇总结论", "expected_output": "根因与建议"},
        ],
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

    result = await orchestrator.reply(Msg(name="user", content=json.dumps(intention_data, ensure_ascii=False), role="user"))
    payload = json.loads(result.content)

    assert payload["status"] == "completed"
    agent_names = [item["agent_name"] for item in payload["results"]]
    assert agent_names == ["CodeAgent", "OperationAgent", "DataAgent", "ResolutionAgent"]
    assert payload["results"][-1]["data"]["responsible_party"] == "数据侧（后台脚本）"
