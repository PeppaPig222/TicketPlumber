"""
Configuration for the DiagBot Ticket Diagnosis System

本文件作为兼容层保留，从 config.settings 导出旧版字典配置，
确保现有代码中 `from config import LLM_CONFIG` 等引用继续生效。

新增代码建议直接使用：
    from config.settings import settings
    api_key = settings.llm.api_key
"""

from config.settings import settings

APP_CONFIG = {
    "app_name": settings.app_name,
    "project_name": settings.project_name,
    "project_display_name": settings.project_display_name,
}

# LLM Configuration
LLM_CONFIG = settings.llm.model_dump()

# System Configuration
SYSTEM_CONFIG = settings.system.model_dump()

# RAG 知识库配置
RAG_CONFIG = settings.rag.model_dump()

# 连接与可用性：重试、熔断、健康检查
RESILIENCE_CONFIG = settings.resilience.model_dump()

# 导出 Settings 单例，方便新代码直接访问
__all__ = [
    "APP_CONFIG",
    "LLM_CONFIG",
    "SYSTEM_CONFIG",
    "RAG_CONFIG",
    "RESILIENCE_CONFIG",
    "settings",
]
