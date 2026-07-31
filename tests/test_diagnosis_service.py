#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import sys

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from services.diagnosis_service import DiagnosisService, TraceRepository


@pytest.mark.asyncio
async def test_order_ticket_diagnosis_runs_three_rounds():
    service = DiagnosisService(trace_repo=TraceRepository())

    result = await service.diagnose("请诊断工单 WO-20260815-0421")

    assert result["status"] == "completed"
    assert result["scenario"] == "order_status_anomaly"
    assert result["diagnosis"]["responsible_party"] == "数据侧（后台脚本）"
    assert "状态同步" in result["diagnosis"]["root_cause"]
    assert result["trace"]["total_rounds"] == 3


@pytest.mark.asyncio
async def test_asset_ticket_diagnosis_finishes_in_two_rounds():
    service = DiagnosisService(trace_repo=TraceRepository())

    result = await service.diagnose("帮我看下工单 WO-20260816-0532 为什么资产分配失败")

    assert result["status"] == "completed"
    assert result["scenario"] == "asset_allocation_failure"
    assert result["diagnosis"]["responsible_party"] == "业务配置与权限"
    assert "三重限制" in result["diagnosis"]["summary"]
    assert result["trace"]["total_rounds"] == 2


@pytest.mark.asyncio
async def test_settlement_ticket_diagnosis_cross_verifies():
    service = DiagnosisService(trace_repo=TraceRepository())

    result = await service.diagnose("请排查工单 WO-20260817-0611 的结算金额不符问题")

    assert result["status"] == "completed"
    assert result["scenario"] == "settlement_amount_mismatch"
    assert result["diagnosis"]["responsible_party"] == "数据侧（标签脚本）"
    assert "标签" in result["diagnosis"]["root_cause"]
    assert result["trace"]["total_rounds"] == 3
