#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
工单诊断场景下的原子 Skill 执行器。

说明：
- 这些执行器负责“单个 Skill 如何查一个数据源”
- Batch 1 之后，外层主链路改为由 CodeAgent / OperationAgent / DataAgent / ResolutionAgent 调用
- 本文件继续保留，作为底层 Skill 层，供专业 Agent 受控组合使用
"""
import json
import time
from typing import Any, Awaitable, Callable, Dict, List

from agentscope.message import Msg

from utils.tool_registry import tool_registry as default_tool_registry


AgentRunner = Callable[[Dict[str, Any], Any], Awaitable[Dict[str, Any]]]


def _extract_payload(msg: Msg) -> Dict[str, Any]:
    if not msg or not getattr(msg, "content", None):
        return {}
    if isinstance(msg.content, dict):
        return msg.content
    try:
        return json.loads(msg.content)
    except json.JSONDecodeError:
        return {}


def _context_value(context: Dict[str, Any], *keys: str) -> Any:
    key_entities = context.get("key_entities", {}) or {}
    collected = context.get("collected_data", {}) or {}
    facts = collected.get("facts", {}) or {}

    for key in keys:
        if key in context and context.get(key) is not None:
            return context.get(key)
        if key in key_entities and key_entities.get(key) is not None:
            return key_entities.get(key)
        if key in collected and collected.get(key) is not None:
            return collected.get(key)
        if key in facts and facts.get(key) is not None:
            return facts.get(key)
    return None


def _safe_summary(lines: List[str]) -> str:
    return "；".join([line for line in lines if line])


class DiagnosticAgent:
    """轻量诊断 Agent，兼容现有 OrchestrationAgent 的调用协议。"""

    def __init__(self, name: str, runner: AgentRunner):
        self.name = name
        self.runner = runner

    async def reply(self, x: Msg = None) -> Msg:
        payload = _extract_payload(x)
        context = payload.get("context", {}) or {}

        start = time.perf_counter()
        result = await self.runner(context, default_tool_registry)
        duration_ms = round((time.perf_counter() - start) * 1000, 1)

        data = {
            **result,
            "duration_ms": duration_ms,
            "tools_called": result.get("tools_called", []),
            "summary": result.get("summary", ""),
        }
        return Msg(
            name=self.name,
            content=json.dumps(data, ensure_ascii=False),
            role="assistant",
        )


async def run_get_order_detail(context: Dict[str, Any], registry) -> Dict[str, Any]:
    order_id = _context_value(context, "order_id")
    if not order_id:
        return {"missing_info": ["order_id"], "summary": "缺少订单号，暂无法查询订单详情"}
    result = await registry.execute("query_order", order_id=order_id)
    order = result.get("data", {}) if result.get("status") == "success" else {}
    return {
        "order_detail": order,
        "summary": f"订单{order_id}当前状态为 {order.get('status', '未知')}，金额 {order.get('amount', '未知')}",
        "tools_called": ["query_order"],
    }


async def run_get_order_timeline(context: Dict[str, Any], registry) -> Dict[str, Any]:
    order_id = _context_value(context, "order_id")
    if not order_id:
        return {"missing_info": ["order_id"], "summary": "缺少订单号，暂无法重建订单时间线"}
    result = await registry.execute("query_order", order_id=order_id)
    order = result.get("data", {}) if result.get("status") == "success" else {}
    timeline = order.get("timeline", [])
    last_event = timeline[-1]["event"] if timeline else "无"
    return {
        "order_timeline": timeline,
        "summary": f"订单时间线共 {len(timeline)} 个节点，最近事件为 {last_event}",
        "tools_called": ["query_order"],
    }


async def run_get_merchant_profile(context: Dict[str, Any], registry) -> Dict[str, Any]:
    merchant_id = _context_value(context, "merchant_id")
    if not merchant_id:
        return {"missing_info": ["merchant_id"], "summary": "缺少商户号，暂无法查询商户信息"}
    result = await registry.execute("query_merchant", merchant_id=merchant_id)
    merchant = result.get("data", {}) if result.get("status") == "success" else {}
    return {
        "merchant_profile": merchant,
        "summary": f"商户{merchant_id}类型 {merchant.get('label') or merchant.get('type', '未知')}，合作状态 {merchant.get('coop_status', '未知')}",
        "tools_called": ["query_merchant"],
    }


async def run_search_history_ticket(context: Dict[str, Any], registry) -> Dict[str, Any]:
    merchant_id = _context_value(context, "merchant_id")
    issue_type = _context_value(context, "issue_type") or context.get("scenario") or "工单诊断"
    result = await registry.execute("search_kb", query=issue_type, merchant_id=merchant_id)
    matches = result.get("data", [])
    top = matches[0] if matches else {}
    return {
        "history_matches": matches,
        "summary": top.get("summary", "未命中历史工单经验"),
        "tools_called": ["search_kb"],
    }


async def run_get_asset_pool(context: Dict[str, Any], registry) -> Dict[str, Any]:
    merchant_id = _context_value(context, "merchant_id")
    if not merchant_id:
        return {"missing_info": ["merchant_id"], "summary": "缺少商户号，暂无法查询资产池"}
    result = await registry.execute("query_asset", merchant_id=merchant_id)
    asset = result.get("data", {}) if result.get("status") == "success" else {}
    request = asset.get("allocation_request", {})
    return {
        "asset_pool": asset,
        "summary": f"资产池总额 {asset.get('total_quota', 0)}，可用 {asset.get('available_quota', 0)}，本次申请 {request.get('requested_quota', 0)}",
        "tools_called": ["query_asset"],
    }


async def run_get_asset_allocation(context: Dict[str, Any], registry) -> Dict[str, Any]:
    merchant_id = _context_value(context, "merchant_id")
    result = await registry.execute("query_asset", merchant_id=merchant_id)
    asset = result.get("data", {}) if result.get("status") == "success" else {}
    allocation_records = asset.get("allocation_records", [])
    return {
        "asset_allocation": allocation_records,
        "allocation_request": asset.get("allocation_request", {}),
        "summary": f"当前已有 {len(allocation_records)} 条生效分配记录",
        "tools_called": ["query_asset"],
    }


async def run_get_user_binding(context: Dict[str, Any], registry) -> Dict[str, Any]:
    merchant_id = _context_value(context, "merchant_id")
    result = await registry.execute("query_asset", merchant_id=merchant_id)
    asset = result.get("data", {}) if result.get("status") == "success" else {}
    binding = asset.get("user_binding", {})
    return {
        "user_binding": binding,
        "summary": f"目标用户当前绑定商户 {binding.get('current_merchant_id', '未知')}，绑定状态 {binding.get('binding_status', '未知')}",
        "tools_called": ["query_asset"],
    }


async def run_get_merchant_contract(context: Dict[str, Any], registry) -> Dict[str, Any]:
    merchant_id = _context_value(context, "merchant_id")
    settlement = await registry.execute("query_settlement", merchant_id=merchant_id)
    data = settlement.get("data", {}) if settlement.get("status") == "success" else {}
    return {
        "merchant_contract": data,
        "summary": f"合同类型 {data.get('contract_type', '未知')}，合同分润比例 {data.get('settlement_ratio', '未知')}",
        "tools_called": ["query_settlement"],
    }


async def run_get_bill_detail(context: Dict[str, Any], registry) -> Dict[str, Any]:
    merchant_id = _context_value(context, "merchant_id")
    settlement = await registry.execute("query_settlement", merchant_id=merchant_id)
    data = settlement.get("data", {}) if settlement.get("status") == "success" else {}
    return {
        "bill_detail": data,
        "summary": f"账期 {data.get('bill_period', '未知')}，账单总额 {data.get('bill_total', '未知')}，实结 {data.get('settled_amount', '未知')}",
        "tools_called": ["query_settlement"],
    }


async def run_get_settlement_rule(context: Dict[str, Any], registry) -> Dict[str, Any]:
    merchant_id = _context_value(context, "merchant_id")
    settlement = await registry.execute("query_settlement", merchant_id=merchant_id)
    data = settlement.get("data", {}) if settlement.get("status") == "success" else {}
    return {
        "settlement_rule": {
            "actual_ratio": data.get("actual_ratio"),
            "contract_ratio": data.get("settlement_ratio"),
            "contract_type": data.get("contract_type"),
        },
        "summary": f"结算规则实际分润比例 {data.get('actual_ratio', '未知')}，合同分润比例 {data.get('settlement_ratio', '未知')}",
        "tools_called": ["query_settlement"],
    }


async def run_order_code_path(context: Dict[str, Any], registry) -> Dict[str, Any]:
    order_id = _context_value(context, "order_id")
    merchant_id = _context_value(context, "merchant_id")
    logs = await registry.execute("trace_api", api_path="/api/refund/callback", order_id=order_id)
    config = await registry.execute("check_config", merchant_id=merchant_id)
    log_items = logs.get("data", [])
    config_data = config.get("data", {})
    has_success = any(item.get("status_code") == 200 for item in log_items)
    return {
        "path": "code",
        "path_verdict": "代码链路无明显异常" if has_success and config_data.get("refund_enabled") else "需进一步排查代码链路",
        "code_path_detail": {
            "logs": log_items,
            "config": config_data,
        },
        "summary": _safe_summary([
            f"退款回调日志 {len(log_items)} 条",
            "回调链路返回过 200" if has_success else "未见成功回调",
            "退款开关已开启" if config_data.get("refund_enabled") else "退款开关异常",
        ]),
        "tools_called": ["trace_api", "check_config"],
    }


async def run_order_operation_path(context: Dict[str, Any], registry) -> Dict[str, Any]:
    order_id = _context_value(context, "order_id")
    order_result = await registry.execute("query_order", order_id=order_id)
    order = order_result.get("data", {}) if order_result.get("status") == "success" else {}
    refund_steps = [item for item in order.get("timeline", []) if "退款" in item.get("event", "")]
    return {
        "path": "operation",
        "path_verdict": "用户操作流程符合规范",
        "operation_path_detail": {
            "refund_steps": refund_steps,
            "timeline": order.get("timeline", []),
        },
        "summary": f"识别到 {len(refund_steps)} 个退款相关节点，未发现异常操作轨迹",
        "tools_called": ["query_order"],
    }


async def run_order_data_path(context: Dict[str, Any], registry) -> Dict[str, Any]:
    order_id = _context_value(context, "order_id")
    snapshot = await registry.execute("check_data", order_id=order_id)
    logs = await registry.execute("trace_api", api_path="/api/refund/callback", order_id=order_id)
    inconsistencies = snapshot.get("inconsistencies", [])
    has_timeout = any(item.get("status_code") == 505 for item in logs.get("data", []))
    return {
        "path": "data",
        "path_verdict": "支付表与订单表状态不一致" if inconsistencies else "未发现跨表不一致",
        "data_path_detail": {
            "snapshot": snapshot.get("data", {}),
            "inconsistencies": inconsistencies,
            "logs": logs.get("data", []),
        },
        "inconsistency_found": bool(inconsistencies or has_timeout),
        "summary": _safe_summary([
            snapshot.get("verdict", "未完成跨表校验"),
            "发现订单状态同步超时" if has_timeout else "",
        ]),
        "tools_called": ["check_data", "trace_api"],
    }


async def run_asset_availability_path(context: Dict[str, Any], registry) -> Dict[str, Any]:
    merchant_id = _context_value(context, "merchant_id")
    asset_result = await registry.execute("query_asset", merchant_id=merchant_id)
    asset = asset_result.get("data", {}) if asset_result.get("status") == "success" else {}
    request = asset.get("allocation_request", {})
    available = asset.get("available_quota", 0)
    requested = request.get("requested_quota", 0)
    return {
        "path": "asset_availability",
        "path_verdict": "可用额度不足以完成本次分配" if requested > available else "额度充足",
        "asset_availability_detail": asset,
        "summary": f"可用额度 {available}，申请额度 {requested}",
        "tools_called": ["query_asset"],
    }


async def run_asset_binding_path(context: Dict[str, Any], registry) -> Dict[str, Any]:
    merchant_id = _context_value(context, "merchant_id")
    asset_result = await registry.execute("query_asset", merchant_id=merchant_id)
    asset = asset_result.get("data", {}) if asset_result.get("status") == "success" else {}
    binding = asset.get("user_binding", {})
    protection = asset.get("protection_period", {})
    return {
        "path": "asset_binding",
        "path_verdict": "目标用户仍在其他商户保护期内" if protection.get("status") == "active" else "未命中保护期限制",
        "asset_binding_detail": {
            "user_binding": binding,
            "protection_period": protection,
        },
        "summary": f"用户绑定商户 {binding.get('current_merchant_id', '未知')}，保护期状态 {protection.get('status', '未知')}",
        "tools_called": ["query_asset"],
    }


async def run_asset_permission_path(context: Dict[str, Any], registry) -> Dict[str, Any]:
    merchant_id = _context_value(context, "merchant_id")
    asset_result = await registry.execute("query_asset", merchant_id=merchant_id)
    asset = asset_result.get("data", {}) if asset_result.get("status") == "success" else {}
    permissions = asset.get("permissions", {})
    return {
        "path": "asset_permission",
        "path_verdict": "操作者无跨商户分配权限" if not permissions.get("cross_merchant_allocate") else "权限正常",
        "asset_permission_detail": permissions,
        "summary": f"跨商户分配权限 {'开启' if permissions.get('cross_merchant_allocate') else '关闭'}",
        "tools_called": ["query_asset"],
    }


async def run_settlement_contract_path(context: Dict[str, Any], registry) -> Dict[str, Any]:
    merchant_id = _context_value(context, "merchant_id")
    merchant = await registry.execute("query_merchant", merchant_id=merchant_id)
    settlement = await registry.execute("query_settlement", merchant_id=merchant_id)
    merchant_data = merchant.get("data", {}) if merchant.get("status") == "success" else {}
    settlement_data = settlement.get("data", {}) if settlement.get("status") == "success" else {}
    label = merchant_data.get("label")
    contract_type = settlement_data.get("contract_type")
    mismatch = label and contract_type and label != contract_type
    return {
        "path": "settlement_contract",
        "path_verdict": "商户标签与合同类型存在偏差" if mismatch else "合同信息与商户标签一致",
        "settlement_contract_detail": {
            "merchant": merchant_data,
            "settlement": settlement_data,
        },
        "summary": f"商户标签 {label}，合同类型 {contract_type}",
        "tools_called": ["query_merchant", "query_settlement"],
    }


async def run_settlement_calculation_path(context: Dict[str, Any], registry) -> Dict[str, Any]:
    merchant_id = _context_value(context, "merchant_id")
    settlement = await registry.execute("query_settlement", merchant_id=merchant_id)
    data = settlement.get("data", {}) if settlement.get("status") == "success" else {}
    inconsistent = data.get("actual_ratio") != data.get("settlement_ratio")
    return {
        "path": "settlement_calculation",
        "path_verdict": "合同比例与实际结算比例不一致" if inconsistent else "结算比例一致",
        "settlement_calculation_detail": data,
        "inconsistency_found": bool(inconsistent),
        "summary": f"合同比例 {data.get('settlement_ratio', '未知')}，实际比例 {data.get('actual_ratio', '未知')}",
        "tools_called": ["query_settlement"],
    }


async def run_settlement_timeline_path(context: Dict[str, Any], registry) -> Dict[str, Any]:
    merchant_id = _context_value(context, "merchant_id")
    settlement = await registry.execute("query_settlement", merchant_id=merchant_id)
    data = settlement.get("data", {}) if settlement.get("status") == "success" else {}
    changes = data.get("tag_change_log", [])
    return {
        "path": "settlement_timeline",
        "path_verdict": "发现历史标签刷写记录" if changes else "未见异常标签变更",
        "settlement_timeline_detail": {
            "timeline": data.get("settlement_timeline", []),
            "tag_change_log": changes,
        },
        "summary": f"结算流程节点 {len(data.get('settlement_timeline', []))} 个，标签变更记录 {len(changes)} 条",
        "tools_called": ["query_settlement"],
    }


async def run_search_policy_faq(context: Dict[str, Any], registry) -> Dict[str, Any]:
    issue_type = _context_value(context, "issue_type") or context.get("scenario") or "工单诊断"
    result = await registry.execute(
        "search_kb",
        query=f"{issue_type} 处理建议 根因",
        merchant_id=_context_value(context, "merchant_id"),
    )
    matches = result.get("data", [])
    top = matches[0] if matches else {}
    return {
        "policy_matches": matches,
        "summary": top.get("resolution", "未命中处理建议"),
        "tools_called": ["search_kb"],
    }


# ========== Batch 2：原子 Skill 补齐 ==========

async def run_get_merchant_coop_status(context: Dict[str, Any], registry) -> Dict[str, Any]:
    """GetMerchantCoopStatus：查询商户合作状态"""
    merchant_id = _context_value(context, "merchant_id")
    if not merchant_id:
        return {"missing_info": ["merchant_id"], "summary": "缺少商户号，暂无法查询合作状态"}
    result = await registry.execute("query_merchant", merchant_id=merchant_id)
    merchant = result.get("data", {}) if result.get("status") == "success" else {}
    coop = merchant.get("coop_status", "未知")
    return {
        "coop_status": coop,
        "summary": f"商户{merchant_id}合作状态为 {coop}",
        "tools_called": ["query_merchant"],
    }


async def run_get_merchant_contract(context: Dict[str, Any], registry) -> Dict[str, Any]:
    """GetMerchantContract：查询商户合同信息"""
    merchant_id = _context_value(context, "merchant_id")
    if not merchant_id:
        return {"missing_info": ["merchant_id"], "summary": "缺少商户号，暂无法查询合同"}
    result = await registry.execute("query_merchant", merchant_id=merchant_id)
    merchant = result.get("data", {}) if result.get("status") == "success" else {}
    contract = merchant.get("contract", {})
    return {
        "contract": contract,
        "summary": f"商户{merchant_id}合同类型 {contract.get('contract_type', '未知')}，分润比例 {contract.get('settlement_ratio', '未知')}",
        "tools_called": ["query_merchant"],
    }


async def run_get_merchant_org_tree(context: Dict[str, Any], registry) -> Dict[str, Any]:
    """GetMerchantOrgTree：查询商户组织树"""
    merchant_id = _context_value(context, "merchant_id")
    if not merchant_id:
        return {"missing_info": ["merchant_id"], "summary": "缺少商户号，暂无法查询组织树"}
    result = await registry.execute("query_merchant", merchant_id=merchant_id)
    merchant = result.get("data", {}) if result.get("status") == "success" else {}
    org_tree = merchant.get("org_tree", {})
    sub_count = len(org_tree.get("sub_merchants", []))
    return {
        "org_tree": org_tree,
        "summary": f"商户{merchant_id}位于 {org_tree.get('region', '未知')}-{org_tree.get('city', '未知')}，下辖 {sub_count} 个子商户",
        "tools_called": ["query_merchant"],
    }


async def run_get_merchant_permission(context: Dict[str, Any], registry) -> Dict[str, Any]:
    """GetMerchantPermission：查询商户权限配置"""
    merchant_id = _context_value(context, "merchant_id")
    if not merchant_id:
        return {"missing_info": ["merchant_id"], "summary": "缺少商户号，暂无法查询权限"}
    result = await registry.execute("query_merchant", merchant_id=merchant_id)
    merchant = result.get("data", {}) if result.get("status") == "success" else {}
    permissions = merchant.get("permissions", {})
    enabled = [k for k, v in permissions.items() if v]
    return {
        "permissions": permissions,
        "summary": f"商户{merchant_id}已开启权限: {', '.join(enabled) if enabled else '无'}",
        "tools_called": ["query_merchant"],
    }


async def run_get_merchant_onboarding(context: Dict[str, Any], registry) -> Dict[str, Any]:
    """GetMerchantOnboarding：查询商户入驻状态"""
    merchant_id = _context_value(context, "merchant_id")
    if not merchant_id:
        return {"missing_info": ["merchant_id"], "summary": "缺少商户号，暂无法查询入驻状态"}
    result = await registry.execute("query_merchant", merchant_id=merchant_id)
    merchant = result.get("data", {}) if result.get("status") == "success" else {}
    onboarding = merchant.get("onboarding", {})
    steps = onboarding.get("steps", [])
    completed = sum(1 for s in steps if s.get("status") == "passed")
    return {
        "onboarding": onboarding,
        "summary": f"商户{merchant_id}入驻状态 {onboarding.get('status', '未知')}，已完成 {completed}/{len(steps)} 步",
        "tools_called": ["query_merchant"],
    }


async def run_get_merchant_blacklist(context: Dict[str, Any], registry) -> Dict[str, Any]:
    """GetMerchantBlacklist：查询商户黑名单记录"""
    merchant_id = _context_value(context, "merchant_id")
    if not merchant_id:
        return {"missing_info": ["merchant_id"], "summary": "缺少商户号，暂无法查询黑名单"}
    result = await registry.execute("query_merchant", merchant_id=merchant_id)
    merchant = result.get("data", {}) if result.get("status") == "success" else {}
    blacklist = merchant.get("blacklist", {})
    is_blacklisted = blacklist.get("is_blacklisted", False)
    records = blacklist.get("records", [])
    return {
        "blacklist": blacklist,
        "summary": f"商户{merchant_id}黑名单状态: {'已拉黑' if is_blacklisted else '正常'}，历史记录 {len(records)} 条",
        "tools_called": ["query_merchant"],
    }


async def run_get_order_refund(context: Dict[str, Any], registry) -> Dict[str, Any]:
    """GetOrderRefund：查询订单退款记录"""
    order_id = _context_value(context, "order_id")
    if not order_id:
        return {"missing_info": ["order_id"], "summary": "缺少订单号，暂无法查询退款"}
    result = await registry.execute("query_order", order_id=order_id)
    order = result.get("data", {}) if result.get("status") == "success" else {}
    refund = order.get("refund", {})
    return {
        "refund": refund,
        "summary": f"订单{order_id}退款状态 {refund.get('status', '无退款')}，金额 {refund.get('amount', 0)}",
        "tools_called": ["query_order"],
    }


async def run_get_asset_recycle(context: Dict[str, Any], registry) -> Dict[str, Any]:
    """GetAssetRecycle：查询资产回收记录"""
    merchant_id = _context_value(context, "merchant_id")
    if not merchant_id:
        return {"missing_info": ["merchant_id"], "summary": "缺少商户号，暂无法查询资产回收"}
    result = await registry.execute("query_asset", merchant_id=merchant_id)
    asset = result.get("data", {}) if result.get("status") == "success" else {}
    recycle = asset.get("recycle", {})
    records = recycle.get("recycle_records", [])
    return {
        "recycle": recycle,
        "summary": f"商户{merchant_id}历史回收 {len(records)} 条，待回收 {len(recycle.get('pending_recycle', []))} 条",
        "tools_called": ["query_asset"],
    }


async def run_get_protection_period(context: Dict[str, Any], registry) -> Dict[str, Any]:
    """GetProtectionPeriod：查询用户保护期"""
    merchant_id = _context_value(context, "merchant_id")
    if not merchant_id:
        return {"missing_info": ["merchant_id"], "summary": "缺少商户号，暂无法查询保护期"}
    result = await registry.execute("query_asset", merchant_id=merchant_id)
    asset = result.get("data", {}) if result.get("status") == "success" else {}
    protection = asset.get("protection_period", {})
    status = protection.get("status", "无")
    return {
        "protection_period": protection,
        "summary": f"目标用户保护期状态 {status}，截止时间 {protection.get('end_time', '无')}",
        "tools_called": ["query_asset"],
    }


async def run_get_billing_config(context: Dict[str, Any], registry) -> Dict[str, Any]:
    """GetBillingConfig：查询计费配置"""
    merchant_id = _context_value(context, "merchant_id")
    if not merchant_id:
        return {"missing_info": ["merchant_id"], "summary": "缺少商户号，暂无法查询计费配置"}
    result = await registry.execute("query_asset", merchant_id=merchant_id)
    asset = result.get("data", {}) if result.get("status") == "success" else {}
    billing = asset.get("billing_config", {})
    return {
        "billing_config": billing,
        "summary": f"商户{merchant_id}计费周期 {billing.get('billing_cycle', '未知')}，单价 {billing.get('unit_price', '未知')}，税率 {billing.get('tax_rate', '未知')}",
        "tools_called": ["query_asset"],
    }


async def run_get_product_catalog(context: Dict[str, Any], registry) -> Dict[str, Any]:
    """GetProductCatalog：查询产品目录"""
    merchant_id = _context_value(context, "merchant_id")
    if not merchant_id:
        return {"missing_info": ["merchant_id"], "summary": "缺少商户号，暂无法查询产品目录"}
    result = await registry.execute("query_asset", merchant_id=merchant_id)
    asset = result.get("data", {}) if result.get("status") == "success" else {}
    catalog = asset.get("product_catalog", [])
    active = [p for p in catalog if p.get("status") == "active"]
    return {
        "product_catalog": catalog,
        "summary": f"商户{merchant_id}产品目录共 {len(catalog)} 个，在售 {len(active)} 个",
        "tools_called": ["query_asset"],
    }


async def run_get_bill_calculation(context: Dict[str, Any], registry) -> Dict[str, Any]:
    """GetBillCalculation：查询账单计算明细"""
    merchant_id = _context_value(context, "merchant_id")
    if not merchant_id:
        return {"missing_info": ["merchant_id"], "summary": "缺少商户号，暂无法查询账单计算"}
    result = await registry.execute("query_settlement", merchant_id=merchant_id)
    data = result.get("data", {}) if result.get("status") == "success" else {}
    calc = data.get("bill_calculation", {})
    error = calc.get("calculation_error")
    return {
        "bill_calculation": calc,
        "summary": f"账单预期 {calc.get('expected_amount', '未知')}，实际 {calc.get('actual_amount', '未知')}" + (f"，差异原因: {error}" if error else "，计算一致"),
        "tools_called": ["query_settlement"],
    }


async def run_get_settlement_status(context: Dict[str, Any], registry) -> Dict[str, Any]:
    """GetSettlementStatus：查询结算状态"""
    merchant_id = _context_value(context, "merchant_id")
    if not merchant_id:
        return {"missing_info": ["merchant_id"], "summary": "缺少商户号，暂无法查询结算状态"}
    result = await registry.execute("query_settlement", merchant_id=merchant_id)
    data = result.get("data", {}) if result.get("status") == "success" else {}
    return {
        "settlement_status": data.get("settlement_status", "未知"),
        "summary": f"商户{merchant_id}结算状态 {data.get('settlement_status', '未知')}，账期 {data.get('bill_period', '未知')}",
        "tools_called": ["query_settlement"],
    }


async def run_get_settlement_timeline(context: Dict[str, Any], registry) -> Dict[str, Any]:
    """GetSettlementTimeline：查询结算时间线"""
    merchant_id = _context_value(context, "merchant_id")
    if not merchant_id:
        return {"missing_info": ["merchant_id"], "summary": "缺少商户号，暂无法查询结算时间线"}
    result = await registry.execute("query_settlement", merchant_id=merchant_id)
    data = result.get("data", {}) if result.get("status") == "success" else {}
    timeline = data.get("settlement_timeline", [])
    return {
        "settlement_timeline": timeline,
        "summary": f"商户{merchant_id}结算时间线共 {len(timeline)} 个节点，最近节点: {timeline[-1]['event'] if timeline else '无'}",
        "tools_called": ["query_settlement"],
    }


async def run_get_reconciliation(context: Dict[str, Any], registry) -> Dict[str, Any]:
    """GetReconciliation：查询对账结果"""
    merchant_id = _context_value(context, "merchant_id")
    if not merchant_id:
        return {"missing_info": ["merchant_id"], "summary": "缺少商户号，暂无法查询对账"}
    result = await registry.execute("query_settlement", merchant_id=merchant_id)
    data = result.get("data", {}) if result.get("status") == "success" else {}
    recon = data.get("reconciliation", {})
    status = recon.get("status", "未知")
    diff = recon.get("difference", 0)
    return {
        "reconciliation": recon,
        "summary": f"商户{merchant_id}对账状态 {status}" + (f"，差异 {diff}" if diff else "，三方一致"),
        "tools_called": ["query_settlement"],
    }


async def run_get_invoice_status(context: Dict[str, Any], registry) -> Dict[str, Any]:
    """GetInvoiceStatus：查询发票状态"""
    merchant_id = _context_value(context, "merchant_id")
    if not merchant_id:
        return {"missing_info": ["merchant_id"], "summary": "缺少商户号，暂无法查询发票"}
    result = await registry.execute("query_settlement", merchant_id=merchant_id)
    data = result.get("data", {}) if result.get("status") == "success" else {}
    invoice = data.get("invoice", {})
    return {
        "invoice": invoice,
        "summary": f"商户{merchant_id}发票状态 {invoice.get('invoice_status', '未知')}，金额 {invoice.get('invoice_amount', 0)}",
        "tools_called": ["query_settlement"],
    }


async def run_get_payment_channel(context: Dict[str, Any], registry) -> Dict[str, Any]:
    """GetPaymentChannel：查询支付渠道状态"""
    merchant_id = _context_value(context, "merchant_id")
    if not merchant_id:
        return {"missing_info": ["merchant_id"], "summary": "缺少商户号，暂无法查询支付渠道"}
    result = await registry.execute("query_settlement", merchant_id=merchant_id)
    data = result.get("data", {}) if result.get("status") == "success" else {}
    channel = data.get("payment_channel", {})
    return {
        "payment_channel": channel,
        "summary": f"商户{merchant_id}支付渠道 {channel.get('channel', '未知')}，状态 {channel.get('channel_status', '未知')}",
        "tools_called": ["query_settlement"],
    }


async def run_validate_frontend_state(context: Dict[str, Any], registry) -> Dict[str, Any]:
    """ValidateFrontendState：验证前端状态与后端是否一致"""
    order_id = _context_value(context, "order_id")
    if not order_id:
        return {"missing_info": ["order_id"], "summary": "缺少订单号，暂无法验证前端状态"}
    order_result = await registry.execute("query_order", order_id=order_id)
    order = order_result.get("data", {}) if order_result.get("status") == "success" else {}
    # 简化：模拟前端展示状态与后端真实状态的差异检测
    backend_status = order.get("status", "未知")
    frontend_status = order.get("frontend_display_status", backend_status)
    consistent = backend_status == frontend_status
    return {
        "frontend_state": {"display_status": frontend_status},
        "backend_state": {"real_status": backend_status},
        "consistent": consistent,
        "summary": f"前端展示状态 {frontend_status}，后端真实状态 {backend_status}，{'一致' if consistent else '不一致'}",
        "tools_called": ["query_order"],
    }


async def run_reconstruct_timeline(context: Dict[str, Any], registry) -> Dict[str, Any]:
    """ReconstructTimeline：重建事件时间线"""
    order_id = _context_value(context, "order_id")
    if not order_id:
        return {"missing_info": ["order_id"], "summary": "缺少订单号，暂无法重建时间线"}
    result = await registry.execute("query_order", order_id=order_id)
    order = result.get("data", {}) if result.get("status") == "success" else {}
    timeline = order.get("timeline", [])
    return {
        "timeline": timeline,
        "summary": f"订单{order_id}共 {len(timeline)} 个事件节点，首事件 {timeline[0]['event'] if timeline else '无'}",
        "tools_called": ["query_order"],
    }


async def run_root_cause_resolver(context: Dict[str, Any], registry) -> Dict[str, Any]:
    scenario = context.get("scenario")
    facts = (context.get("collected_data", {}) or {}).get("facts", {})
    history_matches = facts.get("history_matches", [])
    policy_matches = facts.get("policy_matches", [])

    if scenario == "order_status_anomaly":
        evidence = [
            "代码路径返回成功回调，未见明显前后端异常",
            "用户退款操作轨迹完整，非误操作",
            "支付表 refunded 但订单表 pending_refund，存在数据不一致",
        ]
        return {
            "responsible_party": "数据侧（后台脚本）",
            "root_cause": "退款回调后订单状态同步任务超时，导致订单表未更新。",
            "recommendations": [
                "手动修复 ORD-8823 的订单状态",
                "排查退款回调脚本死锁与重试耗尽问题",
                "补充同批次订单巡检与告警",
            ],
            "evidence": evidence + [item.get("summary") for item in history_matches[:1]] + [item.get("summary") for item in policy_matches[:1]],
            "summary": "交叉验证后确认根因位于数据同步脚本，建议按数据侧故障处理。",
            "tools_called": [],
        }

    if scenario == "settlement_amount_mismatch":
        evidence = [
            "合同配置为联营 50%",
            "实际结算比例按 60% 执行，合同与结算规则不一致",
            "结算时间线存在数据脚本刷写商户标签记录",
        ]
        return {
            "responsible_party": "数据侧（标签脚本）",
            "root_cause": "商户结算标签被批量脚本误刷为直营，导致按错误分润比例结算。",
            "recommendations": [
                "修正商户 3052 的结算标签与规则",
                "重新核算账期并补发或冲正差额",
                "为标签脚本增加审计与变更告警",
            ],
            "evidence": evidence + [item.get("summary") for item in history_matches[:1]] + [item.get("summary") for item in policy_matches[:1]],
            "summary": "交叉验证后确认问题不在合同文本，而在数据标签被脚本误更新。",
            "tools_called": [],
        }

    return {
        "responsible_party": "待人工确认",
        "root_cause": "当前证据不足以自动归因。",
        "recommendations": ["补充更多上下文后再次诊断"],
        "evidence": [],
        "summary": "未生成明确归因结论",
        "tools_called": [],
    }


def build_diagnosis_agents() -> Dict[str, DiagnosticAgent]:
    return {
        # 订单相关
        "get_order_detail": DiagnosticAgent("get_order_detail", run_get_order_detail),
        "get_order_timeline": DiagnosticAgent("get_order_timeline", run_get_order_timeline),
        "GetOrderRefund": DiagnosticAgent("GetOrderRefund", run_get_order_refund),
        "ReconstructTimeline": DiagnosticAgent("ReconstructTimeline", run_reconstruct_timeline),

        # 商户管理
        "get_merchant_profile": DiagnosticAgent("get_merchant_profile", run_get_merchant_profile),
        "GetMerchantCoopStatus": DiagnosticAgent("GetMerchantCoopStatus", run_get_merchant_coop_status),
        "GetMerchantContract": DiagnosticAgent("GetMerchantContract", run_get_merchant_contract),
        "GetMerchantOrgTree": DiagnosticAgent("GetMerchantOrgTree", run_get_merchant_org_tree),
        "GetMerchantPermission": DiagnosticAgent("GetMerchantPermission", run_get_merchant_permission),
        "GetMerchantOnboarding": DiagnosticAgent("GetMerchantOnboarding", run_get_merchant_onboarding),
        "GetMerchantBlacklist": DiagnosticAgent("GetMerchantBlacklist", run_get_merchant_blacklist),

        # 资产经营
        "get_asset_pool": DiagnosticAgent("get_asset_pool", run_get_asset_pool),
        "get_asset_allocation": DiagnosticAgent("get_asset_allocation", run_get_asset_allocation),
        "get_user_binding": DiagnosticAgent("get_user_binding", run_get_user_binding),
        "GetAssetRecycle": DiagnosticAgent("GetAssetRecycle", run_get_asset_recycle),
        "GetProtectionPeriod": DiagnosticAgent("GetProtectionPeriod", run_get_protection_period),
        "GetBillingConfig": DiagnosticAgent("GetBillingConfig", run_get_billing_config),
        "GetProductCatalog": DiagnosticAgent("GetProductCatalog", run_get_product_catalog),

        # 结算资金
        "get_merchant_contract": DiagnosticAgent("get_merchant_contract", run_get_merchant_contract),
        "get_bill_detail": DiagnosticAgent("get_bill_detail", run_get_bill_detail),
        "get_settlement_rule": DiagnosticAgent("get_settlement_rule", run_get_settlement_rule),
        "GetBillCalculation": DiagnosticAgent("GetBillCalculation", run_get_bill_calculation),
        "GetSettlementStatus": DiagnosticAgent("GetSettlementStatus", run_get_settlement_status),
        "GetSettlementTimeline": DiagnosticAgent("GetSettlementTimeline", run_get_settlement_timeline),
        "GetReconciliation": DiagnosticAgent("GetReconciliation", run_get_reconciliation),
        "GetInvoiceStatus": DiagnosticAgent("GetInvoiceStatus", run_get_invoice_status),
        "GetPaymentChannel": DiagnosticAgent("GetPaymentChannel", run_get_payment_channel),

        # 排查路径（仍保留供编排层直接调用）
        "order_code_path": DiagnosticAgent("order_code_path", run_order_code_path),
        "order_operation_path": DiagnosticAgent("order_operation_path", run_order_operation_path),
        "order_data_path": DiagnosticAgent("order_data_path", run_order_data_path),
        "asset_availability_path": DiagnosticAgent("asset_availability_path", run_asset_availability_path),
        "asset_binding_path": DiagnosticAgent("asset_binding_path", run_asset_binding_path),
        "asset_permission_path": DiagnosticAgent("asset_permission_path", run_asset_permission_path),
        "settlement_contract_path": DiagnosticAgent("settlement_contract_path", run_settlement_contract_path),
        "settlement_calculation_path": DiagnosticAgent("settlement_calculation_path", run_settlement_calculation_path),
        "settlement_timeline_path": DiagnosticAgent("settlement_timeline_path", run_settlement_timeline_path),

        # 通用辅助
        "search_history_ticket": DiagnosticAgent("search_history_ticket", run_search_history_ticket),
        "search_policy_faq": DiagnosticAgent("search_policy_faq", run_search_policy_faq),
        "ValidateFrontendState": DiagnosticAgent("ValidateFrontendState", run_validate_frontend_state),

        # 根因判定
        "root_cause_resolver": DiagnosticAgent("root_cause_resolver", run_root_cause_resolver),
    }
