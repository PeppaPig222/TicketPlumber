"""
记忆管理器 (Memory Manager)
统一管理三层记忆，提供简单的API
"""
from typing import Dict, Any, List, Optional
from .short_term_memory import ShortTermMemory
from .long_term_memory import LongTermMemory
from .merchant_profile_store import MerchantProfileStore
from .diagnosis_pattern_store import DiagnosisPatternStore
import logging
import json

logger = logging.getLogger(__name__)


class MemoryManager:
    """
    记忆管理器：统一管理三层记忆
    - 短期记忆：最近对话（会话级，100轮）
    - 长期记忆：用户偏好和诊断历史（跨会话）
    - 商户画像：商户级中期记忆（统计/归属倾向/标签）
    - 诊断模式库：长期向量记忆（成功诊断路径模板）
    """

    def __init__(
        self,
        user_id: str,
        session_id: str,
        storage_path: str = "data/memory",
        llm_model=None,
        merchant_id: str = None,
        milvus_client=None,
        embedding_model=None,
        rag_agent=None,
    ):
        """
        初始化记忆管理器

        Args:
            user_id: 用户ID
            session_id: 会话ID
            storage_path: 长期记忆存储路径
            llm_model: LLM模型实例（用于总结长期记忆）
            merchant_id: 商户ID（可选，用于商户画像）
            milvus_client: MilvusClient 实例（可选，用于诊断模式库）
            embedding_model: 向量化模型（可选，用于诊断模式库）
            rag_agent: RAG 知识库 Agent 实例（可选，用于统一检索入口）
        """
        self.user_id = user_id
        self.session_id = session_id
        self.llm_model = llm_model
        self.merchant_id = merchant_id
        self.rag_agent = rag_agent

        # 初始化各层记忆
        self.short_term = ShortTermMemory(max_turns=100)
        self.long_term = LongTermMemory(user_id, storage_path)
        self.merchant_profile = (
            MerchantProfileStore(merchant_id, storage_path) if merchant_id else None
        )
        self.pattern_store = (
            DiagnosisPatternStore(milvus_client, embedding_model)
            if milvus_client and embedding_model
            else None
        )

        # Session 内 RAG 检索结果缓存，避免单次诊断中重复调用 RAG
        self._rag_cache: Dict[str, Any] = {}

        logger.info(
            f"Memory manager initialized for user {user_id}, session {session_id}, "
            f"merchant={merchant_id}, rag_agent={rag_agent is not None}"
        )

    # ========== 短期记忆操作 ==========

    def add_message(self, role: str, content: str, metadata: Dict = None):
        """
        添加消息到短期记忆和长期记忆

        Args:
            role: 角色 (user/assistant)
            content: 消息内容
            metadata: 元数据
        """
        # 添加到短期记忆（当前会话）
        self.short_term.add_message(role, content, metadata)

        # 同时添加到长期记忆（跨会话持久化）
        self.long_term.add_chat_message(role, content, self.session_id)

    # ========== 长期记忆操作 ==========
    # 注意：大部分方法直接使用 self.short_term 和 self.long_term 即可，无需封装

    def set_merchant_id(self, merchant_id: str):
        """
        延迟设置/切换商户号，用于 ticket 加载后创建商户画像

        Args:
            merchant_id: 商户ID
        """
        if not merchant_id:
            return
        if self.merchant_id == merchant_id and self.merchant_profile is not None:
            return

        self.merchant_id = merchant_id
        self.merchant_profile = MerchantProfileStore(
            merchant_id, self.long_term.storage_path
        )
        logger.info(f"Memory manager switched to merchant: {merchant_id}")

    def get_merchant_context(self) -> str:
        """
        获取用于 Agent prompt 的商户画像文本

        Returns:
            格式化字符串，未初始化时返回空字符串
        """
        if self.merchant_profile is None:
            return ""
        return self.merchant_profile.get_context_for_agent()

    # ========== 综合查询 ==========

    def get_full_context(self) -> Dict[str, Any]:
        """
        获取完整上下文（三层记忆）

        Returns:
            完整上下文字典
        """
        context = {
            "short_term": {
                "recent_dialogue": self.short_term.get_recent_context(5),
                "context_string": self.short_term.get_context_string(5),
                "statistics": self.short_term.get_statistics()
            },
            "long_term": {
                "preferences": self.long_term.get_preference(),
                "chat_history": self.long_term.get_chat_history(10),
                "diagnosis_history": self.long_term.get_diagnosis_history(5),
                "common_issue_types": self.long_term.get_common_issue_types(3),
                "statistics": self.long_term.get_statistics()
            },
            "merchant_profile": None,
            "similar_patterns": [],
        }

        # 商户画像（中期记忆）
        if self.merchant_profile:
            context["merchant_profile"] = self.merchant_profile.get_profile()

        return context

    async def record_diagnosis(self, diagnosis_result: Dict[str, Any]):
        """
        统一收口：一次诊断完成后写入所有相关记忆层

        Args:
            diagnosis_result: 诊断结果字典，需包含
                ticket_id / merchant_id / issue_type / responsible_party / root_cause / summary / timestamp
        """
        ticket_id = diagnosis_result.get("ticket_id", "")
        merchant_id = diagnosis_result.get("merchant_id", "") or self.merchant_id
        issue_type = diagnosis_result.get("issue_type", "")
        responsible_party = diagnosis_result.get("responsible_party", "")
        root_cause = diagnosis_result.get("root_cause", "")
        summary = diagnosis_result.get("summary", "")
        timestamp = diagnosis_result.get("timestamp")

        # 1. 长期记忆：用户维度诊断历史
        self.long_term.save_diagnosis_history({
            "ticket_id": ticket_id,
            "merchant_id": merchant_id,
            "issue_type": issue_type,
            "responsible_party": responsible_party,
            "root_cause": root_cause,
            "summary": summary,
            "timestamp": timestamp,
        })

        # 2. 商户画像：商户维度聚合统计
        if self.merchant_profile and merchant_id:
            self.merchant_profile.record_diagnosis(
                ticket_id=ticket_id,
                issue_type=issue_type,
                responsible_party=responsible_party,
                root_cause=root_cause,
                timestamp=timestamp,
            )

        # 3. 诊断模式库：向量沉淀成功路径
        if self.pattern_store:
            await self.pattern_store.save_pattern(diagnosis_result)

        logger.info(f"Recorded diagnosis across memory layers: {ticket_id}")

    async def find_similar_patterns(self, query: str, k: int = 3) -> List[Dict[str, Any]]:
        """
        从诊断模式库检索相似历史模式

        Args:
            query: 当前问题描述
            k: Top-K

        Returns:
            相似模式列表
        """
        if not self.pattern_store:
            return []
        return await self.pattern_store.find_similar(query, k=k)

    # ========== RAG 统一接入 ==========

    def _extract_text(self, response) -> str:
        """从 LLM 响应中提取文本内容。"""
        if hasattr(response, 'content'):
            if isinstance(response.content, str):
                return response.content
            if isinstance(response.content, list):
                for item in response.content:
                    if isinstance(item, dict) and item.get('type') == 'text':
                        return item.get('text', '')
        if isinstance(response, str):
            return response
        return str(response)

    async def rewrite_query_for_rag(
        self,
        current_query: str,
        collected_facts: Dict[str, Any] = None,
    ) -> str:
        """基于短期记忆和已确认事实改写 query，生成独立检索 query。"""
        from config import RAG_CONFIG

        if not self.llm_model or not RAG_CONFIG.get("enable_query_rewrite", True):
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

    def _rag_cache_key(self, query: str) -> str:
        return f"{self.session_id}:{hash(query)}"

    def get_rag_cache(self, query: str) -> Optional[Dict]:
        return self._rag_cache.get(self._rag_cache_key(query))

    def set_rag_cache(self, query: str, result: Dict):
        self._rag_cache[self._rag_cache_key(query)] = result

    async def search_knowledge(
        self,
        query: str,
        collected_facts: Dict[str, Any] = None,
        use_rewrite: bool = True,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """
        统一的 RAG 检索入口：
        1. query rewrite（可选）
        2. session 缓存命中则复用
        3. 调用 RAGKnowledgeAgent
        4. 写入缓存
        """
        from config import RAG_CONFIG
        from agentscope.message import Msg

        if use_rewrite and RAG_CONFIG.get("enable_query_rewrite", True):
            search_query = await self.rewrite_query_for_rag(query, collected_facts)
        else:
            search_query = query

        if use_cache and RAG_CONFIG.get("enable_session_cache", True):
            cached = self.get_rag_cache(search_query)
            if cached:
                return cached

        if not self.rag_agent:
            return {"status": "no_agent", "answer": "", "retrieved_documents": []}

        try:
            msg = Msg(name="orchestrator", content=search_query, role="user")
            response = await self.rag_agent.reply(msg)
            result = json.loads(response.content)
        except Exception as e:
            logger.warning(f"MemoryManager search_knowledge failed: {e}")
            result = {"status": "error", "answer": "", "retrieved_documents": []}

        if use_cache and RAG_CONFIG.get("enable_session_cache", True):
            self.set_rag_cache(search_query, result)

        return result

    async def unified_retrieval(
        self,
        query: str,
        collected_facts: Dict[str, Any] = None,
        k: int = 3,
    ) -> Dict[str, Any]:
        """
        统一检索：RAG 知识库 + 诊断模式库
        """
        from config import RAG_CONFIG

        rewritten = query
        if RAG_CONFIG.get("enable_query_rewrite", True):
            rewritten = await self.rewrite_query_for_rag(query, collected_facts)

        knowledge_result = await self.search_knowledge(
            rewritten,
            collected_facts=collected_facts,
            use_rewrite=False,
            use_cache=True,
        )

        similar_patterns = []
        if RAG_CONFIG.get("enable_unified_retrieval", True):
            similar_patterns = await self.find_similar_patterns(rewritten, k=k)

        return {
            "knowledge_docs": knowledge_result.get("retrieved_documents", []),
            "similar_patterns": similar_patterns,
            "rewritten_query": rewritten,
        }

    def get_context_for_agent(self, long_term_summary: str = None) -> str:
        """
        获取用于Agent的上下文字符串

        Args:
            long_term_summary: 长期记忆总结（可选，需提前调用 get_long_term_summary_async）

        Returns:
            格式化的上下文字符串
        """
        lines = []

        # 长期记忆总结（历史会话）
        if long_term_summary:
            lines.append("【历史会话总结】")
            lines.append(long_term_summary)
            lines.append("")

        # 用户偏好
        prefs = self.long_term.get_preference()
        has_prefs = any(v for v in prefs.values() if v)
        if has_prefs:
            lines.append("【用户偏好】")
            for key, value in prefs.items():
                if value:
                    lines.append(f"- {key}: {value}")
            lines.append("")

        # 短期记忆（当前会话）
        context_str = self.short_term.get_context_string(3)
        if context_str != "无历史对话":
            lines.append("【当前会话对话】")
            lines.append(context_str)
            lines.append("")

        return "\n".join(lines) if lines else "无上下文信息"

    # ========== 会话管理 ==========

    def end_session(self):
        """结束会话"""
        self.short_term.clear()
        logger.info(f"Session ended: {self.session_id}")

    async def get_long_term_summary_async(self, max_messages: int = 50) -> str:
        """
        使用LLM总结长期聊天历史（异步版本）

        Args:
            max_messages: 最多总结的消息数量

        Returns:
            总结后的文本
        """
        if not self.llm_model:
            return ""

        # 获取长期聊天历史（排除当前会话）
        all_history = self.long_term.get_chat_history(limit=max_messages)
        history_from_other_sessions = [
            msg for msg in all_history
            if msg.get("session_id") != self.session_id
        ]

        # 获取诊断历史
        diagnosis_history = self.long_term.get_diagnosis_history(limit=20)

        # 如果既没有聊天记录也没有诊断记录，直接返回
        if not history_from_other_sessions and not diagnosis_history:
            return ""

        # 构建聊天记录文本
        history_text = []
        for msg in history_from_other_sessions[-max_messages:]:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            timestamp = msg.get("timestamp", "")
            history_text.append(f"[{timestamp}] {role}: {content}")

        history_str = "\n".join(history_text) if history_text else "（无聊天记录）"

        # 构建诊断历史文本
        diagnosis_text = []
        for diagnosis in diagnosis_history:
            timestamp = diagnosis.get("timestamp", "")
            ticket_id = diagnosis.get("ticket_id", "未知工单")
            issue_type = diagnosis.get("issue_type", "未知问题")
            responsible_party = diagnosis.get("responsible_party", "待判定")
            summary = diagnosis.get("summary", "")
            diagnosis_text.append(
                f"[{timestamp}] {ticket_id} / {issue_type} / 归属: {responsible_party} / 摘要: {summary}"
            )

        diagnosis_str = "\n".join(diagnosis_text) if diagnosis_text else "（无诊断记录）"

        # 使用LLM总结
        summarization_prompt = f"""请总结以下历史信息中的关键内容，包括：
1. 用户在工单诊断中关注的问题类型
2. 历史工单的根因与责任归属
3. 反复出现的异常模式或处理建议
4. 其他重要的上下文信息

【历史聊天记录】
{history_str}

【历史诊断记录】
{diagnosis_str}

请用简洁的语言总结（不超过200字）："""

        try:
            # 调用模型（异步调用）
            response = await self.llm_model([{"role": "user", "content": summarization_prompt}])

            # 处理异步生成器响应
            summary = ""
            if hasattr(response, '__aiter__'):
                # 异步生成器，需要迭代获取内容
                async for chunk in response:
                    if isinstance(chunk, str):
                        summary = chunk
                    elif hasattr(chunk, 'content'):
                        if isinstance(chunk.content, str):
                            summary = chunk.content
                        elif isinstance(chunk.content, list):
                            for item in chunk.content:
                                if isinstance(item, dict) and item.get('type') == 'text':
                                    summary = item.get('text', '')
            elif hasattr(response, 'content'):
                summary = str(response.content)
            else:
                summary = str(response)

            logger.info(f"Generated long-term memory summary ({len(summary)} chars)")
            return summary.strip()

        except Exception as e:
            logger.error(f"Failed to generate long-term summary: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return ""

    def get_long_term_summary(self, max_messages: int = 50) -> str:
        """
        使用LLM总结长期聊天历史（同步版本）

        Args:
            max_messages: 最多总结的消息数量

        Returns:
            总结后的文本
        """
        import asyncio

        # 检查是否在事件循环中
        try:
            asyncio.get_running_loop()
            # 已经在事件循环中，不能使用 asyncio.run
            logger.warning("get_long_term_summary called from async context, please use get_long_term_summary_async instead")
            return ""
        except RuntimeError:
            # 没有运行的事件循环，可以使用 asyncio.run
            return asyncio.run(self.get_long_term_summary_async(max_messages))
