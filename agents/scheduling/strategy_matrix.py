#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
调度策略矩阵

把 DiagnosisIntentionAgent 里硬编码的 "scenario × round → agent_schedule" 抽离成：
- ScenarioScheduleBuilder：按场景和轮次生成基础任务列表（与现有行为等价）
- SchedulingRule：对基础任务做策略转换
- StrategyMatrix：组合 builder + rules，输出最终任务列表和 metadata

设计原则：
1. 默认配置下输出与现有 _build_schedule 完全一致，保证回归测试不降级。
2. 规则可独立开关，通过 SCHEDULING_CONFIG 统一配置。
3. 新增字段（depends_on / required_entities / skip_if_missing）仅作为提示，
   Scheduler 可选择性实现；旧编排器忽略这些字段仍能运行。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from config import SCHEDULING_CONFIG

# 假设类型 → 候选 Agent（证据维度路由映射，一对多，体现「路由 = 验证能力映射」）
# type 是「证据维度」闭集，不是「嫌疑 Agent」映射，也不是 scenario 换皮。
HYPOTHESIS_ROUTING: Dict[str, List[str]] = {
    "api_trace": ["CodeAgent"],
    "db_state": ["DataAgent"],
    "config_state": ["CodeAgent", "OperationAgent"],
    "biz_flow": ["OperationAgent"],
    "policy": ["OperationAgent", "DataAgent"],
    "cross_verify": ["CodeAgent", "DataAgent"],
}


@dataclass
class SchedulingContext:
    """调度上下文"""

    scenario: str = "generic_ticket_diagnosis"
    round_num: int = 1
    query: str = ""
    ticket: Dict[str, Any] = field(default_factory=dict)
    collected_data: Dict[str, Any] = field(default_factory=dict)
    key_entities: Dict[str, Any] = field(default_factory=dict)
    rag_available: bool = False

    @property
    def facts(self) -> Dict[str, Any]:
        return self.collected_data.get("facts", {}) or {}

    def has_entity(self, name: str) -> bool:
        """检查是否已提取指定实体"""
        value = self.key_entities.get(name) or self.facts.get(name)
        return bool(value)


@dataclass
class AgentTask:
    """调度任务单元"""

    agent_name: str
    priority: int
    reason: str
    expected_output: str
    strategy: str = "base"
    depends_on: List[str] = field(default_factory=list)
    required_entities: List[str] = field(default_factory=list)
    skip_if_missing: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "priority": self.priority,
            "reason": self.reason,
            "expected_output": self.expected_output,
            "strategy": self.strategy,
            "depends_on": self.depends_on,
            "required_entities": self.required_entities,
            "skip_if_missing": self.skip_if_missing,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentTask":
        return cls(
            agent_name=data["agent_name"],
            priority=data.get("priority", 0),
            reason=data.get("reason", ""),
            expected_output=data.get("expected_output", ""),
            strategy=data.get("strategy", "base"),
            depends_on=list(data.get("depends_on", [])),
            required_entities=list(data.get("required_entities", [])),
            skip_if_missing=bool(data.get("skip_if_missing", False)),
        )


class ScheduleBuilder(ABC):
    """基础调度构建器"""

    @abstractmethod
    def build(self, ctx: SchedulingContext) -> List[AgentTask]:
        raise NotImplementedError


class SchedulingRule(ABC):
    """调度策略规则"""

    name: str = "base_rule"

    @abstractmethod
    def apply(
        self,
        ctx: SchedulingContext,
        tasks: List[AgentTask],
        metadata: Dict[str, Any],
    ) -> List[AgentTask]:
        """对任务列表应用策略转换，可修改 metadata"""
        raise NotImplementedError


