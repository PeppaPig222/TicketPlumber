"""
pydantic-settings 配置管理

支持：
- .env 文件加载
- 环境变量覆盖（按前缀分组）
- 开发 / 测试 / 生产环境区分

环境变量前缀：
- DIAG_LLM_*     → LLM 配置
- DIAG_SYS_*     → 系统配置
- DIAG_RAG_*     → RAG 配置
- DIAG_RES_*     → 韧性/重试/熔断配置
- DIAG_MEM_*     → 记忆系统配置
"""

from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    """LLM 配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="DIAG_LLM_",
        extra="ignore",
    )

    api_key: str = Field(default="API_KEY", description="LLM API Key")
    model_name: str = Field(default="Model_Name", description="LLM 模型名称")
    base_url: str = Field(
        default="https://ark.cn-beijing.volces.com/api/v3",
        description="LLM Base URL",
    )
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=8192, gt=0)


class SystemSettings(BaseSettings):
    """系统配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="DIAG_SYS_",
        extra="ignore",
    )

    enable_llm: bool = Field(default=True, description="是否启用 LLM")
    enable_llm_autonomy: bool = Field(
        default=False,
        description="是否启用专业 Agent 的 LLM 自主决策（ReAct/Plan-and-Execute）。默认关闭以保持确定性；需真实 LLM API 与真实数据支撑",
    )
    log_level: str = Field(default="INFO", description="日志级别")
    max_retries: int = Field(default=3, ge=0)
    timeout: int = Field(default=60, gt=0)
    env: str = Field(
        default="development",
        description="运行环境: development / testing / production",
    )

    @property
    def is_development(self) -> bool:
        return self.env.lower() == "development"

    @property
    def is_testing(self) -> bool:
        return self.env.lower() == "testing"

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production"


class RAGSettings(BaseSettings):
    """RAG 知识库配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="DIAG_RAG_",
        extra="ignore",
    )

    embedding_model: str = Field(
        default="data/models/bge-small-zh-v1.5",
        description="嵌入模型路径或 HuggingFace ID",
    )
    enable_intention_fallback: bool = Field(
        default=True,
        description="IntentionAgent 规则识别失败时是否启用 RAG fallback",
    )
    enable_resolution_evidence: bool = Field(
        default=True,
        description="ResolutionAgent 判责阶段是否启用 RAG 证据补充",
    )
    min_similarity_threshold: float = Field(
        default=0.55,
        ge=0.0,
        le=1.0,
        description="RAG fallback 采纳最小相似度阈值",
    )
    enable_query_rewrite: bool = Field(
        default=True,
        description="是否启用基于记忆上下文的 RAG query rewrite",
    )
    enable_session_cache: bool = Field(
        default=True,
        description="是否启用 session 内 RAG 检索结果缓存",
    )
    enable_unified_retrieval: bool = Field(
        default=True,
        description="是否启用知识库 + 诊断模式库统一检索",
    )
    cache_ttl_seconds: int = Field(
        default=300,
        ge=0,
        description="RAG session 缓存 TTL（秒），当前按 session 生命周期管理",
    )

    # ── 检索增强：缓存层 ──
    enable_embedding_cache: bool = Field(
        default=True,
        description="是否启用 query embedding 本地 LRU 缓存",
    )
    embedding_cache_size: int = Field(
        default=512,
        ge=1,
        description="embedding 缓存最大条目数",
    )
    enable_empty_result_cache: bool = Field(
        default=True,
        description="是否启用空结果缓存，避免无效 query 反复穿透",
    )

    # ── 检索增强：检索层 ──
    recall_top_k: int = Field(
        default=15,
        ge=1,
        description="Milvus 内部召回数量（去重前）",
    )
    final_top_k: int = Field(
        default=3,
        ge=1,
        description="返回给 Agent 的最终结果数量",
    )
    enable_similarity_dedup: bool = Field(
        default=True,
        description="是否启用召回结果向量阈值去重",
    )
    similarity_dedup_threshold: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="去重余弦相似度阈值；0 表示动态阈值 Mean+1.5σ（上限 0.92）",
    )
    enable_parent_document_recall: bool = Field(
        default=True,
        description="是否启用父文档召回（补充来源页信息）",
    )

    # ── 检索增强：知识库构建层 ──
    enable_md5_dedup: bool = Field(
        default=True,
        description="知识库写入时是否启用 MD5 指纹去重",
    )
    enable_boundary_dedup: bool = Field(
        default=True,
        description="知识库写入时是否启用相邻 chunk 边界重叠去重",
    )
    boundary_overlap_threshold: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="相邻 chunk 边界重叠去重阈值",
    )


class MemorySettings(BaseSettings):
    """记忆系统存储配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="DIAG_MEM_",
        extra="ignore",
    )

    backend: str = Field(
        default="local",
        description="记忆存储后端: local / redis / hybrid",
    )
    storage_path: str = Field(
        default="data/memory",
        description="local backend 存储路径",
    )

    # 短期记忆
    short_term_max_turns: int = Field(
        default=100,
        ge=1,
        description="短期记忆最大保存轮数",
    )
    short_term_ttl_seconds: int = Field(
        default=3600,
        ge=0,
        description="短期记忆 TTL（秒）",
    )

    # Redis 配置（backend=redis 时使用）
    redis_host: str = Field(default="localhost")
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_db: int = Field(default=0, ge=0)
    redis_password: Optional[str] = Field(default=None)


