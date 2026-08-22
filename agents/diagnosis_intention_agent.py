#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
工单诊断场景下的规则型 IntentionAgent。
"""
import json
import logging
import re
from typing import Any, Dict, List

from agentscope.message import Msg
from utils.tool_registry import tool_registry

logger = logging.getLogger(__name__)


class DiagnosisIntentionAgent:
    """工单诊断意图识别：输出 scenario + key_entities，不负责调度。"""

    def __init__(
        self,
        name: str = "DiagnosisIntentionAgent",
        rag_agent=None,
        memory_manager=None,
    ):
        self.name = name
        # RAG Agent 用于规则无法识别场景时做知识库 fallback
        self.rag_agent = rag_agent
        # 统一记忆/RAG 入口；优先使用 memory_manager，未注入则回退 rag_agent
        self.memory_manager = memory_manager
        # 语义路由器：规则未命中时做场景分类（懒加载，复用 RAG 的 embedding 模型）
        self._semantic_router = None

    async def reply(self, x: Msg = None) -> Msg:
        payload = self._parse_payload(x)
        round_num = payload.get("round_num", 1)
        query = payload.get("query", "")
        ticket = await self._enrich_ticket(payload.get("ticket", {}) or {}, query)
        collected_data = payload.get("collected_data", {}) or {}
        memory_context = payload.get("memory_context", {}) or {}

        key_entities = await self._build_entities(query, ticket, collected_data)
        scenario = key_entities.get("scenario")
        issue_type = key_entities.get("issue_type")

        base_reasoning = self._build_reasoning(round_num, scenario, issue_type)
        enriched_reasoning = self._enrich_reasoning_with_memory(base_reasoning, memory_context)

        intention = {
            "intent": self._intent_name(round_num),
            "reasoning": enriched_reasoning,
            "intents": [
                {
                    "type": "ticket_diagnosis",
                    "confidence": 0.99 if scenario else 0.55,
                    "description": "商户工单智能诊断",
                    "reason": "检测到工单编号、商户投诉或异常排查语义",
                }
            ],
            "key_entities": key_entities,
            "rewritten_query": query.strip(),
            "scenario": scenario,
            "ticket": ticket,
            "ticket_id": key_entities.get("ticket_id"),
            "issue_type": issue_type,
            "round_num": round_num,
            "query": query,
            "collected_data": collected_data,
        }
        return Msg(
            name=self.name,
            content=json.dumps(intention, ensure_ascii=False),
            role="assistant",
        )

    def _parse_payload(self, msg: Msg) -> Dict[str, Any]:
        if not msg or not getattr(msg, "content", None):
            return {}
        if isinstance(msg.content, dict):
            return msg.content
        try:
            return json.loads(msg.content)
        except json.JSONDecodeError:
            return {"query": str(msg.content)}

    async def _build_entities(self, query: str, ticket: Dict[str, Any], collected_data: Dict[str, Any]) -> Dict[str, Any]:
        from config import RAG_CONFIG

        facts = collected_data.get("facts", {}) or {}
        issue_type = ticket.get("issue_type") or facts.get("issue_type") or self._detect_issue_type(query)
        scenario = self._scenario_from_issue(issue_type, query)

        # 规则未命中时：先用语义路由做场景分类（双阈值 + margin），RAG 作为补充提示与兜底
        kb_hints = []
        route_info = None
        if scenario == "generic_ticket_diagnosis":
            if RAG_CONFIG.get("enable_semantic_router", True):
                route_info = self._semantic_route(query)
                if route_info and route_info["status"] == "known":
                    scenario = route_info["scenario"]

            # RAG 知识检索：保留 kb_hints 供后续 Agent 做证据；语义路由未判出 known 时仍兜底推断场景
            if RAG_CONFIG.get("enable_intention_fallback", True):
                kb_results = await self._search_kb(query)
                if kb_results:
                    # 计算最高相似度（distance 越小越相似）
                    top_distance = min(r.get("distance", 1.0) for r in kb_results)
                    top_similarity = 1.0 - top_distance
                    threshold = RAG_CONFIG.get("min_similarity_threshold", 0.55)

                    kb_hints = [
                        {
                            "source": r.get("metadata", {}).get("source", "unknown"),
                            "page": r.get("metadata", {}).get("page"),
                            "title": r.get("metadata", {}).get("title", ""),
                            "content": r.get("content", "")[:300],
                            "similarity": round(1.0 - r.get("distance", 1.0), 3),
                        }
                        for r in kb_results[:3]
                    ]

                    if scenario == "generic_ticket_diagnosis" and top_similarity >= threshold:
                        kb_text = " ".join([r.get("content", "") for r in kb_results[:3]])
                        scenario = self._scenario_from_issue(issue_type, f"{query} {kb_text}")

        merchant_id = ticket.get("merchant_id") or facts.get("merchant_id") or self._extract_merchant_id(query)
        if not merchant_id:
            merchant_id = await self._resolve_merchant_from_text(query)
        order_id = ticket.get("order_id") or facts.get("order_id") or self._extract(query, r"ORD-\d+")
        if not order_id and merchant_id:
            order_id = await self._resolve_order_from_merchant(merchant_id)
        ticket_id = ticket.get("ticket_id") or facts.get("ticket_id") or self._extract(query, r"WO-\d{8}-\d{4}")

        entities = {
            "ticket_id": ticket_id,
            "merchant_id": merchant_id,
            "order_id": order_id,
            "issue_type": issue_type,
            "scenario": scenario,
            "kb_hints": kb_hints,
        }
        if route_info:
            entities["route"] = route_info
        if ticket:
            entities["ticket_description"] = ticket.get("description")
        return entities

    async def _search_kb(self, query: str) -> List[Dict]:
        """统一通过 memory_manager 检索 RAG 知识；未注入则回退到 rag_agent 直接调用。"""
        from config import RAG_CONFIG

        if self.memory_manager is not None:
            try:
                result = await self.memory_manager.search_knowledge(
                    query,
                    use_rewrite=RAG_CONFIG.get("enable_query_rewrite", True),
                    use_cache=RAG_CONFIG.get("enable_session_cache", True),
                )
                return result.get("retrieved_documents", [])
            except Exception:
                return []

        # 兼容旧路径：无 memory_manager 时直接调用 rag_agent
        if self.rag_agent is None:
            return []
        try:
            return await self.rag_agent.search_knowledge(query, top_k=3)
        except Exception:
            return []

    def _get_semantic_router(self):
        """懒加载语义路由器，复用 RAG 的 embedding 模型，避免重复加载。"""
        if self._semantic_router is not None:
            return self._semantic_router

        from config import RAG_CONFIG
        from agents.semantic_router import SemanticRouter

        encoder = None
        if self.rag_agent is not None:
            encoder = getattr(self.rag_agent, "embedding_model", None)
            # 优先取 encode 方法引用；内部会做归一化
            encoder = getattr(encoder, "encode", None)

        if encoder is None:
            logger.warning("语义路由无法获取 embedding 模型，降级为纯规则")
            return None

        try:
            self._semantic_router = SemanticRouter(
                encoder=encoder,
                high_threshold=RAG_CONFIG.get("semantic_router_high_threshold", 0.6),
                low_threshold=RAG_CONFIG.get("semantic_router_low_threshold", 0.45),
                margin=RAG_CONFIG.get("semantic_router_margin", 0.08),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"语义路由初始化失败，降级为纯规则: {e}")
            self._semantic_router = None
        return self._semantic_router

    def _semantic_route(self, query: str):
        """调用语义路由做场景分类，失败返回 None（由上层降级处理）。"""
        router = self._get_semantic_router()
        if router is None:
            return None
        try:
            return router.route(query)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"语义路由失败: {e}")
            return None

    def _intent_name(self, round_num: int) -> str:
        if round_num == 1:
            return "ticket_diagnosis"
        if round_num == 2:
            return "deep_diagnosis"
        return "cross_validate_and_resolve"

    def _build_reasoning(self, round_num: int, scenario: str, issue_type: str) -> str:
        stage_text = {
            1: "先做基础信息收集，确认工单涉及的核心实体与历史案例。",
            2: "进入深度诊断，按场景拆成多条并行排查路径。",
            3: "对异常证据做交叉验证并输出归属判定。",
        }
        scenario_text = {
            "order_status_anomaly": "订单状态异常场景",
            "asset_allocation_failure": "资产分配失败场景",
            "settlement_amount_mismatch": "结算金额不符场景",
        }
        return f"{stage_text.get(round_num, '继续诊断')} 当前识别为 {scenario_text.get(scenario, '未知工单场景')}，问题类型：{issue_type or '待补充'}。"

    def _enrich_reasoning_with_memory(
        self, base_reasoning: str, memory_context: Dict[str, Any]
    ) -> str:
        """把记忆上下文追加到 reasoning，仅用于丰富意图理解，不改动 scenario/调度。"""
        extras: List[str] = []

        recent_dialogue = memory_context.get("recent_dialogue", "")
        if recent_dialogue:
            extras.append(f"近期对话：{recent_dialogue}")

        merchant_profile = memory_context.get("merchant_profile", "")
        if merchant_profile:
            extras.append(f"商户画像：{merchant_profile}")

        similar_patterns = memory_context.get("similar_patterns", [])
        if similar_patterns:
            summaries = [
                p.get("summary", "") or p.get("pattern", "")
                for p in similar_patterns[:2]
            ]
            extras.append(f"相似历史模式：{'; '.join(s for s in summaries if s)}")

        if not extras:
            return base_reasoning
        return base_reasoning + " [记忆上下文] " + " | ".join(extras)

    def _detect_issue_type(self, query: str) -> str:
        if "结算" in query:
            return "结算金额不符"
        if "资产" in query or "免时长" in query or "分配" in query:
            return "资产分配失败"
        if "订单" in query:
            return "订单状态异常"
        return "工单诊断"

    def _scenario_from_issue(self, issue_type: str, query: str) -> str:
        source = f"{issue_type or ''} {query}"
        if "结算" in source:
            return "settlement_amount_mismatch"
        if "资产" in source or "免时长" in source or "分配" in source:
            return "asset_allocation_failure"
        if "订单" in source:
            return "order_status_anomaly"
        return "generic_ticket_diagnosis"

    async def _enrich_ticket(self, ticket: Dict[str, Any], query: str) -> Dict[str, Any]:
        if ticket.get("ticket_id") and ticket.get("issue_type"):
            return ticket

        ticket_id = ticket.get("ticket_id") or self._extract(query, r"WO-\d{8}-\d{4}")
        if not ticket_id:
            return ticket

        result = await tool_registry.execute("query_ticket", ticket_id=ticket_id)
        if result.get("status") == "success" and isinstance(result.get("data"), dict):
            merged = dict(ticket)
            merged.update(result.get("data"))
            return merged
        return ticket

    def _extract(self, text: str, pattern: str) -> str:
        matched = re.search(pattern, text or "")
        return matched.group(0) if matched else ""

    def _extract_merchant_id(self, text: str) -> str:
        """从 query 提取商户号，兼容「商户2037」这类中文紧邻数字的场景。

        原正则 \\b\\d{4,6}\\b 在 Python Unicode 模式下会因中文字符被视为单词字符，
        导致「商户2037」中的数字无法命中 \\b 边界，商户号被漏提。这里显式匹配
        「商户XXXX」前缀，避免把金额等独立数字误判为商户号。
        """
        matched = re.search(r"商户\s*(\d{4,6})", text or "")
        return matched.group(1) if matched else ""

    async def _resolve_merchant_from_text(self, query: str) -> str:
        """当 query 只含商户名、无数字商户号时，按名称反查 merchant_id。"""
        try:
            result = await tool_registry.execute("resolve_merchant", text=query)
        except Exception:
            return ""
        if result.get("status") == "success" and isinstance(result.get("data"), dict):
            return result["data"].get("merchant_id", "")
        return ""

    async def _resolve_order_from_merchant(self, merchant_id: str) -> str:
        """订单场景下，仅有商户号时按商户反查订单号。"""
        try:
            result = await tool_registry.execute("resolve_order", merchant_id=merchant_id)
        except Exception:
            return ""
        if result.get("status") == "success" and isinstance(result.get("data"), dict):
            return result["data"].get("order_id", "")
        return ""
