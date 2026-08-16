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


def _ambiguous_logs():
    return [
        {"api_path": "/api/refund/callback", "status_code": 200, "error_code": None},
        {"api_path": "/api/refund/callback", "status_code": 505, "error_code": "PAY_005"},
    ]


def test_is_ambiguous_mixed_status():
    assert CodeAgent._is_ambiguous(_ambiguous_logs()) is True


def test_is_ambiguous_multiple_error_codes():
    logs = [
        {"status_code": 500, "error_code": "SYNC_002"},
        {"status_code": 503, "error_code": "PAY_005"},
    ]
    assert CodeAgent._is_ambiguous(logs) is True


def test_is_ambiguous_single_pattern_false():
    logs = [
        {"status_code": 200, "error_code": None},
        {"status_code": 200, "error_code": None},
    ]
    assert CodeAgent._is_ambiguous(logs) is False


def test_is_ambiguous_empty_false():
    assert CodeAgent._is_ambiguous([]) is False


def test_parse_decision_plain_json():
    raw = '{"decision": "trace_deeper", "api_path": "/api/order/sync"}'
    decision = CodeAgent._parse_decision(raw)
    assert decision is not None
    assert decision["decision"] == "trace_deeper"


def test_parse_decision_code_fence():
    raw = '```json\n{"decision": "conclude", "root_cause": "订单同步异常"}\n```'
    decision = CodeAgent._parse_decision(raw)
    assert decision is not None
    assert decision["decision"] == "conclude"


def test_parse_decision_with_prefix_suffix():
    raw = '好的，决策如下：{"decision": "config_check", "config_key": "refund_callback_timeout"} 以上。'
    decision = CodeAgent._parse_decision(raw)
    assert decision is not None
    assert decision["decision"] == "config_check"


def test_parse_decision_invalid_returns_none():
    assert CodeAgent._parse_decision("不是JSON") is None
    assert CodeAgent._parse_decision("") is None
    assert CodeAgent._parse_decision('{"no_decision": true}') is None


class _FakeLLMModel:
    def __init__(self, content: str):
        self._content = content

    async def __call__(self, messages):
        return Msg(name="fake-llm", content=self._content, role="assistant")


@pytest.mark.asyncio
async def test_llm_deep_dive_parses_llm_decision():
    decision_json = json.dumps({
        "decision": "trace_deeper",
        "api_path": "/api/order/sync",
        "root_cause": "订单状态同步异常",
        "reason": "同步链路返回 500",
    }, ensure_ascii=False)
    agent = CodeAgent(name="CodeAgent", model=_FakeLLMModel(decision_json))
    decision = await agent._llm_deep_dive(
        {"order_id": "ORD-8823"},
        _ambiguous_logs(),
        {"refund_enabled": True},
    )
    assert decision is not None
    assert decision["decision"] == "trace_deeper"
    assert decision["api_path"] == "/api/order/sync"
