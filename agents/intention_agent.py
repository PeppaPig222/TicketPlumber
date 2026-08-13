"""
兼容入口：保留旧的 IntentionAgent 名称，但内部逻辑已对齐工单诊断场景。
"""
import json
from typing import List, Optional, Union

from agentscope.agent import AgentBase
from agentscope.message import Msg

from agents.diagnosis_intention_agent import DiagnosisIntentionAgent


class IntentionAgent(AgentBase):
    """兼容旧调用方的诊断意图识别 Agent。"""

    def __init__(self, name: str = "IntentionAgent", model=None, rag_agent=None, memory_manager=None):
        super().__init__()
        self.name = name
        self.model = model
        self.memory_manager = memory_manager
        self._delegate = DiagnosisIntentionAgent(
            name=name, rag_agent=rag_agent, memory_manager=memory_manager
        )

    async def reply(self, x: Optional[Union[Msg, List[Msg]]] = None) -> Msg:
        if isinstance(x, list):
            user_query = x[-1].content if x else ""
            payload = {"query": user_query, "round_num": 1}
            ticket = self._extract_ticket_payload(x)
            if ticket:
                payload["ticket"] = ticket
            return await self._delegate.reply(
                Msg(name="user", content=json.dumps(payload, ensure_ascii=False), role="user")
            )

        if isinstance(x, Msg):
            return await self._delegate.reply(x)

        return await self._delegate.reply(
            Msg(name="user", content=json.dumps({"query": ""}, ensure_ascii=False), role="user")
        )

    def _parse_payload(self, content) -> dict:
        if isinstance(content, dict):
            return dict(content)
        if isinstance(content, str):
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return {"query": content}
        return {}

    def _extract_ticket_payload(self, messages: List[Msg]) -> dict:
        for msg in reversed(messages[:-1]):
            content = getattr(msg, "content", "")
            if not isinstance(content, str):
                continue
            if "WO-" in content or "ORD-" in content or "商户" in content:
                return {"description": content}
        return {}
