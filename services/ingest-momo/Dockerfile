# ingest-momo container image.
# Distroless base — no shell, no package manager, minimal attack surface.
# Production runs this with read-only root filesystem and no privileged caps.

ARG PYTHON_VERSION=3.12-slim-bookworm

# ---- Builder ----------------------------------------------------------------
FROM python:${PYTHON_VERSION} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install uv
ADD https://astral.sh/uv/install.sh /uv-installer.sh
RUN sh /uv-installer.sh && mv /root/.local/bin/uv /usr/local/bin/uv && rm /uv-installer.sh

WORKDIR /workspace

# Copy workspace metadata first for better cache layering
COPY pyproject.toml uv.lock* ./
COPY packages/schemas/pyproject.toml packages/schemas/pyproject.toml
COPY packages/obs/pyproject.toml packages/obs/pyproject.toml
COPY packages/kafka-client/pyproject.toml packages/kafka-client/pyproject.toml
COPY packages/audit-lib/pyproject.toml packages/audit-lib/pyproject.toml
COPY packages/auth-lib/pyproject.toml packages/auth-lib/pyproject.toml
COPY services/ingest-momo/pyproject.toml services/ingest-momo/pyproject.toml

# Now the source
COPY packages/schemas packages/schemas
COPY packages/obs packages/obs
COPY packages/kafka-client packages/kafka-client
COPY packages/audit-lib packages/audit-lib
COPY packages/auth-lib packages/auth-lib
COPY services/ingest-momo services/ingest-momo

RUN uv sync --frozen --package ingest-momo --no-dev

# ---- Runtime ---------------------------------------------------------------
FROM gcr.io/distroless/python3-debian12:nonroot

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/workspace/.venv/bin:${PATH}" \
    INGEST_MOMO_HOST=0.0.0.0 \
    INGEST_MOMO_PORT=8100 \
    LOG_LEVEL=INFO

WORKDIR /workspace

COPY --from=builder /workspace /workspace

USER nonroot
EXPOSE 8100

# Distroless has no shell so HEALTHCHECK uses Python directly.
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8100/health/live', timeout=2).status==200 else 1)"]

ENTRYPOINT ["python", "-m", "ingest_momo"]
