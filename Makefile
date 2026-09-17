# Argus — atajos. Ver roadmap.md para el estado y docs/decisions.md para el porque.
DEADMAN_DIR := $(HOME)/Library/Application Support/argus-deadman

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

genai:  ## Arranca el perfil GenAI: Langfuse para trazas de prompts (F1-12)
	docker compose -f platform/compose.yaml -f platform/compose.genai.yaml --profile genai up -d
	@echo "Esperando a Langfuse (la primera vez migra ClickHouse, tarda)..."
	@until curl -sf http://127.0.0.1:3000/api/public/health >/dev/null 2>&1; do sleep 5; done
	@set -a; . platform/.env; set +a; \
	echo "  Langfuse:  http://localhost:3000"; \
	echo "  usuario:   $$LANGFUSE_INIT_EMAIL"; \
	echo "  clave:     $$LANGFUSE_INIT_PASSWORD"

genai-check:  ## Verifica que las trazas de prompts llegan a Langfuse
	@set -a; . platform/.env; set +a; \
	uv run --with 'opentelemetry-exporter-otlp-proto-http' python scripts/verificar_langfuse.py

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

wheels:  ## Construye las ruedas instalables de las librerias
	@rm -rf dist && mkdir -p dist
	@for p in argus-obs-semconv argus-obs-schemas argus-obs-sdk; do \
	  uv build --package $$p --out-dir dist --quiet; done
	@ls -1 dist/*.whl | sed 's|dist/|  |'

indice:  ## Construye el índice de paquetes y lo sirve en :8081
	@$(MAKE) --no-print-directory wheels
	@uv run --quiet python scripts/construir_indice.py
	@$(COMPOSE) --profile lean up -d indice >/dev/null 2>&1 || true
	@echo
	@echo "  Índice en http://127.0.0.1:8081/simple"
	@echo "  Instalación desde otra aplicación:"
	@echo "    pip install --extra-index-url http://127.0.0.1:8081/simple 'argus-obs-sdk[asgi]'"
	@echo
	@echo "  OJO: --extra-index-url, no --index-url. El nuestro NO replica PyPI,"
	@echo "  así que sustituirlo deja sin resolver las dependencias de terceros."

dash:  ## Abre los dashboards e imprime la credencial
	@set -a; . platform/.env; set +a; \
	echo "  Grafana:  http://localhost:3001"; \
	echo "  usuario:  $$GRAFANA_USER"; \
	echo "  clave:    $$GRAFANA_PASSWORD"; \
	echo; \
	echo "  Una aplicacion:  http://localhost:3001/d/argus-aplicacion"; \
	echo "  La plataforma:   http://localhost:3001/d/argus-plataforma"
	@command -v open >/dev/null && open http://localhost:3001/d/argus-aplicacion || true

demo-traffic:  ## Genera trafico de una app simulada, para ver los dashboards con datos
	@set -a; . platform/.env; set +a; \
	uv run --with 'opentelemetry-exporter-otlp-proto-http' python scripts/trafico_demo.py --minutos $${M:-2}

install-cmd:  ## Imprime el comando EXACTO para instalar el SDK en otra app
	@test -d dist || { echo "No hay ruedas. Ejecuta primero: make wheels"; exit 1; }
	@V=$$(ls dist/argus_obs_sdk-*.whl | head -1 | sed 's/.*argus_obs_sdk-//; s/-py3.*//'); \
	echo; \
	echo "  Copia y pega esto en el repo de tu aplicacion:"; \
	echo; \
	echo "  uv pip install --find-links $(PWD)/dist 'argus-obs-sdk[asgi,client,sql]==$$V'"; \
	echo; \
	echo "  Extras segun lo que use tu app:"; \
	echo "    asgi    FastAPI / Starlette      celery  colas Celery"; \
	echo "    client  httpx / requests         kafka   Kafka / Redpanda"; \
	echo "    sql     SQLAlchemy / asyncpg     genai   LangChain, Ollama, vLLM…"; \
	echo; \
	R=$$(git config --get remote.origin.url 2>/dev/null); \
	if [ -n "$$R" ]; then \
	  echo "  Si la app esta en OTRA maquina, desde la etiqueta de git:"; \
	  echo; \
	  echo "  uv pip install \"argus-obs-sdk[asgi,client,sql] @ git+$$R@v$$V#subdirectory=libs/argus-sdk\""; \
	  echo; \
	fi

