"""
MongoDB 商户画像后端
用于多实例共享商户级画像数据。
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from context.base_memory import BaseMerchantProfileStore

logger = logging.getLogger(__name__)

try:
    import pymongo
    from pymongo import MongoClient
    MONGO_AVAILABLE = True
except ImportError:  # pragma: no cover
    MONGO_AVAILABLE = False
    MongoClient = None  # type: ignore


class MongoMerchantProfileStore(BaseMerchantProfileStore):
    """基于 MongoDB 的商户画像实现。"""

    def __init__(
        self,
        merchant_id: str,
        uri: str = "mongodb://localhost:27017/",
        db_name: str = "diagbot",
        collection_name: str = "merchant_profiles",
    ):
        if not MONGO_AVAILABLE:
            raise ImportError(
                "MongoDB backend requires 'pymongo' package. "
                "Install with: pip install pymongo"
            )
        if not merchant_id:
            raise ValueError("merchant_id is required for MongoMerchantProfileStore")
        self.merchant_id = merchant_id
        self._client = MongoClient(uri)
        self._collection = self._client[db_name][collection_name]
        self._ensure_profile()
        logger.info(f"MongoMerchantProfileStore initialized for merchant {merchant_id}")

    def _ensure_profile(self):
        existing = self._collection.find_one({"merchant_id": self.merchant_id})
        if existing is None:
            self._collection.insert_one({
                "merchant_id": self.merchant_id,
                "diagnosis_count": 0,
                "first_diagnosis_at": None,
                "last_diagnosis_at": None,
                "responsibility_distribution": {},
                "common_issue_types": {},
                "recent_tickets": [],
                "version": "1.0",
            })

    def _load(self) -> Dict[str, Any]:
        return self._collection.find_one({"merchant_id": self.merchant_id}) or {}

    def record_diagnosis(
        self,
        ticket_id: str,
        issue_type: str,
        responsible_party: str,
        root_cause: str,
        timestamp: str = None,
    ):
        now = timestamp or datetime.now().isoformat()
        ticket_summary = {
            "ticket_id": ticket_id,
            "issue_type": issue_type,
            "responsible_party": responsible_party,
            "root_cause": root_cause,
            "timestamp": now,
        }
        self._collection.update_one(
            {"merchant_id": self.merchant_id},
            {
                "$inc": {
                    "diagnosis_count": 1,
                    f"responsibility_distribution.{responsible_party}": 1,
                    f"common_issue_types.{issue_type}": 1,
                },
                "$set": {"last_diagnosis_at": now},
                "$setOnInsert": {"first_diagnosis_at": now},
                "$push": {
                    "recent_tickets": {
                        "$each": [ticket_summary],
                        "$position": 0,
                        "$slice": 20,
                    }
                },
            },
            upsert=True,
        )
        logger.info(
            f"Recorded diagnosis for merchant {self.merchant_id}: "
            f"{ticket_id} / {issue_type} / {responsible_party}"
        )

    def get_profile(self) -> Dict[str, Any]:
        data = self._load()
        # 移除 MongoDB 内部 _id
        data.pop("_id", None)
        return data

    def get_responsibility_tendency(self, top_k: int = 3) -> List[Dict[str, Any]]:
        data = self._load()
        distribution = data.get("responsibility_distribution", {})
        sorted_items = sorted(distribution.items(), key=lambda x: x[1], reverse=True)
        return [
            {"responsible_party": party, "count": count}
            for party, count in sorted_items[:top_k]
        ]

    def get_common_issue_types(self, top_k: int = 5) -> List[Dict[str, Any]]:
        data = self._load()
        issue_types = data.get("common_issue_types", {})
        sorted_items = sorted(issue_types.items(), key=lambda x: x[1], reverse=True)
        return [
            {"issue_type": issue_type, "count": count}
            for issue_type, count in sorted_items[:top_k]
        ]

    def get_context_for_agent(self) -> str:
        data = self.get_profile()
        if data.get("diagnosis_count", 0) == 0:
            return ""
        lines = [f"【商户画像 (ID: {self.merchant_id})】"]
        lines.append(f"- 历史诊断次数: {data['diagnosis_count']}")
        lines.append(f"- 最近诊断时间: {data.get('last_diagnosis_at')}")

        resp_tendency = self.get_responsibility_tendency(3)
        if resp_tendency:
            lines.append("- 历史责任归属倾向:")
            for item in resp_tendency:
                lines.append(f"  • {item['responsible_party']}: {item['count']}次")

        issue_types = self.get_common_issue_types(5)
        if issue_types:
            lines.append("- 常见问题类型:")
            for item in issue_types:
                lines.append(f"  • {item['issue_type']}: {item['count']}次")

        recent = data.get("recent_tickets", [])[:5]
        if recent:
            lines.append("- 最近相关工单:")
            for ticket in recent:
                lines.append(
                    f"  • {ticket['ticket_id']} / {ticket['issue_type']} / 归属: {ticket['responsible_party']}"
                )

        return "\n".join(lines)
