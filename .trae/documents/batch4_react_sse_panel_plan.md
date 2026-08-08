# Batch 4：React + TypeScript + SSE 实时诊断追踪面板计划

## 背景与目标

当前前端是一个静态 HTML 页面（`web/index.html`），通过 `fetch /api/v1/diagnose` 获取完整诊断结果后一次性渲染。这种方式有两个不足：

1. 用户看不到诊断过程的逐步推进，无法体现多 Agent 协作的价值。
2. 与后端 SSE 能力（`/api/v1/trace/stream/{trace_id}`）没有打通。

Batch 4 目标是用 React + TypeScript 重做一个最小可用的诊断追踪面板：

- 左侧展示 Agent 思考链 / Round 时间线
- 右侧展示诊断结论卡片
- 通过 SSE 流式回放 trace 事件
- 红/黄/绿区分不同状态（成功/降级/错误）
- 展示总耗时、每轮决策、工具调用

遵循最小改动原则：后端只补 CORS 和静态文件挂载；前端用 Vite 新建独立工程，不改动现有诊断核心逻辑。

---

## 方案概述

### 1. 前端工程结构

新建 `frontend/` 目录：

```
frontend/
├── package.json              # React 18 + TypeScript + Vite + 无 UI 库（手写样式）
├── tsconfig.json
├── vite.config.ts            # 开发代理 /api -> http://localhost:8000
├── index.html
└── src/
    ├── main.tsx              # React 入口
    ├── App.tsx               # 页面布局
    ├── api.ts                # 封装 /diagnose + /trace/stream SSE
    ├── types.ts              # Trace / Agent / Round 类型定义
    └── components/
        ├── DiagnosisInput.tsx      # 输入区
        ├── AgentTimeline.tsx       # 左侧 Agent 时间线
        ├── DiagnosisConclusion.tsx # 右侧结论卡片
        └── StatusBadge.tsx         # 状态标签
```

技术选型：

- **Vite**：启动快、配置简单、对 TypeScript 支持好。
- **React 18 + Hooks**：useState / useEffect / useRef 足够管理 SSE 和面板状态。
- **无 UI 组件库**：手写 CSS，避免引入额外依赖和学习成本。
- **SSE（EventSource）**：浏览器原生支持，无需 WebSocket 或第三方库。

### 2. 后端改动（最小）

文件：`api/app.py`

1. **增加 CORS**：开发时前端跑在 `http://localhost:5173`，需要允许跨域访问 `http://localhost:8000`。
2. **静态文件挂载**：生产/演示时直接让 FastAPI 伺服 `frontend/dist/`。

示例：

```python
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载构建产物
if (Path(__file__).parent.parent / "frontend" / "dist").exists():
    app.mount("/panel", StaticFiles(directory="frontend/dist", html=True))
```

现有 `/` 路由仍返回旧版 `web/index.html`，不影响兼容性。

### 3. 数据流设计

```
用户输入 → POST /api/v1/diagnose → 拿到 {trace_id, ...}
                                     ↓
                 EventSource GET /api/v1/trace/stream/{trace_id}
                                     ↓
         逐条解析 SSE 事件：agent_update / round_complete / diagnosis_complete
                                     ↓
                            更新 React state → 渲染
```

注意：当前后端 SSE 是**回放式**（diagnose 完成后从 trace_repo 读取 events），不是严格实时流。这符合最小改动原则，前端体验仍是“事件逐条出现”。后续如需真实时，可再改造 DiagnosisService。

### 4. 前端状态结构

```typescript
interface AgentNode {
  agent_name: string;
  priority: number;
  status: "success" | "error" | "timeout" | "degraded" | "unknown";
  duration_ms: number;
  output_summary: string;
  tools_called: string[];
  recommended_skills: string[];
  evidence: string[];
}

interface Round {
  round_num: number;
  intent: string;
  duration_ms: number;
  decision: string;
  agents: AgentNode[];
}

interface DiagnosisResult {
  status: string;
  scenario: string;
  trace: { rounds: Round[]; total_duration_ms: number };
  diagnosis: {
    summary: string;
    root_cause: string;
    responsible_party: string;
    recommendations: string[];
  };
}
```

### 5. 组件设计

#### DiagnosisInput
- 输入框 + 开始诊断按钮
- 支持常用示例工单（三个高频场景）
- 诊断中禁用按钮

