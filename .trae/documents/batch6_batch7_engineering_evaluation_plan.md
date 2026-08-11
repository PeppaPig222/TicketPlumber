# Batch 6 + Batch 7 工程化与评测体系收口计划

## 背景与目标

`改造TODO.md` 中 Batch 6（工程化）与 Batch 7（评测体系）尚未完全收口。本计划目标：

- **Batch 6**：完成配置管理（pydantic-settings + .env）、全局结构化日志、Docker 容器化、健康监控 endpoint 的落地。
- **Batch 7**：确认评测数据集与 runner 已就绪，补齐 Markdown 报告输出，更新 TODO 状态并写入开发手记。
- 全部改动完成后回归测试，确保现有诊断链路不 regress。

---

## 当前现状

### 已具备基础

| 项目 | 状态 | 文件 |
|---|---|---|
| 配置模块 | 普通 dict | `config.py` |
| 结构化日志工具 | 已实现，未全局启用 | `utils/logging_config.py` |
| trace_id 上下文 | 已实现 | `utils/logging_config.py`, `services/diagnosis_service.py` |
| FastAPI /health | 简单返回 ok | `api/app.py:56-58` |
| FastAPI /metrics | 依赖 DiagnosisService.get_metrics | `api/app.py:61-66` |
| 前端挂载 | 已挂载 `/panel` | `api/app.py:39-40` |
| 评测数据集 | 44 条已落地 | `data/evaluation/core_eval_set.json` |
| 评测 runner | 已实现 JSON 报告 | `scripts/run_evaluation.py` |
| Dockerfile / docker-compose / .env | 不存在 | - |
| pytest 配置 | 不存在 | - |

### 主要缺口

- `config.py` 未使用 `pydantic-settings`，不支持 `.env` 和环境变量。
- `setup_logging` 未在 `api/app.py` / `cli.py` 入口全局调用。
- 大量模块仍使用默认日志格式，未接入 JSON trace_id 链路。
- `/health` 仅返回字符串，缺少依赖状态检查。
- `/metrics` 缺少系统级指标（内存、CPU、请求耗时分布）。
- 缺少 Dockerfile 与 docker-compose。
- 评测 runner 缺少 Markdown 报告输出。

---

## 改造方案

### 一、Batch 6.1 配置管理：pydantic-settings + 环境变量

#### 1.1 新增 `config/settings.py`

用 `pydantic-settings` 定义分层配置：

```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DIAG_LLM_")
    api_key: str = "API_KEY"
    model_name: str = "Model_Name"
    base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    temperature: float = 0.7
    max_tokens: int = 8192


class SystemSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DIAG_SYS_")
    enable_llm: bool = True
    log_level: str = "INFO"
    max_retries: int = 3
    timeout: int = 60
    env: str = "development"  # development / testing / production


class RAGSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DIAG_RAG_")
    embedding_model: str = "data/models/bge-small-zh-v1.5"
    enable_intention_fallback: bool = True
    enable_resolution_evidence: bool = True
    min_similarity_threshold: float = 0.55


class ResilienceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DIAG_RES_")
    max_retries: int = 3
    retry_base_delay_sec: float = 1.0
    retry_max_delay_sec: float = 30.0
    circuit_failure_threshold: int = 5
    circuit_recovery_timeout_sec: float = 60.0
    circuit_half_open_successes: int = 2
    health_check_timeout_sec: float = 10.0
    skill_timeout_sec: float = 5.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "DiagBot 工单智能诊断助手"
    project_name: str = "diagbot-ticket-diagnosis"
    project_display_name: str = "小哈工单智能诊断助手"

    llm: LLMSettings = LLMSettings()
    system: SystemSettings = SystemSettings()
    rag: RAGSettings = RAGSettings()
    resilience: ResilienceSettings = ResilienceSettings()


settings = Settings()
```

#### 1.2 新增 `.env.example`

```bash
# LLM
DIAG_LLM_API_KEY=your_api_key
DIAG_LLM_MODEL_NAME=doubao-pro-32k
DIAG_LLM_BASE_URL=https://ark.cn-beijing.volces.com/api/v3

# System
DIAG_SYS_LOG_LEVEL=INFO
DIAG_SYS_ENV=development

# RAG
DIAG_RAG_EMBEDDING_MODEL=data/models/bge-small-zh-v1.5
```

#### 1.3 兼容旧 `config.py`

保留 `config.py`，但其内部从 `config/settings.py` 导出兼容字典，避免大量文件需要立即修改：

