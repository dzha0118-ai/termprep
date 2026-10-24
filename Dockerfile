FROM python:3.13-slim

WORKDIR /app

# Install system dependencies (file parsers + OCR)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    antiword \
    catdoc \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml requirements.txt ./
COPY termprep/ termprep/
COPY web/ web/
COPY web_entry.py .

# Debug: list all files in termprep/ to verify upload completeness
RUN echo "=== termprep/ contents ===" && ls -la termprep/ && echo "=== termprep/web/ contents ===" && ls -la termprep/web/

# Install dependencies (include gunicorn for production)
RUN pip install --no-cache-dir -e . gunicorn

# Expose port
EXPOSE 7860

# Start server
# ── 为什么是 -w 1 ──
# 检查点（HITL）会话、裁决日志、翻译记忆都存在容器内的 SQLite 里。用 2 个 worker
# 时，/api/pipeline/start 落在 worker A、/api/pipeline/{id}/resume 落在 worker B，
# 会出现「session not found」这类跨进程状态不一致（线上已实测复现：同一 session id
# 连续请求返回 404/200 交替）。本应用并发量很小，单 worker 完全够用，且彻底消除
# 这一类问题；配合 SQLite 落盘仍可跨重启恢复。
# --timeout 300：AI 翻译/术语复核单次调用可能远超 gunicorn 默认的 30s，
# 默认超时会把 worker 杀掉，表现为前端「网络错误」。
CMD ["gunicorn", "-w", "1", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:7860", "--timeout", "300", "--graceful-timeout", "30", "--access-logfile", "-", "--error-logfile", "-", "web_entry:app"]
