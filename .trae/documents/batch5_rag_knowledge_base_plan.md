# 改造计划：Batch 5 — 正式 RAG 知识库（基于单份商户架构 PDF）

## Context

当前 `docs/改造TODO.md` 中 Batch 5 仍为：

- [ ] 正式 RAG 知识库，而不是 mock keyword 检索

现状：
- `.claude/skills/ask-question/script/agent.py` 已实现基于 Milvus Lite 的 `RAGKnowledgeAgent`，具备 add_documents / search_knowledge 能力。
- `.claude/skills/ask-question/script/init_knowledge_base.py` 已实现文档切分和导入脚本，但切分策略较简单（按段落 + 固定长度），且目标目录是商旅文档。
- `agents/diagnosis_intention_agent.py` 目前基于规则匹配 scenario，没有真正调用 RAG。
- `agents/resolution_agent.py` 只用 `search_history_ticket` / `search_policy_faq`，没有接入正式知识库。
- 用户当前只有**一份商户 PDF 架构文档**，且该 PDF **复杂架构图较多**。

本计划目标：在不编造事实的前提下，把这份真实 PDF 切分、向量化并存储到 Milvus Lite，同时把 RAG 检索接入诊断主链路（意图识别 fallback + _resolution 历史经验补充）。

## 推荐方案

### 核心原则

- **不凭空编造**：所有入库内容必须来自真实 PDF 或通过 LLM 从 PDF 内容派生（FAQ、摘要、排查手册）。
- **图中信息尽量保留**：优先提取 PDF 中可识别的文字；对无法提取的架构图，在 chunk metadata 中标记页码和图号，便于后续人工补录或 OCR 增强。
- **最小改动**：复用现有 `RAGKnowledgeAgent` 和 `SkillRegistry` 机制，只新增文档处理脚本和接入点。

### 实施步骤

#### 1. 添加 PDF 解析依赖

在 `requirements.txt` 中新增：

```text
pdfplumber>=0.11.0    # 比 pypdf 更适合复杂排版、表格和图中文字的 PDF
```

原因：用户说明 PDF 中复杂架构图较多，`pdfplumber` 对复杂排版的文本定位和表格提取更稳定。

#### 2. 新增 PDF 文档切分脚本

新建 `scripts/process_merchant_pdf.py`：

- 读取 `data/documents/merchant_architecture.pdf`（用户提供的单份 PDF）。
- 使用 `pdfplumber` 逐页提取文本。
- 对每一页：
  - 如果页内可提取文本量 > 100 字符，按段落切分为多个 chunk；超长段落用滑动窗口兜底（max_chars=600, overlap=100）。
  - 如果页内文本量很少（主要是图），生成一个占位 chunk，content 记录为 `[架构图：第 X 页，需人工补充或 OCR]`，metadata 标记 `page`、`has_diagram=true`。
- 每个 chunk 附带 metadata：
  - `source`: PDF 文件名
  - `page`: 页码
  - `category`: "商户架构文档"
  - `title`: 页内第一行或文件名
  - `has_diagram`: 是否以图为主

#### 3. 新增知识库初始化入口

新建 `scripts/init_diagnosis_kb.py`：

- 调用 `scripts/process_merchant_pdf.py` 获取 chunks。
- 加载 `RAGKnowledgeAgent`（复用 `.claude/skills/ask-question/script/agent.py` 中的实现）。
- 使用本地 `data/models/bge-small-zh-v1.5` 模型做 embedding（复用 `config.RAG_CONFIG`）。
- collection_name 使用 `ticket_diagnosis_knowledge`。
- 将 chunks 写入 Milvus Lite，存储路径改为 `data/rag_knowledge/`（项目级目录，而非 ask-question skill 内部）。
- 支持 `--reset` 参数删除重建 collection。

#### 4. 接入诊断主链路

##### 4.1 IntentionAgent 增加 RAG fallback

修改 `agents/diagnosis_intention_agent.py`：

- 当规则匹配无法确定 scenario（`scenario is None` 或 confidence 低）时，调用 RAG 检索知识库。
- 如果检索结果与"订单"/"资产"/"结算"强相关，则推断 scenario。
- 把检索到的相关知识片段追加到 `reasoning` 中。

