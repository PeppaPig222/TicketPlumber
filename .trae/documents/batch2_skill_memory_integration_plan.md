# 改造计划：Batch 2 Skill 充分利用 + 三层记忆系统回注

## Context

当前项目已完成 Batch 2 的 30+ 原子 Skill 注册，但 4 个专业 Agent（CodeAgent / OperationAgent / DataAgent / ResolutionAgent）的 `allowed_skills` 很小，大量 Skill 注册后未被调用。同时，三层记忆系统（`ShortTermMemory`、`LongTermMemory`、`MerchantProfileStore`、`DiagnosisPatternStore`）已通过 `MemoryManager` 实现，但 `DiagnosisService` 仍未注入 `MemoryManager`，导致记忆层与诊断主链路脱节。

本计划目标：
1. 让专业 Agent 充分利用已注册的 30+ 原子 Skill，按场景编排调用。
2. 把三层记忆系统接回主链路，实现诊断前后记忆写入、商户画像注入、历史模式检索。

RAG 文档切分与 Milvus 存储不在本次范围内（用户已明确暂缓）。

---

## Recommended Approach

采用**增量改造、风险可控**的方案：
- 仅扩展专业 Agent 的 `allowed_skills` 和 `_round_one` 的 Skill 编排，第二轮/第三轮/Resolution 的判责逻辑保持不变，确保现有测试稳定。
- 本轮**不**让 ResolutionAgent 依赖 `root_cause_resolver` Skill，只扩展白名单，避免引入输出不一致风险。
- `DiagnosisService` 每次 `diagnose()` 调用时创建新的 `MemoryManager`、`LazyAgentRegistry`、`OrchestrationAgent`，避免 API 并发下的状态竞争；`IntentionAgent` 无状态，可保留实例级。
- `MemoryManager` 增加 `set_merchant_id()` 以支持 ticket 加载后延迟创建 `MerchantProfileStore`。
- API 层从 per-request 创建 `DiagnosisService`，通过 Header 或请求体获取 `user_id` / `session_id`。

---

## Implementation Steps

### Step 1: 扩展 `MemoryManager` 支持延迟设置商户画像

