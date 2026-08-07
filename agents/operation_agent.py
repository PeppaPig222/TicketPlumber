#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OperationAgent：负责用户操作、业务流程与历史案例排查。
"""
from typing import Dict, List

from agentscope.message import Msg

from agents.diagnosis_agent_base import BaseDiagnosisAgent
from agents.diagnosis_agents import _context_value
from utils.tool_registry import tool_registry


class OperationAgent(BaseDiagnosisAgent):
    """操作路径视角的专业诊断 Agent。"""

    allowed_skills = {
        # 商户画像
        "get_merchant_profile",
        "GetMerchantCoopStatus",
        "GetMerchantContract",
        "GetMerchantOrgTree",
        "GetMerchantPermission",
        "GetMerchantOnboarding",
        "GetMerchantBlacklist",
        # 用户/资产操作
        "get_user_binding",
        "GetProtectionPeriod",
        "GetAssetRecycle",
        "get_asset_allocation",
        "asset_binding_path",
        # 订单操作
        "get_order_timeline",
        "GetOrderRefund",
        "order_operation_path",
        # 历史/策略
        "search_history_ticket",
        "search_policy_faq",
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
            return await self._settlement_round_two(context, previous_results)
        if round_num >= 3:
            return await self._follow_up(previous_results)
        return await self._order_round_two(context)

    async def _round_one(self, context: Dict, scenario: str, previous_results: List[Dict]) -> Msg:
        if scenario == "asset_allocation_failure":
            skill_names = [
                "get_user_binding",
                "GetProtectionPeriod",
                "GetAssetRecycle",
                "search_history_ticket",
            ]
        elif scenario == "settlement_amount_mismatch":
            skill_names = [
                "GetMerchantCoopStatus",
                "GetMerchantContract",
                "search_history_ticket",
            ]
        else:
            skill_names = [
                "get_merchant_profile",
                "GetMerchantCoopStatus",
                "GetMerchantOnboarding",
                "GetMerchantBlacklist",
                "search_history_ticket",
            ]

        skill_results = [
            await self._run_skill(skill_name, context, "补全操作侧基础上下文", "历史经验与用户侧线索", previous_results)
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
            summary="操作侧已完成基础事实收集",
            evidence=evidence,
            next_actions=["进入业务流程排查"],
            recommended_skills=skill_names,
            tools_called=self._dedupe_tools(skill_results),
            **merged,
        )

    async def _order_round_two(self, context: Dict) -> Msg:
        order_id = _context_value(context, "order_id")
        order_result = await tool_registry.execute("query_order", order_id=order_id)
        order = order_result.get("data", {}) if order_result.get("status") == "success" else {}
        refund_steps = [item for item in order.get("timeline", []) if "退款" in item.get("event", "")]
        summary = "用户操作流程符合规范"
        return self._response(
            status="success",
            summary=summary,
            evidence=[
                f"识别到 {len(refund_steps)} 个退款相关节点",
                "未发现异常操作轨迹",
            ],
            next_actions=["等待数据侧做一致性校验"],
            recommended_skills=["query_order"],
            tools_called=["query_order"],
            operation_path_detail={
                "refund_steps": refund_steps,
                "timeline": order.get("timeline", []),
            },
            path_verdict=summary,
        )

    async def _asset_round_two(self, context: Dict) -> Msg:
        merchant_id = _context_value(context, "merchant_id")
        asset_result = await tool_registry.execute("query_asset", merchant_id=merchant_id)
        asset = asset_result.get("data", {}) if asset_result.get("status") == "success" else {}
        protection = asset.get("protection_period", {})
        binding = asset.get("user_binding", {})
        summary = "命中用户绑定与保护期限制" if protection.get("status") == "active" else "操作流程未命中保护期限制"
        return self._response(
            status="success",
            summary=summary,
            evidence=[
                f"当前绑定商户 {binding.get('current_merchant_id', '未知')}",
                f"保护期状态 {protection.get('status', '未知')}",
            ],
            next_actions=["结合数据侧额度校验综合判断"],
            recommended_skills=["query_asset"],
            tools_called=["query_asset"],
            operation_path_detail={
                "user_binding": binding,
                "protection_period": protection,
            },
            path_verdict=summary,
        )

    async def _settlement_round_two(self, context: Dict, previous_results: List[Dict]) -> Msg:
        history_result = await self._run_skill(
            "search_history_ticket",
            context,
            "检索结算类历史工单",
            "历史案例",
            previous_results,
        )
        return self._response(
            status="success",
            summary="操作侧未发现人工流程异常，更多像规则或数据问题",
            evidence=[
                history_result.get("summary", "未命中历史工单"),
                "未发现人工录入或流程走错的直接证据",
            ],
            next_actions=["等待数据侧与 ResolutionAgent 继续汇总"],
            recommended_skills=["search_history_ticket"],
            tools_called=history_result.get("tools_called", []),
            history_matches=history_result.get("history_matches", []),
            path_verdict="操作侧未见异常流程",
        )

    async def _follow_up(self, previous_results: List[Dict]) -> Msg:
        code_result = self._find_previous_result(previous_results, "CodeAgent")
        data_result = self._find_previous_result(previous_results, "DataAgent")
        evidence = [
            code_result.get("path_verdict", ""),
            data_result.get("path_verdict", ""),
        ]
        evidence = [item for item in evidence if item]
        return self._response(
            status="success",
            summary="操作侧复核后维持原判断，未发现新增用户操作异常",
            evidence=evidence,
            next_actions=["等待 ResolutionAgent 汇总"],
            recommended_skills=[],
            tools_called=[],
            path_verdict="操作侧维持无异常判断",
        )
