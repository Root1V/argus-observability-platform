#!/usr/bin/env python3
"""Mide el presupuesto de latencia del camino caliente. (F2-09)

La pregunta que responde: **¿cuanto tarda un error en convertirse en un aviso?**

El plan presupuesta ~2 s y pone el objetivo en < 5 s. Hasta ahora eso estaba
DISENADO pero no MEDIDO, que no es lo mismo. Esto lo mide con un reloj.

Se cronometra desde `span.end()` en la aplicacion hasta que el incidente aparece
en el alert-bus, atravesando la tuberia real:

    app --OTLP--> Collector agente --filter+batch 200ms--> alert-bus

Uso:
    python scripts/measure_latency.py            # 10 mediciones
    python scripts/measure_latency.py --n 30     # mas muestras
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

ALERTBUS = os.getenv("ALERTBUS_URL", "http://127.0.0.1:8080")
AGENT_OTLP = os.getenv("ARGUS_ENDPOINT", "http://127.0.0.1:4318")
TOKEN = os.environ.get("ARGUS_GATEWAY_TOKEN", "")

VERDE, ROJO, AMARILLO, GRIS, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

OBJETIVO_S = 5.0
PRESUPUESTO_S = 2.0


def incidentes() -> list[dict]:
    try:
        with urllib.request.urlopen(f"{ALERTBUS}/incidents", timeout=5) as r:
            return json.load(r)
    except Exception:  # noqa: BLE001
        return []


def esperar_incidente(firma: str, *, timeout_s: float = 30.0) -> float | None:
    """Sondea hasta que aparezca el incidente. Devuelve el instante en que se vio."""
    limite = time.time() + timeout_s
    while time.time() < limite:
        for inc in incidentes():
            if inc.get("signature") == firma:
                return time.time()
        # Sondeo fino: el propio sondeo no debe dominar la medicion.
        time.sleep(0.05)
    return None


def medir(n: int) -> list[float]:
    import argus

    argus.init(
        "latency-probe",
        namespace="argus",
        role="cli",
        environment=os.getenv("ARGUS_ENVIRONMENT", "mac-dev"),
        endpoint=AGENT_OTLP,
        # Lote minimo: estamos midiendo la tuberia, no el agrupamiento del SDK.
        # En produccion el valor por defecto (200 ms) es parte del presupuesto.
        json_logs=False,
    )
    handle = argus.handle()

    muestras: list[float] = []
    for i in range(n):
        firma = f"latency-probe-{int(time.time() * 1000)}-{i}"

        with argus.step(firma) as s:
            s.error(firma)          # marca argus.hot -> camino caliente

        # El cronometro arranca cuando el span se cierra, que es el instante en
        # que la informacion existe y podria haberse actuado sobre ella.
        t0 = time.perf_counter()
        wall0 = time.time()

        # Forzamos el envio: sin esto medirimos el `schedule_delay` del SDK,
        # que en un proceso de vida corta puede ser todo el tiempo.
        handle.force_flush(timeout_millis=2000)

        visto = esperar_incidente(firma)
        if visto is None:
            print(f"{ROJO}  muestra {i + 1}: el incidente nunca aparecio{OFF}")
            continue

        transcurrido = visto - wall0 + (time.perf_counter() - t0) * 0
        muestras.append(transcurrido)

        color = VERDE if transcurrido <= PRESUPUESTO_S else (AMARILLO if transcurrido <= OBJETIVO_S else ROJO)
        print(f"  muestra {i + 1:2}: {color}{transcurrido * 1000:7.0f} ms{OFF}")

        time.sleep(0.3)   # separar muestras para no medir el mismo lote

    return muestras


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=10, help="numero de mediciones")
    args = parser.parse_args()

    print("\n--- Presupuesto de latencia del camino caliente (F2-09) ---\n")
    print(f"{GRIS}  Desde span.end() hasta que el incidente existe en el alert-bus.{OFF}")
    print(f"{GRIS}  Ruta: app -> Collector agente -> filtro -> alert-bus{OFF}")
    print(f"{GRIS}  Presupuesto {PRESUPUESTO_S:.0f} s · objetivo < {OBJETIVO_S:.0f} s{OFF}\n")

    try:
        with urllib.request.urlopen(f"{ALERTBUS}/healthz", timeout=5):
            pass
    except Exception as exc:  # noqa: BLE001
        print(f"{ROJO}El alert-bus no responde en {ALERTBUS}: {exc}{OFF}")
        print(f"{GRIS}Arrancalo con: docker compose -f platform/compose.yaml --profile lean up -d{OFF}\n")
        return 1

    muestras = medir(args.n)
    if not muestras:
        print(f"\n{ROJO}Ninguna medicion completo. La tuberia caliente no esta conectada.{OFF}\n")
        return 1

    p50 = statistics.median(muestras)
    p95 = sorted(muestras)[int(len(muestras) * 0.95)] if len(muestras) > 1 else muestras[0]
    peor = max(muestras)

    print(f"\n  {'muestras':10} {len(muestras)}")
    print(f"  {'p50':10} {p50 * 1000:.0f} ms")
    print(f"  {'p95':10} {p95 * 1000:.0f} ms")
    print(f"  {'peor':10} {peor * 1000:.0f} ms")

    print()
    if p95 <= PRESUPUESTO_S:
        print(f"{VERDE}Dentro del presupuesto de {PRESUPUESTO_S:.0f} s.{OFF}\n")
        return 0
    if p95 <= OBJETIVO_S:
        print(f"{AMARILLO}Por encima del presupuesto de {PRESUPUESTO_S:.0f} s pero dentro del objetivo de {OBJETIVO_S:.0f} s.{OFF}")
        print(f"{GRIS}Donde mirar: `batch/hot` en agent.yaml y el `schedule_delay` del SDK.{OFF}\n")
        return 0
    print(f"{ROJO}Por encima del objetivo de {OBJETIVO_S:.0f} s. El camino caliente no cumple.{OFF}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
