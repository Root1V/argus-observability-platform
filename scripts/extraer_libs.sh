#!/usr/bin/env bash
# Extrae SOLO las librerias a un repositorio publico aparte.
#
# Por que aparte y no publicar este repositorio entero: aqui viven los canales
# con Prometheus y Prosodia —sus incidentes, sus rutas de codigo, su backlog— y
# el registro con el mapa de todas las aplicaciones del portafolio. Publicar eso
# seria exponer informacion de otros equipos sin su consentimiento.
#
# Y de paso es la practica correcta: un consumidor que quiere auditar el SDK no
# deberia tener que descargarse una plataforma de observabilidad entera.
set -euo pipefail
cd "$(dirname "$0")/.."

DESTINO="${1:-../argus-obs}"

mkdir -p "$DESTINO"
rsync -a --delete \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '.pytest_cache' \
  libs/argus-semconv libs/argus-schemas libs/argus-sdk "$DESTINO/libs/"
mkdir -p "$DESTINO/tools" "$DESTINO/.github/workflows" "$DESTINO/libs/semconv-model"
cp tools/gen_semconv.py "$DESTINO/tools/"
cp libs/semconv-model/argus.yaml "$DESTINO/libs/semconv-model/"
cp .github/workflows/publicar.yml "$DESTINO/.github/workflows/"
cp LICENSE "$DESTINO/" 2>/dev/null || true

# El workspace del repositorio publico: solo las tres librerias.
cat > "$DESTINO/pyproject.toml" <<'TOML'
# Workspace de las librerias de Argus.
#
# La PLATAFORMA (collector, alert-bus, registro, canarios) vive en otro
# repositorio, privado. Aqui solo esta lo que otros equipos importan.
# Explicitos, no `libs/*`: ese glob recoge `semconv-model`, que es un fichero
# YAML y no un paquete.
[tool.uv.workspace]
members = ["libs/argus-semconv", "libs/argus-schemas", "libs/argus-sdk"]

[tool.uv.sources]
argus-obs-semconv = { workspace = true }
argus-obs-schemas = { workspace = true }

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.24", "pyyaml>=6"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["libs"]
TOML

echo "  extraido en $DESTINO"
find "$DESTINO" -name "*.py" -o -name "*.toml" -o -name "*.yml" -o -name "*.yaml" \
  | grep -v __pycache__ | wc -l | sed 's/^/  ficheros: /'
