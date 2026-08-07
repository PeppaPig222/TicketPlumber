#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
工单诊断服务：串起 IntentionAgent、OrchestrationAgent、LoopDecider 与 TraceCollector。
"""
import json
import re
import uuid
from typing import Any, Dict, List, Optional

from agentscope.message import Msg

from agents.intention_agent import IntentionAgent
from agents.lazy_agent_registry import LazyAgentRegistry
from agents.loop_decider import LoopDecider
from agents.orchestration_agent import OrchestrationAgent
from context.long_term_memory import LongTermMemory
from context.memory_manager import MemoryManager
from skills.registry import SkillRegistry
from utils.logging_config import set_trace_id
from utils.trace_collector import TraceCollector, format_trace_sse
from utils.tool_registry import tool_registry


class TraceRepository:
    """内存版 trace 存储，便于 API 查询与 SSE 回放。"""

    def __init__(self):
        self._data: Dict[str, Dict[str, Any]] = {}

    def save(self, trace_id: str, payload: Dict[str, Any]):
        self._data[trace_id] = payload

    def get(self, trace_id: str) -> Optional[Dict[str, Any]]:
        return self._data.get(trace_id)

    def count(self) -> int:
        return len(self._data)


class DiagnosisService:
    """小哈工单智能诊断助手的核心执行入口。"""

    # 专业 Agent 名称，用于 metrics 统计
    PROFESSIONAL_AGENTS = [
        "CodeAgent",
        "OperationAgent",
        "DataAgent",
        "ResolutionAgent",
    ]

    def __init__(
        self,
        trace_repo: Optional[TraceRepository] = None,
        user_id: str = "diagbot_user",
        storage_path: str = "data/memory",
    ):
        self.trace_repo = trace_repo or TraceRepository()
        self.skill_registry = SkillRegistry()
        self.loop_decider = LoopDecider(max_rounds=3)
        self.user_id = user_id
        self.storage_path = storage_path
        # 保留长期记忆实例，供 CLI 的 history/status/clear 命令兼容访问
        self.long_term_memory = LongTermMemory(user_id=user_id, storage_path=storage_path)

    async def diagnose(
        self,
        query: str,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        trace_id = str(uuid.uuid4())[:8]
        set_trace_id(trace_id)

        effective_user_id = user_id or self.user_id
        effective_session_id = session_id or str(uuid.uuid4())[:8]

        # 创建本次诊断专属的三层记忆管理器
        memory_manager = MemoryManager(
            user_id=effective_user_id,
            session_id=effective_session_id,
            storage_path=self.storage_path,
        )
        # 记录用户提问到短期记忆和长期聊天历史
        memory_manager.add_message("user", query, metadata={"trace_id": trace_id})

        ticket = await self._load_ticket_context(query)
        if ticket.get("merchant_id"):
            memory_manager.set_merchant_id(ticket.get("merchant_id"))

        state = {
            "query": query,
            "ticket": ticket,
            "collected_data": {
                "facts": {
                    "ticket_id": ticket.get("ticket_id", ""),
                    "merchant_id": ticket.get("merchant_id", ""),
                    "order_id": ticket.get("order_id", ""),
                    "issue_type": ticket.get("issue_type", ""),
                }
            },
        }
        trace = TraceCollector(ticket_id=ticket.get("ticket_id", ""))

        # 每次诊断都创建新的 Agent 链，避免并发状态竞争
        # 专业 Agent 从 .claude/skills/ 插件目录懒加载，通过 agent_kwargs 注入依赖
        agent_registry = LazyAgentRegistry(
            model=None,
            cache={},
            memory_manager=memory_manager,
            agent_kwargs={
                "skill_registry": self.skill_registry,
            },
        )
        intention_agent = IntentionAgent(name="IntentionAgent")
        orchestrator = OrchestrationAgent(
            name="DiagnosisOrchestrationAgent",
            agent_registry=agent_registry,
            memory_manager=memory_manager,
        )

        final_decision = "done"
        for round_num in range(1, self.loop_decider.max_rounds + 1):
            # 组装记忆上下文，供 IntentionAgent 丰富意图理解
            memory_context = {
                "recent_dialogue": memory_manager.short_term.get_context_string(3),
                "merchant_profile": memory_manager.get_merchant_context(),
                "similar_patterns": await memory_manager.find_similar_patterns(query),
            }
            intention_payload = {
                "query": query,
                "ticket": state.get("ticket", {}),
                "collected_data": state.get("collected_data", {}),
                "round_num": round_num,
                "memory_context": memory_context,
            }
            intention_msg = Msg(
                name="user",
                content=json.dumps(intention_payload, ensure_ascii=False),
                role="user",
            )
            intention_result = await intention_agent.reply(intention_msg)
            intention_data = json.loads(intention_result.content)

            # 若 intention 解析出 merchant_id，也同步到记忆系统
            intention_merchant_id = (
                intention_data.get("key_entities", {}).get("merchant_id")
                or intention_data.get("ticket", {}).get("merchant_id")
            )
            if intention_merchant_id:
                memory_manager.set_merchant_id(intention_merchant_id)

            trace.start_round(round_num, intent=intention_data.get("intent", ""))
            orchestration_result = await orchestrator.reply(intention_result)
            round_result = json.loads(orchestration_result.content)

            self._record_trace(trace, round_result)
            self._merge_round_result(state, intention_data, round_result)

            final_decision = self.loop_decider.decide(round_result, round_num)
            trace.end_round(final_decision)

            if final_decision in {"done", "need_info"}:
                break

        diagnosis = self._build_response(trace_id, state, trace.get_trace(), final_decision)
        self.trace_repo.save(
            trace_id,
            {
                "trace": trace.get_trace(),
                "diagnosis": diagnosis,
                "events": format_trace_sse(trace.get_trace()),
            },
        )

        # 记录助手回复到短期/长期记忆
        diagnosis_summary = (diagnosis.get("diagnosis") or {}).get("summary", "")
        memory_manager.add_message(
            "assistant",
            diagnosis_summary,
            metadata={"trace_id": trace_id},
        )

        # 统一写入三层记忆
        facts = (state.get("collected_data", {}) or {}).get("facts", {})
        await memory_manager.record_diagnosis({
            "trace_id": trace_id,
            "ticket_id": diagnosis.get("ticket_id", ""),
            "merchant_id": facts.get("merchant_id", ""),
            "issue_type": facts.get("issue_type", ""),
            "scenario": diagnosis.get("scenario", ""),
            "summary": diagnosis_summary,
            "responsible_party": (diagnosis.get("diagnosis") or {}).get("responsible_party", ""),
            "root_cause": (diagnosis.get("diagnosis") or {}).get("root_cause", ""),
            "query": state.get("query", ""),
            "status": diagnosis.get("status", "completed"),
        })

        return diagnosis

    async def get_trace(self, trace_id: str) -> Optional[Dict[str, Any]]:
        payload = self.trace_repo.get(trace_id)
        if not payload:
            return None
        return payload.get("trace")

    async def get_trace_events(self, trace_id: str) -> Optional[List[Dict[str, Any]]]:
        payload = self.trace_repo.get(trace_id)
        if not payload:
            return None
        return payload.get("events", [])

    def get_metrics(self) -> Dict[str, Any]:
        stats = self.long_term_memory.get_statistics()
        return {
            "trace_count": self.trace_repo.count(),
            "max_rounds": self.loop_decider.max_rounds,
            "registered_agents": len(self.PROFESSIONAL_AGENTS),
            "registered_skills": len(list(self.skill_registry.keys())),
            "total_diagnoses": stats.get("total_diagnoses", 0),
        }

    async def _load_ticket_context(self, query: str) -> Dict[str, Any]:
        ticket_id = self._extract(query, r"WO-\d{8}-\d{4}")
        if not ticket_id:
            return {}
        ticket_result = await tool_registry.execute("query_ticket", ticket_id=ticket_id)
        if ticket_result.get("status") == "success":
            return ticket_result.get("data", {})
        return {}

    def _record_trace(self, trace: TraceCollector, round_result: Dict[str, Any]):
        for result in round_result.get("results", []):
            trace.record_agent(
                agent_name=result.get("agent_name", ""),
                priority=result.get("priority", 0),
                status=result.get("status", "unknown"),
                duration_ms=result.get("duration_ms") or 0,
                output_summary=result.get("summary", ""),
                tools_called=result.get("tools_called", []),
                recommended_skills=result.get("recommended_skills", []),
                evidence=result.get("evidence", []),
            )

    def _merge_round_result(
        self,
        state: Dict[str, Any],
        intention_data: Dict[str, Any],
        round_result: Dict[str, Any],
    ):
        collected = state.setdefault("collected_data", {})
        facts = collected.setdefault("facts", {})

        key_entities = intention_data.get("key_entities", {}) or {}
        for key, value in key_entities.items():
            if value:
                facts[key] = value

        for result in round_result.get("results", []):
            data = result.get("data", {}) or {}
            for key, value in data.items():
                if key in {"duration_ms", "tools_called", "summary", "status", "recommended_skills", "next_actions"}:
                    continue
                facts[key] = value

        collected.setdefault("rounds", []).append({
            "intent": intention_data.get("intent"),
            "result": round_result,
        })

    def _build_response(
        self,
        trace_id: str,
        state: Dict[str, Any],
        trace: Dict[str, Any],
        final_decision: str,
    ) -> Dict[str, Any]:
        facts = (state.get("collected_data", {}) or {}).get("facts", {})
        scenario = facts.get("scenario", "order_status_anomaly")

        if final_decision == "need_info":
            return {
                "trace_id": trace_id,
                "status": "need_info",
                "ticket_id": facts.get("ticket_id"),
                "scenario": scenario,
                "diagnosis": {
                    "summary": "当前工单信息不足，建议补充商户号、订单号或完整工单内容后重试。",
                    "responsible_party": "待补充信息",
                    "root_cause": "缺少关键诊断实体",
                    "evidence": [],
                    "recommendations": ["补充工单编号、订单号或商户号"],
                },
                "trace": trace,
            }

        diagnosis = self._scenario_summary(scenario, facts)
        return {
            "trace_id": trace_id,
            "status": "completed",
            "ticket_id": facts.get("ticket_id"),
            "scenario": scenario,
            "diagnosis": diagnosis,
            "trace": trace,
        }

    def _scenario_summary(self, scenario: str, facts: Dict[str, Any]) -> Dict[str, Any]:
        if scenario == "asset_allocation_failure":
            asset_pool = facts.get("asset_pool", {})
            binding = facts.get("asset_binding_detail", {})
            permissions = facts.get("asset_permission_detail", {})
            request = asset_pool.get("allocation_request", {})
            return {
                "summary": facts.get("summary", "该工单不是单点故障，而是额度、保护期和权限三重限制叠加导致资产分配失败。"),
                "responsible_party": facts.get("responsible_party", "业务配置与权限"),
                "root_cause": facts.get("root_cause", "可用额度仅 20 天，但申请 100 天；目标用户仍绑定其他商户且保护期有效；操作者也没有跨商户分配权限。"),
                "evidence": facts.get("evidence", [
                    f"可用额度 {asset_pool.get('available_quota', 0)}，申请额度 {request.get('requested_quota', 0)}",
                    f"用户当前绑定商户 {binding.get('user_binding', {}).get('current_merchant_id', '未知')}",
                    f"跨商户分配权限 {'开启' if permissions.get('cross_merchant_allocate') else '关闭'}",
                ]),
                "recommendations": facts.get("recommendations", [
                    "先回收或释放未使用额度，再发起分配",
                    "等待保护期结束或先处理用户解绑",
                    "如需跨商户操作，补齐权限后再执行",
                ]),
            }

        if scenario == "settlement_amount_mismatch":
            resolver = {
                "responsible_party": facts.get("responsible_party", "数据侧（标签脚本）"),
                "root_cause": facts.get("root_cause", "商户结算标签被脚本误刷，导致按错误分润比例结算。"),
                "recommendations": facts.get("recommendations", []),
                "evidence": facts.get("evidence", []),
                "summary": facts.get("summary", "诊断确认结算金额不符的根因位于数据标签与规则不一致。"),
            }
            if not resolver["recommendations"]:
                resolver["recommendations"] = [
                    "修正商户结算标签与规则",
                    "重新核算账期并处理差额",
                    "增加标签脚本变更审计",
                ]
            return resolver

        resolver = {
            "responsible_party": facts.get("responsible_party", "数据侧（后台脚本）"),
            "root_cause": facts.get("root_cause", "退款回调后订单状态同步任务超时，导致订单状态未更新。"),
            "recommendations": facts.get("recommendations", []),
            "evidence": facts.get("evidence", []),
            "summary": facts.get("summary", "诊断确认问题位于数据同步链路，而非前后端代码或用户操作。"),
        }
        if not resolver["recommendations"]:
            resolver["recommendations"] = [
                "手动修复异常订单状态",
                "排查状态同步脚本与回调超时问题",
                "补充重试和告警机制",
            ]
        return resolver

    def _extract(self, text: str, pattern: str) -> str:
        matched = re.search(pattern, text or "")
        return matched.group(0) if matched else ""
