# Imagen de L3. Contexto de build: la RAIZ del repositorio.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY libs/ ./libs/
COPY services/diagnose/ ./services/diagnose/

RUN uv sync --frozen --no-dev --package diagnose

RUN useradd --create-home --uid 10001 argus
USER argus

CMD ["/app/.venv/bin/python", "-m", "diagnose"]
