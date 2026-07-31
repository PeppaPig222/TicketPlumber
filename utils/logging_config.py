#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
结构化日志配置 - JSON 格式 + trace_id 链路追踪
"""
import logging
import json
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

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
    """JSON 格式日志输出器"""

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
        # 附加字段
        for key in ("agent_name", "round_num", "ticket_id", "duration_ms"):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)
        return json.dumps(log_entry, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO", log_file: str = None):
    """初始化全局日志配置"""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=getattr(logging, level.upper()), handlers=[handler])
    # 抑制第三方库的噪音
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("pymilvus").setLevel(logging.WARNING)
