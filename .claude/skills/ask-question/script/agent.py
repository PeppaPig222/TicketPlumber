"""
RAG知识库智能体 RAGKnowledgeAgent
职责：基于向量数据库的知识检索与问答

核心功能：
1. 知识库构建：将工单诊断相关文档向量化并存储到Milvus Lite
2. 语义检索：根据用户查询检索最相关的知识片段
3. 知识问答：结合检索到的知识和LLM生成准确答案
4. 知识管理：支持添加、更新、删除知识库内容

技术栈：
- Milvus Lite: 轻量级向量数据库（本地存储）
- sentence-transformers: 文本向量化模型
- LLM: 用户配置的豆包模型用于生成答案

安装：
pip install milvus sentence-transformers
"""
from agentscope.agent import AgentBase
from agentscope.message import Msg
from typing import Optional, Union, List, Dict
from collections import OrderedDict
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

import numpy as np

# Add project root to sys.path
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../..")))

_GRPC_MAX_MS = '2147483647'  # gRPC 使用的 int32 上限，约 24.8 天
os.environ['GRPC_KEEPALIVE_TIME_MS'] = _GRPC_MAX_MS
os.environ['GRPC_KEEPALIVE_TIMEOUT_MS'] = '20000'
os.environ['GRPC_KEEPALIVE_PERMIT_WITHOUT_CALLS'] = '0'
os.environ['GRPC_HTTP2_MIN_RECV_PING_INTERVAL_WITHOUT_DATA_MS'] = _GRPC_MAX_MS
os.environ['GRPC_HTTP2_MIN_PING_INTERVAL_WITHOUT_DATA_MS'] = _GRPC_MAX_MS

logger = logging.getLogger(__name__)

try:
    from pymilvus import MilvusClient, DataType
    from sentence_transformers import SentenceTransformer
    DEPENDENCIES_AVAILABLE = True
except ImportError as e:
    logger.warning(f"RAG dependencies not available: {e}")
    logger.warning("Install with: pip install pymilvus sentence-transformers")
    DEPENDENCIES_AVAILABLE = False


