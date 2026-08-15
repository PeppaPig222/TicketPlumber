"""
PostgreSQL 长期记忆后端
用于多实例共享用户级长期记忆。
"""
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from context.base_memory import BaseLongTermMemory

logger = logging.getLogger(__name__)

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    POSTGRES_AVAILABLE = True
except ImportError:  # pragma: no cover
    POSTGRES_AVAILABLE = False
    psycopg2 = None  # type: ignore


class PostgresLongTermMemory(BaseLongTermMemory):
    """基于 PostgreSQL 的长期记忆实现。"""

    def __init__(
        self,
        user_id: str,
        dsn: str = "dbname=diagbot user=postgres password=postgres host=localhost port=5432",
    ):
        if not POSTGRES_AVAILABLE:
            raise ImportError(
                "PostgreSQL backend requires 'psycopg2' package. "
                "Install with: pip install psycopg2-binary"
            )
        self.user_id = user_id
        self.dsn = dsn
        self._ensure_tables()
        logger.info(f"PostgresLongTermMemory initialized for user {user_id}")

    def _conn(self):
        return psycopg2.connect(self.dsn)

    def _ensure_tables(self):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_preferences (
                        user_id TEXT NOT NULL,
                        pref_type TEXT NOT NULL,
                        value JSONB,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, pref_type)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chat_history (
                        id SERIAL PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT,
                        session_id TEXT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_chat_history_user_session
                    ON chat_history(user_id, session_id)
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS diagnosis_history (
                        id SERIAL PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        diagnosis_info JSONB,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                conn.commit()

    def save_preference(self, pref_type: str, value: Any):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_preferences (user_id, pref_type, value, updated_at)
                    VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (user_id, pref_type)
                    DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
                    """,
                    (self.user_id, pref_type, json.dumps(value, ensure_ascii=False)),
                )
                conn.commit()
        logger.info(f"Saved preference for user {self.user_id}: {pref_type}")

    def get_preference(self, pref_type: str = None) -> Any:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if pref_type is None:
                    cur.execute(
                        "SELECT pref_type, value FROM user_preferences WHERE user_id = %s",
                        (self.user_id,),
                    )
                    rows = cur.fetchall()
                    return {row["pref_type"]: json.loads(row["value"]) for row in rows}
                else:
                    cur.execute(
                        "SELECT value FROM user_preferences WHERE user_id = %s AND pref_type = %s",
                        (self.user_id, pref_type),
                    )
                    row = cur.fetchone()
                    return json.loads(row["value"]) if row else None

    def add_chat_message(self, role: str, content: str, session_id: str = None):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chat_history (user_id, role, content, session_id, timestamp)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                    """,
                    (self.user_id, role, content, session_id),
                )
                conn.commit()
        logger.debug(f"Added chat message for user {self.user_id}: {role}")

    def get_chat_history(self, limit: int = None, session_id: str = None) -> List[Dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                sql = "SELECT role, content, session_id, timestamp FROM chat_history WHERE user_id = %s"
                params = [self.user_id]
                if session_id:
                    sql += " AND session_id = %s"
                    params.append(session_id)
                sql += " ORDER BY timestamp"
                if limit:
                    sql += " LIMIT %s"
                    params.append(limit)
                cur.execute(sql, params)
                rows = cur.fetchall()
                return [dict(row) for row in rows]

    def save_diagnosis_history(self, diagnosis_info: Dict[str, Any]):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO diagnosis_history (user_id, diagnosis_info, timestamp)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    """,
                    (self.user_id, json.dumps(diagnosis_info, ensure_ascii=False)),
                )
                conn.commit()
        logger.info(f"Saved diagnosis history for user {self.user_id}")

    def get_diagnosis_history(self, limit: int = 10) -> List[Dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT diagnosis_info, timestamp FROM diagnosis_history
                    WHERE user_id = %s ORDER BY timestamp DESC LIMIT %s
                    """,
                    (self.user_id, limit),
                )
                rows = cur.fetchall()
                return [dict(row["diagnosis_info"], timestamp=row["timestamp"]) for row in rows]

    def get_common_issue_types(self, top_n: int = 5) -> List[tuple]:
        history = self.get_diagnosis_history(limit=1000)
        stats: Dict[str, int] = {}
        for item in history:
            issue_type = item.get("issue_type")
            if issue_type:
                stats[issue_type] = stats.get(issue_type, 0) + 1
        sorted_items = sorted(stats.items(), key=lambda x: x[1], reverse=True)
        return sorted_items[:top_n]

    def get_statistics(self) -> Dict[str, Any]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM chat_history WHERE user_id = %s",
                    (self.user_id,),
                )
                total_messages = cur.fetchone()[0]
                cur.execute(
                    "SELECT COUNT(*) FROM diagnosis_history WHERE user_id = %s",
                    (self.user_id,),
                )
                total_diagnoses = cur.fetchone()[0]
                return {
                    "total_messages": total_messages,
                    "total_diagnoses": total_diagnoses,
                    "common_issue_types": dict(self.get_common_issue_types()),
                }

    def clear_history(self):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM chat_history WHERE user_id = %s",
                    (self.user_id,),
                )
                cur.execute(
                    "DELETE FROM diagnosis_history WHERE user_id = %s",
                    (self.user_id,),
                )
                conn.commit()
        logger.info(f"Cleared history for user {self.user_id}")
