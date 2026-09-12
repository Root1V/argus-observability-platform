#!/usr/bin/env bash
# Valida las configs del Collector contra el binario real.
#
# `validate` comprueba componentes, tipos y referencias entre tuberias. Es lo
# unico que prueba de verdad que estos YAML funcionan: un fallo aqui es un
# contenedor en bucle de reinicio a las tres de la manana.
set -euo pipefail

cd "$(dirname "$0")/.."
IMAGE="otel/opentelemetry-collector-contrib:0.140.0"

# Valores de relleno: `validate` no conecta a nada, solo resuelve la config.
export ARGUS_GATEWAY_TOKEN=validate
export ARGUS_CLICKHOUSE_DSN=tcp://u:p@clickhouse:9000/otel
export ARGUS_VICTORIAMETRICS_ENDPOINT=http://victoriametrics:8428/api/v1/write
export ARGUS_LANGFUSE_ENDPOINT=http://langfuse-web:3000/api/public/otel
export ARGUS_LANGFUSE_AUTH=validate
export ARGUS_GATEWAY_ENDPOINT=gateway:4317
export ARGUS_ALERTBUS_ENDPOINT=alert-bus:4317
export ARGUS_INSECURE=true

envs=()
for v in ARGUS_GATEWAY_TOKEN ARGUS_CLICKHOUSE_DSN ARGUS_VICTORIAMETRICS_ENDPOINT \
         ARGUS_LANGFUSE_ENDPOINT ARGUS_LANGFUSE_AUTH ARGUS_GATEWAY_ENDPOINT \
         ARGUS_ALERTBUS_ENDPOINT ARGUS_INSECURE; do
  envs+=(-e "$v")
done

check() {
  local label=$1; shift
  if out=$(docker run --rm -i "${envs[@]}" "$@" 2>&1) && [ -z "$out" ]; then
    printf '\033[32mOK  \033[0m %s\n' "$label"
  else
    printf '\033[31mFALLO\033[0m %s\n%s\n' "$label" "$out"
    return 1
  fi
}

fail=0
check "agent.yaml" \
  -v "$PWD/collector/agent.yaml:/c/agent.yaml:ro" "$IMAGE" validate --config=/c/agent.yaml || fail=1
check "gateway.yaml (perfil ligero)" \
  -v "$PWD/collector/gateway.yaml:/c/gw.yaml:ro" "$IMAGE" validate --config=/c/gw.yaml || fail=1
check "gateway.yaml + gateway.genai.yaml (perfil genai)" \
  -v "$PWD/collector/gateway.yaml:/c/gw.yaml:ro" \
  -v "$PWD/collector/gateway.genai.yaml:/c/genai.yaml:ro" \
  "$IMAGE" validate --config=/c/gw.yaml --config=/c/genai.yaml || fail=1

exit $fail
