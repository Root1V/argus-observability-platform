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

down:  ## Para el plano central y el agente (conserva los datos)
	-docker compose -f platform/compose.agent.yaml --env-file platform/.env.agent down 2>/dev/null
	$(COMPOSE) --profile lean --profile genai --profile agents down

clean:  ## Para y BORRA todos los datos
	$(COMPOSE) --profile lean --profile genai --profile agents down -v

ps:  ## Estado de los contenedores
	@$(COMPOSE) --profile lean --profile genai --profile agents ps --format 'table {{.Service}}\t{{.Status}}'

logs:  ## Sigue los logs del Collector
	$(COMPOSE) logs -f collector

agent:  ## Arranca el Collector agente de esta maquina (puertos 4317/4318)
	@test -f platform/.env.agent || { \
	  set -a; . platform/.env; set +a; \
	  printf 'ARGUS_GATEWAY_ENDPOINT=collector:4318\nARGUS_ALERTBUS_ENDPOINT=alert-bus:8080\nARGUS_GATEWAY_TOKEN=%s\nARGUS_INSECURE=true\n' "$$ARGUS_GATEWAY_TOKEN" > platform/.env.agent; \
	  echo "platform/.env.agent generado"; }
	docker compose -f platform/compose.agent.yaml --env-file platform/.env.agent up -d
	@until curl -sf http://127.0.0.1:13134/ >/dev/null 2>&1; do sleep 2; done
	@echo "Agente listo. Las aplicaciones exportan a localhost:4317 o :4318"

latency:  ## Mide el presupuesto del camino caliente (F2-09)
	@set -a; . platform/.env; set +a; \
	ARGUS_ENDPOINT=http://127.0.0.1:4318 ARGUS_PROTOCOL=http/protobuf \
	OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer $$ARGUS_GATEWAY_TOKEN" \
	uv run --with 'opentelemetry-exporter-otlp-proto-http' python scripts/measure_latency.py --n $${N:-10}

e2e-f2:  ## Verificacion end-to-end de la Fase 2 (deteccion sin ruido)
	@set -a; . platform/.env; set +a; uv run python scripts/e2e_f2.py

incidents:  ## Incidentes abiertos en el alert-bus
	@curl -s http://127.0.0.1:8080/incidents | python3 -m json.tool

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

.PHONY: help setup up down clean ps logs agent latency incidents e2e-f2 verify check test demo query semconv
