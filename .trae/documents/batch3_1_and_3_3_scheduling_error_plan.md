# Batch 3.1 + 3.3 实施计划：调度策略矩阵与统一错误状态

## Context

当前项目已完成：
- Batch 3.2 降级策略（Skill 超时、search_kb 不可用、日志工具不可用、主循环异常保护、trace 降级标记）
- Batch 6 工程化（pydantic-settings、结构化日志、Docker、健康监控）
- Batch 7 评测体系（44 条评测数据、runner、Markdown 报告）

仍未完成的是：
- **Batch 3.1**：调度策略矩阵（基础信息并行、深度日志条件触发、跨域交叉验证依赖、RAG 与业务 Skill 并行）
- **Batch 3.3**：统一错误与状态（服务层接入 ErrorCode/AppError、API 统一错误结构、工具结果统一状态枚举）

本次目标是在不推翻现有 `DiagnosisService` 多轮循环和 `OrchestrationAgent` 优先级并行分组的前提下，把“调度逻辑”从 `IntentionAgent` 的硬编码里抽成可配置策略，并把散落的状态字符串收敛为统一枚举。

---

## Recommended Approach

### 1. Batch 3.1：调度策略矩阵

新增 `agents/scheduling/strategy_matrix.py`，引入三层抽象：

1. **`SchedulingContext`**：封装当前调度上下文（scenario、round_num、collected_data、key_entities、rag_available）。
2. **`AgentTask`**：标准调度单元，字段包括 `agent_name`、`priority`、`reason`、`expected_output`，以及策略扩展字段 `depends_on`、`required_entities`、`skip_if_missing`、`strategy`。
3. **`StrategyMatrix`**：组合 `ScenarioScheduleBuilder` + 一组 `SchedulingRule`，输出最终任务列表和 `schedule_metadata`。

四条策略规则：

| 规则 | 作用 |
|---|---|
| `BasicInfoParallelRule` | 把 CodeAgent / OperationAgent / DataAgent 等基础信息查询统一放到同一 priority 并行执行 |
| `DeepLogConditionalRule` | 仅在 scenario 为 `order_status_anomaly` 且已提取 `order_id` 时，才在第二轮注入深度日志追踪任务 |
| `CrossDomainDependencyRule` | 给 ResolutionAgent 添加 `depends_on=["CodeAgent", "OperationAgent", "DataAgent"]`，并提升 priority，确保交叉验证在证据收集后执行 |
| `RAGBusinessParallelRule` | 当 `rag_available=True` 且当前 round 在配置范围内时，把 `RAGKnowledgeAgent` 作为独立 Agent 与业务 Agent 并行调度 |

`OrchestrationAgent` 增强：
- `_execute_parallel_agents` 识别 `depends_on`，未满足依赖的任务延迟到下一批次。
- 对 `skip_if_missing=True` 且缺少 `required_entities` 的任务生成 `skipped` 结果，不执行。
- `_aggregate_results` 把 `skipped` 状态纳入统计。

### 2. Batch 3.3：统一错误与状态

#### 2.1 扩展 `utils/errors.py`

- 新增 `ToolStatus(str, Enum)`：`SUCCESS`、`ERROR`、`TIMEOUT`、`NOT_FOUND`、`NO_DATA`、`NO_MATCH`、`DEGRADED`。
- 新增 `ExecutionStatus(str, Enum)`：用于 Agent / 编排结果，如 `SUCCESS`、`ERROR`、`TIMEOUT`、`DEGRADED`、`PARTIAL_FAILURE`、`SKIPPED`。
- 补充错误码：`ORCHESTRATION_FAILED`、`INVALID_INTENTION`、`TICKET_NOT_FOUND`、`MISSING_REQUIRED_INFO`、`INTERNAL_ERROR`。
- 新增 `map_exception_to_error_code(exc)` 辅助函数。

#### 2.2 工具层状态统一

`utils/tool_registry.py` 的 `execute()`：
- 内部统一使用 `ToolStatus`。
- 错误/超时返回体增加 `error_code` 字段。
- 保持 `status` 值为字符串，确保旧代码和测试无需修改。

#### 2.3 服务层接入 `AppError`

`services/diagnosis_service.py`：
- `_fallback_intention()` 结果增加 `error_code=ErrorCode.INTENT_RECOGNITION_FAILED`。
- `_fallback_round_result()` 结果增加 `error_code=ErrorCode.ORCHESTRATION_FAILED`。
- `_record_trace` 使用 `ExecutionStatus` 标记 agent 状态。
- `diagnose()` 顶层增加统一异常捕获，把 `AppError` 和未预期异常转换为统一错误响应。
- 新增 `_error_response(trace_id, app_error)` 统一错误响应结构。

#### 2.4 API 统一错误响应

`api/app.py`：
- 新增 `ErrorResponse` Pydantic 模型。
- 注册异常处理器：
  - `AppError` → JSONResponse，body 为 `{"error": ..., "trace_id": ...}`，header 带 `x-error-code`。
  - `RequestValidationError` → 422 统一结构。
  - 通用 `Exception` → 500 + `INTERNAL_ERROR`。
- 保留现有 `HTTPException` 行为，不破坏前端对 404 等状态的判断。

---

## Configuration Additions

在 `config/settings.py` 新增 `SchedulingSettings`，环境变量前缀 `DIAG_SCH_`：

