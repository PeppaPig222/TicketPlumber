# 改造计划：Batch 1 最终收口 — LazyAgentRegistry + 专业 Agent 插件化

## Context

当前 `docs/改造TODO.md` 中 Batch 1 顶层仍有两项未勾选：

- [ ] 多 Agent 角色层真正落地，而不是以 workflow runner 为主
- [ ] 回到 LazyAgentRegistry + Skill 插件化的主链路

虽然代码中已经存在 `IntentionAgent -> OrchestrationAgent -> LazyAgentRegistry -> 专业 Agent` 的分层，但 `DiagnosisService` 在创建 `LazyAgentRegistry` 时仍然通过 `custom_factories` 硬编码了 `CodeAgent / OperationAgent / DataAgent / ResolutionAgent` 的工厂函数。这导致 `.claude/skills/` 插件化目录下只有 `ask-question / query-info / memory-query` 三个与诊断无关的旧 skill，LazyAgentRegistry 对诊断主链路形同虚设。

本计划目标：在保持现有诊断流程不变、不改动原子 Skill 和判责逻辑的前提下，把 4 个专业诊断 Agent 迁移为 `.claude/skills/` 下的插件，让 `LazyAgentRegistry` 真正扫描加载它们，从而完成 Batch 1 的收口。

## 推荐方案

采用「skill 目录插件优先，custom_factories 兜底」的最小改动方案：

1. 扩展 `LazyAgentRegistry`，使其支持通过 `agent_kwargs` 向插件 Agent 注入额外初始化参数（如 `skill_registry`）。
2. 调整 `LazyAgentRegistry` 的解析优先级：同名 skill 目录优先于 `custom_factories`，使插件化真正生效。
3. 在 `.claude/skills/` 下新增 4 个插件目录：
   - `code-agent/`: 代码/接口链路诊断 Agent
   - `operation-agent/`: 操作/配置侧诊断 Agent
   - `data-agent/`: 数据一致性诊断 Agent
   - `resolution-agent/`: 交叉验证与归属判定 Agent
   每个目录包含 `SKILL.md`（职责说明）和 `script/agent.py`（导出对应专业 Agent 子类）。
4. 修改 `services/diagnosis_service.py`：
   - 创建 `LazyAgentRegistry` 时传入 `agent_kwargs={"skill_registry": self.skill_registry}`。
   - 移除对 4 个专业 Agent 的硬编码 `custom_factories`，改为由 LazyAgentRegistry 自动发现；保留 `custom_factories` 接口用于其他兜底场景（但不用于这 4 个 Agent）。
5. 不改动 `agents/diagnosis_agents.py`、`skills/registry.py` 中的原子 Skill 注册逻辑。
6. 不改动 4 个专业 Agent 内部的 `allowed_skills` 和 `_round_one` 编排逻辑。

## 关键文件

- `agents/lazy_agent_registry.py` —— 扩展 `agent_kwargs` 与 skill 目录优先逻辑
- `.claude/skills/code-agent/SKILL.md` —— 新建
- `.claude/skills/code-agent/script/agent.py` —— 新建
- `.claude/skills/operation-agent/SKILL.md` —— 新建
- `.claude/skills/operation-agent/script/agent.py` —— 新建
- `.claude/skills/data-agent/SKILL.md` —— 新建
- `.claude/skills/data-agent/script/agent.py` —— 新建
- `.claude/skills/resolution-agent/SKILL.md` —— 新建
- `.claude/skills/resolution-agent/script/agent.py` —— 新建
- `services/diagnosis_service.py` —— 调整 LazyAgentRegistry 创建方式
- `tests/test_lazy_agent_registry.py` —— 新增/更新，验证插件加载
- `tests/test_diagnosis_service.py` —— 回归验证

## 实施步骤

1. 扩展 `LazyAgentRegistry`：
   - `__init__` 增加 `agent_kwargs: Optional[Dict[str, Any]] = None`。
   - 在 `__getitem__` 中，判断 agent_name 是否直接命中 skill_map；命中则走 skill 加载流程。
   - skill 加载实例化时，把 `agent_kwargs` 作为 `**kwargs` 传入 Agent 构造函数。

2. 新建 4 个专业 Agent 插件目录：
   - 每个 `SKILL.md` 描述 Agent 名称、触发场景、输入输出。
   - 每个 `script/agent.py` 从 `agents` 包导入真实 Agent 类，定义一个同名的子类（避免循环继承），供 `LazyAgentRegistry` 发现并实例化。

3. 调整 `DiagnosisService`：
   - 在 `diagnose()` 中创建 `LazyAgentRegistry` 时，传入 `agent_kwargs={"skill_registry": self.skill_registry, "memory_manager": memory_manager}`。
   - 不再在 `custom_factories` 中定义这 4 个专业 Agent。

4. 测试与回归：
   - 新增 `tests/test_lazy_agent_registry.py`，验证 `LazyAgentRegistry` 能加载 `code-agent` 等 4 个插件。
   - 运行 `tests/test_diagnosis_service.py`、`tests/test_diagnosis_api.py`、`tests/test_skill_orchestration.py` 等核心测试，确保诊断结果不变。

## 验证方式

运行以下测试命令，应全部通过：

```bash
python -m pytest tests/test_diagnosis_service.py tests/test_diagnosis_api.py tests/test_skill_orchestration.py tests/test_memory_integration.py tests/test_code_agent.py tests/test_operation_agent.py tests/test_data_agent.py tests/test_resolution_agent.py tests/test_lazy_agent_registry.py -q
```

同时通过快速脚本验证 `LazyAgentRegistry` 的 `keys()` 中包含 `code-agent`、`operation-agent`、`data-agent`、`resolution-agent`，并能返回对应 Agent 实例。

## 风险与回退

- **风险**：修改 `LazyAgentRegistry` 的解析优先级可能影响现有的 legacy mapping（`rag_knowledge` -> `ask-question` 等）。回退方式：保留 `custom_factories` 接口，必要时恢复硬编码工厂。
- **风险**：插件目录下的 `script/agent.py` 动态加载时可能因 `sys.path` 问题找不到 `agents` 包。回退方式：在 `agent.py` 中显式添加项目根目录到 `sys.path`（与现有 ask-question 等 skill 保持一致）。
- **范围控制**：本计划只完成 Batch 1 收口，不触及 Batch 3-7（RAG、前端、Docker、评测体系等）。
