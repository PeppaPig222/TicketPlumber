# RAG 检索增强生产级改造计划

## 背景与目标

当前 RAG 模块已完成基础链路跑通：PDF 解析 → 文本切分 → Milvus Lite 向量存储 → IntentionAgent fallback / ResolutionAgent 证据补充。但随着知识库扩容、多轮诊断深化、企业级并发接入，以下三类问题会快速放大：

1. **缓存穿透**：无效/低频 query 反复打到 embedding 模型和 Milvus，拖慢响应并浪费计算资源。
2. **语义重复分片冗余**：滑动窗口切分 + 架构图占位导致召回结果高度重复，浪费 LLM token 并稀释有效信息。
3. **多轮浪费**：IntentionAgent 与 ResolutionAgent 在同一次诊断中独立调用 RAG，相同 query 重复 encode/search，且多轮上下文未做 query rewrite。

本文档将之前讨论的生产级 RAG 架构落地方案系统化，目标是在**不推翻现有架构**的前提下，分阶段实现企业级检索增强。

遵循原则：

- 保留现有 `RAGKnowledgeAgent` / `DiagnosisService` 职责边界。
- 存储层与策略层解耦，方便后续替换 Redis / Milvus Cluster。
- 分阶段实施：先解决 80% 问题的最小改动，再逐步上重量级组件。

---

## 一、目标架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                        应用层 (Agent / Service)                  │
│  DiagnosisService  ──►  MemoryManager  ──►  RAGKnowledgeAgent   │
│         │                       │                       │       │
│         ▼                       ▼                       ▼       │
│  ┌─────────────┐      ┌─────────────────┐      ┌─────────────┐  │
│  │ Query       │      │ Session/Task    │      │ Embedding/  │  │
│  │ Rewrite     │      │ Cache Layer     │      │ Search      │  │
│  │ + 上下文压缩 │      │ (空值/结果/热词)  │      │ Cache       │  │
│  └─────────────┘      └─────────────────┘      └─────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                        检索策略层                                │
│  Hybrid Retrieval ──► Top-K Recall ──► Similarity Dedup ──► MMR │
│  ├─ 向量检索 (BGE + Milvus)                                      │
│  ├─ 关键词过滤 (ticket_id / merchant_id / 系统模块)              │
│  ├─ 向量阈值去重 (cos > Mean + 1.5σ)                            │
│  └─ MMR 重排序 + 父文档召回                                      │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                        知识库构建层                              │
│  Document Ingest ──► MD5 Fingerprint ──► Semantic Chunking      │
│  ──► Semantic Cluster Dedup ──► Boundary Dedup ──► Milvus      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 二、缓存穿透：七层防御体系

### 2.1 分层防御设计

```
【第一层：前置拦截】
├── L1: 接口参数校验（拦截非法请求）
├── L2: 布隆过滤器（判断 key 是否存在，拦截 99%+ 穿透流量）
└── L3: 空值缓存（缓存空结果，防止重复穿透）

【第二层：加速层】
├── L4: 热点预加载（FAQ Top100 常驻内存，冷启动即就绪）
├── L5: Query 哈希缓存-本地进程（LRU，进程内极速响应）
└── L6: Query 哈希缓存-Redis（分布式共享，跨 Pod 一致性）

【第三层：防击穿层】
└── L7: 互斥锁（保证只有一个请求去查 DB/Milvus）
```

### 2.2 分阶段落地建议

| 层级 | 当前适合度 | 落地阶段 | 说明 |
|---|---|---|---|
| L1 参数校验 | ✅ 现在 | Phase 1 | 在 `search_knowledge` 入口校验 query 长度与格式 |
| L3 空值缓存 | ✅ 现在 | Phase 1 | 缓存"无结果"query，避免反复穿透 |
| L5 本地 LRU | ✅ 现在 | Phase 1 | 改动最小，单实例收益最高 |
| L4 热点预加载 | ⚠️ 知识库扩容后 | Phase 2 | 待 FAQ 入库后预加载高频 query |
| L6 Redis 缓存 | ❌ 当前不适用 | Phase 3 | 单机部署不需要，多实例时再上 |
| L2 布隆过滤器 | ❌ 当前不适用 | Phase 3 | 单机 Milvus Lite 收益低于复杂度 |
| L7 互斥锁 | ❌ 当前不适用 | Phase 3 | 单机用线程锁即可，分布式再上 Redis 分布式锁 |

