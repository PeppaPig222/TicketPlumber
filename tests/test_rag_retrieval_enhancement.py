#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
RAG 检索增强 Phase 1 单元测试

覆盖 rag_retrieval_enhancement_plan.md Phase 1 的检索增强辅助方法：
- L1 参数校验 _validate_query
- L3 空值缓存 _is_empty_result / _mark_empty_result
- L5 embedding LRU 缓存 _get_cached_embedding / _cache_embedding
- 向量阈值去重 _dedup_by_similarity
- 父文档召回 _parent_document_recall
- 写入去重 compute_content_hash / deduplicate_chunks / _overlap_ratio
"""
import importlib.util
import os
import sys
from collections import OrderedDict
from pathlib import Path

import numpy as np

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from scripts.process_merchant_pdf import (
    compute_content_hash,
    deduplicate_chunks,
    _overlap_ratio,
)


def _load_rag_agent_class():
    """加载 RAGKnowledgeAgent 类，规避非标准包路径直接 import。"""
    agent_script = (
        Path(project_root) / ".claude" / "skills" / "ask-question" / "script" / "agent.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_rag_retrieval_enhancement_test_agent", agent_script
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.RAGKnowledgeAgent


class _FakeEmbedding:
    """按 content 映射到指定向量，用于可控的去重测试。"""

    def __init__(self, vectors):
        self._vectors = vectors

    def encode(self, text):
        if text in self._vectors:
            return np.array(self._vectors[text], dtype=float)
        return np.array([float(len(text)) % 3, 0.0, 0.0])


def _make_agent():
    """构造未初始化的 RAGKnowledgeAgent 实例，跳过真实依赖加载。"""
    agent = object.__new__(_load_rag_agent_class())
    agent._retrieval_config = {}
    agent._empty_result_cache = set()
    agent._query_embedding_cache = OrderedDict()
    return agent


# ── L1 参数校验 ──

def test_validate_query_rejects_invalid():
    agent = _make_agent()
    assert not agent._validate_query("")
    assert not agent._validate_query("   ")
    assert not agent._validate_query(None)
    assert not agent._validate_query(123)
    assert not agent._validate_query("a")  # 长度 < 2
    assert not agent._validate_query("x" * 501)  # 长度 > 500


def test_validate_query_accepts_valid():
    agent = _make_agent()
    assert agent._validate_query("ab")
    assert agent._validate_query("  ab  ")
    assert agent._validate_query("x" * 500)


# ── L3 空值缓存 ──

def test_empty_result_cache_roundtrip():
    agent = _make_agent()
    assert not agent._is_empty_result("无结果查询")
    agent._mark_empty_result("无结果查询")
    assert agent._is_empty_result("无结果查询")


# ── L5 embedding LRU 缓存 ──

def test_embedding_cache_lru_eviction():
    agent = _make_agent()
    agent._retrieval_config = {"embedding_cache_size": 2}

    agent._cache_embedding("a", [1.0])
    agent._cache_embedding("b", [2.0])
    # 访问 a，使其变为最近使用
    assert agent._get_cached_embedding("a") == [1.0]
    # 写入 c，淘汰最久未使用的 b
    agent._cache_embedding("c", [3.0])

    assert agent._get_cached_embedding("a") == [1.0]
    assert agent._get_cached_embedding("b") is None
    assert agent._get_cached_embedding("c") == [3.0]


def test_embedding_cache_miss_returns_none():
    agent = _make_agent()
    assert agent._get_cached_embedding("不存在") is None


# ── 向量阈值去重 ──

def test_dedup_by_similarity_removes_duplicates():
    agent = _make_agent()
    agent.embedding_model = _FakeEmbedding({
        "A": [1.0, 0.0, 0.0],
        "B": [1.0, 0.0, 0.0],  # 与 A 完全相同
        "C": [0.0, 1.0, 0.0],  # 与 A 正交
    })
    docs = [
        {"content": "A", "metadata": {}},
        {"content": "B", "metadata": {}},
        {"content": "C", "metadata": {}},
    ]

    result = agent._dedup_by_similarity(docs, threshold=0.5)

    # B 与 A 相似度 1.0 > 0.5，被去重；C 保留
    assert [d["content"] for d in result] == ["A", "C"]


def test_dedup_by_similarity_single_doc():
    agent = _make_agent()
    agent.embedding_model = _FakeEmbedding({})
    docs = [{"content": "A", "metadata": {}}]
    assert agent._dedup_by_similarity(docs) == docs


# ── 父文档召回 ──

def test_parent_document_recall_adds_page():
    agent = _make_agent()
    docs = [{"content": "正文内容", "metadata": {"page": 3}}]
    result = agent._parent_document_recall(docs)
    assert result[0]["content"].startswith("[来源: 第3页]")


def test_parent_document_recall_without_page():
    agent = _make_agent()
    docs = [{"content": "正文内容", "metadata": {}}]
    result = agent._parent_document_recall(docs)
    assert result[0]["content"] == "正文内容"


# ── 写入去重 ──

def test_compute_content_hash_deterministic():
    assert compute_content_hash("abc") == compute_content_hash("abc")
    assert compute_content_hash("abc") != compute_content_hash("abd")


def test_deduplicate_chunks_md5():
    docs = [
        {"id": "a", "content": "相同内容"},
        {"id": "b", "content": "相同内容"},
    ]
    result = deduplicate_chunks(docs)
    assert len(result) == 1
    assert result[0]["id"] == "a"


def test_deduplicate_chunks_boundary_overlap():
    a = {"id": "a", "content": "abcdefghijklmnop"}
    b = {"id": "b", "content": "abcdefghijklmnopq"}  # 重叠度 15/16 > 0.8
    result = deduplicate_chunks([a, b], enable_md5=True, enable_boundary=True)
    assert len(result) == 1
    assert result[0]["id"] == "a"


def test_deduplicate_chunks_keeps_distinct():
    docs = [
        {"id": "a", "content": "完全不同的内容甲"},
        {"id": "b", "content": "另一个完全不同的内容乙"},
    ]
    result = deduplicate_chunks(docs)
    assert len(result) == 2


def test_overlap_ratio_high():
    assert _overlap_ratio("abcdefghijklmnop", "abcdefghijklmnopq") > 0.8
