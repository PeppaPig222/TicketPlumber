#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import os
import sys

import pytest
from agentscope.message import Msg

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from agents.data_agent import DataAgent


@pytest.mark.asyncio
async def test_data_agent_order_round_two_detects_inconsistency():
    agent = DataAgent(name="DataAgent")
    payload = {
        "context": {
            "scenario": "order_status_anomaly",
            "round_num": 2,
            "order_id": "ORD-8823",
        },
        "previous_results": [],
    }

    result = await agent.reply(Msg(name="user", content=json.dumps(payload, ensure_ascii=False), role="user"))
    data = json.loads(result.content)

    assert data["status"] == "success"
    assert data["inconsistency_found"] is True
    assert "不一致" in data["summary"]