```python
from config.settings import settings

APP_CONFIG = {
    "app_name": settings.app_name,
    ...
}
LLM_CONFIG = settings.llm.model_dump()
SYSTEM_CONFIG = settings.system.model_dump()
RAG_CONFIG = settings.rag.model_dump()
RESILIENCE_CONFIG = settings.resilience.model_dump()
```

#### 涉及文件

- 新增：`config/settings.py`、`.env.example`
- 修改：`config.py`（做兼容层）
- 依赖：`requirements.txt` 已含 `pydantic-settings>=2.5.0`

---

### 二、Batch 6.2 日志体系：全局启用 + 全链路 trace_id

#### 2.1 在入口全局调用 `setup_logging`

文件：`api/app.py`、`cli.py`

```python
from utils.logging_config import setup_logging
from config import SYSTEM_CONFIG

setup_logging(level=SYSTEM_CONFIG["log_level"])
```

#### 2.2 API 请求中间件注入 trace_id

在 `api/app.py` 增加 middleware：

```python
from utils.logging_config import set_trace_id

@app.middleware("http")
async def trace_id_middleware(request, call_next):
    trace_id = request.headers.get("x-trace-id") or str(uuid.uuid4())[:8]
    set_trace_id(trace_id)
    response = await call_next(request)
    response.headers["x-trace-id"] = trace_id
    return response
```

#### 2.3 Agent / Skill / Tool 日志带结构化字段

通过 `logging.LoggerAdapter` 或 `extra` 字段，在关键节点输出：

```python
logger.info(
    "Agent 执行完成",
    extra={
        "agent_name": agent_name,
        "round_num": round_num,
        "ticket_id": ticket_id,
        "duration_ms": duration_ms,
    }
)
```

优先改造位置：
- `services/diagnosis_service.py`：诊断开始/结束/异常
- `agents/orchestration_agent.py`：round 调度
- `agents/diagnosis_agents.py`：skill 执行结果
- `utils/tool_registry.py`：tool 执行耗时与状态

#### 涉及文件

- `api/app.py`
- `cli.py`
- `utils/logging_config.py`（可选增强：增加 log_file 文件输出）
- `services/diagnosis_service.py`
- `agents/orchestration_agent.py`
- `agents/diagnosis_agents.py`
- `utils/tool_registry.py`

---

### 三、Batch 6.3 容器化：Dockerfile + docker-compose

#### 3.1 `Dockerfile`

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码
COPY . .

# 创建必要目录
RUN mkdir -p data/memory data/rag_knowledge data/models web frontend/dist

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/health').raise_for_status()" || exit 1

