# DiagBot 工单智能诊断助手

基于 AgentScope 多智能体底座改造的 B 端商户工单智能诊断系统，面向“订单状态异常、资产分配失败、结算金额不符”等高频投诉场景，支持多轮 Loop、并行排查路径、根因归属判定和诊断 Trace 可视化。

## 项目定位

这个项目服务的是工单第一响应人。工单来了以后，不管接单的是前端、后端还是 on-call，同样都要先做三件事：

- 查业务数据
- 查接口与日志链路
- 判断责任归属

DiagBot 的目标不是替代人工修复，而是把原本散落在多个平台里的排查动作统一编排起来，先给出一份结构化诊断结论。

## 当前能力

- 支持 3 个高频场景闭环：
  - 订单状态异常
  - 资产分配失败
  - 结算金额不符
- 支持 3 轮 Agentic Loop：
  - Round 1 信息收集
  - Round 2 多路径并行诊断
  - Round 3 交叉验证与归属判定
- 支持 Trace 面板：
  - 查看每轮调用了哪些 Agent
  - 查看每个 Agent 的摘要、状态和耗时
- 支持 Web API 与 CLI 两种入口
- 支持基于 mock 数据的端到端演示

## 核心架构

```text
用户输入 / 工单ID
    ↓
DiagnosisService
    ↓
DiagnosisIntentionAgent
    ↓
OrchestrationAgent
    ↓
SkillRegistry / Diagnosis Agents
    ↓
ToolRegistry + Mock Data
    ↓
LoopDecider
    ↓
TraceCollector / 诊断结论
```

### 主要模块

- `services/diagnosis_service.py`
  诊断主入口，串联多轮 Loop、Trace 和历史记录
- `agents/diagnosis_intention_agent.py`
  诊断场景下的意图识别与分轮调度
- `agents/diagnosis_agents.py`
  原子 Skill Agent 与三类诊断路径实现
- `skills/registry.py`
  诊断 Skill 注册中心
- `utils/tool_registry.py`
  诊断工具层，负责读取 mock 数据和知识库
- `api/app.py`
  FastAPI 入口，提供诊断、trace 查询和 SSE 回放
- `web/index.html`
  最小诊断面板

## 支持场景

### 1. 订单状态异常

示例输入：

```text
请诊断工单 WO-20260815-0421
```

默认流程：

- Round 1：读取工单、订单、商户、历史案例
- Round 2：并行排查代码链路、用户操作、数据一致性
- Round 3：结合 FAQ 与历史案例做交叉验证

### 2. 资产分配失败

示例输入：

```text
帮我看下工单 WO-20260816-0532 为什么资产分配失败
```

### 3. 结算金额不符

示例输入：

```text
请排查工单 WO-20260817-0611 的结算金额不符问题
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动 Web 服务

```bash
uvicorn api.app:app --reload
```

打开浏览器访问：

```text
http://127.0.0.1:8000/
```

### 3. 启动 CLI

```bash
python cli.py
```

CLI 示例：

```text
请诊断工单 WO-20260815-0421
工单 WO-20260817-0611 结算金额不符
商户2037反馈订单ORD-8823状态异常
```

### 4. 直接调用 API

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

当前最重要的诊断链路测试：

```bash
pytest tests/test_diagnosis_service.py tests/test_diagnosis_api.py
```

## Mock 数据

诊断演示依赖以下 mock 数据：

- `data/mock/tickets.json`
- `data/mock/orders.json`
- `data/mock/merchants.json`
- `data/mock/assets.json`
- `data/mock/settlement.json`
- `data/mock/api_logs.json`
- `data/mock/db_snapshots.json`
- `data/mock/knowledge_base.json`

## 当前边界

当前版本已经完成诊断主链路，但仍有一些底座模块保留了兼容实现，例如部分旧测试和旧插件式旅行 Skill 结构。这些兼容层不会影响工单诊断主入口：

- Web：`api/app.py`
- CLI：`cli.py`
- 服务层：`services/diagnosis_service.py`

## 技术方案

详细改造方案见：

[docs/小哈工单智能诊断助手.md](file:///Users/yuchen/Learning-library/demo/小哈工单智能诊断助手agent/docs/小哈工单智能诊断助手.md)