### 2.3 Phase 1 最小改动

文件：`.claude/skills/ask-question/script/agent.py`

在 `RAGKnowledgeAgent` 中新增：

```python
from functools import lru_cache

class RAGKnowledgeAgent(AgentBase):
    def __init__(self, ...):
        # ... 现有初始化 ...
        self._empty_result_cache: set = set()  # L3 空值缓存
        self._query_embedding_cache = LRUCache(maxsize=512)  # L5 本地缓存

    def _validate_query(self, query: str) -> bool:
        """L1: 参数校验"""
        if not query or not isinstance(query, str):
            return False
        query = query.strip()
        return 2 <= len(query) <= 500

    def _get_cached_embedding(self, query: str):
        """L5: query -> embedding 本地缓存"""
        return self._query_embedding_cache.get(query)

    def _cache_embedding(self, query: str, embedding):
        self._query_embedding_cache.put(query, embedding)

    def _is_empty_result(self, query: str) -> bool:
        """L3: 空值缓存判断"""
        return hash(query) in self._empty_result_cache

    def _mark_empty_result(self, query: str):
        """L3: 标记空结果"""
        self._empty_result_cache.add(hash(query))
```

---

## 三、语义重复分片冗余：写入 + 读取双优化

### 3.1 写入时优化

```
Document Ingest
    │
    ▼
MD5 文档指纹去重 ──► 完全重复文档/段落直接跳过
    │
    ▼
Semantic Chunking (BGE 语义边界切分)
    │
    ▼
Semantic Cluster Dedup (MiniBatch K-Means + 质心筛选)
    │
    ▼
Boundary Dedup (相邻 chunk 重叠度去重)
    │
    ▼
Milvus Insert
```

#### 分阶段落地

| 步骤 | 当前适合度 | 落地阶段 | 说明 |
|---|---|---|---|
| MD5 文档指纹去重 | ✅ 现在 | Phase 1 | 重建知识库时避免重复 PDF 重复入库 |
| 边界去重 | ✅ 现在 | Phase 1 | 在 `process_merchant_pdf.py` 中低成本实现 |
| 语义切分 (BGE) | ⚠️ 后续 | Phase 2 | 当前固定滑动窗口够用，文档量大后再升级 |
| 语义聚类去重 (K-Means) | ❌ 当前不需要 | Phase 3 | chunk 数量 >1000 后才有收益 |

#### Phase 1 最小改动

文件：`scripts/process_merchant_pdf.py`

```python
import hashlib

def compute_content_hash(content: str) -> str:
    """MD5 文档/段落指纹"""
    return hashlib.md5(content.strip().encode("utf-8")).hexdigest()

def deduplicate_chunks(chunks: List[Dict]) -> List[Dict]:
    """
    Phase 1 去重：
    1. MD5 去重
    2. 相邻 chunk 重叠度 > 80% 只保留一个
    """
    seen_hashes = set()
    deduped = []

    for chunk in chunks:
        h = compute_content_hash(chunk["content"])
        if h in seen_hashes:
            continue
        seen_hashes.add(h)

        # 边界去重：与上一个保留的 chunk 比较
        if deduped and overlap_ratio(deduped[-1]["content"], chunk["content"]) > 0.8:
            continue
        deduped.append(chunk)

    return deduped
```

### 3.2 读取时优化

```
Query
  │
  ▼
Hybrid Retrieval
  ├─ 向量检索：BGE + Milvus，召回 Top 15-20
  └─ 关键词过滤：ticket_id / merchant_id / 系统模块
  │
  ▼
Similarity Dedup: cos > Mean + 1.5σ 合并
  │
  ▼
MMR Rerank: 平衡相关性与多样性
  │
  ▼
Parent Document Recall: chunk 附近 ±200 字符
  │
  ▼
Summary Compression → LLM
```

#### 分阶段落地

