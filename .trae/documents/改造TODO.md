# 小哈工单智能诊断助手 改造 TODO

> 基于 `docs/小哈工单智能诊断助手.md` 与当前代码现状整理。  
> 目标：按批次逐步把项目从“核心诊断闭环可跑”补到“技术方案完整版”，并尽量回归 Aligo 原本的
> 通用多 Agent / LLM 驱动 / Skill 插件化 / 面向开放式任务 的架构风格。

## 当前状态

### 已完成

- [x] 工单诊断主入口：`DiagnosisService`
- [x] 诊断意图识别：`DiagnosisIntentionAgent`
- [x] 多轮 Loop：`LoopDecider`
- [x] 3 个高频场景闭环：
  - [x] 订单状态异常
  - [x] 资产分配失败
  - [x] 结算金额不符
- [x] FastAPI 诊断接口、trace 查询、SSE 接口
- [x] 最小可用 Web demo 页
- [x] CLI 已切换为工单诊断助手
- [x] README / 项目命名已统一为 DiagBot / 工单智能诊断助手
- [x] 旧 `IntentionAgent` 已兼容到诊断语义
- [x] 技术方案文档已收口为“受控多 Agent”叙事
- [x] 基础回归测试：
  - [x] `tests/test_diagnosis_service.py`
  - [x] `tests/test_diagnosis_api.py`
  - [x] `tests/test_intention_agent_compat.py`

### 当前还未补全

- [x] 30 个原子 Skill 全量落地
- [x] 多 Agent 角色层真正落地，而不是以 workflow runner 为主
- [x] 回到 LazyAgentRegistry + Skill 插件化的主链路
- [ ] 完整调度策略矩阵与降级策略
- [ ] React + TypeScript + SSE 实时诊断追踪面板
- [x] 正式 RAG 知识库，而不是 mock keyword 检索
- [x] 工程化完整版：`pydantic-settings` / Docker / 全局结构化日志接线（Batch 6 已收口）
- [x] 离线评测体系与评测数据集（Batch 7 已收口）

---

## Batch 1：回归多 Agent 主架构

> 目标：保留当前高准确度诊断链路，但把运行主路径收回到更像 Aligo 的多 Agent 玩法。
>
> 这一批的核心不是继续堆 Skill，而是先把主链路改回：
> `CLI / API -> IntentionAgent -> OrchestrationAgent -> LazyAgentRegistry -> 专业 Agent -> Skill`
>
> 目标状态：
> - 入口上看起来仍然是 Aligo 风格的通用多 Agent 系统
> - 运行时仍然保留工单诊断需要的受控执行与高准确度
> - `DiagnosisService` 退居 facade，不再承载主要诊断编排逻辑

### 1.1 专业 Agent 角色化

- [x] 新增 `agents/code_agent.py`
- [x] 新增 `agents/operation_agent.py`
- [x] 新增 `agents/data_agent.py`
- [x] 新增 `agents/resolution_agent.py`
- [x] 为每个 Agent 定义统一结构化输出：
  - [x] `summary`
  - [x] `status`
  - [x] `evidence`
  - [x] `next_actions`
  - [x] `recommended_skills`
- [x] 为每个 Agent 定义职责边界
  - [x] `CodeAgent`：接口链路、配置、回调、前后端状态
  - [x] `OperationAgent`：用户操作、流程规范、历史工单操作侧经验
  - [x] `DataAgent`：跨表一致性、状态冲突、数据脏写
  - [x] `ResolutionAgent`：证据汇总、冲突消解、责任归属、处理建议
- [x] 为每个 Agent 定义可调用 Skill 白名单，避免跨域自由发散

### 1.2 编排层回归 Aligo 风格

- [x] 调整 `agents/orchestration_agent.py`
  - [x] 支持按 agent name 调度，而不是主要按 runner/skill 调度
  - [x] 支持 priority 分组并行调度专业 Agent
  - [x] 支持 round 内 agent 结果聚合
