#!/usr/bin/env python3
"""Manda un aviso de prueba por los canales configurados.

Un canal de notificacion que no has probado es peor que no tener canal: te da
la sensacion de estar cubierto sin estarlo. Esto manda un incidente real por la
tuberia real —el mismo motor, las mismas reglas de enrutamiento, los mismos
sinks— para que veas llegar el mensaje antes de confiar en el.

No simula nada: inyecta un incidente por el endpoint del camino templado y deja
que el alert-bus haga su trabajo.

Y comprueba las DOS mitades de "configurar un canal", que es donde se cuela el
falso positivo: que el sink este cargado (`ALERTBUS_SINKS` en el .env) y que el
registro ENRUTE hacia el. Con solo la primera, el aviso sale por consola, la
prueba pasa, y a las tres de la manana no suena nada.

Uso:
    python scripts/probar_canal.py
    python scripts/probar_canal.py --severidad ticket
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

VERDE, ROJO, AMARILLO, GRIS, BOLD, OFF = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"
)

ALERTBUS = os.getenv("ALERTBUS_URL", "http://127.0.0.1:8080")

# La propia plataforma: existe en el registro, asi que el enrutamiento usa
# reglas de verdad en vez de caer al comportamiento de descubrimiento.
APP_PRUEBA = "argus"
SOLO_DEPURACION = {"console", "json", "memory"}
TOKEN = os.environ.get("ARGUS_GATEWAY_TOKEN", "")


def api(ruta: str, payload=None):
    datos = json.dumps(payload).encode() if payload is not None else None
    peticion = urllib.request.Request(f"{ALERTBUS}{ruta}", data=datos,
                                      method="POST" if datos else "GET")
    if datos:
        peticion.add_header("Content-Type", "application/json")
    if TOKEN:
        peticion.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(peticion, timeout=20) as r:
        cuerpo = r.read()
    return json.loads(cuerpo) if cuerpo else {}


def _delta(antes: dict | None, despues: dict | None) -> dict[str, int]:
    """Cuenta solo lo que paso durante ESTA prueba."""
    a, d = antes or {}, despues or {}
    return {k: d.get(k, 0) - a.get(k, 0) for k in ("enviados", "fallidos", "descartados")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--severidad", choices=["page", "ticket"], default="page",
                        help="`page` avisa al instante; `ticket` espera su ventana")
    args = parser.parse_args()

    print(f"\n{BOLD}Prueba de los canales de notificación{OFF}\n")

    try:
        stats = api("/stats")
    except Exception as exc:  # noqa: BLE001
        print(f"{ROJO}El alert-bus no responde en {ALERTBUS}: {exc}{OFF}")
        print(f"{GRIS}Arráncalo con: make up{OFF}\n")
        return 1

    canales = stats.get("canales", [])
    reales = [c for c in canales if c not in SOLO_DEPURACION]

    print(f"  sinks cargados: {', '.join(canales) or 'ninguno'}")

    # Un destino de pruebas que sobrevive al despliegue es peor que no tener
    # canal: el sink está cargado, el registro enruta, y los avisos se van a un
    # servidor que no existe. Todo parece bien.
    if pruebas := stats.get("destinos_de_prueba", []):
        print(f"\n{ROJO}Hay un canal apuntando a un destino de PRUEBAS:{OFF}")
        for d in pruebas:
            print(f"{GRIS}  {d}{OFF}")
        print(f"{GRIS}  Quita la variable *_API_BASE del entorno y vuelve a desplegar.{OFF}\n")
        return 1
    if not reales:
        print(f"\n{AMARILLO}Solo hay salida por consola y JSON.{OFF}")
        print(f"{GRIS}  Útiles para depurar, pero nadie los mira a las 3 de la mañana.")
        print(f"  Configura uno en platform/.env — ver docs/canales.md{OFF}\n")
        return 1

    # Segunda mitad: el registro decide a dónde va el aviso de ESTA aplicación.
    # Un sink cargado al que nadie enruta no notifica a nadie.
    try:
        entrada = next(a for a in api("/registry") if a["id"] == APP_PRUEBA)
    except (StopIteration, KeyError):
        print(f"\n{ROJO}«{APP_PRUEBA}» no está en el registro.{OFF}")
        print(f"{GRIS}  Revisa platform/registry/apps.yaml{OFF}\n")
        return 1

    enrutados = list(entrada.get("canales", {}).get(args.severidad, [])) or ["console"]
    print(f"  registro → «{APP_PRUEBA}» / {args.severidad}: {', '.join(enrutados)}")

    # Enrutar a un canal que no esta cargado no es un fallo: el registro puede
    # nombrar varios y el aviso sale por los que haya. Se dice, porque un canal
    # que se cae de la lista en silencio es como se cree estar avisando.
    sin_cargar = [c for c in enrutados if c not in canales]
    if sin_cargar:
        print(f"  {GRIS}(sin configurar, se omiten: {', '.join(sin_cargar)}){OFF}")

    efectivos = [c for c in enrutados if c in canales]
    humanos = [c for c in efectivos if c not in SOLO_DEPURACION]

    if not humanos:
        print(f"\n{AMARILLO}El sink está cargado, pero el registro no enruta hacia él.{OFF}")
        print(f"{GRIS}  Configurar un canal son dos cosas, y esta es la segunda.")
        print(f"  En platform/registry/apps.yaml, en la entrada «{APP_PRUEBA}»:")
        print("    canales:")
        print(f"      page:   [{reales[0]}]")
        print(f"      ticket: [{reales[0]}]")
        print(f"  El registro se recarga solo; no hace falta reiniciar nada.{OFF}\n")
        return 1

    print(f"  {VERDE}canal que una persona verá: {', '.join(humanos)}{OFF}\n")

    antes = api("/stats").get("despacho_por_canal", {})

    marca = int(time.time())
    firma = f"prueba-de-canal-{marca}"

    print(f"{GRIS}  inyectando un incidente de severidad «{args.severidad}»…{OFF}")
    api("/api/v2/alerts", [{
        "labels": {
            "alertname": firma,
            "service_namespace": APP_PRUEBA,
            "service_name": "alert-bus",
            "severity": args.severidad,
        },
        "annotations": {
            "summary": "Prueba de canal: si ves esto, los avisos llegan",
        },
    }])

    # El `page` sale al instante; el `ticket` espera la ventana de agrupación.
    espera = 4 if args.severidad == "page" else 70
    if args.severidad == "ticket":
        print(f"{GRIS}  un «ticket» espera su ventana de agrupación (~60 s)…{OFF}")
    time.sleep(espera)

    incidentes = api("/incidents")
    nuestro = next((i for i in incidentes if i["signature"] == firma), None)

    if nuestro is None:
        print(f"\n{ROJO}El incidente no llegó a abrirse.{OFF}")
        print(f"{GRIS}  Mira: docker compose -f platform/compose.yaml logs alert-bus{OFF}\n")
        return 1

    notificado = nuestro.get("notified_at") is not None
    print(f"  {VERDE}OK  {OFF} incidente abierto: {nuestro['id']}")
    print(f"  {VERDE if notificado else ROJO}{'OK  ' if notificado else 'FALLO'}{OFF} "
          f"{'notificado' if notificado else 'NO notificado'}")

    despues = api("/stats").get("despacho_por_canal", {})
    fallidos = 0
    entregados = []
    for canal in efectivos:
        d = _delta(antes.get(canal), despues.get(canal))
        fallidos += d["fallidos"]
        if d["enviados"]:
            entregados.append(canal)

    # La comprobación que faltaba: no «no hubo fallos», sino «el canal que mira
    # una persona entregó». Un canal al que nunca se intentó enviar no falla.
    ok = [c for c in entregados if c not in SOLO_DEPURACION]
    print(f"  {VERDE if ok else ROJO}{'OK  ' if ok else 'FALLO'}{OFF} "
          f"entregado por: {', '.join(entregados) or 'ninguno'}")
    if fallidos:
        print(f"  {ROJO}FALLO{OFF} envíos fallidos: {fallidos}")

    if not ok:
        print(f"\n{ROJO}Ningún canal humano entregó el aviso.{OFF}")
        print(f"{GRIS}  docker compose -f platform/compose.yaml logs alert-bus | grep -i sink{OFF}\n")
        return 1

    # Enriquecer, para que veas también la divulgación progresiva: el mismo
    # mensaje se actualiza en vez de llegar uno nuevo.
    print(f"\n{GRIS}  enriqueciendo el incidente, como haría el agente de investigación…{OFF}")
    api(f"/incidents/{nuestro['fingerprint']}/enrich", {
        "root_cause": "Ninguna: esto es una prueba de canal",
        "confidence": "alta",
        "evidence": ["El mensaje que estás leyendo llegó por la tubería real"],
        "similar_incidents": ["Así se ve cuando el agente ya investigó"],
    })
    time.sleep(3)

    print(f"\n{BOLD}Ahora mira el canal.{OFF}")
    print(f"{GRIS}  Deberías ver UN mensaje, no dos: el aviso inicial y el informe")
    print("  llegan al mismo hilo. Si ves dos, la divulgación progresiva no")
    print(f"  está funcionando en ese canal.{OFF}")

    if fallidos:
        print(f"\n{ROJO}Hubo {fallidos} envío(s) fallidos. Mira los logs:{OFF}")
        print(f"{GRIS}  docker compose -f platform/compose.yaml logs alert-bus | grep sink{OFF}\n")
        return 1

    print(f"\n{VERDE}Los canales funcionan: {', '.join(ok)}.{OFF}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
