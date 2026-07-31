#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import os
import sys

import pytest
from agentscope.message import Msg

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from agents.resolution_agent import ResolutionAgent


@pytest.mark.asyncio
async def test_resolution_agent_synthesizes_order_root_cause():
    agent = ResolutionAgent(name="ResolutionAgent")
    payload = {
        "context": {
            "scenario": "order_status_anomaly",
            "round_num": 3,
            "merchant_id": "2037",
            "issue_type": "订单状态异常",
        },
        "previous_results": [
            {"agent_name": "CodeAgent", "result": {"data": {"path_verdict": "代码链路无明显异常"}}},
            {"agent_name": "OperationAgent", "result": {"data": {"path_verdict": "用户操作流程符合规范"}}},
            {"agent_name": "DataAgent", "result": {"data": {"path_verdict": "支付表与订单表状态不一致"}}},
        ],
    }

    result = await agent.reply(Msg(name="user", content=json.dumps(payload, ensure_ascii=False), role="user"))
    data = json.loads(result.content)

    assert data["status"] == "success"
    assert data["responsible_party"] == "数据侧（后台脚本）"
    assert "状态同步" in data["root_cause"]
    assert len(data["recommendations"]) >= 2