class ScenarioScheduleBuilder(ScheduleBuilder):
    """场景 × 轮次 基础调度构建器

    与 DiagnosisIntentionAgent._build_schedule 默认输出保持一致。
    """

    def build(self, ctx: SchedulingContext) -> List[AgentTask]:
        schedules = {
            "generic_ticket_diagnosis": {
                1: [
                    AgentTask(
                        "OperationAgent", 1,
                        "尝试补全商户上下文与历史经验", "商户画像与历史案例"
                    ),
                    AgentTask(
                        "DataAgent", 1,
                        "预热基础实体", "基础数据事实"
                    ),
                ],
                2: [
                    AgentTask(
                        "CodeAgent", 1,
                        "从技术链路视角补充诊断", "技术侧线索"
                    ),
                    AgentTask(
                        "OperationAgent", 1,
                        "从业务流程视角补充诊断", "操作侧线索"
                    ),
                    AgentTask(
                        "DataAgent", 1,
                        "从数据视角补充诊断", "数据侧线索"
                    ),
                    AgentTask(
                        "ResolutionAgent", 2,
                        "汇总证据并给出初步结论", "归因建议"
                    ),
                ],
                3: [],
            },
            "order_status_anomaly": {
                1: [
                    AgentTask(
                        "CodeAgent", 1,
                        "获取订单当前状态与时间线", "订单基础详情"
                    ),
                    AgentTask(
                        "OperationAgent", 1,
                        "查询商户与历史工单上下文", "商户画像与历史经验"
                    ),
                    AgentTask(
                        "DataAgent", 1,
                        "预热订单关键实体", "后续一致性校验事实"
                    ),
                ],
                2: [
                    AgentTask(
                        "CodeAgent", 1,
                        "排查技术链路", "前后端代码与接口状态"
                    ),
                    AgentTask(
                        "OperationAgent", 1,
                        "排查用户操作", "用户是否误操作"
                    ),
                    AgentTask(
                        "DataAgent", 1,
                        "排查数据一致性", "跨表比对与回调链路"
                    ),
                ],
                3: [
                    AgentTask(
                        "CodeAgent", 1,
                        "复核技术链路结论", "技术侧复核"
                    ),
                    AgentTask(
                        "OperationAgent", 1,
                        "复核业务操作结论", "操作侧复核"
                    ),
                    AgentTask(
                        "DataAgent", 1,
                        "复核数据异常结论", "数据侧复核"
                    ),
                    AgentTask(
                        "ResolutionAgent", 2,
                        "汇总结论并判责", "根因与建议"
                    ),
                ],
            },
            "asset_allocation_failure": {
                1: [
                    AgentTask(
                        "CodeAgent", 1,
                        "获取资产池与系统配置基础信息", "额度与配置概况"
                    ),
                    AgentTask(
                        "OperationAgent", 1,
                        "获取用户绑定与历史经验", "绑定状态与历史案例"
                    ),
                    AgentTask(
                        "DataAgent", 1,
                        "预热资产与分配实体", "可用额度与分配事实"
                    ),
                ],
                2: [
                    AgentTask(
                        "CodeAgent", 1,
                        "检查系统配置与权限开关", "系统配置限制"
                    ),
                    AgentTask(
                        "OperationAgent", 1,
                        "检查绑定与保护期", "用户归属限制"
                    ),
                    AgentTask(
                        "DataAgent", 1,
                        "检查额度限制", "额度是否足够"
                    ),
                    AgentTask(
                        "ResolutionAgent", 2,
                        "汇总结论并给出处理建议", "根因与建议"
                    ),
                ],
                3: [],
            },
            "settlement_amount_mismatch": {
                1: [
                    AgentTask(
                        "CodeAgent", 1,
                        "读取合同、账单与结算规则", "结算基础事实"
                    ),
                    AgentTask(
                        "OperationAgent", 1,
                        "检索相似工单与操作侧线索", "历史处理经验"
                    ),
                    AgentTask(
                        "DataAgent", 1,
                        "预热账单与规则实体", "后续规则校验事实"
                    ),
                ],
                2: [
                    AgentTask(
                        "CodeAgent", 1,
                        "检查规则与计算链路", "比例与金额一致性"
                    ),
                    AgentTask(
                        "OperationAgent", 1,
                        "排除人工流程异常", "历史与流程线索"
                    ),
                    AgentTask(
                        "DataAgent", 1,
                        "检查合同标签与数据时间线", "标签变更和规则痕迹"
                    ),
                ],
                3: [
                    AgentTask(
                        "CodeAgent", 1,
                        "复核规则链路结论", "技术侧复核"
                    ),
                    AgentTask(
                        "OperationAgent", 1,
                        "复核流程与历史案例", "操作侧复核"
                    ),
                    AgentTask(
                        "DataAgent", 1,
                        "复核标签与比例冲突", "数据侧复核"
                    ),
                    AgentTask(
                        "ResolutionAgent", 2,
                        "汇总结论并判责", "根因与建议"
                    ),
                ],
            },
        }
        return schedules.get(ctx.scenario, {}).get(ctx.round_num, [])


class BasicInfoParallelRule(SchedulingRule):
    """基础信息查询统一并行策略

    将第一轮的基础信息收集类任务统一放到同一 priority，便于 Scheduler
    一次性并行执行。默认它们已经是同一 priority，本规则主要做显式标记。
    """

    name = "basic_info_parallel"

    def apply(
        self,
        ctx: SchedulingContext,
        tasks: List[AgentTask],
        metadata: Dict[str, Any],
    ) -> List[AgentTask]:
        if not SCHEDULING_CONFIG.get("enable_basic_info_parallel", True):
            return tasks

        basic_agents = {"CodeAgent", "OperationAgent", "DataAgent"}
        for task in tasks:
            if task.agent_name in basic_agents and ctx.round_num == 1:
                task.strategy = self.name
        metadata["basic_info_parallel"] = True
        return tasks


