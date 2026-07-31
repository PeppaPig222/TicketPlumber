#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
工单诊断 Skill 注册中心。
"""
import json
from typing import Any, Dict, Optional

from agentscope.message import Msg

from agents.diagnosis_agents import build_diagnosis_agents


class SkillRegistry:
    """为诊断场景提供原子 Skill Agent 的注册、发现与执行能力。"""

    def __init__(self):
        self._skills: Dict[str, Any] = build_diagnosis_agents()

    def __contains__(self, skill_name: str) -> bool:
        return skill_name in self._skills

    def __getitem__(self, skill_name: str) -> Any:
        return self._skills[skill_name]

    def get(self, skill_name: str, default=None) -> Any:
        return self._skills.get(skill_name, default)

    def keys(self):
        return self._skills.keys()

    def items(self):
        return self._skills.items()

    async def execute(
        self,
        skill_name: str,
        context: Optional[Dict[str, Any]] = None,
        reason: str = "",
        expected_output: str = "",
        previous_results: Optional[list] = None,
    ) -> Dict[str, Any]:
        """执行单个诊断 Skill。"""
        if skill_name not in self._skills:
            return {
                "error": f"Skill 未注册: {skill_name}",
                "status": "error",
                "summary": f"找不到 Skill {skill_name}",
                "tools_called": [],
            }

        agent = self._skills[skill_name]
        payload = {
            "context": context or {},
            "reason": reason,
            "expected_output": expected_output,
            "previous_results": previous_results or [],
        }
        response = await agent.reply(
            Msg(
                name="SkillRegistry",
                content=json.dumps(payload, ensure_ascii=False),
                role="user",
            )
        )
        if isinstance(response.content, dict):
            return response.content
        try:
            return json.loads(response.content)
        except json.JSONDecodeError:
            return {
                "status": "success",
                "summary": str(response.content),
                "output": response.content,
                "tools_called": [],
            }
