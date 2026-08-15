# TicketPlumber 工单智能诊断助手

基于自研 Multi-Agent 编排底座的 B 端商户工单智能诊断系统。

项目目标是把商户投诉场景里原本分散在前端、后端、数据侧的排查动作，收拢成一条可追踪、可回放、可扩展的多 Agent 诊断链路，帮助工单第一响应人更快完成信息收集、根因定位和责任归属判断。

## 项目特点

- 主链路分层：
  - `DiagnosisIntentionAgent`
  - `Scheduler`
  - `LazyAgentRegistry`
  - `Memory`
  - `Skill` 插件化
- 面向工单诊断场景做了诊断域适配：
  - `CodeAgent`
  - `OperationAgent`
  - `DataAgent`
  - `ResolutionAgent`
- 支持多轮 Loop：
  - 信息收集
  - 深度诊断
  - 交叉验证与归属判定
- 支持 CLI、FastAPI、Web Demo 三种入口
- 支持 trace 查询与 SSE 回放
- 当前已跑通 3 个高频场景：
  - 订单状态异常
  - 资产分配失败
  - 结算金额不符

## 项目定位

TicketPlumber 服务的是工单第一响应人。

在真实业务里，商户投诉工单进来后，接单的人不一定是固定角色，可能是前端，也可能是后端或 on-call。同一个人往往要做三件事：

1. 查业务数据
2. 查接口与日志链路
3. 判断责任归属

TicketPlumber 不直接替代人工修复，而是先把排查过程中的信息收集、证据整理和初步归因自动化，让第一响应人少开几个系统、少反复切换上下文。

## 与 Aligo 原工程的关系

这个项目不是“只换业务文案”的轻量改名版本，也不是完全推翻原有底座重做。

更准确的描述是：

- **复用的部分**
  - `DiagnosisIntentionAgent -> Scheduler -> LazyAgentRegistry` 的主链路分层
  - priority 并行调度
  - 长短期记忆机制
  - 重试、熔断、结构化输出兜底
  - trace 与日志能力
- **诊断域适配的部分**
  - 诊断主入口
  - 专业诊断 Agent
  - 领域记忆内容
  - 诊断 Skill 与 Tool 数据源
  - CLI / API / Web 的产品形态

所以它更像是：**复用 Aligo 的通用多 Agent 骨架，迁移到工单诊断场景。**

## 架构总览

```text
                 User
                   ↓
             Intention Agent
              LLM Understanding
                   ↓
            Strategy Matrix
             Deterministic
                   ↓
              Scheduler
             Deterministic
                   ↓
        ┌──────────┼──────────┐
        ↓          ↓          ↓
     CodeAgent OperationAgent DataAgent
        ↓          ↓          ↓
       Skill      Skill      Skill
        ↓          ↓          ↓
       Tool       Tool       Tool
        └──────────┼──────────┘
                   ↓
             Verification
                   ↓
              Final Answer
```

当前代码里仍然保留了 `DiagnosisService` 作为 facade，用来串联 API、trace、history 和多轮执行；后续目标是继续把主链路收回到更接近 Aligo 风格的 Agent 调度模式。

## 核心主链路

当前主入口已经能够完成一条完整的诊断闭环：

```text
CLI / HTTP Request
  -> DiagnosisService
  -> DiagnosisIntentionAgent
  -> Scheduler
  -> SkillRegistry / Diagnosis Agents
  -> LoopDecider
  -> TraceCollector
  -> Diagnosis Result
```

目标形态则是进一步回收为：

```text
CLI / HTTP Request
  -> DiagnosisIntentionAgent
  -> Scheduler
  -> LazyAgentRegistry
  -> CodeAgent / OperationAgent / DataAgent / ResolutionAgent
  -> SkillRegistry
  -> ToolRegistry
  -> Trace + Result
```

## 核心模块

### 入口与服务层

- `cli.py`
  - 命令行交互入口
  - 展示诊断结果、历史记录、trace 摘要
- `api/app.py`
  - FastAPI 入口
  - 提供诊断、trace 查询和 SSE 回放接口
- `services/diagnosis_service.py`
  - 当前诊断 facade
  - 串联 Intention、Scheduler、Loop、Trace 和历史记录

### Agent 层

- `agents/diagnosis_intention_agent.py`
  - 工单诊断场景下的意图识别与实体提取
- `agents/scheduler.py`
  - 轮内 priority 并行调度与结果聚合（确定性逻辑，非 Agent）
- `agents/diagnosis_agents.py`
  - 当前诊断路径执行实现
- `agents/loop_decider.py`
  - 轮间决策：`done / cross_verify / need_info`

### Skill 与 Tool 层

- `skills/registry.py`
  - Skill 注册中心
- `utils/tool_registry.py`
  - 9 个核心工具：query_ticket / query_order / trace_api / query_merchant / query_asset / query_settlement / check_config / check_data / search_kb