class DeepLogConditionalRule(SchedulingRule):
    """深度日志追踪条件触发策略

    当场景为 order_status_anomaly 且已提取 order_id 时，在第二轮注入深度日志
    追踪任务（复用 CodeAgent，但带 depends_on 与 required_entities）。
    """

    name = "deep_log_conditional"

    def apply(
        self,
        ctx: SchedulingContext,
        tasks: List[AgentTask],
        metadata: Dict[str, Any],
    ) -> List[AgentTask]:
        if not SCHEDULING_CONFIG.get("enable_deep_log_conditional", True):
            return tasks

        trigger_scenarios = SCHEDULING_CONFIG.get(
            "deep_log_trigger_scenarios", ["order_status_anomaly"]
        )
        required_entities = SCHEDULING_CONFIG.get(
            "deep_log_required_entities", ["order_id"]
        )

        if ctx.scenario not in trigger_scenarios:
            metadata["deep_log_enabled"] = False
            return tasks

        if not all(ctx.has_entity(e) for e in required_entities):
            metadata["deep_log_enabled"] = False
            metadata["deep_log_skipped_reason"] = "缺少触发实体"
            return tasks

        # 在第二轮追加深度日志追踪任务
        if ctx.round_num == 2:
            deep_log_task = AgentTask(
                agent_name="CodeAgent",
                priority=1,
                reason="基于 order_id 做深度接口日志追踪",
                expected_output="回调链路、超时、异常日志",
                strategy=self.name,
                depends_on=["DataAgent"],
                required_entities=required_entities,
                skip_if_missing=True,
            )
            tasks.append(deep_log_task)
            metadata["deep_log_enabled"] = True

        return tasks


class CrossDomainDependencyRule(SchedulingRule):
    """跨域交叉验证依赖调度策略

    给 ResolutionAgent 添加依赖（需等待 CodeAgent / OperationAgent / DataAgent
    完成），并提升其 priority 到 cross_domain_resolution_priority，确保交叉
    验证在证据收集之后执行。
    """

    name = "cross_domain_dependency"

    def apply(
        self,
        ctx: SchedulingContext,
        tasks: List[AgentTask],
        metadata: Dict[str, Any],
    ) -> List[AgentTask]:
        if not SCHEDULING_CONFIG.get("enable_cross_domain_validation", True):
            return tasks

        resolution_priority = SCHEDULING_CONFIG.get(
            "cross_domain_resolution_priority", 2
        )
        evidence_agents = ["CodeAgent", "OperationAgent", "DataAgent"]

        for task in tasks:
            if task.agent_name == "ResolutionAgent":
                task.priority = resolution_priority
                task.strategy = self.name
                # 仅添加当前轮次实际存在的证据 Agent 作为依赖
                present_evidence = [
                    a for a in evidence_agents
                    if any(t.agent_name == a for t in tasks)
                ]
                task.depends_on = list(set(task.depends_on + present_evidence))

        metadata["cross_domain_validation"] = True
        return tasks


class RAGBusinessParallelRule(SchedulingRule):
    """RAG 与业务 Skill 并行执行策略

    当 RAG 可用且当前轮次在配置范围内时，把 RAGKnowledgeAgent 作为独立 Agent
    与业务 Agent 并行调度，供 ResolutionAgent 后续汇总证据。
    """

    name = "rag_business_parallel"

    def apply(
        self,
        ctx: SchedulingContext,
        tasks: List[AgentTask],
        metadata: Dict[str, Any],
    ) -> List[AgentTask]:
        if not SCHEDULING_CONFIG.get("enable_rag_business_parallel", True):
            metadata["rag_parallel_enabled"] = False
            return tasks

        if not ctx.rag_available:
            metadata["rag_parallel_enabled"] = False
            metadata["rag_parallel_skipped_reason"] = "RAG 不可用"
            return tasks

        rag_rounds = SCHEDULING_CONFIG.get("rag_parallel_rounds", [2, 3])
        if ctx.round_num not in rag_rounds:
            metadata["rag_parallel_enabled"] = False
            return tasks

        rag_agent_name = SCHEDULING_CONFIG.get("rag_agent_name", "RAGKnowledgeAgent")
        rag_task = AgentTask(
            agent_name=rag_agent_name,
            priority=1,
            reason="并行检索知识库，补充历史案例与政策依据",
            expected_output="相关知识库片段与相似案例",
            strategy=self.name,
        )
        tasks.append(rag_task)
        metadata["rag_parallel_enabled"] = True
        return tasks


