#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
结构化日志配置 - JSON 格式 + trace_id 链路追踪

支持：
- 按环境切换 JSON / 可阅读格式
- trace_id 上下文透传
- 文件日志输出
- 附加业务字段（agent_name, round_num, ticket_id, duration_ms）
"""
import logging
import json
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Optional

# trace_id 上下文变量
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")


def set_trace_id(trace_id: str = None):
    """设置当前请求的 trace_id"""
    trace_id_var.set(trace_id or str(uuid.uuid4())[:8])


def get_trace_id() -> str:
    """获取当前 trace_id"""
    tid = trace_id_var.get()
    return tid or "unknown"


class JsonFormatter(logging.Formatter):
    """JSON 格式日志输出器（生产环境推荐）"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "trace_id": get_trace_id(),
            "module": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["error"] = str(record.exc_info[1])
        # 附加业务字段
        for key in ("agent_name", "round_num", "ticket_id", "duration_ms", "skill_name", "tool_name", "status"):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)
        return json.dumps(log_entry, ensure_ascii=False, default=str)


class ReadableFormatter(logging.Formatter):
    """可阅读格式日志输出器（开发环境推荐）"""

    def format(self, record: logging.LogRecord) -> str:
        trace_id = get_trace_id()
        trace_part = f"[{trace_id}] " if trace_id and trace_id != "unknown" else ""

        # 附加业务字段
        extras = []
        for key in ("agent_name", "round_num", "ticket_id", "duration_ms", "skill_name", "tool_name", "status"):
            if hasattr(record, key):
                extras.append(f"{key}={getattr(record, key)}")
        extra_part = f" ({', '.join(extras)})" if extras else ""

        return f"{record.levelname:8} {trace_part}{record.name} - {record.getMessage()}{extra_part}"


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    json_format: Optional[bool] = None,
):
    """
    初始化全局日志配置

    Args:
        level: 日志级别
        log_file: 日志文件路径，None 表示只输出到控制台
        json_format: 是否使用 JSON 格式。None 时根据 DIAG_SYS_ENV 环境变量自动判断
    """
    # 自动判断格式：生产环境默认 JSON，开发/测试环境默认可阅读
    if json_format is None:
        env = "development"
        try:
            from config.settings import settings
            env = settings.system.env.lower()
        except Exception:
            env = "development"
        json_format = env == "production"

    formatter = JsonFormatter() if json_format else ReadableFormatter()

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    handlers = [stream_handler]
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    # 避免重复配置：先清空现有 root handler
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=handlers,
        force=True,
    )

    # 抑制第三方库的噪音
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("pymilvus").setLevel(logging.WARNING)
    logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
