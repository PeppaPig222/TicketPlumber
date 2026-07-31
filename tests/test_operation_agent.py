#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import os
import sys

import pytest
from agentscope.message import Msg

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from agents.operation_agent import OperationAgent


@pytest.mark.asyncio
async def test_operation_agent_asset_round_two_detects_binding_limits():
    agent = OperationAgent(name="OperationAgent")
    payload = {
        "context": {
            "scenario": "asset_allocation_failure",
            "round_num": 2,
            "merchant_id": "10001",
        },
        "previous_results": [],
    }

    result = await agent.reply(Msg(name="user", content=json.dumps(payload, ensure_ascii=False), role="user"))
    data = json.loads(result.content)

    assert data["status"] == "success"
    assert "保护期" in data["summary"]
    assert data["operation_path_detail"]["protection_period"]["status"] == "active"
