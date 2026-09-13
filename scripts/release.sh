#!/usr/bin/env bash
# Publica una version de las librerias para que las apps piloto la consuman.
#
# Durante el piloto NO hace falta un indice: `uv pip install` sabe instalar
# desde una etiqueta de git, y eso da versionado real con cero infraestructura.
#
#   ./scripts/release.sh 1.0.0a1      # prelanzamiento para el piloto
#   ./scripts/release.sh 1.0.0        # estable, cuando el piloto lo respalde
#
# El esquema de version importa: con `1.0.0aN`, un `pip install argus-obs-sdk`
# sin `--pre` NO se lleva un prelanzamiento por accidente.
set -euo pipefail
cd "$(dirname "$0")/.."

VERDE=$'\033[32m'; ROJO=$'\033[31m'; GRIS=$'\033[2m'; BOLD=$'\033[1m'; OFF=$'\033[0m'

VERSION=${1:?uso: release.sh <version>   p.ej. 1.0.0a1}
LIBS=(libs/argus-semconv libs/argus-schemas libs/argus-sdk)

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+((a|b|rc)[0-9]+)?$ ]]; then
  printf '%sVersion invalida: %s%s\n' "$ROJO" "$VERSION" "$OFF"
  printf '%sUsa PEP 440: 1.0.0a1 (alfa), 1.0.0rc1 (candidata), 1.0.0 (estable).%s\n' "$GRIS" "$OFF"
  exit 2
fi

if [ -n "$(git status --porcelain)" ]; then
  printf '%sHay cambios sin commitear. Una version debe apuntar a un estado exacto.%s\n' "$ROJO" "$OFF"
  exit 1
fi

printf '\n%s── Publicando %s %s\n' "$BOLD" "$VERSION" "$OFF"

for lib in "${LIBS[@]}"; do
  python3 - "$lib/pyproject.toml" "$VERSION" <<'PY'
import re, sys, pathlib
ruta, version = pathlib.Path(sys.argv[1]), sys.argv[2]
mayor = version.split(".")[0]
t = ruta.read_text()
t = re.sub(r'^version = "[^"]+"', f'version = "{version}"', t, count=1, flags=re.M)

# Las dependencias ENTRE nuestras librerias se fijan al limite inferior de ESTA
# version, no a `>=1.0`.
#
# PEP 440 dice que `>=1.0` NO acepta `1.0.0a1`: un prelanzamiento solo satisface
# un especificador que mencione un prelanzamiento. Con `>=1.0` publicabamos tres
# paquetes que no podian instalarse entre ellos, y el error que sale
# ("pre-releases weren't enabled") no apunta a la causa.
t = re.sub(
    r'"(argus-obs-[a-z]+)(\[[^\]]*\])?>=[^,"]+,<\d+"',
    lambda m: f'"{m.group(1)}{m.group(2) or ""}>={version},<{int(mayor) + 1}"',
    t,
)
ruta.write_text(t)
PY
  printf '  %-24s %s\n' "$(basename "$lib")" "$VERSION"
done

uv sync --quiet
printf '%sOK  %s dependencias resueltas\n' "$VERDE" "$OFF"

# Las pruebas son parte de publicar, no un paso opcional previo: una version
# etiquetada es algo que alguien va a instalar.
printf '%s     ejecutando pruebas…%s\n' "$GRIS" "$OFF"
if ! uv run --quiet python -m pytest -q -p no:cacheprovider >/dev/null 2>&1; then
  printf '%sFALLO%s las pruebas no pasan; no se publica\n' "$ROJO" "$OFF"
  exit 1
fi
printf '%sOK  %s pruebas en verde\n' "$VERDE" "$OFF"

rm -rf dist && mkdir -p dist
for p in argus-obs-semconv argus-obs-schemas argus-obs-sdk; do
  uv build --package "$p" --out-dir dist --quiet
done
printf '%sOK  %s ruedas construidas\n' "$VERDE" "$OFF"
ls -1 dist/*.whl | sed 's|dist/|       |'

git add -A
git commit -q -m "Version $VERSION de las librerias"
git tag -a "v$VERSION" -m "Librerias $VERSION"

printf '\n%s── Como la instala una aplicacion %s\n\n' "$BOLD" "$OFF"
REPO=$(git config --get remote.origin.url 2>/dev/null || echo "$PWD")
cat <<EOF
  ${GRIS}Desde la etiqueta de git (sin infraestructura):${OFF}
  uv pip install "argus-obs-sdk[asgi,client] @ git+${REPO}@v${VERSION}#subdirectory=libs/argus-sdk"

  ${GRIS}O desde las ruedas construidas:${OFF}
  uv pip install --find-links $PWD/dist 'argus-obs-sdk[asgi,client]==${VERSION}'

EOF
printf '%s  Falta empujar la etiqueta:  git push && git push --tags%s\n\n' "$GRIS" "$OFF"
