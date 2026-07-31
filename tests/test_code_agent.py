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


@pytest.mark.asyncio
async def test_code_agent_order_round_two_returns_code_verdict():
    agent = CodeAgent(name="CodeAgent")
    payload = {
        "context": {
            "scenario": "order_status_anomaly",
            "round_num": 2,
            "merchant_id": "2037",
            "order_id": "ORD-8823",
        },
        "previous_results": [],
    }

    result = await agent.reply(Msg(name="user", content=json.dumps(payload, ensure_ascii=False), role="user"))
    data = json.loads(result.content)

    assert data["status"] == "success"
    assert "代码链路" in data["summary"]
    assert "trace_api" in data["tools_called"]
    assert "check_config" in data["tools_called"]
    assert "path_verdict" in data
