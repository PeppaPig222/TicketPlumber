"""
诊断模式库 (Diagnosis Pattern Store)
长期记忆：存储成功诊断路径模板、证据链关联模式和根因推断规则
存储：Milvus 向量库（复用现有 RAG 基础设施）
"""
from typing import Dict, Any, List, Optional
import json
import logging
import asyncio

logger = logging.getLogger(__name__)


class DiagnosisPatternStore:
    """
    诊断模式库：基于向量检索沉淀历史诊断经验
    - 诊断成功后写入模式
    - 新工单进入时检索相似模式
    - 供 Agent 参考历史成功路径
    """

    COLLECTION_NAME = "diagnosis_patterns"

    def __init__(
        self,
        milvus_client,
        embedding_model,
        embedding_dim: int = 384,
        metric_type: str = "COSINE",
    ):
        """
        初始化诊断模式库

        Args:
            milvus_client: MilvusClient 实例（复用已有连接）
            embedding_model: 向量化模型（复用 BGE/SentenceTransformer）
            embedding_dim: 向量维度
            metric_type: 相似度度量方式
        """
        self.milvus_client = milvus_client
        self.embedding_model = embedding_model
        self.embedding_dim = embedding_dim
        self.metric_type = metric_type

        self._ensure_collection()
        logger.info("Diagnosis pattern store initialized")

    def _ensure_collection(self):
        """确保 collection 存在"""
        try:
            if not self.milvus_client.has_collection(self.COLLECTION_NAME):
                self.milvus_client.create_collection(
                    collection_name=self.COLLECTION_NAME,
                    dimension=self.embedding_dim,
                    metric_type=self.metric_type,
                    auto_id=True,
                )
                logger.info(
                    f"Created diagnosis patterns collection: {self.COLLECTION_NAME}"
                )
        except Exception as e:
            logger.exception(f"Failed to ensure diagnosis patterns collection: {e}")

    @staticmethod
    def _build_pattern_text(diagnosis_result: Dict[str, str]) -> str:
        """将诊断结果构建为可向量化的文本"""
        issue_type = diagnosis_result.get("issue_type", "")
        responsible_party = diagnosis_result.get("responsible_party", "")
        root_cause = diagnosis_result.get("root_cause", "")
        summary = diagnosis_result.get("summary", "")

        # 拼接为结构化文本，便于语义匹配
        parts = [
            f"问题类型: {issue_type}" if issue_type else "",
            f"责任归属: {responsible_party}" if responsible_party else "",
            f"根因: {root_cause}" if root_cause else "",
            f"摘要: {summary}" if summary else "",
        ]
        return " | ".join([p for p in parts if p])

    async def save_pattern(
        self,
        diagnosis_result: Dict[str, Any],
        evidence_chain: Optional[List[str]] = None,
        status: str = "verified",
    ) -> Optional[str]:
        """
        保存一条诊断模式到向量库

        Args:
            diagnosis_result: 诊断结果字典，需包含 issue_type / responsible_party / root_cause / summary
            evidence_chain: 证据链（可选），如 ["query_order", "check_config", "trace_api"]
            status: 模式状态，verified（已验证）/ pending（待审核）/ deprecated（已废弃）

        Returns:
            插入实体的主键ID，失败返回 None
        """
        if not self.milvus_client or not self.embedding_model:
            logger.warning("DiagnosisPatternStore not fully initialized, skip saving")
            return None

        pattern_text = self._build_pattern_text(diagnosis_result)
        if not pattern_text:
            logger.warning("Empty pattern text, skip saving")
            return None

        try:
            embedding = await asyncio.to_thread(self.embedding_model.encode, pattern_text)

            metadata = {
                "issue_type": diagnosis_result.get("issue_type", ""),
                "responsible_party": diagnosis_result.get("responsible_party", ""),
                "root_cause": diagnosis_result.get("root_cause", ""),
                "summary": diagnosis_result.get("summary", ""),
                "ticket_id": diagnosis_result.get("ticket_id", ""),
                "merchant_id": diagnosis_result.get("merchant_id", ""),
                "evidence_chain": json.dumps(evidence_chain or [], ensure_ascii=False),
                "status": status,
                "created_at": diagnosis_result.get(
                    "timestamp", datetime_now_iso()
                ),
            }

            data_to_insert = [
                {
                    "vector": embedding.tolist() if hasattr(embedding, "tolist") else list(embedding),
                    "content": pattern_text,
                    "metadata": json.dumps(metadata, ensure_ascii=False),
                }
            ]

            result = await asyncio.to_thread(
                self.milvus_client.insert,
                collection_name=self.COLLECTION_NAME,
                data=data_to_insert,
            )

            logger.info(f"Saved diagnosis pattern: {pattern_text[:60]}...")
            return result[0] if isinstance(result, list) else None

        except Exception as e:
            logger.exception(f"Failed to save diagnosis pattern: {e}")
            return None

    async def find_similar(
        self,
        query: str,
        k: int = 3,
        status_filter: Optional[str] = "verified",
    ) -> List[Dict[str, Any]]:
        """
        检索与当前查询最相似的诊断模式

        Args:
            query: 查询文本，如 "商户反馈结算金额少了"
            k: 返回 Top-K 条
            status_filter: 按模式状态过滤，None 表示不过滤

        Returns:
            相似模式列表
        """
        if not self.milvus_client or not self.embedding_model:
            logger.debug("DiagnosisPatternStore not initialized, return empty results")
            return []

        try:
            query_embedding = await asyncio.to_thread(self.embedding_model.encode, query)

            results = await asyncio.to_thread(
                self.milvus_client.search,
                collection_name=self.COLLECTION_NAME,
                data=[query_embedding.tolist() if hasattr(query_embedding, "tolist") else list(query_embedding)],
                limit=k,
                output_fields=["id", "content", "metadata"],
            )

            patterns = []
            for hits in results:
                for hit in hits:
                    metadata = hit.get("entity", {}).get("metadata", "{}")
                    try:
                        metadata = json.loads(metadata)
                    except Exception:
                        metadata = {}

                    if status_filter and metadata.get("status") != status_filter:
                        continue

                    patterns.append(
                        {
                            "id": hit.get("id"),
                            "score": hit.get("distance", 0.0),
                            "content": hit.get("entity", {}).get("content", ""),
                            "issue_type": metadata.get("issue_type", ""),
                            "responsible_party": metadata.get("responsible_party", ""),
                            "root_cause": metadata.get("root_cause", ""),
                            "summary": metadata.get("summary", ""),
                            "evidence_chain": json.loads(
                                metadata.get("evidence_chain", "[]")
                            ),
                            "ticket_id": metadata.get("ticket_id", ""),
                        }
                    )

            logger.debug(f"Found {len(patterns)} similar diagnosis patterns")
            return patterns

        except Exception as e:
            logger.exception(f"Failed to find similar diagnosis patterns: {e}")
            return []

    async def get_stats(self) -> Dict[str, Any]:
        """获取模式库统计"""
        try:
            stats = await asyncio.to_thread(
                self.milvus_client.get_collection_stats,
                self.COLLECTION_NAME,
            )
            return {
                "collection": self.COLLECTION_NAME,
                "pattern_count": stats.get("row_count", 0),
            }
        except Exception as e:
            logger.exception(f"Failed to get pattern store stats: {e}")
            return {"collection": self.COLLECTION_NAME, "pattern_count": 0}


def datetime_now_iso() -> str:
    """获取当前 ISO 时间字符串"""
    from datetime import datetime
    return datetime.now().isoformat()
