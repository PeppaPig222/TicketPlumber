#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
统一错误码体系
"""
from enum import Enum


class ErrorCode(Enum):
    """错误码枚举"""

    # Agent 层
    INTENT_RECOGNITION_FAILED = ("A001", "意图识别失败")
    AGENT_EXECUTION_FAILED = ("A002", "子Agent执行失败")
    AGENT_NOT_FOUND = ("A003", "子Agent未注册")
    LOOP_MAX_ROUNDS = ("A004", "诊断已达最大轮次")

    # RAG 层
    RAG_SEARCH_FAILED = ("R001", "知识库检索失败")
    RAG_NO_KNOWLEDGE = ("R002", "知识库中无相关信息")

    # 外部依赖
    LLM_TIMEOUT = ("E001", "LLM调用超时")
    LLM_RATE_LIMITED = ("E002", "LLM调用限流")
    NETWORK_ERROR = ("E003", "网络请求失败")
    TOOL_EXECUTION_FAILED = ("E004", "工具调用失败")

    # 系统
    CIRCUIT_OPEN = ("S001", "熔断器已打开，拒绝请求")
    CONFIG_ERROR = ("S002", "配置错误")


class AppError(Exception):
    """应用级异常"""

    def __init__(self, code: ErrorCode, detail: str = None, **kwargs):
        self.code = code
        self.detail = detail or code.value[1]
        self.extra = kwargs
        super().__init__(f"[{code.value[0]}] {self.detail}")

    def to_dict(self) -> dict:
        return {
            "error_code": self.code.value[0],
            "error_type": self.code.name,
            "message": self.detail,
            **self.extra,
        }
