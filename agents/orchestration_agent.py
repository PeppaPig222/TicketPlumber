"""
协调器智能体 OrchestrationAgent
职责：根据意图识别结果，协调调度多个子智能体完成任务

核心功能：
1. 接收 IntentionAgent 的调度决策
2. 按照优先级顺序执行子智能体
3. 管理智能体之间的消息传递
4. 聚合多个智能体的结果
5. 与三层记忆系统集成

执行模式：
- Sequential (顺序执行): 按优先级依次执行，前一个的输出作为后一个的输入
- Parallel (并行执行): 同时执行多个智能体（暂不实现）
"""
from agentscope.agent import AgentBase
from agentscope.message import Msg
from typing import Optional, Union, List, Dict, Any
import json
import logging
import asyncio

logger = logging.getLogger(__name__)


class OrchestrationAgent(AgentBase):
    """协调器智能体 - 调度和协调多个子智能体"""

    def __init__(
        self,
        name: str = "OrchestrationAgent",
        agent_registry: Dict[str, AgentBase] = None,
        memory_manager = None,
        **kwargs
    ):
        """
        初始化协调器

        Args:
            name: 智能体名称
            agent_registry: 子智能体注册表 {agent_name: agent_instance}
            memory_manager: 记忆管理器
        """
        super().__init__()
        self.name = name
        self.agent_registry = agent_registry or {}
        self.memory_manager = memory_manager

    def register_agent(self, agent_name: str, agent: AgentBase):
        """注册子智能体"""
        self.agent_registry[agent_name] = agent
        logger.info("Registered agent", extra={"agent_name": agent_name})

    def unregister_agent(self, agent_name: str):
        """注销子智能体"""
        if agent_name in self.agent_registry:
            del self.agent_registry[agent_name]
            logger.info("Unregistered agent", extra={"agent_name": agent_name})

    async def reply(self, x: Optional[Union[Msg, List[Msg]]] = None) -> Msg:
        """
        协调执行流程

        Args:
            x: 输入消息，应包含 IntentionAgent 的输出

        Returns:
            Msg: 执行结果
        """
        if x is None:
            return Msg(
                name=self.name,
                content=json.dumps({"error": "No input provided"}),
                role="assistant"
            )

        # 解析输入
        if isinstance(x, list):
            intention_output = x[-1].content if x else "{}"
        else:
            intention_output = x.content

        # 解析意图识别结果
        try:
            intention_data = json.loads(intention_output) if isinstance(intention_output, str) else intention_output
        except json.JSONDecodeError as e:
            logger.error("Failed to parse intention output", exc_info=True)
            return Msg(
                name=self.name,
                content=json.dumps({"error": "Invalid intention format"}),
                role="assistant"
            )

        # 获取智能体调度计划
        agent_schedule = intention_data.get("agent_schedule", [])
        if not agent_schedule:
            return Msg(
                name=self.name,
                content=json.dumps({
                    "status": "no_agents",
                    "message": "没有需要调度的智能体"
                }),
                role="assistant"
            )

        # 按优先级排序
        sorted_schedule = sorted(agent_schedule, key=lambda x: x.get("priority", 999))

        logger.info(
            f"Orchestrating {len(sorted_schedule)} agents",
            extra={"agent_count": len(sorted_schedule)},
        )

        # 准备上下文信息
        context = self._prepare_context(intention_data)

        # 并行执行智能体（按优先级分组）
        results = []
        current_priority = None
        parallel_tasks = []

        for task in sorted_schedule:
            priority = task.get("priority", 0)

            # 如果优先级变化，先执行当前批次
            if current_priority is not None and priority != current_priority:
                # 并行执行当前优先级的所有任务
                if parallel_tasks:
                    batch_results = await self._execute_parallel_agents(parallel_tasks, context, results)
                    results.extend(batch_results)
                    parallel_tasks = []

            current_priority = priority
            parallel_tasks.append(task)

        # 执行最后一批
        if parallel_tasks:
            batch_results = await self._execute_parallel_agents(parallel_tasks, context, results)
            results.extend(batch_results)

        # 聚合结果
        final_result = self._aggregate_results(results, intention_data)

        # 更新记忆
        if self.memory_manager:
            self._update_memory(intention_data, results)

        return Msg(
            name=self.name,
            content=json.dumps(final_result, ensure_ascii=False),
            role="assistant"
        )

    def _prepare_context(self, intention_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        准备上下文信息，供子智能体使用

        Args:
            intention_data: 意图识别结果

        Returns:
            上下文字典
        """
        context = {
            "reasoning": intention_data.get("reasoning", ""),
            "intents": intention_data.get("intents", []),
            "key_entities": intention_data.get("key_entities", {}),
            "rewritten_query": intention_data.get("rewritten_query", "")
        }

        # 透传诊断场景中额外的上下文字段，避免上层 Loop 状态在编排时丢失。
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

        # 从记忆系统获取上下文
        if self.memory_manager:
            # 短期记忆：最近对话
            recent_context = self.memory_manager.short_term.get_recent_context(3)
            context["recent_dialogue"] = recent_context

            # 长期记忆：用户偏好
            preferences = self.memory_manager.long_term.get_preference()
            context["user_preferences"] = preferences

        return context

    async def _execute_parallel_agents(
        self,
        tasks: List[Dict],
        context: Dict[str, Any],
        previous_results: List[Dict]
    ) -> List[Dict]:
        """
        并行执行多个智能体，支持依赖调度与 skip_if_missing。

        处理逻辑：
        1. 先剔除因缺少 required_entities 而需要跳过的任务；
        2. 对同一 priority 的任务按 depends_on 做拓扑分批，依赖已满足才执行；
        3. 依赖未满足（例如 depends_on 不在本批次）则保留到下一批次处理。
        """
        if not tasks:
            return []

        logger.info(
            f"Executing {len(tasks)} agents with dependency/skip awareness",
            extra={"agent_count": len(tasks)},
        )

        completed_agent_names = {
            r.get("agent_name")
            for r in previous_results
            if r.get("result", {}).get("status") != "error"
        }
        key_entities = context.get("key_entities", {}) or {}
        collected_facts = (
            context.get("collected_data", {}).get("facts", {}) or {}
        )

        executable_tasks: List[Dict] = []
        skipped_results: List[Dict] = []

        for task in tasks:
            agent_name = task.get("agent_name")

            # 1. skip_if_missing：缺少必要实体则跳过
            required_entities = task.get("required_entities", [])
            if task.get("skip_if_missing") and required_entities:
                missing = [
                    e for e in required_entities
                    if not (key_entities.get(e) or collected_facts.get(e))
                ]
                if missing:
                    logger.info(
                        "Skipping agent due to missing entities",
                        extra={
                            "agent_name": agent_name,
                            "missing_entities": missing,
                        },
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

            # 2. 检查 depends_on：依赖已在 previous_results 中完成才执行
            depends_on = task.get("depends_on", [])
            unmet = [d for d in depends_on if d not in completed_agent_names]
            if unmet:
                logger.info(
                    "Deferring agent due to unmet dependencies",
                    extra={
                        "agent_name": agent_name,
                        "unmet_dependencies": unmet,
                    },
                )
                # 将任务延后到本批次后面单独执行（通过后续迭代处理）
                task["_deferred_unmet"] = unmet

            executable_tasks.append(task)

        # 按 depends_on 做拓扑分批执行
        results: List[Dict] = list(skipped_results)
        remaining = list(executable_tasks)
        max_iterations = len(remaining) + 1
        iteration = 0

        while remaining and iteration < max_iterations:
            iteration += 1
            ready = []
            still_remaining = []

            for task in remaining:
                depends_on = task.get("depends_on", [])
                deferred = task.pop("_deferred_unmet", None)
                unmet = [
                    d for d in depends_on
                    if d not in completed_agent_names
                ]
                if unmet:
                    if deferred == unmet:
                        # 依赖始终无法满足，降级执行避免死等
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
                # 无任务可执行但仍有剩余，避免死循环，全部降级执行
                logger.warning(
                    "No ready tasks but remaining exist, executing all remaining",
                )
                ready = remaining
                still_remaining = []

            batch_results = await self._execute_agent_batch(ready, context, previous_results + results)
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
        tasks: List[Dict],
        context: Dict[str, Any],
        previous_results: List[Dict]
    ) -> List[Dict]:
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

        logger.info(
            f"Executing {len(tasks)} agents in parallel",
            extra={"agent_count": len(tasks)},
        )

        parallel_coroutines = []
        for task in tasks:
            agent_name = task.get("agent_name")
            priority = task.get("priority", 0)
            reason = task.get("reason", "")
            expected_output = task.get("expected_output", "")

            logger.info(
                f"Parallel executing agent: {agent_name}",
                extra={"agent_name": agent_name, "priority": priority},
            )

            coroutine = self._execute_agent(
                agent_name=agent_name,
                context=context,
                reason=reason,
                expected_output=expected_output,
                previous_results=previous_results,
            )
            parallel_coroutines.append((agent_name, priority, coroutine))

        execution_results = await asyncio.gather(
            *[coro for _, _, coro in parallel_coroutines],
            return_exceptions=True,
        )

        results = []
        for (agent_name, priority, _), exec_result in zip(parallel_coroutines, execution_results):
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
        previous_results: List[Dict]
    ) -> Dict[str, Any]:
        """
        执行单个智能体

        Args:
            agent_name: 智能体名称
            context: 上下文信息
            reason: 调用原因
            expected_output: 期望输出
            previous_results: 前序智能体的结果

        Returns:
            执行结果
        """
        # 检查智能体是否注册
        if agent_name not in self.agent_registry:
            logger.warning(
                "Agent not registered",
                extra={"agent_name": agent_name},
            )
            return {
                "status": "error",
                "message": f"智能体未注册: {agent_name}"
            }

        agent = self.agent_registry[agent_name]

        # 构建输入消息
        input_msg = Msg(
            name="Orchestrator",
            content=json.dumps({
                "context": context,
                "reason": reason,
                "expected_output": expected_output,
                "previous_results": previous_results
            }, ensure_ascii=False),
            role="user"
        )

        try:
            # 调用智能体
            response = await agent.reply(input_msg)

            # 解析响应
            if isinstance(response.content, str):
                try:
                    result = json.loads(response.content)
                except json.JSONDecodeError:
                    result = {"output": response.content}
            else:
                result = response.content

            # 检查 result 中是否有 error 字段
            # 如果有，说明智能体内部执行失败了
            if isinstance(result, dict) and "error" in result:
                error_msg = result.get("error", "未知错误")
                return {
                    "status": "error",
                    "agent_name": agent_name,
                    "data": result,
                    "message": error_msg
                }

            return {
                "status": "success",
                "agent_name": agent_name,
                "data": result
            }

        except Exception as e:
            logger.error(
                "Agent execution failed",
                extra={"agent_name": agent_name, "status": "error"},
                exc_info=True,
            )
            # 返回友好的错误信息，但不中断流程
            return {
                "status": "error",
                "agent_name": agent_name,
                "data": {"error": str(e)},
                "message": f"智能体执行失败: {str(e)}"
            }

    def _aggregate_results(
        self,
        results: List[Dict],
        intention_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        聚合多个智能体的结果

        Args:
            results: 所有智能体的执行结果
            intention_data: 原始意图识别结果

        Returns:
            聚合后的最终结果
        """
        aggregated = {
            "status": "completed",
            "intention": {
                "intents": intention_data.get("intents", []),
                "key_entities": intention_data.get("key_entities", {})
            },
            "agents_executed": len(results),
            "results": []
        }

        # 收集每个智能体的结果
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

        # 检查是否有错误 / 跳过
        errors = [r for r in results if r["result"].get("status") == "error"]
        skipped = [r for r in results if r["result"].get("status") == "skipped"]
        if errors:
            aggregated["status"] = "partial_failure"
            aggregated["errors"] = len(errors)
        if skipped:
            aggregated["skipped"] = len(skipped)

        return aggregated

    def _update_memory(self, intention_data: Dict[str, Any], results: List[Dict]):
        """
        更新记忆系统

        Args:
            intention_data: 意图识别结果
            results: 智能体执行结果
        """
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