实现方式：
- 新增 `_search_kb(query)` 异步方法，封装 `RAGKnowledgeAgent.search_knowledge`。
- 由于 RAG 依赖可能未安装，用 try/except 包裹，失败时静默 fallback 到规则匹配。

##### 4.2 ResolutionAgent 增加知识库检索

修改 `agents/resolution_agent.py`：

- 在 `allowed_skills` 中保留 `search_policy_faq`。
- 在 `_round_three` 或 `_resolve_*` 方法中，调用 RAG 检索历史策略/排查手册，补充 `evidence` 和 `recommendations`。
- 不改动现有判责结论，只增强证据链。

##### 4.3 统一 RAG Agent 实例管理

在 `services/diagnosis_service.py` 中：

- 创建诊断时初始化一个 `RAGKnowledgeAgent` 实例（或复用单例）。
- 通过 `agent_kwargs` 把 RAG Agent 注入 IntentionAgent 和 ResolutionAgent。
- 如果 RAG 依赖未安装或模型缺失，给出降级提示但不阻塞诊断。

#### 5. LLM 数据增强（可选但推荐）

基于已提取的 PDF 文本，用脚本生成三类衍生文档并一起入库：

- **FAQ**：针对 PDF 中的关键概念生成"常见问题-答案"对。
- **排查手册**：针对商户架构中的常见故障点生成"现象-排查步骤-责任方"模板。
- **场景摘要**：为每个主要章节生成一句话摘要。

生成规则：
- 所有内容必须能从 PDF 原文中找到依据。
- 在 metadata 中标记 `generated_by="llm"`，与原始 PDF chunks 区分。
- 如果 LLM 生成内容无法验证，直接丢弃。

#### 6. 测试与验证

- 新增 `tests/test_rag_integration.py`：
  - 验证 PDF 切分脚本能生成非空 chunks。
  - 验证 `init_diagnosis_kb.py` 能成功写入 Milvus Lite。
  - 验证 RAG 检索能返回与 query 相关的文档。
  - 验证 IntentionAgent 在 scenario 不确定时能调用 RAG fallback。
- 运行核心回归测试：`tests/test_diagnosis_service.py`、`tests/test_diagnosis_api.py`。

## 关键文件

- `requirements.txt` —— 新增 `pdfplumber`
- `scripts/process_merchant_pdf.py` —— 新建，PDF 文本提取与切分
- `scripts/init_diagnosis_kb.py` —— 新建，知识库初始化入口
- `agents/diagnosis_intention_agent.py` —— 增加 RAG fallback
- `agents/resolution_agent.py` —— 增加知识库检索
- `services/diagnosis_service.py` —— 注入 RAG Agent
- `data/rag_knowledge/` —— 新建 Milvus Lite 存储目录
- `tests/test_rag_integration.py` —— 新增测试

## 用户需要准备的数据

用户需将商户 PDF 架构文档放置到：

```text
data/documents/merchant_architecture.pdf
```

如果文件名不同，可通过 `scripts/init_diagnosis_kb.py --pdf path/to/file.pdf` 指定。

## 验证方式

1. 放置 PDF 后运行：

```bash
python scripts/init_diagnosis_kb.py --reset
```

应输出成功添加的 chunk 数量和知识库统计。

2. 运行测试：

```bash
python -m pytest tests/test_rag_integration.py tests/test_diagnosis_service.py tests/test_diagnosis_api.py -q
```

应全部通过。

3. 运行一个诊断请求，检查 IntentionAgent 的 `reasoning` 中是否包含 RAG 检索到的知识片段。

## 风险与回退

- **风险**：PDF 中架构图文字无法提取，导致知识库缺失图中信息。
  - 回退：metadata 标记为 `[第 X 页架构图]`，后续可人工补充或接入 OCR。
- **风险**：embedding 模型 `bge-small-zh-v1.5` 未下载。
  - 回退：脚本启动时检查模型路径，缺失则提示用户先下载或改用 HuggingFace 在线模型。
- **风险**：RAG 检索结果干扰 scenario 判断。
  - 回退：RAG 只作为 fallback，当规则匹配成功时优先使用规则；检索阈值低于 0.5 时不采纳。
