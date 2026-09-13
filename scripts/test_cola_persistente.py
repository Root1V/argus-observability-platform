#!/usr/bin/env python3
"""Prueba de la cola persistente del Collector agente. (F1-09)

Valida D-004, que es una de las garantías que sostiene toda la topología: el
plano central vive en un portátil que se suspende, cambia de red y a veces está
apagado. Si la cola no funciona, cada una de esas cosas es pérdida de datos —
y peor, deja **huecos que el agente de RCA leerá como silencio en vez de como
ausencia de datos**.

La prueba simula cerrar la tapa:

  1. Cuenta lo que hay en ClickHouse.
  2. **Para el plano central.**
  3. Genera tráfico contra el Collector agente, que sigue vivo.
     (Spans con error, para quedar exentos del muestreo del gateway; si no,
     el test no podría distinguir "la cola perdió datos" de "el muestreador
     los descartó".)
  4. Comprueba que el agente lo está acumulando en disco.
  5. **Arranca el plano central.**
  6. Comprueba que llegó TODO, sin perder ni un span.

Uso:
    python scripts/test_cola_persistente.py
    python scripts/test_cola_persistente.py --spans 200 --corte 30
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

VERDE, ROJO, AMARILLO, GRIS, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPOSE = ["docker", "compose", "-f", os.path.join(RAIZ, "platform", "compose.yaml")]

CH = os.getenv("ARGUS_CH_URL", "http://127.0.0.1:8123")
CH_USER = os.getenv("CLICKHOUSE_USER", "argus")
CH_PASS = os.environ["CLICKHOUSE_PASSWORD"]

MARCA = f"cola-{int(time.time())}"


def ch(sql: str) -> str:
    peticion = urllib.request.Request(f"{CH}/", data=sql.encode(), method="POST")
    peticion.add_header("X-ClickHouse-User", CH_USER)
    peticion.add_header("X-ClickHouse-Key", CH_PASS)
    with urllib.request.urlopen(peticion, timeout=20) as r:
        return r.read().decode().strip()


def compose(*args: str, check: bool = True) -> str:
    resultado = subprocess.run([*COMPOSE, *args], capture_output=True, text=True, timeout=180)
    if check and resultado.returncode != 0:
        raise RuntimeError(f"docker compose {' '.join(args)}: {resultado.stderr[:300]}")
    return resultado.stdout


def cola_del_agente() -> int:
    """Ocupación de la cola del agente, leída de sus propias métricas."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8889/metrics", timeout=5) as r:
            texto = r.read().decode()
    except Exception:  # noqa: BLE001
        return -1

    total = 0
    for linea in texto.splitlines():
        if linea.startswith("otelcol_exporter_queue_size") and "gateway" in linea:
            try:
                total += int(float(linea.rsplit(" ", 1)[1]))
            except ValueError:
                continue
    return total


