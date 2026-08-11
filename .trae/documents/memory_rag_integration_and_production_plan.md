# Memory 与 RAG 接入及生产级架构改造计划

## 背景与目标

当前项目已经具备一套四层记忆系统（`context/` 目录）：短期记忆、长期记忆、商户画像、诊断模式库。同时 RAG 知识库也已通过 `RAGKnowledgeAgent` 接入诊断主链路。但两者目前是**割裂运行**的：

- RAG 检索没有利用记忆的上下文做 Query Rewrite。
- 一次诊断内多次 RAG 调用结果没有共享。
- 记忆层的存储实现是 demo 级别（内存 + JSON 文件 + Milvus Lite），无法直接支撑企业多实例部署。

本文档目标：

1. 明确 Memory 与 RAG 的接入方式，实现"记忆驱动检索"。
2. 设计 Memory 存储层的抽象接口，使其可替换为 Redis / PostgreSQL / Milvus Cluster 等企业级存储。
3. 给出分阶段落地路线，避免一次性大改造。

---

## 一、当前 Memory 架构现状

### 1.1 四层记忆设计

```
┌─────────────────────────────────────────────────────────────┐
│                      MemoryManager                          │
│  统一对外接口，供 DiagnosisService 和各 Agent 使用            │
└─────────────────────────────────────────────────────────────┘
        │              │              │              │
        ▼              ▼              ▼              ▼
┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
│ 短期记忆     │ │ 长期记忆     │ │ 商户画像     │ │ 诊断模式库   │
│ ShortTerm   │ │ LongTerm    │ │ Merchant    │ │ Pattern     │
│ Memory      │ │ Memory      │ │ Profile     │ │ Store       │
├─────────────┤ ├─────────────┤ ├─────────────┤ ├─────────────┤
│ 存储: 内存   │ │ 存储: JSON  │ │ 存储: JSON  │ │ 存储: Milvus│
│ 范围: 会话级 │ │ 范围: 用户级 │ │ 范围: 商户级 │ │ 范围: 全局  │
│ 容量: 100轮 │ │ 持久化: ✅   │ │ 持久化: ✅   │ │ 持久化: ✅   │
└─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘
```

### 1.2 当前各层职责

| 层级 | 文件 | 当前实现 | 已有能力 |
|---|---|---|---|
| 短期记忆 | `context/short_term_memory.py` | 内存 list | 保存当前会话最近 100 轮对话，自动淘汰 |
| 长期记忆 | `context/long_term_memory.py` | JSON 文件 | 跨会话保存用户偏好、聊天历史、诊断历史 |
| 商户画像 | `context/merchant_profile_store.py` | JSON 文件 | 按 merchant_id 聚合统计问题类型、归属倾向 |
| 诊断模式库 | `context/diagnosis_pattern_store.py` | Milvus Lite | 向量化沉淀成功诊断路径，支持相似模式检索 |

### 1.3 当前已接入诊断主链路

