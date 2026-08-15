"""
Redis 短期记忆后端
用于多实例共享会话级短期记忆。
"""
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from context.base_memory import BaseShortTermMemory

logger = logging.getLogger(__name__)

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:  # pragma: no cover
    REDIS_AVAILABLE = False
    redis = None  # type: ignore


class RedisShortTermMemory(BaseShortTermMemory):
    """基于 Redis 的短期记忆实现，支持 TTL 与会话隔离。"""

    def __init__(
        self,
        session_id: str,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: Optional[str] = None,
        max_turns: int = 100,
        ttl_seconds: int = 3600,
    ):
        if not REDIS_AVAILABLE:
            raise ImportError(
                "Redis backend requires 'redis' package. "
                "Install with: pip install redis"
            )
        self.session_id = session_id
        self.max_turns = max_turns
        self.ttl_seconds = ttl_seconds
        self._key = f"diagbot:stm:{session_id}"
        self._client = redis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            decode_responses=True,
        )
        logger.info(f"RedisShortTermMemory initialized for session {session_id}")

    def _load_messages(self) -> List[Dict[str, Any]]:
        raw = self._client.get(self._key)
        if not raw:
            return []
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"Failed to decode Redis STM data for {self.session_id}")
            return []

    def _save_messages(self, messages: List[Dict[str, Any]]):
        self._client.setex(
            self._key,
            self.ttl_seconds,
            json.dumps(messages, ensure_ascii=False),
        )

    def add_message(self, role: str, content: str, metadata: Dict = None):
        messages = self._load_messages()
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {},
        }
        messages.append(message)
        max_messages = self.max_turns * 2
        if len(messages) > max_messages:
            messages = messages[-max_messages:]
        self._save_messages(messages)
        logger.debug(f"Added message to Redis STM: {role}")

    def get_recent_context(self, n_turns: int = None) -> List[Dict[str, Any]]:
        messages = self._load_messages()
        if n_turns is None:
            return messages.copy()
        n_messages = n_turns * 2
        return messages[-n_messages:] if len(messages) > n_messages else messages.copy()

    def get_context_string(self, n_turns: int = 5) -> str:
        messages = self.get_recent_context(n_turns)
        if not messages:
            return "无历史对话"
        lines = []
        for msg in messages:
            role_name = "用户" if msg["role"] == "user" else "助手"
            lines.append(f"{role_name}: {msg['content']}")
        return "\n".join(lines)

    def clear(self):
        self._client.delete(self._key)
        logger.info(f"Redis STM cleared for session {self.session_id}")

    def get_statistics(self) -> Dict[str, Any]:
        messages = self._load_messages()
        return {
            "total_messages": len(messages),
            "max_turns": self.max_turns,
            "oldest_message_time": messages[0]["timestamp"] if messages else None,
            "newest_message_time": messages[-1]["timestamp"] if messages else None,
        }