deadman-setup:  ## Instala el dead man's switch como tarea del sistema (fuera del repo)
	@test -f deadman/deadman.json || { \
	  cp deadman/deadman.json.example deadman/deadman.json; \
	  echo "  deadman/deadman.json creado desde el ejemplo: rellena al menos un canal"; }
	@mkdir -p "$(DEADMAN_DIR)"
	@cp deadman/deadman.py deadman/deadman.json "$(DEADMAN_DIR)/"
	@chmod 600 "$(DEADMAN_DIR)/deadman.json"
	@sed -e "s|/RUTA/INSTALADA|$(DEADMAN_DIR)|g" \
	  deadman/com.argus.deadman.plist > /tmp/com.argus.deadman.plist
	@echo "  instalado en $(DEADMAN_DIR)"
	@echo "  (fuera de ~/Documents: launchd no puede leer ahi sin acceso total al disco)"
	@echo
	@echo "  1. Comprueba que puede avisarte ANTES de confiar en el:"
	@echo "     python3 '$(DEADMAN_DIR)/deadman.py' --config '$(DEADMAN_DIR)/deadman.json' --test"
	@echo
	@echo "  2. Instalalo:"
	@echo "     cp /tmp/com.argus.deadman.plist ~/Library/LaunchAgents/"
	@echo "     launchctl load ~/Library/LaunchAgents/com.argus.deadman.plist"
	@echo
	@echo "  Al cambiar la configuracion, vuelve a ejecutar 'make deadman-setup':"
	@echo "  la copia instalada no se actualiza sola."

release:  ## Etiqueta y construye una version:  make release V=1.0.0a1
	@./scripts/release.sh $(V)

telegram-setup:  ## Termina de configurar Telegram (espera a que pulses Iniciar)
	@uv run --quiet python scripts/telegram_setup.py $(if $(ESPERA),--espera $(ESPERA),)

rotate-token:  ## Rota ARGUS_GATEWAY_TOKEN en .env, .env.agent y el secreto de vmalert
	@./scripts/rotar_token.sh

channel-test:  ## Manda un aviso de PRUEBA por los canales configurados
	@set -a; . platform/.env; set +a; uv run python scripts/probar_canal.py --severidad $${SEV:-page}

pilot-status:  ## ¿Cuánto le queda al piloto para poder cerrarse?
	@uv run --quiet python scripts/pilot_status.py

pilot-check:  ## ¿Listo para conectar una aplicacion real?
	@uv run python scripts/pilot_check.py

queue-test:  ## Prueba la cola persistente: para el central, genera trafico, arranca (F1-09)
	@set -a; . platform/.env; set +a; \
	uv run --with 'opentelemetry-exporter-otlp-proto-http' python scripts/test_cola_persistente.py --spans $${N:-100}

migrate-rehearse:  ## Ensayo EN FRIO de la migracion de maquina (F1-08)
	@./scripts/migrate.sh rehearse

migrate-dump:  ## Vuelca los volumenes:  make migrate-dump DEST=~/argus-backup
	@./scripts/migrate.sh dump $(DEST)

migrate-restore:  ## Restaura los volumenes:  make migrate-restore SRC=~/argus-backup
	@./scripts/migrate.sh restore $(SRC)

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


.PHONY: help setup genai genai-check up down clean ps logs agent latency incidents e2e-f2 wheels dash demo-traffic install-cmd deadman-setup channel-test release pilot-check queue-test migrate-rehearse migrate-dump migrate-restore verify check test demo query semconv
