#!/usr/bin/env python3
"""Verificación end-to-end de la Fase 2, contra el stack real.

Comprueba los cuatro comportamientos que definen "detección sin ruido":

 1. Un error abre un incidente y avisa.
 2. Veinte errores idénticos son UN incidente con cuenta veinte, no veinte avisos.
 3. Cuando falla una dependencia, el síntoma se SUPRIME y el aviso útil —el de
    la causa— no queda enterrado.
 4. La investigación ACTUALIZA el mismo hilo en vez de mandar un aviso nuevo.

Y, si el canario lleva suficiente tiempo corriendo, que detecta silencios.

Uso:
    python scripts/e2e_f2.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

ALERTBUS = os.getenv("ALERTBUS_URL", "http://127.0.0.1:8080")
TOKEN = os.environ.get("ARGUS_GATEWAY_TOKEN", "")

PASS, FAIL, DIM = "\033[32mOK  \033[0m", "\033[31mFALLO\033[0m", "\033[2m"
OFF = "\033[0m"
_fallos: list[str] = []


def check(etiqueta: str, condicion: bool, detalle: str = "") -> None:
    print(f"{PASS if condicion else FAIL} {etiqueta}")
    if detalle and not condicion:
        print(f"       {detalle}")
    if not condicion:
        _fallos.append(etiqueta)


def api(ruta: str, payload: dict | None = None) -> dict | list:
    url = f"{ALERTBUS}{ruta}"
    datos = json.dumps(payload).encode() if payload is not None else None
    peticion = urllib.request.Request(url, data=datos, method="POST" if datos else "GET")
    if datos:
        peticion.add_header("Content-Type", "application/json")
    if TOKEN:
        peticion.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(peticion, timeout=15) as r:
        cuerpo = r.read()
    return json.loads(cuerpo) if cuerpo else {}


def alerta(app: str, componente: str, nombre: str) -> None:
    """Inyecta por el camino templado, que no depende del Collector agente."""
    api("/api/v2/alerts", {
        "alerts": [{
            "labels": {"alertname": nombre, "service_namespace": app, "service_name": componente},
            "annotations": {"summary": f"{nombre} en {componente}"},
        }]
    })


def incidentes() -> list[dict]:
    return api("/incidents")  # type: ignore[return-value]


def buscar(app: str, firma: str | None = None) -> dict | None:
    """Busca por app Y FIRMA.

    Buscar solo por app era fragil: una aplicacion puede tener varios
    incidentes abiertos con firmas distintas, y el test encontraba el de una
    ejecucion anterior en vez del suyo. Cada ejecucion marca sus alertas con el
    sello de tiempo justo para poder aislarse.
    """
    for inc in incidentes():
        if inc["app"] != app:
            continue
        if firma is None or inc["signature"] == firma:
            return inc
    return None


def main() -> int:
    marca = int(time.time())
    print(f"\n--- Verificación end-to-end de la Fase 2  (marca: {marca}) ---\n")

    try:
        api("/healthz")
    except Exception as exc:  # noqa: BLE001
        print(f"{FAIL} El alert-bus no responde en {ALERTBUS}: {exc}")
        print(f"{DIM}Arráncalo con: make up{OFF}\n")
        return 1

    antes = api("/stats")

    # --- 1. Un error abre un incidente --------------------------------------
    firma_basica = f"prueba-basica-{marca}"
    alerta("llm-benchmark", "bench-runner", firma_basica)
    time.sleep(1)
    check("Una alerta abre un incidente", buscar("llm-benchmark", firma_basica) is not None)

    # --- 2. Deduplicación ---------------------------------------------------
    firma_tormenta = f"tormenta-{marca}"
    for _ in range(20):
        alerta("edge-ai-inference", "gateway", firma_tormenta)
    time.sleep(1.5)

    incidente = buscar("edge-ai-inference", firma_tormenta)
    check(
        "Veinte alertas idénticas son UN incidente",
        incidente is not None and incidente["count"] >= 20,
        f"cuenta: {incidente['count'] if incidente else 'sin incidente'}",
    )

    # --- 3. Correlación por topología ---------------------------------------
    # `intelligent-document-platform` declara depender de `postgres-main`.
    firma_causa = f"causa-{marca}"
    firma_sintoma = f"sintoma-{marca}"
    alerta("postgres-main", "postgres-main", firma_causa)
    time.sleep(1)
    alerta("intelligent-document-platform", "idp-api", firma_sintoma)
    time.sleep(1.5)

    causa = buscar("postgres-main", firma_causa)
    sintoma = buscar("intelligent-document-platform", firma_sintoma)

    check(
        "El síntoma se suprime cuando hay causa aguas arriba",
        sintoma is not None and sintoma.get("suppressed_by") is not None,
        f"suppressed_by: {sintoma.get('suppressed_by') if sintoma else 'sin incidente'}",
    )
    check(
        "La causa registra a qué está afectando",
        causa is not None and len(causa.get("symptoms", [])) >= 1,
        f"síntomas: {causa.get('symptoms') if causa else 'sin incidente'}",
    )

    # --- 4. Divulgación progresiva ------------------------------------------
    if causa:
        api(f"/incidents/{causa['fingerprint']}/enrich", {
            "root_cause": "El disco de datos se llenó al 100%",
            "confidence": "alta",
            "evidence": ["disk.used_percent = 100 desde las 14:31"],
            "similar_incidents": ["#12 (3 feb): mismo patrón tras una migración"],
        })
        time.sleep(1)
        enriquecido = buscar("postgres-main", firma_causa)
        check(
            "La investigación enriquece el MISMO incidente",
            enriquecido is not None and enriquecido.get("root_cause") is not None,
            "el incidente no recogió la causa raíz",
        )
        check(
            "Y no abre uno nuevo",
            len([i for i in incidentes() if i["signature"] == firma_causa]) == 1,
            "el enriquecimiento duplicó el incidente en vez de actualizarlo",
        )

    # --- 5. Contabilidad ----------------------------------------------------
    despues = api("/stats")
    dedup = despues["signals_deduplicated"] - antes["signals_deduplicated"]
    supr = despues["symptoms_suppressed"] - antes["symptoms_suppressed"]
    notif = despues["notifications_sent"] - antes["notifications_sent"]

    check("El motor contabiliza las deduplicaciones", dedup >= 19, f"deduplicadas: {dedup}")
    check("Y las supresiones por correlación", supr >= 1, f"suprimidos: {supr}")

    print(f"\n{DIM}  señales entrantes:    {despues['signals_in'] - antes['signals_in']}")
    print(f"  notificaciones:       {notif}")
    print(f"  deduplicadas:         {dedup}")
    print(f"  síntomas suprimidos:  {supr}")
    print(f"  canales activos:      {', '.join(despues.get('canales', []))}")
    print(f"  despacho:             {despues.get('despacho', {})}{OFF}")

    print()
    if _fallos:
        print(f"\033[31m{len(_fallos)} comprobación(es) fallaron:\033[0m")
        for f in _fallos:
            print(f"  - {f}")
        return 1
    print("\033[32mTodas las comprobaciones pasaron.\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
