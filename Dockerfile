# ── 构建阶段：安装依赖 ──────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# onnxruntime 在 slim 基础镜像下需要 libgomp1
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# 镜像构建期触发一次 RapidOCR 初始化（验证依赖可用，模型已随包分发，无网络下载）
RUN python -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR(); print('rapidocr ready')"

# ── 运行阶段 ────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# 同款运行时依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY backend/ ./backend/
COPY frontend/ ./frontend/

RUN mkdir -p /data

ENV PYTHONPATH=/app/backend \
    PYTHONUNBUFFERED=1 \
    CONFIG_PATH=/data \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT} --log-level info --app-dir /app/backend"]