class RAGKnowledgeAgent(AgentBase):
    """RAG知识库智能体"""

    def __init__(
        self,
        name: str = "RAGKnowledgeAgent",
        model=None,
        knowledge_base_path: str | None = None,
        collection_name: str = "ticket_diagnosis_knowledge",
        embedding_model: str = "BAAI/bge-small-zh-v1.5",
        top_k: int = 3,
        **kwargs
    ):
        super().__init__()
        self.name = name
        self.model = model
        
        if knowledge_base_path is None:
            # Default to local data directory in skill folder
            current_dir = Path(__file__).parent.parent
            knowledge_base_path = str(current_dir / "data" / "rag_knowledge")

        self.knowledge_base_path = Path(knowledge_base_path)
        self.collection_name = collection_name
        self.top_k = top_k
        from utils.skill_loader import SkillLoader
        self.skill_loader = SkillLoader()

        # ── 检索增强：缓存与检索配置 ──
        self._retrieval_config: Dict[str, Any] = {}
        try:
            from config import RAG_CONFIG
            self._retrieval_config = dict(RAG_CONFIG)
        except Exception:
            pass

        self._empty_result_cache: set = set()          # L3 空值缓存
        self._query_embedding_cache: OrderedDict = OrderedDict()  # L5 本地 LRU

        if not DEPENDENCIES_AVAILABLE:
            logger.error("RAG dependencies not installed. Install with: pip install pymilvus sentence-transformers")
            self.initialized = False
            return

        # 优先使用 config 中的配置（支持本地路径，避免连 HuggingFace）
        try:
            from config import RAG_CONFIG
            embedding_model = RAG_CONFIG.get("embedding_model", embedding_model)
        except Exception:
            pass

        # 若配置的是本地路径且存在，则从本地加载，否则按模型 ID 使用（会联网）
        model_path_or_id = embedding_model
        path_obj = Path(embedding_model).expanduser()
        if not path_obj.is_absolute():
            path_obj = Path.cwd() / path_obj
        if path_obj.exists():
            model_path_or_id = str(path_obj.resolve())
            logger.info(f"Using local embedding model: {model_path_or_id}")
        else:
            if "/" in embedding_model or "\\" in embedding_model or embedding_model.startswith("."):
                logger.warning(
                    f"Configured embedding path does not exist: {embedding_model}，将使用 BAAI/bge-small-zh-v1.5 并尝试联网下载。"
                )
                model_path_or_id = "BAAI/bge-small-zh-v1.5"
        logger.info(f"Loading embedding model: {model_path_or_id}")
        self.embedding_model = SentenceTransformer(model_path_or_id)
        self.embedding_dim = self.embedding_model.get_sentence_embedding_dimension()

        # 初始化 Milvus：优先远程服务（容器化部署），否则本地 Milvus Lite
        milvus_uri = self._retrieval_config.get("milvus_uri", "")
        if milvus_uri:
            logger.info(f"Connecting to remote Milvus at: {milvus_uri}")
            self.milvus_client = MilvusClient(uri=milvus_uri, db_name="default")
        else:
            milvus_db_path = str(self.knowledge_base_path / "milvus_lite.db")
            logger.info(f"Initializing Milvus Lite at: {milvus_db_path}")
            # pymilvus 2.6+ Milvus Lite 直接使用本地文件路径作为 uri
            self.milvus_client = MilvusClient(uri=milvus_db_path, db_name="default")
            milvus_uri = milvus_db_path  # 记录实际 uri 供重连
        self._client_created_at = None  # 用于追踪客户端创建时间

        # 检查collection是否存在
        if self.milvus_client.has_collection(collection_name):
            logger.info(f"Loaded existing collection: {collection_name}")
        else:
            # 创建新collection
            logger.info(f"Creating new collection: {collection_name}")
            self.milvus_client.create_collection(
                collection_name=collection_name,
                dimension=self.embedding_dim,
                metric_type="COSINE",  # 余弦相似度
                auto_id=False,
            )
            logger.info(f"Created new collection: {collection_name}")

        self.initialized = True
        self._milvus_uri = milvus_uri  # 保存 uri（远程地址或本地路径）用于重连
        logger.info(f"RAG Knowledge Agent initialized (Milvus uri: {milvus_uri})")

    async def _ensure_connection(self):
        """确保 Milvus 连接正常，如果需要则重新创建客户端"""
        try:
            # has_collection 是同步方法，不要 await
            self.milvus_client.has_collection(self.collection_name)
        except Exception as e:
            logger.warning(f"Milvus connection issue detected: {e}, reconnecting...")
            try:
                # 关闭旧连接
                if hasattr(self.milvus_client, 'close'):
                    try:
                        self.milvus_client.close()
                    except:
                        pass

                # 重新创建客户端
                self.milvus_client = MilvusClient(self._milvus_uri, db_name="default")
                logger.info("Milvus client reconnected successfully")
            except Exception as reconnect_error:
                logger.error(f"Failed to reconnect Milvus: {reconnect_error}")
                raise

    async def add_documents(self, documents: List[Dict[str, str]]) -> Dict:
        """
        添加文档到知识库

        Args:
            documents: 文档列表，每个文档包含 {'content': '内容', 'metadata': {...}}

        Returns:
            添加结果统计
        """
        if not self.initialized:
            return {"status": "error", "message": "RAG Agent not initialized"}
        
        try:
            # 确保连接正常
            await self._ensure_connection()
            # 获取当前文档总数，用于生成连续的ID
            stats = self.milvus_client.get_collection_stats(self.collection_name)
            current_count = stats.get("row_count", 0)

            # 准备数据
            data_to_insert = []

            for i, doc in enumerate(documents):
                # Milvus 要求 id 必须是 int64
                doc_id = current_count + i + 1
                content = doc['content']
                metadata = doc.get('metadata', {})

                # 生成向量
                embedding = self.embedding_model.encode(content).tolist()

                # Milvus 数据格式
                data_to_insert.append({
                    "id": doc_id,
                    "vector": embedding,
                    "content": content,
                    "metadata": json.dumps(metadata, ensure_ascii=False)  # 将metadata转为JSON字符串
                })

            # 批量插入到 Milvus
            self.milvus_client.insert(
                collection_name=self.collection_name,
                data=data_to_insert
            )

            # 获取总数
            stats = self.milvus_client.get_collection_stats(self.collection_name)
            total_count = stats.get("row_count", len(documents))

            logger.info(f"Successfully added {len(documents)} documents to knowledge base")
            return {
                "status": "success",
                "added_count": len(documents),
                "total_count": total_count
            }

        except Exception as e:
            logger.error(f"Error adding documents: {e}")
            return {"status": "error", "message": str(e)}

    async def search_knowledge(self, query: str, top_k: Optional[int] = None) -> List[Dict]:
        """
        检索知识库（带缓存穿透防御 + 检索增强）

        Args:
            query: 查询文本
            top_k: 返回top k个结果

        Returns:
            检索结果列表
        """
        if not self.initialized:
            return []

        # L1: 参数校验
        if not self._validate_query(query):
            return []

        # L3: 空值缓存命中，直接返回空结果
        if self._retrieval_config.get("enable_empty_result_cache", True) and self._is_empty_result(query):
            return []

        try:
            # 确保连接正常 + collection 已加载（避免 released 状态）
            await self._ensure_connection()
            self._ensure_collection_loaded()

            k = top_k or self._retrieval_config.get("final_top_k") or self.top_k
            # Phase 1: 内部召回放大，再后处理去重截断
            recall_k = max(k * 5, int(self._retrieval_config.get("recall_top_k", 15)))

            # L5: embedding 本地 LRU 缓存
            query_embedding = None
            if self._retrieval_config.get("enable_embedding_cache", True):
                query_embedding = self._get_cached_embedding(query)
            if query_embedding is None:
                query_embedding = self.embedding_model.encode(query).tolist()
                if self._retrieval_config.get("enable_embedding_cache", True):
                    self._cache_embedding(query, query_embedding)

            # 在 Milvus 中检索
            results = self.milvus_client.search(
                collection_name=self.collection_name,
                data=[query_embedding],
                limit=recall_k,
                output_fields=["id", "content", "metadata"]
            )

            # 格式化结果
            retrieved_docs = []
            if results and len(results) > 0:
                for hit in results[0]:
                    # 解析metadata
                    metadata_str = hit.get("entity", {}).get("metadata", "{}")
                    try:
                        metadata = json.loads(metadata_str)
                    except Exception:
                        metadata = {}

                    retrieved_docs.append({
                        'id': hit.get("entity", {}).get("id", ""),
                        'content': hit.get("entity", {}).get("content", ""),
                        'metadata': metadata,
                        'distance': hit.get("distance", 0.0)
                    })

            # 向量阈值去重
            if self._retrieval_config.get("enable_similarity_dedup", True):
                retrieved_docs = self._dedup_by_similarity(
                    retrieved_docs,
                    threshold=self._retrieval_config.get("similarity_dedup_threshold") or None,
                )

            # 父文档召回（补充来源页信息）
            if self._retrieval_config.get("enable_parent_document_recall", True):
                retrieved_docs = self._parent_document_recall(retrieved_docs)

            # 截断到最终 top_k
            final = retrieved_docs[:k]

            # L3: 标记空结果，避免后续重复穿透
            if not final and self._retrieval_config.get("enable_empty_result_cache", True):
                self._mark_empty_result(query)

            logger.info(f"Retrieved {len(final)} documents for query: {query[:50]}")
            return final

        except Exception as e:
            logger.error(f"Error searching knowledge: {e}")
            return []

    # ───────────── 检索增强辅助方法 ─────────────

    def _validate_query(self, query: str) -> bool:
        """L1: 参数校验，拦截非法 query。"""
        if not query or not isinstance(query, str):
            return False
        query = query.strip()
        return 2 <= len(query) <= 500

    def _get_cached_embedding(self, query: str):
        """L5: query -> embedding 本地 LRU 缓存读取。"""
        if query in self._query_embedding_cache:
            self._query_embedding_cache.move_to_end(query)
            return self._query_embedding_cache[query]
        return None

    def _cache_embedding(self, query: str, embedding: List[float]):
        """L5: query -> embedding 本地 LRU 缓存写入。"""
        maxsize = int(self._retrieval_config.get("embedding_cache_size", 512))
        self._query_embedding_cache[query] = embedding
        if len(self._query_embedding_cache) > maxsize:
            self._query_embedding_cache.popitem(last=False)

    def _is_empty_result(self, query: str) -> bool:
        """L3: 空值缓存判断。"""
        return hash(query) in self._empty_result_cache

    def _mark_empty_result(self, query: str):
        """L3: 标记空结果。"""
        self._empty_result_cache.add(hash(query))

    def _ensure_collection_loaded(self):
        """确保 collection 处于 loaded 状态，避免 Milvus Lite released 报错。"""
        try:
            self.milvus_client.load_collection(self.collection_name)
        except Exception:
            # 已加载或方法不支持时忽略
            pass

    def _dedup_by_similarity(self, docs: List[Dict], threshold: float = None) -> List[Dict]:
        """
        基于余弦相似度去重：
        - 计算所有结果两两相似度
        - 动态阈值 = Mean + 1.5 * Std（上限 0.92）
        - 超过阈值且后出现的 doc 被丢弃
        """
        if len(docs) <= 1:
            return docs

        try:
            embeddings = [self.embedding_model.encode(d["content"]) for d in docs]
        except Exception:
            return docs

        n = len(embeddings)
        norms = [float(np.linalg.norm(e)) for e in embeddings]
        matrix = np.zeros((n, n))
        for i in range(n):
            matrix[i][i] = 1.0
            for j in range(i + 1, n):
                if norms[i] > 0 and norms[j] > 0:
                    sim = float(np.dot(embeddings[i], embeddings[j]) / (norms[i] * norms[j]))
                    matrix[i][j] = sim
                    matrix[j][i] = sim

        mean = float(np.mean(matrix))
        std = float(np.std(matrix))
        thr = threshold or min(mean + 1.5 * std, 0.92)

        kept_indices: List[int] = []
        for i in range(n):
            is_dup = any(matrix[i][j] > thr and i > j for j in kept_indices)
            if not is_dup:
                kept_indices.append(i)

        return [docs[i] for i in kept_indices]

    def _parent_document_recall(self, docs: List[Dict]) -> List[Dict]:
        """
        父文档召回：从 metadata 补充来源页信息。
        后续可扩展为读取原始文档的 ±200 字符上下文。
        """
        for doc in docs:
            meta = doc.get("metadata", {})
            if "page" in meta:
                doc["content"] = f"[来源: 第{meta['page']}页]\n{doc['content']}"
        return docs

    def _extract_text(self, content: Any) -> str:
        """从 LLM 消息格式中提取纯文本"""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
                elif hasattr(block, "text"):
                    texts.append(block.text)
            return " ".join(texts)
        return str(content) if content else ""

    async def reply(self, x: Optional[Union[Msg, List[Msg]]] = None) -> Msg:
        """
        RAG问答主流程
        1. 接收用户查询
        2. 检索相关知识
        3. 结合知识生成答案
        """
        if not self.initialized:
            return Msg(
                name=self.name,
                content=json.dumps({
                    "status": "error",
                    "message": "RAG Agent not initialized. Please install dependencies: pip install pymilvus sentence-transformers"
                }),
                role="assistant"
            )

        if x is None:
            return Msg(name=self.name, content=json.dumps({}), role="assistant")

        # 获取用户查询
        if isinstance(x, list):
            content = x[-1].content if x else ""
        else:
            content = x.content

        # 尝试解析 JSON 输入 (来自 Orchestrator)
        user_query = content
        if isinstance(content, str) and content.strip().startswith('{'):
            try:
                data = json.loads(content)
                # 只要解析成功，就认为 content 是结构化数据，尝试提取 query
                extracted_query = ""
                if "context" in data and isinstance(data["context"], dict):
                    extracted_query = data["context"].get("rewritten_query", "")
                elif "rewritten_query" in data:
                    extracted_query = data.get("rewritten_query", "")
                
                # 使用提取到的 query（即使为空，也比 JSON 字符串好）
                user_query = extracted_query
            except:
                pass  # 解析失败则保留原字符串

        # 检索相关知识
        retrieved_docs = await self.search_knowledge(self._extract_text(user_query))

        if not retrieved_docs:
            result = {
                "status": "no_knowledge",
                "query": user_query,
                "answer": "抱歉，我在知识库中没有找到相关信息。",
                "retrieved_documents": []
            }
            return Msg(name=self.name, content=json.dumps(result, ensure_ascii=False), role="assistant")

        # 构建知识上下文
        knowledge_context = "\n\n".join([
            f"【知识片段{i+1}】\n{doc['content']}"
            for i, doc in enumerate(retrieved_docs)
        ])

        # 如果有LLM，使用LLM生成答案
        if self.model:
            # 动态读取 Prompt 指令 (Progressive Disclosure)
            skill_instruction = self.skill_loader.get_skill_content("ask-question")
            if not skill_instruction:
                skill_instruction = "请基于知识库中的信息回答用户的问题。"

            prompt = f"""你是一个工单诊断专家，精通商户管理和故障排查。请严格基于以下知识库中的信息回答用户的问题。

【用户问题】
{user_query}

【知识库信息】
{knowledge_context}

【任务说明】
{skill_instruction}

【重要约束】
1. 如果【知识库信息】中没有包含回答用户问题所需的信息，请直接回答"抱歉，知识库中没有找到相关信息"，不要尝试根据你自己的知识编造答案。
2. 即使问题很基础，如果知识库里没写，就说不知道。
3. 请以专业、客观的语气回答，给出具体的排查步骤和归属方判定。
"""

            try:
                # 调用LLM生成答案
                messages = [
                    {"role": "system", "content": "你是一个工单诊断专家，精通商户管理、订单排查、资产分配和结算对账。"},
                    {"role": "user", "content": prompt}
                ]
                response = await self.model(messages)

                # 获取响应内容 - 处理异步生成器
                answer = ""
                if hasattr(response, '__aiter__'):
                    # 异步生成器，需要迭代获取内容
                    async for chunk in response:
                        if isinstance(chunk, str):
                            answer = chunk
                        elif hasattr(chunk, 'content'):
                            if isinstance(chunk.content, str):
                                answer = chunk.content
                            elif isinstance(chunk.content, list):
                                for item in chunk.content:
                                    if isinstance(item, dict) and item.get('type') == 'text':
                                        answer = item.get('text', '')
                elif hasattr(response, 'text'):
                    answer = response.text
                elif hasattr(response, 'content'):
                    answer = response.content
                elif isinstance(response, dict) and 'content' in response:
                    answer = response['content']
                else:
                    answer = str(response) if response else "无法生成答案"

                if not answer:
                    answer = "无法生成答案"
                
                # 清理 LLM 可能输出的 JSON 格式
                answer_str = answer.strip()
                if answer_str.startswith("{") and answer_str.endswith("}"):
                    try:
                        json_obj = json.loads(answer_str)
                        # 如果 LLM 输出了 {"answer": "..."} 或 {"content": "..."}
                        if isinstance(json_obj, dict):
                            answer = json_obj.get("answer") or json_obj.get("content") or answer
                    except:
                        pass

            except Exception as e:
                logger.error(f"Error generating answer with LLM: {e}")
                answer = f"知识库中找到相关信息，但生成答案时出错：{str(e)}"
        else:
            # 如果没有LLM，直接返回检索到的知识
            answer = "以下是知识库中的相关信息：\n\n" + knowledge_context

        result = {
            "status": "success",
            "query": user_query,
            "answer": answer,
            "retrieved_documents": [
                {
                    "content": doc['content'][:200] + "..." if len(doc['content']) > 200 else doc['content'],
                    "metadata": doc['metadata']
                }
                for doc in retrieved_docs
            ]
        }

        return Msg(name=self.name, content=json.dumps(result, ensure_ascii=False), role="assistant")

    async def get_stats(self) -> Dict:
        """获取知识库统计信息"""
        if not self.initialized:
            return {"status": "error", "message": "Not initialized"}

        try:
            # 确保连接正常
            await self._ensure_connection()
            stats = self.milvus_client.get_collection_stats(self.collection_name)
            return {
                "status": "success",
                "collection_name": self.collection_name,
                "total_documents": stats.get("row_count", 0),
                "knowledge_base_path": str(self.knowledge_base_path)
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def close(self):
        """关闭 Milvus 连接"""
        if hasattr(self, 'milvus_client'):
            try:
                if hasattr(self.milvus_client, 'close'):
                    self.milvus_client.close()
                    logger.info("Milvus client closed successfully")
            except Exception as e:
                logger.warning(f"Error closing Milvus client: {e}")

    def __del__(self):
        """析构函数，确保资源被释放"""
        self.close()
