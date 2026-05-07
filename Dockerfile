# ── 构建阶段：安装依赖 ──────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# 安装依赖（利用缓存层）
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── 运行阶段 ────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# 从构建阶段复制已安装的包
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 复制应用代码
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# 创建配置文件持久化目录
RUN mkdir -p /data

# 环境变量
ENV PYTHONPATH=/app/backend \
    PYTHONUNBUFFERED=1 \
    CONFIG_PATH=/data \
    PORT=8000

EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# 启动命令（生产模式，不启用 reload）
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT} --log-level info --app-dir /app/backend"]
