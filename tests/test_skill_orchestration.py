#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证专业 Agent 在 _round_one 中调用了扩展后的原子 Skill。"""
import json
import os
import sys

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from agentscope.message import Msg

from agents.code_agent import CodeAgent
from agents.operation_agent import OperationAgent
from agents.data_agent import DataAgent
from agents.resolution_agent import ResolutionAgent


class TestCodeAgentSkillOrchestration:
    @pytest.mark.asyncio
    async def test_order_scenario_round_one_calls_enriched_skills(self):
        agent = CodeAgent(name="CodeAgent")
        payload = {
            "context": {"scenario": "order_status_anomaly", "round_num": 1, "order_id": "ORD-8823"},
            "previous_results": [],
        }
        result = await agent.reply(Msg(name="user", content=json.dumps(payload), role="user"))
        data = json.loads(result.content)
        recommended = data.get("recommended_skills", [])

        assert "get_order_detail" in recommended
        assert "GetOrderRefund" in recommended
        assert "ReconstructTimeline" in recommended

    @pytest.mark.asyncio
    async def test_asset_scenario_round_one_calls_enriched_skills(self):
        agent = CodeAgent(name="CodeAgent")
        payload = {
            "context": {"scenario": "asset_allocation_failure", "round_num": 1},
            "previous_results": [],
        }
        result = await agent.reply(Msg(name="user", content=json.dumps(payload), role="user"))
        data = json.loads(result.content)
        recommended = data.get("recommended_skills", [])

        assert "get_asset_pool" in recommended
        assert "GetBillingConfig" in recommended

    @pytest.mark.asyncio
    async def test_settlement_scenario_round_one_calls_enriched_skills(self):
        agent = CodeAgent(name="CodeAgent")
        payload = {
            "context": {"scenario": "settlement_amount_mismatch", "round_num": 1, "merchant_id": "2037"},
            "previous_results": [],
        }
        result = await agent.reply(Msg(name="user", content=json.dumps(payload), role="user"))
        data = json.loads(result.content)
        recommended = data.get("recommended_skills", [])

        assert "GetBillCalculation" in recommended
        assert "GetReconciliation" in recommended


class TestOperationAgentSkillOrchestration:
    @pytest.mark.asyncio
    async def test_order_scenario_round_one_calls_merchant_skills(self):
        agent = OperationAgent(name="OperationAgent")
        payload = {
            "context": {"scenario": "order_status_anomaly", "round_num": 1, "merchant_id": "2037"},
            "previous_results": [],
        }
        result = await agent.reply(Msg(name="user", content=json.dumps(payload), role="user"))
        data = json.loads(result.content)
        recommended = data.get("recommended_skills", [])

        assert "get_merchant_profile" in recommended
        assert "GetMerchantCoopStatus" in recommended

    @pytest.mark.asyncio
    async def test_asset_scenario_round_one_calls_protection_skill(self):
        agent = OperationAgent(name="OperationAgent")
        payload = {
            "context": {"scenario": "asset_allocation_failure", "round_num": 1, "merchant_id": "2037"},
            "previous_results": [],
        }
        result = await agent.reply(Msg(name="user", content=json.dumps(payload), role="user"))
        data = json.loads(result.content)
        recommended = data.get("recommended_skills", [])

        assert "GetProtectionPeriod" in recommended
        assert "GetAssetRecycle" in recommended


class TestDataAgentSkillOrchestration:
    @pytest.mark.asyncio
    async def test_order_scenario_round_one_calls_data_path_skills(self):
        agent = DataAgent(name="DataAgent")
        payload = {
            "context": {"scenario": "order_status_anomaly", "round_num": 1, "order_id": "ORD-8823"},
            "previous_results": [],
        }
        result = await agent.reply(Msg(name="user", content=json.dumps(payload), role="user"))
        data = json.loads(result.content)
        recommended = data.get("recommended_skills", [])

        assert "order_data_path" in recommended
        assert "ValidateFrontendState" in recommended

    @pytest.mark.asyncio
    async def test_settlement_scenario_round_one_calls_calculation_skills(self):
        agent = DataAgent(name="DataAgent")
        payload = {
            "context": {"scenario": "settlement_amount_mismatch", "round_num": 1, "merchant_id": "2037"},
            "previous_results": [],
        }
        result = await agent.reply(Msg(name="user", content=json.dumps(payload), role="user"))
        data = json.loads(result.content)
        recommended = data.get("recommended_skills", [])

        assert "GetBillCalculation" in recommended
        assert "settlement_contract_path" in recommended


class TestResolutionAgentAllowedSkills:
    def test_resolution_agent_allows_root_cause_resolver(self):
        assert "root_cause_resolver" in ResolutionAgent.allowed_skills
        assert "search_history_ticket" in ResolutionAgent.allowed_skills
        assert "search_policy_faq" in ResolutionAgent.allowed_skills
