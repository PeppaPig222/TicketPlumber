#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
诊断专业 Agent 的公共基类。
"""
from __future__ import annotations

import inspect
import json
import logging
from typing import Any, Dict, Iterable, List, Optional

from agentscope.agent import AgentBase
from agentscope.message import Msg

from config import SYSTEM_CONFIG
from context.diagnosis_state import AgentResult
from utils.tool_registry import tool_registry

logger = logging.getLogger(__name__)


class BaseDiagnosisAgent(AgentBase):
    """为专业诊断 Agent 提供统一输入解析、Skill 调用和结构化输出。"""

    allowed_skills: Iterable[str] = ()
    # 工具白名单（执行层物理隔离）：越界调用被 _execute_tool 拦截，防止 LLM 自主越权
    allowed_tools: Iterable[str] = ()
    # 单 Agent 内最大探索步数上限（ReAct/自主决策的护栏，确定性路径不会超步）
    max_steps: int = 8

    def __init__(
        self,
        name: str,
        model=None,
        skill_registry: Optional[SkillRegistry] = None,
        memory_manager=None,
        rag_agent=None,
        **kwargs,
    ):
        super().__init__()
        _ = kwargs
        self.name = name
        self.model = model
        # 延迟导入，避免与 skills.registry 的循环依赖
        from skills.registry import SkillRegistry
        self.skill_registry = skill_registry or SkillRegistry()
        self.memory_manager = memory_manager
        self.rag_agent = rag_agent

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

    def _pending_hypotheses(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从黑板读待验证假设（由 Scheduler 假设路由注入 pending_hypotheses）。

        返回 status == "pending" 的假设列表；开关关闭或未路由时为空列表。
        """
        facts = (context.get("collected_data") or {}).get("facts", {}) or {}
        pending = facts.get("pending_hypotheses") or []
        if isinstance(pending, dict):
            pending = [pending]
        return [
            hyp for hyp in pending
            if isinstance(hyp, dict) and hyp.get("status") == "pending" and hyp.get("type")
        ]

    def _resolved_hypothesis(
        self, hypothesis: Dict[str, Any], verified: bool, evidence: List[str]
    ) -> Dict[str, Any]:
        """把待验证假设写成已验证/已驳斥（状态机 pending → verified/refuted）。

        保留 type/detail/proposed_by，附加 verified_by 与验证证据。
        """
        resolved = dict(hypothesis)
        resolved["status"] = "verified" if verified else "refuted"
        resolved["verified_by"] = self.name
        resolved["evidence"] = list(hypothesis.get("evidence") or []) + list(evidence)
        return resolved

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

    async def _execute_tool(self, tool_name: str, **kwargs) -> Dict[str, Any]:
        """执行工具调用，带角色白名单校验（Role-aware 边界控制）。

        与 _run_skill 的 Skill 白名单对应，这里是 Tool 白名单：
        越界调用返回 error 并记录，形成物理隔离（防 LLM 自主越权）。
        """
        if self.allowed_tools and tool_name not in self.allowed_tools:
            logger.warning(
                "Tool not allowed for agent",
                extra={"agent": self.name, "tool_name": tool_name},
            )
            return {
                "status": "error",
                "tool": tool_name,
                "error_code": "TOOL_NOT_ALLOWED",
                "message": f"{self.name} 不允许调用工具 {tool_name}",
                "data": {},
            }
        return await tool_registry.execute(tool_name, **kwargs)

    @staticmethod
    def _compute_evidence_coverage(
        evidence: List[str], tools_called: List[str]
    ) -> Optional[float]:
        """证据覆盖率（启发式，非模型置信度）。

        有工具调用时 = 证据条数 / 工具数；无工具调用时 = 有无证据。
        上限 1.0，避免命名成 confidence 造成「名不副实」。
        """
        if not tools_called:
            return 1.0 if evidence else None
        return min(1.0, round(len(evidence) / len(tools_called), 2))

    def _autonomy_enabled(self) -> bool:
        """LLM 局部自主是否可用：配置开关开启 且 已注入 LLM 模型。

        测试/降级场景下 model 为 None，直接短路为确定性路径，
        保证「规则优先 + LLM 兜底」的分层治理。
        """
        return bool(SYSTEM_CONFIG.get("enable_llm_autonomy", False)) and self.model is not None

    @staticmethod
    def _parse_json(raw: str) -> Optional[Dict[str, Any]]:
        """从 LLM 返回值中容错提取 JSON dict（兼容 markdown code fence 与前后缀）。"""
        if not raw:
            return None
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, ValueError):
            return None

    async def _call_llm(self, messages: List[Dict[str, str]]) -> Optional[str]:
        """LLM 接入点（阶段2：Agent 局部自主能力的可选增强）。

        无 model（或调用失败）时返回 None，调用方降级到确定性规则路径，
        保证「规则优先 + LLM 兜底」的分层治理，不破坏可复现性。

        兼容多种返回形态：str / dict / Msg / agentscope ChatResponse / 异步生成器。
        """
        if self.model is None:
            return None
        try:
            response = await self.model(messages)
            return await self._collect_llm_text(response)
        except Exception as e:
            logger.warning(
                "LLM call failed", extra={"agent": self.name, "error": str(e)}
            )
            return None

    @classmethod
    async def _collect_llm_text(cls, response: Any) -> Optional[str]:
        """从 LLM 返回值中提取纯文本，统一流式与非流式形态。"""
        if response is None:
            return None
        # 流式：异步生成器（agentscope OpenAIChatModel 默认 stream=True）
        if inspect.isasyncgen(response):
            parts = []
            async for chunk in response:
                text = cls._text_from_response(chunk)
                if text:
                    parts.append(text)
            return "".join(parts) or None
        return cls._text_from_response(response) or None

    @classmethod
    def _text_from_response(cls, response: Any) -> str:
        """从单个 LLM 响应对象提取文本内容。"""
        if isinstance(response, str):
            return response
        # Msg / ChatResponse / dict 等均有 content 字段；dict-like 对象优先按 dict 取
        content = (
            response.get("content")
            if isinstance(response, dict)
            else getattr(response, "content", None)
        )
        if isinstance(content, str):
            return content
        if isinstance(content, (list, tuple)):
            return cls._text_from_blocks(content)
        if isinstance(content, dict):
            return content.get("text") or content.get("content") or ""
        if isinstance(response, dict):
            return response.get("text") or ""
        return str(content) if content is not None else ""

    @staticmethod
    def _text_from_blocks(blocks: Iterable[Any]) -> str:
        """从 agentscope ChatResponse.content（TextBlock 列表）提取拼接文本。"""
        parts = []
        for block in blocks:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", "") or "")
            elif getattr(block, "type", None) == "text":
                parts.append(getattr(block, "text", "") or "")
        return "".join(parts)

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
        evidence_list = evidence or []
        tools_list = tools_called or []
        result = AgentResult(
            status=status,
            summary=summary,
            evidence=evidence_list,
            next_actions=next_actions or [],
            recommended_skills=recommended_skills or [],
            tools_called=tools_list,
            evidence_coverage=self._compute_evidence_coverage(evidence_list, tools_list),
            **extra_data,
        )
        return Msg(
            name=self.name,
            content=json.dumps(result.to_dict(), ensure_ascii=False),
            role="assistant",
        )

    def _dedupe_tools(self, skill_results: Iterable[Dict[str, Any]]) -> List[str]:
        tools: List[str] = []
        for item in skill_results:
            for tool in item.get("tools_called", []) or []:
                if tool not in tools:
                    tools.append(tool)
        return tools
