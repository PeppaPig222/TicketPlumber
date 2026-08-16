#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CodeAgent：负责接口链路、配置与代码侧排查。

除确定性链路排查外，提供「日志异常模式驱动的 LLM 动态决策」能力：
当回调日志呈现多异常模式（歧义）且 enable_llm_autonomy 开启时，调用 LLM
基于日志异常决定深挖哪条链路（ReAct-lite 单步），否则退回确定性规则路径。
"""
import json
import logging
from typing import Dict, List, Optional

from agentscope.message import Msg

from config import SYSTEM_CONFIG
from agents.diagnosis_agent_base import BaseDiagnosisAgent
from agents.diagnosis_agents import _context_value

logger = logging.getLogger(__name__)


class CodeAgent(BaseDiagnosisAgent):
    """技术链路视角的专业诊断 Agent。"""

    allowed_skills = {
        "get_order_detail",
        "get_order_timeline",
        "get_asset_pool",
        "get_asset_allocation",
        "get_merchant_contract",
        "get_bill_detail",
        "get_settlement_rule",
        "GetOrderRefund",
        "ReconstructTimeline",
        "GetBillingConfig",
        "GetBillCalculation",
        "GetReconciliation",
    }

    # 工具白名单（执行层物理隔离）：只允许代码侧排查工具
    allowed_tools = {
        "trace_api",
        "check_config",
        "query_settlement",
    }

    async def reply(self, x: Msg = None) -> Msg:
        payload = self._parse_payload(x)
        context = self._get_context(payload)
        previous_results = self._get_previous_results(payload)
        round_num = context.get("round_num", 1)
        scenario = context.get("scenario")

        if round_num == 1:
            return await self._round_one(context, scenario, previous_results)
        if scenario == "asset_allocation_failure":
            return await self._asset_round_two(context)
        if scenario == "settlement_amount_mismatch":
            return await self._settlement_round_two(context)
        if round_num >= 3:
            return await self._follow_up(scenario, previous_results)
        return await self._order_round_two(context)

    async def _round_one(self, context: Dict, scenario: str, previous_results: List[Dict]) -> Msg:
        if scenario == "asset_allocation_failure":
            skill_names = ["get_asset_pool", "get_asset_allocation", "GetBillingConfig"]
        elif scenario == "settlement_amount_mismatch":
            skill_names = ["get_merchant_contract", "get_bill_detail", "get_settlement_rule", "GetBillCalculation", "GetReconciliation"]
        else:
            skill_names = ["get_order_detail", "get_order_timeline", "GetOrderRefund", "ReconstructTimeline"]

        skill_results = [
            await self._run_skill(skill_name, context, "补全代码侧基础上下文", "结构化基础事实", previous_results)
            for skill_name in skill_names
        ]

        merged = {}
        evidence = []
        for result in skill_results:
            merged.update({k: v for k, v in result.items() if k not in {"status", "summary", "tools_called"}})
            if result.get("summary"):
                evidence.append(result["summary"])

        return self._response(
            status="success",
            summary="代码侧已完成基础事实收集",
            evidence=evidence,
            next_actions=["进入深度链路排查"],
            recommended_skills=skill_names,
            tools_called=self._dedupe_tools(skill_results),
            **merged,
        )

    async def _order_round_two(self, context: Dict) -> Msg:
        order_id = _context_value(context, "order_id")
        merchant_id = _context_value(context, "merchant_id")
        logs = await self._execute_tool("trace_api", api_path="/api/refund/callback", order_id=order_id)
        config = await self._execute_tool("check_config", merchant_id=merchant_id)
        log_items = logs.get("data", [])
        config_data = config.get("data", {})
        has_success = any(item.get("status_code") == 200 for item in log_items)

        evidence = [
            f"退款回调日志 {len(log_items)} 条",
            "接口链路存在 200 成功回调" if has_success else "未观察到成功回调",
            "退款功能开关开启" if config_data.get("refund_enabled") else "退款开关异常",
        ]
        verdict = "代码链路无明显异常" if has_success and config_data.get("refund_enabled") else "代码链路存在可疑点"
        tools_called = ["trace_api", "check_config"]
        code_path_detail = {"logs": log_items, "config": config_data}

        # 阶段3：LLM 动态决策（ReAct-lite 单步）——仅当日志多异常且开关开启时触发
        llm_decision = None
        if self._autonomy_enabled() and self._is_ambiguous(log_items):
            llm_decision = await self._llm_deep_dive(context, log_items, config_data)

        if llm_decision:
            action = llm_decision.get("decision")
            if action == "trace_deeper":
                deep_path = llm_decision.get("api_path") or "/api/order/sync"
                deep = await self._execute_tool("trace_api", api_path=deep_path, order_id=order_id)
                deep_items = deep.get("data", [])
                evidence.append(f"LLM 深挖 {deep_path}：命中 {len(deep_items)} 条日志")
                tools_called.append("trace_api")
                code_path_detail["deep_logs"] = deep_items
            elif action == "config_check":
                config_key = llm_decision.get("config_key")
                deep = await self._execute_tool("check_config", merchant_id=merchant_id, config_key=config_key)
                evidence.append(f"LLM 深挖配置项 {config_key or '(默认)'}：{deep.get('status')}")
                tools_called.append("check_config")
                code_path_detail["deep_config"] = deep.get("data", {})
            if llm_decision.get("root_cause"):
                verdict = llm_decision["root_cause"]
                evidence.append(f"LLM 判定根因：{verdict}")
            code_path_detail["llm_decision"] = llm_decision
            code_path_detail["llm_autonomy"] = True

        return self._response(
            status="success",
            summary=verdict,
            evidence=evidence,
            next_actions=["交由数据侧继续做一致性校验"],
            recommended_skills=["trace_api", "check_config"],
            tools_called=tools_called,
            code_path_detail=code_path_detail,
            path_verdict=verdict,
        )

    def _autonomy_enabled(self) -> bool:
        """LLM 自主决策是否可用：配置开关开启 且 已注入 LLM 模型。

        测试/降级场景下 model 为 None，直接短路为确定性路径。
        """
        return bool(SYSTEM_CONFIG.get("enable_llm_autonomy", False)) and self.model is not None

    @staticmethod
    def _is_ambiguous(log_items: List[Dict]) -> bool:
        """判定日志是否呈现多异常模式（歧义），作为 LLM 深挖的触发条件。

        信号：存在多种不同 error_code，或同时出现 2xx 与 4xx/5xx 状态码。
        """
        if not log_items:
            return False
        error_codes = {item.get("error_code") for item in log_items if item.get("error_code")}
        statuses = {item.get("status_code") for item in log_items if item.get("status_code") is not None}
        if len(error_codes) > 1:
            return True
        has_success = any(200 <= s < 300 for s in statuses)
        has_failure = any(s >= 400 for s in statuses)
        return has_success and has_failure

    def _plan_prompt(self, context: Dict, log_items: List[Dict], config_data: Dict) -> str:
        """构造深挖决策 prompt：给出日志异常模式与候选链路，要求输出 JSON。"""
        order_id = _context_value(context, "order_id")
        logs_summary = "\n".join(
            f"- {item.get('api_path')} status={item.get('status_code')} "
            f"error={item.get('error_code') or '无'} rt={item.get('response_time_ms')}ms "
            f"note={item.get('note', '')}"
            for item in log_items
        )
        return (
            "你是工单诊断的代码链路排查 Agent。当前退款回调日志呈现多异常模式，"
            "需要你基于日志异常决定「深挖哪条链路」，只做单步决策。\n\n"
            f"订单号：{order_id}\n"
            f"配置摘要：{json.dumps(config_data, ensure_ascii=False)}\n"
            f"日志：\n{logs_summary}\n\n"
            "可选决策（decision 字段三选一）：\n"
            "1. trace_deeper：继续追踪其他接口日志（api_path 候选 /api/order/sync 或 /api/refund/query）\n"
            "2. config_check：检查某个配置项（config_key 可选）\n"
            "3. conclude：证据已足够，直接给出根因（root_cause）\n\n"
            "只输出 JSON，不要输出其他内容，格式：\n"
            '{"decision": "trace_deeper|config_check|conclude", "api_path": "...", '
            '"config_key": "...", "root_cause": "...", "reason": "..."}'
        )

    @staticmethod
    def _parse_decision(raw: str) -> Optional[Dict]:
        """解析 LLM 返回的 JSON 决策，容错处理 markdown code fence 与前后缀。"""
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
            if isinstance(data, dict) and "decision" in data:
                return data
        except (json.JSONDecodeError, ValueError):
            return None
        return None

    async def _llm_deep_dive(
        self, context: Dict, log_items: List[Dict], config_data: Dict
    ) -> Optional[Dict]:
        """调用 LLM 基于日志异常模式做单步深挖决策，失败返回 None 退回规则路径。"""
        prompt = self._plan_prompt(context, log_items, config_data)
        raw = await self._call_llm([{"role": "user", "content": prompt}])
        if not raw:
            return None
        decision = self._parse_decision(raw)
        if decision is None:
            logger.warning(
                "LLM deep dive decision parse failed",
                extra={"agent": self.name, "raw": raw[:200]},
            )
        return decision

    async def _asset_round_two(self, context: Dict) -> Msg:
        merchant_id = _context_value(context, "merchant_id")
        config = await self._execute_tool("check_config", merchant_id=merchant_id)
        permissions = (config.get("data") or {}).get("permissions", [])
        can_allocate = "asset_allocate" in permissions
        summary = "代码侧未发现系统开关异常" if can_allocate else "操作者侧权限配置可能不足"
        return self._response(
            status="success",
            summary=summary,
            evidence=[
                "商户配置读取成功",
                f"权限点包含 {', '.join(permissions) if permissions else '无'}",
            ],
            next_actions=["继续检查用户绑定与保护期"],
            recommended_skills=["check_config"],
            tools_called=["check_config"],
            code_path_detail={"config": config.get("data", {})},
            path_verdict=summary,
        )

    async def _settlement_round_two(self, context: Dict) -> Msg:
        merchant_id = _context_value(context, "merchant_id")
        settlement = await self._execute_tool("query_settlement", merchant_id=merchant_id)
        data = settlement.get("data", {}) if settlement.get("status") == "success" else {}
        inconsistent = data.get("actual_ratio") != data.get("settlement_ratio")
        summary = "结算计算链路命中比例不一致" if inconsistent else "结算计算链路正常"
        return self._response(
            status="success",
            summary=summary,
            evidence=[
                f"合同比例 {data.get('settlement_ratio', '未知')}",
                f"实际比例 {data.get('actual_ratio', '未知')}",
                f"账单总额 {data.get('bill_total', '未知')}",
            ],
            next_actions=["继续核对标签与时间线变更"],
            recommended_skills=["query_settlement"],
            tools_called=["query_settlement"],
            code_path_detail={"settlement": data},
            path_verdict=summary,
            inconsistency_found=bool(inconsistent),
        )

    async def _follow_up(self, scenario: str, previous_results: List[Dict]) -> Msg:
        data_result = self._find_previous_result(previous_results, "DataAgent")
        code_result = self._find_previous_result(previous_results, "CodeAgent")
        evidence = list(code_result.get("evidence", []))
        if data_result.get("path_verdict"):
            evidence.append(f"数据侧补充结论：{data_result.get('path_verdict')}")
        summary = "代码侧复核后维持原判断，继续交由 ResolutionAgent 汇总"
        if scenario == "settlement_amount_mismatch":
            summary = "代码侧复核结算链路后，确认问题更偏向规则或数据标签"
        return self._response(
            status="success",
            summary=summary,
            evidence=evidence,
            next_actions=["等待 ResolutionAgent 汇总判责"],
            recommended_skills=[],
            tools_called=[],
            path_verdict=summary,
        )
