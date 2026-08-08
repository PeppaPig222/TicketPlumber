#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
轻量工具注册中心（MCP 理念）
管理工单诊断场景的 9 个核心诊断工具
对应 30 个原子 Skill 的数据源适配层
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any, Callable, Coroutine
import re

from config import RESILIENCE_CONFIG

logger = logging.getLogger(__name__)

# Mock 数据根目录
MOCK_DIR = Path(__file__).parent.parent / "data" / "mock"


def _load_json(filename: str) -> Any:
    """加载 mock JSON 数据"""
    path = MOCK_DIR / filename
    if not path.exists():
        logger.warning(f"Mock data file not found: {path}")
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class ToolRegistry:
    """轻量工具注册中心，提供 MCP 风格的工具定义与执行"""

    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._register_builtin_tools()

    def _register_builtin_tools(self):
        """注册所有内置诊断工具"""
        # ── 入口解析 ──
        self.register("query_ticket", "查询工单详情（商户ID、订单号、问题描述、附件）",
                      {"ticket_id": "string"}, self._handle_query_ticket)

        # ── 订单相关 ──
        self.register("query_order", "查询订单状态、支付记录、退款流水（对应 GetOrderDetail/GetOrderTimeline/GetOrderRefund）",
                      {"order_id": "string"}, self._handle_query_order)

        # ── 日志追踪 ──
        self.register("trace_api", "查询指定接口在指定时间段的调用日志（对应 TraceRequestLog）",
                      {"api_path": "string", "order_id": "string (optional)"}, self._handle_trace_api)

        # ── 商户相关 ──
        self.register("query_merchant", "查询商户信息、合同、合作状态（对应 GetMerchantProfile/GetMerchantContract/GetMerchantCoopStatus）",
                      {"merchant_id": "string"}, self._handle_query_merchant)

        # ── 资产相关 ──
        self.register("query_asset", "查询资产池、分配记录、用户绑定（对应 GetAssetPool/GetAssetAllocation/GetUserBinding/GetProtectionPeriod）",
                      {"merchant_id": "string", "user_id": "string (optional)"}, self._handle_query_asset)

        # ── 结算相关 ──
        self.register("query_settlement", "查询账单、结算、对账数据（对应 GetBillDetail/GetSettlementStatus/GetReconciliation）",
                      {"merchant_id": "string", "bill_period": "string (optional)"}, self._handle_query_settlement)

        # ── 配置检查 ──
        self.register("check_config", "查询功能开关、灰度配置、商户权限（对应 GetBillingConfig/GetMerchantPermission）",
                      {"merchant_id": "string", "config_key": "string (optional)"}, self._handle_check_config)

        # ── 数据一致性 ──
        self.register("check_data", "跨表对比数据一致性（订单表 vs 支付表 vs 结算表 vs 资产表）",
                      {"order_id": "string", "merchant_id": "string (optional)"}, self._handle_check_data)

        # ── RAG 检索 ──
        self.register("search_kb", "RAG 知识库检索（对应 SearchHistoryTicket/SearchPolicyFAQ）",
                      {"query": "string", "merchant_id": "string (optional)"}, self._handle_search_kb)

    # ───────────── Mock 数据处理器 ─────────────

    async def _handle_query_ticket(self, ticket_id: str = None, **kwargs) -> Dict[str, Any]:
        tickets = _load_json("tickets.json") or []
        for t in tickets:
            if t.get("ticket_id") == ticket_id:
                return {"status": "success", "data": t}
        return {"status": "not_found", "message": f"工单 {ticket_id} 不存在", "data": tickets}

    async def _handle_query_order(self, order_id: str = None, **kwargs) -> Dict[str, Any]:
        orders = _load_json("orders.json") or []
        for o in orders:
            if o.get("order_id") == order_id:
                return {"status": "success", "data": o}
        return {"status": "not_found", "message": f"订单 {order_id} 不存在", "data": orders}

    async def _handle_trace_api(self, api_path: str = None, order_id: str = None, **kwargs) -> Dict[str, Any]:
        logs = _load_json("api_logs.json") or []
        matched = [l for l in logs if l.get("api_path") == api_path]
        if order_id:
            matched = [l for l in matched if l.get("order_id") == order_id]
        return {"status": "success", "data": matched} if matched else {"status": "no_logs", "message": "未找到匹配日志", "data": []}

    async def _handle_query_merchant(self, merchant_id: str = None, **kwargs) -> Dict[str, Any]:
        merchants = _load_json("merchants.json") or []
        for m in merchants:
            if m.get("merchant_id") == merchant_id:
                return {"status": "success", "data": m}
        return {"status": "not_found", "message": f"商户 {merchant_id} 不存在", "data": merchants}

    async def _handle_query_asset(self, merchant_id: str = None, **kwargs) -> Dict[str, Any]:
        assets = _load_json("assets.json") or []
        for a in assets:
            if a.get("merchant_id") == merchant_id:
                return {"status": "success", "data": a}
        return {"status": "not_found", "message": f"商户 {merchant_id} 无资产数据", "data": assets}

    async def _handle_query_settlement(self, merchant_id: str = None, **kwargs) -> Dict[str, Any]:
        settlements = _load_json("settlement.json") or []
        for s in settlements:
            if s.get("merchant_id") == merchant_id:
                return {"status": "success", "data": s}
        return {"status": "not_found", "message": f"商户 {merchant_id} 无结算数据", "data": settlements}

    async def _handle_check_config(self, merchant_id: str = None, config_key: str = None, **kwargs) -> Dict[str, Any]:
        # 简化版：从商户数据中提取配置相关字段
        merchants = _load_json("merchants.json") or []
        for m in merchants:
            if m.get("merchant_id") == merchant_id:
                return {
                    "status": "success",
                    "data": {
                        "refund_enabled": True,
                        "gray_release": False,
                        "merchant_type": m.get("type"),
                        "merchant_label": m.get("label"),
                        "permissions": ["order_view", "asset_allocate", "settlement_view"]
                    }
                }
        return {"status": "not_found", "message": f"商户 {merchant_id} 无配置数据"}

    async def _handle_check_data(self, order_id: str = None, **kwargs) -> Dict[str, Any]:
        snapshots = _load_json("db_snapshots.json") or {}
        if order_id in snapshots:
            data = snapshots[order_id]
            # 检测不一致
            inconsistencies = []
            order_status = data.get("order_main", {}).get("status")
            payment_status = data.get("payment_records", {}).get("status")
            settlement_status = data.get("settlement_records", {}).get("status")
            if order_status != payment_status:
                inconsistencies.append({"table": "order_main vs payment_records", "order": order_status, "payment": payment_status})
            return {
                "status": "success",
                "data": data,
                "inconsistencies": inconsistencies,
                "verdict": "数据不一致" if inconsistencies else "数据一致"
            }
        return {"status": "not_found", "message": f"订单 {order_id} 无数据快照"}

    async def _handle_search_kb(self, query: str = None, **kwargs) -> Dict[str, Any]:
        """基于 mock 数据的轻量知识库检索"""
        entries = _load_json("knowledge_base.json") or []
        merchant_id = kwargs.get("merchant_id")
        query = query or ""

        tokens = [
            token.strip().lower()
            for token in re.split(r"[\s,，。；;、]+", query)
            if token.strip()
        ]

        matched = []
        for entry in entries:
            if merchant_id and entry.get("merchant_id") and entry.get("merchant_id") != merchant_id:
                continue

            haystacks = [
                entry.get("summary", ""),
                entry.get("resolution", ""),
                " ".join(entry.get("keywords", [])),
                entry.get("category", ""),
            ]
            searchable_text = " ".join(haystacks).lower()

            score = 0
            for token in tokens:
                if token in searchable_text:
                    score += 2
            for keyword in entry.get("keywords", []):
                if keyword.lower() in query.lower():
                    score += 3

            if score > 0:
                matched.append({
                    **entry,
                    "score": score,
                })

        matched.sort(key=lambda item: item.get("score", 0), reverse=True)
        top_matches = matched[:3]

        return {
            "status": "success" if top_matches else "no_match",
            "query": query,
            "data": top_matches,
            "message": "命中知识库结果" if top_matches else "未命中知识库"
        }

    # ───────────── 注册/执行接口 ─────────────

    def register(self, name: str, description: str, parameters: dict, handler: Callable[..., Coroutine]):
        """注册一个诊断工具"""
        self._tools[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "handler": handler,
        }
        logger.info(f"Tool registered: {name}")

    def get_tool_schemas(self) -> str:
        """生成给 LLM 的工具描述（JSON Schema 风格），用于 Prompt 注入"""
        schemas = []
        for name, tool in self._tools.items():
            schemas.append(f"- {name}: {tool['description']}\n  参数: {tool['parameters']}")
        return "\n".join(schemas)

    def get_tool_names(self) -> list:
        """获取所有已注册工具名"""
        return list(self._tools.keys())

    async def execute(self, name: str, **kwargs) -> Dict[str, Any]:
        """执行工具调用，支持超时与统一降级返回格式。"""
        if name not in self._tools:
            logger.error(f"Tool not found: {name}")
            return {
                "status": "error",
                "tool": name,
                "message": f"工具 {name} 不存在",
                "data": {"available": self.get_tool_names()},
            }

        timeout = RESILIENCE_CONFIG.get("skill_timeout_sec", 5.0)
        handler = self._tools[name]["handler"]

        try:
            result = await asyncio.wait_for(handler(**kwargs), timeout=timeout)
            # 对老代码兼容：如果 handler 返回的是不带 status 的字典，默认视为 success
            if isinstance(result, dict) and "status" not in result and "error" not in result:
                return {"status": "success", **result}
            if isinstance(result, dict) and "error" in result and "status" not in result:
                return {
                    "status": "error",
                    "tool": name,
                    "message": str(result.get("error")),
                    "data": result,
                }
            return result
        except asyncio.TimeoutError:
            logger.warning(f"Tool {name} execution timed out after {timeout}s")
            return {
                "status": "timeout",
                "tool": name,
                "message": f"工具 {name} 执行超时（{timeout}秒）",
                "data": {},
            }
        except Exception as e:
            logger.error(f"Tool {name} execution failed: {e}")
            return {
                "status": "error",
                "tool": name,
                "message": str(e),
                "data": {},
            }


# 全局单例
tool_registry = ToolRegistry()