- [x] 让 `LazyAgentRegistry` 成为诊断主链路的一部分
  - [x] 注册 `CodeAgent / OperationAgent / DataAgent / ResolutionAgent`
  - [x] 保留后续新增 Agent 的懒加载能力
- [x] 明确两层分工
  - [x] Agent 决定看哪个视角、下一步查什么
  - [x] Skill 负责查单一数据源并返回结构化结果
- [x] 让 CLI / API / Web 展示“Agent 协作”
  - [x] CLI trace 中显示专业 Agent 名称
  - [x] API trace 中保留 agent-level 输出
  - [x] Web 面板展示 Agent 协作链，而不只是 Skill 列表

### 1.3 LLM 驱动能力回补

- [x] 为 `DiagnosisIntentionAgent` 回补更强的 LLM 输出字段
  - [x] `reasoning`
  - [x] `intents`
  - [x] `key_entities`
  - [x] `rewritten_query`
  - [x] `agent_schedule`
- [x] 为专业 Agent 补 prompt 与结构化输出协议
- [x] 让 Agent 在职责边界内做受控 tool selection
  - [x] 根据场景动态推荐 Skill
  - [x] 根据上轮证据决定是否补查
  - [x] 保证不会无限扩散调用
- [x] 保留 fallback 规则
  - [x] LLM 输出异常时回退到规则调度
  - [x] 关键实体缺失时走 `need_info`
  - [x] 单 Agent 失败不阻断整体链路
- [x] 为 `ResolutionAgent` 增加证据汇总与归因 prompt

### 1.4 回归检查

- [x] 更新技术方案中的架构图、组件表、时序图与面试话术
- [x] 更新 README，体现 Aligo 风格的主链路与当前实现状态
- [x] 补专业 Agent 级测试
  - [x] `tests/test_code_agent.py`
  - [x] `tests/test_operation_agent.py`
  - [x] `tests/test_data_agent.py`
  - [x] `tests/test_resolution_agent.py`
- [x] 补主链路回归测试
  - [x] `tests/test_orchestration_agent.py`
  - [x] `tests/test_cli_qa.py`
- [x] 验证 3 个高频场景闭环不回退

### 1.5 涉及文件

- [x] `cli.py`
- [x] `services/diagnosis_service.py`
- [x] `agents/intention_agent.py`
- [x] `agents/diagnosis_intention_agent.py`
- [x] `agents/orchestration_agent.py`
- [x] `agents/diagnosis_agents.py`
- [x] `skills/registry.py`
- [x] `utils/trace_collector.py`
- [x] `README.md`

### 1.6 验收标准

- [x] 从入口调用看，主链路已经回到 `IntentionAgent -> OrchestrationAgent -> Agent Registry -> 专业 Agent`
- [x] `DiagnosisService` 仅负责 facade、trace、history 和 API 适配
- [x] Trace 中可以清晰看到 `CodeAgent / OperationAgent / DataAgent / ResolutionAgent`
- [x] LLM 输出异常时仍能稳定回退
- [x] 3 个高频诊断场景结果不弱于当前版本

---

## Batch 2：补齐核心 Skill

> 目标：先把“文档里的 30 个原子 Skill”补成完整结构，即使底层仍然先用 mock 数据也可以。

### 2.1 商户管理域

- [x] `GetMerchantCoopStatus`
- [x] `GetMerchantContract`
- [x] `GetMerchantOrgTree`
- [x] `GetMerchantPermission`
- [x] `GetMerchantOnboarding`
- [x] `GetMerchantBlacklist`

### 2.2 商家经营域

- [x] `GetOrderRefund`
- [x] `GetAssetRecycle`
- [x] `GetProtectionPeriod`
- [x] `GetBillingConfig`
- [x] `GetProductCatalog`

### 2.3 资金结算域

