#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证三层记忆系统接入诊断主链路后的写入行为。"""
import json
import os
import sys

import pytest

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from services.diagnosis_service import DiagnosisService, TraceRepository


@pytest.mark.asyncio
async def test_diagnosis_service_records_long_term_memory(tmp_path):
    """诊断完成后，长期记忆文件中应包含本次诊断记录。"""
    storage_path = str(tmp_path / "memory")
    service = DiagnosisService(
        trace_repo=TraceRepository(),
        user_id="mem_test_user",
        storage_path=storage_path,
    )

    result = await service.diagnose("请诊断工单 WO-20260815-0421")

    assert result["status"] == "completed"
    assert result["ticket_id"] == "WO-20260815-0421"

    ltm_path = tmp_path / "memory" / "mem_test_user.json"
    assert ltm_path.exists(), f"长期记忆文件未生成: {ltm_path}"

    data = json.loads(ltm_path.read_text(encoding="utf-8"))
    history = data.get("diagnosis_history", [])
    assert len(history) >= 1
    assert any(item.get("ticket_id") == "WO-20260815-0421" for item in history)


@pytest.mark.asyncio
async def test_diagnosis_service_creates_merchant_profile(tmp_path):
    """诊断完成后，应生成对应商户画像文件。"""
    storage_path = str(tmp_path / "memory")
    service = DiagnosisService(
        trace_repo=TraceRepository(),
        user_id="mem_test_user",
        storage_path=storage_path,
    )

    await service.diagnose("请诊断工单 WO-20260815-0421")

    # 该工单对应商户号 2037（由 mock 数据决定）
    profile_path = tmp_path / "memory" / "merchant_2037.json"
    assert profile_path.exists(), f"商户画像文件未生成: {profile_path}"

    profile_data = json.loads(profile_path.read_text(encoding="utf-8"))
    assert profile_data.get("merchant_id") == "2037"
    assert profile_data.get("diagnosis_count", 0) >= 1


@pytest.mark.asyncio
async def test_diagnosis_service_records_chat_history(tmp_path):
    """诊断完成后，长期记忆的 chat_history 中应包含用户提问和助手回复。"""
    storage_path = str(tmp_path / "memory")
    service = DiagnosisService(
        trace_repo=TraceRepository(),
        user_id="mem_test_user",
        storage_path=storage_path,
    )

    await service.diagnose("请诊断工单 WO-20260815-0421", session_id="sess_001")

    ltm_path = tmp_path / "memory" / "mem_test_user.json"
    data = json.loads(ltm_path.read_text(encoding="utf-8"))
    chat_history = data.get("chat_history", [])

    user_messages = [m for m in chat_history if m.get("role") == "user"]
    assistant_messages = [m for m in chat_history if m.get("role") == "assistant"]

    assert any("WO-20260815-0421" in m.get("content", "") for m in user_messages)
    assert len(assistant_messages) >= 1


@pytest.mark.asyncio
async def test_diagnosis_service_session_isolation(tmp_path):
    """同一用户不同 session_id 不应互相覆盖短期记忆上下文。"""
    storage_path = str(tmp_path / "memory")
    service = DiagnosisService(
        trace_repo=TraceRepository(),
        user_id="session_user",
        storage_path=storage_path,
    )

    await service.diagnose("请诊断工单 WO-20260815-0421", session_id="sess_A")
    await service.diagnose("请诊断工单 WO-20260816-0532", session_id="sess_B")

    ltm_path = tmp_path / "memory" / "session_user.json"
    data = json.loads(ltm_path.read_text(encoding="utf-8"))
    history = data.get("diagnosis_history", [])

    tickets = {item.get("ticket_id") for item in history}
    assert "WO-20260815-0421" in tickets
    assert "WO-20260816-0532" in tickets
