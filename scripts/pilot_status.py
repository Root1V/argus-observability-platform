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


def criterios() -> list[tuple[bool | None, str, str]]:
    """(cumplido, titulo, detalle). `None` = no se puede comprobar desde aqui."""
    stats = _get("http://127.0.0.1:8080/stats")
    registro = _get("http://127.0.0.1:8080/registry") or []

    activas = [a for a in registro if a.get("estado") == "activo"
               and a["id"] not in ("argus",) and not a["id"].startswith(("postgres", "redis", "minio", "clickhouse", "victoria", "temporal"))]
    canales = [c for c in (stats or {}).get("canales", []) if c not in ("console", "json", "memory")]

    # 7 · el vigilante corre FUERA de los contenedores
    try:
        launchd = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5).stdout
        fila = [l for l in launchd.splitlines() if "com.argus.deadman" in l]
        vigilante = bool(fila) and fila[0].split()[1] == "0"
        detalle_v = fila[0].strip() if fila else "no instalado"
    except Exception:  # noqa: BLE001
        vigilante, detalle_v = False, "no se pudo consultar launchctl"

    return [
        (bool(activas), "1 · Una aplicación real emite con identidad correcta",
         ", ".join(a["id"] for a in activas) or "ninguna app activa"),
        (bool(canales), "2 · Un incidente real llega a una persona",
         f"canal humano: {', '.join(canales) or 'ninguno'}"),
        ((stats or {}).get("signals_deduplicated", 0) > 0, "3 · Una tormenta real se deduplica",
         f"{(stats or {}).get('signals_in',0)} señales → {(stats or {}).get('notifications_sent',0)} notificaciones"),
        (None, "4 · El silencio de un servicio real abre incidente",
         "verificado el 13/09 con auth-service; no se re-comprueba solo"),
        (None, "5 · Una traza cruza una frontera que NO es HTTP",
         "los 3 servicios del piloto son APIs HTTP — F1-27"),
        (None, "6 · Un segundo host manda telemetría",
         "un solo agente desplegado — F1-10"),
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
