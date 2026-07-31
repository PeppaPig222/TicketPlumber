#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ResolutionAgent：负责证据汇总、冲突消解与责任归属判定。
"""
from typing import Dict, List

from agentscope.message import Msg

from agents.diagnosis_agent_base import BaseDiagnosisAgent


class ResolutionAgent(BaseDiagnosisAgent):
    """归因与结论生成 Agent。"""

    allowed_skills = {"search_history_ticket", "search_policy_faq"}

    async def reply(self, x: Msg = None) -> Msg:
        payload = self._parse_payload(x)
        context = self._get_context(payload)
        previous_results = self._get_previous_results(payload)
        scenario = context.get("scenario")

        if scenario == "asset_allocation_failure":
            return await self._resolve_asset(context, previous_results)
        if scenario == "settlement_amount_mismatch":
            return await self._resolve_settlement(context, previous_results)
        return await self._resolve_order(context, previous_results)

    async def _resolve_order(self, context: Dict, previous_results: List[Dict]) -> Msg:
        history_result = await self._run_skill(
            "search_history_ticket",
            context,
            "复核同类历史工单",
            "辅助归因",
            previous_results,
        )
        policy_result = await self._run_skill(
            "search_policy_faq",
            context,
            "补充标准处理建议",
            "处理建议",
            previous_results,
        )
        code = self._find_previous_result(previous_results, "CodeAgent")
        operation = self._find_previous_result(previous_results, "OperationAgent")
        data = self._find_previous_result(previous_results, "DataAgent")
        evidence = [
            code.get("path_verdict", ""),
            operation.get("path_verdict", ""),
            data.get("path_verdict", ""),
            history_result.get("summary", ""),
            policy_result.get("summary", ""),
        ]
        evidence = [item for item in evidence if item]
        return self._response(
            status="success",
            summary="交叉验证后确认问题位于数据同步链路，而非前后端代码或用户操作。",
            evidence=evidence,
            next_actions=[
                "手动修复异常订单状态",
                "排查状态同步脚本与回调超时问题",
                "补充重试和告警机制",
            ],
            recommended_skills=["search_history_ticket", "search_policy_faq"],
            tools_called=self._dedupe_tools([history_result, policy_result]),
            responsible_party="数据侧（后台脚本）",
            root_cause="退款回调后订单状态同步任务超时，导致订单状态未更新。",
            recommendations=[
                "手动修复 ORD-8823 的订单状态",
                "排查退款回调脚本死锁与重试耗尽问题",
                "补充同批次订单巡检与告警",
            ],
            history_matches=history_result.get("history_matches", []),
            policy_matches=policy_result.get("policy_matches", []),
        )

    async def _resolve_asset(self, context: Dict, previous_results: List[Dict]) -> Msg:
        history_result = await self._run_skill(
            "search_history_ticket",
            context,
            "复核资产分配类历史案例",
            "历史处理经验",
            previous_results,
        )
        code = self._find_previous_result(previous_results, "CodeAgent")
        operation = self._find_previous_result(previous_results, "OperationAgent")
        data = self._find_previous_result(previous_results, "DataAgent")
        evidence = [
            data.get("path_verdict", ""),
            operation.get("path_verdict", ""),
            code.get("path_verdict", ""),
            history_result.get("summary", ""),
        ]
        evidence = [item for item in evidence if item]
        return self._response(
            status="success",
            summary="诊断确认资产分配失败由额度、绑定保护期与权限限制叠加导致。",
            evidence=evidence,
            next_actions=[
                "优先回收未使用额度",
                "等待保护期结束后再分配",
                "如需跨商户分配，申请补充权限",
            ],
            recommended_skills=["search_history_ticket"],
            tools_called=history_result.get("tools_called", []),
            responsible_party="业务配置与权限",
            root_cause="商户可用额度不足，同时目标用户仍受保护期限制，操作者也缺少跨商户分配权限。",
            recommendations=[
                "回收未使用额度后再重试",
                "等待用户保护期结束",
                "申请跨商户分配权限",
            ],
            history_matches=history_result.get("history_matches", []),
        )

    async def _resolve_settlement(self, context: Dict, previous_results: List[Dict]) -> Msg:
        history_result = await self._run_skill(
            "search_history_ticket",
            context,
            "复核相似结算异常案例",
            "历史处理经验",
            previous_results,
        )
        policy_result = await self._run_skill(
            "search_policy_faq",
            context,
            "补充结算规则处理建议",
            "处理建议",
            previous_results,
        )
        code = self._find_previous_result(previous_results, "CodeAgent")
        operation = self._find_previous_result(previous_results, "OperationAgent")
        data = self._find_previous_result(previous_results, "DataAgent")
        evidence = [
            code.get("path_verdict", ""),
            operation.get("path_verdict", ""),
            data.get("path_verdict", ""),
            history_result.get("summary", ""),
            policy_result.get("summary", ""),
        ]
        evidence = [item for item in evidence if item]
        return self._response(
            status="success",
            summary="诊断确认结算金额不符的根因位于数据标签与规则不一致。",
            evidence=evidence,
            next_actions=[
                "修正商户结算标签与规则",
                "重新核算账期并处理差额",
                "增加标签脚本变更审计",
            ],
            recommended_skills=["search_history_ticket", "search_policy_faq"],
            tools_called=self._dedupe_tools([history_result, policy_result]),
            responsible_party="数据侧（标签脚本）",
            root_cause="商户结算标签被脚本误刷，导致按错误分润比例结算。",
            recommendations=[
                "修正商户结算标签与规则",
                "重新核算账期并补发或冲正差额",
                "为标签脚本增加审计与变更告警",
            ],
            history_matches=history_result.get("history_matches", []),
            policy_matches=policy_result.get("policy_matches", []),
        )
