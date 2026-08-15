#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
记忆存储后端测试
验证抽象基类、local backend、工厂函数、依赖注入、企业级 backend 导入提示。
"""
import pytest

from context.memory_manager import MemoryManager
from context.factory import MemoryBackendFactory, create_memory_manager
from context.base_memory import (
    BaseShortTermMemory,
    BaseLongTermMemory,
    BaseMerchantProfileStore,
)
from context.short_term_memory import InMemoryShortTermMemory, ShortTermMemory
from context.long_term_memory import FileLongTermMemory, LongTermMemory
from context.merchant_profile_store import FileMerchantProfileStore, MerchantProfileStore


class _FakeShortTermMemory(BaseShortTermMemory):
    def __init__(self):
        self.messages = []

    def add_message(self, role: str, content: str, metadata: dict = None):
        self.messages.append({"role": role, "content": content})

    def get_recent_context(self, n_turns: int = None):
        return self.messages

    def get_context_string(self, n_turns: int = 5):
        return "fake"

    def clear(self):
        self.messages = []

    def get_statistics(self):
        return {"total_messages": len(self.messages)}


class _FakeLongTermMemory(BaseLongTermMemory):
    def __init__(self):
        self.prefs = {}

    def save_preference(self, pref_type: str, value):
        self.prefs[pref_type] = value

    def get_preference(self, pref_type: str = None):
        return self.prefs if pref_type is None else self.prefs.get(pref_type)

    def add_chat_message(self, role: str, content: str, session_id: str = None):
        pass

    def get_chat_history(self, limit: int = None, session_id: str = None):
        return []

    def save_diagnosis_history(self, diagnosis_info: dict):
        pass

    def get_diagnosis_history(self, limit: int = 10):
        return []

    def get_common_issue_types(self, top_n: int = 5):
        return []

    def get_statistics(self):
        return {}

    def clear_history(self):
        pass


class _FakeMerchantProfile(BaseMerchantProfileStore):
    def __init__(self):
        self.records = []

    def record_diagnosis(self, ticket_id, issue_type, responsible_party, root_cause, timestamp=None):
        self.records.append(ticket_id)

    def get_profile(self):
        return {}

    def get_responsibility_tendency(self, top_k: int = 3):
        return []

    def get_common_issue_types(self, top_k: int = 5):
        return []

    def get_context_for_agent(self):
        return "fake merchant context"


def test_short_term_alias_backward_compatible():
    assert ShortTermMemory is InMemoryShortTermMemory


def test_long_term_alias_backward_compatible():
    assert LongTermMemory is FileLongTermMemory


def test_merchant_profile_alias_backward_compatible():
    assert MerchantProfileStore is FileMerchantProfileStore


def test_local_backend_factory_creates_memory_manager():
    factory = MemoryBackendFactory({"backend": "local"})
    mm = factory.create_memory_manager(
        user_id="u1",
        session_id="s1",
        merchant_id="m1",
    )
    assert isinstance(mm, MemoryManager)
    assert isinstance(mm.short_term, InMemoryShortTermMemory)
    assert isinstance(mm.long_term, FileLongTermMemory)
    assert isinstance(mm.merchant_profile, FileMerchantProfileStore)


def test_create_memory_manager_helper():
    mm = create_memory_manager(
        user_id="u2",
        session_id="s2",
        config={"backend": "local", "storage_path": "data/memory_test"},
        merchant_id="m2",
    )
    assert isinstance(mm, MemoryManager)
    assert mm.user_id == "u2"
    assert mm.merchant_id == "m2"


def test_memory_manager_dependency_injection():
    short_term = _FakeShortTermMemory()
    long_term = _FakeLongTermMemory()
    merchant_profile = _FakeMerchantProfile()

    mm = MemoryManager(
        user_id="u3",
        session_id="s3",
        short_term_memory=short_term,
        long_term_memory=long_term,
        merchant_profile_store=merchant_profile,
    )

    mm.add_message("user", "hello")
    assert short_term.messages[0]["content"] == "hello"

    mm.long_term.save_preference("channel", "wecom")
    assert long_term.prefs["channel"] == "wecom"

    mm.merchant_profile.record_diagnosis("T-1", "order", "backend", "system bug")
    assert merchant_profile.records == ["T-1"]


def test_unknown_backend_raises():
    factory = MemoryBackendFactory({"backend": "unknown"})
    with pytest.raises(ValueError, match="Unknown memory backend"):
        factory.create_memory_manager(user_id="u4", session_id="s4")


def test_redis_backend_import_error_without_dependency():
    # 当前环境未安装 redis，构造 Redis backend 应抛出 ImportError
    factory = MemoryBackendFactory({
        "backend": "redis",
        "redis": {"host": "localhost", "port": 6379},
    })
    with pytest.raises(ImportError):
        factory.create_memory_manager(user_id="u5", session_id="s5")


def test_base_classes_cannot_be_instantiated():
    with pytest.raises(TypeError):
        BaseShortTermMemory()
    with pytest.raises(TypeError):
        BaseLongTermMemory()
    with pytest.raises(TypeError):
        BaseMerchantProfileStore()


def test_local_backend_round_trip(tmp_path):
    storage_path = str(tmp_path / "memory")
    mm = create_memory_manager(
        user_id="u6",
        session_id="s6",
        config={"backend": "local", "storage_path": storage_path},
        merchant_id="m6",
    )
    mm.add_message("user", "hi")
    mm.long_term.save_preference("lang", "zh")
    mm.merchant_profile.record_diagnosis("T-2", "asset", "backend", "allocation failure")

    # 重新加载应能读到持久化数据
    mm2 = create_memory_manager(
        user_id="u6",
        session_id="s7",
        config={"backend": "local", "storage_path": storage_path},
        merchant_id="m6",
    )
    assert mm2.long_term.get_preference("lang") == "zh"
    assert mm2.merchant_profile.get_profile()["diagnosis_count"] == 1
