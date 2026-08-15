"""
长期记忆 (Long-term Memory)
持久化存储用户信息，支持跨会话访问
"""
from typing import Dict, Any, List, Optional
import json
import os
from datetime import datetime
from pathlib import Path
import logging

from context.base_memory import BaseLongTermMemory

logger = logging.getLogger(__name__)


class FileLongTermMemory(BaseLongTermMemory):
    """
    长期记忆：基于 JSON 文件持久化用户信息
    - 诊断相关偏好与配置
    - 历史工单诊断记录
    - 统计信息
    """

    def __init__(self, user_id: str, storage_path: str = "data/memory"):
        """
        初始化长期记忆

        Args:
            user_id: 用户ID
            storage_path: 存储路径
        """
        self.user_id = user_id
        self.storage_path = storage_path
        self.db_path = os.path.join(storage_path, f"{user_id}.json")

        # 确保存储目录存在
        Path(storage_path).mkdir(parents=True, exist_ok=True)

        # 加载或初始化数据
        self.data = self._load()
        logger.info(f"Long-term memory initialized for user: {user_id}")

    def _load(self) -> Dict[str, Any]:
        """从文件加载数据"""
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    logger.debug(f"Loaded long-term memory from {self.db_path}")

                    # 数据迁移：兼容旧格式
                    data = self._migrate_data(data)
                    return data
            except Exception as e:
                logger.error(f"Failed to load long-term memory: {e}")
                return self._init_data()
        else:
            logger.info("No existing long-term memory, creating new")
            return self._init_data()

    def _migrate_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        迁移旧数据格式到新格式

        Args:
            data: 原始数据

        Returns:
            迁移后的数据
        """
        # 1. 确保必需字段存在
        if "chat_history" not in data:
            data["chat_history"] = []
        if "statistics" not in data:
            data["statistics"] = {}
        if "total_messages" not in data.get("statistics", {}):
            data["statistics"]["total_messages"] = 0
        if "preferences" not in data:
            data["preferences"] = []
        if "diagnosis_history" not in data:
            legacy_trip_history = data.get("trip_history", [])
            migrated_history = []
            for idx, item in enumerate(legacy_trip_history, 1):
                if not isinstance(item, dict):
                    continue
                migrated_history.append({
                    "diagnosis_id": item.get("trip_id", f"diagnosis_{idx}"),
                    "timestamp": item.get("timestamp", datetime.now().isoformat()),
                    "ticket_id": item.get("ticket_id", ""),
                    "merchant_id": item.get("merchant_id", ""),
                    "issue_type": item.get("issue_type", item.get("purpose", "历史记录")),
                    "scenario": item.get("scenario", ""),
                    "summary": item.get("summary", ""),
                    "responsible_party": item.get("responsible_party", ""),
                    "query": item.get("query", ""),
                    "status": item.get("status", "completed"),
                    "legacy_record": item,
                })
            data["diagnosis_history"] = migrated_history

        statistics = data["statistics"]
        if "total_diagnoses" not in statistics:
            statistics["total_diagnoses"] = statistics.get(
                "total_trips",
                len(data.get("diagnosis_history", [])),
            )
        if "common_issue_types" not in statistics:
            common_issue_types = {}
            for item in data.get("diagnosis_history", []):
                issue_type = item.get("issue_type")
                if issue_type:
                    common_issue_types[issue_type] = common_issue_types.get(issue_type, 0) + 1
            statistics["common_issue_types"] = common_issue_types

        # 2. 迁移旧格式：字典 → 列表
        if isinstance(data.get("preferences"), dict):
            old_prefs = data["preferences"]
            new_prefs = []
            for pref_type, pref_value in old_prefs.items():
                if pref_value is not None:
                    new_prefs.append({"type": pref_type, "value": pref_value})
            data["preferences"] = new_prefs
            logger.info(f"Migrated: Converted preferences from dict to list ({len(new_prefs)} items)")

        # 3. 修复嵌套 bug（旧代码产生的错误数据）
        if isinstance(data.get("preferences"), list):
            fixed_prefs = []
            for pref in data["preferences"]:
                if isinstance(pref, dict):
                    # 错误的嵌套：{"type": "preferences", "value": [...]}
                    if pref.get("type") == "preferences" and isinstance(pref.get("value"), list):
                        for nested_pref in pref["value"]:
                            if isinstance(nested_pref, dict) and "type" in nested_pref:
                                fixed_prefs.append({"type": nested_pref["type"], "value": nested_pref["value"]})
                        logger.info("Migrated: Fixed nested preferences bug")
                    else:
                        fixed_prefs.append(pref)

            if fixed_prefs != data["preferences"]:
                data["preferences"] = fixed_prefs

        # 保存迁移后的数据
        self.data = data
        self._save()

        return data

    def _init_data(self) -> Dict[str, Any]:
        """初始化数据结构"""
        return {
            "user_id": self.user_id,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "preferences": [],  # 偏好列表: [{"type": "notification_channel", "value": "企业微信"}, ...]
            "chat_history": [],  # 所有聊天记录（跨会话）
            "diagnosis_history": [],  # 所有诊断记录
            "statistics": {
                "total_diagnoses": 0,
                "total_messages": 0,
                "common_issue_types": {}
            }
        }

    def _save(self):
        """保存数据到文件"""
        try:
            self.data["updated_at"] = datetime.now().isoformat()
            with open(self.db_path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            logger.debug(f"Saved long-term memory to {self.db_path}")
        except Exception as e:
            logger.error(f"Failed to save long-term memory: {e}")

    def save_preference(self, pref_type: str, value: Any):
        """
        保存用户偏好（列表格式）

        Args:
            pref_type: 偏好类型
            value: 偏好值
        """
        # 查找是否已存在该类型的偏好
        preferences = self.data["preferences"]
        found = False

        for pref in preferences:
            if pref.get("type") == pref_type:
                pref["value"] = value
                found = True
                break

        # 如果不存在，添加新的偏好
        if not found:
            preferences.append({"type": pref_type, "value": value})

        self._save()
        logger.info(f"Saved preference: {pref_type} = {value}")

    def get_preference(self, pref_type: str = None) -> Any:
        """
        获取用户偏好

        Args:
            pref_type: 偏好类型，None返回字典格式的全部偏好

        Returns:
            偏好值或偏好字典
        """
        preferences = self.data["preferences"]

        if pref_type is None:
            # 返回字典格式，方便调用方使用
            result = {}
            for pref in preferences:
                result[pref.get("type")] = pref.get("value")
            return result
        else:
            # 查找特定类型的偏好
            for pref in preferences:
                if pref.get("type") == pref_type:
                    return pref.get("value")
            return None

    def add_chat_message(self, role: str, content: str, session_id: str = None):
        """
        添加聊天消息到长期记忆

        Args:
            role: 角色 (user/assistant)
            content: 消息内容
            session_id: 会话ID（可选）
        """
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "session_id": session_id
        }

        self.data["chat_history"].append(message)
        self.data["statistics"]["total_messages"] += 1
        self._save()
        logger.debug(f"Added chat message to long-term memory: {role}")

    def get_chat_history(self, limit: int = None, session_id: str = None) -> List[Dict[str, Any]]:
        """
        获取聊天历史

        Args:
            limit: 返回数量限制
            session_id: 会话ID（只返回特定会话的消息）

        Returns:
            消息列表
        """
        messages = self.data["chat_history"]

        if session_id:
            messages = [m for m in messages if m.get("session_id") == session_id]

        if limit:
            return messages[-limit:]
        return messages

    def save_diagnosis_history(self, diagnosis_info: Dict[str, Any]):
        """
        保存工单诊断历史

        Args:
            diagnosis_info: 诊断信息
        """
        diagnosis_record = {
            "diagnosis_id": f"diagnosis_{len(self.data['diagnosis_history']) + 1}",
            "timestamp": datetime.now().isoformat(),
            **diagnosis_info
        }

        self.data["diagnosis_history"].append(diagnosis_record)

        # 更新统计信息
        self.data["statistics"]["total_diagnoses"] += 1

        # 更新常见问题类型统计
        issue_type = diagnosis_info.get("issue_type")
        if issue_type:
            issue_stats = self.data["statistics"]["common_issue_types"]
            issue_stats[issue_type] = issue_stats.get(issue_type, 0) + 1

        self._save()
        logger.info(f"Saved diagnosis history: {diagnosis_record['diagnosis_id']}")

    def get_diagnosis_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        获取历史诊断记录

        Args:
            limit: 返回数量限制

        Returns:
            诊断记录列表
        """
        history = self.data["diagnosis_history"]
        return history[-limit:] if limit else history

    def get_common_issue_types(self, top_n: int = 5) -> List[tuple]:
        """
        获取常见问题类型

        Args:
            top_n: 返回前N个

        Returns:
            [(issue_type, count), ...]
        """
        issue_stats = self.data["statistics"]["common_issue_types"]
        sorted_items = sorted(issue_stats.items(), key=lambda x: x[1], reverse=True)
        return sorted_items[:top_n]

    # 兼容旧接口：将旧 trip_history 访问映射到 diagnosis_history。
    # 诊断域主链路不再直接使用这些名称，但保留以避免旧脚本立即失效。
    def save_trip_history(self, trip_info: Dict[str, Any]):
        self.save_diagnosis_history(trip_info)

    def get_trip_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        return self.get_diagnosis_history(limit)

    def get_frequent_destinations(self, top_n: int = 5) -> List[tuple]:
        return self.get_common_issue_types(top_n)

    def increment_query_count(self):
        """增加查询计数"""
        self.data["statistics"]["total_queries"] += 1
        self._save()

    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return self.data["statistics"].copy()

    def clear_history(self):
        """清空历史记录（保留偏好）"""
        self.data["chat_history"] = []
        self.data["diagnosis_history"] = []
        self.data["statistics"]["total_diagnoses"] = 0
        self.data["statistics"]["total_messages"] = 0
        self.data["statistics"]["common_issue_types"] = {}
        self._save()
        logger.info("Cleared all history (chat + diagnoses)")

    def delete_all(self):
        """删除所有数据（包括文件）"""
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
            logger.warning(f"Deleted long-term memory file: {self.db_path}")


# 兼容旧导入：LongTermMemory 作为 FileLongTermMemory 的别名
LongTermMemory = FileLongTermMemory
