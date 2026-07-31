#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
工单诊断 Skill 注册中心。
"""
from typing import Any, Dict

from agents.diagnosis_agents import build_diagnosis_agents


class SkillRegistry:
    """为诊断场景提供原子 Skill Agent 的注册与发现能力。"""

    def __init__(self):
        self._agents: Dict[str, Any] = build_diagnosis_agents()

    def __contains__(self, agent_name: str) -> bool:
        return agent_name in self._agents

    def __getitem__(self, agent_name: str) -> Any:
        return self._agents[agent_name]

    def get(self, agent_name: str, default=None) -> Any:
        return self._agents.get(agent_name, default)

    def keys(self):
        return self._agents.keys()

    def items(self):
        return self._agents.items()