CMD ["python", "-m", "uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

#### 3.2 `docker-compose.yml`

```yaml
version: "3.8"

services:
  diagbot:
    build: .
    container_name: diagbot
    ports:
      - "8000:8000"
    environment:
      - DIAG_SYS_ENV=production
      - DIAG_SYS_LOG_LEVEL=INFO
      - DIAG_LLM_API_KEY=${DIAG_LLM_API_KEY}
      - DIAG_LLM_MODEL_NAME=${DIAG_LLM_MODEL_NAME}
    volumes:
      - ./data:/app/data
      - ./.env:/app/.env:ro
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import requests; requests.get('http://localhost:8000/health').raise_for_status()"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
```

#### 3.3 `.dockerignore`

```
__pycache__/
*.pyc
.venv/
.git/
frontend/node_modules/
frontend/dist/
web/
data/memory/*.json
data/rag_knowledge/*.db
.env
```

注意：前端构建产物 `frontend/dist` 需要本地先 `npm run build`，再 COPY 进镜像。README 中补充说明。

---

### 四、Batch 6.4 健康与监控：/health + /metrics

#### 4.1 丰富 `/health`

返回依赖状态：

```python
@app.get("/health")
async def health():
    health_status = {
        "status": "ok",
        "service": "xiaoha-ticket-diagnosis",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat(),
        "dependencies": {
            "llm": await _check_llm(),
            "rag": await _check_rag(),
            "memory": _check_memory(),
        },
    }
    return health_status
```

依赖检查逻辑：
- LLM：检查 `LLM_CONFIG["api_key"]` 是否已配置且不是占位符。
- RAG：检查 `data/rag_knowledge/milvus_lite.db` 是否存在或可连接。
- Memory：检查 `data/memory` 目录可写。

#### 4.2 丰富 `/metrics`

在 `DiagnosisService.get_metrics` 基础上增加：

```python
import psutil
import time

{
    "diagnosis_count": ...,
    "trace_count": ...,
    "avg_diagnosis_duration_ms": ...,
    "system": {
        "cpu_percent": psutil.cpu_percent(),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_usage_percent": psutil.disk_usage("/").percent,
    },
    "uptime_seconds": time.time() - START_TIME,
}
```

需要新增 `psutil` 依赖到 `requirements.txt`。

#### 涉及文件

- `api/app.py`
- `services/diagnosis_service.py`
- `requirements.txt`

---

### 五、Batch 7 评测体系收口

#### 5.1 现状确认

- `data/evaluation/core_eval_set.json` 已包含 44 条评测数据。
- `scripts/run_evaluation.py` 已实现批量执行与 JSON 报告。
- 指标覆盖：scenario_accuracy、responsible_party_accuracy、root_cause_hit_rate、rounds_accuracy、pass_at_1、by_category。

#### 5.2 补齐 Markdown 报告输出

在 `scripts/run_evaluation.py` 中新增 `save_markdown_report(report)`：

```python
def save_markdown_report(report: Dict[str, Any]):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = REPORT_DIR / f"eval_report_{timestamp}.md"

    lines = [
        "# 小哈工单智能诊断助手 — 离线评测报告",
        f"\n- 数据集: {report['dataset']}",
        f"- 用例数: {report['total_cases']}",
        f"- 时间: {report['timestamp']}",
        "\n## 总体指标",
        ...
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Markdown 报告已保存: {md_path}")
```

Markdown 报告包含：
- 总体指标表格
- 分类 Pass@1 表格
- 失败用例明细表格
- 关键结论一句话总结

#### 5.3 更新 `改造TODO.md`

将 Batch 7 所有子项标记为 `[x]`。

---

## 实施顺序

按依赖关系，建议以下顺序：

1. **配置管理**（Batch 6.1）：先落地 pydantic-settings，后续日志/健康检查可读取配置。
2. **日志体系**（Batch 6.2）：全局启用，改造 API/Service/Agent/Tool 关键节点。
3. **健康监控**（Batch 6.4）：依赖配置和日志已经就绪。
4. **容器化**（Batch 6.3）：最后做，因为依赖前面配置稳定。
5. **评测收口**（Batch 7）：并行做 Markdown 报告和 TODO 更新。

---

## 涉及文件清单

### 新增

- `config/settings.py`
- `.env.example`
- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`
- `pytest.ini`（可选）

### 修改

- `config.py`：改为兼容层，从 settings 导出旧字典。
- `api/app.py`：
  - 全局 `setup_logging`
  - 增加 trace_id middleware
  - 丰富 `/health`
  - 丰富 `/metrics`
- `cli.py`：全局 `setup_logging`。
- `utils/logging_config.py`：可选增强文件输出。
- `services/diagnosis_service.py`：结构化日志字段、metrics 增强。
- `agents/orchestration_agent.py`：结构化日志字段。
- `agents/diagnosis_agents.py`：结构化日志字段。
- `utils/tool_registry.py`：结构化日志字段。
- `requirements.txt`：新增 `psutil`。
- `scripts/run_evaluation.py`：新增 Markdown 报告。
- `docs/开发手记.md`：记录 Batch 6/7 收口内容。
- `.trae/documents/改造TODO.md`：更新 Batch 6/7 勾选状态。

---

## 验收标准

1. `python -c "from config import SYSTEM_CONFIG; print(SYSTEM_CONFIG['log_level'])"` 可读取 `.env` 中的配置。
2. `python -m uvicorn api.app:app --port 8080` 启动后：
   - `/health` 返回包含 dependencies 的 JSON。
   - `/metrics` 返回包含 system 的 JSON。
   - 请求 `/api/v1/diagnose` 后，日志输出 JSON 格式且带 `trace_id`。
3. `python cli.py` 启动后，日志输出 JSON 格式。
4. `docker compose up --build` 能拉起服务，且 `/health` 返回 ok。
5. `python scripts/run_evaluation.py` 输出 JSON + Markdown 两份报告。
6. 运行回归测试：
   ```bash
   pytest tests/test_diagnosis_service.py tests/test_diagnosis_api.py tests/test_intention_agent.py tests/test_rag_integration.py
   ```
   关键测试全部通过。

---

## 风险与缓解

| 风险 | 缓解措施 |
|---|---|
| pydantic-settings 重构破坏旧配置引用 | `config.py` 保留旧字典导出，业务代码逐步迁移 |
| JSON 日志影响本地开发可读性 | `.env` 中 `DIAG_SYS_ENV=development` 时可切换为可阅读格式 |
| Docker 构建慢 / 镜像大 | 使用 `python:3.11-slim`，`.dockerignore` 排除 node_modules 和 db |
| 健康检查依赖 LLM API 超时 | 健康检查只做配置校验，不真正调用 LLM |
| 增加依赖导致测试失败 | 每次修改后跑回归测试 |
