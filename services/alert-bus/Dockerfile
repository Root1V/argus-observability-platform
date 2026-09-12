# Imagen del alert-bus.
#
# El contexto de build es la RAIZ del repositorio, no este directorio: el
# servicio depende de `argus-schemas` y `argus-sdk`, que viven en el workspace.
FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

WORKDIR /app

# Las librerias del workspace primero: cambian menos que el servicio, asi que
# esta capa se reaprovecha entre builds.
COPY pyproject.toml uv.lock ./
COPY libs/ ./libs/
COPY services/alert-bus/ ./services/alert-bus/

RUN uv sync --frozen --no-dev --package alert-bus

# Sin privilegios. La pieza que recibe telemetria de toda la red no tiene
# ninguna razon para correr como root.
RUN useradd --create-home --uid 10001 argus
USER argus

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"

CMD ["/app/.venv/bin/python", "-m", "alert_bus"]
