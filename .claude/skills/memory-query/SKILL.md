---
name: memory-query
description: Use this skill when the user asks about their diagnosis history, past tickets, or saved preferences. Triggers when user asks "我之前排查过什么问题", "常见故障类型有哪些", "我的诊断偏好", "历史工单记录". This skill uses MemoryQueryAgent and requires a MemoryManager (user_id, session_id) to access long-term memory.
---

# Memory Query (记忆查询)

基于用户**长期记忆**回答诊断历史、偏好设置等问题，使用 **MemoryQueryAgent**。需传入 **MemoryManager** 以访问 `data/memory/{user_id}.json` 中的诊断记录、偏好与聊天摘要。

## When to Use

- 用户询问历史诊断记录、工单排查偏好、或过往对话内容时

## Agent

- **MemoryQueryAgent** (`agents/memory_query_agent.py`)
- 入参：**model**、**memory_manager**（必选，否则无记忆可查）
- **异步**：`reply()` 为 `async`，需 `await`

## 依赖

- **MemoryManager**：`context.memory_manager.MemoryManager(user_id, session_id, storage_path, llm_model)`
- 长期记忆存储：`data/memory/{user_id}.json`

## 初始化与调用

```python
import asyncio
import json
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel
from config_agentscope import init_agentscope
from config import LLM_CONFIG
from context.memory_manager import MemoryManager
from agents.memory_query_agent import MemoryQueryAgent

async def memory_query(user_query: str, user_id: str = "default_user", session_id: str = "default"):
    init_agentscope()
    model = OpenAIChatModel(
        model_name=LLM_CONFIG["model_name"],
        api_key=LLM_CONFIG["api_key"],
        client_kwargs={"base_url": LLM_CONFIG["base_url"], "timeout": 60},
        temperature=LLM_CONFIG.get("temperature", 0.7),
        max_tokens=LLM_CONFIG.get("max_tokens", 2000),
    )
    memory_manager = MemoryManager(user_id=user_id, session_id=session_id, llm_model=model)
    agent = MemoryQueryAgent(
        name="MemoryQueryAgent",
        model=model,
        memory_manager=memory_manager,
    )
    user_msg = Msg(
        name="user",
        content=json.dumps({"context": {"rewritten_query": user_query}}),
        role="user",
    )
    result = await agent.reply(user_msg)
    return json.loads(result.content) if isinstance(result.content, str) else result.content

# 使用
data = asyncio.run(memory_query("我上次排查的支付回调超时是什么原因？"))
# data: {"status": "success", "query": "...", "answer": "...", "memory_sources": {"diagnosis_count", "has_preferences", ...}}
```

## 返回格式

- `status`: `"success"` 或 `"error"`
- `query`: 用户问题
- `answer`: 基于记忆的自然语言回答
- `memory_sources`: 如 `diagnosis_count`, `has_preferences`, `has_chat_summary`

## 回答指南

【回答要求】
1. 直接基于诊断历史和偏好信息回答问题
2. 如果记忆中没有相关信息，诚实说明"记录中没有相关信息"
3. 回答要自然、准确、有条理
4. 如果有多条记录，可以按时间顺序或问题类型分类列举
5. 不要编造不存在的信息

请直接回答用户的问题。