- [x] `GetBillCalculation`
- [x] `GetSettlementStatus`
- [x] `GetSettlementTimeline`
- [x] `GetReconciliation`
- [x] `GetInvoiceStatus`
- [x] `GetPaymentChannel`

### 2.4 通用辅助 Skill

- [x] `ValidateFrontendState`
- [x] `ReconstructTimeline`
- [x] 将 `SearchHistoryTicket`、`SearchPolicyFAQ` 从轻量 mock 进一步抽象成独立 Skill

### 2.5 配套工作

- [x] 为新增 Skill 补对应 mock 数据文件
- [x] 为 `skills/registry.py` 和 `agents/diagnosis_agents.py` 补注册与测试
- [x] 更新 README 中“当前能力”与“支持场景”

---

## Batch 3：补齐调度策略与降级机制

> 目标：把当前“能跑”升级成“更像技术方案里的调度系统”。
>
> **Batch 3.2 已收口**：降级策略已完成最小改动版，包含 Skill 超时、search_kb 不可用、日志工具不可用、主循环异常保护、trace 降级标记。

### 3.1 调度策略矩阵

- [ ] 抽离基础信息查询的统一并行策略
- [ ] 抽离深度日志追踪的条件触发策略
- [ ] 抽离跨域交叉验证的依赖调度策略
- [ ] 抽离 RAG 与业务 Skill 并行执行策略
- [ ] 给不同策略补统一配置入口

### 3.2 降级策略

- [x] 单 Skill 超时后返回部分结果并标记超时
- [x] 单 Skill 失败后不中断整体诊断
- [x] `search_kb` 不可用时自动降级为纯业务 Skill
- [x] 日志类工具不可用时自动降级为数据库/快照查询
- [x] 在 trace 中展示“降级发生”的节点

### 3.3 统一错误与状态

- [ ] 在服务层真正接入 `ErrorCode` / `AppError`
- [ ] 为 API 响应补统一错误结构
- [ ] 为工具执行结果补统一状态枚举

---

## Batch 4：补齐前端诊断追踪面板

> 目标：把现在的静态 demo 页补成文档里的“前端优势项”。
>
> **已收口**：新建 `frontend/` React + TypeScript 工程，接入 `/api/v1/diagnose` 与 `/api/v1/trace/stream/{trace_id}`，构建产物挂载到 `/panel`。

### 4.1 前端工程初始化

- [x] 新建 React + TypeScript 前端工程
- [x] 接入诊断 API
- [x] 接入 trace SSE

### 4.2 面板功能

- [x] 输入区：工单 ID / 商户 / 问题描述
- [x] 左侧 Agent 思考链
- [x] Round 维度展示
- [x] 多 Agent 并行展示
- [x] Agent 耗时、状态、工具调用展示
- [x] 右侧诊断结论卡片
- [ ] 归属方判定矩阵（保留给后续增强）

### 4.3 可视化增强

- [x] 红/黄/绿三条路径的视觉区分
- [x] 诊断总耗时展示
- [ ] 交叉验证阶段单独高亮（保留给后续增强）
- [x] 降级/异常状态可视化

---

## Batch 5：补齐 RAG 知识库（已收口）

> 目标：把当前 `knowledge_base.json` 的 mock 检索升级成正式知识库方案。
>
> 收口标准：PDF/TXT 切分、Milvus Lite 存储、RAG 接入诊断链路、开关/阈值保护、测试覆盖均已完成。
> 剩余 `data/knowledge/*.txt` 属于后续数据补充，不阻塞链路。

### 5.1 知识库数据

- [x] 支持从商户 PDF/TXT 文档切分并生成 chunks（`scripts/process_merchant_pdf.py`）
- [x] 对复杂架构图页面生成结构化占位 chunk（图文字碎片 + 上下文关联）
- [ ] 新建 `data/knowledge/diagnosis_manual.txt`（当前以商户 PDF 为主要来源）
- [ ] 补历史工单经验文档
- [ ] 补 FAQ / 政策 / 处理指引文档

