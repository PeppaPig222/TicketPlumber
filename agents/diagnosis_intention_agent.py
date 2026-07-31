#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
工单诊断场景下的规则型 IntentionAgent。
"""
import json
import re
from typing import Any, Dict, List

from agentscope.message import Msg


class DiagnosisIntentionAgent:
    """基于工单上下文生成每一轮的 Skill 调度计划。"""

    def __init__(self, name: str = "DiagnosisIntentionAgent"):
        self.name = name

    async def reply(self, x: Msg = None) -> Msg:
        payload = self._parse_payload(x)
        round_num = payload.get("round_num", 1)
        query = payload.get("query", "")
        ticket = payload.get("ticket", {}) or {}
        collected_data = payload.get("collected_data", {}) or {}

        key_entities = self._build_entities(query, ticket, collected_data)
        scenario = key_entities.get("scenario")
        issue_type = key_entities.get("issue_type")

        intention = {
            "intent": self._intent_name(round_num),
            "reasoning": self._build_reasoning(round_num, scenario, issue_type),
            "intents": [
                {
                    "type": "ticket_diagnosis",
                    "confidence": 0.99 if scenario else 0.55,
                    "description": "商户工单智能诊断",
                    "reason": "检测到工单编号、商户投诉或异常排查语义",
                }
            ],
            "key_entities": key_entities,
            "rewritten_query": query.strip(),
            "scenario": scenario,
            "ticket": ticket,
            "ticket_id": key_entities.get("ticket_id"),
            "issue_type": issue_type,
            "round_num": round_num,
            "query": query,
            "collected_data": collected_data,
            "agent_schedule": self._build_schedule(scenario, round_num),
        }
        return Msg(
            name=self.name,
            content=json.dumps(intention, ensure_ascii=False),
            role="assistant",
        )

    def _parse_payload(self, msg: Msg) -> Dict[str, Any]:
        if not msg or not getattr(msg, "content", None):
            return {}
        if isinstance(msg.content, dict):
            return msg.content
        try:
            return json.loads(msg.content)
        except json.JSONDecodeError:
            return {"query": str(msg.content)}

    def _build_entities(self, query: str, ticket: Dict[str, Any], collected_data: Dict[str, Any]) -> Dict[str, Any]:
        facts = collected_data.get("facts", {}) or {}
        issue_type = ticket.get("issue_type") or facts.get("issue_type") or self._detect_issue_type(query)
        scenario = self._scenario_from_issue(issue_type, query)

        merchant_id = ticket.get("merchant_id") or facts.get("merchant_id") or self._extract(query, r"\b\d{4,6}\b")
        order_id = ticket.get("order_id") or facts.get("order_id") or self._extract(query, r"ORD-\d+")
        ticket_id = ticket.get("ticket_id") or facts.get("ticket_id") or self._extract(query, r"WO-\d{8}-\d{4}")

        entities = {
            "ticket_id": ticket_id,
            "merchant_id": merchant_id,
            "order_id": order_id,
            "issue_type": issue_type,
            "scenario": scenario,
        }
        if ticket:
            entities["ticket_description"] = ticket.get("description")
        return entities

    def _intent_name(self, round_num: int) -> str:
        if round_num == 1:
            return "ticket_diagnosis"
        if round_num == 2:
            return "deep_diagnosis"
        return "cross_validate_and_resolve"

    def _build_reasoning(self, round_num: int, scenario: str, issue_type: str) -> str:
        stage_text = {
            1: "先做基础信息收集，确认工单涉及的核心实体与历史案例。",
            2: "进入深度诊断，按场景拆成多条并行排查路径。",
            3: "对异常证据做交叉验证并输出归属判定。",
        }
        scenario_text = {
            "order_status_anomaly": "订单状态异常场景",
            "asset_allocation_failure": "资产分配失败场景",
            "settlement_amount_mismatch": "结算金额不符场景",
        }
        return f"{stage_text.get(round_num, '继续诊断')} 当前识别为 {scenario_text.get(scenario, '未知工单场景')}，问题类型：{issue_type or '待补充'}。"

    def _build_schedule(self, scenario: str, round_num: int) -> List[Dict[str, Any]]:
        schedules = {
            "order_status_anomaly": {
                1: [
                    self._task("get_order_detail", 1, "获取订单当前状态", "订单基础详情"),
                    self._task("get_order_timeline", 1, "重建订单时间线", "订单关键事件序列"),
                    self._task("get_merchant_profile", 1, "查询商户基础信息", "商户画像"),
                    self._task("search_history_ticket", 1, "检索相似历史工单", "历史处理经验"),
                ],
                2: [
                    self._task("order_code_path", 1, "排查技术链路", "前后端代码与接口状态"),
                    self._task("order_operation_path", 1, "排查用户操作", "用户是否误操作"),
                    self._task("order_data_path", 1, "排查数据一致性", "跨表比对与回调链路"),
                ],
                3: [
                    self._task("search_policy_faq", 1, "补充知识库处理建议", "FAQ 经验"),
                    self._task("search_history_ticket", 1, "复核历史案例", "同类工单结果"),
                    self._task("root_cause_resolver", 2, "汇总结论并判责", "根因与建议"),
                ],
            },
            "asset_allocation_failure": {
                1: [
                    self._task("get_asset_pool", 1, "获取资产池概况", "可用额度"),
                    self._task("get_asset_allocation", 1, "获取分配记录", "当前分配情况"),
                    self._task("get_user_binding", 1, "获取用户绑定信息", "绑定状态"),
                    self._task("search_history_ticket", 1, "检索类似工单", "历史经验"),
                ],
                2: [
                    self._task("asset_availability_path", 1, "检查额度限制", "额度是否足够"),
                    self._task("asset_binding_path", 1, "检查绑定与保护期", "用户归属限制"),
                    self._task("asset_permission_path", 1, "检查操作者权限", "权限限制"),
                ],
                3: [],
            },
            "settlement_amount_mismatch": {
                1: [
                    self._task("get_merchant_contract", 1, "读取合同信息", "合同与分润比例"),
                    self._task("get_bill_detail", 1, "读取账单信息", "账单金额"),
                    self._task("get_settlement_rule", 1, "读取结算规则", "实际规则"),
                    self._task("search_history_ticket", 1, "检索类似工单", "历史处理经验"),
                ],
                2: [
                    self._task("settlement_contract_path", 1, "检查合同与商户标签", "合同和商户信息"),
                    self._task("settlement_calculation_path", 1, "检查计算结果", "比例与金额一致性"),
                    self._task("settlement_timeline_path", 1, "检查结算时间线", "标签变更和流程痕迹"),
                ],
                3: [
                    self._task("search_policy_faq", 1, "补充处理建议", "FAQ 处理建议"),
                    self._task("search_history_ticket", 1, "复核历史案例", "相似工单结果"),
                    self._task("root_cause_resolver", 2, "汇总结论并判责", "根因与建议"),
                ],
            },
        }
        return schedules.get(scenario, {}).get(round_num, [])

    def _detect_issue_type(self, query: str) -> str:
        if "结算" in query:
            return "结算金额不符"
        if "资产" in query or "免时长" in query or "分配" in query:
            return "资产分配失败"
        if "订单" in query:
            return "订单状态异常"
        return "工单诊断"

    def _scenario_from_issue(self, issue_type: str, query: str) -> str:
        source = f"{issue_type or ''} {query}"
        if "结算" in source:
            return "settlement_amount_mismatch"
        if "资产" in source or "免时长" in source or "分配" in source:
            return "asset_allocation_failure"
        if "订单" in source:
            return "order_status_anomaly"
        return "order_status_anomaly"

    def _extract(self, text: str, pattern: str) -> str:
        matched = re.search(pattern, text or "")
        return matched.group(0) if matched else ""

    def _task(self, agent_name: str, priority: int, reason: str, expected_output: str) -> Dict[str, Any]:
        return {
            "agent_name": agent_name,
            "priority": priority,
            "reason": reason,
            "expected_output": expected_output,
        }
