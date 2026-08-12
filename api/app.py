#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
FastAPI 入口：提供诊断、trace 查询与 SSE 回放接口。
"""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from config import LLM_CONFIG, SYSTEM_CONFIG
from services.diagnosis_service import DiagnosisService, TraceRepository
from utils.errors import AppError, ErrorCode
from utils.logging_config import set_trace_id, setup_logging

# 全局启用结构化日志
setup_logging(level=SYSTEM_CONFIG["log_level"])

app = FastAPI(title="小哈工单智能诊断助手", version="1.0.0")


@app.middleware("http")
async def trace_id_middleware(request, call_next):
    """为每个请求注入 trace_id，并返回响应头"""
    trace_id = request.headers.get("x-trace-id") or str(uuid.uuid4())[:8]
    set_trace_id(trace_id)
    response = await call_next(request)
    response.headers["x-trace-id"] = trace_id
    return response


# 全局共享 trace 存储，让 /diagnose 写入的 trace 能被 /trace 查询到
trace_repo = TraceRepository()
# 默认 service 用于 /metrics，避免 per-request 实例统计失真
default_diagnosis_service = DiagnosisService(trace_repo=trace_repo)
WEB_INDEX = Path(__file__).resolve().parent.parent / "web" / "index.html"
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

# 开发时允许前端独立服务跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 如果已构建 React 面板，则挂载到 /panel
if FRONTEND_DIST.exists():
    app.mount("/panel", StaticFiles(directory=str(FRONTEND_DIST), html=True))


class DiagnoseRequest(BaseModel):
    query: str = Field(..., description="工单描述或工单ID")
    user_id: Optional[str] = Field(None, description="用户ID")
    session_id: Optional[str] = Field(None, description="会话ID")


class ErrorResponse(BaseModel):
    """统一错误响应结构"""

    error_code: str = Field(..., description="错误码")
    error_type: str = Field(..., description="错误类型")
    message: str = Field(..., description="错误信息")


def _make_error_body(
    code: ErrorCode, detail: Optional[str] = None, trace_id: Optional[str] = None
) -> Dict:
    message = detail or code.value[1]
    body = {
        "error": {
            "error_code": code.value[0],
            "error_type": code.name,
            "message": message,
        }
    }
    if trace_id:
        body["trace_id"] = trace_id
    return body


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    trace_id = request.headers.get("x-trace-id") or "unknown"
    return JSONResponse(
        status_code=500,
        content=_make_error_body(exc.code, exc.detail, trace_id),
        headers={"x-error-code": exc.code.value[0]},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    trace_id = request.headers.get("x-trace-id") or "unknown"
    detail = str(exc.errors())
    return JSONResponse(
        status_code=422,
        content=_make_error_body(
            ErrorCode.INVALID_INTENTION, detail=detail, trace_id=trace_id
        ),
        headers={"x-error-code": ErrorCode.INVALID_INTENTION.value[0]},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    trace_id = request.headers.get("x-trace-id") or "unknown"
    return JSONResponse(
        status_code=500,
        content=_make_error_body(
            ErrorCode.INTERNAL_ERROR, detail=str(exc), trace_id=trace_id
        ),
        headers={"x-error-code": ErrorCode.INTERNAL_ERROR.value[0]},
    )


@app.get("/")
async def index():
    if WEB_INDEX.exists():
        return FileResponse(WEB_INDEX)
    raise HTTPException(status_code=404, detail="前端页面不存在")


def _check_llm_config() -> Dict[str, str]:
    """检查 LLM 配置是否有效（不真正调用 API）"""
    api_key = LLM_CONFIG.get("api_key", "")
    if not api_key or api_key in ("API_KEY", "your_api_key_here", ""):
        return {"status": "unconfigured", "message": "LLM API Key 未配置"}
    return {"status": "ok", "message": "LLM 配置已就绪"}


def _check_rag_storage() -> Dict[str, str]:
    """检查 RAG 知识库存储是否可访问"""
    rag_db = Path("data/rag_knowledge/milvus_lite.db")
    if rag_db.exists():
        return {"status": "ok", "message": f"RAG 知识库已就绪 ({rag_db})"}
    return {"status": "not_initialized", "message": "RAG 知识库未初始化，可运行 scripts/init_diagnosis_kb.py"}


def _check_memory_storage() -> Dict[str, str]:
    """检查记忆存储目录是否可写"""
    memory_dir = Path("data/memory")
    try:
        memory_dir.mkdir(parents=True, exist_ok=True)
        test_file = memory_dir / ".write_test"
        test_file.write_text("ok")
        test_file.unlink()
        return {"status": "ok", "message": f"记忆存储目录可写 ({memory_dir})"}
    except Exception as e:
        return {"status": "error", "message": f"记忆存储目录不可写: {e}"}


@app.get("/health")
async def health():
    dependencies = {
        "llm": _check_llm_config(),
        "rag": _check_rag_storage(),
        "memory": _check_memory_storage(),
    }
    overall_status = "ok" if all(d["status"] == "ok" for d in dependencies.values()) else "degraded"

    return {
        "status": overall_status,
        "service": "xiaoha-ticket-diagnosis",
        "version": "1.0.0",
        "env": SYSTEM_CONFIG.get("env", "development"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dependencies": dependencies,
    }


@app.get("/metrics")
async def metrics():
    metrics_data = default_diagnosis_service.get_metrics()
    # trace_count 以全局 trace_repo 为准
    metrics_data["trace_count"] = trace_repo.count()
    metrics_data["timestamp"] = datetime.now(timezone.utc).isoformat()
    return metrics_data


@app.post("/api/v1/diagnose")
async def diagnose(
    request: DiagnoseRequest,
    x_user_id: Optional[str] = Header(None),
    x_session_id: Optional[str] = Header(None),
):
    user_id = request.user_id or x_user_id or f"api_{uuid.uuid4().hex[:8]}"
    session_id = request.session_id or x_session_id or f"sess_{uuid.uuid4().hex[:8]}"
    # 每次请求创建新的 service，避免多请求共享 MemoryManager 状态
    service = DiagnosisService(trace_repo=trace_repo, user_id=user_id)
    return await service.diagnose(request.query, session_id=session_id)


@app.get("/api/v1/trace/{trace_id}")
async def get_trace(trace_id: str):
    payload = trace_repo.get(trace_id)
    if not payload:
        raise HTTPException(status_code=404, detail="trace 不存在")
    return payload.get("trace")


@app.get("/api/v1/trace/stream/{trace_id}")
async def stream_trace(trace_id: str):
    payload = trace_repo.get(trace_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="trace 不存在")
    events = payload.get("events", [])

    async def event_generator():
        for item in events:
            yield {
                "event": item["event"],
                "data": json.dumps(item["data"], ensure_ascii=False),
            }

    return EventSourceResponse(event_generator())
