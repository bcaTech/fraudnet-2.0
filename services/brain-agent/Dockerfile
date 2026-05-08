ARG PYTHON_VERSION=3.12-slim-bookworm
FROM python:${PYTHON_VERSION} AS builder
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates build-essential \
 && rm -rf /var/lib/apt/lists/*
ADD https://astral.sh/uv/install.sh /uv-installer.sh
RUN sh /uv-installer.sh && mv /root/.local/bin/uv /usr/local/bin/uv && rm /uv-installer.sh
WORKDIR /workspace
COPY pyproject.toml uv.lock* ./
COPY packages/schemas/pyproject.toml packages/schemas/pyproject.toml
COPY packages/obs/pyproject.toml packages/obs/pyproject.toml
COPY packages/audit-lib/pyproject.toml packages/audit-lib/pyproject.toml
COPY packages/auth-lib/pyproject.toml packages/auth-lib/pyproject.toml
COPY packages/graph-client/pyproject.toml packages/graph-client/pyproject.toml
COPY packages/feature-client/pyproject.toml packages/feature-client/pyproject.toml
COPY services/brain-agent/pyproject.toml services/brain-agent/pyproject.toml
COPY packages/schemas packages/schemas
COPY packages/obs packages/obs
COPY packages/audit-lib packages/audit-lib
COPY packages/auth-lib packages/auth-lib
COPY packages/graph-client packages/graph-client
COPY packages/feature-client packages/feature-client
COPY services/brain-agent services/brain-agent
RUN uv sync --frozen --package brain-agent --no-dev

FROM gcr.io/distroless/python3-debian12:nonroot
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/workspace/.venv/bin:${PATH}" \
    BRAIN_AGENT_HOST=0.0.0.0 BRAIN_AGENT_PORT=8330 LOG_LEVEL=INFO
WORKDIR /workspace
COPY --from=builder /workspace /workspace
USER nonroot
EXPOSE 8330
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8330/health/live', timeout=2).status==200 else 1)"]
ENTRYPOINT ["python", "-m", "brain_agent"]
