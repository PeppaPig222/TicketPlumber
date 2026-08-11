# ============================================================
# 小哈工单智能诊断助手 — Docker 镜像
# ============================================================
FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖：gcc 用于编译部分 Python 包，libgomp1 用于 OpenMP
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖清单并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目源码
COPY . .

# 创建必要的数据目录
RUN mkdir -p data/memory data/rag_knowledge data/models data/documents data/evaluation/reports

# 暴露服务端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()" || exit 1

# 启动命令
CMD ["python", "-m", "uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
