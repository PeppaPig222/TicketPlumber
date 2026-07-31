#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
诊断全链路追踪数据收集器
收集每一轮 Loop 中每个 Agent 的执行数据，供前端可视化面板展示
"""
import time
import json
from typing import Dict, Any, List
from datetime import datetime


class TraceCollector:
    """收集诊断全链路追踪数据"""

    def __init__(self, ticket_id: str = ""):
        self.ticket_id = ticket_id
        self.start_time = time.time()
        self.rounds: List[Dict[str, Any]] = []
        self._current_round: Dict[str, Any] = {}

    def start_round(self, round_num: int, intent: str = ""):
        """开始新的一轮诊断"""
        self._current_round = {
            "round_num": round_num,
            "intent": intent,
            "start_time": time.time(),
            "agents": [],
            "duration_ms": 0,
        }

    def record_agent(
        self,
        agent_name: str,
        priority: int,
        status: str,
        duration_ms: float,
        output_summary: str = "",
        tools_called: List[str] = None,
        recommended_skills: List[str] = None,
        evidence: List[str] = None,
    ):
        """记录单个 Agent 执行数据"""
        self._current_round.setdefault("agents", []).append({
            "agent_name": agent_name,
            "priority": priority,
            "status": status,
            "duration_ms": round(duration_ms, 1),
            "output_summary": output_summary[:200],
            "tools_called": tools_called or [],
            "recommended_skills": recommended_skills or [],
            "evidence": evidence or [],
        })

    def end_round(self, decision: str = ""):
        """结束当前轮次"""
        self._current_round["duration_ms"] = round(
            (time.time() - self._current_round["start_time"]) * 1000, 1
        )
        self._current_round["decision"] = decision
        self.rounds.append(self._current_round)
        self._current_round = {}

    def get_trace(self) -> Dict[str, Any]:
        """获取完整追踪数据"""
        total_duration_ms = round((time.time() - self.start_time) * 1000, 1)
        return {
            "ticket_id": self.ticket_id,
            "start_time": datetime.fromtimestamp(self.start_time).isoformat(),
            "total_duration_ms": total_duration_ms,
            "total_rounds": len(self.rounds),
            "rounds": self.rounds,
        }

    def get_trace_json(self) -> str:
        """获取追踪数据的 JSON 字符串"""
        return json.dumps(self.get_trace(), ensure_ascii=False, indent=2)


# SSE 事件推送辅助
def format_trace_sse(trace: Dict[str, Any]) -> list:
    """将追踪数据格式化为 SSE 事件列表"""
    events = []
    for round_data in trace.get("rounds", []):
        for agent in round_data.get("agents", []):
            events.append({
                "event": "agent_update",
                "data": {
                    "round": round_data["round_num"],
                    "agent": agent["agent_name"],
                    "status": agent["status"],
                    "duration_ms": agent["duration_ms"],
                    "tools": agent["tools_called"],
                    "recommended_skills": agent.get("recommended_skills", []),
                }
            })
        events.append({
            "event": "round_complete",
            "data": {
                "round": round_data["round_num"],
                "decision": round_data["decision"],
                "duration_ms": round_data["duration_ms"],
            }
        })
    events.append({
        "event": "diagnosis_complete",
        "data": {"total_duration_ms": trace["total_duration_ms"]},
    })
    return events
