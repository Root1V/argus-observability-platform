#!/usr/bin/env bash
# Rota ARGUS_GATEWAY_TOKEN en todos los sitios donde vive.
#
# El token autoriza a INYECTAR telemetria. Quien lo tenga puede escribir en el
# almacen del que los agentes sacan sus conclusiones, asi que protege la
# integridad de lo que entra, no su confidencialidad.
#
# Quien NO lo necesita: las aplicaciones. El receptor OTLP del agente no exige
# autenticacion —las apps exportan a localhost y la frontera la pone el mapeo
# de puertos—, asi que rotar no afecta a ningun equipo externo. Solo a nosotros.
set -euo pipefail
cd "$(dirname "$0")/.."

NUEVO=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")

for f in platform/.env platform/.env.agent; do
  [ -f "$f" ] || continue
  python3 - "$f" "$NUEVO" <<'PY'
import pathlib, re, sys
p, nuevo = pathlib.Path(sys.argv[1]), sys.argv[2]
t = p.read_text()
if re.search(r"^ARGUS_GATEWAY_TOKEN=", t, re.M):
    t = re.sub(r"^ARGUS_GATEWAY_TOKEN=.*$", f"ARGUS_GATEWAY_TOKEN={nuevo}", t, flags=re.M)
else:
    t = t.rstrip("\n") + f"\nARGUS_GATEWAY_TOKEN={nuevo}\n"
p.write_text(t)
print(f"  actualizado {p}")
PY
done

# El fichero que monta vmalert. Sin salto de linea: el token es el contenido.
mkdir -p platform/secrets
printf '%s' "$NUEVO" > platform/secrets/argus_token
chmod 600 platform/secrets/argus_token
echo "  actualizado platform/secrets/argus_token"

echo
echo "  Ahora, para que los procesos lo recojan:"
echo "    make up"
echo
echo "  Y comprueba que la entrega sigue viva:"
echo "    make channel-test"
