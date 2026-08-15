#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
诊断专业 Agent 的公共基类。
"""
import json
from typing import Any, Dict, Iterable, List, Optional

from agentscope.agent import AgentBase
from agentscope.message import Msg

from skills.registry import SkillRegistry


class BaseDiagnosisAgent(AgentBase):
    """为专业诊断 Agent 提供统一输入解析、Skill 调用和结构化输出。"""

    allowed_skills: Iterable[str] = ()

    def __init__(
        self,
        name: str,
        model=None,
        skill_registry: Optional[SkillRegistry] = None,
        memory_manager=None,
        **kwargs,
    ):
        super().__init__()
        _ = kwargs
        self.name = name
        self.model = model
        self.skill_registry = skill_registry or SkillRegistry()
        self.memory_manager = memory_manager

    def _parse_payload(self, msg: Optional[Msg]) -> Dict[str, Any]:
        if not msg or not getattr(msg, "content", None):
            return {}
        if isinstance(msg.content, dict):
            return msg.content
        try:
            return json.loads(msg.content)
        except json.JSONDecodeError:
            return {"context": {}, "raw_content": str(msg.content)}

    def _get_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return payload.get("context", {}) or {}

    def _get_previous_results(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        return payload.get("previous_results", []) or []

    def _find_previous_result(self, previous_results: List[Dict[str, Any]], agent_name: str) -> Dict[str, Any]:
        for item in previous_results:
            if item.get("agent_name") == agent_name:
                return item.get("result", {}).get("data", {}) or {}
        return {}

    async def _run_skill(
        self,
        skill_name: str,
        context: Dict[str, Any],
        reason: str,
        expected_output: str,
        previous_results: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if self.allowed_skills and skill_name not in self.allowed_skills:
            return {
                "status": "error",
                "summary": f"{self.name} 不允许调用 Skill {skill_name}",
                "tools_called": [],
            }
        return await self.skill_registry.execute(
            skill_name=skill_name,
            context=context,
            reason=reason,
            expected_output=expected_output,
            previous_results=previous_results or [],
        )

    def _response(
        self,
        *,
        status: str,
        summary: str,
        evidence: Optional[List[str]] = None,
        next_actions: Optional[List[str]] = None,
        recommended_skills: Optional[List[str]] = None,
        tools_called: Optional[List[str]] = None,
        **extra_data,
    ) -> Msg:
        payload = {
            "status": status,
            "summary": summary,
            "evidence": evidence or [],
            "next_actions": next_actions or [],
            "recommended_skills": recommended_skills or [],
            "tools_called": tools_called or [],
        }
        payload.update(extra_data)
        return Msg(
            name=self.name,
            content=json.dumps(payload, ensure_ascii=False),
            role="assistant",
        )

    def _dedupe_tools(self, skill_results: Iterable[Dict[str, Any]]) -> List[str]:
        tools: List[str] = []
        for item in skill_results:
            for tool in item.get("tools_called", []) or []:
                if tool not in tools:
                    tools.append(tool)
        return tools