```python
class SchedulingSettings(BaseSettings):
    enable_strategy_matrix: bool = True
    enable_basic_info_parallel: bool = True
    enable_deep_log_conditional: bool = True
    deep_log_trigger_scenarios: List[str] = ["order_status_anomaly"]
    deep_log_required_entities: List[str] = ["order_id"]
    enable_cross_domain_validation: bool = True
    cross_domain_resolution_priority: int = 2
    enable_rag_business_parallel: bool = True
    rag_agent_name: str = "RAGKnowledgeAgent"
    rag_parallel_rounds: List[int] = [2, 3]
```

在 `Settings` 中增加 `scheduling: SchedulingSettings = SchedulingSettings()`，并在 `config/__init__.py` 导出 `SCHEDULING_CONFIG = settings.scheduling.model_dump()`。

---

## Files to Modify

### 新增文件

- `agents/scheduling/__init__.py`
- `agents/scheduling/strategy_matrix.py`
- `tests/test_strategy_matrix.py`
- `tests/test_errors.py`

### 修改文件

- `agents/diagnosis_intention_agent.py`：用 `StrategyMatrix` 生成 `agent_schedule`。
- `agents/orchestration_agent.py`：支持 `depends_on`、`required_entities`、`skip_if_missing`。
- `agents/resolution_agent.py`：优先从并行 RAG 结果取证据，再回退到原 `_search_kb`。
- `services/diagnosis_service.py`：注入 RAG 工厂、接入 `AppError`、统一错误响应。
- `api/app.py`：注册异常处理器、统一错误响应。
- `utils/errors.py`：扩展枚举和错误码。
- `utils/tool_registry.py`：工具状态统一枚举。
- `config/settings.py`：新增 `SchedulingSettings`。
- `config/__init__.py`：导出 `SCHEDULING_CONFIG`。
- `tests/test_orchestration_agent.py`：补充依赖调度和 skip 用例。
- `tests/test_tool_registry.py`：验证 `error_code` 和枚举兼容。
- `tests/test_diagnosis_service.py`：验证 fallback 错误码和统一错误响应。
- `tests/test_diagnosis_api.py`：验证 API 错误结构和 header。

---

## Testing Strategy

1. **策略矩阵等价性**：默认配置下各 scenario/round 的调度与现有 `_build_schedule` 输出等价。
2. **条件触发**：`DeepLogConditionalRule` 在缺少 `order_id` 时不注入深度日志任务。
3. **依赖调度**：`ResolutionAgent` 等待 CodeAgent / OperationAgent / DataAgent 完成后才执行。
4. **RAG 并行**：`rag_available=True` 时 RAGKnowledgeAgent 被并行注入；为 False 时降级为原 ResolutionAgent 内检索。
5. **状态枚举兼容**：`ToolStatus` 继承 `str, Enum`，JSON 序列化后仍是旧字符串。
6. **错误响应**：`AppError` 触发统一 JSON 结构和 `x-error-code` header。
7. **回归测试**：跑 `tests/test_diagnosis_service.py`、`tests/test_diagnosis_api.py`、`tests/test_intention_agent*.py`、`tests/test_orchestration_agent.py`、`tests/test_resolution_agent.py`、`tests/test_tool_registry.py`。

---

## Risk Mitigation

| 风险 | 缓解 |
|---|---|
| 策略矩阵改变现有调度，导致测试失败 | 默认配置精确还原现有 schedule；`enable_strategy_matrix=False` 可一键回退旧逻辑。 |
| RAG 未初始化导致调度失败 | `RAGBusinessParallelRule` 仅在 `rag_available=True` 时注入；ResolutionAgent 保留原回退。 |
| 工具状态字符串改枚举破坏旧代码 | `ToolStatus` 继承 `str, Enum`，JSON 仍是字符串。 |
| `RESILIENCE_CONFIG` 等旧导入失效 | 不修改原有导出，仅新增 `SCHEDULING_CONFIG`。 |
| API 错误结构改变影响前端 | 成功响应不变；错误时新增 `error` 字段，`HTTPException` 保持原状。 |

---

## Implementation Order

1. 扩展 `utils/errors.py`（ToolStatus / ExecutionStatus / 错误码 / 映射函数）。
2. 统一 `utils/tool_registry.py` 状态并附加 `error_code`。
3. 新增 `config/settings.py` 的 `SchedulingSettings`，导出 `SCHEDULING_CONFIG`。
4. 创建 `agents/scheduling/strategy_matrix.py`。
5. 在 `agents/diagnosis_intention_agent.py` 接入 `StrategyMatrix`。
6. 增强 `agents/orchestration_agent.py` 的依赖与 skip 逻辑。
7. 在 `services/diagnosis_service.py` 注入 RAG 工厂并接入 `AppError`。
8. 调整 `agents/resolution_agent.py` 读取并行 RAG 结果。
9. 在 `api/app.py` 注册统一异常处理器。
10. 补充 `tests/test_strategy_matrix.py`、`tests/test_errors.py` 及相关测试修改。
11. 运行回归测试并修复失败用例。
12. 更新 `改造TODO.md` 中 Batch 3.1 / 3.3 勾选状态。

---

*Plan created on 2026-08-11 for Batch 3.1 + 3.3 implementation.*