#### AgentTimeline
- 按 Round 分组展示 Agent 节点
- 每个 Agent 节点显示：名称、状态徽章、耗时、summary、tools_called
- 状态颜色：success 绿色、degraded 黄色/橙色、error 红色、unknown 灰色
- Round 之间用时间线和 decision 连接

#### DiagnosisConclusion
- 场景、责任方、摘要、根因、建议
- 总耗时展示
- 诊断状态（completed / partial_failure / need_info）

#### StatusBadge
- 根据 status 返回不同颜色的小标签

### 6. 样式设计

- 整体：浅色背景 `#f5f7fb`，卡片白色圆角阴影
- 布局：桌面端左右两栏（左侧 timeline 60%，右侧结论 40%），移动端堆叠
- 成功路径：左侧竖线绿色
- 降级路径：左侧竖线橙色
- 错误路径：左侧竖线红色
- Agent 节点：`status` 决定左边框颜色

### 7. 开发/构建/部署

```bash
# 进入前端工程
cd frontend

# 安装依赖
npm install

# 开发（自动代理 API 到 localhost:8000）
npm run dev

# 构建
npm run build

# 后端启动后访问
# http://localhost:8000/          旧版页面
# http://localhost:8000/panel     新版 React 面板
```

---

## 关键文件清单

| 文件 | 改动 |
|---|---|
| `frontend/package.json` | 新建：项目依赖与脚本 |
| `frontend/tsconfig.json` | 新建：TypeScript 配置 |
| `frontend/vite.config.ts` | 新建：开发代理 |
| `frontend/index.html` | 新建：入口 HTML |
| `frontend/src/main.tsx` | 新建：React 入口 |
| `frontend/src/App.tsx` | 新建：页面布局与状态 |
| `frontend/src/api.ts` | 新建：API + SSE 封装 |
| `frontend/src/types.ts` | 新建：类型定义 |
| `frontend/src/components/DiagnosisInput.tsx` | 新建 |
| `frontend/src/components/AgentTimeline.tsx` | 新建 |
| `frontend/src/components/DiagnosisConclusion.tsx` | 新建 |
| `frontend/src/components/StatusBadge.tsx` | 新建 |
| `api/app.py` | 修改：增加 CORS + 静态文件挂载 |
| `docs/开发手记.md` | 修改：新增 Batch 4 实施记录 |
| `docs/改造TODO.md` | 修改：标记 Batch 4 完成 |

---

## 验证方式

1. **开发环境**：
   - 后端：`uvicorn api.app:app --reload --port 8000`
   - 前端：`cd frontend && npm run dev`
   - 浏览器访问 `http://localhost:5173/`
   - 输入工单，观察 SSE 事件逐条渲染

2. **构建验证**：
   - `cd frontend && npm run build`
   - 确认 `frontend/dist/` 生成
   - 访问 `http://localhost:8000/panel` 验证面板可用

3. **后端测试**：
   - `pytest tests/test_diagnosis_api.py` 通过
   - `pytest tests/test_diagnosis_service.py` 通过

4. **兼容性**：
   - `http://localhost:8000/` 仍返回旧版 `web/index.html`

---

## 风险与回退

- **风险**：SSE 是回放式，agent_update 事件会一次性快速出现，可能不像真实时流。
  - **缓解**：前端在每个事件之间加 200-400ms 动画延迟，提升视觉上的“逐步推进”感。
- **风险**：`frontend/dist` 目录不存在时 `/panel` 路由 404。
  - **缓解**：只在 `dist` 存在时挂载，README 中说明先 `npm run build`。
- **风险**：用户未安装 Node.js/npm。
  - **缓解**：保留旧版 `web/index.html`，新项目仍可跑后端 API。

---

## 计划完成后的开发手记条目

1. **为什么选 Vite + 手写 CSS**：对初学者友好、构建快、不增加心智负担。
2. **SSE 回放 vs 实时流**：当前后端 trace_repo 的存储方式决定了回放式 SSE，如何在前端模拟逐步渲染。
3. **状态颜色语义设计**：红/黄/绿分别对应什么系统状态，如何与 trace 的 status 字段映射。
4. **前后端联调踩坑**：CORS、静态文件挂载、开发代理配置。
