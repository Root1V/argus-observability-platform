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

RUN useradd --create-home --uid 10001 argus \
    # El volumen de la sombra se monta aqui. Un volumen nombrado hereda el
    # dueño del directorio de la imagen la primera vez; sin esto seria de root
    # y el proceso, que corre como `argus`, no podria escribir su registro.
    && mkdir -p /var/lib/argus && chown argus /var/lib/argus
USER argus

CMD ["/app/.venv/bin/python", "-m", "diagnose"]
