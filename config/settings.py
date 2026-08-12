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
"""

from typing import List

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


class ResilienceSettings(BaseSettings):
    """连接与可用性：重试、熔断、健康检查"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="DIAG_RES_",
        extra="ignore",
    )

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
    resilience: ResilienceSettings = ResilienceSettings()
    scheduling: SchedulingSettings = SchedulingSettings()


# 全局单例
settings = Settings()
