# Argus — atajos. Ver roadmap.md para el estado y docs/decisions.md para el porque.
.DEFAULT_GOAL := help
COMPOSE := docker compose -f platform/compose.yaml

help:  ## Muestra esta ayuda
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup:  ## Instala dependencias y genera los secretos locales
	uv sync
	@test -f platform/.env || { \
	  printf 'CLICKHOUSE_USER=argus\nCLICKHOUSE_PASSWORD=%s\nARGUS_GATEWAY_TOKEN=%s\nLANGFUSE_DB_PASSWORD=%s\nLANGFUSE_REDIS_PASSWORD=%s\nMINIO_USER=argus\nMINIO_PASSWORD=%s\nLANGFUSE_NEXTAUTH_SECRET=%s\nLANGFUSE_SALT=%s\nLANGFUSE_ENCRYPTION_KEY=%s\nTEMPORAL_DB_PASSWORD=%s\n' \
	    $$(openssl rand -hex 16) $$(openssl rand -hex 32) $$(openssl rand -hex 16) \
	    $$(openssl rand -hex 16) $$(openssl rand -hex 16) $$(openssl rand -hex 32) \
	    $$(openssl rand -hex 32) $$(openssl rand -hex 32) $$(openssl rand -hex 16) \
	    > platform/.env && echo "platform/.env generado"; }

up:  ## Arranca el plano central en modo ligero (4 contenedores)
	$(COMPOSE) --profile lean up -d
	@echo "Esperando a ClickHouse..."
	@until curl -sf http://127.0.0.1:8123/ping >/dev/null 2>&1; do sleep 2; done
	@until curl -sf http://127.0.0.1:13133/ >/dev/null 2>&1; do sleep 2; done
	@echo "Listo. OTLP en :4317 (gRPC) y :4318 (HTTP)"

down:  ## Para el plano central (conserva los datos)
	$(COMPOSE) --profile lean --profile genai --profile agents down

clean:  ## Para y BORRA todos los datos
	$(COMPOSE) --profile lean --profile genai --profile agents down -v

ps:  ## Estado de los contenedores
	@$(COMPOSE) --profile lean --profile genai --profile agents ps --format 'table {{.Service}}\t{{.Status}}'

logs:  ## Sigue los logs del Collector
	$(COMPOSE) logs -f collector

verify:  ## Verificacion completa (necesita el plano central arrancado)
	@./scripts/verify.sh

check:  ## Verificacion rapida, sin Docker
	@./scripts/verify.sh --quick

test:  ## Solo la suite de pruebas
	uv run python -m pytest -q

demo:  ## Demuestra el apalancamiento de librerias, sin plataforma
	@ARGUS_CONSOLE=true ARGUS_DISABLED=true uv run python examples/aplicacion_instrumentada.py

query:  ## Consulta ClickHouse:  make query SQL="SELECT ..."
	@set -a; . platform/.env; set +a; \
	curl -s -H "X-ClickHouse-User: $$CLICKHOUSE_USER" -H "X-ClickHouse-Key: $$CLICKHOUSE_PASSWORD" \
	  http://127.0.0.1:8123/ --data-binary "$(SQL)"

semconv:  ## Regenera las constantes desde argus.yaml
	uv run python tools/gen_semconv.py

.PHONY: help setup up down clean ps logs verify check test demo query semconv
