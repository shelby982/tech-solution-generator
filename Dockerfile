# ── 构建阶段：安装依赖 ──────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# onnxruntime / RapidOCR 在 slim 基础镜像下需要这些系统库
#   libgomp1      —— onnxruntime 的 OpenMP 运行时
#   libgl1        —— OpenCV（RapidOCR 依赖）的 GL 依赖
#   libglib2.0-0  —— OpenCV 的 glib 依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# 构建期验证 RapidOCR 可用（模型随包分发，不下载）。
# 但**不能因为这一步失败就整体构建失败**：OCR 只是"图片素材识别"的增强能力，
# 线上优先保证服务能起来，OCR 初始化失败时运行期会降级（日志告警，不阻塞请求）。
RUN python -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR(); print('rapidocr ready')" \
    || echo "[warn] RapidOCR 预热失败，构建继续；运行期将按需初始化"

# ── 运行阶段 ────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# 运行期同样需要这些系统库（OCR / 图像处理 / 中文渲染）
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 libgl1 libglib2.0-0 \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 设计版图表（Diagram Design）用 Chromium 渲染 HTML→PNG。
# 浏览器装到 /app/.playwright-browsers —— 与 backend/services/diagram_render.py 的
# 自动探测路径一致（它会在 PLAYWRIGHT_BROWSERS_PATH 未设置时用这个目录）。
ENV PLAYWRIGHT_BROWSERS_PATH=/app/.playwright-browsers
RUN playwright install --with-deps chromium \
    || echo "[warn] Chromium 安装失败，构建继续；图表渲染将走降级路径"

COPY backend/ ./backend/
COPY frontend/ ./frontend/

RUN mkdir -p /data

ENV PYTHONPATH=/app/backend \
    PYTHONUNBUFFERED=1 \
    CONFIG_PATH=/data \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT} --log-level info --app-dir /app/backend"]
