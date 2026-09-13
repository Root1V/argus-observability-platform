#!/usr/bin/env bash
# Volcado y restauracion de los volumenes del plano central.
#
# Existe para que el runbook de migracion sea ejecutable y no una lista de
# pasos que alguien interpreta a las tres de la manana.
#
#   ./scripts/migrate.sh dump     ~/argus-backup
#   ./scripts/migrate.sh restore  ~/argus-backup
#   ./scripts/migrate.sh rehearse            # ensayo en frio, no toca nada
set -euo pipefail
cd "$(dirname "$0")/.."

VERDE=$'\033[32m'; ROJO=$'\033[31m'; GRIS=$'\033[2m'; BOLD=$'\033[1m'; OFF=$'\033[0m'
PROYECTO="argus"
COMPOSE="docker compose -f platform/compose.yaml"

# Los volumenes que importan. El de las colas es recomendable pero no
# imprescindible: si se pierde, se pierde lo que estuviera pendiente de enviar.
VOLUMENES_CORE=(clickhouse-data vm-data collector-queue grafana-data)
VOLUMENES_GENAI=(langfuse-postgres langfuse-redis langfuse-minio)
VOLUMENES_AGENTS=(temporal-postgres)

ok()   { printf '%sOK  %s %s\n' "$VERDE" "$OFF" "$1"; }
mal()  { printf '%sFALLO%s %s\n' "$ROJO" "$OFF" "$1"; }
nota() { printf '%s     %s%s\n' "$GRIS" "$1" "$OFF"; }
paso() { printf '\n%s── %s %s\n' "$BOLD" "$1" "$OFF"; }

existe_volumen() { docker volume inspect "${PROYECTO}_$1" >/dev/null 2>&1; }

volumenes_presentes() {
  local encontrados=()
  for v in "${VOLUMENES_CORE[@]}" "${VOLUMENES_GENAI[@]}" "${VOLUMENES_AGENTS[@]}"; do
    existe_volumen "$v" && encontrados+=("$v")
  done
  printf '%s\n' "${encontrados[@]}"
}

cmd_dump() {
  local destino=${1:?uso: migrate.sh dump <directorio>}
  mkdir -p "$destino"

  paso "Volcando volúmenes a $destino"

  # Los servicios tienen que estar PARADOS. Volcar ClickHouse en caliente
  # produce un fichero que restaura sin quejarse y con datos corruptos, que es
  # el peor resultado posible: parece que funciona.
  if [ -n "$($COMPOSE ps -q 2>/dev/null)" ]; then
    mal "Hay servicios corriendo. Para el plano central primero: make down"
    nota "Volcar ClickHouse en caliente produce una copia corrupta que restaura sin quejarse."
    return 1
  fi

  local volcados=()
  while read -r v; do
    [ -z "$v" ] && continue
    printf '     %-22s' "$v"
    docker run --rm \
      -v "${PROYECTO}_${v}:/origen:ro" \
      -v "$(cd "$destino" && pwd):/destino" \
      busybox:1.37 tar czf "/destino/${v}.tar.gz" -C /origen . 2>/dev/null
    printf '%s\n' "$(du -h "$destino/${v}.tar.gz" | cut -f1)"
    volcados+=("$v")
  done < <(volumenes_presentes)

  # El manifiesto importa: restaurar sobre versiones distintas de ClickHouse
  # puede exigir migraciones, y sin esto no sabrias desde donde vienes.
  {
    printf '{\n  "fecha": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '  "origen": "%s",\n' "$(hostname)"
    printf '  "volumenes": ['
    printf '"%s",' "${volcados[@]}" | sed 's/,$//'
    printf '],\n  "imagenes": {\n'
    printf '    "clickhouse": "%s",\n' "$(grep -oE 'clickhouse/clickhouse-server:[^ ]+' platform/compose.yaml | head -1)"
    printf '    "collector": "%s",\n' "$(grep -oE 'otel/opentelemetry-collector-contrib:[^ ]+' platform/compose.yaml | head -1)"
    printf '    "victoriametrics": "%s"\n' "$(grep -oE 'victoriametrics/victoria-metrics:[^ ]+' platform/compose.yaml | head -1)"
    printf '  }\n}\n'
  } > "$destino/manifiesto.json"

  ok "${#volcados[@]} volúmenes volcados"
  nota "Total: $(du -sh "$destino" | cut -f1)"
  nota "El platform/.env NO va aquí: llévalo por un canal seguro."
}

