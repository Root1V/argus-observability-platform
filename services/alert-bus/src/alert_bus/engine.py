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
from .sinks import InlineDispatcher, Sink

log = logging.getLogger("alert_bus.engine")


def _now() -> datetime:
    return datetime.now(UTC)


# Lo unico que se acepta como juicio sobre la causa. Cerrado: lo escribe una
# persona desde un boton y acaba en la memoria del agente (B-46).
CAUSAS = ("correcta", "incorrecta")


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
        correlation_window_s: int = 300,
        dispatcher: object | None = None,
        store: object | None = None,
        escalar_tras_s: int = 0,
        escalados_max: int = 2,
    ) -> None:
        self._registry = registry
        self._sinks = list(sinks)
        # Sin despachador explicito se envia en el acto. El de hilos se inyecta
        # en produccion para que un webhook lento no bloquee la deteccion.
        self._dispatcher = dispatcher or InlineDispatcher()
        self._group_window = timedelta(seconds=group_window_s)
        self._resolve_after = timedelta(seconds=resolve_after_s)
        self._max = max_incidents
        # Solo se suprime contra causas RECIENTES. Un incidente aguas arriba
        # abierto desde hace horas no puede seguir explicando lo que pasa
        # ahora: a partir de cierto punto, "hay algo roto arriba" deja de ser
        # una explicacion y pasa a ser una excusa para no avisar.
        self._correlation_window = timedelta(seconds=correlation_window_s)
        # B-47: volver a avisar de un `page` que nadie ha reconocido. 0 lo
        # apaga, y es el valor por defecto a proposito: con avisos que se
        # reabren solos (B-21), escalar duplicaria justo el ruido que mas
        # cansa. Un aviso nuevo, no una edicion: Telegram no suena al editar.
        self._escalar_tras = timedelta(seconds=escalar_tras_s) if escalar_tras_s > 0 else None
        self._escalados_max = escalados_max

        self._incidents: dict[str, Incident] = {}   # huella -> incidente abierto
        self._order: deque[str] = deque()           # para acotar la memoria
        self._lock = threading.Lock()

        # Persistencia opcional. Sin ella el motor funciona igual que siempre:
        # detecta, correlaciona y notifica, y olvida al reiniciarse.
        #
        # NO se escribe al absorber una senal repetida, que es el camino
        # caliente. Eso solo refresca `last_seen_at`, y perderlo no cuesta
        # nada: la siguiente senal lo vuelve a poner. Se escribe cuando cambia
        # algo que NO se puede reconstruir desde el flujo — abrir, notificar,
        # acusar recibo, enriquecer, resolver (D-103).
        self._store = store
        if store is not None:
            self._restaurar()

        self.stats = {
            "signals_in": 0,
            "incidents_opened": 0,
            "signals_deduplicated": 0,
            "notifications_sent": 0,
            "unregistered_apps": 0,
            "symptoms_suppressed": 0,
            "symptoms_promoted": 0,
            "acknowledged": 0,
            "escalations": 0,
            "feedback": 0,
        }

    # --- Persistencia --------------------------------------------------------

    def _persistir(self, incident: Incident) -> None:
        """Un fallo del almacen no puede tumbar la deteccion.

        Se prefiere un alert-bus que detecta y no recuerda a uno que no
        detecta, asi que esto nunca levanta hacia arriba.
        """
        if self._store is None:
            return
        try:
            self._store.guardar(incident)
        except Exception as exc:  # noqa: BLE001
            log.error("engine.persist_failed", extra={"incident": incident.id, "error": str(exc)})

    def _evento(self, incident: Incident, tipo: str, detalle: str = "") -> None:
        """Una entrada de la linea de tiempo (B-49), con el reloj de este proceso."""
        if self._store is None:
            return
        try:
            self._store.registrar_evento(incident, tipo, _now(), detalle)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            log.error("engine.event_failed", extra={"incident": incident.id, "tipo": tipo, "error": str(exc)})

    def _restaurar(self) -> None:
        """Recupera los incidentes que estaban abiertos al apagarse.

        Lo importante de recuperar no es que aparezcan en el tablero —de eso
        ya se encarga la reconciliacion del emisor (D-100)— sino que conserven
        su IDENTIDAD: su `id`, su `opened_at`, su `notified_at`, el acuse de
        recibo y el informe del agente. Sin eso, el primer ciclo tras un
        reinicio vuelve a notificar un problema que alguien ya estaba mirando.
        """
        try:
            recuperados = list(self._store.abiertos())  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            log.error("engine.restore_failed", extra={"error": str(exc)})
            return

        for incident in recuperados:
            self._incidents[incident.fingerprint] = incident
            self._order.append(incident.fingerprint)
        if recuperados:
            log.info("engine.restored", extra={"incidentes": len(recuperados)})

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

        # La senal se normaliza a la identidad RESUELTA antes de nada mas.
        # Durante un renombrado llegan los dos namespaces a la vez, y la huella
        # se construye con `app`: sin esto, el mismo fallo del mismo servicio
        # abre DOS incidentes —uno por nombre— y notifica dos veces. La
        # correlacion por topologia tampoco encontraria las dependencias, que
        # el registro declara con el nombre nuevo.
        if signal.app != app.id:
            signal.app = app.id

        severity = self._severity_for(signal, app.severity_ceiling)
        huella = signal.fingerprint()

        with self._lock:
            existing = self._incidents.get(huella)

            if existing is not None and existing.state is not IncidentState.RESOLVED:
                existing.absorb(signal)
                self.stats["signals_deduplicated"] += 1
                # Una tormenta de senales puede ELEVAR la severidad, pero nunca
                # bajarla: que el problema se repita no lo hace menos grave.
                #
                # Esta rama NO persiste: es el camino caliente y lo unico que
                # cambia —`last_seen_at` y el contador— se reconstruye con la
                # siguiente senal. Escribir aqui seria pagar un viaje a disco
                # por senal, que es justo lo que el motor evita.
                if severity.rank > existing.severity.rank:
                    existing.severity = severity
                return existing, False

            incident = Incident.from_signal(signal, severity, uuid.uuid4().hex[:12])
            incident.thread_key = f"argus-{huella}"

            # Correlacion por topologia, ANTES de decidir si se notifica.
            causa = self._find_upstream_cause(signal.app)
            if causa is not None:
                incident.suppressed_by = causa.id
                causa.symptoms.append(incident.id)
                causa.updated_at = _now()
                self.stats["symptoms_suppressed"] += 1

            self._incidents[huella] = incident
            self._order.append(huella)
            self.stats["incidents_opened"] += 1
            self._evict_if_needed()

        # Fuera del lock: escribir en disco no puede bloquear la ingesta.
        self._persistir(incident)
        self._evento(incident, "abierto", f"{incident.severity} · emisor {incident.opened_at.isoformat()}"
                     + (f" · sintoma de {incident.suppressed_by}" if incident.suppressed_by else ""))
        return incident, True

    def _find_upstream_cause(self, app_id: str) -> Incident | None:
        """Busca un incidente abierto en algo de lo que esta app depende.

        Se llama con el lock TOMADO.

        Devuelve la causa mas CERCANA en el grafo, no la mas antigua ni la mas
        grave: es la mas accionable. Si Postgres esta caido, el aviso util es el
        de Postgres, no el del disco que lo aloja.
        """
        upstream = self._registry.upstream_of(app_id)
        if not upstream:
            return None

        ahora = _now()
        por_app: dict[str, Incident] = {}
        for incidente in self._incidents.values():
            if incidente.state is IncidentState.RESOLVED or incidente.is_symptom:
                continue
            if ahora - incidente.opened_at > self._correlation_window:
                continue
            # Si hay varios abiertos en la misma app aguas arriba: el mas
            # grave, y en empate el mas RECIENTE.
            #
            # La recencia importa porque la correlacion es temporal: de dos
            # incidentes igual de graves en Postgres, el que empezo hace
            # treinta segundos explica mejor lo que esta pasando ahora que uno
            # que lleva abierto diez minutos.
            previo = por_app.get(incidente.app)
            if previo is None or incidente.severity.rank > previo.severity.rank or (incidente.severity.rank == previo.severity.rank and incidente.opened_at > previo.opened_at):
                por_app[incidente.app] = incidente

        # `upstream` viene ordenado de mas cercano a mas lejano.
        for dependencia in upstream:
            if (candidato := por_app.get(dependencia)) is not None:
                return candidato
        return None

    def _severity_for(self, signal: Signal, ceiling: Severity) -> Severity:
        """Severidad base por tipo de senal, acotada por la criticidad de la app.

        Una aplicacion de criticidad baja no despierta a nadie por mucho que
        falle: es la valvula que evita que un laboratorio genere guardias.
        """
        # Si quien emite pidio una severidad, manda: conoce su urgencia mejor
        # que una tabla por tipo. La criticidad sigue acotando despues.
        if signal.severity is not None:
            return signal.severity if signal.severity.rank <= ceiling.rank else ceiling

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

    def _notify(self, incident: Incident, *, update: bool, escalado: bool = False) -> None:
        canales = set(self._registry.channels_for(incident.app, incident.severity))
        destinos = [s for s in self._sinks if s.name in canales]

        if not destinos:
            # Un incidente cuyo canal no existe no puede desaparecer en
            # silencio: acabaria con la plataforma creyendo que avisa cuando no.
            log.warning(
                "notify.no_sink",
                extra={"incident": incident.id, "app": incident.app, "channels": sorted(canales)},
            )

        # Configurar un canal son DOS cosas: cargar el sink (.env) y enrutar
        # hacia el (registro). Si el registro pide un canal que no esta cargado,
        # el aviso sale igual por los demas y nadie se entera de que falto uno
        # —el modo de fallo mas caro, porque parece exito—. Asi que se dice.
        ausentes = canales - {s.name for s in self._sinks}
        if ausentes:
            log.warning(
                "notify.channel_not_loaded",
                extra={
                    "incident": incident.id,
                    "app": incident.app,
                    "channels": sorted(ausentes),
                },
            )

        # El envio sale del camino de ingesta: un canal lento no puede retrasar
        # la deteccion del siguiente incidente (D-029).
        self._dispatcher.submit(destinos, incident, update=update)

        if not update and not escalado:
            incident.notified_at = _now()
            self.stats["notifications_sent"] += 1
            self._evento(incident, "notificado", ",".join(sorted(s.name for s in destinos)))
            # `notified_at` es lo que impide volver a avisar de lo mismo. Si
            # no se persiste, un reinicio lo pone a `None` y el primer ciclo
            # de reconciliacion vuelve a notificar un problema que ya se
            # notifico — que es exactamente el ruido que D-100 dejo abierto.
            self._persistir(incident)

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
        # El informe del agente cuesta tokens y minutos; perderlo en un
        # reinicio significa volver a investigar lo mismo.
        self._persistir(incident)
        self._evento(incident, "diagnosticado",
                     f"{'con causa' if incident.root_cause else 'sin causa'} · confianza {incident.confidence or '—'}")

        if incident.notified_at is not None:
            self._notify(incident, update=True)
        return incident

    # --- Mantenimiento -------------------------------------------------------

    def sweep(self) -> int:
        """Cierra los incidentes inactivos y promueve sintomas huerfanos.

        Sin el cierre, un incidente resuelto seguiria absorbiendo senales meses
        despues y el mismo problema dos semanas mas tarde no generaria aviso.

        Y sin la promocion, un sintoma cuya causa se resolvio pero que SIGUE
        fallando se quedaria callado para siempre. Ese es el fallo peligroso de
        la correlacion: convertir "esto tiene explicacion" en "esto no hace
        falta mirarlo".
        """
        ahora = _now()
        cerrados = 0
        vivos: set[str] = set()
        # Un incidente resuelto se borra de memoria pero SE GUARDA. Sin ese
        # rastro no se puede responder "¿ha abierto alguna vez un incidente el
        # silencio de un servicio?", que es el criterio 4 del piloto: su exito
        # es un evento pasado, y lo deseable es que ahora mismo no ocurra.
        resueltos: list[Incident] = []

        with self._lock:
            for huella, incident in list(self._incidents.items()):
                if incident.state is IncidentState.RESOLVED:
                    continue
                if ahora - incident.last_seen_at >= self._resolve_after:
                    incident.state = IncidentState.RESOLVED
                    incident.resolved_at = ahora
                    cerrados += 1
                    resueltos.append(incident)
                    del self._incidents[huella]
                else:
                    vivos.add(incident.id)

            # Sintomas cuya causa ya no esta abierta: dejan de estar suprimidos.
            huerfanos = [
                i for i in self._incidents.values()
                if i.is_symptom and i.suppressed_by not in vivos
            ]
            for sintoma in huerfanos:
                sintoma.suppressed_by = None
                sintoma.updated_at = ahora
                self.stats["symptoms_promoted"] += 1

        for incident in resueltos:
            self._persistir(incident)
            self._evento(incident, "resuelto", "sin senales")

        # Los que ahora merecen aviso, lo reciben. Fuera del lock: notificar
        # puede tardar y no queremos bloquear la ingesta mientras tanto.
        for sintoma in huerfanos:
            if sintoma.needs_notification:
                self._notify(sintoma, update=False)

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

    def acknowledge(self, incident_id: str, *, por: str = "api") -> Incident | None:
        encontrado: Incident | None = None
        primera_vez = False
        with self._lock:
            for incident in self._incidents.values():
                if incident.id == incident_id:
                    incident.state = IncidentState.ACKNOWLEDGED
                    incident.updated_at = _now()
                    # La PRIMERA vez es la que mide el MTTA; pulsar otra vez
                    # «lo veo» no puede mover la hora.
                    if incident.acknowledged_at is None:
                        incident.acknowledged_at = incident.updated_at
                        primera_vez = True
                    encontrado = incident
                    break
        # El acuse de recibo es de lo que MAS importa conservar: significa que
        # una persona ya lo esta mirando, y volver a avisarle tras un reinicio
        # es la forma mas rapida de que deje de mirar.
        if encontrado is not None:
            self._persistir(encontrado)
            if primera_vez:
                self.stats["acknowledged"] += 1
                self._evento(encontrado, "reconocido", por)
        return encontrado

    def feedback(self, incident_id: str, *, por: str = "api", causa: str | None = None,
                 causa_real: str | None = None, util: bool | None = None) -> Incident | None:
        """Lo que dice la persona del incidente (B-46). Vale tambien para uno ya resuelto.

        El feedback llega a menudo despues del cierre —se mira el aviso con
        calma al dia siguiente—, asi que si no esta en memoria se busca en el
        almacen.
        """
        if causa is not None and causa not in CAUSAS:
            raise ValueError(f"causa debe ser una de {CAUSAS}")
        nuevo: dict[str, object] = {}
        if causa is not None:
            nuevo["causa"] = causa
        if causa_real:
            nuevo["causa_real"] = " ".join(causa_real.split())[:500]
            nuevo["causa"] = "incorrecta"
        if util is not None:
            nuevo["util"] = bool(util)
        if not nuevo:
            return None

        with self._lock:
            incident = next((i for i in self._incidents.values() if i.id == incident_id), None)
            if incident is not None:
                incident.feedback = {**incident.feedback, **nuevo, "por": por, "cuando": _now().isoformat()}
                incident.updated_at = _now()
        if incident is None and self._store is not None:
            incident = self._store.obtener(incident_id)  # type: ignore[union-attr]
            if incident is not None:
                incident.feedback = {**incident.feedback, **nuevo, "por": por, "cuando": _now().isoformat()}
        if incident is None:
            return None
        self._persistir(incident)
        self.stats["feedback"] += 1
        self._evento(incident, "feedback", ", ".join(f"{k}={v}" for k, v in nuevo.items()) + f" · {por}")
        return incident

    def escalate(self) -> list[Incident]:
        """Vuelve a avisar de los `page` que nadie ha reconocido (B-47). Apagado si `escalar_tras_s` es 0."""
        if self._escalar_tras is None:
            return []
        ahora = _now()
        debidos: list[Incident] = []
        with self._lock:
            for incident in self._incidents.values():
                if (incident.severity is not Severity.PAGE or incident.state is not IncidentState.OPEN
                        or incident.is_symptom or incident.notified_at is None
                        or incident.escalations >= self._escalados_max):
                    continue
                # Cada escalado espera su propio plazo desde el anterior.
                if ahora - incident.notified_at >= self._escalar_tras * (incident.escalations + 1):
                    incident.escalations += 1
                    incident.updated_at = ahora
                    debidos.append(incident)
        for incident in debidos:
            self.stats["escalations"] += 1
            self._persistir(incident)
            minutos = int((ahora - incident.notified_at).total_seconds() // 60)  # type: ignore[operator]
            self._evento(incident, "escalado", f"aviso {incident.escalations} · {minutos} min sin acuse")
            self._notify(incident, update=False, escalado=True)
        return debidos

    def timeline(self, incident_id: str) -> list[dict]:
        if self._store is None:
            return []
        return self._store.linea_de_tiempo(incident_id)  # type: ignore[union-attr]
