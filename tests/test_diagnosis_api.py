#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import sys

from fastapi.testclient import TestClient

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from api.app import app


client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in {"ok", "degraded"}
    assert "dependencies" in data
    assert "llm" in data["dependencies"]
    assert "rag" in data["dependencies"]
    assert "memory" in data["dependencies"]


def test_diagnose_and_trace_endpoints():
    diagnosis_response = client.post(
        "/api/v1/diagnose",
        json={"query": "请诊断工单 WO-20260815-0421"},
    )

    assert diagnosis_response.status_code == 200
    payload = diagnosis_response.json()
    trace_id = payload["trace_id"]

    trace_response = client.get(f"/api/v1/trace/{trace_id}")
    assert trace_response.status_code == 200
    assert trace_response.json()["ticket_id"] == "WO-20260815-0421"

    stream_response = client.get(f"/api/v1/trace/stream/{trace_id}")
    assert stream_response.status_code == 200
    assert "agent_update" in stream_response.text


def test_diagnose_accepts_user_and_session():
    response = client.post(
        "/api/v1/diagnose",
        json={
            "query": "请诊断工单 WO-20260815-0421",
            "user_id": "api_test_user",
            "session_id": "sess_001",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["ticket_id"] == "WO-20260815-0421"
