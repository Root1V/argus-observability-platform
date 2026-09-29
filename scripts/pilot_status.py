#!/usr/bin/env python3
"""Cuanto le queda al piloto para poder cerrarse.

Existe porque el criterio original —«un par de semanas sin sorpresas»— no era
comprobable, y un criterio que no se puede comprobar se cumple el dia que
alguien tiene prisa (D-069).

El criterio 8 se mide contando dias desde el ultimo **fallo de plataforma**, que
son las decisiones marcadas con `> **Fallo de plataforma** · descubierto AAAA-MM-DD`
en `docs/decisions.md`. Marcar uno nuevo reinicia el reloj, y eso es deliberado:
si la plataforma sigue descubriendo que no hacia lo que decia, no esta lista.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

VERDE, ROJO, AMARILLO, GRIS, BOLD, OFF = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"
)
RAIZ = Path(__file__).resolve().parent.parent
VENTANA_DIAS = 14


def fallos() -> list[tuple[dt.date, str, str]]:
    texto = (RAIZ / "docs" / "decisions.md").read_text(encoding="utf-8")
    salida = []
    for m in re.finditer(
        r"^## (D-\d+) · .*?\n\n> \*\*Fallo de plataforma\*\* · descubierto (\d{4}-\d{2}-\d{2}) · (.+)$",
        texto, re.M,
    ):
        salida.append((dt.date.fromisoformat(m.group(2)), m.group(1), m.group(3)))
    return sorted(salida)


def _get(url: str, timeout: float = 4.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:  # noqa: BLE001
        return None






# Un id corto de contenedor: doce hexadecimales. Docker los genera asi.
_ID_DE_CONTENEDOR = re.compile(r"[0-9a-f]{12}")


def clasificar_hosts(nombres) -> tuple[list[str], list[str]]:
    """Separa nombres de maquina de identificadores de contenedor.

    Sin este filtro, un despliegue viejo que siga sellando ids inflaria la
    cuenta y el criterio 6 se daria por cumplido con UNA sola maquina mal
    identificada — que es exactamente el estado del que veniamos (D-104).

    Vive aparte para poder probarla sin ClickHouse delante.
    """
    reales: list[str] = []
    sospechosos: list[str] = []
    for nombre in nombres:
        if not nombre:
            continue
        (sospechosos if _ID_DE_CONTENEDOR.fullmatch(nombre) else reales).append(nombre)
    return reales, sospechosos


def _hosts_que_emiten() -> tuple[bool | None, str]:
    """Criterio 6, medido en el almacen.

    Estuvo en «?» con «un solo agente desplegado», y la nota escondia algo
    peor que la ausencia de una segunda maquina: **no habria forma de
    distinguirla**. `resourcedetection` dentro de un contenedor devuelve el ID
    DEL CONTENEDOR, asi que los 22.377 spans del almacen llevaban todos el
    mismo `host.name` — dieciseis servicios bajo un identificador que cambia
    en cada recreacion (D-104).

    Ahora el agente sella el nombre de su maquina, que es el unico que lo sabe
    porque hay uno por host. Contar valores distintos ES el criterio.

    Se ignoran los que parecen id de contenedor: doce caracteres hexadecimales.
    Sin ese filtro, un despliegue viejo inflaria la cuenta y el criterio se
    daria por cumplido con una sola maquina mal identificada.
    """
    # Se miran METRICAS y no trazas, y una ventana corta.
    #
    # Con trazas y 24 h, una sola peticion de prueba desde un host cumplia el
    # criterio y lo dejaba en verde un dia entero — aunque ese host ya no
    # existiera. "Manda telemetria" es presente, y una prueba puntual no es un
    # host.
    #
    # Las metricas del propio agente fluyen mientras la maquina este viva, asi
    # que su presencia en los ultimos minutos SI significa que hay un agente
    # corriendo ahi ahora mismo.
    # Se cuentan las metricas del PROPIO Collector (`otelcol_*`), no las
    # derivadas de spans.
    #
    # `spanmetrics` mantiene viva cada serie que ha visto: al retirar un agente
    # de prueba, sus metricas seguian llegando con marca de tiempo actual y el
    # criterio se quedaba en verde con un host que ya no existia. Se le puso
    # `metrics_expiration`, pero aun asi la senal correcta es otra: las
    # metricas internas de un agente PARAN cuando el agente para, sin depender
    # de la configuracion de ningun conector (D-104).
    sql = (
        "SELECT ResourceAttributes['host.name'] AS h, count() "
        "FROM otel.otel_metrics_sum "
        "WHERE TimeUnix > now() - INTERVAL 10 MINUTE AND h != '' "
        "  AND MetricName LIKE 'otelcol_%' "
        "GROUP BY h ORDER BY 2 DESC FORMAT TSV"
    )
    try:
        r = subprocess.run(
            ["docker", "exec", "argus-clickhouse-1", "clickhouse-client", "-q", sql],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:  # noqa: BLE001
        return None, "no se pudo consultar ClickHouse"
    if r.returncode != 0:
        return None, "no se pudo consultar ClickHouse"

    reales, sospechosos = clasificar_hosts(
        linea.split("\t")[0] for linea in r.stdout.strip().splitlines()
    )

    aviso = f" ({len(sospechosos)} con id de contenedor, ignorados)" if sospechosos else ""
    if len(reales) >= 2:
        return True, f"{len(reales)} hosts emitiendo ahora: {', '.join(sorted(reales)[:3])}{aviso}"
    if reales:
        return False, f"un solo host identificado: {reales[0]}{aviso}"
    return None, f"ningun host con nombre real todavia{aviso}"


def _el_silencio_abrio_incidente() -> tuple[bool | None, str]:
    """Criterio 4, medido en el historico en vez de anotado a mano.

    Estuvo en «?» con «verificado el 13/09 con auth-service; no se
    re-comprueba solo» durante dos semanas y media, y la nota era honesta: NO
    se podia re-comprobar. El exito de este criterio es un EVENTO —el silencio
    de un servicio abrio un incidente— y el estado actual no lo puede
    responder, porque lo deseable es que ahora mismo no este pasando.

    Con los incidentes persistidos (D-103) ya hay algo que consultar: el
    historico conserva los resueltos.

    Devuelve `None` si el alert-bus no tiene persistencia: "no tengo registro"
    no es "no ha pasado", y confundirlos es el error que D-102 documenta.
    """
    stats = _get("http://127.0.0.1:8080/stats")
    if stats is None:
        return None, "el alert-bus no responde"
    if not stats.get("persistencia"):
        return None, "sin persistencia de incidentes: no hay historico que consultar"

    historico = _get("http://127.0.0.1:8080/incidents/history?signature=canary-silencio&dias=30")
    if historico is None:
        return None, "no se pudo consultar el historico"

    if not historico:
        # "No ha pasado" y "llevo cinco minutos mirando" no son lo mismo, y
        # esta es justo la distincion que D-102 documenta. Se mira la edad del
        # registro ENTERO, no la de los silencios: si el almacen acaba de
        # nacer, su vacio no dice nada.
        todos = _get("http://127.0.0.1:8080/incidents/history?dias=30") or []
        fechas = sorted(d.get("opened_at") or "" for d in todos if d.get("opened_at"))
        if not fechas:
            return None, "el registro de incidentes esta recien creado: todavia no dice nada"
        try:
            desde = dt.datetime.fromisoformat(fechas[0])
            horas = (dt.datetime.now(desde.tzinfo) - desde).total_seconds() / 3600
        except ValueError:
            return None, "no se pudo leer la antiguedad del registro"
        if horas < 24:
            return None, f"el registro lleva {horas:.1f} h: aun no es evidencia de ausencia"
        return False, f"ningun incidente de silencio en {horas / 24:.0f} dias de registro"

    ultimo = historico[-1]
    cuando = (ultimo.get("opened_at") or "")[:16].replace("T", " ")
    return True, (
        f"{len(historico)} en 30 dias; el ultimo {ultimo.get('app')}/"
        f"{ultimo.get('component')} el {cuando}"
    )


def _traza_que_cruza_una_cola() -> tuple[bool | None, str]:
    """Criterio 5, medido en el almacen en vez de anotado a mano.

    Estuvo en «?» con el texto «los 3 servicios del piloto son APIs HTTP»
    mucho despues de dejar de ser cierto: Prosodia cerro `S-06` con un doblaje
    real cuya traza cruza Celery de la API al worker. El criterio estaba
    cumplido y el tablero seguia diciendo que no, porque era una frase escrita
    una vez y nadie la volvio a mirar (D-102).

    La consulta pide lo que el criterio pide de verdad: UNA traza, DOS
    servicios, y al menos un span con `messaging.destination` — o sea una
    frontera que no es HTTP y que ademas no se partio.
    """
    sql = (
        "SELECT TraceId, uniqExact(ServiceName) AS s, "
        "  arrayStringConcat(groupUniqArray(ServiceName), ' -> ') AS quienes, "
        "  anyIf(SpanAttributes['messaging.destination'], "
        "        SpanAttributes['messaging.destination'] != '') AS cola "
        "FROM otel.otel_traces WHERE Timestamp > now() - INTERVAL 30 DAY "
        "GROUP BY TraceId "
        "HAVING s >= 2 AND countIf(SpanAttributes['messaging.destination'] != '') > 0 "
        "ORDER BY max(Timestamp) DESC LIMIT 1 FORMAT TSV"
    )
    try:
        salida = subprocess.run(
            ["docker", "exec", "argus-clickhouse-1", "clickhouse-client", "-q", sql],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:  # noqa: BLE001
        return None, "no se pudo consultar ClickHouse"

    fila = salida.stdout.strip()
    if salida.returncode != 0 or not fila:
        # `None` y no `False`: sin almacen no se puede afirmar que NO exista.
        return (None, "sin trazas que crucen una cola en 30 dias") if salida.returncode == 0 \
            else (None, "no se pudo consultar ClickHouse")

    partes = fila.split("\t")
    traza, _, quienes, cola = [*partes, "", "", "", ""][:4]
    return True, f"{quienes} por `{cola}` en una sola traza ({traza[:12]}…)"


def criterios() -> list[tuple[bool | None, str, str]]:
    """(cumplido, titulo, detalle). `None` = no se puede comprobar desde aqui."""
    stats = _get("http://127.0.0.1:8080/stats")
    registro = _get("http://127.0.0.1:8080/registry") or []

    activas = [a for a in registro if a.get("estado") == "activo"
               and a["id"] not in ("argus",) and not a["id"].startswith(("postgres", "redis", "minio", "clickhouse", "victoria", "temporal"))]
    canales = [c for c in (stats or {}).get("canales", []) if c not in ("console", "json", "memory")]
    cruza_cola, detalle_cola = _traza_que_cruza_una_cola()
    silencio_ok, detalle_silencio = _el_silencio_abrio_incidente()
    hosts_ok, detalle_hosts = _hosts_que_emiten()

    # 7 · el vigilante corre FUERA de los contenedores
    try:
        launchd = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5).stdout
        fila = [linea for linea in launchd.splitlines() if "com.argus.deadman" in linea]
        # El criterio pregunta si HAY red de seguridad externa, no si esa red
        # no ha encontrado nada.
        #
        # `launchctl list` da el codigo de salida de la ultima ejecucion, y el
        # vigilante sale con 1 cuando DETECTA un objetivo caido. Leerlo como
        # "roto" ponia el criterio en NO justo cuando el vigilante acababa de
        # hacer su trabajo — y el unico componente cuya credibilidad sostiene
        # todo lo demas es precisamente ese (D-101).
        #
        # Los codigos que importan:
        #   0  ejecuto y todo bien
        #   1  ejecuto y hay algo caido      <- el vigilante FUNCIONA
        #   2+ no pudo ejecutarse            <- el vigilante esta roto
        SALIDAS_SANAS = ("0", "1")
        codigo = fila[0].split()[1] if fila else None
        vigilante = codigo in SALIDAS_SANAS
        if not fila:
            detalle_v = "no instalado"
        elif codigo == "1":
            detalle_v = f"{fila[0].strip()} · corriendo, y su ultima pasada DETECTO algo"
        else:
            detalle_v = fila[0].strip()
    except Exception:  # noqa: BLE001
        vigilante, detalle_v = False, "no se pudo consultar launchctl"

    # Cargado en launchd NO es lo mismo que al dia. Lo que corre es una COPIA
    # fuera del repositorio, y hoy se descubrio que llevaba once dias sin
    # actualizarse: el arreglo estaba escrito y no hacia nada (D-088). Es el
    # mismo patron que `.env.agent` en D-079, y la segunda vez que una copia
    # derivada nos engana, asi que ahora se compara.
    if vigilante:
        import hashlib

        instalado = (
            pathlib.Path.home()
            / "Library/Application Support/argus-deadman/deadman.py"
        )
        repo = pathlib.Path(__file__).resolve().parents[1] / "deadman/deadman.py"
        try:
            h_i = hashlib.sha256(instalado.read_bytes()).hexdigest()[:12]
            h_r = hashlib.sha256(repo.read_bytes()).hexdigest()[:12]
            if h_i != h_r:
                vigilante = False
                detalle_v = (
                    f"cargado, pero la copia instalada NO es la del repo "
                    f"({h_i} vs {h_r}). Ejecuta: make deadman-setup"
                )
            else:
                detalle_v = f"{detalle_v} · al dia ({h_r})"
        except OSError as exc:
            vigilante = False
            detalle_v = f"no se pudo comparar la copia instalada: {exc}"

    return [
        (bool(activas), "1 · Una aplicación real emite con identidad correcta",
         ", ".join(a["id"] for a in activas) or "ninguna app activa"),
        (bool(canales), "2 · Un incidente real llega a una persona",
         f"canal humano: {', '.join(canales) or 'ninguno'}"),
        ((stats or {}).get("signals_deduplicated", 0) > 0, "3 · Una tormenta real se deduplica",
         f"{(stats or {}).get('signals_in',0)} señales → {(stats or {}).get('notifications_sent',0)} notificaciones"),
        (silencio_ok, "4 · El silencio de un servicio real abre incidente", detalle_silencio),
        (cruza_cola, "5 · Una traza cruza una frontera que NO es HTTP", detalle_cola),
        (hosts_ok, "6 · Un segundo host manda telemetría", detalle_hosts),
        (vigilante, "7 · Hay red de seguridad externa", f"dead man's switch: {detalle_v}"),
    ]


def main() -> int:
    print(f"\n{BOLD}Estado del piloto{OFF}\n")

    for ok, titulo, detalle in criterios():
        marca = f"{VERDE}OK  {OFF}" if ok else (f"{AMARILLO}?   {OFF}" if ok is None else f"{ROJO}NO  {OFF}")
        print(f"  {marca} {titulo}")
        print(f"{GRIS}       {detalle}{OFF}")

    lista = fallos()
    hoy = dt.date.today()
    print(f"\n  {BOLD}8 · Catorce días sin un fallo nuevo de la plataforma{OFF}")

    if not lista:
        print(f"{GRIS}       ningún fallo registrado{OFF}")
        return 0

    ultimo, codigo, resumen = lista[-1]
    dias = (hoy - ultimo).days
    quedan = max(0, VENTANA_DIAS - dias)
    barra = "█" * dias + "·" * quedan

    color = VERDE if quedan == 0 else (AMARILLO if dias >= 7 else ROJO)
    print(f"       {color}{barra}{OFF}  {dias}/{VENTANA_DIAS} días")
    print(f"{GRIS}       último: {codigo} ({ultimo}) — {resumen}{OFF}")
    print(f"{GRIS}       {len(lista)} fallos registrados en total{OFF}")

    print()
    if quedan:
        print(f"{AMARILLO}El piloto NO se puede cerrar todavía: faltan {quedan} días sin fallos nuevos.{OFF}")
        print(f"{GRIS}  Y los criterios marcados con «?» hay que comprobarlos a mano.{OFF}\n")
        return 1

    print(f"{VERDE}La ventana de 14 días está cumplida. Repasa los «?» y cierra.{OFF}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
