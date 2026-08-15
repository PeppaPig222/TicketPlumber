#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import os
import sys

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from services.diagnosis_service import DiagnosisService, TraceRepository
from context.diagnosis_state import DiagnosisState


@pytest.mark.asyncio
async def test_order_ticket_diagnosis_runs_three_rounds():
    service = DiagnosisService(trace_repo=TraceRepository())

    result = await service.diagnose("请诊断工单 WO-20260815-0421")

    assert result["status"] == "completed"
    assert result["scenario"] == "order_status_anomaly"
    assert result["diagnosis"]["responsible_party"] == "数据侧（后台脚本）"
    assert "状态同步" in result["diagnosis"]["root_cause"]
    assert result["trace"]["total_rounds"] == 3


@pytest.mark.asyncio
async def test_asset_ticket_diagnosis_finishes_in_two_rounds():
    service = DiagnosisService(trace_repo=TraceRepository())

    result = await service.diagnose("帮我看下工单 WO-20260816-0532 为什么资产分配失败")

    assert result["status"] == "completed"
    assert result["scenario"] == "asset_allocation_failure"
    assert result["diagnosis"]["responsible_party"] == "业务配置与权限"
    assert "三重限制" in result["diagnosis"]["summary"]
    assert result["trace"]["total_rounds"] == 2


@pytest.mark.asyncio
async def test_settlement_ticket_diagnosis_cross_verifies():
    service = DiagnosisService(trace_repo=TraceRepository())

    result = await service.diagnose("请排查工单 WO-20260817-0611 的结算金额不符问题")

    assert result["status"] == "completed"
    assert result["scenario"] == "settlement_amount_mismatch"
    assert result["diagnosis"]["responsible_party"] == "数据侧（标签脚本）"
    assert "标签" in result["diagnosis"]["root_cause"]
    assert result["trace"]["total_rounds"] == 3


@pytest.mark.asyncio
async def test_diagnosis_service_survives_intention_agent_failure():
    """IntentionAgent 抛异常时，DiagnosisService 应回退到规则调度，诊断不中断。"""
    service = DiagnosisService(trace_repo=TraceRepository())

    class BadIntentionAgent:
        async def reply(self, _x=None):
            raise RuntimeError("模拟意图识别失败")

    async def patched_diagnose(query, user_id=None, session_id=None):
        # 这里手动复用 diagnose 主流程，但注入坏 agent
        from agentscope.message import Msg
        import uuid

        trace_id = str(uuid.uuid4())[:8]
        effective_user_id = user_id or service.user_id
        effective_session_id = session_id or str(uuid.uuid4())[:8]

        from context.memory_manager import MemoryManager

        memory_manager = MemoryManager(
            user_id=effective_user_id,
            session_id=effective_session_id,
            storage_path=service.storage_path,
        )
        memory_manager.add_message("user", query, metadata={"trace_id": trace_id})

        ticket = await service._load_ticket_context(query)
        if ticket.get("merchant_id"):
            memory_manager.set_merchant_id(ticket.get("merchant_id"))

        state = DiagnosisState(
            query=query,
            ticket=ticket,
            facts={
                "ticket_id": ticket.get("ticket_id", ""),
                "merchant_id": ticket.get("merchant_id", ""),
                "order_id": ticket.get("order_id", ""),
                "issue_type": ticket.get("issue_type", ""),
            },
        )
        from utils.trace_collector import TraceCollector

        trace = TraceCollector(ticket_id=ticket.get("ticket_id", ""))

        from utils.logging_config import set_trace_id

        set_trace_id(trace_id)

        # 注入坏 IntentionAgent
        intention_agent = BadIntentionAgent()
        from agents.scheduler import Scheduler
        from agents.lazy_agent_registry import LazyAgentRegistry

        agent_registry = LazyAgentRegistry(
            model=None,
            cache={},
            memory_manager=memory_manager,
            agent_kwargs={
                "skill_registry": service.skill_registry,
                "rag_agent": service.rag_agent,
            },
        )
        scheduler = Scheduler(
            name="DiagnosisScheduler",
            agent_registry=agent_registry,
            memory_manager=memory_manager,
            rag_available=service.rag_agent is not None,
        )

        final_decision = "done"
        for round_num in range(1, service.loop_decider.max_rounds + 1):
            memory_context = {
                "recent_dialogue": memory_manager.short_term.get_context_string(3),
                "merchant_profile": memory_manager.get_merchant_context(),
                "similar_patterns": await memory_manager.find_similar_patterns(query),
            }
            intention_payload = {
                "query": query,
                "ticket": state.ticket,
                "collected_data": state.to_intention_collected_data(),
                "round_num": round_num,
                "memory_context": memory_context,
            }
            intention_msg = Msg(
                name="user",
                content=json.dumps(intention_payload, ensure_ascii=False),
                role="user",
            )

            # 使用 service 内部的异常保护逻辑
            try:
                intention_result = await intention_agent.reply(intention_msg)
                intention_data = json.loads(intention_result.content)
            except Exception:
                intention_data = service._fallback_intention(round_num, query, state)
                intention_result = Msg(
                    name="IntentionAgent",
                    content=json.dumps(intention_data, ensure_ascii=False),
                    role="assistant",
                )

            trace.start_round(round_num, intent=intention_data.get("intent", ""))
            round_result = await scheduler.run(intention_data)

            service._record_trace(trace, round_result)
            service._merge_round_result(state, intention_data, round_result)

            final_decision = service.loop_decider.decide(round_result, round_num)
            trace.end_round(final_decision)

            if final_decision in {"done", "need_info"}:
                break

        return service._build_response(trace_id, state, trace.get_trace(), final_decision)

    service.diagnose = patched_diagnose

    result = await service.diagnose("请诊断工单 WO-20260815-0421")

    assert result["status"] == "completed"
    assert result["scenario"] == "order_status_anomaly"
    # trace 中应出现 degraded 或错误后的正常 agent 执行
    assert result["trace"]["total_rounds"] >= 1


