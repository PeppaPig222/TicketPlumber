#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试工单诊断意图识别智能体

使用方法：
  python tests/test_intention_agent.py
"""
import asyncio
import json
import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from agentscope.message import Msg

from agents.intention_agent import IntentionAgent


async def test_intention_agent():
    agent = IntentionAgent(name="IntentionAgent")

    test_cases = [
        {"name": "订单状态异常", "query": "请诊断工单 WO-20260815-0421"},
        {"name": "资产分配失败", "query": "帮我排查工单 WO-20260816-0532"},
        {"name": "结算金额不符", "query": "请看下工单 WO-20260817-0611"},
        {"name": "订单号直查", "query": "商户2037反馈订单ORD-8823状态异常"},
    ]

    for i, test_case in enumerate(test_cases, 1):
        print("\n" + "=" * 70)
        print(f"测试 {i}: {test_case['name']}")
        print("=" * 70)
        print(f"用户查询: {test_case['query']}")
        print()

        result = await agent.reply(Msg(name="user", content=test_case["query"], role="user"))
        payload = json.loads(result.content)
        display_result(payload)


def display_result(result):
    print("【推理过程】")
    print(result.get("reasoning", ""))
    print()

    print("【识别结果】")
    print(f"  - intent: {result.get('intent')}")
    print(f"  - scenario: {result.get('scenario')}")
    print(f"  - issue_type: {result.get('issue_type')}")
    print(f"  - ticket_id: {result.get('ticket_id')}")
    print()

    print("【关键实体】")
    for key, value in result.get("key_entities", {}).items():
        if value:
            print(f"  - {key}: {value}")
    print()

    print("【调度计划】")
    for item in result.get("agent_schedule", []):
        print(f"  - P{item.get('priority')} {item.get('agent_name')}: {item.get('reason')}")


if __name__ == "__main__":
    print("=" * 70)
    print("工单诊断意图识别测试")
    print("=" * 70)
    asyncio.run(test_intention_agent())
