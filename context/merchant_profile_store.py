"""
商户级画像 (Merchant Profile Store)
中期记忆：聚合单个商户的诊断历史、责任归属倾向和常见问题标签
存储：本地 JSON 文件（过渡方案），后续可迁移至 MySQL/Postgres
"""
from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path
import json
import os
import logging

logger = logging.getLogger(__name__)


class MerchantProfileStore:
    """
    商户级画像：维护单个商户的诊断统计与标签
    - 历史工单统计
    - 责任归属倾向（各归属方出现频次）
    - 常见问题类型标签
    - 最近工单摘要
    """

    def __init__(self, merchant_id: str, storage_path: str = "data/memory"):
        """
        初始化商户画像

        Args:
            merchant_id: 商户ID
            storage_path: 存储路径
        """
        if not merchant_id:
            raise ValueError("merchant_id is required for MerchantProfileStore")

        self.merchant_id = merchant_id
        self.storage_path = storage_path
        self.db_path = os.path.join(storage_path, f"merchant_{merchant_id}.json")

        Path(storage_path).mkdir(parents=True, exist_ok=True)
        self.data = self._load()
        logger.info(f"Merchant profile store initialized for merchant: {merchant_id}")

    def _load(self) -> Dict[str, Any]:
        """从文件加载画像数据"""
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    logger.debug(f"Loaded merchant profile from {self.db_path}")
                    return data
            except Exception as e:
                logger.error(f"Failed to load merchant profile: {e}")
                return self._init_data()
        return self._init_data()

    def _init_data(self) -> Dict[str, Any]:
        """初始化空白画像"""
        return {
            "merchant_id": self.merchant_id,
            "diagnosis_count": 0,
            "first_diagnosis_at": None,
            "last_diagnosis_at": None,
            "responsibility_distribution": {},
            "common_issue_types": {},
            "recent_tickets": [],
            "version": "1.0"
        }

    def _save(self):
        """持久化到文件"""
        try:
            with open(self.db_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            logger.debug(f"Saved merchant profile to {self.db_path}")
        except Exception as e:
            logger.exception(f"Failed to save merchant profile: {e}")

    def record_diagnosis(
        self,
        ticket_id: str,
        issue_type: str,
        responsible_party: str,
        root_cause: str,
        timestamp: str = None,
    ):
        """
        记录一次诊断结果，更新画像统计

        Args:
            ticket_id: 工单ID
            issue_type: 问题类型
            responsible_party: 责任归属方
            root_cause: 根因摘要
            timestamp: 时间戳，默认当前时间
        """
        now = timestamp or datetime.now().isoformat()

        # 更新基础统计
        self.data["diagnosis_count"] += 1
        if self.data["first_diagnosis_at"] is None:
            self.data["first_diagnosis_at"] = now
        self.data["last_diagnosis_at"] = now

        # 更新责任归属分布
        resp_dist = self.data["responsibility_distribution"]
        resp_dist[responsible_party] = resp_dist.get(responsible_party, 0) + 1

        # 更新问题类型标签
        issue_types = self.data["common_issue_types"]
        issue_types[issue_type] = issue_types.get(issue_type, 0) + 1

        # 维护最近工单（保留最近 20 条）
        ticket_summary = {
            "ticket_id": ticket_id,
            "issue_type": issue_type,
            "responsible_party": responsible_party,
            "root_cause": root_cause,
            "timestamp": now,
        }
        self.data["recent_tickets"].insert(0, ticket_summary)
        self.data["recent_tickets"] = self.data["recent_tickets"][:20]

        self._save()
        logger.info(
            f"Recorded diagnosis for merchant {self.merchant_id}: "
            f"{ticket_id} / {issue_type} / {responsible_party}"
        )

    def get_profile(self) -> Dict[str, Any]:
        """
        获取完整商户画像

        Returns:
            画像字典
        """
        return self.data.copy()

    def get_responsibility_tendency(self, top_k: int = 3) -> List[Dict[str, Any]]:
        """
        获取责任归属倾向 Top-K

        Args:
            top_k: 返回前 K 个

        Returns:
            排序后的归属方列表
        """
        sorted_items = sorted(
            self.data["responsibility_distribution"].items(),
            key=lambda x: x[1],
            reverse=True,
        )
        return [
            {"responsible_party": party, "count": count}
            for party, count in sorted_items[:top_k]
        ]

    def get_common_issue_types(self, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        获取常见问题类型 Top-K

        Args:
            top_k: 返回前 K 个

        Returns:
            排序后的问题类型列表
        """
        sorted_items = sorted(
            self.data["common_issue_types"].items(),
            key=lambda x: x[1],
            reverse=True,
        )
        return [
            {"issue_type": issue_type, "count": count}
            for issue_type, count in sorted_items[:top_k]
        ]

    def get_context_for_agent(self) -> str:
        """
        获取用于 Agent prompt 的商户画像文本

        Returns:
            格式化字符串
        """
        if self.data["diagnosis_count"] == 0:
            return ""

        lines = [f"【商户画像 (ID: {self.merchant_id})】"]
        lines.append(f"- 历史诊断次数: {self.data['diagnosis_count']}")
        lines.append(f"- 最近诊断时间: {self.data['last_diagnosis_at']}")

        resp_tendency = self.get_responsibility_tendency(3)
        if resp_tendency:
            lines.append("- 历史责任归属倾向:")
            for item in resp_tendency:
                lines.append(
                    f"  • {item['responsible_party']}: {item['count']}次"
                )

        issue_types = self.get_common_issue_types(5)
        if issue_types:
            lines.append("- 常见问题类型:")
            for item in issue_types:
                lines.append(f"  • {item['issue_type']}: {item['count']}次")

        recent = self.data["recent_tickets"][:5]
        if recent:
            lines.append("- 最近相关工单:")
            for ticket in recent:
                lines.append(
                    f"  • {ticket['ticket_id']} / {ticket['issue_type']} / 归属: {ticket['responsible_party']}"
                )

        return "\n".join(lines)
