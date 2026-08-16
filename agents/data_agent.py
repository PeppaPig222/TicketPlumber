#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DataAgent：负责数据一致性、跨表校验与标签冲突诊断。
"""
import json
import logging
from typing import Dict, List, Optional

from agentscope.message import Msg

from agents.diagnosis_agent_base import BaseDiagnosisAgent
from agents.diagnosis_agents import _context_value

logger = logging.getLogger(__name__)


class DataAgent(BaseDiagnosisAgent):
    """数据视角的专业诊断 Agent。"""

    allowed_skills = {
        "get_order_detail",
        "get_asset_pool",
        "get_asset_allocation",
        "get_bill_detail",
        "get_settlement_rule",
        "order_data_path",
        "ValidateFrontendState",
        "GetBillCalculation",
        "settlement_contract_path",
    }

    # 工具白名单（执行层物理隔离）：只允许数据侧排查工具
    allowed_tools = {
        "check_data",
        "trace_api",
        "query_asset",
        "query_merchant",
        "query_settlement",
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
            skill_names = ["get_bill_detail", "get_settlement_rule", "GetBillCalculation", "settlement_contract_path"]
        else:
            skill_names = ["get_order_detail", "order_data_path", "ValidateFrontendState"]

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
        snapshot = await self._execute_tool("check_data", order_id=order_id)
        logs = await self._execute_tool("trace_api", api_path="/api/refund/callback", order_id=order_id)
        inconsistencies = snapshot.get("inconsistencies", [])
        has_timeout = any(item.get("status_code") == 505 for item in logs.get("data", []))
        conflict = bool(inconsistencies or has_timeout)
        summary = "支付表与订单表状态不一致" if inconsistencies else "未发现跨表不一致"
        evidence = [snapshot.get("verdict", "未完成跨表校验")]
        if has_timeout:
            evidence.append("发现订单状态同步超时")

        # 受控 LLM 归因：规则已发现冲突，LLM 在候选集内判别主因（不改变 inconsistency_found）
        hypothesis = None
        if conflict:
            attribution = await self._llm_attribution(
                {"inconsistencies": inconsistencies, "has_timeout": has_timeout},
                {"cross_table_mismatch", "callback_timeout", "both"},
            )
            if attribution:
                explanation = attribution.get("explanation") or ""
                evidence.append(f"LLM 归因：{explanation}")
                if explanation:
                    summary = explanation
                hypothesis = self._build_attribution_hypothesis(attribution, summary)

        return self._response(
            status="success",
            summary=summary,
            evidence=evidence,
            next_actions=["触发交叉验证与归属判定"] if conflict else ["可直接结束诊断"],
            recommended_skills=["check_data", "trace_api"],
            tools_called=["check_data", "trace_api"],
            data_path_detail={
                "snapshot": snapshot.get("data", {}),
                "inconsistencies": inconsistencies,
                "logs": logs.get("data", []),
            },
            inconsistency_found=conflict,
            path_verdict=summary,
            hypothesis=hypothesis,
        )

    async def _asset_round_two(self, context: Dict) -> Msg:
        merchant_id = _context_value(context, "merchant_id")
        asset_result = await self._execute_tool("query_asset", merchant_id=merchant_id)
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
        merchant = await self._execute_tool("query_merchant", merchant_id=merchant_id)
        settlement = await self._execute_tool("query_settlement", merchant_id=merchant_id)
        merchant_data = merchant.get("data", {}) if merchant.get("status") == "success" else {}
        settlement_data = settlement.get("data", {}) if settlement.get("status") == "success" else {}
        ratio_mismatch = settlement_data.get("actual_ratio") != settlement_data.get("settlement_ratio")
        tag_mismatch = merchant_data.get("label") and settlement_data.get("contract_type") and merchant_data.get("label") != settlement_data.get("contract_type")
        conflict = bool(ratio_mismatch or tag_mismatch)
        summary = "结算标签与比例存在不一致" if conflict else "未发现明显规则与标签冲突"
        evidence = [
            f"商户标签 {merchant_data.get('label', '未知')}",
            f"合同类型 {settlement_data.get('contract_type', '未知')}",
            f"实际比例 {settlement_data.get('actual_ratio', '未知')}",
        ]

        # 受控 LLM 归因：规则已发现冲突，LLM 在候选集内判别主因（不改变 inconsistency_found）
        hypothesis = None
        if conflict:
            attribution = await self._llm_attribution(
                {"ratio_mismatch": ratio_mismatch, "tag_mismatch": tag_mismatch},
                {"ratio_mismatch", "label_conflict", "both"},
            )
            if attribution:
                explanation = attribution.get("explanation") or ""
                evidence.append(f"LLM 归因：{explanation}")
                if explanation:
                    summary = explanation
                hypothesis = self._build_attribution_hypothesis(attribution, summary)

        return self._response(
            status="success",
            summary=summary,
            evidence=evidence,
            next_actions=["交给 ResolutionAgent 汇总归因"],
            recommended_skills=["query_merchant", "query_settlement"],
            tools_called=["query_merchant", "query_settlement"],
            data_path_detail={
                "merchant": merchant_data,
                "settlement": settlement_data,
            },
            inconsistency_found=conflict,
            path_verdict=summary,
            hypothesis=hypothesis,
        )

    def _attribution_prompt(self, signals: Dict) -> str:
        """构造受控归因 prompt：规则已发现冲突，LLM 在候选集内判别主因。"""
        return (
            "你是工单诊断的数据一致性 Agent。规则校验已发现数据冲突，"
            "需要你在候选集内判别主因类型。注意：你不做工具选择、不改变流程，"
            "只负责对已查到的结构化结果做归因解读。\n\n"
            f"结构化信号：{json.dumps(signals, ensure_ascii=False)}\n\n"
            "conflict_type 必须从候选集选一个，不得自造：\n"
            "  订单/退款场景：cross_table_mismatch | callback_timeout | both\n"
            "  结算场景：ratio_mismatch | label_conflict | both\n\n"
            "只输出 JSON，不要输出其他内容，格式：\n"
            '{"conflict_type": "候选值", "confidence": 0.0, "explanation": "归因解释"}'
        )

    @classmethod
    def _parse_attribution(cls, raw: str, allowed_types: set) -> Optional[Dict]:
        """解析 LLM 归因 JSON，conflict_type 必须命中候选集，否则丢弃（受控边界）。"""
        data = cls._parse_json(raw)
        if not data or not data.get("conflict_type"):
            return None
        if data["conflict_type"] not in allowed_types:
            logger.warning(
                "LLM attribution conflict_type out of allowed set",
                extra={"agent": cls.__name__, "conflict_type": data["conflict_type"]},
            )
            return None
        return data

    async def _llm_attribution(self, signals: Dict, allowed_types: set) -> Optional[Dict]:
        """受控归因：规则已发现冲突后，LLM 在候选集内判别主因。

        与 CodeAgent 的探索型 LLM 不同，此处 LLM 无工具选择权、无流程跳转权，
        只是「解读器」——对规则已查到的结构化信号做归因，越界候选集则丢弃。
        """
        if not self._autonomy_enabled():
            return None
        raw = await self._call_llm([{"role": "user", "content": self._attribution_prompt(signals)}])
        if not raw:
            return None
        return self._parse_attribution(raw, allowed_types)

    def _build_attribution_hypothesis(self, attribution: Dict, fallback_summary: str) -> Dict:
        """把受控归因结论提升为黑板假设（type 固定为数据侧证据维度 db_state）。"""
        explanation = attribution.get("explanation") or fallback_summary
        return {
            "type": "db_state",
            "detail": explanation,
            "status": "pending",
            "proposed_by": "DataAgent",
            "evidence": [explanation],
        }

    async def _follow_up(self, context: Dict) -> Msg:
        scenario = context.get("scenario")
        # 只验证本 Agent 能力范围内的 db_state 假设（路由映射 = 验证能力映射）
        db_hypotheses = [
            h for h in self._pending_hypotheses(context) if h.get("type") == "db_state"
        ]

        summary = "数据侧复核后确认前序异常结论"
        if scenario == "settlement_amount_mismatch":
            summary = "数据侧复核后确认标签与规则冲突仍然存在"

        resolved = []
        tools_called: List[str] = []
        evidence: List[str] = []

        if db_hypotheses:
            if scenario == "settlement_amount_mismatch":
                merchant_id = _context_value(context, "merchant_id")
                merchant = await self._execute_tool("query_merchant", merchant_id=merchant_id)
                settlement = await self._execute_tool("query_settlement", merchant_id=merchant_id)
                merchant_data = merchant.get("data", {}) if merchant.get("status") == "success" else {}
                settlement_data = settlement.get("data", {}) if settlement.get("status") == "success" else {}
                ratio_mismatch = settlement_data.get("actual_ratio") != settlement_data.get("settlement_ratio")
                tag_mismatch = (
                    merchant_data.get("label")
                    and settlement_data.get("contract_type")
                    and merchant_data.get("label") != settlement_data.get("contract_type")
                )
                conflict = bool(ratio_mismatch or tag_mismatch)
                tools_called = ["query_merchant", "query_settlement"]
                for hyp in db_hypotheses:
                    msg = f"验证假设[{hyp.get('detail')}]：标签/比例冲突 {'仍存在' if conflict else '已消失'}"
                    evidence.append(msg)
                    resolved.append(self._resolved_hypothesis(hyp, conflict, [msg]))
                summary = "数据侧复核后确认标签与规则冲突仍存在" if conflict else "数据侧复核后未再发现标签与规则冲突"
            else:
                order_id = _context_value(context, "order_id")
                snapshot = await self._execute_tool("check_data", order_id=order_id)
                logs = await self._execute_tool("trace_api", api_path="/api/refund/callback", order_id=order_id)
                inconsistencies = snapshot.get("inconsistencies", [])
                has_timeout = any(item.get("status_code") == 505 for item in logs.get("data", []))
                conflict = bool(inconsistencies or has_timeout)
                tools_called = ["check_data", "trace_api"]
                for hyp in db_hypotheses:
                    msg = f"验证假设[{hyp.get('detail')}]：跨表不一致/超时 {'仍存在' if conflict else '已消失'}"
                    evidence.append(msg)
                    resolved.append(self._resolved_hypothesis(hyp, conflict, [msg]))
                summary = "数据侧复核后确认跨表不一致仍存在" if conflict else "数据侧复核后未再发现跨表不一致"

        return self._response(
            status="success",
            summary=summary,
            evidence=evidence or [summary],
            next_actions=["等待 ResolutionAgent 汇总"],
            recommended_skills=tools_called,
            tools_called=tools_called,
            path_verdict=summary,
            hypotheses=resolved or None,
        )