class ResilienceSettings(BaseSettings):
    """连接与可用性：重试、熔断、健康检查"""

    max_retries: int = Field(default=3, ge=0)
    retry_base_delay_sec: float = Field(default=1.0, gt=0)
    retry_max_delay_sec: float = Field(default=30.0, gt=0)
    circuit_failure_threshold: int = Field(default=5, gt=0)
    circuit_recovery_timeout_sec: float = Field(default=60.0, gt=0)
    circuit_half_open_successes: int = Field(default=2, gt=0)
    health_check_timeout_sec: float = Field(default=10.0, gt=0)
    skill_timeout_sec: float = Field(default=5.0, gt=0)


class SchedulingSettings(BaseSettings):
    """调度策略矩阵配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="DIAG_SCH_",
        extra="ignore",
    )

    enable_strategy_matrix: bool = Field(
        default=True,
        description="是否启用策略矩阵调度",
    )
    enable_hypothesis_routing: bool = Field(
        default=False,
        description="是否启用假设驱动路由（扫描黑板 pending hypothesis，merge 去重追加候选 Agent）",
    )
    enable_basic_info_parallel: bool = Field(
        default=True,
        description="基础信息查询统一并行策略",
    )
    enable_deep_log_conditional: bool = Field(
        default=True,
        description="深度日志追踪条件触发策略",
    )
    deep_log_trigger_scenarios: List[str] = Field(
        default_factory=lambda: ["order_status_anomaly"],
        description="触发深度日志追踪的场景列表",
    )
    deep_log_required_entities: List[str] = Field(
        default_factory=lambda: ["order_id"],
        description="触发深度日志追踪所需的实体",
    )
    enable_cross_domain_validation: bool = Field(
        default=True,
        description="跨域交叉验证依赖调度策略",
    )
    cross_domain_resolution_priority: int = Field(
        default=2,
        description="ResolutionAgent 在交叉验证阶段的优先级",
    )
    enable_rag_business_parallel: bool = Field(
        default=True,
        description="RAG 与业务 Skill 并行执行策略",
    )
    rag_agent_name: str = Field(
        default="RAGKnowledgeAgent",
        description="RAG Agent 在调度矩阵中的名称",
    )
    rag_parallel_rounds: List[int] = Field(
        default_factory=lambda: [2, 3],
        description="RAG 与业务 Skill 并行的轮次",
    )


class Settings(BaseSettings):
    """全局配置入口"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="DiagBot 工单智能诊断助手")
    project_name: str = Field(default="diagbot-ticket-diagnosis")
    project_display_name: str = Field(default="小哈工单智能诊断助手")

    llm: LLMSettings = LLMSettings()
    system: SystemSettings = SystemSettings()
    rag: RAGSettings = RAGSettings()
    memory: MemorySettings = MemorySettings()
    resilience: ResilienceSettings = ResilienceSettings()
    scheduling: SchedulingSettings = SchedulingSettings()


# 全局单例
settings = Settings()
