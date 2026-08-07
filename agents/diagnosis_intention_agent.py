#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
工单诊断场景下的规则型 IntentionAgent。
"""
import json
import re
from typing import Any, Dict, List

from agentscope.message import Msg
from utils.tool_registry import tool_registry


class DiagnosisIntentionAgent:
    """基于工单上下文生成每一轮的专业 Agent 调度计划。"""

    def __init__(self, name: str = "DiagnosisIntentionAgent"):
        self.name = name

    async def reply(self, x: Msg = None) -> Msg:
        payload = self._parse_payload(x)
        round_num = payload.get("round_num", 1)
        query = payload.get("query", "")
        ticket = await self._enrich_ticket(payload.get("ticket", {}) or {}, query)
        collected_data = payload.get("collected_data", {}) or {}
        memory_context = payload.get("memory_context", {}) or {}

        key_entities = self._build_entities(query, ticket, collected_data)
        scenario = key_entities.get("scenario")
        issue_type = key_entities.get("issue_type")

        base_reasoning = self._build_reasoning(round_num, scenario, issue_type)
        enriched_reasoning = self._enrich_reasoning_with_memory(base_reasoning, memory_context)

        intention = {
            "intent": self._intent_name(round_num),
            "reasoning": enriched_reasoning,
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

    def _enrich_reasoning_with_memory(
        self, base_reasoning: str, memory_context: Dict[str, Any]
    ) -> str:
        """把记忆上下文追加到 reasoning，仅用于丰富意图理解，不改动 scenario/调度。"""
        extras: List[str] = []

        recent_dialogue = memory_context.get("recent_dialogue", "")
        if recent_dialogue:
            extras.append(f"近期对话：{recent_dialogue}")

        merchant_profile = memory_context.get("merchant_profile", "")
        if merchant_profile:
            extras.append(f"商户画像：{merchant_profile}")

        similar_patterns = memory_context.get("similar_patterns", [])
        if similar_patterns:
            summaries = [
                p.get("summary", "") or p.get("pattern", "")
                for p in similar_patterns[:2]
            ]
            extras.append(f"相似历史模式：{'; '.join(s for s in summaries if s)}")

        if not extras:
            return base_reasoning
        return base_reasoning + " [记忆上下文] " + " | ".join(extras)

    def _build_schedule(self, scenario: str, round_num: int) -> List[Dict[str, Any]]:
        schedules = {
            "generic_ticket_diagnosis": {
                1: [
                    self._task("OperationAgent", 1, "尝试补全商户上下文与历史经验", "商户画像与历史案例"),
                    self._task("DataAgent", 1, "预热基础实体", "基础数据事实"),
                ],
                2: [
                    self._task("CodeAgent", 1, "从技术链路视角补充诊断", "技术侧线索"),
                    self._task("OperationAgent", 1, "从业务流程视角补充诊断", "操作侧线索"),
                    self._task("DataAgent", 1, "从数据视角补充诊断", "数据侧线索"),
                    self._task("ResolutionAgent", 2, "汇总证据并给出初步结论", "归因建议"),
                ],
                3: [],
            },
            "order_status_anomaly": {
                1: [
                    self._task("CodeAgent", 1, "获取订单当前状态与时间线", "订单基础详情"),
                    self._task("OperationAgent", 1, "查询商户与历史工单上下文", "商户画像与历史经验"),
                    self._task("DataAgent", 1, "预热订单关键实体", "后续一致性校验事实"),
                ],
                2: [
                    self._task("CodeAgent", 1, "排查技术链路", "前后端代码与接口状态"),
                    self._task("OperationAgent", 1, "排查用户操作", "用户是否误操作"),
                    self._task("DataAgent", 1, "排查数据一致性", "跨表比对与回调链路"),
                ],
                3: [
                    self._task("CodeAgent", 1, "复核技术链路结论", "技术侧复核"),
                    self._task("OperationAgent", 1, "复核业务操作结论", "操作侧复核"),
                    self._task("DataAgent", 1, "复核数据异常结论", "数据侧复核"),
                    self._task("ResolutionAgent", 2, "汇总结论并判责", "根因与建议"),
                ],
            },
            "asset_allocation_failure": {
                1: [
                    self._task("CodeAgent", 1, "获取资产池与系统配置基础信息", "额度与配置概况"),
                    self._task("OperationAgent", 1, "获取用户绑定与历史经验", "绑定状态与历史案例"),
                    self._task("DataAgent", 1, "预热资产与分配实体", "可用额度与分配事实"),
                ],
                2: [
                    self._task("CodeAgent", 1, "检查系统配置与权限开关", "系统配置限制"),
                    self._task("OperationAgent", 1, "检查绑定与保护期", "用户归属限制"),
                    self._task("DataAgent", 1, "检查额度限制", "额度是否足够"),
                    self._task("ResolutionAgent", 2, "汇总结论并给出处理建议", "根因与建议"),
                ],
                3: [],
            },
            "settlement_amount_mismatch": {
                1: [
                    self._task("CodeAgent", 1, "读取合同、账单与结算规则", "结算基础事实"),
                    self._task("OperationAgent", 1, "检索相似工单与操作侧线索", "历史处理经验"),
                    self._task("DataAgent", 1, "预热账单与规则实体", "后续规则校验事实"),
                ],
                2: [
                    self._task("CodeAgent", 1, "检查规则与计算链路", "比例与金额一致性"),
                    self._task("OperationAgent", 1, "排除人工流程异常", "历史与流程线索"),
                    self._task("DataAgent", 1, "检查合同标签与数据时间线", "标签变更和规则痕迹"),
                ],
                3: [
                    self._task("CodeAgent", 1, "复核规则链路结论", "技术侧复核"),
                    self._task("OperationAgent", 1, "复核流程与历史案例", "操作侧复核"),
                    self._task("DataAgent", 1, "复核标签与比例冲突", "数据侧复核"),
                    self._task("ResolutionAgent", 2, "汇总结论并判责", "根因与建议"),
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
        return "generic_ticket_diagnosis"

    async def _enrich_ticket(self, ticket: Dict[str, Any], query: str) -> Dict[str, Any]:
        if ticket.get("ticket_id") and ticket.get("issue_type"):
            return ticket

        ticket_id = ticket.get("ticket_id") or self._extract(query, r"WO-\d{8}-\d{4}")
        if not ticket_id:
            return ticket

        result = await tool_registry.execute("query_ticket", ticket_id=ticket_id)
        if result.get("status") == "success" and isinstance(result.get("data"), dict):
            merged = dict(ticket)
            merged.update(result.get("data"))
            return merged
        return ticket

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
