# Batch 3.2 降级策略改造计划

## 背景与目标

当前诊断链路对工具/Agent 失败的容忍度不够：

- `tool_registry.execute` 没有超时控制，单个 skill 卡住会拖垮整轮诊断。
- skill 返回格式不统一（有时 `status`，有时 `error`），调用方难以判断失败/降级。
- `DiagnosisService` 主循环未捕获 `IntentionAgent` / `OrchestrationAgent` 的异常，一旦抛错整次诊断直接失败。
- trace 中无法直观看到“哪个节点降级了”。

本计划聚焦 Batch 3.2：

1. 单 Skill 超时后返回部分结果并标记超时。
2. 单 Skill 失败后不中断整体诊断。
3. `search_kb` 不可用时自动降级为纯业务 Skill。
4. 日志类工具（`trace_api`）不可用时自动降级为数据库/快照查询。
5. 在 trace 中展示“降级发生”的节点。

遵循最小改动原则：不改 Agent 职责边界，不改诊断结论生成逻辑，只在执行层加保护和标记。

---

## 方案概述

### 1. ToolRegistry 增加超时与统一降级返回

文件：`utils/tool_registry.py`

- 在 `execute` 内部用 `asyncio.wait_for` 包装 handler 调用，超时时间从 `RESILIENCE_CONFIG["skill_timeout_sec"]` 读取（默认 5 秒）。
- 统一返回格式：
  - 成功：`{"status": "success", "data": ...}`
  - 超时：`{"status": "timeout", "tool": name, "message": "...", "data": {}}`
  - 异常：`{"status": "error", "tool": name, "message": "...", "data": {}}`
  - 未找到：`{"status": "not_found", "message": "...", "data": ...}`
- 这样所有 skill 调用者都只需检查 `result.get("status")`。

### 2. Skill 层识别降级并继续执行

文件：`agents/diagnosis_agents.py`

改造以下典型 skill：

- `run_search_history_ticket` / `run_search_policy_faq`：
  - 若 `search_kb` 返回 `error`/`timeout`，返回空 matches，summary 为 `"知识库检索不可用，已降级为纯业务证据"`。
- `run_order_code_path`：
  - 若 `trace_api` 返回 `error`/`timeout`，仅依赖 `check_config` 结果做判断，并在 summary 中标注 `"日志检索超时/失败，已降级为配置检查"`。
- `run_order_data_path`：
  - 若 `trace_api` 失败，`check_data` 仍可独立给出跨表不一致结论。
- 其他 skill：若底层 tool 返回 `error`/`timeout`，data 字段置空，summary 带 `[已降级]` 标记，继续返回。

### 3. DiagnosisService 主循环加异常保护

文件：`services/diagnosis_service.py`

- `intention_agent.reply` 失败时，回退到规则调度：构造一个包含 `generic_ticket_diagnosis` 的默认 intention。
- `orchestrator.reply` 失败时，返回当前已收集的 state 作为部分结果，diagnosis status 为 `"partial_failure"`。
- 记录异常到 trace（新增 `trace.record_error(round_num, error)` 或复用 `record_agent`）。

### 4. Trace 中展示降级

文件：`utils/trace_collector.py`、`services/diagnosis_service.py`

- `TraceCollector.record_agent` 的 `status` 字段支持 `"success" / "error" / "timeout" / "degraded"`。
- `DiagnosisService._record_trace` 中：
  - 若 agent result 的 `status` 为 `error`/`timeout`/`partial_failure`，trace status 记为 `"degraded"`。
  - 在 `tools_called` 中标注失败的 tool 名和降级原因（如 `"query_order(timeout)"`）。
- `format_trace_sse` 无需改动，已能透传 status。

### 5. 配置入口

文件：`config.py`

`RESILIENCE_CONFIG` 中新增：

```python
"skill_timeout_sec": 5.0,      # 单个 skill 超时时间
"agent_timeout_sec": 30.0,     # 单个 Agent 超时时间（OrchestrationAgent 内部已用 asyncio.gather）
```

---

## 关键文件清单

| 文件 | 改动点 |
|---|---|
| `config.py` | 增加 `skill_timeout_sec` 配置 |
| `utils/tool_registry.py` | `execute` 加 timeout，统一降级返回格式 |
| `agents/diagnosis_agents.py` | 关键 skill 处理降级结果 |
| `services/diagnosis_service.py` | 主循环异常保护，trace 标记降级 |
| `utils/trace_collector.py` | 支持 `degraded` status（文档/测试层面） |
| `tests/test_tool_registry.py` | 新建：测试超时/异常降级 |
| `tests/test_diagnosis_service.py` | 补 Agent 失败/降级场景测试 |

---

## 验证方式

1. 单元测试：
   - `tool_registry.execute` 对未注册工具、超时、异常返回统一降级格式。
   - `search_kb` 失败时 `run_search_history_ticket` 返回空 matches 并带降级标记。
   - `trace_api` 失败时 `run_order_code_path` 仍能返回基于 `check_config` 的结果。

2. 集成测试：
   - 在 `tests/test_diagnosis_service.py` 中构造一个会抛异常的 fake agent，验证诊断不中断且返回 `"partial_failure"`。
   - 验证 trace 中 agent status 为 `"degraded"`。

3. 回归测试：
   - 运行 `pytest tests/test_diagnosis_service.py tests/test_diagnosis_api.py tests/test_intention_agent.py tests/test_intention_agent_compat.py tests/test_orchestration_agent.py tests/test_resolution_agent.py tests/test_lazy_agent_registry.py tests/test_rag_integration.py`，确保 35 个测试全部通过。

---

## 风险与回退

- 风险：统一返回格式可能破坏现有 skill 调用者（目前调用方已通过 `result.get("status") == "success"` 判断，基本兼容）。
- 风险：timeout 会让原本慢但成功的调用被截断，默认值 5 秒对 mock 数据足够，真实环境可调。
- 回退：把 `skill_timeout_sec` 设得很大（如 60 秒）即可关闭超时行为。

---

## 计划完成后的开发手记条目

每个点 500 字以内，写入 `docs/开发手记.md`：

1. **Skill 超时降级**：为什么要加 timeout、统一返回格式的好处、如何影响调用方。
2. **知识库不可用降级**：`search_kb` 失败后如何保持诊断继续。
3. **日志工具不可用降级**：`trace_api` 超时后如何用数据库/配置检查兜底。
4. **主循环异常保护**：`IntentionAgent`/`OrchestrationAgent` 失败时的回退策略。
5. **Trace 降级可视化**：如何让前端/用户看到哪个节点降级了。