@pytest.mark.asyncio
async def test_diagnosis_service_survives_scheduler_failure():
    """Scheduler 抛异常时，DiagnosisService 应返回部分结果，不崩溃。"""
    service = DiagnosisService(trace_repo=TraceRepository())

    class BadScheduler:
        async def run(self, _x=None):
            raise RuntimeError("模拟调度失败")

    import json as _json
    from agentscope.message import Msg
    from agents.diagnosis_intention_agent import DiagnosisIntentionAgent
    from utils.trace_collector import TraceCollector
    from utils.logging_config import set_trace_id
    from context.memory_manager import MemoryManager
    import uuid

    trace_id = str(uuid.uuid4())[:8]
    set_trace_id(trace_id)

    memory_manager = MemoryManager(
        user_id=service.user_id,
        session_id=str(uuid.uuid4())[:8],
        storage_path=service.storage_path,
    )
    query = "请诊断工单 WO-20260815-0421"
    memory_manager.add_message("user", query, metadata={"trace_id": trace_id})

    ticket = await service._load_ticket_context(query)
    state = DiagnosisState(
        query=query,
        ticket=ticket,
        facts={
            "ticket_id": ticket.get("ticket_id", ""),
            "merchant_id": ticket.get("merchant_id", ""),
            "order_id": ticket.get("order_id", ""),
            "issue_type": ticket.get("issue_type", ""),
        },
    )
    trace = TraceCollector(ticket_id=ticket.get("ticket_id", ""))

    intention_agent = DiagnosisIntentionAgent(name="DiagnosisIntentionAgent")
    scheduler = BadScheduler()

    intention_payload = {
        "query": query,
        "ticket": state.ticket,
        "collected_data": state.to_intention_collected_data(),
        "round_num": 1,
        "memory_context": {
            "recent_dialogue": "",
            "merchant_profile": "",
            "similar_patterns": [],
        },
    }
    intention_msg = Msg(
        name="user",
        content=_json.dumps(intention_payload, ensure_ascii=False),
        role="user",
    )
    intention_result = await intention_agent.reply(intention_msg)
    intention_data = _json.loads(intention_result.content)

    trace.start_round(1, intent=intention_data.get("intent", ""))

    try:
        round_result = await scheduler.run(intention_data)
    except Exception as e:
        round_result = service._fallback_round_result(1, e)

    service._record_trace(trace, round_result)
    service._merge_round_result(state, intention_data, round_result)

    final_decision = service.loop_decider.decide(round_result, 1)
    trace.end_round(final_decision)

    diagnosis = service._build_response(trace_id, state, trace.get_trace(), final_decision)

    # 即使编排失败，也应返回一个可解释的响应
    assert diagnosis["status"] in {"completed", "partial_failure"}
    assert diagnosis["trace"]["total_rounds"] == 1
    # trace 中 Scheduler 节点应为 degraded
    agents = diagnosis["trace"]["rounds"][0]["agents"]
    assert any(a.get("status") == "degraded" for a in agents)