| 步骤 | 当前适合度 | 落地阶段 | 说明 |
|---|---|---|---|
| Top-K 扩大召回 | ✅ 现在 | Phase 1 | 当前 `top_k=3` 太少，建议扩到 10-15 |
| 向量阈值去重 | ✅ 现在 | Phase 1 | 避免同一架构图被反复召回 |
| 父文档召回 | ✅ 现在 | Phase 1 | 对架构图占位 chunk 特别有价值 |
| MMR 重排序 | ⚠️ 后续 | Phase 2 | 知识库类型丰富后收益明显 |
| Hybrid Retrieval | ⚠️ 后续 | Phase 2 | 对工单号、商户 ID 精确查询有价值 |
| 摘要压缩 | ❌ 当前不需要 | Phase 3 | 当前 chunk 长度不大 |

#### Phase 1 最小改动

文件：`.claude/skills/ask-question/script/agent.py`

```python
import numpy as np

class RAGKnowledgeAgent(AgentBase):
    async def search_knowledge(self, query: str, top_k: Optional[int] = None):
        # 1. 参数校验
        if not self._validate_query(query):
            return []

        # 2. 空值缓存命中
        if self._is_empty_result(query):
            return []

        k = top_k or self.top_k
        # Phase 1: 内部召回放大，再后处理去重
        recall_k = max(k * 5, 15)

        # 3. embedding 缓存
        query_embedding = self._get_cached_embedding(query)
        if query_embedding is None:
            query_embedding = self.embedding_model.encode(query).tolist()
            self._cache_embedding(query, query_embedding)

        results = self.milvus_client.search(
            collection_name=self.collection_name,
            data=[query_embedding],
            limit=recall_k,
            output_fields=["id", "content", "metadata"]
        )

        retrieved = self._format_results(results)

        # 4. 向量阈值去重
        retrieved = self._dedup_by_similarity(retrieved)

        # 5. 父文档召回 (placeholder，当前 chunk 自包含)
        retrieved = self._parent_document_recall(retrieved)

        # 6. 截断到最终 top_k
        final = retrieved[:k]

        if not final:
            self._mark_empty_result(query)

        return final

    def _dedup_by_similarity(self, docs: List[Dict], threshold: float = None) -> List[Dict]:
        """
        基于余弦相似度去重：
        - 计算所有结果两两相似度
        - 动态阈值 = Mean + 1.5 * Std
        - 超过阈值且后出现的 doc 被合并/丢弃
        """
        if len(docs) <= 1:
            return docs

        embeddings = [self.embedding_model.encode(d["content"]) for d in docs]
        matrix = cosine_similarity(embeddings)

        mean = np.mean(matrix)
        std = np.std(matrix)
        threshold = threshold or min(mean + 1.5 * std, 0.92)

        kept_indices = []
        for i in range(len(docs)):
            is_dup = any(
                matrix[i][j] > threshold and i > j
                for j in kept_indices
            )
            if not is_dup:
                kept_indices.append(i)

        return [docs[i] for i in kept_indices]

    def _parent_document_recall(self, docs: List[Dict]) -> List[Dict]:
        """
        父文档召回：
        当前实现为 placeholder，从 metadata 中补充 page/source 信息。
        后续可扩展为读取原始文档的 ±200 字符上下文。
        """
        for doc in docs:
            meta = doc.get("metadata", {})
            if "page" in meta:
                doc["content"] = f"[来源: 第{meta['page']}页]\n{doc['content']}"
        return docs
```

---

## 四、多轮浪费：Query Rewrite + 分层缓存

### 4.1 问题定位

当前多轮诊断中：

- IntentionAgent 在第一轮调用一次 RAG（场景识别 fallback）。
- ResolutionAgent 在第三轮调用一次 RAG（策略补充证据）。
- 两次调用相互独立，相同或相似的 query 会重复 encode + search。
- 多轮上下文没有用于改写 query，导致第三轮 RAG 用原始问题检索，而不是用"已确认事实 + 当前目标"检索。

### 4.2 目标架构

```
User Query
    │
    ▼
Query Rewrite Module
  ├─ 输入：当前 query + short_term 最近 3 轮 + collected_data 已确认事实
  ├─ 输出：独立语义 query（用于 RAG 检索）
  └─ 无 LLM 时 fallback 到原始 query
    │
    ▼
Session/Task Cache Layer
  ├─ key: (session_id, rewritten_query)
  ├─ value: {embedding, retrieved_chunks, reranked_chunks, tool_results}
  └─ TTL: 300s（一次诊断周期内）
    │
    ▼
RAGKnowledgeAgent.search_knowledge()
```

