#!/usr/bin/env bash
# Verificacion completa de Argus.
#
# Esto es tambien el contenido del pipeline de CI cuando exista (X-02).
#
#   ./scripts/verify.sh          todo, incluida la parte que necesita Docker
#   ./scripts/verify.sh --quick  solo lo que no necesita el plano central
set -uo pipefail
cd "$(dirname "$0")/.."

QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1

GREEN=$'\033[32m'; RED=$'\033[31m'; DIM=$'\033[2m'; BOLD=$'\033[1m'; OFF=$'\033[0m'
fallos=0

paso() { printf '\n%s── %s %s\n' "$BOLD" "$1" "$OFF"; }
ok()   { printf '%sOK  %s %s\n' "$GREEN" "$OFF" "$1"; }
mal()  { printf '%sFALLO%s %s\n' "$RED" "$OFF" "$1"; fallos=$((fallos+1)); }
nota() { printf '%s     %s%s\n' "$DIM" "$1" "$OFF"; }

# --- 1. Las convenciones no se han desviado del modelo -----------------------
paso "1/7  Modelo de convenciones"
nota "Con cinco lenguajes, el fallo que rompe las consultas agregadas no es que"
nota "un servicio no emita, sino que dos emitan lo mismo con nombres distintos."
if uv run --quiet python tools/gen_semconv.py --check >/dev/null 2>&1; then
  ok "Las constantes generadas coinciden con argus.yaml"
else
  mal "Deriva entre el modelo y lo generado — ejecuta: uv run python tools/gen_semconv.py"
fi

# --- 2. Suite de pruebas -----------------------------------------------------
paso "2/7  Suite de pruebas"
if out=$(uv run --quiet python -m pytest -q -p no:cacheprovider 2>&1); then
  ok "$(echo "$out" | tail -1 | sed 's/^ *//')"
else
  mal "Pruebas fallando"; echo "$out" | tail -15
fi

# --- 3. Linter ---------------------------------------------------------------
paso "3/7  Linter"
if uv run --quiet ruff check . >/dev/null 2>&1; then
  ok "ruff sin hallazgos"
else
  mal "ruff encontro problemas"; uv run --quiet ruff check . 2>&1 | tail -8
fi

# --- 4. El ejemplo del apalancamiento ----------------------------------------
paso "4/7  Ejemplo: libreria instrumentada"
nota "Demuestra D-002: la libreria emite spans en una app que llamo a init(),"
nota "y no cuesta nada en una que no."
if out=$(ARGUS_CONSOLE=true ARGUS_DISABLED=true uv run --quiet python examples/aplicacion_instrumentada.py 2>/dev/null); then
  spans=$(echo "$out" | grep -c '"name":' || true)
  if [ "$spans" -ge 4 ]; then
    ok "La aplicacion escribio 2 lineas y salieron $spans spans"
  else
    mal "Se esperaban 4 spans, salieron $spans"
  fi
else
  mal "El ejemplo no corre"
fi

if [ "$QUICK" = "1" ]; then
  paso "Resumen (modo rapido)"
  nota "Omitido lo que necesita Docker: configs del Collector y prueba end-to-end."
  [ "$fallos" = "0" ] && { printf '%sTodo en verde.%s\n\n' "$GREEN" "$OFF"; exit 0; }
  printf '%s%d comprobacion(es) fallaron.%s\n\n' "$RED" "$fallos" "$OFF"; exit 1
fi

# --- 5. Configs del Collector contra su binario real -------------------------
paso "5/7  Configs del Collector"
nota "Contra el binario real: un fallo aqui es un contenedor en bucle de"
nota "reinicio a las tres de la manana."
if ! command -v docker >/dev/null 2>&1; then
  mal "Docker no disponible"
elif out=$(./platform/scripts/validate-collector.sh 2>&1); then
  echo "$out" | sed 's/^/     /'
else
  mal "Alguna config del Collector no valida"; echo "$out" | tail -10
fi

# --- 6. Prueba de humo end-to-end contra el stack real -----------------------
paso "6/7  Prueba end-to-end"
nota "Escribe en la ClickHouse de verdad y consulta lo que llego. No hay mocks:"
nota "cinco de las decisiones de docs/decisions.md las encontro esta prueba."

if [ ! -f platform/.env ]; then
  mal "Falta platform/.env — copia platform/.env.example y rellenalo"
elif ! curl -sf http://127.0.0.1:13133/ >/dev/null 2>&1; then
  mal "El plano central no responde en 13133"
  nota "Arrancalo con: docker compose -f platform/compose.yaml --profile lean up -d"
else
  set -a; . platform/.env; set +a
  if out=$(uv run --quiet --with 'opentelemetry-exporter-otlp-proto-http>=1.30,<2' python scripts/e2e_smoke.py 2>&1); then
    echo "$out" | grep -E "OK|FALLO" | sed 's/^/     /'
  else
    mal "La prueba end-to-end fallo"; echo "$out" | grep -E "OK|FALLO|- " | sed 's/^/     /'
  fi
fi

# --- 7. Deteccion sin ruido --------------------------------------------------
paso "7/7  Detección sin ruido (Fase 2)"
nota "Deduplicacion, correlacion por topologia y divulgacion progresiva."
if ! curl -sf http://127.0.0.1:8080/healthz >/dev/null 2>&1; then
  nota "El alert-bus no responde; omitido. Arráncalo con: make up"
elif out=$(uv run --quiet python scripts/e2e_f2.py 2>&1); then
  echo "$out" | grep -E "OK|FALLO|señales|notificaciones" | sed 's/^/     /'
else
  mal "La verificación de la Fase 2 falló"; echo "$out" | grep -E "OK|FALLO|- " | sed \'s/^/     /\'
fi

# --- Resumen -----------------------------------------------------------------
paso "Resumen"
if [ "$fallos" = "0" ]; then
  printf '%sTodo en verde.%s\n\n' "$GREEN" "$OFF"; exit 0
fi
printf '%s%d comprobacion(es) fallaron.%s\n\n' "$RED" "$fallos" "$OFF"; exit 1
