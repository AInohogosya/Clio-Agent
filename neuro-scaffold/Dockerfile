# ============================================================
# Neuro-Scaffold Dockerfile
# Multi-stage build for minimal, secure production image
# ============================================================

# ── Stage 1: Builder ───────────────────────────────────────────
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir --prefix=/install .

# ── Stage 2: Production ───────────────────────────────────────
FROM python:3.12-slim AS production

# Security: Create non-root user with high UID to avoid host conflicts
RUN groupadd --gid 10000 neuro && \
    useradd --uid 10000 --gid neuro --shell /bin/bash \
    --create-home --home-dir /home/neuro neuro

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    tmux \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY src/ /app/src/
COPY pyproject.toml /app/

# Set up workspace directory
RUN mkdir -p /workspace /tmp/neuro && \
    chown -R neuro:neuro /workspace /tmp/neuro /app

# Security hardening
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    NEURO_MODE=subagent \
    NEURO_LOG_LEVEL=INFO \
    NEURO_CONTAINER_WORKSPACE=/workspace \
    NEURO_CONTAINER_USER=neuro \
    NEURO_CONTAINER_UID=10000 \
    NEURO_CONTAINER_GID=10000 \
    NEURO_CONTAINER_MEMORY_LIMIT=512m \
    NEURO_CONTAINER_CPU_LIMIT=1.0 \
    NEURO_CONTAINER_NETWORK_ENABLED=false \
    NEURO_CONTAINER_READONLY_ROOTFS=true

# Switch to non-root user
USER neuro
WORKDIR /workspace

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:9090/health')" || exit 1

EXPOSE 9090

ENTRYPOINT ["python3", "-m", "uvicorn", "neuro_scaffold.gateway.server:app"]
CMD ["--host", "0.0.0.0", "--port", "9090", "--workers", "1"]
