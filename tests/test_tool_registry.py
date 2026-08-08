#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ToolRegistry 降级策略测试。

覆盖：
- 未注册工具返回统一 error 格式
- 工具执行异常返回统一 error 格式
- 工具执行超时返回统一 timeout 格式
- 正常工具仍返回 success
"""
import asyncio
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from utils.tool_registry import ToolRegistry


@pytest.fixture
def registry():
    return ToolRegistry()


@pytest.mark.asyncio
async def test_execute_unregistered_tool_returns_error(registry):
    result = await registry.execute("not_exist_tool", arg="value")
    assert result["status"] == "error"
    assert result["tool"] == "not_exist_tool"
    assert "message" in result
    assert "available" in result.get("data", {})


@pytest.mark.asyncio
async def test_execute_exception_returns_error(registry):
    async def boom(**kwargs):
        raise RuntimeError("模拟工具异常")

    registry.register("boom_tool", "会爆炸的工具", {}, boom)
    result = await registry.execute("boom_tool")

    assert result["status"] == "error"
    assert result["tool"] == "boom_tool"
    assert "模拟工具异常" in result.get("message", "")


@pytest.mark.asyncio
async def test_execute_timeout_returns_timeout(registry):
    async def slow(**kwargs):
        await asyncio.sleep(10)
        return {"status": "success", "data": {}}

    registry.register("slow_tool", "慢工具", {}, slow)
    result = await registry.execute("slow_tool")

    assert result["status"] == "timeout"
    assert result["tool"] == "slow_tool"
    assert "超时" in result.get("message", "")


@pytest.mark.asyncio
async def test_execute_success_unchanged(registry):
    async def ok(**kwargs):
        return {"status": "success", "data": {"value": 42}}

    registry.register("ok_tool", "正常工具", {}, ok)
    result = await registry.execute("ok_tool")

    assert result["status"] == "success"
    assert result.get("data", {}).get("value") == 42


@pytest.mark.asyncio
async def test_legacy_error_dict_normalized(registry):
    """兼容旧 handler 直接返回 {"error": ...} 的格式。"""

    async def legacy(**kwargs):
        return {"error": "旧格式错误"}

    registry.register("legacy_tool", "旧格式工具", {}, legacy)
    result = await registry.execute("legacy_tool")

    assert result["status"] == "error"
    assert result["tool"] == "legacy_tool"
    assert "旧格式错误" in result.get("message", "")
