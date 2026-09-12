"""El motor: deduplica, agrupa, decide severidad y enruta.

Aqui vive la tension central de la fase. El tiempo real quiere avisar ya;
la ausencia de fatiga quiere agrupar y esperar. Las dos cosas a la vez se
consiguen con **divulgacion progresiva**:

- Un `page` se notifica en cuanto nace, con lo poco que se sepa.
- Las senales siguientes NO generan mensajes nuevos: actualizan el mismo hilo.
- Cuando el agente de investigacion termine, edita ese mismo mensaje.

Asi el primer aviso llega en segundos y el informe completo cuando esta listo,
sin duplicar notificaciones. Las severidades menores si esperan su ventana,
porque ahi la rapidez no compra nada.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from argus_schemas import Incident, IncidentState, Severity, Signal, SignalKind

from .registry import Registry
from .sinks import Sink

log = logging.getLogger("alert_bus.engine")


def _now() -> datetime:
    return datetime.now(UTC)


class Engine:
    """Estado en memoria de los incidentes abiertos.

    En memoria a proposito: el camino caliente no puede pagar un viaje a la base
    de datos por senal. Lo que se pierde en un reinicio son incidentes abiertos,
    y eso se recupera con la siguiente senal — la fuente de verdad de que algo
    esta roto es el flujo, no este proceso.
    """

    def __init__(
        self,
        registry: Registry,
        sinks: Iterable[Sink] = (),
        *,
        group_window_s: int = 60,
        resolve_after_s: int = 900,
        max_incidents: int = 5_000,
    ) -> None:
        self._registry = registry
        self._sinks = list(sinks)
        self._group_window = timedelta(seconds=group_window_s)
        self._resolve_after = timedelta(seconds=resolve_after_s)
        self._max = max_incidents

        self._incidents: dict[str, Incident] = {}   # huella -> incidente abierto
        self._order: deque[str] = deque()           # para acotar la memoria
        self._lock = threading.Lock()

        self.stats = {
            "signals_in": 0,
            "incidents_opened": 0,
            "signals_deduplicated": 0,
            "notifications_sent": 0,
            "unregistered_apps": 0,
        }

    # --- Entrada -------------------------------------------------------------

    def ingest(self, signals: Iterable[Signal]) -> list[Incident]:
        """Procesa un lote y devuelve los incidentes que hay que notificar ya."""
        to_notify: list[Incident] = []

        for signal in signals:
            self.stats["signals_in"] += 1
            incident, is_new = self._absorb(signal)

            if is_new and incident.needs_notification:
                to_notify.append(incident)

        for incident in to_notify:
            self._notify(incident, update=False)

        return to_notify

    def _absorb(self, signal: Signal) -> tuple[Incident, bool]:
        app, newly_discovered = self._registry.resolve(signal.app, signal.component)
        if newly_discovered:
            self.stats["unregistered_apps"] += 1

        severity = self._severity_for(signal, app.severity_ceiling)
        huella = signal.fingerprint()

        with self._lock:
            existing = self._incidents.get(huella)

            if existing is not None and existing.state is not IncidentState.RESOLVED:
                existing.absorb(signal)
                self.stats["signals_deduplicated"] += 1
                # Una tormenta de senales puede ELEVAR la severidad, pero nunca
                # bajarla: que el problema se repita no lo hace menos grave.
                if severity.rank > existing.severity.rank:
                    existing.severity = severity
                return existing, False

            incident = Incident.from_signal(signal, severity, uuid.uuid4().hex[:12])
            incident.thread_key = f"argus-{huella}"
            self._incidents[huella] = incident
            self._order.append(huella)
            self.stats["incidents_opened"] += 1
            self._evict_if_needed()
            return incident, True

    def _severity_for(self, signal: Signal, ceiling: Severity) -> Severity:
        """Severidad base por tipo de senal, acotada por la criticidad de la app.

        Una aplicacion de criticidad baja no despierta a nadie por mucho que
        falle: es la valvula que evita que un laboratorio genere guardias.
        """
        base = {
            SignalKind.ERROR: Severity.PAGE,
            SignalKind.GUARDRAIL: Severity.PAGE,
            SignalKind.SILENCE: Severity.PAGE,
            SignalKind.SLO_BREACH: Severity.TICKET,
            SignalKind.BURN_RATE: Severity.PAGE,
            SignalKind.ANOMALY: Severity.TICKET,
            SignalKind.EVAL_DROP: Severity.TICKET,
            SignalKind.DRIFT: Severity.INFO,
        }.get(signal.kind, Severity.TICKET)

        return base if base.rank <= ceiling.rank else ceiling

    # --- Salida --------------------------------------------------------------

    def _notify(self, incident: Incident, *, update: bool) -> None:
        channels = self._registry.channels_for(incident.app, incident.severity)
        for sink in self._sinks:
            if sink.name not in channels and "console" not in channels:
                continue
            try:
                sink.send(incident, update=update)
            except Exception as exc:  # noqa: BLE001
                # Un sink caido no puede impedir que los demas reciban. Perder
                # un canal es malo; perder la alerta entera es peor.
                log.error("sink.failed", extra={"sink": sink.name, "error": str(exc)})

        if not update:
            incident.notified_at = _now()
            self.stats["notifications_sent"] += 1

    def flush_grouped(self) -> list[Incident]:
        """Notifica los incidentes cuya ventana de agrupacion ya cerro.

        Solo afecta a severidades por debajo de `page`: los `page` ya salieron
        en cuanto nacieron.
        """
        ahora = _now()
        listos: list[Incident] = []

        with self._lock:
            for incident in self._incidents.values():
                if incident.notified_at is not None:
                    continue
                if incident.severity is Severity.INFO:
                    continue     # solo digest diario
                if ahora - incident.opened_at >= self._group_window:
                    listos.append(incident)

        for incident in listos:
            self._notify(incident, update=False)
        return listos

    def enrich(self, fingerprint: str, **campos: object) -> Incident | None:
        """Anade el resultado de la investigacion y ACTUALIZA el mismo hilo.

        Es la tercera fase de la divulgacion progresiva: el mensaje que ya salio
        se edita, no se manda otro.
        """
        with self._lock:
            incident = self._incidents.get(fingerprint)
            if incident is None:
                return None
            for clave, valor in campos.items():
                if hasattr(incident, clave) and valor is not None:
                    setattr(incident, clave, valor)
            incident.enriched_at = _now()
            incident.updated_at = _now()

        if incident.notified_at is not None:
            self._notify(incident, update=True)
        return incident

    # --- Mantenimiento -------------------------------------------------------

    def sweep(self) -> int:
        """Cierra los incidentes que llevan rato sin senales nuevas.

        Sin esto, un incidente resuelto seguiria absorbiendo senales meses
        despues y el mismo problema dos semanas mas tarde no generaria aviso.
        """
        ahora = _now()
        cerrados = 0
        with self._lock:
            for huella, incident in list(self._incidents.items()):
                if incident.state is IncidentState.RESOLVED:
                    continue
                if ahora - incident.last_seen_at >= self._resolve_after:
                    incident.state = IncidentState.RESOLVED
                    incident.resolved_at = ahora
                    cerrados += 1
                    del self._incidents[huella]
        return cerrados

    def _evict_if_needed(self) -> None:
        """Acota la memoria. Se llama con el lock tomado."""
        while len(self._incidents) > self._max and self._order:
            huella = self._order.popleft()
            self._incidents.pop(huella, None)

    # --- Consulta ------------------------------------------------------------

    def open_incidents(self) -> list[Incident]:
        with self._lock:
            return sorted(
                self._incidents.values(),
                key=lambda i: (-i.severity.rank, i.opened_at),
            )

    def acknowledge(self, incident_id: str) -> Incident | None:
        with self._lock:
            for incident in self._incidents.values():
                if incident.id == incident_id:
                    incident.state = IncidentState.ACKNOWLEDGED
                    incident.updated_at = _now()
                    return incident
        return None