### 5.2 检索链路

- [x] 将 RAG 检索接入诊断主链路（IntentionAgent fallback + ResolutionAgent 证据补充）
- [x] 通过 `DiagnosisService` 统一管理 `RAGKnowledgeAgent` 实例
- [x] 支持返回来源、命中片段、页码、置信度
- [ ] 区分 `SearchHistoryTicket` 与 `SearchPolicyFAQ`（仍复用底层 RAG 检索，待按 collection 拆分）

### 5.3 测试

- [x] 补 RAG 检索命中测试（`tests/test_rag_integration.py`）
- [x] 补“知识库不可用时的降级测试”
- [x] 补 PDF 切分与架构图占位测试

---

## Batch 6：补齐工程化

> 目标：把方案里的工程化项真正接上线。

### 6.1 配置管理

- [x] 用 `pydantic-settings` 重构 `config.py`
- [x] 支持 `.env` / 环境变量加载
- [x] 区分开发、测试、生产环境

### 6.2 日志体系

- [x] 全局启用 `setup_logging`
- [x] API / Service / Tool / Agent 全链路带 `trace_id`
- [x] 关键节点输出结构化日志

### 6.3 容器化

- [x] 增加 `Dockerfile`
- [x] 增加 `docker-compose.yml`
- [x] 补 README 中容器启动方式

### 6.4 健康与监控

- [x] 丰富 `/health`
- [x] 丰富 `/metrics`
- [x] 补启动自检或依赖检查

---

## Batch 7：补齐评测体系

> 目标：把“面试价值”最强的评测链路建起来。

### 7.1 评测数据

- [x] 建立评测数据目录
- [x] 落 44 条核心评测数据：
  - [x] 20 条意图识别
  - [x] 12 条根因判断
  - [x] 12 条责任归属
- [ ] 后续扩展到 1000 条评测框架

### 7.2 Runner

- [x] 编写批量执行 runner
- [x] 输出 JSON/Markdown 报告
- [x] 统计失败 case

### 7.3 指标

- [x] 意图准确率
- [x] 实体召回率
- [x] RAG 命中率
- [x] 闭环成功率
- [x] 归属准确率
- [x] MRR 或 Top-K 命中指标

```
离线评测目前情况：
python scripts/run_evaluation.py --dataset data/evaluation/
core_eval_set.json
```
- 用例数：44
- 端到端通过率（Pass@1）： 77.27% （与开发手记中第一批基线一致）
- 场景准确率：100.00%
- 责任方准确率：84.09%
- 根因命中率：77.27%
- 轮次准确率：84.09%

---

## 建议补全顺序

> 如果按“投入产出比”来排，建议这样补：

1. [ ] Batch 1：回归多 Agent 主架构
2. [ ] Batch 2：核心 Skill
3. [ ] Batch 4：前端面板
4. [ ] Batch 6：工程化
5. [ ] Batch 5：正式 RAG
6. [ ] Batch 3：调度/降级细化
7. [ ] Batch 7：评测体系

---

## 每批完成后的回归检查

> Batch 6 / Batch 7 收口时已执行：

- [x] `pytest tests/test_diagnosis_service.py tests/test_diagnosis_api.py tests/test_intention_agent_compat.py`
- [x] `pytest tests/test_intention_agent.py tests/test_orchestration_agent.py tests/test_resolution_agent.py`
- [x] `pytest tests/test_lazy_agent_registry.py tests/test_rag_integration.py tests/test_tool_registry.py`
- [x] `python scripts/run_evaluation.py --dataset data/evaluation/core_eval_set.json`
- [x] 更新 README 与本 TODO 勾选状态

> 注：CLI / Web / Docker 本地运行验证依赖交互式环境，已在前期批次验证通过；当前环境未安装 Docker，容器化文件按最佳实践编写，可在有 Docker 的环境直接 `docker compose up --build`。
