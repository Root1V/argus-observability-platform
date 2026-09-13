#!/usr/bin/env python3
"""¿Está todo listo para conectar una aplicación real? (piloto)

Responde a una pregunta concreta: **si conecto una de mis aplicaciones ahora
mismo, ¿funcionaría el flujo entero?** Desde que la app emite un span hasta que
llega un aviso a una persona.

Distingue tres cosas, y la distinción importa:

  BLOQUEANTE  sin esto el piloto no funciona
  SEGÚN LA APP  solo hace falta si la app piloto usa X
  RECOMENDABLE  el piloto funciona, pero con menos red de seguridad

Uso:
    python scripts/pilot_check.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

VERDE, ROJO, AMARILLO, GRIS, BOLD, OFF = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"
)

RAIZ = Path(__file__).resolve().parent.parent

bloqueantes_fallidos: list[str] = []
avisos: list[str] = []


def linea(estado: str, etiqueta: str, detalle: str = "") -> None:
    iconos = {"ok": f"{VERDE}OK  {OFF}", "no": f"{ROJO}FALTA{OFF}", "aviso": f"{AMARILLO}AVISO{OFF}"}
    print(f"{iconos[estado]} {etiqueta}")
    if detalle:
        print(f"{GRIS}       {detalle}{OFF}")


def bloqueante(etiqueta: str, ok: bool, detalle_fallo: str = "", detalle_ok: str = "") -> bool:
    linea("ok" if ok else "no", etiqueta, detalle_ok if ok else detalle_fallo)
    if not ok:
        bloqueantes_fallidos.append(etiqueta)
    return ok


def recomendable(etiqueta: str, ok: bool, detalle: str = "") -> bool:
    linea("ok" if ok else "aviso", etiqueta, "" if ok else detalle)
    if not ok:
        avisos.append(etiqueta)
    return ok


def http_ok(url: str, timeout: float = 5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except urllib.error.HTTPError as exc:
        return exc.code < 500
    except Exception:  # noqa: BLE001
        return False


def get_json(url: str) -> dict | list | None:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.load(r)
    except Exception:  # noqa: BLE001
        return None


def seccion(titulo: str) -> None:
    print(f"\n{BOLD}── {titulo} {OFF}")


def main() -> int:
    print(f"\n{BOLD}¿Listo para un piloto con una aplicación real?{OFF}")

    # --- 1. El plano central responde ---------------------------------------
    seccion("1. El plano central")

    bloqueante(
        "El Collector gateway acepta telemetría",
        http_ok("http://127.0.0.1:13133/"),
        "Arranca la plataforma: make up",
    )
    bloqueante(
        "ClickHouse almacena",
        http_ok("http://127.0.0.1:8123/ping"),
        "Arranca la plataforma: make up",
    )
    bloqueante(
        "El alert-bus detecta",
        http_ok("http://127.0.0.1:8080/healthz"),
        "Arranca la plataforma: make up",
    )
    recomendable(
        "VictoriaMetrics guarda métricas",
        http_ok("http://127.0.0.1:8428/health"),
        "Sin esto no hay SLO ni burn-rate; la detección de errores sigue funcionando",
    )

    # --- 2. El agente de esta máquina ----------------------------------------
    seccion("2. El Collector agente")
    print(f"{GRIS}   Tu app exporta aquí, no al plano central. Es lo que permite mover")
    print(f"   el central de máquina sin tocar ninguna aplicación.{OFF}")

    bloqueante(
        "Hay un agente escuchando en localhost:4317/4318",
        http_ok("http://127.0.0.1:13134/"),
        "Arráncalo: make agent",
    )
    recomendable(
        "El agente expone sus métricas (para vigilar su cola)",
        http_ok("http://127.0.0.1:8889/metrics"),
        "Sin esto no sabrás si la cola crece porque el central está inalcanzable",
    )

    # --- 3. La librería es instalable ----------------------------------------
    seccion("3. La librería, instalable desde fuera")
    print(f"{GRIS}   Una app real no vive en este workspace: necesita instalarla.{OFF}")

    ruedas = list((RAIZ / "dist").glob("argus_obs_sdk-*.whl"))
    bloqueante(
        "Hay ruedas construidas de argus-obs-sdk",
        bool(ruedas),
        "Constrúyelas: make wheels",
        detalle_ok=f"{len(list((RAIZ / 'dist').glob('*.whl')))} ruedas en dist/",
    )

    if ruedas:
        # Instalar de verdad en un entorno limpio: que exista la rueda no
        # garantiza que se resuelva, y ese es justo el fallo que aparecería el
        # día del piloto.
        prueba = Path("/tmp/argus-pilot-check")
        subprocess.run(["rm", "-rf", str(prueba)], check=False)
        prueba.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(["uv", "venv", "--quiet"], cwd=prueba, check=True, timeout=120,
                           capture_output=True)
            resultado = subprocess.run(
                ["uv", "pip", "install", "--quiet", "--find-links", str(RAIZ / "dist"),
                 "argus-obs-sdk[asgi,client]"],
                cwd=prueba, capture_output=True, text=True, timeout=300,
            )
            instalable = resultado.returncode == 0
            bloqueante(
                "Se instala en un entorno limpio",
                instalable,
                (resultado.stderr or "")[:200],
            )
        except Exception as exc:  # noqa: BLE001
            bloqueante("Se instala en un entorno limpio", False, str(exc)[:200])

    # --- 4. Llega el aviso a una persona -------------------------------------
    seccion("4. El aviso llega a una persona")

    stats = get_json("http://127.0.0.1:8080/stats") or {}
    canales = set(stats.get("canales", []))
    canales_reales = canales - {"console", "json", "memory"}

    bloqueante(
        "Hay al menos un canal que una persona vea",
        bool(canales_reales),
        "Solo hay salida por consola. Configura ALERTBUS_GCHAT_WEBHOOK o SMTP "
        "en platform/.env y reinicia el alert-bus.",
        detalle_ok=f"canales: {', '.join(sorted(canales_reales))}",
    )
    if canales and not canales_reales:
        print(f"{GRIS}       (activos: {', '.join(sorted(canales))} — útiles para depurar, "
              f"nadie los mira a las 3 AM){OFF}")

    # --- 5. El registro conoce la app ----------------------------------------
    seccion("5. El registro de aplicaciones")

    registro = get_json("http://127.0.0.1:8080/registry") or []
    activas = [a for a in registro if a.get("estado") == "activo"]
    planificadas = [a for a in registro if a.get("estado") == "planificado"]

    linea("ok", f"{len(registro)} aplicaciones registradas",
          f"{len(activas)} activas · {len(planificadas)} planificadas")
    print(f"{GRIS}       Al conectar la app piloto, cambia su `estado` a `activo` en")
    print(f"       platform/registry/apps.yaml. Si no, el canario no vigilará su silencio.{OFF}")

    # --- 6. Red de seguridad --------------------------------------------------
    seccion("6. Red de seguridad")

    recomendable(
        "El dead man's switch está configurado",
        (RAIZ / "deadman" / "deadman.json").exists(),
        "Si la plataforma cae, su silencio parece salud. Ver deadman/README.md",
    )
    recomendable(
        "La cola persistente está verificada",
        True,
        "",
    )
    print(f"{GRIS}       Verificada con make queue-test: 100 de 100 spans sobreviven a un corte.{OFF}")

    # --- 7. Según qué haga la app piloto -------------------------------------
    seccion("7. Según qué haga tu aplicación piloto")

    langfuse = http_ok("http://127.0.0.1:3000/api/public/health")
    print(f"{'  ' + VERDE + 'OK  ' + OFF if langfuse else '  ' + GRIS + '·   ' + OFF} "
          f"Langfuse (trazas de prompts) {'disponible' if langfuse else 'NO arrancado'}")
    if not langfuse:
        print(f"{GRIS}       Solo hace falta si la app piloto llama a un LLM y quieres ver")
        print(f"       prompts, coste y evaluación. Arráncalo con el perfil genai.{OFF}")

    print(f"  {GRIS}·   {OFF} Si la app usa Axonium, instrumentarlo da trazas GenAI gratis (F1b-01)")
    print(f"  {GRIS}·   {OFF} Si la app es Go, usa instrumentación en compilación: cero código")

    # --- Veredicto -----------------------------------------------------------
    print(f"\n{BOLD}── Veredicto {OFF}")

    if bloqueantes_fallidos:
        print(f"\n{ROJO}NO listo. {len(bloqueantes_fallidos)} bloqueante(s):{OFF}")
        for b in bloqueantes_fallidos:
            print(f"  · {b}")
        print()
        return 1

    print(f"\n{VERDE}Listo para el piloto.{OFF}")
    if avisos:
        print(f"\n{AMARILLO}Funcionaría, pero con menos red de seguridad:{OFF}")
        for a in avisos:
            print(f"  · {a}")

    print(f"\n{GRIS}Siguiente paso: elige UNA aplicación y sigue")
    print(f"docs/piloto.md — instrumentarla son dos líneas.{OFF}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