**文件**: [context/memory_manager.py](file:///Users/yuchen/Learning-library/demo/小哈工单智能诊断助手agent/context/memory_manager.py)

- 新增 `set_merchant_id(merchant_id: str)` 方法：
  - 如果 `merchant_id` 与当前不同，创建/切换到新的 `MerchantProfileStore`。
  - 更新 `self.merchant_id`。
- 新增 `get_merchant_context() -> str` 方法：
  - 如果 `merchant_profile` 已初始化，返回 `merchant_profile.get_context_for_agent()`。
  - 否则返回空字符串。
- `record_diagnosis()` 中 `merchant_id` 取 `diagnosis_result.get("merchant_id") or self.merchant_id`。

### Step 2: 扩展 4 个专业 Agent 的 `allowed_skills`

**文件**:
- [agents/code_agent.py](file:///Users/yuchen/Learning-library/demo/小哈工单智能诊断助手agent/agents/code_agent.py)
- [agents/operation_agent.py](file:///Users/yuchen/Learning-library/demo/小哈工单智能诊断助手agent/agents/operation_agent.py)
- [agents/data_agent.py](file:///Users/yuchen/Learning-library/demo/小哈工单智能诊断助手agent/agents/data_agent.py)
- [agents/resolution_agent.py](file:///Users/yuchen/Learning-library/demo/小哈工单智能诊断助手agent/agents/resolution_agent.py)

扩展后白名单示例：

**CodeAgent**:
```python
allowed_skills = {
    "get_order_detail", "get_order_timeline", "GetOrderRefund",
    "ReconstructTimeline", "ValidateFrontendState", "order_code_path",
    "get_asset_pool", "get_asset_allocation", "GetBillingConfig", "GetProductCatalog",
    "get_merchant_contract", "get_bill_detail", "get_settlement_rule",
    "GetBillCalculation", "GetSettlementStatus", "GetSettlementTimeline",
    "GetReconciliation", "GetInvoiceStatus", "GetPaymentChannel",
    "settlement_calculation_path", "settlement_timeline_path",
    "search_policy_faq",
}
```

**OperationAgent**:
```python
allowed_skills = {
    "get_merchant_profile", "GetMerchantCoopStatus", "GetMerchantContract",
    "GetMerchantOrgTree", "GetMerchantPermission", "GetMerchantOnboarding",
    "GetMerchantBlacklist",
    "get_user_binding", "GetProtectionPeriod", "GetAssetRecycle",
    "get_asset_allocation", "asset_binding_path",
    "get_order_timeline", "GetOrderRefund", "order_operation_path",
    "search_history_ticket", "search_policy_faq",
}
```

**DataAgent**:
```python
allowed_skills = {
    "get_order_detail", "order_data_path", "ReconstructTimeline", "ValidateFrontendState",
    "get_asset_pool", "get_asset_allocation", "asset_availability_path",
    "get_bill_detail", "get_settlement_rule", "GetBillCalculation",
    "GetSettlementStatus", "GetSettlementTimeline", "GetReconciliation",
    "settlement_contract_path", "settlement_calculation_path",
    "search_history_ticket",
}
```

**ResolutionAgent**:
```python
allowed_skills = {"search_history_ticket", "search_policy_faq", "root_cause_resolver"}
```

### Step 3: 按场景扩展 `_round_one` Skill 编排

**文件**: 同上 4 个专业 Agent 文件

保持 `_order_round_two`、`_asset_round_two`、`_settlement_round_two`、`_follow_up` 逻辑不变，仅扩展 `_round_one` 中调用的 Skill 列表。

示例（CodeAgent._round_one）：
```python
if scenario == "asset_allocation_failure":
    skill_names = [
        "get_asset_pool", "get_asset_allocation",
        "GetBillingConfig", "GetProductCatalog",
    ]
elif scenario == "settlement_amount_mismatch":
    skill_names = [
        "get_merchant_contract", "get_bill_detail", "get_settlement_rule",
        "GetBillCalculation", "GetSettlementStatus", "GetSettlementTimeline",
        "GetReconciliation",
    ]
else:  # order_status_anomaly / default
    skill_names = [
        "get_order_detail", "get_order_timeline",
        "GetOrderRefund", "ReconstructTimeline", "ValidateFrontendState",
    ]
```

`OperationAgent._round_one` 和 `DataAgent._round_one` 按同样模式扩展。

### Step 4: 在 `DiagnosisService` 中接入 `MemoryManager`

**文件**: [services/diagnosis_service.py](file:///Users/yuchen/Learning-library/demo/小哈工单智能诊断助手agent/services/diagnosis_service.py)

修改 `diagnose()` 方法：
1. 接收可选 `user_id` / `session_id` 参数。
2. 创建本次请求的 `MemoryManager`：
   ```python
   memory_manager = MemoryManager(
       user_id=user_id or self.user_id,
       session_id=session_id or str(uuid.uuid4())[:8],
       storage_path=self.storage_path,
   )
   ```
3. 记录用户提问：
   ```python
   memory_manager.add_message("user", query)
   ```
4. 加载 ticket 后，根据 `ticket.get("merchant_id")` 调用 `memory_manager.set_merchant_id(...)`。
5. 每次 `diagnose()` 内部创建新的 `LazyAgentRegistry` 和 `OrchestrationAgent`，把 `memory_manager` 注入：
   ```python
   agent_registry = LazyAgentRegistry(
       model=None,
       cache={},
       memory_manager=memory_manager,
       custom_factories={...},
   )
   orchestrator = OrchestrationAgent(
       name="DiagnosisOrchestrationAgent",
       agent_registry=agent_registry,
       memory_manager=memory_manager,
   )
   ```
6. 第一轮 intention 前，把记忆上下文打包进 payload：
   ```python
   memory_context = {
       "recent_dialogue": memory_manager.short_term.get_context_string(3),
       "merchant_profile": memory_manager.get_merchant_context(),
       "similar_patterns": await memory_manager.find_similar_patterns(query),
   }
   intention_payload["memory_context"] = memory_context
   ```
7. 诊断完成后，记录助手回复并统一持久化：
   ```python
   memory_manager.add_message(
       "assistant",
       diagnosis.get("diagnosis", {}).get("summary", ""),
       metadata={"trace_id": trace_id},
   )
   await memory_manager.record_diagnosis({
       "trace_id": trace_id,
       "ticket_id": diagnosis.get("ticket_id", ""),
       "merchant_id": facts.get("merchant_id", ""),
       "issue_type": facts.get("issue_type", ""),
       "scenario": diagnosis.get("scenario", ""),
       "summary": diagnosis["diagnosis"].get("summary", ""),
       "responsible_party": diagnosis["diagnosis"].get("responsible_party", ""),
       "root_cause": diagnosis["diagnosis"].get("root_cause", ""),
   })
   ```
8. 保留 `self.long_term_memory` 属性用于 CLI 的 `show_status` / `show_history` / `clear_history` 兼容；或改为在需要时从 `MemoryManager` 访问。
9. `get_metrics()` 中 `registered_agents` 改为基于已知专业 Agent 列表或 `orchestrator.agent_registry.keys()` 计算（若每次创建，则无法依赖实例 registry）。

### Step 5: `IntentionAgent` 利用记忆上下文

**文件**: [agents/diagnosis_intention_agent.py](file:///Users/yuchen/Learning-library/demo/小哈工单智能诊断助手agent/agents/diagnosis_intention_agent.py)

- `reply()` 中解析 `memory_context` 字段。
- 把 `recent_dialogue`、`merchant_profile`、`similar_patterns` 追加到 `reasoning` 文本中，用于丰富意图理解。
- **不修改** `_build_entities`、`_scenario_from_issue`、`_build_schedule`，确保 scenario / key_entities / agent_schedule 与现有测试一致。

### Step 6: 调整 CLI 与会话连续性

**文件**: [cli.py](file:///Users/yuchen/Learning-library/demo/小哈工单智能诊断助手agent/cli.py)

- `DiagBotCLI.__init__` 中生成固定 `session_id`。
- `process_query()` 调用 `diagnose(query, session_id=self.session_id)`。
- `show_status` / `show_history` / `clear_history` 继续通过 `self.diagnosis_service.long_term_memory` 访问；若该属性移除，则改为访问最后一次 diagnose 创建的 `MemoryManager.long_term` 或保留默认 `LongTermMemory` 实例。

### Step 7: API 层改为 per-request 创建 Service

**文件**: [api/app.py](file:///Users/yuchen/Learning-library/demo/小哈工单智能诊断助手agent/api/app.py)

- 移除全局 `diagnosis_service` 单例。
- 保留全局 `trace_repo = TraceRepository()`，供 `/diagnose` 写入和 `/trace` 查询共享。
- `/diagnose` 根据请求创建新的 `DiagnosisService`：
  ```python
  @app.post("/api/v1/diagnose")
  async def diagnose(
      request: DiagnoseRequest,
      x_user_id: str | None = Header(None),
      x_session_id: str | None = Header(None),
  ):
      user_id = request.user_id or x_user_id or "api_user"
      session_id = request.session_id or x_session_id or str(uuid.uuid4())[:8]
      service = DiagnosisService(trace_repo=trace_repo, user_id=user_id)
      return await service.diagnose(request.query, session_id=session_id)
  ```
- `DiagnoseRequest` 增加 `user_id`、`session_id` 可选字段。
- `/metrics` 使用默认 service 或从 `trace_repo` 计算 `trace_count`；`total_diagnoses` 可改为 `trace_repo.count()`。
- `/trace/*` 使用共享 `trace_repo` 直接返回，不依赖 service 实例。

### Step 8: 新增/更新测试

**新增文件**:
- `tests/test_skill_orchestration.py`：验证专业 Agent 第一轮调用了扩展后的 Skill。
- `tests/test_memory_integration.py`：验证诊断后长期记忆、商户画像文件被写入。

**修改文件**:
- `tests/test_diagnosis_api.py`：验证 `/diagnose` 支持 `user_id` / `session_id`。

**保持不变的文件**:
- `tests/test_diagnosis_service.py`：由于第二轮/第三轮逻辑不变，现有断言应继续通过。
- `tests/test_batch2_skills.py`：Skill 执行逻辑不变。

---

## Verification

1. 运行现有核心测试，确保诊断结论不变：
   ```bash
   python -m pytest tests/test_diagnosis_service.py tests/test_batch2_skills.py tests/test_intention_agent_compat.py -q
   ```

2. 运行新增测试，验证 Skill 编排和记忆写入：
   ```bash
   python -m pytest tests/test_skill_orchestration.py tests/test_memory_integration.py tests/test_diagnosis_api.py -q
   ```

3. 运行 CLI 快速冒烟：
   ```bash
   echo "请诊断工单 WO-20260815-0421" | python cli.py
   ```

4. 运行 API 快速冒烟：
   ```bash
   curl -X POST "http://127.0.0.1:8000/api/v1/diagnose" \
     -H "Content-Type: application/json" \
     -d '{"query":"请诊断工单 WO-20260815-0421","user_id":"test_user"}'
   ```

---

## Risks and Mitigations

| 风险 | 影响 | 规避措施 |
|------|------|----------|
| 扩展 `allowed_skills` 后改变第二轮输出 | 现有测试断言失败 | 仅扩展 `_round_one`，第二/三轮逻辑不变；LoopDecider 看到的 `inconsistency_found` 不变 |
| `MemoryManager` 与 `OrchestrationAgent` 双重写入长期记忆 | 诊断历史重复 | `OrchestrationAgent._update_memory` 在 ResolutionAgent 未产出结论时只写入 chat；Service 统一调用 `record_diagnosis`；必要时给 `OrchestrationAgent` 加 `persist_memory=False` |
| API 并发共享有状态 service | 会话/商户画像串扰 | `/diagnose` 每次创建新的 `DiagnosisService` + `MemoryManager`，只共享 `TraceRepository` |
| `merchant_id` 在第一次 intention 时未知 | 商户画像无法首轮使用 | ticket 加载后调用 `set_merchant_id`；画像用于后续轮次和模式检索 |
| CLI 多查询短期记忆不连续 | 同一 CLI 会话上下文丢失 | CLI 启动时固定 `session_id`，每次查询传入 |
| 测试污染 `data/memory` | 留下测试数据 | 新增记忆测试使用 `tmp_path`；既有测试逐步迁移 |

---

## Critical Files to Modify

- [context/memory_manager.py](file:///Users/yuchen/Learning-library/demo/小哈工单智能诊断助手agent/context/memory_manager.py)
- [agents/code_agent.py](file:///Users/yuchen/Learning-library/demo/小哈工单智能诊断助手agent/agents/code_agent.py)
- [agents/operation_agent.py](file:///Users/yuchen/Learning-library/demo/小哈工单智能诊断助手agent/agents/operation_agent.py)
- [agents/data_agent.py](file:///Users/yuchen/Learning-library/demo/小哈工单智能诊断助手agent/agents/data_agent.py)
- [agents/resolution_agent.py](file:///Users/yuchen/Learning-library/demo/小哈工单智能诊断助手agent/agents/resolution_agent.py)
- [services/diagnosis_service.py](file:///Users/yuchen/Learning-library/demo/小哈工单智能诊断助手agent/services/diagnosis_service.py)
- [agents/diagnosis_intention_agent.py](file:///Users/yuchen/Learning-library/demo/小哈工单智能诊断助手agent/agents/diagnosis_intention_agent.py)
- [cli.py](file:///Users/yuchen/Learning-library/demo/小哈工单智能诊断助手agent/cli.py)
- [api/app.py](file:///Users/yuchen/Learning-library/demo/小哈工单智能诊断助手agent/api/app.py)
- `tests/test_skill_orchestration.py`（新增）
- `tests/test_memory_integration.py`（新增）
- [tests/test_diagnosis_api.py](file:///Users/yuchen/Learning-library/demo/小哈工单智能诊断助手agent/tests/test_diagnosis_api.py)
