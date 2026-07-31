"""
DiagBot Ticket Diagnosis System - Agents Package
"""
# 这里的导入主要是为了向后兼容，或者作为类型提示
# 实际的加载现在通过 lazy_agent_registry 动态进行
from .intention_agent import IntentionAgent
from .orchestration_agent import OrchestrationAgent
from .lazy_agent_registry import LazyAgentRegistry
from .code_agent import CodeAgent
from .operation_agent import OperationAgent
from .data_agent import DataAgent
from .resolution_agent import ResolutionAgent

__all__ = [
    'IntentionAgent',
    'OrchestrationAgent',
    'LazyAgentRegistry',
    'CodeAgent',
    'OperationAgent',
    'DataAgent',
    'ResolutionAgent',
]
