#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
RAG 与诊断链路集成测试。

覆盖：
- PDF/TXT 文档切分（含架构图占位）
- RAGKnowledgeAgent 初始化与检索（依赖可选）
- DiagnosisIntentionAgent 的 RAG fallback
- ResolutionAgent 的 RAG 证据补充
- DiagnosisService 的 rag_available 指标
"""
import json
import os
import sys
from pathlib import Path
from typing import Dict

import pytest
from agentscope.message import Msg

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from agents.diagnosis_intention_agent import DiagnosisIntentionAgent
from agents.resolution_agent import ResolutionAgent
from scripts.process_merchant_pdf import process_document


def _txt_sample() -> str:
    return """
# 商户结算规则说明

## 订单状态流转
当用户发起退款后，支付网关会异步回调订单中心。
订单中心收到回调后，应更新订单表状态为已退款。
若状态同步任务超时，订单表可能仍显示待退款。

## 资产分配
商户可用额度不足、目标用户保护期未结束或操作者缺少跨商户分配权限时，
资产分配会失败。

## 结算金额
商户结算标签决定实际分润比例。若标签被批量脚本误刷，
会导致结算金额与合同比例不一致。
""".strip()


class FakeRAGAgent:
    """用于测试的 RAG Agent 替身，无需真实向量库。"""

    def __init__(self, responses=None):
        self.responses = responses or []

    async def search_knowledge(self, query: str, top_k: int = 3):
        _ = query, top_k
        return self.responses


def _make_kb_response(content: str, distance: float = 0.25) -> Dict:
    """构造带 similarity 的 RAG 返回结果。distance 越小相似度越高。"""
    return {
        "content": content,
        "metadata": {"source": "merchant_architecture.txt", "page": 1},
        "distance": distance,
    }


@pytest.fixture
def sample_txt_path(tmp_path):
    path = tmp_path / "merchant_architecture.txt"
    path.write_text(_txt_sample(), encoding="utf-8")
    return path


def test_process_document_txt(sample_txt_path):
    docs = process_document(sample_txt_path)
    assert len(docs) > 0
    assert all("id" in d and "content" in d and "metadata" in d for d in docs)
    assert not any(d["metadata"].get("has_diagram") for d in docs)
    # 关键主题应被切分到某个 chunk 中
    all_text = " ".join(d["content"] for d in docs)
    assert "订单状态" in all_text
    assert "资产分配" in all_text
    assert "结算金额" in all_text


def test_process_document_diagram_detection(sample_txt_path):
    # 构造一个几乎无文本的页面模拟图页：当前 TXT 实现视为单页非图
    docs = process_document(sample_txt_path)
    diagram_count = sum(1 for d in docs if d["metadata"].get("has_diagram"))
    assert diagram_count == 0


def test_process_document_empty_txt(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("   \n\n   ", encoding="utf-8")
    docs = process_document(path)
    # 空内容不生成任何 chunk
    assert docs == []


@pytest.mark.asyncio
async def test_intention_agent_rag_fallback_above_threshold():
    # distance=0.25 -> similarity=0.75，超过默认阈值 0.55
    fake_kb = [
        _make_kb_response(
            "结算金额与合同分润比例不一致时，应检查商户结算标签是否被脚本误刷。",
            distance=0.25,
        )
    ]
    agent = DiagnosisIntentionAgent(name="DiagnosisIntentionAgent", rag_agent=FakeRAGAgent(fake_kb))

    # 使用不含明显关键词的查询，触发 RAG fallback
    query = "商户反馈本月到账金额少了"
    result = await agent.reply(Msg(name="user", content=query, role="user"))
    payload = json.loads(result.content)

    assert payload["scenario"] == "settlement_amount_mismatch"
    key_entities = payload.get("key_entities", {})
    assert "kb_hints" in key_entities
    assert len(key_entities["kb_hints"]) > 0
    assert key_entities["kb_hints"][0]["similarity"] == 0.75


@pytest.mark.asyncio
async def test_intention_agent_rag_fallback_below_threshold():
    # distance=0.6 -> similarity=0.4，低于默认阈值 0.55，不应采纳 RAG 推断
    fake_kb = [
        _make_kb_response(
            "结算金额与合同分润比例不一致时，应检查商户结算标签是否被脚本误刷。",
            distance=0.6,
        )
    ]
    agent = DiagnosisIntentionAgent(name="DiagnosisIntentionAgent", rag_agent=FakeRAGAgent(fake_kb))

    query = "商户反馈本月到账金额少了"
    result = await agent.reply(Msg(name="user", content=query, role="user"))
    payload = json.loads(result.content)

    # 规则无法识别，RAG 结果置信度低，应保持 generic
    assert payload["scenario"] == "generic_ticket_diagnosis"
    key_entities = payload.get("key_entities", {})
    # kb_hints 仍可保留，用于 reasoning 参考
    assert "kb_hints" in key_entities
    assert len(key_entities["kb_hints"]) > 0


@pytest.mark.asyncio
async def test_intention_agent_rule_still_works_without_rag():
    agent = DiagnosisIntentionAgent(name="DiagnosisIntentionAgent")

    result = await agent.reply(
        Msg(name="user", content="订单 ORD-8823 退款后状态还是待退款", role="user")
    )
    payload = json.loads(result.content)

    assert payload["scenario"] == "order_status_anomaly"
    assert payload.get("key_entities", {}).get("order_id") == "ORD-8823"


@pytest.mark.asyncio
async def test_resolution_agent_rag_evidence():
    fake_kb = [
        _make_kb_response(
            "退款回调超时会导致订单状态未更新，建议检查状态同步脚本。",
            distance=0.25,
        )
    ]
    agent = ResolutionAgent(name="ResolutionAgent", rag_agent=FakeRAGAgent(fake_kb))

    payload = {
        "context": {
            "scenario": "order_status_anomaly",
            "round_num": 3,
            "merchant_id": "2037",
            "issue_type": "订单状态异常",
            "query": "订单状态异常",
        },
        "previous_results": [
            {"agent_name": "CodeAgent", "result": {"data": {"path_verdict": "代码链路无明显异常"}}},
            {"agent_name": "OperationAgent", "result": {"data": {"path_verdict": "用户操作流程符合规范"}}},
            {"agent_name": "DataAgent", "result": {"data": {"path_verdict": "支付表与订单表状态不一致"}}},
        ],
    }

    result = await agent.reply(Msg(name="user", content=json.dumps(payload, ensure_ascii=False), role="user"))
    data = json.loads(result.content)

    assert data["status"] == "success"
    assert "kb_matches" in data
    assert len(data["kb_matches"]) > 0
    # evidence 中应包含 RAG 摘要
    assert any("知识库参考" in e for e in data["evidence"])


@pytest.mark.asyncio
async def test_resolution_agent_degrades_without_rag():
    agent = ResolutionAgent(name="ResolutionAgent")

    payload = {
        "context": {
            "scenario": "order_status_anomaly",
            "round_num": 3,
            "merchant_id": "2037",
            "issue_type": "订单状态异常",
        },
        "previous_results": [
            {"agent_name": "CodeAgent", "result": {"data": {"path_verdict": "代码链路无明显异常"}}},
            {"agent_name": "OperationAgent", "result": {"data": {"path_verdict": "用户操作流程符合规范"}}},
            {"agent_name": "DataAgent", "result": {"data": {"path_verdict": "支付表与订单表状态不一致"}}},
        ],
    }

    result = await agent.reply(Msg(name="user", content=json.dumps(payload, ensure_ascii=False), role="user"))
    data = json.loads(result.content)

    assert data["status"] == "success"
    assert data.get("kb_matches") == []


@pytest.mark.asyncio
async def test_resolution_agent_rag_disabled_by_config():
    from config import RAG_CONFIG

    original = RAG_CONFIG.get("enable_resolution_evidence", True)
    RAG_CONFIG["enable_resolution_evidence"] = False

    try:
        fake_kb = [
            _make_kb_response(
                "退款回调超时会导致订单状态未更新，建议检查状态同步脚本。",
                distance=0.25,
            )
        ]
        agent = ResolutionAgent(name="ResolutionAgent", rag_agent=FakeRAGAgent(fake_kb))

        payload = {
            "context": {
                "scenario": "order_status_anomaly",
                "round_num": 3,
                "merchant_id": "2037",
                "issue_type": "订单状态异常",
                "query": "订单状态异常",
            },
            "previous_results": [
                {"agent_name": "CodeAgent", "result": {"data": {"path_verdict": "代码链路无明显异常"}}},
                {"agent_name": "OperationAgent", "result": {"data": {"path_verdict": "用户操作流程符合规范"}}},
                {"agent_name": "DataAgent", "result": {"data": {"path_verdict": "支付表与订单表状态不一致"}}},
            ],
        }

        result = await agent.reply(Msg(name="user", content=json.dumps(payload, ensure_ascii=False), role="user"))
        data = json.loads(result.content)

        assert data["status"] == "success"
        assert data.get("kb_matches") == []
        # evidence 中不应包含 RAG 摘要
        assert not any("知识库参考" in e for e in data["evidence"])
    finally:
        RAG_CONFIG["enable_resolution_evidence"] = original


def test_diagnosis_service_rag_metric():
    from services.diagnosis_service import DiagnosisService

    service = DiagnosisService()
    metrics = service.get_metrics()
    assert "rag_available" in metrics
    # 若本地未安装 pymilvus/sentence-transformers，则 rag_agent 为 None
    assert isinstance(metrics["rag_available"], bool)


def _rag_dependencies_available() -> bool:
    import importlib

    try:
        importlib.import_module("pymilvus")
        importlib.import_module("sentence_transformers")
        return True
    except Exception:
        return False


@pytest.mark.skipif(
    not _rag_dependencies_available(),
    reason="RAG 依赖未安装，跳过真实向量库测试",
)
@pytest.mark.asyncio
async def test_rag_knowledge_agent_with_real_dependencies(tmp_path):
    """在已安装依赖的环境中验证 RAGKnowledgeAgent 可写入并检索。"""
    import importlib.util

    agent_script = Path(project_root) / ".claude" / "skills" / "ask-question" / "script" / "agent.py"
    spec = importlib.util.spec_from_file_location("RAGKnowledgeAgentModule", agent_script)
    module = importlib.util.module_from_spec(spec)
    sys.modules["RAGKnowledgeAgentModule"] = module
    spec.loader.exec_module(module)
    RAGKnowledgeAgent = module.RAGKnowledgeAgent

    kb_path = tmp_path / "rag_kb"
    kb_path.mkdir(parents=True, exist_ok=True)
    agent = RAGKnowledgeAgent(
        name="RAGKnowledgeAgent",
        model=None,
        knowledge_base_path=str(kb_path),
        collection_name="test_ticket_diagnosis",
        embedding_model="BAAI/bge-small-zh-v1.5",
        top_k=2,
    )

    if not getattr(agent, "initialized", False):
        pytest.skip("RAG Agent 初始化失败")

    documents = [
        {
            "id": "doc_1",
            "content": "订单退款后状态未更新，应检查退款回调与状态同步脚本。",
            "metadata": {"source": "test.txt", "page": 1},
        },
        {
            "id": "doc_2",
            "content": "资产分配失败通常由额度不足、保护期或权限限制导致。",
            "metadata": {"source": "test.txt", "page": 2},
        },
    ]

    result = await agent.add_documents(documents)
    assert result["status"] == "success"
    assert result["added_count"] == 2

    hits = await agent.search_knowledge("退款后订单状态还是待退款", top_k=2)
    assert len(hits) > 0
    assert any("退款" in h["content"] for h in hits)

    agent.close()
