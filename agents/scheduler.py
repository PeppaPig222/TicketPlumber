#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
确定性调度器 Scheduler

职责：把「scenario × round → schedule」的确定性编排逻辑从伪 Agent 壳中剥离出来，
作为普通类显性化。它不调用任何 LLM，只负责：
1. 用 StrategyMatrix 依据 scenario / round 生成调度计划；
2. 按 priority 分组、依赖（depends_on）与实体门槛（skip_if_missing）执行；
3. 聚合子 Agent 结果，供上层 LoopDecider 决策。

与旧 OrchestrationAgent 的差异：不继承 AgentBase、不接收 Msg，而是直接接收
intention_data dict 并返回 round_result dict；StrategyMatrix 由本类持有。
"""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from agentscope.message import Msg

from agents.scheduling import SchedulingContext, StrategyMatrix

logger = logging.getLogger(__name__)


class Scheduler:
    """确定性调度器 - 依据策略矩阵协调多个子智能体。"""

    def __init__(
        self,
        name: str = "Scheduler",
        agent_registry: Dict[str, Any] = None,
        memory_manager=None,
        strategy_matrix: Optional[StrategyMatrix] = None,
        rag_available: bool = False,
    ):
        self.name = name
        self.agent_registry = agent_registry or {}
        self.memory_manager = memory_manager
        self.strategy_matrix = strategy_matrix or StrategyMatrix()
        self.rag_available = rag_available

    def register_agent(self, agent_name: str, agent):
        """注册子智能体。"""
        self.agent_registry[agent_name] = agent
        logger.info("Registered agent", extra={"agent_name": agent_name})

    def unregister_agent(self, agent_name: str):
        """注销子智能体。"""
        if agent_name in self.agent_registry:
            del self.agent_registry[agent_name]
            logger.info("Unregistered agent", extra={"agent_name": agent_name})

    async def run(self, intention_data: Dict[str, Any]) -> Dict[str, Any]:
        """执行一轮调度，返回 round_result dict。"""
        tasks, schedule_metadata = self._build_schedule(intention_data)
        agent_schedule = [t.to_dict() for t in tasks]

        # 把本类生成的 schedule/metadata 透传，供子 Agent 与记忆读取
        intention_data = dict(intention_data)
        intention_data["agent_schedule"] = agent_schedule
        intention_data["schedule_metadata"] = schedule_metadata

        if not agent_schedule:
            return {
                "status": "no_agents",
                "message": "没有需要调度的智能体",
                "agents_executed": 0,
                "results": [],
            }

        sorted_schedule = sorted(
            agent_schedule, key=lambda x: x.get("priority", 999)
        )
        context = self._prepare_context(intention_data)

        # 按 priority 分组并行执行
        results: List[Dict[str, Any]] = []
        current_priority = None
        parallel_tasks: List[Dict[str, Any]] = []

        for task in sorted_schedule:
            priority = task.get("priority", 0)
            if current_priority is not None and priority != current_priority:
                if parallel_tasks:
                    batch_results = await self._execute_parallel_agents(
                        parallel_tasks, context, results
                    )
                    results.extend(batch_results)
                    parallel_tasks = []
            current_priority = priority
            parallel_tasks.append(task)

        if parallel_tasks:
            batch_results = await self._execute_parallel_agents(
                parallel_tasks, context, results
            )
            results.extend(batch_results)

        final_result = self._aggregate_results(results, intention_data)

        if self.memory_manager:
            self._update_memory(intention_data, results)

        return final_result

    def _build_schedule(
        self, intention_data: Dict[str, Any]
    ) -> tuple:
        """用策略矩阵依据 scenario × round 生成调度计划。"""
        ctx = SchedulingContext(
            scenario=intention_data.get("scenario", "generic_ticket_diagnosis"),
            round_num=intention_data.get("round_num", 1),
            query=intention_data.get("query", ""),
            ticket=intention_data.get("ticket", {}) or {},
            collected_data=intention_data.get("collected_data", {}) or {},
            key_entities=intention_data.get("key_entities", {}) or {},
            rag_available=self.rag_available,
        )
        return self.strategy_matrix.build(ctx)

    def _prepare_context(self, intention_data: Dict[str, Any]) -> Dict[str, Any]:
        """准备子智能体使用的上下文。"""
        context = {
            "reasoning": intention_data.get("reasoning", ""),
            "intents": intention_data.get("intents", []),
            "key_entities": intention_data.get("key_entities", {}),
            "rewritten_query": intention_data.get("rewritten_query", ""),
        }

        passthrough_fields = [
            "intent",
            "scenario",
            "round_num",
            "ticket",
            "ticket_id",
            "issue_type",
            "query",
            "collected_data",
            "schedule_metadata",
        ]
        for field in passthrough_fields:
            if field in intention_data:
                context[field] = intention_data.get(field)

        if self.memory_manager:
            recent_context = self.memory_manager.short_term.get_recent_context(3)
            context["recent_dialogue"] = recent_context
            preferences = self.memory_manager.long_term.get_preference()
            context["user_preferences"] = preferences

        return context

    async def _execute_parallel_agents(
        self,
        tasks: List[Dict[str, Any]],
        context: Dict[str, Any],
        previous_results: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """并行执行同 priority 任务，支持 depends_on 与 skip_if_missing。"""
        if not tasks:
            return []

        completed_agent_names = {
            r.get("agent_name")
            for r in previous_results
            if r.get("result", {}).get("status") != "error"
        }
        key_entities = context.get("key_entities", {}) or {}
        collected_facts = context.get("collected_data", {}).get("facts", {}) or {}

        executable_tasks: List[Dict[str, Any]] = []
        skipped_results: List[Dict[str, Any]] = []

        for task in tasks:
            agent_name = task.get("agent_name")

            required_entities = task.get("required_entities", [])
            if task.get("skip_if_missing") and required_entities:
                missing = [
                    e for e in required_entities
                    if not (key_entities.get(e) or collected_facts.get(e))
                ]
                if missing:
                    logger.info(
                        "Skipping agent due to missing entities",
                        extra={"agent_name": agent_name, "missing_entities": missing},
                    )
                    skipped_results.append({
                        "agent_name": agent_name,
                        "priority": task.get("priority", 0),
                        "result": {
                            "status": "skipped",
                            "agent_name": agent_name,
                            "data": {"missing_entities": missing},
                            "message": f"缺少必要实体，跳过执行: {missing}",
                        },
                    })
                    continue

            depends_on = task.get("depends_on", [])
            unmet = [d for d in depends_on if d not in completed_agent_names]
            if unmet:
                task["_deferred_unmet"] = unmet

            executable_tasks.append(task)

        results: List[Dict[str, Any]] = list(skipped_results)
        remaining = list(executable_tasks)
        max_iterations = len(remaining) + 1
        iteration = 0

        while remaining and iteration < max_iterations:
            iteration += 1
            ready: List[Dict[str, Any]] = []
            still_remaining: List[Dict[str, Any]] = []

            for task in remaining:
                depends_on = task.get("depends_on", [])
                deferred = task.pop("_deferred_unmet", None)
                unmet = [d for d in depends_on if d not in completed_agent_names]
                if unmet:
                    if deferred == unmet:
                        logger.warning(
                            "Dependencies still unmet, executing anyway",
                            extra={
                                "agent_name": task.get("agent_name"),
                                "unmet_dependencies": unmet,
                            },
                        )
                        ready.append(task)
                    else:
                        task["_deferred_unmet"] = unmet
                        still_remaining.append(task)
                else:
                    ready.append(task)

            if not ready:
                logger.warning(
                    "No ready tasks but remaining exist, executing all remaining",
                )
                ready = remaining
                still_remaining = []

            batch_results = await self._execute_agent_batch(
                ready, context, previous_results + results
            )
            results.extend(batch_results)
            completed_agent_names.update(
                r.get("agent_name")
                for r in batch_results
                if r.get("result", {}).get("status") != "error"
            )
            remaining = still_remaining

        return results

    async def _execute_agent_batch(
        self,
        tasks: List[Dict[str, Any]],
        context: Dict[str, Any],
        previous_results: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """执行一批已准备好的 Agent 任务（可并行）。"""
        if not tasks:
            return []

        if len(tasks) == 1:
            task = tasks[0]
            result = await self._execute_agent(
                agent_name=task.get("agent_name"),
                context=context,
                reason=task.get("reason", ""),
                expected_output=task.get("expected_output", ""),
                previous_results=previous_results,
            )
            return [{
                "agent_name": task.get("agent_name"),
                "priority": task.get("priority", 0),
                "result": result,
            }]

        parallel_coroutines = []
        for task in tasks:
            parallel_coroutines.append((
                task.get("agent_name"),
                task.get("priority", 0),
                self._execute_agent(
                    agent_name=task.get("agent_name"),
                    context=context,
                    reason=task.get("reason", ""),
                    expected_output=task.get("expected_output", ""),
                    previous_results=previous_results,
                ),
            ))

        execution_results = await asyncio.gather(
            *[coro for _, _, coro in parallel_coroutines],
            return_exceptions=True,
        )

        results = []
        for (agent_name, priority, _), exec_result in zip(
            parallel_coroutines, execution_results
        ):
            if isinstance(exec_result, Exception):
                logger.error(
                    "Parallel agent execution failed",
                    extra={"agent_name": agent_name, "status": "error"},
                    exc_info=True,
                )
                result = {
                    "status": "error",
                    "agent_name": agent_name,
                    "data": {"error": str(exec_result)},
                    "message": f"并行执行失败: {str(exec_result)}",
                }
            else:
                result = exec_result

            results.append({
                "agent_name": agent_name,
                "priority": priority,
                "result": result,
            })

        return results

    async def _execute_agent(
        self,
        agent_name: str,
        context: Dict[str, Any],
        reason: str,
        expected_output: str,
        previous_results: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """执行单个子智能体。"""
        if agent_name not in self.agent_registry:
            logger.warning("Agent not registered", extra={"agent_name": agent_name})
            return {"status": "error", "message": f"智能体未注册: {agent_name}"}

        agent = self.agent_registry[agent_name]
        input_msg = Msg(
            name="Scheduler",
            content=json.dumps({
                "context": context,
                "reason": reason,
                "expected_output": expected_output,
                "previous_results": previous_results,
            }, ensure_ascii=False),
            role="user",
        )

        try:
            response = await agent.reply(input_msg)
            if isinstance(response.content, str):
                try:
                    result = json.loads(response.content)
                except json.JSONDecodeError:
                    result = {"output": response.content}
            else:
                result = response.content

            if isinstance(result, dict) and "error" in result:
                return {
                    "status": "error",
                    "agent_name": agent_name,
                    "data": result,
                    "message": result.get("error", "未知错误"),
                }

            return {"status": "success", "agent_name": agent_name, "data": result}
        except Exception as e:
            logger.error(
                "Agent execution failed",
                extra={"agent_name": agent_name, "status": "error"},
                exc_info=True,
            )
            return {
                "status": "error",
                "agent_name": agent_name,
                "data": {"error": str(e)},
                "message": f"智能体执行失败: {str(e)}",
            }

    def _aggregate_results(
        self,
        results: List[Dict[str, Any]],
        intention_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """聚合多个智能体的结果。"""
        aggregated = {
            "status": "completed",
            "intention": {
                "intents": intention_data.get("intents", []),
                "key_entities": intention_data.get("key_entities", {}),
            },
            "agents_executed": len(results),
            "results": [],
        }

        for result in results:
            data = result["result"].get("data", {}) or {}
            aggregated["results"].append({
                "agent_name": result["agent_name"],
                "priority": result["priority"],
                "status": result["result"].get("status", "unknown"),
                "data": data,
                "summary": data.get("summary", ""),
                "duration_ms": data.get("duration_ms"),
                "tools_called": data.get("tools_called", []),
                "recommended_skills": data.get("recommended_skills", []),
                "evidence": data.get("evidence", []),
                "next_actions": data.get("next_actions", []),
            })

        errors = [r for r in results if r["result"].get("status") == "error"]
        skipped = [r for r in results if r["result"].get("status") == "skipped"]
        if errors:
            aggregated["status"] = "partial_failure"
            aggregated["errors"] = len(errors)
        if skipped:
            aggregated["skipped"] = len(skipped)

        return aggregated

    def _update_memory(
        self,
        intention_data: Dict[str, Any],
        results: List[Dict[str, Any]],
    ):
        """更新长期记忆。"""
        if not self.memory_manager:
            return

        ticket_id = (
            intention_data.get("ticket_id")
            or intention_data.get("key_entities", {}).get("ticket_id")
            or intention_data.get("ticket", {}).get("ticket_id", "")
        )
        merchant_id = (
            intention_data.get("key_entities", {}).get("merchant_id")
            or intention_data.get("ticket", {}).get("merchant_id", "")
        )
        issue_type = (
            intention_data.get("issue_type")
            or intention_data.get("key_entities", {}).get("issue_type", "")
        )
        scenario = intention_data.get("scenario", "")
        query = intention_data.get("query", "")

        agent_observations = []
        resolution_data = None

        for result in results:
            agent_name = result["agent_name"]
            data = result["result"].get("data", {}) or {}
            if not isinstance(data, dict):
                continue
            summary = data.get("summary")
            if summary:
                agent_observations.append(f"{agent_name}: {summary}")
            if agent_name == "ResolutionAgent":
                resolution_data = data

        if resolution_data:
            self.memory_manager.long_term.save_diagnosis_history({
                "ticket_id": ticket_id,
                "merchant_id": merchant_id,
                "issue_type": issue_type,
                "scenario": scenario,
                "summary": resolution_data.get("summary", ""),
                "responsible_party": resolution_data.get("responsible_party", ""),
                "root_cause": resolution_data.get("root_cause", ""),
                "query": query,
                "status": "completed",
                "agent_observations": agent_observations,
                "evidence": resolution_data.get("evidence", []),
            })
            logger.info(
                "Saved diagnosis memory",
                extra={
                    "ticket_id": ticket_id or "unknown",
                    "responsible_party": resolution_data.get("responsible_party", "unknown"),
                    "scenario": scenario,
                },
            )
            return

        if agent_observations:
            self.memory_manager.long_term.add_chat_message(
                role="assistant",
                content=" | ".join(agent_observations),
                session_id=getattr(self.memory_manager, "session_id", None),
            )
            logger.info(
                "Saved agent observations to long-term chat history",
                extra={"observation_count": len(agent_observations)},
            )
