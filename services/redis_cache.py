#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Redis 缓存层：RAG / 工具结果等跨会话中间结果缓存。

设计目标（生产化外壳）：
- 有 Redis 时用 Redis（TTL 过期、多实例共享）；
- 无 Redis / 连接失败时静默降级为进程内内存字典，保证 demo 不依赖外部服务。
"""
import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    import redis as _redis
    REDIS_AVAILABLE = True
except ImportError:  # pragma: no cover
    REDIS_AVAILABLE = False
    _redis = None


class RedisCache:
    """可降级的 Redis 缓存封装。"""

    def __init__(
        self,
        enabled: bool = False,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: Optional[str] = None,
        ttl_seconds: int = 300,
        key_prefix: str = "diagbot:cache:",
    ):
        self.enabled = enabled
        self.ttl_seconds = ttl_seconds
        self.key_prefix = key_prefix
        self._client = None
        self._fallback: Dict[str, Any] = {}

        if not enabled:
            return
        if not REDIS_AVAILABLE:
            logger.warning("redis 包未安装，RedisCache 降级为内存缓存")
            return
        try:
            client = _redis.Redis(
                host=host,
                port=port,
                db=db,
                password=password,
                decode_responses=True,
            )
            client.ping()
            self._client = client
            logger.info(f"RedisCache connected to {host}:{port}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Redis 连接失败，降级为内存缓存: {e}")
            self._client = None

    @property
    def available(self) -> bool:
        return self._client is not None

    def _full_key(self, key: str) -> str:
        return f"{self.key_prefix}{key}"

    def get(self, key: str) -> Optional[Any]:
        full_key = self._full_key(key)
        if self._client is not None:
            try:
                raw = self._client.get(full_key)
                return json.loads(raw) if raw is not None else None
            except Exception:  # noqa: BLE001
                return None
        return self._fallback.get(full_key)

    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None):
        full_key = self._full_key(key)
        ttl = ttl_seconds if ttl_seconds is not None else self.ttl_seconds
        if self._client is not None:
            try:
                self._client.setex(
                    full_key,
                    ttl,
                    json.dumps(value, ensure_ascii=False),
                )
                return
            except Exception:  # noqa: BLE001
                pass
        self._fallback[full_key] = value

    def delete(self, key: str):
        full_key = self._full_key(key)
        if self._client is not None:
            try:
                self._client.delete(full_key)
            except Exception:  # noqa: BLE001
                pass
        self._fallback.pop(full_key, None)
