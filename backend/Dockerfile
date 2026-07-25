# syntax=docker/dockerfile:1
#
# Build multi-etapa con uv: la imagen final es un python slim con el venv y
# el código, sin uv, sin cachés de paquetes y sin herramientas de build.
#
# Bases pinneadas por digest: `docker compose up --build` (que DEPLOYMENT.md
# pide correr en cada actualización) no puede cambiar de sistema base por
# debajo sin que el cambio aparezca en el diff.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58 AS builder

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev


FROM python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b AS runtime

RUN groupadd --system rag && useradd --system --gid rag --create-home rag \
    && apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder --chown=rag:rag /app/.venv /app/.venv
COPY --from=builder --chown=rag:rag /app/src /app/src
# `eval/` NO se copia: el golden set lleva RUC reales y nombres de contratos,
# y no hace ninguna falta en la imagen que sirve tráfico.

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER rag
EXPOSE 8000

# /api/health devuelve 503 cuando Qdrant u Ollama no responden, así que
# `curl -fsS` ahora sí falla y Docker marca el contenedor unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "rag.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
