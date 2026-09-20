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
import random
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Final

from argus_schemas import Incident

from .base import Sink

log = logging.getLogger("alert_bus.dispatch")


# El primer intento mas dos reintentos, con espera creciente. Las entregas que
# fallaron de verdad fueron momentos sueltos —un wifi con inspeccion TLS en
# medio—, no cortes de horas: 1 s y 2 s cubren esa forma de fallo.
REINTENTOS: Final = 2
ESPERA_BASE_S: Final = 1.0


def _enviar_con_reintentos(sink: Sink, incident: Incident, *, update: bool) -> None:
    """Intenta entregar varias veces antes de darse por vencido.

    Existe por una asimetria que no se sostenia: la telemetria tiene una cola
    en disco con WAL para sobrevivir a que el portatil cambie de red, y la
    NOTIFICACION —que es la salida de todo el sistema— tenia un solo intento
    sobre esa misma red. Seis incidentes reales se detectaron correctamente
    entre el 17 y el 19 de septiembre y **ninguno se entrego** (D-080).

    Relanza la ultima excepcion si se agotan los intentos: quien llama decide
    como contarlo.
    """
    for intento in range(REINTENTOS + 1):
        try:
            sink.send(incident, update=update)
            if intento:
                log.info(
                    "dispatch.send_recovered",
                    extra={"sink": sink.name, "incident": incident.id, "intento": intento + 1},
                )
            return
        except Exception as exc:
            if intento == REINTENTOS:
                raise
            # A `debug`: un reintento que acaba funcionando no es un problema,
            # y anotarlo como error entrena a ignorar los errores.
            log.debug(
                "dispatch.send_retry",
                extra={
                    "sink": sink.name,
                    "incident": incident.id,
                    "intento": intento + 1,
                    "error": str(exc),
                },
            )
            # Jitter: con varios canales cayendo a la vez, sin el vuelven todos
            # en el mismo instante contra el mismo destino.
            time.sleep(ESPERA_BASE_S * (2**intento) * (0.5 + random.random()))


