#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
FastAPI 入口：提供诊断、trace 查询与 SSE 回放接口。
"""
import json
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from services.diagnosis_service import DiagnosisService, TraceRepository


app = FastAPI(title="小哈工单智能诊断助手", version="1.0.0")
# 全局共享 trace 存储，让 /diagnose 写入的 trace 能被 /trace 查询到
trace_repo = TraceRepository()
# 默认 service 用于 /metrics，避免 per-request 实例统计失真
default_diagnosis_service = DiagnosisService(trace_repo=trace_repo)
WEB_INDEX = Path(__file__).resolve().parent.parent / "web" / "index.html"


class DiagnoseRequest(BaseModel):
    query: str = Field(..., description="工单描述或工单ID")
    user_id: Optional[str] = Field(None, description="用户ID")
    session_id: Optional[str] = Field(None, description="会话ID")


@app.get("/")
async def index():
    if WEB_INDEX.exists():
        return FileResponse(WEB_INDEX)
    raise HTTPException(status_code=404, detail="前端页面不存在")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "xiaoha-ticket-diagnosis"}


@app.get("/metrics")
async def metrics():
    metrics_data = default_diagnosis_service.get_metrics()
    # trace_count 以全局 trace_repo 为准
    metrics_data["trace_count"] = trace_repo.count()
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
