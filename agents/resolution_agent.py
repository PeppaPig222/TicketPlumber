#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ResolutionAgent：负责证据汇总、冲突消解与责任归属判定。
"""
from typing import Dict, List

from agentscope.message import Msg

from agents.diagnosis_agent_base import BaseDiagnosisAgent


class ResolutionAgent(BaseDiagnosisAgent):
    """归因与结论生成 Agent。"""

    allowed_skills = {"search_history_ticket", "search_policy_faq", "root_cause_resolver"}

    async def reply(self, x: Msg = None) -> Msg:
        payload = self._parse_payload(x)
        context = self._get_context(payload)
        previous_results = self._get_previous_results(payload)
        scenario = context.get("scenario")

        if scenario == "asset_allocation_failure":
            return await self._resolve_asset(context, previous_results)
        if scenario == "settlement_amount_mismatch":
            return await self._resolve_settlement(context, previous_results)
        return await self._resolve_order(context, previous_results)

    async def _search_kb(
        self, context: Dict, previous_results: List[Dict] = None
    ) -> Dict:
        """调用 RAG 知识库补充证据链；优先使用并行 RAG Agent 结果，不可用再直接检索。"""
        from config import RAG_CONFIG

        previous_results = previous_results or []

        if not RAG_CONFIG.get("enable_resolution_evidence", True):
            return {"summary": "", "kb_matches": []}

        # 1. 优先读取并行调度的 RAGKnowledgeAgent 结果
        rag_result = self._find_previous_result(previous_results, "RAGKnowledgeAgent")
        if rag_result and rag_result.get("status") == "success":
            docs = rag_result.get("retrieved_documents", []) or []
            if docs:
                summaries = []
                kb_matches = []
                for doc in docs:
                    content = doc.get("content", "")
                    metadata = doc.get("metadata", {}) or {}
                    if content:
                        summaries.append(content[:200])
                    kb_matches.append({
                        "source": metadata.get("source", "unknown"),
                        "page": metadata.get("page"),
                        "title": metadata.get("title", ""),
                        "content": content[:300],
                        "similarity": metadata.get("similarity") or round(
                            1.0 - metadata.get("distance", 1.0), 3
                        ),
                    })
                return {
                    "summary": "知识库参考：" + " | ".join(summaries),
                    "kb_matches": kb_matches,
                }

        # 2. 直接检索 RAG（兼容旧路径）
        if self.rag_agent is None:
            return {"summary": "", "kb_matches": []}

        try:
            query = context.get("rewritten_query") or context.get("query") or ""
            issue_type = context.get("issue_type") or ""
            search_query = f"{query} {issue_type}".strip() or query
            results = await self.rag_agent.search_knowledge(search_query, top_k=3)
            if not results:
                return {"summary": "", "kb_matches": []}

            summaries = []
            kb_matches = []
            for r in results:
                content = r.get("content", "")
                metadata = r.get("metadata", {}) or {}
                if content:
                    summaries.append(content[:200])
                kb_matches.append({
                    "source": metadata.get("source", "unknown"),
                    "page": metadata.get("page"),
                    "title": metadata.get("title", ""),
                    "content": content[:300],
                    "similarity": round(1.0 - r.get("distance", 1.0), 3),
                })

            return {
                "summary": "知识库参考：" + " | ".join(summaries),
                "kb_matches": kb_matches,
            }
        except Exception:
            return {"summary": "", "kb_matches": []}

    def _build_responsibility_matrix(
        self, scenario: str, previous_results: List[Dict]
    ) -> List[Dict]:
        """生成责任方判定矩阵，用于前端归属方可视化。"""
        code = self._find_previous_result(previous_results, "CodeAgent")
        operation = self._find_previous_result(previous_results, "OperationAgent")
        data = self._find_previous_result(previous_results, "DataAgent")

        code_status = code.get("status", "unknown")
        operation_status = operation.get("status", "unknown")
        data_status = data.get("status", "unknown")

        code_ok = code_status == "success"
        operation_ok = operation_status == "success"
        data_ok = data_status == "success"

        if scenario == "asset_allocation_failure":
            return [
                {
                    "party": "技术侧（系统配置/权限）",
                    "score": 0.85 if code_ok else 0.3,
                    "reasons": ["跨商户分配权限开关未开启"] if code_ok else ["权限链路未确认"],
                },
                {
                    "party": "业务/运营侧（额度管理）",
                    "score": 0.9 if data_ok else 0.3,
                    "reasons": ["商户可用额度不足"] if data_ok else ["额度状态未确认"],
                },
                {
                    "party": "用户侧（绑定/保护期）",
                    "score": 0.8 if operation_ok else 0.2,
                    "reasons": ["目标用户仍受保护期限制"] if operation_ok else ["绑定状态未确认"],
                },
                {
                    "party": "数据侧",
                    "score": 0.2,
                    "reasons": ["暂无数据异常证据"],
                },
            ]

        if scenario == "settlement_amount_mismatch":
            return [
                {
                    "party": "技术侧（计算链路）",
                    "score": 0.5 if code_ok else 0.2,
                    "reasons": ["规则计算链路基本正常"] if code_ok else ["计算链路未确认"],
                },
                {
                    "party": "业务/运营侧（流程）",
                    "score": 0.4 if operation_ok else 0.2,
                    "reasons": ["人工流程无明显异常"] if operation_ok else ["流程状态未确认"],
                },
                {
                    "party": "用户侧",
                    "score": 0.1,
                    "reasons": ["无用户操作导致金额不符证据"],
                },
                {
                    "party": "数据侧（标签脚本）",
                    "score": 0.95 if data_ok else 0.3,
                    "reasons": ["结算标签被脚本误刷"] if data_ok else ["标签状态未确认"],
                },
            ]

        # order_status_anomaly / generic
        return [
            {
                "party": "技术侧（前后端/接口）",
                "score": 0.2 if code_ok else 0.1,
                "reasons": ["前后端与接口链路无明显异常"] if code_ok else ["链路状态未确认"],
            },
            {
                "party": "业务/运营侧",
                "score": 0.2 if operation_ok else 0.1,
                "reasons": ["操作流程无明显异常"] if operation_ok else ["操作侧未确认"],
            },
            {
                "party": "用户侧",
                "score": 0.15 if operation_ok else 0.1,
                "reasons": ["无用户误操作证据"] if operation_ok else ["用户侧未确认"],
            },
            {
                "party": "数据侧（后台脚本）",
                "score": 0.95 if data_ok else 0.3,
                "reasons": ["退款回调后状态同步任务超时"] if data_ok else ["数据侧未确认"],
            },
        ]

    async def _resolve_order(self, context: Dict, previous_results: List[Dict]) -> Msg:
        history_result = await self._run_skill(
            "search_history_ticket",
            context,
            "复核同类历史工单",
            "辅助归因",
            previous_results,
        )
        policy_result = await self._run_skill(
            "search_policy_faq",
            context,
            "补充标准处理建议",
            "处理建议",
            previous_results,
        )
        kb_result = await self._search_kb(context, previous_results)
        code = self._find_previous_result(previous_results, "CodeAgent")
        operation = self._find_previous_result(previous_results, "OperationAgent")
        data = self._find_previous_result(previous_results, "DataAgent")
        evidence = [
            code.get("path_verdict", ""),
            operation.get("path_verdict", ""),
            data.get("path_verdict", ""),
            history_result.get("summary", ""),
            policy_result.get("summary", ""),
            kb_result.get("summary", ""),
        ]
        evidence = [item for item in evidence if item]
        scenario = context.get("scenario", "generic_ticket_diagnosis")
        return self._response(
            status="success",
            summary="交叉验证后确认问题位于数据同步链路，而非前后端代码或用户操作。",
            evidence=evidence,
            next_actions=[
                "手动修复异常订单状态",
                "排查状态同步脚本与回调超时问题",
                "补充重试和告警机制",
            ],
            recommended_skills=["search_history_ticket", "search_policy_faq"],
            tools_called=self._dedupe_tools([history_result, policy_result]),
            responsible_party="数据侧（后台脚本）",
            responsible_party_matrix=self._build_responsibility_matrix(
                scenario, previous_results
            ),
            root_cause="退款回调后订单状态同步任务超时，导致订单状态未更新。",
            recommendations=[
                "手动修复 ORD-8823 的订单状态",
                "排查退款回调脚本死锁与重试耗尽问题",
                "补充同批次订单巡检与告警",
            ],
            history_matches=history_result.get("history_matches", []),
            policy_matches=policy_result.get("policy_matches", []),
            kb_matches=kb_result.get("kb_matches", []),
        )

    async def _resolve_asset(self, context: Dict, previous_results: List[Dict]) -> Msg:
        history_result = await self._run_skill(
            "search_history_ticket",
            context,
            "复核资产分配类历史案例",
            "历史处理经验",
            previous_results,
        )
        kb_result = await self._search_kb(context, previous_results)
        code = self._find_previous_result(previous_results, "CodeAgent")
        operation = self._find_previous_result(previous_results, "OperationAgent")
        data = self._find_previous_result(previous_results, "DataAgent")
        evidence = [
            data.get("path_verdict", ""),
            operation.get("path_verdict", ""),
            code.get("path_verdict", ""),
            history_result.get("summary", ""),
            kb_result.get("summary", ""),
        ]
        evidence = [item for item in evidence if item]
        scenario = context.get("scenario", "generic_ticket_diagnosis")
        return self._response(
            status="success",
            summary="诊断确认资产分配失败由额度、绑定保护期与权限限制叠加导致。",
            evidence=evidence,
            next_actions=[
                "优先回收未使用额度",
                "等待保护期结束后再分配",
                "如需跨商户分配，申请补充权限",
            ],
            recommended_skills=["search_history_ticket"],
            tools_called=history_result.get("tools_called", []),
            responsible_party="业务配置与权限",
            responsible_party_matrix=self._build_responsibility_matrix(
                scenario, previous_results
            ),
            root_cause="商户可用额度不足，同时目标用户仍受保护期限制，操作者也缺少跨商户分配权限。",
            recommendations=[
                "回收未使用额度后再重试",
                "等待用户保护期结束",
                "申请跨商户分配权限",
            ],
            history_matches=history_result.get("history_matches", []),
            kb_matches=kb_result.get("kb_matches", []),
        )

    async def _resolve_settlement(self, context: Dict, previous_results: List[Dict]) -> Msg:
        history_result = await self._run_skill(
            "search_history_ticket",
            context,
            "复核相似结算异常案例",
            "历史处理经验",
            previous_results,
        )
        policy_result = await self._run_skill(
            "search_policy_faq",
            context,
            "补充结算规则处理建议",
            "处理建议",
            previous_results,
        )
        kb_result = await self._search_kb(context, previous_results)
        code = self._find_previous_result(previous_results, "CodeAgent")
        operation = self._find_previous_result(previous_results, "OperationAgent")
        data = self._find_previous_result(previous_results, "DataAgent")
        evidence = [
            code.get("path_verdict", ""),
            operation.get("path_verdict", ""),
            data.get("path_verdict", ""),
            history_result.get("summary", ""),
            policy_result.get("summary", ""),
            kb_result.get("summary", ""),
        ]
        evidence = [item for item in evidence if item]
        scenario = context.get("scenario", "generic_ticket_diagnosis")
        return self._response(
            status="success",
            summary="诊断确认结算金额不符的根因位于数据标签与规则不一致。",
            evidence=evidence,
            next_actions=[
                "修正商户结算标签与规则",
                "重新核算账期并处理差额",
                "增加标签脚本变更审计",
            ],
            recommended_skills=["search_history_ticket", "search_policy_faq"],
            tools_called=self._dedupe_tools([history_result, policy_result]),
            responsible_party="数据侧（标签脚本）",
            responsible_party_matrix=self._build_responsibility_matrix(
                scenario, previous_results
            ),
            root_cause="商户结算标签被脚本误刷，导致按错误分润比例结算。",
            recommendations=[
                "修正商户结算标签与规则",
                "重新核算账期并补发或冲正差额",
                "为标签脚本增加审计与变更告警",
            ],
            history_matches=history_result.get("history_matches", []),
            policy_matches=policy_result.get("policy_matches", []),
            kb_matches=kb_result.get("kb_matches", []),
        )