[services/diagnosis_service.py:86-145](file:///Users/yuchen/Learning-library/demo/小哈工单智能诊断助手agent/services/diagnosis_service.py#L86-L145) 中：

```python
memory_manager = MemoryManager(
    user_id=user_id,
    session_id=session_id,
    llm_model=self.llm_model,
    milvus_client=...,
    embedding_model=...,
)

memory_context = {
    "recent_dialogue": memory_manager.short_term.get_context_string(3),
    "merchant_profile": memory_manager.get_merchant_context(),
    "similar_patterns": await memory_manager.find_similar_patterns(query),
}
```

当前记忆已经能给 Agent 提供：

- 最近 3 轮对话
- 商户画像上下文
- 相似历史诊断模式

---

## 二、Memory 与 RAG 的接入设计

### 2.1 当前问题

| 问题 | 说明 |
|---|---|
| RAG 不使用对话上下文 | `RAGKnowledgeAgent.reply()` 直接用原始 query 检索 |
| RAG 结果不进入记忆 | 一次诊断内 IntentionAgent / ResolutionAgent 各自独立调用 RAG |
| 记忆未缓存 RAG 中间结果 | embedding、检索结果、rerank 结果无法复用 |
| 诊断模式库与 RAG 知识库割裂 | 两个 collection，没有统一检索入口 |

### 2.2 目标接入架构

```
┌────────────────────────────────────────────────────────────────────┐
│                         诊断主链路                                  │
│  DiagnosisService                                                  │
│    │                                                               │
│    ▼                                                               │
│  MemoryManager (统一入口)                                           │
│    │                                                               │
│    ├──► 短期记忆：当前会话对话                                       │
│    ├──► 长期记忆：用户偏好/诊断历史                                  │
│    ├──► 商户画像：商户级统计                                         │
│    ├──► 诊断模式库：历史成功路径 (Milvus)                            │
│    └──► RAG 缓存层：session 内检索结果复用                           │
│                      │                                             │
│                      ▼                                             │
│            ┌─────────────────────┐                                 │
│            │   Query Rewrite     │                                 │
│            │   + 上下文压缩       │                                 │
│            └─────────────────────┘                                 │
│                      │                                             │
│                      ▼                                             │
│            ┌─────────────────────┐                                 │
│            │  RAGKnowledgeAgent  │                                 │
│            │  (知识库向量检索)    │                                 │
│            └─────────────────────┘                                 │
│                      │                                             │
│                      ▼                                             │
│            ┌─────────────────────┐                                 │
│            │  Unified Retrieval  │                                 │
│            │  知识库 + 诊断模式库  │                                 │
│            └─────────────────────┘                                 │
└────────────────────────────────────────────────────────────────────┘
```

### 2.3 接入点改造

#### 2.3.1 MemoryManager 增加 RAG 相关能力

文件：`context/memory_manager.py`

```python
class MemoryManager:
    def __init__(self, ..., rag_agent=None):
        # ... 现有初始化 ...
        self.rag_agent = rag_agent
        self._rag_cache: Dict[str, Any] = {}  # session 内 RAG 结果缓存

    async def rewrite_query_for_rag(
        self,
        current_query: str,
        collected_facts: Dict[str, Any] = None
    ) -> str:
        """基于短期记忆和已确认事实改写 query"""
        if not self.llm_model:
            return current_query

        recent_dialogue = self.short_term.get_context_string(2)
        facts = collected_facts or {}

        prompt = f"""根据当前问题、最近对话和已确认事实，生成一个独立的检索 query。

【当前问题】{current_query}
【最近对话】{recent_dialogue}
【已确认事实】{facts}

要求：
1. query 语义完整，不依赖上下文。
2. 保留关键实体：工单号、商户号、系统模块、异常现象。
3. 只输出 query，不解释。
"""
        try:
            response = await self.llm_model([{"role": "user", "content": prompt}])
            return self._extract_text(response).strip() or current_query
        except Exception as e:
            logger.warning(f"Query rewrite failed: {e}")
            return current_query

    def get_rag_cache(self, query: str) -> Optional[Dict]:
        key = f"{self.session_id}:{hash(query)}"
        return self._rag_cache.get(key)

    def set_rag_cache(self, query: str, result: Dict):
        key = f"{self.session_id}:{hash(query)}"
        self._rag_cache[key] = result

    async def search_knowledge(
        self,
        query: str,
        collected_facts: Dict[str, Any] = None,
        use_rewrite: bool = True,
        use_cache: bool = True
    ) -> Dict[str, Any]:
        """
        统一的 RAG 检索入口：
        1. query rewrite（可选）
        2. session 缓存命中则复用
        3. 调用 RAGKnowledgeAgent
        4. 写入缓存
        """
        if use_rewrite:
            search_query = await self.rewrite_query_for_rag(query, collected_facts)
        else:
            search_query = query

        if use_cache:
            cached = self.get_rag_cache(search_query)
            if cached:
                return cached

        if not self.rag_agent:
            return {"status": "no_agent", "answer": "", "retrieved_documents": []}

        msg = Msg(name="orchestrator", content=search_query, role="user")
        response = await self.rag_agent.reply(msg)

        try:
            result = json.loads(response.content)
        except Exception:
            result = {"status": "parse_error", "answer": "", "retrieved_documents": []}

        if use_cache:
            self.set_rag_cache(search_query, result)

        return result

    async def unified_retrieval(
        self,
        query: str,
        collected_facts: Dict[str, Any] = None,
        k: int = 3
    ) -> Dict[str, Any]:
        """
        统一检索：RAG 知识库 + 诊断模式库
        返回结构：
        {
            "knowledge_docs": [...],
            "similar_patterns": [...],
            "rewritten_query": "..."
        }
        """
        rewritten = await self.rewrite_query_for_rag(query, collected_facts)

        knowledge_result = await self.search_knowledge(
            rewritten,
            use_rewrite=False,  # 已经改写过了
            use_cache=True
        )

        similar_patterns = await self.find_similar_patterns(rewritten, k=k)

        return {
            "knowledge_docs": knowledge_result.get("retrieved_documents", []),
            "similar_patterns": similar_patterns,
            "rewritten_query": rewritten,
        }
```

#### 2.3.2 DiagnosisService 统一 RAG 调用

文件：`services/diagnosis_service.py`

```python
class DiagnosisService:
    async def diagnose(self, query: str, user_id: str = "default", session_id: str = None):
        # ... 现有初始化 ...
        memory_manager = MemoryManager(
            user_id=user_id,
            session_id=session_id,
            llm_model=self.llm_model,
            milvus_client=...,  # 复用给 pattern_store
            embedding_model=...,  # 复用给 pattern_store
            rag_agent=await self._get_rag_agent(),  # 新增
        )

        # IntentionAgent 使用 unified_retrieval 做 fallback
        # ResolutionAgent 直接调用 memory_manager.search_knowledge()
        # 两者共享 session 内缓存
```

#### 2.3.3 IntentionAgent / ResolutionAgent 改造

文件：`agents/diagnosis_intention_agent.py`、`agents/resolution_agent.py`

```python
# 不再直接调用 rag_agent，改为调用 memory_manager
memory_manager = context.get("memory_manager")
if memory_manager:
    rag_result = await memory_manager.search_knowledge(
        query,
        collected_facts=context.get("collected_data", {}).get("facts", {})
    )
```

---

## 三、Memory 生产级架构设计

### 3.1 当前架构的企业级瓶颈

| 层级 | 当前实现 | 企业级瓶颈 |
|---|---|---|
| 短期记忆 | 内存 list | 多 Pod 无法共享；进程重启丢失；无 TTL |
| 长期记忆 | JSON 文件 | 并发写会损坏；无法水平扩展；无索引/分页 |
| 商户画像 | JSON 文件 | 同上；商户维度数据量大后性能差 |
| 诊断模式库 | Milvus Lite | 本地文件，无法多实例共享 |

### 3.2 生产级目标架构

```
┌────────────────────────────────────────────────────────────────────┐
│                         接入层                                      │
│  MemoryManager (统一接口，存储实现可替换)                            │
└────────────────────────────────────────────────────────────────────┘
        │                  │                  │
        ▼                  ▼                  ▼
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│  短期记忆      │  │  长期记忆      │  │  商户画像      │
│  接口: BaseSTM │  │  接口: BaseLTM │  │  接口: BaseMP  │
└───────────────┘  └───────────────┘  └───────────────┘
        │                  │                  │
   ┌────┴────┐        ┌────┴────┐        ┌────┴────┐
   │         │        │         │        │         │
   ▼         ▼        ▼         ▼        ▼         ▼
InMemory   Redis   JSON File   PostgreSQL  JSON File   MongoDB
(Local)   (Dist)   (Local)    (Dist)     (Local)    (Dist)
```

### 3.3 存储层抽象接口

#### 3.3.1 抽象基类

文件：`context/base_memory.py`（新增）

```python
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional


class BaseShortTermMemory(ABC):
    @abstractmethod
    def add_message(self, role: str, content: str, metadata: Dict = None): ...

    @abstractmethod
    def get_recent_context(self, n_turns: int = None) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def get_context_string(self, n_turns: int = 5) -> str: ...

    @abstractmethod
    def clear(self): ...


class BaseLongTermMemory(ABC):
    @abstractmethod
    def save_preference(self, pref_type: str, value: Any): ...

    @abstractmethod
    def get_preference(self, pref_type: str = None) -> Any: ...

    @abstractmethod
    def add_chat_message(self, role: str, content: str, session_id: str = None): ...

    @abstractmethod
    def get_chat_history(self, limit: int = None, session_id: str = None) -> List[Dict]: ...

    @abstractmethod
    def save_diagnosis_history(self, diagnosis_info: Dict[str, Any]): ...

    @abstractmethod
    def get_diagnosis_history(self, limit: int = 10) -> List[Dict]: ...


class BaseMerchantProfileStore(ABC):
    @abstractmethod
    def record_diagnosis(self, ticket_id, issue_type, responsible_party, root_cause, timestamp): ...

    @abstractmethod
    def get_profile(self) -> Dict[str, Any]: ...

    @abstractmethod
    def get_context_for_agent(self) -> str: ...
```

#### 3.3.2 现有实现迁移

| 当前类 | 新名称 | 说明 |
|---|---|---|
| `ShortTermMemory` | `InMemoryShortTermMemory` | 实现 `BaseShortTermMemory` |
| `LongTermMemory` | `FileLongTermMemory` | 实现 `BaseLongTermMemory` |
| `MerchantProfileStore` | `FileMerchantProfileStore` | 实现 `BaseMerchantProfileStore` |

#### 3.3.3 MemoryManager 依赖注入

```python
class MemoryManager:
    def __init__(
        self,
        user_id: str,
        session_id: str,
        short_term_memory: Optional[BaseShortTermMemory] = None,
        long_term_memory: Optional[BaseLongTermMemory] = None,
        merchant_profile_store: Optional[BaseMerchantProfileStore] = None,
        pattern_store: Optional[DiagnosisPatternStore] = None,
        rag_agent=None,
        llm_model=None,
    ):
        self.short_term = short_term_memory or InMemoryShortTermMemory(max_turns=100)
        self.long_term = long_term_memory or FileLongTermMemory(user_id)
        self.merchant_profile = merchant_profile_store
        self.pattern_store = pattern_store
        self.rag_agent = rag_agent
        self.llm_model = llm_model
```

### 3.4 各层企业级存储选型建议

| 层级 | 当前 | 小规模企业 | 中大型企业 |
|---|---|---|---|
| 短期记忆 | 内存 | Redis | Redis Cluster |
| 长期记忆 | JSON 文件 | PostgreSQL / SQLite | PostgreSQL + 读写分离 |
| 商户画像 | JSON 文件 | PostgreSQL / MongoDB | MongoDB / ClickHouse |
| 诊断模式库 | Milvus Lite | Milvus Standalone | Milvus Cluster / Zilliz |
| RAG 知识库 | Milvus Lite | Milvus Standalone | Milvus Cluster / Zilliz |

---

## 四、分阶段实施路线图

### Phase 1：Memory 与 RAG 接入（1-2 周）

目标：让 RAG 用上记忆，减少多轮浪费。

1. `MemoryManager` 增加：
   - `rag_agent` 注入
   - `rewrite_query_for_rag`
   - `search_knowledge`（带缓存）
   - `unified_retrieval`
2. `DiagnosisService` 创建 `MemoryManager` 时注入 `rag_agent`。
3. `IntentionAgent` / `ResolutionAgent` 改走 `memory_manager.search_knowledge()`。
4. 增加 `config.py` 中 `MEMORY_RAG_CONFIG` 开关。
5. 补充测试：`tests/test_memory_rag_integration.py`。

### Phase 2：存储层抽象（2-3 周）

目标：让存储实现可替换，为企业级部署留接口。

1. 新增 `context/base_memory.py` 抽象接口。
2. 现有实现改名：
   - `ShortTermMemory` → `InMemoryShortTermMemory`
   - `LongTermMemory` → `FileLongTermMemory`
   - `MerchantProfileStore` → `FileMerchantProfileStore`
3. `MemoryManager` 改为依赖注入。
4. 增加 `MemoryBackendFactory`，根据配置创建不同实现。
5. 现有 JSON 实现保留为默认 backend，保证 demo 可运行。

### Phase 3：企业级存储落地（1-2 个月）

目标：替换为分布式存储。

1. 实现 `RedisShortTermMemory`。
2. 实现 `PostgresLongTermMemory`。
3. 实现 `MongoMerchantProfileStore`。
4. Milvus Lite → Milvus Cluster。
5. 增加 Redis 分布式缓存层（RAG embedding/query 结果）。
6. 增加分布式互斥锁（防缓存击穿）。
7. 增加会话亲和性 / WebSocket sticky session 策略。

---

## 五、关键接口设计

### 5.1 MemoryManager 对外接口

```python
class MemoryManager:
    # 消息与上下文
    def add_message(self, role: str, content: str, metadata: Dict = None): ...
    def get_context_for_agent(self) -> str: ...
    def get_full_context(self) -> Dict[str, Any]: ...

    # 商户
    def set_merchant_id(self, merchant_id: str): ...
    def get_merchant_context(self) -> str: ...

    # 诊断记录
    async def record_diagnosis(self, diagnosis_result: Dict[str, Any]): ...

    # 模式库
    async def find_similar_patterns(self, query: str, k: int = 3) -> List[Dict]: ...

    # RAG 接入（新增）
    async def rewrite_query_for_rag(self, current_query: str, collected_facts: Dict = None) -> str: ...
    async def search_knowledge(self, query: str, collected_facts: Dict = None, ...) -> Dict: ...
    async def unified_retrieval(self, query: str, collected_facts: Dict = None, k: int = 3) -> Dict: ...
```

### 5.2 工厂函数

```python
# context/factory.py
from config import MEMORY_CONFIG

def create_memory_manager(user_id: str, session_id: str, **kwargs) -> MemoryManager:
    backend = MEMORY_CONFIG.get("backend", "local")

    if backend == "local":
        return MemoryManager(
            user_id=user_id,
            session_id=session_id,
            short_term_memory=InMemoryShortTermMemory(),
            long_term_memory=FileLongTermMemory(user_id),
            merchant_profile_store=FileMerchantProfileStore(merchant_id),
            **kwargs
        )

    if backend == "redis":
        # 后续实现
        return MemoryManager(
            user_id=user_id,
            session_id=session_id,
            short_term_memory=RedisShortTermMemory(session_id),
            long_term_memory=FileLongTermMemory(user_id),  # 长期记忆可逐步迁移
            **kwargs
        )

    raise ValueError(f"Unknown memory backend: {backend}")
```

---

## 六、配置入口建议

文件：`config.py`

```python
MEMORY_CONFIG = {
    # 存储后端
    "backend": "local",  # local / redis / hybrid

    # 短期记忆
    "short_term": {
        "max_turns": 100,
        "ttl_seconds": 3600,
    },

    # 长期记忆
    "long_term": {
        "storage_path": "data/memory",
        "backend": "file",  # file / postgres
    },

    # 商户画像
    "merchant_profile": {
        "backend": "file",  # file / mongo
    },

    # RAG 接入
    "rag": {
        "enable_query_rewrite": True,
        "enable_session_cache": True,
        "enable_unified_retrieval": True,
        "cache_ttl_seconds": 300,
    }
}

# Redis 配置（后续使用）
REDIS_CONFIG = {
    "host": "localhost",
    "port": 6379,
    "db": 0,
    "password": None,
}
```

---

## 七、风险与收益

### 收益

| 优化项 | 预期收益 |
|---|---|
| Query Rewrite | 多轮 RAG 命中率提升 20%-40% |
| Session RAG 缓存 | 单次诊断内 RAG 调用减少 50%-80% |
| Unified Retrieval | 知识库 + 模式库一次检索，减少 LLM 调用 |
| 存储层抽象 | 后续切换 Redis/PostgreSQL/Milvus Cluster 无需改业务代码 |
| 企业级存储 | 支撑多实例、高并发、数据一致性 |

### 风险

| 风险 | 缓解措施 |
|---|---|
| Query Rewrite 增加 LLM 调用 | 可配置开关；缓存改写结果 |
| 抽象接口增加复杂度 | 保留默认 local backend，现有测试不破坏 |
| 分布式存储引入运维成本 | Phase 3 再实施，先保证接口可用 |
| 短期记忆迁 Redis 后延迟增加 | 本地保留热缓存，Redis 做持久化 |

---

## 八、下一步行动

建议先执行 **Phase 1** 的前 3 项，形成最小可用闭环：

1. 在 `MemoryManager` 中增加 `rag_agent` 注入和 `search_knowledge` 方法。
2. 在 `DiagnosisService` 中把 `rag_agent` 注入 `MemoryManager`。
3. 让 `IntentionAgent` 和 `ResolutionAgent` 统一走 `memory_manager.search_knowledge()`。

完成后再评估是否需要继续 Phase 2 的存储层抽象。
