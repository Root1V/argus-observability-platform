"""El expediente: todo lo que se sabe de un incidente ANTES de opinar.

D-013 decidio que L3 no es un agente: es **una llamada con salida estructurada
sobre contexto pre-recolectado por consultas fijas**. Este modulo es la parte
«contexto pre-recolectado», y vive aparte de la llamada a proposito — porque es
la parte que se puede comprobar. Un expediente es un hecho; un juicio es una
opinion sobre un expediente.

**Que lleva dentro, y por que esos seis bloques y no otros.** Son las preguntas
que una persona hace en ese orden al mirar un aviso:

1. `arbol` — el ejemplar: el arbol de spans de UNA traza del incidente. Es
   donde se ve que el `502` que avisa lleva dentro un `400` contra otro sitio.
2. `historia` — ¿es nuevo? Un fallo que lleva tres semanas pasando no se
   diagnostica igual que uno que empezo hace ocho minutos.
3. `alcance` — ¿es todo o una parte? La diferencia entre «el servicio esta
   caido» y «una ruta devuelve 500» es esta cifra y ninguna otra.
4. `concurrentes` — ¿que mas se rompio en la misma ventana? Correlacion
   TEMPORAL, que es distinta de la del registro: coge lo que no esta declarado
   como dependencia.
5. `topologia` — de que depende y quien depende de el, segun el registro.
6. `identidad` — el incidente tal y como lo cuenta el alert-bus.

**Lo que NO lleva.** Nada sin acotar. Cada bloque tiene su tope: 40 spans, 5
incidentes de historia, 8 servicios concurrentes. Un expediente que crece con
el tamano del problema es un expediente que deja de caber justo cuando el
problema es grande.

Y no lleva texto libre del mensaje de error mas alla del ejemplar: el `DETAIL:`
de Postgres trae los UUID de la fila, y meterlo en algo que se agrupa fue lo
que convirtio un error en 335 firmas (D-118). Aqui el mensaje se usa para
EXPLICAR, no para agrupar, asi que entra — pero solo del ejemplar, y acotado.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

# Topes. Son del modulo y no de cada llamada porque el tope es una decision de
# diseno —que quepa— y no una preferencia de quien llama.
MAX_SPANS = 40
MAX_HISTORIA = 5
MAX_CONCURRENTES = 8
MAX_MENSAJE = 400


class Span(BaseModel):
    """Un span del ejemplar, con lo justo para leer el arbol."""

    span_id: str
    parent_span_id: str = ""
    nombre: str
    tipo: str = ""           # SpanKind: Server, Client, Internal...
    servicio: str = ""
    ms: float = 0.0
    error: bool = False
    clase: str | None = None         # `error.type`, si lo hay
    mensaje: str | None = None       # `exception.message` o `status.message`
    excepcion: str | None = None     # `exception.type` del evento
    ruta: str | None = None          # `http.route` o `url.full` del destino
    momento: datetime | None = None  # cuando empezo; lo necesita el puente
    profundidad: int = 0

    @property
    def es_raiz(self) -> bool:
        return self.profundidad == 0


class Arbol(BaseModel):
    """El arbol de spans de una traza, ya ordenado y con profundidades.

    Ordenado por recorrido en profundidad, que es como se lee: un hijo va
    inmediatamente debajo de su padre y sangrado.
    """

    trace_id: str = ""
    spans: list[Span] = Field(default_factory=list)
    truncado: bool = False

    @property
    def cadena_del_fallo(self) -> list[Span]:
        """Los spans con error, de menos a mas profundo. La espina causal.

        Es lo unico del arbol que se mira primero: si el ultimo eslabon tiene
        una clase distinta del primero, el aviso esta nombrando el sintoma y la
        causa esta un nivel mas abajo.

        Medido antes de escribir esto: de 8.622 trazas con error de los ultimos
        siete dias, **8.609 tienen una sola clase de error** y solo 13 tienen
        dos o mas. Asi que esta cadena resuelve el 0,15% de los casos, no la
        mayoria — y eso es justo lo que hay que saber antes de construir el
        resto del expediente creyendo que con el arbol basta.
        """
        return [s for s in self.spans if s.error]

    @property
    def eslabon_mas_profundo(self) -> Span | None:
        """El fallo mas interior, desempatando por tipo de span.

        El desempate no es cosmetico. El middleware ASGI emite spans auxiliares
        —`POST /ruta http send`, `http receive`— que COPIAN el estado de la
        respuesta, asi que un 502 del servidor aparece tambien como un 502 en
        un span `Internal` a la misma profundidad. Sin desempate, `max` elige
        por orden de llegada y el eslabon mas profundo sale siendo la copia
        contable del mismo fallo en vez de la llamada saliente que lo causo.

        Orden: mas profundo primero; a igual profundidad, `Client` —una llamada
        que SALE del servicio, que es informacion nueva— antes que `Internal` o
        `Server`; y a igual tipo, el que mas tardo.
        """
        cadena = self.cadena_del_fallo
        if not cadena:
            return None
        return max(
            cadena,
            key=lambda s: (s.profundidad, 1 if s.tipo == "Client" else 0, s.ms),
        )

    def texto(self) -> str:
        """El arbol sangrado, una linea por span."""
        lineas = []
        for s in self.spans:
            marca = "✗" if s.error else " "
            detalle = []
            if s.clase:
                detalle.append(s.clase)
            if s.excepcion and s.excepcion != s.clase:
                detalle.append(s.excepcion)
            if s.ruta:
                detalle.append(s.ruta)
            cola = f"  [{' · '.join(detalle)}]" if detalle else ""
            lineas.append(
                f"{marca} {'  ' * s.profundidad}{s.nombre} ({s.tipo or '?'}, {s.servicio}) {s.ms:.0f}ms{cola}"
            )
            if s.error and s.mensaje:
                lineas.append(f"    {'  ' * s.profundidad}↳ {s.mensaje[:MAX_MENSAJE]}")
        if self.truncado:
            lineas.append(f"… (arbol recortado a {MAX_SPANS} spans)")
        return "\n".join(lineas)


class Historia(BaseModel):
    """Si esta firma se habia visto antes, y cuanto.

    `veces` cuenta INCIDENTES, no senales: la pregunta es cuantas veces esto
    abrio un aviso, que es lo que dice si es cronico o nuevo.
    """

    veces: int = 0
    primera_vez: datetime | None = None
    ultima_vez: datetime | None = None
    # Causas que alguien —o L3— escribio en ocurrencias anteriores de la misma
    # firma. Es la semilla de la memoria de F3-06, con una diferencia que
    # importa: aqui se busca por firma EXACTA, no por parecido. B-32 dice por
    # que lo de «parecido» no se puede hacer todavia: 571 incidentes tienen 571
    # huellas distintas, asi que un top-N de similares devolveria 468 copias
    # del mismo error como 468 precedentes.
    causas_anteriores: list[str] = Field(default_factory=list)

    # Lo mismo contado desde las TRAZAS, que no se pisan. Es la fuente que
    # manda para «¿es nuevo?»: el almacen de incidentes guardo hasta D-133 una
    # sola fila por huella, asi que su `veces` esta por debajo de la realidad
    # para todo lo anterior al 25/10. `dias_mirados == 0` significa que no se
    # consulto, que es distinto de cero dias con errores.
    dias_con_errores: int = 0
    dias_mirados: int = 0
    errores_en_trazas: int = 0
    primer_dia: datetime | None = None

    @property
    def es_cronico(self) -> bool:
        """Errores en al menos un tercio de los dias mirados, y mas de dos."""
        return self.dias_mirados > 0 and self.dias_con_errores > 2 and (
            self.dias_con_errores * 3 >= self.dias_mirados
        )

    @property
    def es_nuevo(self) -> bool:
        return self.veces <= 1 and self.dias_con_errores <= 1


class Alcance(BaseModel):
    """Cuanta parte del trafico esta afectada, en la ventana del incidente.

    Las dos cifras juntas son la que importa: 12 de 12 es una caida, 12 de
    4.000 es un caso raro, y el aviso es identico en los dos casos.
    """

    fallidas: int = 0
    totales: int = 0
    ventana_min: int = 0
    rutas: dict[str, int] = Field(default_factory=dict)   # ruta -> fallidas

    @property
    def fraccion(self) -> float | None:
        return (self.fallidas / self.totales) if self.totales else None

    @property
    def es_total(self) -> bool:
        """Todas las peticiones observadas fallaron."""
        return self.totales > 0 and self.fallidas >= self.totales


class Contraparte(BaseModel):
    """El otro lado de la llamada saliente que fallo.

    Existe porque **en este stack la traza no cruza el servicio**: 3 trazas de
    132.000 tienen spans de dos servicios, y los 2.369 spans `Server` de
    `auth-service` son todos raiz. Asi que el arbol del ejemplar se corta justo
    donde empieza lo interesante, y el salto hay que darlo a mano: mismo nombre
    de span, lado servidor, ventana de segundos.

    `encontrada=False` es **debil**, y eso costo medirlo. Parece decir «nadie
    atendio la llamada» y casi siempre dice «el muestreo se llevo la traza del
    destino»: las politicas de cola retienen lo que tiene error, lo lento, lo
    de GenAI y un 10% del resto, y un destino atendiendo BIEN una llamada que
    al cliente le fallo no es ninguna de las tres. Asi que se descarta con
    probabilidad ~0,9.

    Por eso ninguna regla concluye a partir del puente vacio: lo que concluye
    es la CLASE del fallo, y el puente solo aporta confirmacion cuando aparece.
    """

    llamada: str = ""                # el span saliente que fallo
    destino: str | None = None       # `server.address:port` o la URL
    encontrada: bool = False
    servicio: str | None = None      # quien la atendio, si alguien lo hizo
    codigo: str | None = None        # su propio codigo de respuesta
    estado: str | None = None        # su propio StatusCode de span
    ms: float | None = None
    # Discrepancia: el que llama lo cuenta como error y el que atiende no.
    # Es un hallazgo en si mismo y casi siempre el mismo: un 4xx es error para
    # el cliente —pidio mal— y NO lo es para el servidor, que hizo su trabajo
    # rechazandolo. Cuando aparece, la causa esta en quien llama.
    discrepa: bool = False


class Emision(BaseModel):
    """Lo que el componente emitio en los 30 dias anteriores al incidente.

    Solo se recolecta para los silencios: es la diferencia entre «dejo de
    hablar» y «nunca hablo» (B-39). `consultada=False` no es «no emitio»: es
    que no se pregunto.
    """

    consultada: bool = False
    dias: int = 30
    por_fuente: dict[str, int] = Field(default_factory=dict)
    primera: datetime | None = None
    ultima: datetime | None = None

    @property
    def total(self) -> int:
        return sum(self.por_fuente.values())

    @property
    def nunca(self) -> bool:
        return self.consultada and self.total == 0


class Instancia(BaseModel):
    instancia: str
    version: str = ""
    desde: datetime | None = None
    hasta: datetime | None = None
    errores: int = 0
    primer_error: datetime | None = None


class Cambios(BaseModel):
    """Los procesos del componente en las 24 h anteriores (B-38).

    Lo que el almacen no tiene es un registro de despliegues; lo que si tiene
    es `service.instance.id`, que cambia en cada arranque. Con eso se ve si el
    fallo empezo con un proceso nuevo —un despliegue, un reinicio— o a mitad de
    la vida de uno viejo, y las dos cosas apuntan a sitios distintos.

    `consultada=False` es que no se pregunto; `sin_identidad=True` es que se
    pregunto y el componente no emite `service.instance.id`, que es distinto de
    «no hubo reinicios».
    """

    consultada: bool = False
    horas: int = 24
    instancias: list[Instancia] = Field(default_factory=list)
    sin_identidad: bool = False

    @property
    def actual(self) -> Instancia | None:
        return max(self.instancias, key=lambda i: i.hasta or datetime.min.replace(tzinfo=UTC), default=None)

    @property
    def versiones(self) -> set[str]:
        return {i.version for i in self.instancias if i.version}

    @property
    def reinicios(self) -> int:
        return max(0, len(self.instancias) - 1)

    @property
    def minutos_en_marcha_antes_del_fallo(self) -> float | None:
        """Cuanto llevaba vivo el proceso actual cuando dio su primer error."""
        a = self.actual
        if a is None or a.desde is None or a.primer_error is None:
            return None
        return (a.primer_error - a.desde).total_seconds() / 60



class Plataforma(BaseModel):
    """Si la plataforma ENTERA recibia datos antes de que se abriera el incidente.

    Solo para silencios, y medido hasta la apertura y no despues: lo que se ve
    despues no lo podia ver nadie en ese momento.
    """

    consultada: bool = False
    minutos_con_datos: int = 0
    minutos_mirados: int = 60

    @property
    def a_rafagas(self) -> bool:
        """Menos de la mitad de los minutos con datos: el regimen del host dormido."""
        return self.consultada and self.minutos_con_datos * 2 < self.minutos_mirados


class Concurrente(BaseModel):
    """Otro servicio con errores en la misma ventana."""

    servicio: str
    clase: str = ""
    errores: int = 0
    # Si el registro lo declara aguas arriba del que falla. Un concurrente
    # aguas arriba es candidato a causa; uno que no, es coincidencia hasta que
    # se demuestre otra cosa.
    aguas_arriba: bool = False


class Expediente(BaseModel):
    """El contexto completo de un incidente, acotado y sin opinion."""

    # --- Identidad -----------------------------------------------------------
    incidente_id: str
    huella: str
    app: str
    componente: str
    firma: str
    tipo_senal: str
    severidad: str
    titulo: str = ""
    senales: int = 1
    edad_s: float = 0.0
    entorno: str | None = None

    # --- Los cinco bloques de contexto ---------------------------------------
    arbol: Arbol = Field(default_factory=Arbol)
    contraparte: Contraparte | None = None
    emision: Emision = Field(default_factory=Emision)
    cambios: Cambios = Field(default_factory=Cambios)
    plataforma: Plataforma = Field(default_factory=Plataforma)
    historia: Historia = Field(default_factory=Historia)
    alcance: Alcance = Field(default_factory=Alcance)
    concurrentes: list[Concurrente] = Field(default_factory=list)
    aguas_arriba: list[str] = Field(default_factory=list)
    aguas_abajo: list[str] = Field(default_factory=list)

    # Lo que no se pudo recolectar, con el motivo. Un expediente al que le
    # falta un bloque NO es un expediente vacio, y quien juzgue tiene que poder
    # distinguir «no hay trazas» de «no pude consultar las trazas»: lo primero
    # es un hecho sobre el incidente y lo segundo un fallo mio. Es la misma
    # distincion que `persistencia` en `/stats` (D-103).
    lagunas: list[str] = Field(default_factory=list)

    def texto(self) -> str:
        """El expediente en texto, que es lo que se le pasa al modelo.

        Se redacta aqui y no en el prompt para que lo que lee el modelo sea
        EXACTAMENTE lo que lee una persona al depurar — si fueran dos
        redacciones, la que se revisa no seria la que se usa.
        """
        bloques: list[str] = []

        bloques.append(
            "INCIDENTE\n"
            f"  app/componente : {self.app}/{self.componente}\n"
            f"  firma          : {self.firma}  (tipo: {self.tipo_senal}, severidad: {self.severidad})\n"
            f"  titulo         : {self.titulo}\n"
            f"  senales        : {self.senales} en {self.edad_s:.0f}s"
            + (f"\n  entorno        : {self.entorno}" if self.entorno else "")
        )

        if self.arbol.spans:
            bloques.append(f"ARBOL DE UNA TRAZA DE EJEMPLO ({self.arbol.trace_id})\n{self.arbol.texto()}")

        if (c := self.contraparte) is not None:
            if not c.encontrada:
                bloques.append(
                    "EL OTRO LADO DE LA LLAMADA QUE FALLO\n"
                    f"  llamada  : {c.llamada}" + (f" → {c.destino}" if c.destino else "") + "\n"
                    "  NADIE registro haber atendido esa llamada en la misma ventana.\n"
                    "  La traza no cruza el servicio en este stack (propagacion en `never`),\n"
                    "  asi que esto se busco por RUTA y ventana de tiempo. No encontrarlo\n"
                    "  casi siempre es el MUESTREO: las politicas de cola retienen trazas\n"
                    "  con error, lentas o de GenAI y un 10% del resto, y el destino\n"
                    "  atendiendo bien no es ninguna de las tres. Tambien puede ser que\n"
                    "  nadie la atendiera, que el destino no este instrumentado, o que\n"
                    "  nombre esa ruta con otra plantilla."
                )
            else:
                lineas = [
                    f"  llamada  : {c.llamada}" + (f" → {c.destino}" if c.destino else ""),
                    f"  atendida : {c.servicio}, codigo {c.codigo or '?'}, estado de span {c.estado or '?'}"
                    + (f", {c.ms:.0f}ms" if c.ms is not None else ""),
                ]
                if c.discrepa:
                    lineas.append(
                        "  DISCREPAN : quien llama lo cuenta como error y quien atiende no."
                        " La causa esta del lado de quien llama."
                    )
                bloques.append("EL OTRO LADO DE LA LLAMADA QUE FALLO\n" + "\n".join(lineas))

        h = self.historia
        if h.dias_con_errores:
            bloques_h = [
                f"  en las trazas: errores de esta clase en {h.dias_con_errores} de los ultimos "
                f"{h.dias_mirados} dias ({h.errores_en_trazas} en total)"
                + (f", desde el {h.primer_dia:%Y-%m-%d}" if h.primer_dia else "")
            ]
            if h.es_cronico:
                bloques_h.append("  es CRONICO: no empezo con este incidente")
            bloques.append("RECURRENCIA\n" + "\n".join(bloques_h))
        if h.veces:
            linea = f"  esta firma ha abierto {h.veces} incidente(s)"
            if h.primera_vez:
                linea += f", el primero el {h.primera_vez:%Y-%m-%d %H:%M}Z"
            if h.ultima_vez and h.veces > 1:
                linea += f", el ultimo el {h.ultima_vez:%Y-%m-%d %H:%M}Z"
            partes = [linea]
            for causa in h.causas_anteriores[:MAX_HISTORIA]:
                partes.append(f"  causa anotada antes: {causa}")
            bloques.append("HISTORIA\n" + "\n".join(partes))

        a = self.alcance
        if a.totales:
            pct = f"{a.fraccion * 100:.1f}%" if a.fraccion is not None else "?"
            lineas = [f"  {a.fallidas} de {a.totales} peticiones fallaron en los ultimos {a.ventana_min} min ({pct})"]
            for ruta, n in sorted(a.rutas.items(), key=lambda kv: -kv[1])[:5]:
                lineas.append(f"  {n:>6}  {ruta}")
            bloques.append("ALCANCE\n" + "\n".join(lineas))

        c = self.cambios
        if c.consultada and (a := c.actual) is not None:
            lineas = [
                f"  {len(c.instancias)} proceso(s) en {c.horas} h · versiones: {', '.join(sorted(c.versiones)) or '?'}"
            ]
            if a.desde:
                # El primer span OBSERVADO, no el arranque: un proceso sin
                # trafico no emite. Es una cota inferior de su edad, y se dice asi.
                lineas.append(f"  el actual emite desde el {a.desde:%Y-%m-%d %H:%M}Z (al menos "
                              f"{((a.hasta or a.desde) - a.desde).total_seconds() / 3600:.1f} h vivo)")
            if (m := c.minutos_en_marcha_antes_del_fallo) is not None:
                lineas.append(f"  su primer error llego {m:.0f} min despues de arrancar")
            bloques.append("PROCESOS (reinicios y despliegues)\n" + "\n".join(lineas))
        elif c.sin_identidad:
            bloques.append("PROCESOS\n  el componente no emite `service.instance.id`: no se pueden ver reinicios")

        pl = self.plataforma
        if pl.consultada:
            linea = (f"  la plataforma ENTERA recibio datos en {pl.minutos_con_datos} de los "
                     f"{pl.minutos_mirados} minutos anteriores")
            if pl.a_rafagas:
                linea += "\n  a rafagas: el silencio no es de este servicio, es del host o de la ingesta"
            bloques.append("PLATAFORMA\n" + linea)

        e = self.emision
        if e.consultada:
            if e.nunca:
                bloques.append(
                    f"EMISION ANTERIOR\n  NADA en los {e.dias} dias anteriores: ni trazas, ni logs, ni metricas.\n"
                    "  No es que dejara de hablar: no consta que haya hablado nunca."
                )
            else:
                fuentes = ", ".join(f"{k} {v}" for k, v in e.por_fuente.items() if v)
                ultima = f"; ultimo dato el {e.ultima:%Y-%m-%d %H:%M}Z" if e.ultima else ""
                bloques.append(f"EMISION ANTERIOR\n  {e.total} datos en {e.dias} dias ({fuentes}){ultima}")

        if self.concurrentes:
            lineas = [
                f"  {c.servicio}: {c.errores} error(es) de clase {c.clase or '?'}"
                + ("  ← declarado aguas arriba" if c.aguas_arriba else "")
                for c in self.concurrentes[:MAX_CONCURRENTES]
            ]
            bloques.append("OTROS SERVICIOS CON ERRORES EN LA MISMA VENTANA\n" + "\n".join(lineas))

        if self.aguas_arriba or self.aguas_abajo:
            bloques.append(
                "TOPOLOGIA (segun el registro)\n"
                f"  depende de   : {', '.join(self.aguas_arriba) or '—'}\n"
                f"  le dependen  : {', '.join(self.aguas_abajo) or '—'}"
            )

        if self.lagunas:
            bloques.append("NO PUDE RECOLECTAR\n" + "\n".join(f"  {x}" for x in self.lagunas))

        return "\n\n".join(bloques)


def arbol_desde_filas(filas: list[dict[str, Any]], *, max_spans: int = MAX_SPANS) -> Arbol:
    """Monta el arbol a partir de filas de `otel_traces`.

    Funcion pura: la consulta vive en `almacen.py` y esto solo da forma. Asi la
    parte con la logica —quien es hijo de quien, que profundidad tiene, en que
    orden se lee— se prueba sin ClickHouse delante.

    **Los huerfanos cuentan como raices.** Con muestreo por cola no llega el
    arbol entero: un hijo puede estar retenido y su padre no. Tratarlo como
    raiz lo DEJA VER; descartarlo por no encontrar al padre esconderia justo el
    span que explica el fallo. Es el caso normal, no el raro.
    """
    spans: dict[str, Span] = {}
    orden_llegada: list[str] = []

    for fila in filas:
        atributos: dict[str, str] = dict(fila.get("SpanAttributes") or {})
        eventos_nombre = list(fila.get("Events.Name") or [])
        eventos_attr = list(fila.get("Events.Attributes") or [])

        excepcion = None
        mensaje = None
        for nombre, attr in zip(eventos_nombre, eventos_attr, strict=False):
            if nombre == "exception":
                attr = dict(attr or {})
                excepcion = attr.get("exception.type") or excepcion
                mensaje = attr.get("exception.message") or mensaje
                break
        if not mensaje:
            mensaje = fila.get("StatusMessage") or None

        span = Span(
            span_id=fila.get("SpanId", ""),
            parent_span_id=fila.get("ParentSpanId", "") or "",
            nombre=fila.get("SpanName", ""),
            tipo=fila.get("SpanKind", "") or "",
            servicio=fila.get("ServiceName", "") or "",
            ms=float(fila.get("Duration") or 0) / 1e6,
            error=(fila.get("StatusCode") == "Error"),
            clase=atributos.get("error.type") or None,
            mensaje=mensaje,
            excepcion=excepcion,
            # La ruta del destino cuando es una llamada saliente: es lo que
            # convierte «un cliente fallo» en «la llamada a /oauth2/token
            # fallo», que es la diferencia entre un dato y una pista.
            ruta=atributos.get("url.full") or atributos.get("http.url") or atributos.get("http.route") or None,
            momento=_como_fecha(fila.get("Timestamp")),
        )
        if span.span_id and span.span_id not in spans:
            spans[span.span_id] = span
            orden_llegada.append(span.span_id)

    hijos: dict[str, list[str]] = {}
    raices: list[str] = []
    for sid in orden_llegada:
        padre = spans[sid].parent_span_id
        if padre and padre in spans:
            hijos.setdefault(padre, []).append(sid)
        else:
            raices.append(sid)

    ordenados: list[Span] = []

    def _recorrer(sid: str, profundidad: int) -> None:
        if len(ordenados) >= max_spans:
            return
        span = spans[sid]
        span.profundidad = profundidad
        ordenados.append(span)
        for hijo in hijos.get(sid, []):
            _recorrer(hijo, profundidad + 1)

    for raiz in raices:
        _recorrer(raiz, 0)

    trace_id = str(filas[0].get("TraceId", "")) if filas else ""
    return Arbol(trace_id=trace_id, spans=ordenados, truncado=len(spans) > len(ordenados))


def _como_fecha(valor: Any) -> datetime | None:
    """La hora de un span tal y como la devuelve ClickHouse por HTTP.

    Llega como texto sin zona y la columna es UTC, asi que se le pone UTC
    explicitamente. Dejarla ingenua es como se cuelan los desfases de reloj que
    costaron D-129: dos marcas sin zona se comparan sin quejarse.
    """
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=UTC)
    texto = str(valor)
    # La columna es `DateTime64(9)` y llega con NUEVE decimales;
    # `%f` entiende seis. Sin recortar, `strptime` falla con los dos formatos y
    # el span se queda sin hora — y entonces el puente al otro lado de la
    # llamada no se puede buscar, en silencio y en todos los casos. Lo cazo el
    # fixture copiado del almacen: con uno escrito a mano habria puesto seis
    # decimales, porque seis es lo que sabe mi `%f`.
    if "." in texto:
        entero, fraccion = texto.split(".", 1)
        texto = f"{entero}.{fraccion[:6]}"
    for formato in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(texto, formato).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None