def emitir(n: int) -> None:
    """Emite n spans contra el Collector AGENTE, que sigue vivo.

    Se emiten spans con ERROR a proposito, y la razon es metodologica: el
    gateway aplica tail sampling con un 10% probabilistico de base, asi que con
    spans normales llegarian ~10 de 100 y el test no podria distinguir "la cola
    perdio datos" de "el muestreador los descarto".

    Esa ambiguedad es exactamente lo que un test no puede tener. Los spans con
    error estan exentos del muestreo por politica, asi que lo que llega es
    exactamente lo que la cola entrego.
    """
    import argus

    handle = argus.init(
        "cola-probe",
        namespace=MARCA,
        role="cli",
        environment="ci",
        endpoint="http://127.0.0.1:4318",
        json_logs=False,
    )
    for i in range(n):
        with argus.step("trabajo.durante.el.corte") as s:
            s.set(indice=i)
            s.error("corte-simulado")
    handle.force_flush(timeout_millis=10_000)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spans", type=int, default=100, help="spans a emitir durante el corte")
    parser.add_argument("--corte", type=int, default=20, help="segundos con el central parado")
    args = parser.parse_args()

    os.environ["ARGUS_PROTOCOL"] = "http/protobuf"
    os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization=Bearer {os.environ['ARGUS_GATEWAY_TOKEN']}"

    print("\n--- Cola persistente del Collector agente (F1-09) ---\n")
    print(f"{GRIS}  Simula cerrar la tapa del portátil: el plano central desaparece")
    print(f"  mientras las aplicaciones siguen trabajando.{OFF}\n")

    fallos: list[str] = []

    def check(etiqueta: str, ok: bool, detalle: str = "") -> None:
        print(f"{VERDE}OK  {OFF} {etiqueta}" if ok else f"{ROJO}FALLO{OFF} {etiqueta}")
        if detalle and not ok:
            print(f"       {detalle}")
        if not ok:
            fallos.append(etiqueta)

    # --- 1. Estado de partida ------------------------------------------------
    try:
        ch("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        print(f"{ROJO}ClickHouse no responde: {exc}{OFF}\n")
        return 1
    if cola_del_agente() < 0:
        print(f"{ROJO}El Collector agente no responde en :8889. Arráncalo con: make agent{OFF}\n")
        return 1

    print(f"{GRIS}  1/5  parando el plano central…{OFF}")
    compose("stop", "collector")
    time.sleep(3)

    # --- 2. Trabajar con el central caído ------------------------------------
    print(f"{GRIS}  2/5  emitiendo {args.spans} spans con el central caído…{OFF}")
    inicio = time.perf_counter()
    emitir(args.spans)
    emision_ms = (time.perf_counter() - inicio) * 1000

    check(
        "La aplicación no se entera de que el central no está",
        emision_ms < 15_000,
        f"emitir {args.spans} spans tardó {emision_ms:.0f} ms",
    )

    time.sleep(3)
    encolados = cola_del_agente()
    check(
        "El agente acumula en vez de descartar",
        encolados > 0,
        f"ocupación de la cola: {encolados} (esperaba > 0)",
    )
    print(f"{GRIS}       cola del agente: {encolados} lotes pendientes{OFF}")

    # --- 3. Mantener el corte ------------------------------------------------
    print(f"{GRIS}  3/5  manteniendo el corte {args.corte}s…{OFF}")
    time.sleep(args.corte)

    llegados_durante = int(ch(
        f"SELECT count() FROM otel.otel_traces "
        f"WHERE ResourceAttributes['service.namespace'] = '{MARCA}'"
    ) or 0)
    check(
        "Con el central caído no llega nada (obviamente)",
        llegados_durante == 0,
        f"llegaron {llegados_durante} spans con el central parado",
    )

    # --- 4. Volver ------------------------------------------------------------
    print(f"{GRIS}  4/5  arrancando el plano central…{OFF}")
    compose("--profile", "lean", "up", "-d", "collector")

    print(f"{GRIS}  5/5  esperando a que el agente vacíe la cola…{OFF}")
    llegados = 0
    limite = time.time() + 180
    while time.time() < limite:
        time.sleep(5)
        try:
            llegados = int(ch(
                f"SELECT count() FROM otel.otel_traces "
                f"WHERE ResourceAttributes['service.namespace'] = '{MARCA}'"
            ) or 0)
        except Exception:  # noqa: BLE001
            continue
        if llegados >= args.spans:
            break

    check(
        "Al volver, llega TODO lo acumulado",
        llegados >= args.spans,
        f"llegaron {llegados} de {args.spans} spans; se perdieron {args.spans - llegados}",
    )

    print()
    print(f"{GRIS}  emitidos durante el corte: {args.spans}")
    print(f"  llegados tras la vuelta:   {llegados}")
    print(f"  duración del corte:        {args.corte}s{OFF}")

    print()
    if fallos:
        print(f"{ROJO}{len(fallos)} comprobación(es) fallaron:{OFF}")
        for f in fallos:
            print(f"  - {f}")
        print(f"\n{AMARILLO}La cola persistente es la garantía que sostiene la topología portátil.")
        print(f"Sin ella, cerrar la tapa del portátil es pérdida de datos.{OFF}\n")
        return 1

    print(f"{VERDE}La cola persistente funciona: cerrar la tapa no pierde datos.{OFF}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