- `agents/diagnosis_agents.py`
  - 已落地 30+ 个原子 Skill，覆盖：
    - 商户管理：GetMerchantCoopStatus / GetMerchantContract / GetMerchantOrgTree / GetMerchantPermission / GetMerchantOnboarding / GetMerchantBlacklist
    - 商家经营：GetOrderDetail / GetOrderRefund / GetAssetPool / GetAssetAllocation / GetAssetRecycle / GetProtectionPeriod / GetBillingConfig / GetProductCatalog
    - 资金结算：GetBillDetail / GetBillCalculation / GetSettlementStatus / GetSettlementTimeline / GetReconciliation / GetInvoiceStatus / GetPaymentChannel
    - 通用辅助：SearchHistoryTicket / SearchPolicyFAQ / ValidateFrontendState / ReconstructTimeline
    - 三路径排查：order_code_path / order_operation_path / order_data_path 等

### 记忆与观测

- `context/`
  - 短期与长期记忆
  - 机制上仍然是长短期记忆，只是内容改成了诊断域
- `utils/trace_collector.py`
  - 收集每轮 Agent / Skill 的执行摘要、状态、耗时
- `utils/logging_config.py`
  - 结构化日志与 `trace_id`

## 支持场景

### 1. 订单状态异常

示例输入：

```text
请诊断工单 WO-20260815-0421
```

典型排查点：

- 订单状态与支付状态是否一致
- 回调链路是否成功
- 订单表、支付表、结算表是否存在状态冲突
- 是否命中历史相似工单

### 2. 资产分配失败

示例输入：

```text
帮我看下工单 WO-20260816-0532 为什么资产分配失败
```

典型排查点：

- 商户资产池额度
- 用户绑定关系
- 保护期限制
- 权限或跨商户分配限制

### 3. 结算金额不符

示例输入：

```text
请排查工单 WO-20260817-0611 的结算金额不符问题
```

典型排查点：

- 合同分润比例
- 账单明细与结算规则
- 计算过程与标签配置
- 历史同类结算异常案例

## 运行方式

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

配置环境变量（复制示例文件并修改）：

```bash
cp .env.example .env
# 编辑 .env，填入真实的 LLM API Key 和模型名
```

### 2. 启动 Web 服务

本地开发：

```bash
uvicorn api.app:app --reload
```

Docker 部署（需先安装 Docker）：

```bash
# 构建并启动
docker compose up --build -d

# 查看健康状态
curl http://127.0.0.1:8000/health

# 查看监控指标
curl http://127.0.0.1:8000/metrics
```

打开：

```text
http://127.0.0.1:8000/
```

### 3. 启动 CLI

```bash
python cli.py
```

可直接输入：

```text
请诊断工单 WO-20260815-0421
商户2037反馈订单ORD-8823状态异常
工单 WO-20260817-0611 结算金额不符
```

### 4. 调用 API

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/diagnose" \
  -H "Content-Type: application/json" \
  -d '{"query":"请诊断工单 WO-20260815-0421"}'
```

查询 trace：

```bash
curl "http://127.0.0.1:8000/api/v1/trace/<trace_id>"
```

SSE 回放：

```bash
curl "http://127.0.0.1:8000/api/v1/trace/stream/<trace_id>"
```

## 测试

当前建议优先跑这些回归测试：

```bash
pytest tests/test_diagnosis_service.py tests/test_diagnosis_api.py tests/test_intention_agent_compat.py
```

如果在做 CLI / Agent 主链路回收，也建议补跑：

```bash
pytest tests/test_intention_agent.py tests/test_cli_qa.py
```

## Mock 数据与演示数据

当前演示链路依赖以下 mock 数据：

- `data/mock/tickets.json`
- `data/mock/orders.json`
- `data/mock/merchants.json`
- `data/mock/assets.json`
- `data/mock/settlement.json`
- `data/mock/api_logs.json`
- `data/mock/db_snapshots.json`
- `data/mock/knowledge_base.json`

## 当前状态

当前项目已经完成：

- 诊断主链路可跑
- CLI / API / Web demo 可用
- 3 个高频场景闭环
- trace 查询与 SSE 回放

当前还在继续收口的方向：

- 回归更像 Aligo 的多 Agent 主链路
- 把专业 Agent 真正接回 `LazyAgentRegistry`
- 把 workflow runner 进一步改成 agent-driven 调度
- 补齐 30 个原子 Skill
- 补齐 React + TypeScript 版前端诊断面板

详细计划见：

- [docs/改造TODO.md](file:///Users/yuchen/Learning-library/demo/小哈工单智能诊断助手agent/docs/改造TODO.md)
- [docs/小哈工单智能诊断助手.md](file:///Users/yuchen/Learning-library/demo/小哈工单智能诊断助手agent/docs/小哈工单智能诊断助手.md)

## 目录参考

```text
.
├── agents/          # Intention / Scheduler / Loop / Diagnosis Agents
├── api/             # FastAPI 入口
├── context/         # 长短期记忆
├── data/mock/       # 演示用 mock 数据
├── docs/            # 技术方案、设计原则、TODO
├── frontend/        # React + TypeScript 诊断面板
├── services/        # 诊断 facade
├── skills/          # Skill 注册与后续插件化扩展
├── tests/           # 诊断链路测试
└── utils/           # tool registry / trace / logging 等基础设施
```
