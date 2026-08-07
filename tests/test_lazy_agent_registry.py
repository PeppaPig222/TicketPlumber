#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证 LazyAgentRegistry 能从 .claude/skills 加载诊断专业 Agent 插件。"""
import os
import sys

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from agents.lazy_agent_registry import LazyAgentRegistry
from agents.code_agent import CodeAgent
from agents.operation_agent import OperationAgent
from agents.data_agent import DataAgent
from agents.resolution_agent import ResolutionAgent
from skills.registry import SkillRegistry


@pytest.fixture
def registry():
    return LazyAgentRegistry(
        model=None,
        cache={},
        agent_kwargs={"skill_registry": SkillRegistry()},
    )


class TestLazyAgentRegistryDiscovery:
    def test_registry_discovers_diagnosis_agents(self, registry):
        keys = registry.keys()
        assert "code-agent" in keys
        assert "operation-agent" in keys
        assert "data-agent" in keys
        assert "resolution-agent" in keys

    def test_registry_contains_legacy_skills(self, registry):
        # ask-question / query-info / memory-query 仍然存在
        keys = registry.keys()
        assert "ask-question" in keys
        assert "query-info" in keys
        assert "memory-query" in keys


class TestLazyAgentRegistryLoading:
    def test_load_code_agent(self, registry):
        agent = registry["code-agent"]
        assert isinstance(agent, CodeAgent)
        assert agent.name == "code-agent"
        assert agent.skill_registry is not None

    def test_load_operation_agent(self, registry):
        agent = registry["operation-agent"]
        assert isinstance(agent, OperationAgent)
        assert agent.name == "operation-agent"
        assert agent.skill_registry is not None

    def test_load_data_agent(self, registry):
        agent = registry["data-agent"]
        assert isinstance(agent, DataAgent)
        assert agent.name == "data-agent"
        assert agent.skill_registry is not None

    def test_load_resolution_agent(self, registry):
        agent = registry["resolution-agent"]
        assert isinstance(agent, ResolutionAgent)
        assert agent.name == "resolution-agent"
        assert agent.skill_registry is not None

    def test_legacy_mapping_still_works(self, registry):
        # rag_knowledge 应解析到 ask-question skill 目录
        # 不实际加载 ask-question，避免其依赖 RAG 数据目录
        resolved = registry._resolve_agent_name("rag_knowledge")
        assert resolved == "ask-question"

    def test_pascal_case_alias_for_professional_agents(self, registry):
        assert registry._resolve_agent_name("CodeAgent") == "code-agent"
        assert registry._resolve_agent_name("OperationAgent") == "operation-agent"
        assert registry._resolve_agent_name("DataAgent") == "data-agent"
        assert registry._resolve_agent_name("ResolutionAgent") == "resolution-agent"

    def test_load_professional_agent_by_pascal_name(self, registry):
        agent = registry["CodeAgent"]
        assert isinstance(agent, CodeAgent)
        assert agent.name == "CodeAgent"
        assert agent.skill_registry is not None

    def test_skill_priority_over_custom_factory(self):
        """skill 目录应优先于 custom_factories 加载。"""
        custom_called = False

        def custom_factory():
            nonlocal custom_called
            custom_called = True
            return None

        reg = LazyAgentRegistry(
            model=None,
            cache={},
            custom_factories={"code-agent": custom_factory},
            agent_kwargs={"skill_registry": SkillRegistry()},
        )

        agent = reg["code-agent"]
        assert isinstance(agent, CodeAgent)
        assert not custom_called


class TestLazyAgentRegistryCaching:
    def test_loaded_agent_is_cached(self, registry):
        agent1 = registry["code-agent"]
        agent2 = registry["code-agent"]
        assert agent1 is agent2
