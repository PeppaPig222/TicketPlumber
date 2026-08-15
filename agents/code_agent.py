#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CodeAgent：负责接口链路、配置与代码侧排查。
"""
from typing import Dict, List

from agentscope.message import Msg

from agents.diagnosis_agent_base import BaseDiagnosisAgent
from agents.diagnosis_agents import _context_value
from utils.tool_registry import tool_registry


class CodeAgent(BaseDiagnosisAgent):
    """技术链路视角的专业诊断 Agent。"""

    allowed_skills = {
        "get_order_detail",
        "get_order_timeline",
        "get_asset_pool",
        "get_asset_allocation",
        "get_merchant_contract",
        "get_bill_detail",
        "get_settlement_rule",
        "GetOrderRefund",
        "ReconstructTimeline",
        "GetBillingConfig",
        "GetBillCalculation",
        "GetReconciliation",
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
            return await self._follow_up(scenario, previous_results)
        return await self._order_round_two(context)

    async def _round_one(self, context: Dict, scenario: str, previous_results: List[Dict]) -> Msg:
        if scenario == "asset_allocation_failure":
            skill_names = ["get_asset_pool", "get_asset_allocation", "GetBillingConfig"]
        elif scenario == "settlement_amount_mismatch":
            skill_names = ["get_merchant_contract", "get_bill_detail", "get_settlement_rule", "GetBillCalculation", "GetReconciliation"]
        else:
            skill_names = ["get_order_detail", "get_order_timeline", "GetOrderRefund", "ReconstructTimeline"]

        skill_results = [
            await self._run_skill(skill_name, context, "补全代码侧基础上下文", "结构化基础事实", previous_results)
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
            summary="代码侧已完成基础事实收集",
            evidence=evidence,
            next_actions=["进入深度链路排查"],
            recommended_skills=skill_names,
            tools_called=self._dedupe_tools(skill_results),
            **merged,
        )

    async def _order_round_two(self, context: Dict) -> Msg:
        order_id = _context_value(context, "order_id")
        merchant_id = _context_value(context, "merchant_id")
        logs = await tool_registry.execute("trace_api", api_path="/api/refund/callback", order_id=order_id)
        config = await tool_registry.execute("check_config", merchant_id=merchant_id)
        log_items = logs.get("data", [])
        config_data = config.get("data", {})
        has_success = any(item.get("status_code") == 200 for item in log_items)

        evidence = [
            f"退款回调日志 {len(log_items)} 条",
            "接口链路存在 200 成功回调" if has_success else "未观察到成功回调",
            "退款功能开关开启" if config_data.get("refund_enabled") else "退款开关异常",
        ]
        verdict = "代码链路无明显异常" if has_success and config_data.get("refund_enabled") else "代码链路存在可疑点"
        return self._response(
            status="success",
            summary=verdict,
            evidence=evidence,
            next_actions=["交由数据侧继续做一致性校验"],
            recommended_skills=["trace_api", "check_config"],
            tools_called=["trace_api", "check_config"],
            code_path_detail={
                "logs": log_items,
                "config": config_data,
            },
            path_verdict=verdict,
        )

    async def _asset_round_two(self, context: Dict) -> Msg:
        merchant_id = _context_value(context, "merchant_id")
        config = await tool_registry.execute("check_config", merchant_id=merchant_id)
        permissions = (config.get("data") or {}).get("permissions", [])
        can_allocate = "asset_allocate" in permissions
        summary = "代码侧未发现系统开关异常" if can_allocate else "操作者侧权限配置可能不足"
        return self._response(
            status="success",
            summary=summary,
            evidence=[
                "商户配置读取成功",
                f"权限点包含 {', '.join(permissions) if permissions else '无'}",
            ],
            next_actions=["继续检查用户绑定与保护期"],
            recommended_skills=["check_config"],
            tools_called=["check_config"],
            code_path_detail={"config": config.get("data", {})},
            path_verdict=summary,
        )

    async def _settlement_round_two(self, context: Dict) -> Msg:
        merchant_id = _context_value(context, "merchant_id")
        settlement = await tool_registry.execute("query_settlement", merchant_id=merchant_id)
        data = settlement.get("data", {}) if settlement.get("status") == "success" else {}
        inconsistent = data.get("actual_ratio") != data.get("settlement_ratio")
        summary = "结算计算链路命中比例不一致" if inconsistent else "结算计算链路正常"
        return self._response(
            status="success",
            summary=summary,
            evidence=[
                f"合同比例 {data.get('settlement_ratio', '未知')}",
                f"实际比例 {data.get('actual_ratio', '未知')}",
                f"账单总额 {data.get('bill_total', '未知')}",
            ],
            next_actions=["继续核对标签与时间线变更"],
            recommended_skills=["query_settlement"],
            tools_called=["query_settlement"],
            code_path_detail={"settlement": data},
            path_verdict=summary,
            inconsistency_found=bool(inconsistent),
        )

    async def _follow_up(self, scenario: str, previous_results: List[Dict]) -> Msg:
        data_result = self._find_previous_result(previous_results, "DataAgent")
        code_result = self._find_previous_result(previous_results, "CodeAgent")
        evidence = list(code_result.get("evidence", []))
        if data_result.get("path_verdict"):
            evidence.append(f"数据侧补充结论：{data_result.get('path_verdict')}")
        summary = "代码侧复核后维持原判断，继续交由 ResolutionAgent 汇总"
        if scenario == "settlement_amount_mismatch":
            summary = "代码侧复核结算链路后，确认问题更偏向规则或数据标签"
        return self._response(
            status="success",
            summary=summary,
            evidence=evidence,
            next_actions=["等待 ResolutionAgent 汇总判责"],
            recommended_skills=[],
            tools_called=[],
            path_verdict=summary,
        )
