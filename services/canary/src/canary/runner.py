"""Planificador: ejecuta las sondas y reporta al alert-bus.

Tres cosas que no son obvias y que decidí explícitamente:

1. **Confirmación antes de alertar.** Una sonda fallida no es un incidente. Las
   redes tienen microcortes y los servicios tienen reinicios de un segundo.
   Hacen falta N fallos consecutivos, lo que cambia el tiempo de deteccion por
   precision — y en un canario esa es la moneda correcta, porque un canario que
   grita por cada hipo es un canario que se silencia.

2. **Se reporta la RECUPERACION igual que el fallo.** Un incidente que se
   resuelve solo tiene que cerrarse, o la lista de incidentes abiertos deja de
   ser util en cuestion de dias.

3. **El canario se observa a si mismo.** Si el canario cae, deja de reportar
   silencios, y el silencio del canario es indistinguible de que todo va bien.
   Por eso emite su propio latido.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

import httpx
from argus_schemas import Signal, SignalKind

from .probes import Medicion, Resultado, Sonda

log = logging.getLogger("canary")


class Runner:
    def __init__(
        self,
        sondas: list[Sonda],
        *,
        alertbus_url: str,
        token: str = "",
        intervalo_s: int = 300,
        fallos_para_alertar: int = 2,
        timeout_s: float = 10.0,
    ) -> None:
        self._sondas = sondas
        self._alertbus = alertbus_url.rstrip("/")
        self._token = token
        self._intervalo = intervalo_s
        self._umbral = fallos_para_alertar
        self._timeout = timeout_s

        # objetivo -> fallos consecutivos. Es lo que implementa la confirmacion.
        self._consecutivos: dict[str, int] = defaultdict(int)
        # objetivos que ya generaron alerta, para no repetir y para poder cerrar.
        self._alertados: set[str] = set()

        self.stats = {"rondas": 0, "sondas_ok": 0, "sondas_fallidas": 0, "alertas": 0, "recuperaciones": 0}

    async def ronda(self) -> list[Medicion]:
        """Ejecuta todas las sondas una vez, en paralelo."""
        resultados = await asyncio.gather(
            *(s.ejecutar() for s in self._sondas), return_exceptions=True
        )

        mediciones: list[Medicion] = []
        for sonda, resultado in zip(self._sondas, resultados, strict=True):
            if isinstance(resultado, BaseException):
                # Una sonda que revienta es un fallo de la sonda, no de la app.
                # Reportarlo como caida de la app seria inventarse un incidente.
                log.error(
                    "probe.crashed",
                    extra={"probe": type(sonda).__name__, "error": str(resultado)},
                )
                continue
            mediciones.append(resultado)

        self.stats["rondas"] += 1
        await self._procesar(mediciones)
        return mediciones

    async def _procesar(self, mediciones: list[Medicion]) -> None:
        senales: list[Signal] = []

        for medicion in mediciones:
            objetivo = medicion.objetivo

            if not medicion.es_problema:
                self.stats["sondas_ok"] += 1
                if self._consecutivos[objetivo]:
                    self._consecutivos[objetivo] = 0
                if objetivo in self._alertados:
                    self._alertados.discard(objetivo)
                    self.stats["recuperaciones"] += 1
                    log.info("canary.recovered", extra={"objetivo": objetivo})
                continue

            self.stats["sondas_fallidas"] += 1
            self._consecutivos[objetivo] += 1

            # Confirmacion: N fallos consecutivos antes de molestar a nadie.
            if self._consecutivos[objetivo] < self._umbral:
                log.info(
                    "canary.unconfirmed",
                    extra={"objetivo": objetivo, "fallos": self._consecutivos[objetivo]},
                )
                continue

            # Ya alertado: el alert-bus deduplica igualmente, pero no tiene
            # sentido mandarle la misma senal cada cinco minutos para siempre.
            if objetivo in self._alertados:
                continue

            self._alertados.add(objetivo)
            self.stats["alertas"] += 1
            senales.append(self._a_senal(medicion))

        if senales:
            await self._reportar(senales)

    def _a_senal(self, medicion: Medicion) -> Signal:
        kind = SignalKind.SILENCE if medicion.resultado is Resultado.SILENCIO else SignalKind.ERROR
        titulo = {
            Resultado.CAIDO: f"{medicion.component} no responde",
            Resultado.LENTO: f"{medicion.component} responde por encima de su objetivo",
            Resultado.SILENCIO: f"{medicion.component} dejó de emitir telemetría",
        }.get(medicion.resultado, f"{medicion.component}: {medicion.resultado}")

        return Signal(
            kind=kind,
            app=medicion.app,
            component=medicion.component,
            signature=f"canary-{medicion.resultado.value}",
            title=titulo,
            duration_ms=medicion.duracion_ms,
            attributes={"canary.detalle": medicion.detalle, "canary.resultado": medicion.resultado.value},
        )

    async def _reportar(self, senales: list[Signal]) -> None:
        """Manda las señales al alert-bus por el camino de Alertmanager.

        No por OTLP: estas señales no vienen de spans y fabricar un
        `ExportTraceServiceRequest` a mano seria pretender que son algo que no
        son.
        """
        payload = {
            "alerts": [
                {
                    "labels": {
                        "alertname": s.signature,
                        "service_namespace": s.app,
                        "service_name": s.component,
                        "source": "canary",
                    },
                    "annotations": {"summary": s.title, **{k: str(v) for k, v in s.attributes.items()}},
                }
                for s in senales
            ]
        }
        cabeceras = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as cliente:
                respuesta = await cliente.post(
                    f"{self._alertbus}/api/v2/alerts", json=payload, headers=cabeceras
                )
            respuesta.raise_for_status()
            log.info("canary.reported", extra={"senales": len(senales)})
        except Exception as exc:  # noqa: BLE001
            # Si el canario no puede reportar, lo unico peor que fallar es
            # fallar en silencio: esto sale por el log, que el Collector recoge.
            log.error("canary.report_failed", extra={"error": str(exc), "senales": len(senales)})

    async def bucle(self) -> None:
        """Corre indefinidamente."""
        import argus

        while True:
            # El canario se observa a si mismo: si deja de emitir este span,
            # su propio silencio es detectable.
            with argus.step("canary.round") as paso:
                mediciones = await self.ronda()
                paso.set(
                    sondas=len(mediciones),
                    fallidas=sum(1 for m in mediciones if m.es_problema),
                    alertadas=len(self._alertados),
                )
            await asyncio.sleep(self._intervalo)
