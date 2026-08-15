"""
MemoryBackendFactory
根据配置创建不同存储后端的 MemoryManager，默认 local backend 保持 demo 可运行。
"""
from typing import Dict, Any, Optional

from context.memory_manager import MemoryManager
from context.base_memory import (
    BaseShortTermMemory,
    BaseLongTermMemory,
    BaseMerchantProfileStore,
)
from context.short_term_memory import InMemoryShortTermMemory
from context.long_term_memory import FileLongTermMemory
from context.merchant_profile_store import FileMerchantProfileStore


class MemoryBackendFactory:
    """记忆存储后端工厂：按配置创建 MemoryManager 及其依赖组件。"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    def create_memory_manager(
        self,
        user_id: str,
        session_id: str,
        merchant_id: str = None,
        llm_model=None,
        rag_agent=None,
        milvus_client=None,
        embedding_model=None,
        **kwargs,
    ) -> MemoryManager:
        """根据配置创建 MemoryManager。"""
        backend = self.config.get("backend", "local")

        if backend == "local":
            return self._create_local(
                user_id=user_id,
                session_id=session_id,
                merchant_id=merchant_id,
                llm_model=llm_model,
                rag_agent=rag_agent,
                milvus_client=milvus_client,
                embedding_model=embedding_model,
                **kwargs,
            )

        if backend == "redis":
            return self._create_redis(
                user_id=user_id,
                session_id=session_id,
                merchant_id=merchant_id,
                llm_model=llm_model,
                rag_agent=rag_agent,
                milvus_client=milvus_client,
                embedding_model=embedding_model,
                **kwargs,
            )

        raise ValueError(f"Unknown memory backend: {backend}")

    def _create_local(self, **kwargs) -> MemoryManager:
        """local backend：内存 + JSON 文件，demo/开发默认。"""
        user_id = kwargs["user_id"]
        session_id = kwargs["session_id"]
        merchant_id = kwargs.get("merchant_id")
        storage_path = self.config.get("storage_path", "data/memory")

        short_term = InMemoryShortTermMemory(
            max_turns=self.config.get("short_term", {}).get("max_turns", 100)
        )
        long_term = FileLongTermMemory(user_id, storage_path)
        merchant_profile = (
            FileMerchantProfileStore(merchant_id, storage_path)
            if merchant_id else None
        )

        return MemoryManager(
            user_id=user_id,
            session_id=session_id,
            storage_path=storage_path,
            llm_model=kwargs.get("llm_model"),
            merchant_id=merchant_id,
            milvus_client=kwargs.get("milvus_client"),
            embedding_model=kwargs.get("embedding_model"),
            rag_agent=kwargs.get("rag_agent"),
            short_term_memory=short_term,
            long_term_memory=long_term,
            merchant_profile_store=merchant_profile,
        )

    def _create_redis(self, **kwargs) -> MemoryManager:
        """
        redis backend：短期记忆使用 Redis，长期记忆与商户画像可逐步迁移。
        需要安装 redis 包并配置 REDIS_CONFIG。
        """
        from context.redis_backend import RedisShortTermMemory

        user_id = kwargs["user_id"]
        session_id = kwargs["session_id"]
        merchant_id = kwargs.get("merchant_id")
        storage_path = self.config.get("storage_path", "data/memory")

        redis_config = self.config.get("redis", {})
        ttl_seconds = self.config.get("short_term", {}).get("ttl_seconds", 3600)

        short_term = RedisShortTermMemory(
            session_id=session_id,
            ttl_seconds=ttl_seconds,
            **redis_config,
        )
        # 长期记忆与商户画像默认仍用 File，后续可替换为 Postgres/Mongo
        long_term = FileLongTermMemory(user_id, storage_path)
        merchant_profile = (
            FileMerchantProfileStore(merchant_id, storage_path)
            if merchant_id else None
        )

        return MemoryManager(
            user_id=user_id,
            session_id=session_id,
            storage_path=storage_path,
            llm_model=kwargs.get("llm_model"),
            merchant_id=merchant_id,
            milvus_client=kwargs.get("milvus_client"),
            embedding_model=kwargs.get("embedding_model"),
            rag_agent=kwargs.get("rag_agent"),
            short_term_memory=short_term,
            long_term_memory=long_term,
            merchant_profile_store=merchant_profile,
        )


def create_memory_manager(
    user_id: str,
    session_id: str,
    config: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> MemoryManager:
    """便捷函数：按配置创建 MemoryManager。"""
    factory = MemoryBackendFactory(config)
    return factory.create_memory_manager(
        user_id=user_id,
        session_id=session_id,
        **kwargs,
    )
