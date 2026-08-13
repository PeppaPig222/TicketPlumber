#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
工单诊断服务：串起 IntentionAgent、OrchestrationAgent、LoopDecider 与 TraceCollector。
"""
import json
import logging
import re
import sys
import time
import uuid
from typing import Any, Dict, List, Optional

import psutil

from agentscope.message import Msg

logger = logging.getLogger(__name__)

from agents.intention_agent import IntentionAgent
from agents.lazy_agent_registry import LazyAgentRegistry
from agents.loop_decider import LoopDecider
from agents.orchestration_agent import OrchestrationAgent
from context.long_term_memory import LongTermMemory
from context.memory_manager import MemoryManager
from skills.registry import SkillRegistry
from utils.errors import AppError, ErrorCode, ExecutionStatus, map_exception_to_error_code
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


# 模块启动时间，用于计算 uptime
_START_TIME = time.time()


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
        rag_agent=None,
    ):
        self.trace_repo = trace_repo or TraceRepository()
        self.skill_registry = SkillRegistry()
        self.loop_decider = LoopDecider(max_rounds=3)
        self.user_id = user_id
        self.storage_path = storage_path
        # 保留长期记忆实例，供 CLI 的 history/status/clear 命令兼容访问
        self.long_term_memory = LongTermMemory(user_id=user_id, storage_path=storage_path)
        # RAG Agent：外部注入优先，否则尝试懒加载
        self.rag_agent = rag_agent or self._load_rag_agent()

    async def diagnose(
        self,
        query: str,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        trace_id = str(uuid.uuid4())[:8]
        set_trace_id(trace_id)

        try:
            return await self._diagnose_internal(
                query=query,
                user_id=user_id,
                session_id=session_id,
                trace_id=trace_id,
            )
        except AppError as e:
            logger.error(
                "Diagnosis failed with AppError",
                extra={"trace_id": trace_id, "error_code": e.code.value[0]},
            )
            return self._error_response(trace_id, e)
        except Exception as e:
            logger.error(
                "Diagnosis failed with unexpected error",
                extra={"trace_id": trace_id},
                exc_info=True,
            )
            return self._error_response(
                trace_id,
                AppError(
                    map_exception_to_error_code(e),
                    detail=str(e),
                ),
            )

    async def _diagnose_internal(
        self,
        query: str,
        user_id: Optional[str],
        session_id: Optional[str],
        trace_id: str,
    ) -> Dict[str, Any]:
        effective_user_id = user_id or self.user_id
        effective_session_id = session_id or str(uuid.uuid4())[:8]

        logger.info(
            "开始工单诊断",
            extra={
                "trace_id": trace_id,
                "user_id": effective_user_id,
                "session_id": effective_session_id,
                "query": query[:100],
            },
        )

        # 创建本次诊断专属的三层记忆管理器，并注入 rag_agent 统一 RAG 入口
        memory_manager = MemoryManager(
            user_id=effective_user_id,
            session_id=effective_session_id,
            storage_path=self.storage_path,
            rag_agent=self.rag_agent,
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
        # 构造 Agent 注册表：专业 Agent 从 skill 懒加载，RAG Agent 通过工厂注入
        custom_factories = {}
        if self.rag_agent is not None:
            custom_factories["RAGKnowledgeAgent"] = lambda: self.rag_agent

        agent_registry = LazyAgentRegistry(
            model=None,
            cache={},
            memory_manager=memory_manager,
            agent_kwargs={
                "skill_registry": self.skill_registry,
                "rag_agent": self.rag_agent,
            },
            custom_factories=custom_factories,
        )
        intention_agent = IntentionAgent(
            name="IntentionAgent",
            rag_agent=self.rag_agent,
            memory_manager=memory_manager,
        )
        orchestrator = OrchestrationAgent(
            name="DiagnosisOrchestrationAgent",
            agent_registry=agent_registry,
            memory_manager=memory_manager,
        )

        final_decision = "done"
        for round_num in range(1, self.loop_decider.max_rounds + 1):
            logger.info(
                f"开始第 {round_num} 轮诊断",
                extra={"round_num": round_num, "trace_id": trace_id},
            )
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
            try:
                intention_result = await intention_agent.reply(intention_msg)
                intention_data = json.loads(intention_result.content)
                logger.info(
                    "IntentionAgent 完成意图识别",
                    extra={
                        "round_num": round_num,
                        "trace_id": trace_id,
                        "intent": intention_data.get("intent", ""),
                        "scenario": intention_data.get("scenario", ""),
                    },
                )
            except Exception as e:
                logger.error(
                    f"IntentionAgent failed in round {round_num}: {e}",
                    extra={"round_num": round_num, "trace_id": trace_id},
                )
                # 回退到规则调度：使用通用诊断意图
                intention_data = self._fallback_intention(round_num, query, state)
                intention_result = Msg(
                    name="IntentionAgent",
                    content=json.dumps(intention_data, ensure_ascii=False),
                    role="assistant",
                )

            # 若 intention 解析出 merchant_id，也同步到记忆系统
            intention_merchant_id = (
                intention_data.get("key_entities", {}).get("merchant_id")
                or intention_data.get("ticket", {}).get("merchant_id")
            )
            if intention_merchant_id:
                memory_manager.set_merchant_id(intention_merchant_id)

            trace.start_round(round_num, intent=intention_data.get("intent", ""))

            try:
                orchestration_result = await orchestrator.reply(intention_result)
                round_result = json.loads(orchestration_result.content)
                agent_names = [a.get("agent_name", "") for a in round_result.get("agents", [])]
                logger.info(
                    "OrchestrationAgent 完成本轮调度",
                    extra={
                        "round_num": round_num,
                        "trace_id": trace_id,
                        "agents": agent_names,
                        "decision": round_result.get("decision", ""),
                    },
                )
            except Exception as e:
                logger.error(
                    f"OrchestrationAgent failed in round {round_num}: {e}",
                    extra={"round_num": round_num, "trace_id": trace_id},
                )
                round_result = self._fallback_round_result(round_num, e)

            self._record_trace(trace, round_result)
            self._merge_round_result(state, intention_data, round_result)

            final_decision = self.loop_decider.decide(round_result, round_num)
            trace.end_round(final_decision)
            logger.info(
                f"第 {round_num} 轮结束，决策: {final_decision}",
                extra={"round_num": round_num, "trace_id": trace_id, "decision": final_decision},
            )

            if final_decision in {"done", "need_info"}:
                break

        diagnosis = self._build_response(trace_id, state, trace.get_trace(), final_decision)
        logger.info(
            "工单诊断完成",
            extra={
                "trace_id": trace_id,
                "status": diagnosis.get("status", ""),
                "scenario": diagnosis.get("scenario", ""),
                "total_rounds": (diagnosis.get("trace") or {}).get("total_rounds", 0),
            },
        )
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
        try:
            system_metrics = {
                "cpu_percent": psutil.cpu_percent(interval=None),
                "memory_percent": psutil.virtual_memory().percent,
                "disk_usage_percent": psutil.disk_usage("/").percent,
            }
        except Exception:
            system_metrics = {}

        return {
            "trace_count": self.trace_repo.count(),
            "max_rounds": self.loop_decider.max_rounds,
            "registered_agents": len(self.PROFESSIONAL_AGENTS),
            "registered_skills": len(list(self.skill_registry.keys())),
            "total_diagnoses": stats.get("total_diagnoses", 0),
            "rag_available": self.rag_agent is not None,
            "uptime_seconds": round(time.time() - _START_TIME, 2),
            "system": system_metrics,
        }

    async def _load_ticket_context(self, query: str) -> Dict[str, Any]:
        ticket_id = self._extract(query, r"WO-\d{8}-\d{4}")
        if not ticket_id:
            return {}
        ticket_result = await tool_registry.execute("query_ticket", ticket_id=ticket_id)
        if ticket_result.get("status") == "success":
            return ticket_result.get("data", {})
        return {}

    def _fallback_intention(self, round_num: int, query: str, state: Dict[str, Any]) -> Dict[str, Any]:
        """IntentionAgent 失败时的规则回退意图。"""
        scenario = (
            state.get("collected_data", {}).get("facts", {}).get("scenario")
            or "generic_ticket_diagnosis"
        )
        return {
            "intent": "ticket_diagnosis",
            "reasoning": f"第{round_num}轮意图识别失败，回退到规则调度",
            "intents": [
                {
                    "type": "ticket_diagnosis",
                    "confidence": 0.5,
                    "description": "回退诊断",
                    "reason": "IntentionAgent 异常，使用规则兜底",
                }
            ],
            "key_entities": state.get("collected_data", {}).get("facts", {}),
            "rewritten_query": query,
            "scenario": scenario,
            "ticket": state.get("ticket", {}),
            "round_num": round_num,
            "query": query,
            "collected_data": state.get("collected_data", {}),
            "error_code": ErrorCode.INTENT_RECOGNITION_FAILED.value[0],
            "agent_schedule": [
                {
                    "agent_name": "CodeAgent",
                    "priority": 1,
                    "reason": "规则兜底调度",
                    "expected_output": "技术侧线索",
                },
                {
                    "agent_name": "OperationAgent",
                    "priority": 1,
                    "reason": "规则兜底调度",
                    "expected_output": "操作侧线索",
                },
                {
                    "agent_name": "DataAgent",
                    "priority": 1,
                    "reason": "规则兜底调度",
                    "expected_output": "数据侧线索",
                },
                {
                    "agent_name": "ResolutionAgent",
                    "priority": 2,
                    "reason": "规则兜底调度",
                    "expected_output": "归因建议",
                },
            ],
        }

    def _fallback_round_result(self, round_num: int, error: Exception) -> Dict[str, Any]:
        """OrchestrationAgent 失败时的部分结果。"""
        return {
            "status": "partial_failure",
            "errors": 1,
            "agents_executed": 0,
            "error_code": ErrorCode.ORCHESTRATION_FAILED.value[0],
            "results": [
                {
                    "agent_name": "OrchestrationAgent",
                    "priority": 0,
                    "status": "degraded",
                    "data": {"error": str(error)},
                    "summary": f"第{round_num}轮编排失败，已降级并继续诊断",
                    "duration_ms": 0,
                    "tools_called": [],
                    "recommended_skills": [],
                    "evidence": [str(error)],
                    "next_actions": ["检查 Agent 配置或日志"],
                }
            ],
        }

    def _record_trace(self, trace: TraceCollector, round_result: Dict[str, Any]):
        overall_status = round_result.get("status", ExecutionStatus.UNKNOWN.value)
        has_errors = round_result.get("errors", 0) > 0 or overall_status in {
            ExecutionStatus.PARTIAL_FAILURE.value,
            ExecutionStatus.ERROR.value,
        }

        for result in round_result.get("results", []):
            agent_status = result.get("status", ExecutionStatus.UNKNOWN.value)
            tools_called = result.get("tools_called", [])

            # 如果整轮失败或单个 agent 失败/超时，标记为 degraded
            if has_errors or agent_status in {
                ExecutionStatus.ERROR.value,
                ExecutionStatus.TIMEOUT.value,
            }:
                agent_status = ExecutionStatus.DEGRADED.value

            trace.record_agent(
                agent_name=result.get("agent_name", ""),
                priority=result.get("priority", 0),
                status=agent_status,
                duration_ms=result.get("duration_ms") or 0,
                output_summary=result.get("summary", ""),
                tools_called=tools_called,
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
                if key in {"duration_ms", "tools_called", "summary", "status", "recommended_skills", "next_actions", "error"}:
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
                "responsible_party_matrix": facts.get("responsible_party_matrix", []),
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
                "responsible_party_matrix": facts.get("responsible_party_matrix", []),
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
            "responsible_party_matrix": facts.get("responsible_party_matrix", []),
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

    def _error_response(
        self, trace_id: str, app_error: AppError
    ) -> Dict[str, Any]:
        """构造统一错误响应结构。"""
        return {
            "trace_id": trace_id,
            "status": "error",
            "ticket_id": None,
            "scenario": "unknown",
            "diagnosis": {
                "summary": "诊断过程中发生错误",
                "responsible_party": "未知",
                "root_cause": app_error.detail,
                "evidence": [],
                "recommendations": ["请稍后重试或联系运维"],
            },
            "error": app_error.to_dict(),
            "trace": {
                "trace_id": trace_id,
                "ticket_id": "",
                "rounds": [],
                "total_rounds": 0,
                "status": "error",
            },
        }

    def _load_rag_agent(self):
        """懒加载 RAGKnowledgeAgent（来自 ask-question skill），失败时返回 None。"""
        try:
            import importlib.util
            from pathlib import Path

            project_root = Path(__file__).resolve().parent.parent
            agent_script = (
                project_root
                / ".claude"
                / "skills"
                / "ask-question"
                / "script"
                / "agent.py"
            )
            if not agent_script.exists():
                return None

            spec = importlib.util.spec_from_file_location(
                "RAGKnowledgeAgentModule", agent_script
            )
            if spec is None or spec.loader is None:
                return None

            module = importlib.util.module_from_spec(spec)
            sys.modules["RAGKnowledgeAgentModule"] = module
            spec.loader.exec_module(module)
            RAGKnowledgeAgent = module.RAGKnowledgeAgent

            from config import RAG_CONFIG

            model_path = RAG_CONFIG.get("embedding_model", "BAAI/bge-small-zh-v1.5")
            path_obj = Path(model_path).expanduser()
            if not path_obj.is_absolute():
                path_obj = project_root / path_obj
            embedding_model = (
                str(path_obj.resolve()) if path_obj.exists() else model_path
            )

            kb_path = project_root / "data" / "rag_knowledge"
            kb_path.mkdir(parents=True, exist_ok=True)

            rag_agent = RAGKnowledgeAgent(
                name="RAGKnowledgeAgent",
                model=None,
                knowledge_base_path=str(kb_path),
                collection_name="ticket_diagnosis_knowledge",
                embedding_model=embedding_model,
                top_k=3,
            )
            if getattr(rag_agent, "initialized", False):
                return rag_agent
        except Exception:
            pass
        return None

    def _extract(self, text: str, pattern: str) -> str:
        matched = re.search(pattern, text or "")
        return matched.group(0) if matched else ""
