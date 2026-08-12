#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from utils.errors import (
    AppError,
    ErrorCode,
    ExecutionStatus,
    ToolStatus,
    map_exception_to_error_code,
)


def test_error_code_values():
    assert ErrorCode.INTENT_RECOGNITION_FAILED.value[0] == "A001"
    assert ErrorCode.ORCHESTRATION_FAILED.value[1] == "编排执行失败"
    assert ErrorCode.TOOL_NOT_FOUND.value[0] == "E005"


def test_app_error_to_dict():
    err = AppError(ErrorCode.TOOL_TIMEOUT, detail="查询超时")
    data = err.to_dict()
    assert data["error_code"] == "E006"
    assert data["error_type"] == "TOOL_TIMEOUT"
    assert data["message"] == "查询超时"


def test_app_error_default_detail():
    err = AppError(ErrorCode.CONFIG_ERROR)
    assert err.detail == "配置错误"
    assert str(err) == "[S002] 配置错误"


def test_tool_status_is_string_compatible():
    assert ToolStatus.SUCCESS == "success"
    assert ToolStatus.ERROR.value == "error"
    assert ToolStatus.TIMEOUT.value == "timeout"


def test_execution_status_values():
    assert ExecutionStatus.SUCCESS.value == "success"
    assert ExecutionStatus.SKIPPED.value == "skipped"
    assert ExecutionStatus.PARTIAL_FAILURE.value == "partial_failure"


def test_map_exception_to_error_code():
    assert map_exception_to_error_code(AppError(ErrorCode.LLM_TIMEOUT)) == ErrorCode.LLM_TIMEOUT
    assert map_exception_to_error_code(TimeoutError()) == ErrorCode.TOOL_TIMEOUT
    assert map_exception_to_error_code(ConnectionError()) == ErrorCode.NETWORK_ERROR
    assert map_exception_to_error_code(ValueError("boom")) == ErrorCode.INTERNAL_ERROR
