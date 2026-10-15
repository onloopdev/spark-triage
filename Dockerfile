FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY agent ./agent
COPY triage_mcp ./triage_mcp
COPY data ./data

RUN python -m pip install --no-cache-dir . \
    && npm init -y \
    && npm install --omit=dev @modelcontextprotocol/server-filesystem

ENV TRIAGE_MODEL=gemma4:31b
ENV OLLAMA_HOST=https://ollama.com
ENV TRIAGE_INTAKE_DIR=/app/data/intake
ENV TRIAGE_DB=/tmp/triage.db
ENV TRIAGE_SWEEP_MIN=0

EXPOSE 10000

CMD ["sh", "-c", "python -c 'import os, uvicorn; from agent.webdesk import create_app; uvicorn.run(create_app(), host=\"0.0.0.0\", port=int(os.environ.get(\"PORT\", \"10000\")), log_level=\"info\")'"]
