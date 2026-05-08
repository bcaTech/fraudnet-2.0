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
COPY packages/kafka-client/pyproject.toml packages/kafka-client/pyproject.toml
COPY services/action-tier1/pyproject.toml services/action-tier1/pyproject.toml
COPY packages/schemas packages/schemas
COPY packages/obs packages/obs
COPY packages/kafka-client packages/kafka-client
COPY services/action-tier1 services/action-tier1
RUN uv sync --frozen --package action-tier1 --no-dev

FROM gcr.io/distroless/python3-debian12:nonroot
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/workspace/.venv/bin:${PATH}" \
    ACTION_TIER1_HOST=0.0.0.0 ACTION_TIER1_PORT=8201 LOG_LEVEL=INFO
WORKDIR /workspace
COPY --from=builder /workspace /workspace
USER nonroot
EXPOSE 8201
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8201/health/live', timeout=2).status==200 else 1)"]
ENTRYPOINT ["python", "-m", "action_tier1"]
