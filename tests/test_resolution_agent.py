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


def test_verified_hypotheses_reads_follow_up_results():
    agent = ResolutionAgent(name="ResolutionAgent")
    previous_results = [
        {
            "agent_name": "CodeAgent",
            "result": {"data": {"hypotheses": [
                {"type": "api_trace", "detail": "退款请求参数校验失败", "status": "verified", "proposed_by": "CodeAgent"},
            ]}},
        },
        {
            "agent_name": "DataAgent",
            "result": {"data": {"hypotheses": [
                {"type": "db_state", "detail": "支付回调超时导致订单状态未同步", "status": "verified", "proposed_by": "DataAgent"},
            ]}},
        },
    ]
    verified = agent._verified_hypotheses(previous_results)
    assert {h["type"] for h in verified} == {"api_trace", "db_state"}


def test_hypothesis_root_cause_prefers_api_trace():
    agent = ResolutionAgent(name="ResolutionAgent")
    previous_results = [
        {
            "agent_name": "CodeAgent",
            "result": {"data": {"hypotheses": [
                {"type": "api_trace", "detail": "退款请求参数校验失败", "status": "verified", "proposed_by": "CodeAgent"},
            ]}},
        },
        {
            "agent_name": "DataAgent",
            "result": {"data": {"hypotheses": [
                {"type": "db_state", "detail": "支付回调超时导致订单状态未同步", "status": "verified", "proposed_by": "DataAgent"},
            ]}},
        },
    ]
    assert agent._hypothesis_root_cause(previous_results) == "退款请求参数校验失败"


def test_hypothesis_root_cause_ignores_refuted():
    agent = ResolutionAgent(name="ResolutionAgent")
    previous_results = [
        {
            "agent_name": "CodeAgent",
            "result": {"data": {"hypotheses": [
                {"type": "api_trace", "detail": "退款请求参数校验失败", "status": "refuted", "proposed_by": "CodeAgent"},
            ]}},
        },
    ]
    assert agent._hypothesis_root_cause(previous_results) is None


@pytest.mark.asyncio
async def test_resolve_settlement_consumes_verified_hypothesis():
    agent = ResolutionAgent(name="ResolutionAgent")
    payload = {
        "context": {
            "scenario": "settlement_amount_mismatch",
            "round_num": 3,
            "merchant_id": "3052",
        },
        "previous_results": [
            {"agent_name": "CodeAgent", "result": {"data": {"path_verdict": "结算计算链路命中比例不一致"}}},
            {"agent_name": "OperationAgent", "result": {"data": {"path_verdict": "人工流程无明显异常"}}},
            {
                "agent_name": "DataAgent",
                "result": {"data": {
                    "path_verdict": "结算标签与比例存在不一致",
                    "hypotheses": [
                        {
                            "type": "db_state",
                            "detail": "结算标签脚本误刷导致分润比例应用错误",
                            "status": "verified",
                            "proposed_by": "DataAgent",
                            "evidence": ["LLM 归因：结算标签脚本误刷导致分润比例应用错误"],
                        },
                    ],
                }},
            },
        ],
    }

    result = await agent.reply(Msg(name="user", content=json.dumps(payload, ensure_ascii=False), role="user"))
    data = json.loads(result.content)

    assert data["status"] == "success"
    assert data["root_cause"] == "结算标签脚本误刷导致分润比例应用错误"
    assert data["responsible_party"] == "数据侧（标签脚本）"