### 4.3 分层缓存内容

| 缓存对象 | 生命周期 | 存储位置 | 作用 |
|---|---|---|---|
| query embedding | 短 | 进程内 LRU | 避免重复编码 |
| 检索结果 chunks | 短 | Session 缓存 | Intention/Resolution 共享 |
| rerank 结果 | 短 | Session 缓存 | 多次引用同一结果时复用 |
| tool result | 中 | Session/Redis | 跨轮工具调用结果复用 |
| 空值结果 | 中 | 进程内/Redis | 防止缓存穿透 |
| FAQ Top100 | 长 | 内存 | 热点预加载 |

### 4.4 Phase 1 最小改动

#### 4.4.1 在 MemoryManager 中增加 Session RAG 缓存

文件：`context/memory_manager.py`

```python
class MemoryManager:
    def __init__(self, ...):
        # ... 现有初始化 ...
        self._rag_cache: Dict[str, Any] = {}  # session 内 RAG 结果缓存

    def get_rag_cache_key(self, query: str) -> str:
        """基于 session_id + query 生成缓存 key"""
        return f"{self.session_id}:{hash(query)}"

    def get_cached_rag_result(self, query: str) -> Optional[Dict]:
        return self._rag_cache.get(self.get_rag_cache_key(query))

    def set_cached_rag_result(self, query: str, result: Dict):
        self._rag_cache[self.get_rag_cache_key(query)] = result

    async def rewrite_query_for_rag(
        self,
        current_query: str,
        collected_facts: Dict[str, Any] = None
    ) -> str:
        """
        Query Rewrite + 上下文压缩：
        - 输入当前 query、最近对话、已确认事实
        - 输出独立语义 query
        - 无 LLM 时返回原始 query
        """
        if not self.llm_model:
            return current_query

        recent_dialogue = self.short_term.get_context_string(2)
        facts = collected_facts or {}

        prompt = f"""你是一个工单诊断助手。请根据当前问题、最近对话和已确认事实，生成一个独立的检索 query，用于从知识库中检索相关信息。

【当前问题】
{current_query}

【最近对话】
{recent_dialogue}

【已确认事实】
{facts}

要求：
1. 生成的 query 必须语义完整，不依赖上下文也能理解。
2. 保留关键实体：工单号、商户号、系统模块、异常现象等。
3. 只输出检索 query，不要解释。
"""

        try:
            response = await self.llm_model([{"role": "user", "content": prompt}])
            rewritten = self._extract_text(response).strip()
            return rewritten or current_query
        except Exception as e:
            logger.warning(f"Query rewrite failed: {e}, fallback to original query")
            return current_query

    def _extract_text(self, response) -> str:
        """统一提取 LLM 文本"""
        if hasattr(response, "__aiter__"):
            text = ""
            async for chunk in response:
                if isinstance(chunk, str):
                    text = chunk
                elif hasattr(chunk, "content"):
                    text = chunk.content
            return text
        if hasattr(response, "text"):
            return response.text
        if hasattr(response, "content"):
            return response.content
        return str(response)
```

#### 4.4.2 DiagnosisService 中复用 RAG 结果

文件：`services/diagnosis_service.py`

```python
class DiagnosisService:
    async def _get_rag_result(
        self,
        memory_manager: MemoryManager,
        query: str,
        collected_facts: Dict[str, Any]
    ) -> List[Dict]:
        """
        统一的 RAG 查询入口：
        1. query rewrite
        2. session 缓存命中则复用
        3. 否则调用 RAGKnowledgeAgent
        """
        rewritten = await memory_manager.rewrite_query_for_rag(query, collected_facts)

        cached = memory_manager.get_cached_rag_result(rewritten)
        if cached is not None:
            logger.info(f"RAG cache hit for rewritten query: {rewritten[:50]}")
            return cached.get("retrieved_docs", [])

        rag_agent = await self._get_rag_agent()
        msg = Msg(name="orchestrator", content=rewritten, role="user")
        response = await rag_agent.reply(msg)

        try:
            result = json.loads(response.content)
            docs = result.get("retrieved_documents", [])
            memory_manager.set_cached_rag_result(rewritten, {"retrieved_docs": docs})
            return docs
        except Exception:
            return []
```

