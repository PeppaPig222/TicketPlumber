# 小哈工单智能诊断助手 Skills

基于本项目 **agents/** 实际实现的业务 Skills，便于在对话中按意图调用对应 Agent。

---

## 怎么用

在对话里**用自然语言说出你的需求**，系统会根据描述自动选用对应的 Skill（或组合多个）。不需要记命令，直接像和同事说话一样问即可。

---

### 1. ask-question（知识库问答 / RAG）

**怎么问：**
- 「订单支付成功但状态显示待退款怎么排查？」
- 「商户分配免时长提示余额不足怎么办？」
- 「退款回调超时错误码是什么？」
- 「结算金额和合同分润比例对不上是什么原因？」

**会干啥：** 用 RAG 从工单诊断知识库（5 类文档）检索相关内容，LLM 生成答案。  
**前置条件：** 已运行过 `python scripts/init_knowledge_base.py`。

---

### 2. query-info（实时信息查询）

**怎么问：**
- 「查一下这笔订单的支付流水」
- 「商户 XXX 的资产余额是多少？」
- 「搜一下 XX 接口的最新文档」

**会干啥：** 通过 Tool Registry 调用订单/商户/资产等数据源，并支持网络搜索补充实时信息。

---

### 3. memory-query（查诊断历史）

**怎么问：**
- 「我之前排查过类似问题吗？」
- 「常见故障类型有哪些？」
- 「我的诊断偏好」

**会干啥：** 从长期记忆（`data/memory/{user_id}.json`）里查诊断记录、偏好、对话摘要，用自然语言回答。  
**前置条件：** 需要有 MemoryManager（user_id/session_id）。

---

## 可用 Skills 一览

| Skill | 用途 | 触发示例 | 主要 Agent |
|-------|------|----------|------------|
| **ask-question** | 工单诊断知识问答 | 「退款异常怎么排查」「错误码E1001是什么」 | RAGKnowledgeAgent |
| **query-info** | 实时信息查询 | 「查订单」「商户余额」「搜索XX」 | InformationQueryAgent |
| **memory-query** | 查询诊断历史与偏好 | 「之前排查过什么」「我的常见问题类型」 | MemoryQueryAgent |

---

## 统一约定（与代码一致）

1. **模型传入方式**  
   所有 Agent 使用 **`model=model`**（传入已创建的 `OpenAIChatModel` 实例）。  

2. **异步调用**  
   所有子 Agent 的 `reply()` 均为 **async**，调用时需 **await**。

3. **模型创建**  
   ```python
   from agentscope.model import OpenAIChatModel
   from config import LLM_CONFIG
   model = OpenAIChatModel(
       model_name=LLM_CONFIG["model_name"],
       api_key=LLM_CONFIG["api_key"],
       client_kwargs={"base_url": LLM_CONFIG["base_url"], "timeout": 60},
       temperature=LLM_CONFIG.get("temperature", 0.7),
       max_tokens=LLM_CONFIG.get("max_tokens", 2000),
   )
   ```

4. **依赖 main.py / cli.py**  
   Skills 直接导入 **agents/** 与 **context/**，不依赖 `main.py` 或 `cli.py`。

---

## Agent 与文件对应

| Agent | 文件 | 职责 |
|-------|------|------|
| RAGKnowledgeAgent | rag_knowledge_agent.py | 诊断知识库检索与问答 |
| InformationQueryAgent | information_query_agent.py | 实时信息查询 |
| MemoryQueryAgent | memory_query_agent.py | 基于长期记忆回答诊断历史 |
| OrchestrationAgent | orchestration_agent.py | 协调多 Agent（CLI 主流程使用） |

---

## 目录结构

```
.claude/skills/
├── README.md
├── ask-question/SKILL.md
├── query-info/SKILL.md
└── memory-query/SKILL.md
```

---
