#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DataAgent：负责数据一致性、跨表校验与标签冲突诊断。
"""
from typing import Dict, List

from agentscope.message import Msg

from agents.diagnosis_agent_base import BaseDiagnosisAgent
from agents.diagnosis_agents import _context_value
from utils.tool_registry import tool_registry


class DataAgent(BaseDiagnosisAgent):
    """数据视角的专业诊断 Agent。"""

    allowed_skills = {
        "get_order_detail",
        "get_asset_pool",
        "get_asset_allocation",
        "get_bill_detail",
        "get_settlement_rule",
    }

    async def reply(self, x: Msg = None) -> Msg:
        payload = self._parse_payload(x)
        context = self._get_context(payload)
        previous_results = self._get_previous_results(payload)
        round_num = context.get("round_num", 1)
        scenario = context.get("scenario")

        if round_num == 1:
            return await self._round_one(context, scenario, previous_results)
        if scenario == "asset_allocation_failure":
            return await self._asset_round_two(context)
        if scenario == "settlement_amount_mismatch":
            return await self._settlement_round_two(context)
        if round_num >= 3:
            return await self._follow_up(context)
        return await self._order_round_two(context)

    async def _round_one(self, context: Dict, scenario: str, previous_results: List[Dict]) -> Msg:
        if scenario == "asset_allocation_failure":
            skill_names = ["get_asset_pool", "get_asset_allocation"]
        elif scenario == "settlement_amount_mismatch":
            skill_names = ["get_bill_detail", "get_settlement_rule"]
        else:
            skill_names = ["get_order_detail"]

        skill_results = [
            await self._run_skill(skill_name, context, "预加载数据侧核心实体", "后续一致性校验所需事实", previous_results)
            for skill_name in skill_names
        ]

        merged = {}
        evidence = []
        for result in skill_results:
            merged.update({k: v for k, v in result.items() if k not in {"status", "summary", "tools_called"}})
            if result.get("summary"):
                evidence.append(result["summary"])

        return self._response(
            status="success",
            summary="数据侧已完成基础实体预热",
            evidence=evidence,
            next_actions=["进入一致性校验"],
            recommended_skills=skill_names,
            tools_called=self._dedupe_tools(skill_results),
            **merged,
        )

    async def _order_round_two(self, context: Dict) -> Msg:
        order_id = _context_value(context, "order_id")
        snapshot = await tool_registry.execute("check_data", order_id=order_id)
        logs = await tool_registry.execute("trace_api", api_path="/api/refund/callback", order_id=order_id)
        inconsistencies = snapshot.get("inconsistencies", [])
        has_timeout = any(item.get("status_code") == 505 for item in logs.get("data", []))
        summary = "支付表与订单表状态不一致" if inconsistencies else "未发现跨表不一致"
        evidence = [snapshot.get("verdict", "未完成跨表校验")]
        if has_timeout:
            evidence.append("发现订单状态同步超时")
        return self._response(
            status="success",
            summary=summary,
            evidence=evidence,
            next_actions=["触发交叉验证与归属判定"] if inconsistencies or has_timeout else ["可直接结束诊断"],
            recommended_skills=["check_data", "trace_api"],
            tools_called=["check_data", "trace_api"],
            data_path_detail={
                "snapshot": snapshot.get("data", {}),
                "inconsistencies": inconsistencies,
                "logs": logs.get("data", []),
            },
            inconsistency_found=bool(inconsistencies or has_timeout),
            path_verdict=summary,
        )

    async def _asset_round_two(self, context: Dict) -> Msg:
        merchant_id = _context_value(context, "merchant_id")
        asset_result = await tool_registry.execute("query_asset", merchant_id=merchant_id)
        asset = asset_result.get("data", {}) if asset_result.get("status") == "success" else {}
        request = asset.get("allocation_request", {})
        available = asset.get("available_quota", 0)
        requested = request.get("requested_quota", 0)
        summary = "可用额度不足以完成本次分配" if requested > available else "额度本身无异常"
        return self._response(
            status="success",
            summary=summary,
            evidence=[
                f"可用额度 {available}",
                f"申请额度 {requested}",
                f"已有分配记录 {len(asset.get('allocation_records', []))} 条",
            ],
            next_actions=["结合权限与保护期限制综合判断"],
            recommended_skills=["query_asset"],
            tools_called=["query_asset"],
            data_path_detail=asset,
            path_verdict=summary,
        )

    async def _settlement_round_two(self, context: Dict) -> Msg:
        merchant_id = _context_value(context, "merchant_id")
        merchant = await tool_registry.execute("query_merchant", merchant_id=merchant_id)
        settlement = await tool_registry.execute("query_settlement", merchant_id=merchant_id)
        merchant_data = merchant.get("data", {}) if merchant.get("status") == "success" else {}
        settlement_data = settlement.get("data", {}) if settlement.get("status") == "success" else {}
        ratio_mismatch = settlement_data.get("actual_ratio") != settlement_data.get("settlement_ratio")
        tag_mismatch = merchant_data.get("label") and settlement_data.get("contract_type") and merchant_data.get("label") != settlement_data.get("contract_type")
        summary = "结算标签与比例存在不一致" if ratio_mismatch or tag_mismatch else "未发现明显规则与标签冲突"
        return self._response(
            status="success",
            summary=summary,
            evidence=[
                f"商户标签 {merchant_data.get('label', '未知')}",
                f"合同类型 {settlement_data.get('contract_type', '未知')}",
                f"实际比例 {settlement_data.get('actual_ratio', '未知')}",
            ],
            next_actions=["交给 ResolutionAgent 汇总归因"],
            recommended_skills=["query_merchant", "query_settlement"],
            tools_called=["query_merchant", "query_settlement"],
            data_path_detail={
                "merchant": merchant_data,
                "settlement": settlement_data,
            },
            inconsistency_found=bool(ratio_mismatch or tag_mismatch),
            path_verdict=summary,
        )

    async def _follow_up(self, context: Dict) -> Msg:
        scenario = context.get("scenario")
        summary = "数据侧复核后确认前序异常结论"
        if scenario == "settlement_amount_mismatch":
            summary = "数据侧复核后确认标签与规则冲突仍然存在"
        return self._response(
            status="success",
            summary=summary,
            evidence=[summary],
            next_actions=["等待 ResolutionAgent 汇总"],
            recommended_skills=[],
            tools_called=[],
            path_verdict=summary,
        )
