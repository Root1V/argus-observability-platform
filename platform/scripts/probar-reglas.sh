#!/usr/bin/env bash
# Prueba las reglas de vmalert contra el binario real, en dos pasos.
#
# 1. `vmalert -dryRun`: que TODAS las reglas se cargan. Es lo que vmalert hara
#    al arrancar, y una regla que no parsea deja sin evaluar su grupo entero.
# 2. `vmalert-tool unittest`: que las reglas con pruebas de comportamiento
#    (`platform/tests/reglas/*_test.yaml`) avisan cuando deben y SOLO cuando
#    deben. Una regla que valida y nunca dispara pasa el paso 1 igual.
#
# Misma version que el vmalert del compose: una prueba con otra version
# probaria otro evaluador.
set -euo pipefail

cd "$(dirname "$0")/.."
VERSION="v1.111.0"

echo "· vmalert -dryRun"
docker run --rm -v "$PWD/rules:/rules:ro" "victoriametrics/vmalert:${VERSION}" \
  '-rule=/rules/*.yaml' -dryRun >/dev/null

for prueba in tests/reglas/*_test.yaml; do
  echo "· vmalert-tool unittest $(basename "$prueba")"
  docker run --rm -v "$PWD/rules:/rules:ro" -v "$PWD/tests/reglas:/pruebas:ro" \
    "victoriametrics/vmalert-tool:${VERSION}" unittest "--files=/pruebas/$(basename "$prueba")"
done
echo "OK"
