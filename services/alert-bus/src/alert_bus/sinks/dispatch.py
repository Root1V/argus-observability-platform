"""Despacho asincrono de notificaciones.

Es la pieza que justifica tener los canales EN PROCESO en vez de en un servicio
aparte (D-029). El argumento real a favor de separar era el aislamiento: un
webhook lento no puede bloquear la deteccion. Eso se consigue con una cola, que
es mucho mas barato que un servicio.

Un canal que tarda treinta segundos no retrasa ni un milisegundo la deteccion
del siguiente incidente.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Iterable
from dataclasses import dataclass

from argus_schemas import Incident

from .base import Sink

log = logging.getLogger("alert_bus.dispatch")


@dataclass(frozen=True)
class Envio:
    sink: Sink
    incident: Incident
    update: bool


class Dispatcher:
    """Cola con hilos trabajadores para enviar sin bloquear.

    La cola es ACOTADA y descarta al llenarse, igual que la del SDK y por el
    mismo motivo: si los canales no dan abasto, perder notificaciones es malo,
    pero quedarse sin memoria en el proceso que detecta incidentes es peor.

    El descarte se cuenta, porque una cola que descarta en silencio es una
    plataforma que cree estar avisando y no lo esta.
    """

    def __init__(self, *, workers: int = 2, max_queue: int = 1_000) -> None:
        self._cola: queue.Queue[Envio | None] = queue.Queue(maxsize=max_queue)
        self._hilos: list[threading.Thread] = []
        self._parar = threading.Event()
        self._workers = workers
        self.stats = {"enviados": 0, "fallidos": 0, "descartados": 0}

    def start(self) -> None:
        for i in range(self._workers):
            hilo = threading.Thread(target=self._run, name=f"argus-dispatch-{i}", daemon=True)
            hilo.start()
            self._hilos.append(hilo)

    def stop(self, *, timeout_s: float = 5.0) -> None:
        self._parar.set()
        for _ in self._hilos:
            try:
                self._cola.put_nowait(None)
            except queue.Full:
                pass
        for hilo in self._hilos:
            hilo.join(timeout=timeout_s)
        self._hilos.clear()

    def submit(self, sinks: Iterable[Sink], incident: Incident, *, update: bool) -> None:
        """Encola los envios. NUNCA bloquea."""
        for sink in sinks:
            try:
                self._cola.put_nowait(Envio(sink, incident, update))
            except queue.Full:
                self.stats["descartados"] += 1
                log.error(
                    "dispatch.queue_full",
                    extra={"sink": sink.name, "incident": incident.id},
                )

    def drain(self, *, timeout_s: float = 5.0) -> None:
        """Espera a que se vacie la cola. Para tests y para el apagado."""
        limite = threading.Event()
        threading.Timer(timeout_s, limite.set).start()
        while not self._cola.empty() and not limite.is_set():
            threading.Event().wait(0.01)

    def _run(self) -> None:
        while not self._parar.is_set():
            try:
                envio = self._cola.get(timeout=0.5)
            except queue.Empty:
                continue
            if envio is None:
                break
            try:
                envio.sink.send(envio.incident, update=envio.update)
                self.stats["enviados"] += 1
            except Exception as exc:  # noqa: BLE001
                # Un canal caido no puede tumbar a los demas ni al hilo.
                self.stats["fallidos"] += 1
                log.error(
                    "dispatch.send_failed",
                    extra={"sink": envio.sink.name, "incident": envio.incident.id, "error": str(exc)},
                )
            finally:
                self._cola.task_done()


class InlineDispatcher:
    """Envia en el acto, sin hilos. Para tests y para el sink de consola."""

    def __init__(self) -> None:
        self.stats = {"enviados": 0, "fallidos": 0, "descartados": 0}

    def start(self) -> None: ...
    def stop(self, *, timeout_s: float = 5.0) -> None: ...
    def drain(self, *, timeout_s: float = 5.0) -> None: ...

    def submit(self, sinks: Iterable[Sink], incident: Incident, *, update: bool) -> None:
        for sink in sinks:
            try:
                sink.send(incident, update=update)
                self.stats["enviados"] += 1
            except Exception as exc:  # noqa: BLE001
                self.stats["fallidos"] += 1
                log.error("dispatch.send_failed", extra={"sink": sink.name, "error": str(exc)})
