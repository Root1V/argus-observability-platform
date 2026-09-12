"""Salidas. Son una interfaz a proposito.

El `notifier` con Google Chat y SMTP llega en F2-04, y enchufar Keep o PagerDuty
mas adelante es escribir un adaptador, no rehacer la capa (D-025).

Todo sink recibe `update`: `False` cuando el incidente nace, `True` cuando el
agente lo enriquece. Un sink que soporte editar mensajes (Google Chat) debe
ACTUALIZAR el existente usando `thread_key`; uno que no (email) puede ignorar
las actualizaciones o mandar un seguimiento.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import ClassVar, Protocol

from argus_schemas import Incident, Severity

log = logging.getLogger("alert_bus.sinks")


class Sink(Protocol):
    name: str

    def send(self, incident: Incident, *, update: bool) -> None: ...


class ConsoleSink:
    """Salida por consola, legible para una persona.

    No es solo para desarrollo: en una plataforma que arranca, ver los
    incidentes en el log del propio alert-bus es la forma mas rapida de saber
    si la deteccion funciona antes de que exista ningun canal.
    """

    name = "console"

    _COLOR: ClassVar[dict] = {
        Severity.PAGE: "\033[31m",
        Severity.TICKET: "\033[33m",
        Severity.INFO: "\033[2m",
    }
    _ICON: ClassVar[dict] = {Severity.PAGE: "🔴", Severity.TICKET: "🟡", Severity.INFO: "⚪"}

    def __init__(self, stream=sys.stdout) -> None:
        self._stream = stream

    def send(self, incident: Incident, *, update: bool) -> None:
        color = self._COLOR.get(incident.severity, "")
        icon = self._ICON.get(incident.severity, "•")
        off = "\033[0m"
        prefijo = "↻ " if update else ""

        lineas = [
            f"{color}{prefijo}{icon} [{incident.app}/{incident.component}] {incident.title}{off}",
            f"   severidad={incident.severity.value}  señales={incident.count}  "
            f"edad={incident.age_seconds:.0f}s  id={incident.id}",
        ]
        if incident.trace_ids:
            lineas.append(f"   traza: {incident.trace_ids[0]}")
        if incident.root_cause:
            lineas.append(f"   CAUSA PROBABLE ({incident.confidence or 'sin confianza'}): {incident.root_cause}")
        for evidencia in incident.evidence[:3]:
            lineas.append(f"   · {evidencia}")
        if incident.similar_incidents:
            lineas.append(f"   VISTO ANTES: {', '.join(incident.similar_incidents[:2])}")

        print("\n".join(lineas), file=self._stream, flush=True)


class JSONSink:
    """Una linea JSON por incidente.

    Pensado para que el propio alert-bus sea observable: estas lineas las recoge
    el Collector agente como cualquier otro log, asi que los incidentes quedan
    en ClickHouse sin escribir un exportador.
    """

    name = "json"

    def __init__(self, stream=sys.stdout) -> None:
        self._stream = stream

    def send(self, incident: Incident, *, update: bool) -> None:
        payload = incident.model_dump(mode="json")
        payload["_event"] = "incident.updated" if update else "incident.opened"
        payload["components_affected"] = sorted(incident.components_affected)
        print(json.dumps(payload, ensure_ascii=False), file=self._stream, flush=True)


class MemorySink:
    """Guarda los incidentes en memoria. Para tests."""

    name = "memory"

    def __init__(self) -> None:
        self.opened: list[Incident] = []
        self.updated: list[Incident] = []

    def send(self, incident: Incident, *, update: bool) -> None:
        (self.updated if update else self.opened).append(incident)

    def clear(self) -> None:
        self.opened.clear()
        self.updated.clear()


class FailingSink:
    """Sink que siempre falla. Existe para probar que no tumba a los demas."""

    name = "failing"

    def send(self, incident: Incident, *, update: bool) -> None:
        raise RuntimeError("este sink falla a proposito")