class HypothesisRoutingRule(SchedulingRule):
    """假设驱动路由策略

    扫描黑板（collected_data.facts）中的 pending hypothesis，按证据维度路由到
    候选 Agent，merge（追加去重）进静态矩阵基线：
    - 候选 Agent 已在基线中 → 去重，保留基线 reason，附加假设 detail 到 expected_output；
    - 候选 Agent 不在基线中 → 追加为新的验证任务。
    不 override 基线，保证高频场景的确定性覆盖不丢失。

    由 SCHEDULING_CONFIG["enable_hypothesis_routing"] 开关控制，默认关闭；
    无假设时行为与静态矩阵完全一致。
    """

    name = "hypothesis_routing"

    def apply(
        self,
        ctx: SchedulingContext,
        tasks: List[AgentTask],
        metadata: Dict[str, Any],
    ) -> List[AgentTask]:
        if not SCHEDULING_CONFIG.get("enable_hypothesis_routing", False):
            metadata["hypothesis_routing_enabled"] = False
            return tasks

        pending = self._collect_pending_hypotheses(ctx)
        if not pending:
            metadata["hypothesis_routing_enabled"] = True
            metadata["pending_hypotheses"] = []
            return tasks

        existing = {task.agent_name: task for task in tasks}
        for hyp in pending:
            detail = hyp.get("detail") or ""
            for agent_name in HYPOTHESIS_ROUTING.get(hyp.get("type"), []):
                if agent_name in existing:
                    task = existing[agent_name]
                    if detail and detail not in task.expected_output:
                        task.expected_output = f"{task.expected_output}；待验证假设：{detail}"
                    task.strategy = self._mark_strategy(task.strategy)
                else:
                    task = AgentTask(
                        agent_name=agent_name,
                        priority=1,
                        reason=f"验证假设（{hyp.get('type')}）",
                        expected_output=f"待验证假设：{detail}" if detail else "验证待决假设",
                        strategy=self.name,
                    )
                    tasks.append(task)
                    existing[agent_name] = task

        metadata["hypothesis_routing_enabled"] = True
        metadata["pending_hypotheses"] = pending
        return tasks

    @staticmethod
    def _collect_pending_hypotheses(ctx: SchedulingContext) -> List[Dict[str, Any]]:
        """从黑板事实中收集所有 pending 假设（兼容复数累积与单数直存）。"""
        pending: List[Dict[str, Any]] = []
        seen: List[str] = []

        hypotheses = ctx.facts.get("hypotheses")
        if isinstance(hypotheses, dict):
            hypotheses = [hypotheses]
        for hyp in hypotheses or []:
            if isinstance(hyp, dict) and hyp.get("status") == "pending" and hyp.get("type"):
                key = f"{hyp.get('type')}:{hyp.get('detail')}"
                if key not in seen:
                    seen.append(key)
                    pending.append(hyp)

        single = ctx.facts.get("hypothesis")
        if isinstance(single, dict) and single.get("status") == "pending" and single.get("type"):
            key = f"{single.get('type')}:{single.get('detail')}"
            if key not in seen:
                pending.append(single)

        return pending

    @staticmethod
    def _mark_strategy(strategy: str) -> str:
        if "hypothesis_routing" in strategy:
            return strategy
        if not strategy or strategy == "base":
            return "hypothesis_routing"
        return f"{strategy}+hypothesis_routing"


class StrategyMatrix:
    """调度策略矩阵入口

    组合基础 builder 和一组 rules，输出最终任务列表与调度元数据。
    """

    def __init__(
        self,
        builder: Optional[ScheduleBuilder] = None,
        rules: Optional[List[SchedulingRule]] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.builder = builder or ScenarioScheduleBuilder()
        self.rules = rules or self._default_rules()
        self.config = config or SCHEDULING_CONFIG

    @staticmethod
    def _default_rules() -> List[SchedulingRule]:
        return [
            BasicInfoParallelRule(),
            DeepLogConditionalRule(),
            CrossDomainDependencyRule(),
            RAGBusinessParallelRule(),
            HypothesisRoutingRule(),
        ]

    def build(
        self, ctx: SchedulingContext
    ) -> Tuple[List[AgentTask], Dict[str, Any]]:
        """生成最终任务列表与 metadata"""
        if not self.config.get("enable_strategy_matrix", True):
            tasks = self.builder.build(ctx)
            metadata = {"strategy_matrix_enabled": False}
            return tasks, metadata

        tasks = self.builder.build(ctx)
        metadata: Dict[str, Any] = {"strategy_matrix_enabled": True}
        for rule in self.rules:
            tasks = rule.apply(ctx, tasks, metadata)

        return tasks, metadata
