#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Batch 2 原子 Skill 测试
验证 TODO Batch2 要求的 16 个新原子 Skill 均已注册并可执行
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pytest
from skills.registry import SkillRegistry

pytestmark = pytest.mark.asyncio


BATCH2_SKILLS = {
        # 商户管理域
    "GetMerchantCoopStatus",
    "GetMerchantContract",
    "GetMerchantOrgTree",
    "GetMerchantPermission",
    "GetMerchantOnboarding",
    "GetMerchantBlacklist",
    # 商家经营域
    "GetOrderRefund",
    "GetAssetRecycle",
    "GetProtectionPeriod",
    "GetBillingConfig",
    "GetProductCatalog",
    # 资金结算域
    "GetBillCalculation",
    "GetSettlementStatus",
    "GetSettlementTimeline",
    "GetReconciliation",
    "GetInvoiceStatus",
    "GetPaymentChannel",
    # 通用辅助域
    "ValidateFrontendState",
    "ReconstructTimeline",
}


async def test_batch2_skills_registered():
    """所有 Batch2 Skill 都已在注册表中"""
    registry = SkillRegistry()
    missing = BATCH2_SKILLS - set(registry.keys())
    assert not missing, f"以下 Batch2 Skill 未注册: {missing}"


async def test_batch2_skills_execute():
    """所有 Batch2 Skill 用标准上下文可执行并返回结果"""
    registry = SkillRegistry()
    context = {
        "key_entities": {
            "merchant_id": "3052",
            "order_id": "ORD-8823",
        },
        "scenario": "order_status_anomaly",
    }

    results = {}
    for name in BATCH2_SKILLS:
        result = await registry.execute(name, context=context, reason="batch2 smoke test")
        assert "summary" in result, f"{name} 返回结果缺少 summary: {result}"
        assert "error" not in result, f"{name} 执行报错: {result}"
        results[name] = result

    # 关键字段校验
    assert results["GetMerchantBlacklist"]["blacklist"]["is_blacklisted"] is False
    assert results["GetMerchantCoopStatus"]["coop_status"] == "normal"
    assert results["GetOrderRefund"]["refund"]["status"] == "pending_review"
    assert results["GetBillCalculation"]["bill_calculation"]["actual_amount"] == 3600.00
