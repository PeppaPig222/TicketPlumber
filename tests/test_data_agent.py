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
from context.diagnosis_state import DiagnosisState


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


@pytest.mark.asyncio
async def test_data_agent_follow_up_verifies_db_state_hypothesis():
    agent = DataAgent(name="DataAgent")
    pending = {
        "type": "db_state",
        "detail": "支付表与订单表状态不一致",
        "status": "pending",
        "proposed_by": "DataAgent",
        "evidence": ["规则发现跨表不一致"],
    }
    context = {
        "scenario": "order_status_anomaly",
        "round_num": 3,
        "order_id": "ORD-8823",
        "merchant_id": "2037",
        "collected_data": {"facts": {"pending_hypotheses": [pending]}},
    }

    result = await agent._follow_up(context)
    data = json.loads(result.content)

    assert data["status"] == "success"
    assert data["tools_called"]  # follow_up 不再空转
    assert data["hypotheses"]
    resolved = data["hypotheses"][0]
    assert resolved["status"] in {"verified", "refuted"}
    assert resolved["type"] == "db_state"
    assert resolved["verified_by"] == "DataAgent"


def test_merge_round_upserts_hypothesis_status_transition():
    state = DiagnosisState()
    pending = {
        "type": "db_state",
        "detail": "支付表与订单表状态不一致",
        "status": "pending",
        "proposed_by": "DataAgent",
    }
    state.merge_round({"results": [{"agent_name": "DataAgent", "data": {"hypothesis": pending}}]})
    assert state.facts["hypotheses"][0]["status"] == "pending"

    verified = dict(pending, status="verified", verified_by="DataAgent")
    state.merge_round({"results": [{"agent_name": "DataAgent", "data": {"hypotheses": [verified]}}]})
    assert len(state.facts["hypotheses"]) == 1  # upsert 不追加重复项
    assert state.facts["hypotheses"][0]["status"] == "verified"
