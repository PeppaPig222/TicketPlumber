#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
诊断状态黑板与统一消息契约

解决多 Agent 通信中「消息协议无 Schema」导致的实体丢失与状态污染问题：

- AgentResult：专业 Agent 的统一输出契约，集中声明 status / degraded / inconsistency_found 信号，
  替代下游散落各处的字符串判断与多套 status 取值。
- DiagnosisState：诊断黑板，集中管理跨轮事实（facts），替代散落在 DiagnosisService 里的 state dict。
"""
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from utils.errors import ExecutionStatus


class AgentResult(BaseModel):
    """专业 Agent 统一输出契约。

    使用 extra="allow" 保留业务扩展字段（path_verdict、responsible_party 等），
    同时用显式字段统一 status 与 degraded 信号，避免下游靠字符串猜状态。
    """

    model_config = ConfigDict(extra="allow")

    status: str = ExecutionStatus.SUCCESS.value
    summary: str = ""
    evidence: List[str] = Field(default_factory=list)
    next_actions: List[str] = Field(default_factory=list)
    recommended_skills: List[str] = Field(default_factory=list)
    tools_called: List[str] = Field(default_factory=list)
    # 单一降级信号：status 为失败态时自动置 True，下游无需再枚举多套状态值
    degraded: bool = False
    # 结构化不一致信号：替代对 summary 做 "不一致" 字符串包含判断
    inconsistency_found: Optional[bool] = None

    @model_validator(mode="after")
    def _sync_degraded(self) -> "AgentResult":
        if self.status in {
            ExecutionStatus.ERROR.value,
            ExecutionStatus.TIMEOUT.value,
            ExecutionStatus.DEGRADED.value,
            ExecutionStatus.PARTIAL_FAILURE.value,
        }:
            self.degraded = True
        return self

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump(exclude_none=True)


class DiagnosisState(BaseModel):
    """诊断黑板：一次诊断过程的集中状态载体。"""

    model_config = ConfigDict(extra="allow")

    query: str = ""
    ticket: Dict[str, Any] = Field(default_factory=dict)
    facts: Dict[str, Any] = Field(default_factory=dict)
    rounds: List[Dict[str, Any]] = Field(default_factory=list)

    # 跨轮合并时明确排除的控制字段（不沉淀进 facts，避免污染下游结论）
    EXCLUDED_KEYS: ClassVar[set] = {
        "duration_ms",
        "tools_called",
        "summary",
        "status",
        "recommended_skills",
        "next_actions",
        "error",
        "degraded",
    }

    def to_intention_collected_data(self) -> Dict[str, Any]:
        """兼容 IntentionAgent 期望的 collected_data 结构。"""
        return {"facts": self.facts, "rounds": self.rounds}

    def merge_intention(self, key_entities: Dict[str, Any]) -> None:
        """合并意图识别到的关键实体（仅保留非空值）。"""
        for key, value in (key_entities or {}).items():
            if value:
                self.facts[key] = value

    def merge_round(self, round_result: Dict[str, Any]) -> None:
        """合并单轮 agent 结果中的业务事实，控制字段由 EXCLUDED_KEYS 排除。"""
        for result in round_result.get("results", []):
            data = result.get("data", {}) or {}
            for key, value in data.items():
                if key in self.EXCLUDED_KEYS:
                    continue
                self.facts[key] = value

    def add_round(self, intent: str, round_result: Dict[str, Any]) -> None:
        self.rounds.append({"intent": intent, "result": round_result})