---

## 五、整体改造实施路线图

### Phase 1（当前，1-2 周）

目标：解决 80% 的实际问题，改动最小。

1. **RAG Agent 加本地 LRU 缓存**（embedding + 空值结果）
2. **参数校验**拦截非法 query
3. **PDF 入库去重**（MD5 + 边界去重）
4. **检索后向量阈值去重 + 父文档召回**
5. **Top-K 召回扩大**到 15
6. **MemoryManager 增加 Session RAG 缓存**
7. **Query Rewrite** 接入 short_term + collected_facts
8. **DiagnosisService 统一 RAG 入口**，Intention/Resolution 共享结果

### Phase 2（知识库扩容后，2-4 周）

1. **热点预加载**：FAQ Top100 常驻内存
2. **MMR 重排序**：平衡相关性与多样性
3. **Hybrid Retrieval**：向量 + 关键词混合检索
4. **语义切分**：替代固定滑动窗口
5. **摘要压缩**：长文档 chunk 送入 LLM 前压缩

### Phase 3（企业分布式部署，1-3 个月）

1. **Redis 分布式缓存**替换本地 LRU
2. **布隆过滤器**拦截非法/不存在 query
3. **分布式互斥锁**防止缓存击穿
4. **语义聚类去重**（K-Means / HDBSCAN）
5. **Milvus Lite → Milvus Cluster / Zilliz Cloud**
6. **长期记忆 JSON → PostgreSQL / MongoDB**
7. **商户画像独立表 + 聚合计算**

---

## 六、配置入口建议

文件：`config.py`

```python
RAG_RETRIEVAL_CONFIG = {
    # 缓存层
    "enable_embedding_cache": True,
    "embedding_cache_size": 512,
    "enable_empty_result_cache": True,
    "enable_session_rag_cache": True,

    # 检索层
    "recall_top_k": 15,           # 内部召回数量
    "final_top_k": 3,             # 返回 Agent 的数量
    "similarity_dedup_threshold": None,  # None 表示动态阈值 Mean+1.5σ

    # Query Rewrite
    "enable_query_rewrite": True,
    "query_rewrite_max_history_turns": 2,

    # 阶段开关
    "enable_mmr": False,
    "enable_hybrid_retrieval": False,
    "enable_parent_document_recall": True,
}

KNOWLEDGE_BASE_BUILD_CONFIG = {
    "enable_md5_dedup": True,
    "enable_boundary_dedup": True,
    "boundary_overlap_threshold": 0.8,
    "enable_semantic_chunking": False,
    "enable_semantic_cluster_dedup": False,
}
```

---

## 七、风险与收益

### 收益

| 优化项 | 预期收益 |
|---|---|
| 本地 LRU + Session 缓存 | RAG 重复调用减少 50%-80% |
| 空值缓存 | 无效 query 不再穿透到 Milvus |
| 向量阈值去重 | 召回结果重复率下降 60%+ |
| Query Rewrite | 多轮 RAG 命中率提升 20%-40% |
| MD5/边界去重 | 知识库体积减少 10%-30% |

### 风险

| 风险 | 缓解措施 |
|---|---|
| 缓存导致结果陈旧 | Session 缓存 TTL 300s，空值缓存定期失效 |
| Query Rewrite 引入偏差 | 保留原始 query fallback，A/B 测试后全量 |
| 动态阈值去重过度 | 设置上限阈值 0.92，保留人工可配置开关 |
| 阶段 2/3 改造过大 | 严格按 Phase 实施，每阶段有独立测试 |

---

## 八、下一步行动

建议优先执行 **Phase 1** 中的以下 3 项，形成可验证的最小闭环：

1. 在 `RAGKnowledgeAgent` 中实现 L1/L3/L5 缓存。
2. 在 `MemoryManager` 中实现 Session RAG 缓存 + Query Rewrite。
3. 在 `DiagnosisService` 中统一 RAG 调用入口，让 IntentionAgent 和 ResolutionAgent 共享结果。

完成后再跑一遍 `scripts/run_evaluation.py` 和 `tests/test_rag_integration.py`，验证准确率不下降、响应时间下降。
