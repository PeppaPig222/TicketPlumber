#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
持久化存储层：trace、诊断结果与用户反馈的统一落库。

设计目标（生产化外壳）：
- 默认 SQLite（标准库 sqlite3，零外部依赖），demo/单机可直接跑；
- 接口与 TraceRepository 兼容，PostgreSQL 等替换时无需改业务代码；
- 反馈闭环的落点：feedback 表作为「线上反馈 → 评测集」的中间载体。
"""
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class TraceRepository:
    """内存版 trace 存储（基类/降级实现），接口：save / get / count。"""

    def __init__(self):
        self._data: Dict[str, Dict[str, Any]] = {}

    def save(self, trace_id: str, payload: Dict[str, Any]):
        self._data[trace_id] = payload

    def get(self, trace_id: str) -> Optional[Dict[str, Any]]:
        return self._data.get(trace_id)

    def count(self) -> int:
        return len(self._data)


class SQLiteStore:
    """SQLite 持久化：trace + feedback 两张表，线程安全、按操作开连接。"""

    def __init__(self, db_path: str = "data/diagbot.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._ensure_tables()

    def _ensure_tables(self):
        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS traces (
                    trace_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL,
                    correct INTEGER NOT NULL,
                    expected_responsible_party TEXT,
                    expected_root_cause TEXT,
                    comment TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_trace ON feedback(trace_id)"
            )
            conn.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ---- trace ----
    def save_trace(self, trace_id: str, payload: Dict[str, Any]):
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO traces (trace_id, payload, created_at) "
                    "VALUES (?, ?, ?)",
                    (trace_id, json.dumps(payload, ensure_ascii=False), self._now()),
                )
                conn.commit()

    def get_trace(self, trace_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT payload FROM traces WHERE trace_id = ?", (trace_id,)
                ).fetchone()
        if row is None:
            return None
        return json.loads(row["payload"])

    def count_traces(self) -> int:
        with self._lock:
            with self._conn() as conn:
                row = conn.execute("SELECT COUNT(*) AS c FROM traces").fetchone()
        return row["c"]

    # ---- feedback ----
    def save_feedback(self, feedback: Dict[str, Any]):
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO feedback (
                        trace_id, correct, expected_responsible_party,
                        expected_root_cause, comment, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        feedback.get("trace_id", ""),
                        int(bool(feedback.get("correct", False))),
                        feedback.get("expected_responsible_party"),
                        feedback.get("expected_root_cause"),
                        feedback.get("comment"),
                        feedback.get("created_at") or self._now(),
                    ),
                )
                conn.commit()

    def list_feedback(self, limit: int = 100, correct_only: Optional[bool] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM feedback"
        where = []
        params: List[Any] = []
        if correct_only is not None:
            where.append("correct = ?")
            params.append(int(correct_only))
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            with self._conn() as conn:
                rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def count_feedback(self) -> int:
        with self._lock:
            with self._conn() as conn:
                row = conn.execute("SELECT COUNT(*) AS c FROM feedback").fetchone()
        return row["c"]

    def feedback_accuracy(self) -> Optional[float]:
        with self._lock:
            with self._conn() as conn:
                total = conn.execute("SELECT COUNT(*) AS c FROM feedback").fetchone()["c"]
                correct = conn.execute(
                    "SELECT COUNT(*) AS c FROM feedback WHERE correct = 1"
                ).fetchone()["c"]
        if total == 0:
            return None
        return correct / total


class SQLiteTraceRepository(TraceRepository):
    """TraceRepository 的 SQLite 实现，兼容 save / get / count 接口。"""

    def __init__(self, store: SQLiteStore):
        super().__init__()
        self.store = store

    def save(self, trace_id: str, payload: Dict[str, Any]):
        self.store.save_trace(trace_id, payload)

    def get(self, trace_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get_trace(trace_id)

    def count(self) -> int:
        return self.store.count_traces()


class FeedbackStore:
    """反馈存储：记录用户对诊断结论的修正，作为反馈闭环的数据源。"""

    def __init__(self, store: SQLiteStore):
        self.store = store

    def save(self, feedback: Dict[str, Any]) -> Dict[str, Any]:
        self.store.save_feedback(feedback)
        return {"status": "ok"}

    def list(self, limit: int = 100, correct_only: Optional[bool] = None) -> List[Dict[str, Any]]:
        return self.store.list_feedback(limit=limit, correct_only=correct_only)

    def count(self) -> int:
        return self.store.count_feedback()

    def accuracy(self) -> Optional[float]:
        return self.store.feedback_accuracy()
