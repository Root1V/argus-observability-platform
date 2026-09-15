"""Senal e incidente: el vocabulario que comparten todos los servicios."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Severity(StrEnum):
    """Cuanto importa, y por tanto por donde y cuando se avisa.

    El orden importa: se compara con `>=` para decidir escalados.
    """

    INFO = "info"       # solo digest diario
    TICKET = "ticket"   # Google Chat, sin urgencia
    PAGE = "page"       # notificacion inmediata; fuera de horario, tambien WhatsApp

    @property
    def rank(self) -> int:
        return {"info": 0, "ticket": 1, "page": 2}[self.value]


class SignalKind(StrEnum):
    """De donde vino la senal.

    Distinguirlo importa porque cada origen tiene una latencia y una fiabilidad
    distintas: un `error` se ve en un solo span y es inequivoco; un
    `burn_rate` necesita una ventana y puede ser ruido.
    """

    ERROR = "error"                 # span con status=ERROR — camino caliente
    SLO_BREACH = "slo_breach"       # duracion sobre el objetivo — camino caliente
    GUARDRAIL = "guardrail"         # presupuesto de coste, bucle de agente
    BURN_RATE = "burn_rate"         # regla de vmalert — camino templado
    ANOMALY = "anomaly"             # banda estadistica — camino templado
    EVAL_DROP = "eval_drop"         # caida de calidad de IA
    DRIFT = "drift"                 # deriva de modelo
    SILENCE = "silence"             # el canario dejo de recibir respuesta


class IncidentState(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


def _now() -> datetime:
    return datetime.now(UTC)


def fingerprint(*parts: str | None) -> str:
    """Huella estable de un incidente.

    Se construye SOLO con campos de cardinalidad cerrada: aplicacion,
    componente, tipo de senal y firma (`error.type` o el nombre del evento).
    Nunca con el mensaje, el `trace_id` ni un identificador de usuario.

    Esa restriccion es lo que hace que mil errores iguales colapsen en UN
    incidente en vez de en mil notificaciones.
    """
    material = "|".join(p or "" for p in parts)
    return hashlib.sha256(material.encode()).hexdigest()[:16]


class Signal(BaseModel):
    """Un evento crudo que PUEDE ser un incidente.

    Lo produce el camino caliente (spans filtrados por el Collector agente) o el
    templado (reglas de vmalert). Todavia no se ha decidido si merece avisar a
    nadie: eso lo hace el `alert-bus`.
    """

    kind: SignalKind
    received_at: datetime = Field(default_factory=_now)
    occurred_at: datetime | None = None

    # Severidad PEDIDA por quien emite la senal, cuando la sabe. Una regla de
    # alerta escribe `severity: ticket` porque conoce su propia urgencia mejor
    # que una tabla por tipo de senal; ignorarla convierte cada regla del camino
    # templado en un `page` y a la guardia en gente que silencia avisos.
    #
    # Sigue siendo una PETICION: la criticidad de la aplicacion la acota
    # igualmente, asi que un laboratorio no despierta a nadie ni pidiendolo.
    severity: Severity | None = None

    # Identidad de dos niveles: la aplicacion y el sub-componente.
    app: str = "unregistered"
    component: str = "unknown"
    role: str | None = None
    environment: str | None = None

    # Que paso. `signature` es de cardinalidad CERRADA a proposito: es lo que
    # permite agrupar y lo que hace que `GROUP BY` devuelva algo accionable.
    signature: str = "unknown"
    title: str = ""

    # Con que evidencia. El `trace_id` es lo que permite saltar del aviso a la
    # traza completa cuando el camino frio la haya almacenado.
    trace_id: str | None = None
    span_id: str | None = None

    duration_ms: int | None = None
    slo_threshold_ms: int | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

    def fingerprint(self) -> str:
        return fingerprint(self.app, self.component, self.kind.value, self.signature)


class Incident(BaseModel):
    """Lo que se notifica y lo que investigan los agentes.

    Un incidente agrupa N senales con la misma huella. Nace con la primera y se
    actualiza con las siguientes: nunca se crea un segundo incidente para el
    mismo problema mientras el primero siga abierto.
    """

    id: str
    fingerprint: str
    kind: SignalKind
    severity: Severity

    app: str
    component: str
    environment: str | None = None
    title: str
    signature: str

    state: IncidentState = IncidentState.OPEN
    opened_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    last_seen_at: datetime = Field(default_factory=_now)
    resolved_at: datetime | None = None

    # Cuantas senales lo alimentaron. Es el numero que convierte "hay un error"
    # en "hay 1.247 errores en cuatro segundos".
    count: int = 1

    # Muestra acotada de trazas. Acotada a proposito: el informe necesita
    # ejemplos, no el conjunto entero.
    trace_ids: list[str] = Field(default_factory=list)
    components_affected: set[str] = Field(default_factory=set)

    # --- Correlacion por topologia -------------------------------------------
    # Cuando falla una dependencia, TODO lo que depende de ella falla tambien.
    # Sin esto, una caida de Postgres genera un aviso por cada aplicacion que lo
    # usa, y el aviso que importa —el de Postgres— queda enterrado entre los
    # sintomas.
    suppressed_by: str | None = None      # id del incidente causa, si es sintoma
    symptoms: list[str] = Field(default_factory=list)   # ids de los sintomas que causa

    # --- Divulgacion progresiva (D-015) -------------------------------------
    # `thread_key` es lo que permite que el segundo evento ACTUALICE el mensaje
    # en vez de mandar uno nuevo. Sin esto, tiempo real significa spam.
    thread_key: str | None = None
    notified_at: datetime | None = None
    enriched_at: datetime | None = None

    # Lo que anade el agente de investigacion, mas tarde.
    root_cause: str | None = None
    confidence: str | None = None
    evidence: list[str] = Field(default_factory=list)
    similar_incidents: list[str] = Field(default_factory=list)

    attributes: dict[str, Any] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}

    @classmethod
    def from_signal(cls, signal: Signal, severity: Severity, incident_id: str) -> Incident:
        return cls(
            id=incident_id,
            fingerprint=signal.fingerprint(),
            kind=signal.kind,
            severity=severity,
            app=signal.app,
            component=signal.component,
            environment=signal.environment,
            title=signal.title or f"{signal.signature} en {signal.component}",
            signature=signal.signature,
            opened_at=signal.occurred_at or signal.received_at,
            trace_ids=[signal.trace_id] if signal.trace_id else [],
            components_affected={signal.component},
            attributes=dict(signal.attributes),
        )

    def absorb(self, signal: Signal, *, max_traces: int = 10) -> None:
        """Incorpora una senal mas al incidente ya abierto."""
        self.count += 1
        self.last_seen_at = signal.received_at
        self.updated_at = _now()
        self.components_affected.add(signal.component)
        if (
            signal.trace_id
            and signal.trace_id not in self.trace_ids
            and len(self.trace_ids) < max_traces
        ):
            self.trace_ids.append(signal.trace_id)

    @property
    def is_symptom(self) -> bool:
        return self.suppressed_by is not None

    @property
    def needs_notification(self) -> bool:
        """Si hay que mandar algo ahora mismo.

        Un `page` se notifica en cuanto nace: esperar a agrupar mata el tiempo
        real. Las severidades menores esperan su ventana, porque ahi la rapidez
        no compra nada.

        Un SINTOMA no se notifica nunca por su cuenta: su causa ya lo hizo, y
        avisar de los dos es el ruido que la correlacion existe para evitar.
        """
        if self.is_symptom:
            return False
        return self.notified_at is None and self.severity is Severity.PAGE

    @property
    def age_seconds(self) -> float:
        return (_now() - self.opened_at).total_seconds()