@dataclass(frozen=True)
class Envio:
    sink: Sink
    incident: Incident
    update: bool
    # Cuantas veces se ha intentado ya. Viaja con el envio para que el
    # reintento no necesite estado aparte.
    intento: int = 0


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
        # Desglose por canal. El total no basta: con varios sinks, "2 enviados"
        # no dice si el que mira una persona fue uno de los dos. Esa diferencia
        # es la que separa estar avisado de creerlo.
        self.por_canal: dict[str, dict[str, int]] = {}
        # Reintentos programados que aun no han vuelto a la cola. `drain` tiene
        # que esperarlos: si no, decir "cola vacia" significaria "entregado"
        # cuando en realidad significa "todavia no lo he vuelto a intentar".
        self._pendientes: set[threading.Timer] = set()
        self._lock = threading.Lock()

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

    def _contar(self, sink: str, clave: str) -> None:
        casilla = self.por_canal.setdefault(
            sink, {"enviados": 0, "fallidos": 0, "descartados": 0}
        )
        casilla[clave] += 1

    def submit(self, sinks: Iterable[Sink], incident: Incident, *, update: bool) -> None:
        """Encola los envios. NUNCA bloquea."""
        for sink in sinks:
            try:
                self._cola.put_nowait(Envio(sink, incident, update))
            except queue.Full:
                self.stats["descartados"] += 1
                self._contar(sink.name, "descartados")
                log.error(
                    "dispatch.queue_full",
                    extra={"sink": sink.name, "incident": incident.id},
                )

    def drain(self, *, timeout_s: float = 5.0) -> None:
        """Espera a que no quede trabajo. Para tests y para el apagado.

        Cuenta tambien los reintentos programados: con la cola vacia pero un
        temporizador esperando, "drenado" significaria "entregado" cuando en
        realidad significa "todavia no lo he vuelto a intentar".
        """
        limite = threading.Event()
        temporizador = threading.Timer(timeout_s, limite.set)
        temporizador.daemon = True
        temporizador.start()
        try:
            while not limite.is_set():
                with self._lock:
                    self._pendientes = {t for t in self._pendientes if t.is_alive()}
                    reintentos = len(self._pendientes)
                if self._cola.empty() and not reintentos:
                    return
                threading.Event().wait(0.01)
        finally:
            temporizador.cancel()

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
                self._contar(envio.sink.name, "enviados")
                if envio.intento:
                    log.info(
                        "dispatch.send_recovered",
                        extra={
                            "sink": envio.sink.name,
                            "incident": envio.incident.id,
                            "intento": envio.intento + 1,
                        },
                    )
            except Exception as exc:  # noqa: BLE001
                # Un canal caido no puede tumbar a los demas ni al hilo.
                self._tras_el_fallo(envio, exc)
            finally:
                self._cola.task_done()

    def _tras_el_fallo(self, envio: Envio, exc: Exception) -> None:
        """Reprograma el envio, o lo da por perdido si ya no quedan intentos.

        **El reintento NO duerme en el hilo trabajador.** Con un solo worker,
        esperar tres segundos por un canal muerto retrasa la entrega de todos
        los demas — que es justo la propiedad que esta cola existe para
        proteger. Se reencola con un temporizador y el hilo sigue trabajando.
        """
        if envio.intento >= REINTENTOS or self._parar.is_set():
            self.stats["fallidos"] += 1
            self._contar(envio.sink.name, "fallidos")
            log.error(
                "dispatch.send_failed",
                extra={
                    "sink": envio.sink.name,
                    "incident": envio.incident.id,
                    "intentos": envio.intento + 1,
                    "error": str(exc),
                },
            )
            return

        log.debug(
            "dispatch.send_retry",
            extra={
                "sink": envio.sink.name,
                "incident": envio.incident.id,
                "intento": envio.intento + 1,
                "error": str(exc),
            },
        )
        siguiente = replace(envio, intento=envio.intento + 1)
        espera = ESPERA_BASE_S * (2**envio.intento) * (0.5 + random.random())
        temporizador = threading.Timer(espera, self._reencolar, args=(siguiente,))
        temporizador.daemon = True
        with self._lock:
            self._pendientes.add(temporizador)
        temporizador.start()

    def _reencolar(self, envio: Envio) -> None:
        with self._lock:
            self._pendientes = {t for t in self._pendientes if t.is_alive()}
        if self._parar.is_set():
            self.stats["fallidos"] += 1
            self._contar(envio.sink.name, "fallidos")
            return
        try:
            self._cola.put_nowait(envio)
        except queue.Full:
            self.stats["descartados"] += 1
            self._contar(envio.sink.name, "descartados")


class InlineDispatcher:
    """Envia en el acto, sin hilos. Para tests y para el sink de consola."""

    def __init__(self) -> None:
        self.stats = {"enviados": 0, "fallidos": 0, "descartados": 0}
        self.por_canal: dict[str, dict[str, int]] = {}

    def _contar(self, sink: str, clave: str) -> None:
        casilla = self.por_canal.setdefault(
            sink, {"enviados": 0, "fallidos": 0, "descartados": 0}
        )
        casilla[clave] += 1

    def start(self) -> None: ...
    def stop(self, *, timeout_s: float = 5.0) -> None: ...
    def drain(self, *, timeout_s: float = 5.0) -> None: ...

    def submit(self, sinks: Iterable[Sink], incident: Incident, *, update: bool) -> None:
        for sink in sinks:
            try:
                _enviar_con_reintentos(sink, incident, update=update)
                self.stats["enviados"] += 1
                self._contar(sink.name, "enviados")
            except Exception as exc:  # noqa: BLE001
                self.stats["fallidos"] += 1
                self._contar(sink.name, "fallidos")
                log.error("dispatch.send_failed", extra={"sink": sink.name, "error": str(exc)})
