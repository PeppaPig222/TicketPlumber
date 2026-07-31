#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
FastAPI 入口：提供诊断、trace 查询与 SSE 回放接口。
"""
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from services.diagnosis_service import DiagnosisService


app = FastAPI(title="小哈工单智能诊断助手", version="1.0.0")
diagnosis_service = DiagnosisService()
WEB_INDEX = Path(__file__).resolve().parent.parent / "web" / "index.html"


class DiagnoseRequest(BaseModel):
    query: str = Field(..., description="工单描述或工单ID")


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
    return diagnosis_service.get_metrics()


@app.post("/api/v1/diagnose")
async def diagnose(request: DiagnoseRequest):
    return await diagnosis_service.diagnose(request.query)


@app.get("/api/v1/trace/{trace_id}")
async def get_trace(trace_id: str):
    trace = await diagnosis_service.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="trace 不存在")
    return trace


@app.get("/api/v1/trace/stream/{trace_id}")
async def stream_trace(trace_id: str):
    events = await diagnosis_service.get_trace_events(trace_id)
    if events is None:
        raise HTTPException(status_code=404, detail="trace 不存在")

    async def event_generator():
        for item in events:
            yield {
                "event": item["event"],
                "data": json.dumps(item["data"], ensure_ascii=False),
            }

    return EventSourceResponse(event_generator())
