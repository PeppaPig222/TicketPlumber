#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import os
import sys

import pytest
from agentscope.message import Msg

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from agents.intention_agent import IntentionAgent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "expected_scenario", "expected_issue_type"),
    [
        ("请诊断工单 WO-20260815-0421", "order_status_anomaly", "订单状态异常"),
        ("帮我排查工单 WO-20260816-0532", "asset_allocation_failure", "资产分配失败"),
        ("请看下工单 WO-20260817-0611", "settlement_amount_mismatch", "结算金额不符"),
    ],
)
async def test_legacy_intention_agent_maps_ticket_to_diagnosis(query, expected_scenario, expected_issue_type):
    agent = IntentionAgent()
    result = await agent.reply(Msg(name="user", content=query, role="user"))
    payload = json.loads(result.content)

    assert payload["intents"][0]["type"] == "ticket_diagnosis"
    assert payload["scenario"] == expected_scenario
    assert payload["issue_type"] == expected_issue_type
    assert len(payload["agent_schedule"]) > 0


@pytest.mark.asyncio
async def test_legacy_intention_agent_supports_message_list_input():
    agent = IntentionAgent()
    messages = [
        Msg(name="assistant", content="请提供工单ID", role="assistant"),
        Msg(name="user", content="请诊断工单 WO-20260815-0421", role="user"),
    ]

    result = await agent.reply(messages)
    payload = json.loads(result.content)

    assert payload["ticket_id"] == "WO-20260815-0421"
    assert payload["scenario"] == "order_status_anomaly"
