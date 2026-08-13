#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Memory 与 RAG 接入集成测试
验证 MemoryManager 统一 RAG 入口、session 缓存、query rewrite、统一检索，
以及 IntentionAgent / ResolutionAgent 通过 memory_manager 调用 RAG。
"""
import json
import pytest

from agentscope.message import Msg

from context.memory_manager import MemoryManager
from agents.diagnosis_intention_agent import DiagnosisIntentionAgent
from agents.resolution_agent import ResolutionAgent


class _FakeRAGAgent:
    """模拟 RAGKnowledgeAgent，记录调用次数与 query。"""

    def __init__(self):
        self.call_count = 0
        self.queries = []

    async def reply(self, msg):
        self.call_count += 1
        self.queries.append(msg.content)
        return Msg(
            name="RAGKnowledgeAgent",
            content=json.dumps(
                {
                    "status": "success",
                    "answer": "fake answer",
                    "retrieved_documents": [
                        {
                            "content": "测试知识片段内容",
                            "metadata": {
                                "source": "faq_policy.txt",
                                "page": 1,
                                "title": "测试文档",
                                "distance": 0.2,
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            role="assistant",
        )


class _FakeLLM:
    """模拟 LLM，返回固定改写结果。"""

    async def __call__(self, messages):
        return Msg(
            name="fake-llm",
            content="改写后的检索query",
            role="assistant",
        )


@pytest.fixture
def fake_rag_agent():
    return _FakeRAGAgent()


@pytest.fixture
def memory_manager(fake_rag_agent):
    return MemoryManager(
        user_id="u1",
        session_id="s1",
        rag_agent=fake_rag_agent,
    )


@pytest.mark.asyncio
async def test_memory_manager_search_knowledge_calls_rag_agent(memory_manager, fake_rag_agent):
    result = await memory_manager.search_knowledge("测试查询")

    assert result["status"] == "success"
    assert len(result["retrieved_documents"]) == 1
    assert fake_rag_agent.call_count == 1


@pytest.mark.asyncio
async def test_memory_manager_search_knowledge_uses_session_cache(memory_manager, fake_rag_agent):
    q = "测试缓存"
    r1 = await memory_manager.search_knowledge(q)
    r2 = await memory_manager.search_knowledge(q)

    assert r1 == r2
    assert fake_rag_agent.call_count == 1


@pytest.mark.asyncio
async def test_memory_manager_rewrite_query_for_rag(fake_rag_agent):
    mm = MemoryManager(
        user_id="u1",
        session_id="s1",
        rag_agent=fake_rag_agent,
        llm_model=_FakeLLM(),
    )
    rewritten = await mm.rewrite_query_for_rag("当前问题", {"ticket_id": "WO-123"})

    assert rewritten == "改写后的检索query"


@pytest.mark.asyncio
async def test_memory_manager_unified_retrieval_returns_knowledge_and_patterns(memory_manager, fake_rag_agent):
    # pattern_store 为 None 时 similar_patterns 为空列表
    result = await memory_manager.unified_retrieval(" unified query ")

    assert "knowledge_docs" in result
    assert "similar_patterns" in result
    assert "rewritten_query" in result
    assert len(result["knowledge_docs"]) == 1
    assert fake_rag_agent.call_count == 1


@pytest.mark.asyncio
async def test_intention_agent_uses_memory_manager_for_rag_fallback(fake_rag_agent):
    agent = DiagnosisIntentionAgent(
        rag_agent=fake_rag_agent,
        memory_manager=MemoryManager(user_id="u1", session_id="s2", rag_agent=fake_rag_agent),
    )
    msg = Msg(
        name="user",
        content=json.dumps(
            {"query": "某个通用问题", "round_num": 1, "collected_data": {}},
            ensure_ascii=False,
        ),
        role="user",
    )
    result = await agent.reply(msg)
    data = json.loads(result.content)

    # 规则无法识别 -> generic -> RAG fallback -> 可能仍无法推断具体场景
    assert "scenario" in data
    assert "kb_hints" in data.get("key_entities", {})
    assert fake_rag_agent.call_count >= 1


@pytest.mark.asyncio
async def test_resolution_agent_uses_memory_manager_for_kb(fake_rag_agent):
    agent = ResolutionAgent(
        name="ResolutionAgent",
        rag_agent=fake_rag_agent,
        memory_manager=MemoryManager(user_id="u1", session_id="s3", rag_agent=fake_rag_agent),
    )
    context = {
        "query": "测试问题",
        "rewritten_query": "测试问题",
        "issue_type": "工单诊断",
        "scenario": "generic_ticket_diagnosis",
        "collected_data": {"facts": {}},
    }
    result = await agent._search_kb(context, previous_results=[])

    assert result["summary"].startswith("知识库参考：")
    assert len(result["kb_matches"]) == 1
    assert result["kb_matches"][0]["source"] == "faq_policy.txt"


@pytest.mark.asyncio
async def test_memory_manager_search_knowledge_no_agent():
    mm = MemoryManager(user_id="u1", session_id="s4")
    result = await mm.search_knowledge("query")

    assert result["status"] == "no_agent"
    assert result["retrieved_documents"] == []
