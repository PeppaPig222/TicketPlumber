#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
统一错误码体系与状态枚举
"""
from enum import Enum
from typing import Any, Dict, Optional


class ErrorCode(Enum):
    """错误码枚举"""

    # Agent 层
    INTENT_RECOGNITION_FAILED = ("A001", "意图识别失败")
    AGENT_EXECUTION_FAILED = ("A002", "子Agent执行失败")
    AGENT_NOT_FOUND = ("A003", "子Agent未注册")
    LOOP_MAX_ROUNDS = ("A004", "诊断已达最大轮次")
    ORCHESTRATION_FAILED = ("A005", "编排执行失败")
    INVALID_INTENTION = ("A006", "意图结果格式非法")

    # RAG 层
    RAG_SEARCH_FAILED = ("R001", "知识库检索失败")
    RAG_NO_KNOWLEDGE = ("R002", "知识库中无相关信息")
    RAG_NOT_AVAILABLE = ("R003", "知识库未初始化或不可用")

    # 外部依赖
    LLM_TIMEOUT = ("E001", "LLM调用超时")
    LLM_RATE_LIMITED = ("E002", "LLM调用限流")
    NETWORK_ERROR = ("E003", "网络请求失败")
    TOOL_EXECUTION_FAILED = ("E004", "工具调用失败")
    TOOL_NOT_FOUND = ("E005", "工具未注册")
    TOOL_TIMEOUT = ("E006", "工具执行超时")

    # 系统
    CIRCUIT_OPEN = ("S001", "熔断器已打开，拒绝请求")
    CONFIG_ERROR = ("S002", "配置错误")
    INTERNAL_ERROR = ("S003", "系统内部错误")
    MISSING_REQUIRED_INFO = ("S004", "缺少必要诊断信息")
    TICKET_NOT_FOUND = ("S005", "工单不存在")


class AppError(Exception):
    """应用级异常"""

    def __init__(self, code: ErrorCode, detail: str = None, **kwargs):
        self.code = code
        self.detail = detail or code.value[1]
        self.extra = kwargs
        super().__init__(f"[{code.value[0]}] {self.detail}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_code": self.code.value[0],
            "error_type": self.code.name,
            "message": self.detail,
            **self.extra,
        }


class ToolStatus(str, Enum):
    """工具执行状态枚举

    继承 str 以兼容现有按字符串判断 status 的代码。
    """

    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    NOT_FOUND = "not_found"
    NO_DATA = "no_data"
    NO_MATCH = "no_match"
    DEGRADED = "degraded"


class ExecutionStatus(str, Enum):
    """Agent / 编排执行状态枚举"""

    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    DEGRADED = "degraded"
    PARTIAL_FAILURE = "partial_failure"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"


def map_exception_to_error_code(exc: Exception) -> ErrorCode:
    """根据异常类型映射为 ErrorCode"""
    if isinstance(exc, AppError):
        return exc.code
    if isinstance(exc, TimeoutError):
        return ErrorCode.TOOL_TIMEOUT
    if isinstance(exc, ConnectionError):
        return ErrorCode.NETWORK_ERROR
    return ErrorCode.INTERNAL_ERROR