cmd_restore() {
  local origen=${1:?uso: migrate.sh restore <directorio>}
  [ -f "$origen/manifiesto.json" ] || { mal "No hay manifiesto.json en $origen"; return 1; }

  paso "Restaurando desde $origen"
  nota "$(python3 -c "import json;d=json.load(open('$origen/manifiesto.json'));print(f\"volcado de {d['origen']} el {d['fecha']}\")")"

  if [ -n "$($COMPOSE ps -q 2>/dev/null)" ]; then
    mal "Hay servicios corriendo. Páralos primero: make down"
    return 1
  fi

  local restaurados=0
  for archivo in "$origen"/*.tar.gz; do
    [ -f "$archivo" ] || continue
    local v; v=$(basename "$archivo" .tar.gz)
    printf '     %-22s' "$v"
    docker volume create "${PROYECTO}_${v}" >/dev/null
    docker run --rm \
      -v "${PROYECTO}_${v}:/destino" \
      -v "$(cd "$origen" && pwd):/origen:ro" \
      busybox:1.37 sh -c "rm -rf /destino/* /destino/..?* 2>/dev/null; tar xzf /origen/$(basename "$archivo") -C /destino" 2>/dev/null
    printf 'restaurado\n'
    restaurados=$((restaurados + 1))
  done

  ok "$restaurados volúmenes restaurados"
  nota "Ahora: make up && make verify"
  nota "Y comprueba que hay datos HISTÓRICOS, no solo que el stack arranca."
}

cmd_rehearse() {
  # El ensayo existe porque un runbook que nunca se ha ejecutado no es un
  # runbook, es una intencion. Esto comprueba las condiciones sin tocar nada.
  paso "Ensayo en frío de la migración"
  nota "No toca nada: comprueba que la migración sería posible."

  local fallos=0

  local presentes; presentes=$(volumenes_presentes)
  if [ -z "$presentes" ]; then
    mal "No se encontró ningún volumen de $PROYECTO"; fallos=$((fallos+1))
  else
    ok "$(echo "$presentes" | wc -l | tr -d ' ') volúmenes localizados"
    echo "$presentes" | sed 's/^/       /'
  fi

  local total=0
  while read -r v; do
    [ -z "$v" ] && continue
    local bytes
    bytes=$(docker run --rm -v "${PROYECTO}_${v}:/v:ro" busybox:1.37 du -sb /v 2>/dev/null | cut -f1 || echo 0)
    total=$((total + bytes))
  done < <(echo "$presentes")
  ok "Tamaño total a mover: $(numfmt --to=iec "$total" 2>/dev/null || echo "$total bytes")"

  local libre
  libre=$(df -k "$HOME" | awk 'NR==2{print $4*1024}')
  if [ "$libre" -gt "$((total * 2))" ]; then
    ok "Espacio suficiente en $HOME para el volcado"
  else
    mal "Puede faltar espacio: $(numfmt --to=iec "$libre" 2>/dev/null || echo "$libre") libres"; fallos=$((fallos+1))
  fi

  if [ -f platform/.env ]; then
    ok "platform/.env existe (recuerda: va por canal seguro, no en el volcado)"
  else
    mal "Falta platform/.env"; fallos=$((fallos+1))
  fi

  if grep -q "ARGUS_GATEWAY_ENDPOINT" platform/.env.agent 2>/dev/null; then
    local endpoint; endpoint=$(grep ARGUS_GATEWAY_ENDPOINT platform/.env.agent | cut -d= -f2)
    if [[ "$endpoint" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+ ]]; then
      mal "El agente apunta a una IP ($endpoint)"
      nota "Con una IP, migrar obliga a reconfigurar TODOS los agentes."
      nota "Con un nombre estable de red privada, el paso 5 del runbook desaparece."
      fallos=$((fallos+1))
    else
      ok "El agente apunta a un nombre ($endpoint), no a una IP"
    fi
  fi

  if [ -f deadman/deadman.json ]; then
    ok "El dead man's switch está configurado (acuérdate de reapuntarlo)"
  else
    nota "Sin dead man's switch configurado; no bloquea la migración."
  fi

  paso "Resultado del ensayo"
  if [ "$fallos" = "0" ]; then
    printf '%sLa migración es viable con el estado actual.%s\n\n' "$VERDE" "$OFF"
    return 0
  fi
  printf '%s%d condición(es) impedirían o complicarían la migración.%s\n\n' "$ROJO" "$fallos" "$OFF"
  return 1
}

case "${1:-}" in
  dump)     shift; cmd_dump "$@" ;;
  restore)  shift; cmd_restore "$@" ;;
  rehearse) cmd_rehearse ;;
  *) echo "uso: $0 {dump <dir>|restore <dir>|rehearse}"; exit 2 ;;
esac
