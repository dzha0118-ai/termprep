FROM python:3.13-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml requirements.txt ./
COPY termprep/ termprep/
COPY web_entry.py .

# Install dependencies
RUN pip install --no-cache-dir -e .

# Expose port
EXPOSE 7860

# Start server
CMD ["gunicorn", "-w", "2", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:7860", "web_entry:app"]
