"""
记忆存储层抽象基类
为短期记忆、长期记忆、商户画像提供统一接口，使底层存储可替换为
InMemory / JSON / Redis / PostgreSQL / MongoDB 等企业级实现。
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BaseShortTermMemory(ABC):
    """短期记忆抽象：会话级，关注最近 N 轮对话。"""

    @abstractmethod
    def add_message(self, role: str, content: str, metadata: Dict = None):
        """添加一条消息。"""
        ...

    @abstractmethod
    def get_recent_context(self, n_turns: int = None) -> List[Dict[str, Any]]:
        """获取最近 n 轮对话（一轮 = user + assistant 两条消息）。"""
        ...

    @abstractmethod
    def get_context_string(self, n_turns: int = 5) -> str:
        """获取最近 n 轮对话的格式化字符串。"""
        ...

    @abstractmethod
    def clear(self):
        """清空当前会话短期记忆。"""
        ...

    @abstractmethod
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息。"""
        ...


class BaseLongTermMemory(ABC):
    """长期记忆抽象：用户级，跨会话持久化偏好、聊天历史、诊断历史。"""

    @abstractmethod
    def save_preference(self, pref_type: str, value: Any):
        """保存或更新用户偏好。"""
        ...

    @abstractmethod
    def get_preference(self, pref_type: str = None) -> Any:
        """获取用户偏好；pref_type 为 None 时返回全部偏好的字典。"""
        ...

    @abstractmethod
    def add_chat_message(self, role: str, content: str, session_id: str = None):
        """添加聊天消息到长期历史。"""
        ...

    @abstractmethod
    def get_chat_history(self, limit: int = None, session_id: str = None) -> List[Dict]:
        """获取聊天历史，可按 session_id 过滤。"""
        ...

    @abstractmethod
    def save_diagnosis_history(self, diagnosis_info: Dict[str, Any]):
        """保存诊断历史记录。"""
        ...

    @abstractmethod
    def get_diagnosis_history(self, limit: int = 10) -> List[Dict]:
        """获取诊断历史记录。"""
        ...

    @abstractmethod
    def get_common_issue_types(self, top_n: int = 5) -> List[tuple]:
        """获取常见问题类型 Top-N。"""
        ...

    @abstractmethod
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息。"""
        ...

    @abstractmethod
    def clear_history(self):
        """清空历史记录（保留偏好）。"""
        ...


class BaseMerchantProfileStore(ABC):
    """商户画像抽象：商户级，聚合诊断统计、归属倾向、问题标签。"""

    @abstractmethod
    def record_diagnosis(
        self,
        ticket_id: str,
        issue_type: str,
        responsible_party: str,
        root_cause: str,
        timestamp: str = None,
    ):
        """记录一次诊断结果并更新画像。"""
        ...

    @abstractmethod
    def get_profile(self) -> Dict[str, Any]:
        """获取完整商户画像。"""
        ...

    @abstractmethod
    def get_responsibility_tendency(self, top_k: int = 3) -> List[Dict[str, Any]]:
        """获取责任归属倾向 Top-K。"""
        ...

    @abstractmethod
    def get_common_issue_types(self, top_k: int = 5) -> List[Dict[str, Any]]:
        """获取常见问题类型 Top-K。"""
        ...

    @abstractmethod
    def get_context_for_agent(self) -> str:
        """获取用于 Agent prompt 的商户画像文本。"""
        ...
