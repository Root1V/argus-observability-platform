"""El juicio: de un expediente a una causa, una confianza y una evidencia.

Esto es lo que L3 entrega. Y lo primero que entrega es un juez **sin modelo**:
reglas deterministas sobre el expediente.

**Por que las reglas primero, y no como relleno hasta que haya una clave de
API.** F3-08 pide un banco de incidentes con causa conocida porque es «la unica
forma honesta de medir». Medir contra que, es la pregunta que falta: un modelo
que acierta el 70% de los diagnosticos no dice nada si unas reglas de cien
lineas aciertan el 65%. Las reglas son el CONTROL del experimento. Sin control
no hay medida, hay una cifra.

Y hay una segunda razon, menos elegante y mas decisiva: hoy **561 de 561
notificaciones** le han dicho a una persona «investigando…» y **0 de 571**
incidentes han llegado a tener causa. Unas reglas que digan «el 502 que te
avisa lleva dentro un 400 contra /oauth2/token, y quien atendio esa llamada la
dio por buena» ya es infinitamente mas que eso, y no necesita ninguna clave.

**Lo que las reglas NO hacen es inventar.** Cuando ninguna dispara, el
veredicto dice que no hay causa y lleva confianza `baja`; no se redacta una
descripcion del sintoma y se la llama causa. Es la unica regla que no se puede
relajar: un diagnostico equivocado con aire de certeza es peor que
«investigando…», porque manda a alguien a mirar donde no es.

El juez con modelo va detras de esta misma interfaz y recibe el mismo
expediente. Asi se pueden pasar los dos sobre el mismo banco y restar.
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field

from .expediente import Expediente

Confianza = Literal["alta", "media", "baja"]

# Lo que se dice cuando ninguna regla dispara. NO se escribe en `root_cause`:
# el campo se deja a `None` a proposito, porque `render.titular` lee ese campo
# para decidir si anuncia «causa identificada». Rellenarlo con una descripcion
# del sintoma haria que el aviso anunciara una causa que nadie tiene — el
# unico fallo de este servicio que seria peor que no existir.
SIN_CAUSA = "sin causa identificada; el expediente va como contexto"


class Veredicto(BaseModel):
    """Lo que se le manda al alert-bus para que edite el aviso.

    Los nombres de los tres primeros campos son los del esquema `Incident`
    —`root_cause`, `confidence`, `evidence`— a proposito: este objeto se
    convierte en el cuerpo del `POST /incidents/{huella}/enrich` sin
    traduccion. Una traduccion en medio es un sitio donde el nombre se puede
    cambiar en un lado y no en el otro.
    """

    root_cause: str | None = None
    confidence: Confianza = "baja"
    evidence: list[str] = Field(default_factory=list)

    # Quien lo dijo. No es decorativo: cuando se compare el juez de reglas con
    # el del modelo sobre el banco de F3-08, esto es la columna por la que se
    # agrupa. Y en produccion, es lo que permite responder «¿esta causa la
    # escribio una regla o un modelo?» sin mirar la hora del despliegue.
    origen: str = "reglas"

    @property
    def tiene_causa(self) -> bool:
        return bool(self.root_cause)

    def como_enriquecimiento(self) -> dict[str, object]:
        """El cuerpo del POST. `None` fuera: `enrich` ignora los nulos."""
        cuerpo: dict[str, object] = {
            "confidence": self.confidence,
            "evidence": self.evidence,
        }
        if self.root_cause:
            cuerpo["root_cause"] = self.root_cause
        return cuerpo


class Juez(Protocol):
    """Un expediente entra, un veredicto sale. El modelo tambien cumple esto."""

    nombre: str

    def juzgar(self, expediente: Expediente) -> Veredicto: ...


class JuezDeReglas:
    """El control del experimento: deterministico, gratis y sin red.

    Las reglas estan ORDENADAS por fuerza de la inferencia, no por lo bonito
    que queda el texto. La primera que dispara gana, y las demas aportan
    evidencia. Ese orden es la parte revisable de este modulo: cambiarlo cambia
    los diagnosticos, asi que esta escrito como una lista y no como una cadena
    de `if` anidados.
    """

    nombre = "reglas"

    def juzgar(self, exp: Expediente) -> Veredicto:
        evidencia = _evidencia(exp)

        for regla in (
            _nunca_ha_emitido,
            _calla_toda_la_plataforma,
            _el_destino_tardo_mas_de_lo_que_se_espero,
            _la_contraparte_lo_dio_por_bueno,
            _el_destino_respondio_un_error,
            _la_llamada_no_obtuvo_respuesta,
            _varios_componentes_el_mismo_fallo,
            _empezo_a_mitad_de_un_proceso,
            _la_firma_nombra_el_sintoma,
            _hay_una_excepcion_con_nombre,
            _una_dependencia_falla_a_la_vez,
            _ya_paso_y_alguien_anoto_la_causa,
        ):
            if (resultado := regla(exp)) is not None:
                causa, confianza = resultado
                return Veredicto(
                    root_cause=causa,
                    confidence=confianza,
                    evidence=evidencia,
                    origen=self.nombre,
                )

        # Sin causa: `root_cause` se queda en `None` y el aviso sigue diciendo
        # «investigando…», que es la verdad. Lo que SI se manda es la
        # evidencia, y eso ya convierte el aviso en algo utilizable: cuantas
        # peticiones, desde cuando, que mas estaba roto a la vez.
        return Veredicto(
            root_cause=None,
            confidence="baja",
            evidence=evidencia,
            origen=self.nombre,
        )


# Clases de fallo que significan «no hubo respuesta». Son las unicas con las
# que «nadie la atendio» es una conclusion coherente: un codigo HTTP es una
# RESPUESTA, y una respuesta prueba que alguien contesto.
SIN_RESPUESTA = ("timeout", "connect", "protocol", "ssl", "closed", "refused", "reset", "eof")


def _no_hubo_respuesta(clase: str | None, excepcion: str | None) -> bool:
    texto = f"{clase or ''} {excepcion or ''}".lower()
    return any(marca in texto for marca in SIN_RESPUESTA)


# --- Las reglas, de mas fuerte a mas debil -----------------------------------
#
# Cada una devuelve `(causa, confianza)` o `None`. Son funciones de modulo y no
# metodos para que una prueba pueda ejercer UNA sola: la pregunta «¿dispara la
# regla del puente con este expediente?» no deberia necesitar montar el juez.


def _nunca_ha_emitido(exp: Expediente) -> tuple[str, Confianza] | None:
    """Un silencio sobre un componente que no ha emitido NADA en 30 dias (B-39).

    Va primera y con confianza alta porque no es una inferencia: es un conteo
    sobre todas las tablas que la sonda de silencio puede ver. Lo que NO afirma
    es por que: parado, sin cablear o dado de alta antes de tiempo son las tres
    lecturas, y las tres se arreglan fuera del servicio.

    Sale del silencio de `tei`: 166 señales de `page` sobre un componente cuyas
    instancias estaban paradas y que no habia emitido un dato en todo el
    historico. El juez se abstenia, que con el expediente de entonces era lo
    correcto; con este bloque ya no hace falta.
    """
    e = exp.emision
    if not e.nunca:
        return None
    return (
        f"{exp.app}/{exp.componente} no dejo de emitir: no ha emitido NADA —ni trazas, ni logs, "
        f"ni metricas— en los {e.dias} dias anteriores. La sonda vigila un componente que nunca "
        "ha hablado. O esta parado, o no esta cableado, o se dio de alta en el registro antes de "
        "tiempo; las tres cosas se arreglan fuera del servicio.",
        "alta",
    )


def _calla_toda_la_plataforma(exp: Expediente) -> tuple[str, Confianza] | None:
    """Un silencio mientras la plataforma ENTERA solo recibe datos a rafagas.

    Entonces el silencio no es del servicio. Salio de un caso no visto que el
    juez FALLO: el 23/10 a las 10:50 el canario abrio silencio sobre tres
    servicios a la vez y el juez culpo al gateway por seis `PermissionError`
    —una dependencia declarada con errores en la ventana— mientras el portatil
    del plano central dormia y la plataforma llevaba 45 de los 60 minutos
    anteriores sin recibir un solo span de nadie.

    Confianza media: lo medido es que no es el servicio; entre el host y la
    ingesta no se puede elegir desde aqui.
    """
    pl = exp.plataforma
    if not pl.a_rafagas:
        return None
    return (
        f"No es {exp.app}/{exp.componente}: en los {pl.minutos_mirados} minutos anteriores la plataforma "
        f"ENTERA recibio datos solo en {pl.minutos_con_datos}, a rafagas. El silencio es del host del plano "
        "central o de la ingesta —un portatil que duerme y despierta unos minutos cada cuarto de hora hace "
        "exactamente esto—, no de este servicio. Si el host despierta, el incidente se cierra solo.",
        "media",
    )


def _el_destino_tardo_mas_de_lo_que_se_espero(exp: Expediente) -> tuple[str, Confianza] | None:
    """El destino contesto bien, pero DESPUES de que quien llama se rindiera.

    Es la unica regla que entrega una cadena causal **medida**: dos duraciones
    de dos spans distintos, y una mayor que la otra. No es correlacion y no es
    una heuristica sobre nombres.

    Es tambien la segunda correccion que salio del ensayo contra el stack real,
    y la mas vergonzosa. Un `ReadTimeout` del gateway contra
    `/v1/backends` salia diagnosticado por la regla de la discrepancia como
    «la causa esta en lo que el gateway esta ENVIANDO». Los datos decian otra
    cosa, y a la vista: `manager-api` atendio esa peticion, devolvio 200 y
    tardo **81 segundos**; el gateway se rindio a los 75. La peticion estaba
    perfecta. El destino es lento (D-131).

    La discrepancia de marcas estaba bien detectada; lo que estaba mal era la
    conclusion, porque «te lo dio por bueno» solo significa «pediste mal»
    cuando quien llama RECIBIO una respuesta y la clasifico como error. Si no
    recibio ninguna, el visto bueno del destino llego tarde — y llegar tarde es
    precisamente el fallo.
    """
    c = exp.contraparte
    eslabon = exp.arbol.eslabon_mas_profundo
    if c is None or not c.encontrada or c.ms is None or eslabon is None:
        return None
    if not _no_hubo_respuesta(eslabon.clase, eslabon.excepcion):
        return None
    if c.ms < eslabon.ms:
        return None
    hecho = (
        f"{c.servicio} atendio la llamada a {c.destino} y la contesto bien "
        f"(codigo {c.codigo or '?'}), pero tardo {c.ms / 1000:.0f} s, y "
        f"{exp.app}/{exp.componente} se rindio a los {eslabon.ms / 1000:.0f} s. "
    )
    # B-35: con la linea base, «tardo mucho» se separa en dos diagnosticos con
    # dos arreglos distintos. Sin ella, se dicen los dos y se deja elegir.
    lb = c.linea_base
    fraccion = lb.fraccion_sobre_plazo if lb is not None else None
    if lb is None or fraccion is None or lb.p50_ms is None:
        return (
            hecho + f"La peticion no tiene nada de malo: la causa es la latencia de {c.servicio}, "
            "o un plazo de espera mas corto que su tiempo de respuesta normal.",
            "alta",
        )
    normal = (
        f"p50 {lb.p50_ms / 1000:.1f} s, p95 {(lb.p95_ms or 0) / 1000:.1f} s "
        f"en ~{lb.estimadas} peticiones de {lb.dias} dias"
    )
    if fraccion >= PASA_DEL_PLAZO_HABITUALMENTE:
        return (
            hecho + f"Y no es una anomalia: {c.servicio} tarda esto normalmente en esa ruta —{normal}— "
            f"y el {fraccion * 100:.1f}% de sus respuestas llega despues de los {eslabon.ms / 1000:.0f} s "
            f"que espera {exp.componente}. La causa es la latencia habitual de {c.servicio} contra un "
            "plazo metido dentro de su tiempo de respuesta normal: seguira fallando mientras no cambie "
            "uno de los dos.",
            "alta",
        )
    return (
        hecho + f"Y fue una excepcion: normalmente {normal}, y solo el {fraccion * 100:.1f}% pasa del "
        f"plazo. La causa es la latencia de {c.servicio} en ese momento, no el plazo.",
        "alta",
    )


# Desde que fraccion de respuestas tardias un plazo deja de ser «casi nunca»
# para ser «parte de lo normal». Un 1 % de un servicio con trafico es un fallo
# cada pocos minutos.
PASA_DEL_PLAZO_HABITUALMENTE = 0.01


def _la_contraparte_lo_dio_por_bueno(exp: Expediente) -> tuple[str, Confianza] | None:
    """Quien atiende la llamada no la cuenta como error. La culpa es de quien llama.

    El caso tipico de un stack HTTP: un 4xx es un error para el cliente —pidio
    mal— y NO lo es para el servidor, que hizo su trabajo rechazandolo. Cuando
    las dos marcas discrepan asi, la causa esta del lado de quien llama, y eso
    es exactamente lo contrario de lo que sugiere el aviso.

    **Exige que quien llama haya RECIBIDO una respuesta.** Sin esa condicion la
    regla disparaba con un `ReadTimeout` y concluia que la peticion estaba mal
    cuando lo medido era que el destino tardo 81 s y el cliente espero 75: el
    visto bueno existia, solo que llego tarde (D-131).
    """
    c = exp.contraparte
    if c is None or not c.encontrada or not c.discrepa:
        return None
    eslabon = exp.arbol.eslabon_mas_profundo
    if eslabon is not None and _no_hubo_respuesta(eslabon.clase, eslabon.excepcion):
        return None
    return (
        f"La llamada {c.llamada} a {c.destino} falla, pero {c.servicio} la atendio "
        f"y la dio por buena (codigo {c.codigo or '?'}, span sin error). La causa esta "
        f"en lo que {exp.app}/{exp.componente} esta ENVIANDO, no en el destino.",
        "alta",
    )


def _el_destino_respondio_un_error(exp: Expediente) -> tuple[str, Confianza] | None:
    """La llamada saliente recibio un codigo HTTP de error.

    Esta regla existe porque la de abajo estaba mal, y se vio en el primer
    ensayo contra el stack real. Un incidente `404` del gateway salia
    diagnosticado como «el fallo ocurrio antes de llegar a la aplicacion
    destino», que para un 404 es casi lo contrario de la verdad: **un codigo de
    estado es una respuesta, y una respuesta prueba que alguien contesto.** Lo
    que falla con un 404 saliente es la peticion — una ruta que el destino no
    tiene— y eso apunta a quien llama (D-131).

    El puente no hace falta para concluirlo, y cuando esta vacio no cambia la
    conclusion: solo significa que no se pudo confirmar desde el otro lado.
    """
    c = exp.contraparte
    if c is None or not c.destino:
        return None
    clase = (exp.arbol.eslabon_mas_profundo.clase if exp.arbol.eslabon_mas_profundo else None) or ""
    if not clase.isdigit():
        return None

    confirmado = (
        f" {c.servicio} lo confirma desde su lado (codigo {c.codigo or '?'})."
        if c.encontrada
        else (
            " No se pudo confirmar desde el otro lado, y eso no dice nada: el muestreo de"
            " cola descarta ~9 de cada 10 trazas sin error, lentitud ni GenAI."
        )
    )
    return (
        f"La llamada saliente a {c.destino} recibio un `{clase}`. Un codigo de estado es "
        f"una RESPUESTA, asi que el destino contesto: lo que falla es la peticion que "
        f"{exp.app}/{exp.componente} esta haciendo —ruta inexistente, cuerpo invalido o "
        f"credencial rechazada—, no la disponibilidad del destino.{confirmado}",
        "media",
    )


def _la_llamada_no_obtuvo_respuesta(exp: Expediente) -> tuple[str, Confianza] | None:
    """La llamada saliente no llego a recibir respuesta, y nadie registro atenderla.

    Descarta medio arbol de hipotesis: conexion rechazada, proxy, TLS, o un
    destino que tarda mas que el plazo de quien llama.

    Dos condiciones, y la segunda es la que se me olvido al escribirla la
    primera vez: que la clase del fallo sea de las que significan «no hubo
    respuesta». Sin ella, la regla disparaba con un `404` y concluia que la
    peticion nunca llego (D-131).

    **La conclusion la sostiene la CLASE, no el puente vacio.** Es un cambio de
    peso que vino de medir el muestreo: las politicas de cola retienen trazas
    con error, lentas, de GenAI, y un 10% del resto. El span del destino
    atendiendo bien una llamada que al cliente le fallo **no es ninguna de las
    tres**, asi que se descarta con probabilidad ~0,9. Un puente vacio es, antes
    que nada, un dado perdido.

    Asi que el puente aporta «no pude confirmarlo» y nada mas, y la confianza
    media sale de que un `ReadTimeout` ES una ausencia de respuesta por
    definicion de la clase.
    """
    c = exp.contraparte
    if c is None or c.encontrada or not c.destino:
        return None
    eslabon = exp.arbol.eslabon_mas_profundo
    clase = eslabon.clase if eslabon else None
    if not _no_hubo_respuesta(clase, eslabon.excepcion if eslabon else None):
        return None

    tardanza = f" tras {eslabon.ms / 1000:.0f} s" if eslabon and eslabon.ms >= 1000 else ""
    return (
        f"La llamada {c.llamada} a {c.destino} termino sin respuesta{tardanza}. El fallo "
        "esta en el transporte o en el plazo, no en la logica del destino: conexion "
        "rechazada, proxy, TLS, o un destino que tarda mas de lo que quien llama espera. "
        "No se pudo ver el otro lado, y eso NO lo confirma: el muestreo de cola retiene "
        "trazas con error, lentas o de GenAI y un 10% del resto, asi que el destino "
        "atendiendo bien esta llamada se descarta con probabilidad ~0,9.",
        "media",
    )


# Clases que dicen «el esquema de la base no es el que el codigo espera». Si
# aparecen a mitad de la vida de un proceso, lo que cambio fue la base.
DE_ESQUEMA = ("notnullviolation", "undefinedcolumn", "undefinedtable", "integrityerror",
              "foreignkeyviolation", "uniqueviolation", "datatypemismatch", "programmingerror")

# Un proceso que lleva al menos esto vivo antes de su primer error no fallo por
# arrancar: fallo porque algo cambio debajo.
MINUTOS_SIN_FALLAR = 60


# Lo que suelen compartir varios procesos de una app, por la clase del error.
# Solo orienta la mirada: el registro casi nunca declara estos recursos
# —Centinela «usa su propio Postgres y Valkey»— y por eso no se pueden nombrar.
COMPARTIDO_POR_CLASE = (
    (("redis", "valkey"), "su Redis/Valkey"),
    (("psycopg", "asyncpg", "sqlalchemy", "operationalerror", "postgres"), "su base de datos"),
    (("kafka", "amqp", "pika", "celery"), "su cola de mensajes"),
)


def _varios_componentes_el_mismo_fallo(exp: Expediente) -> tuple[str, Confianza] | None:
    """Tres o mas componentes de la misma app abren la misma clase a la vez (B-40).

    Procesos distintos no se equivocan igual y en el mismo minuto por su
    cuenta: lo que falla es algo que comparten. Va antes que las reglas que
    leen la excepcion porque esas dicen lo mismo cinco veces —«Timeout reading
    from socket»— y ninguna de las cinco dice lo unico que importa, que es UNA
    causa.

    Con un hermano solo no: dos coincidencias son demasiado poco para dejar de
    mirar el componente. Confianza media: no se ve el recurso compartido, se
    deduce de que todos fallen igual.
    """
    if len(exp.hermanos) < 2:
        return None
    clase = _clase_de(exp.firma)
    componentes = [exp.componente, *(h.componente for h in exp.hermanos)]
    lejos = max(abs(h.segundos) for h in exp.hermanos)
    minuscula = clase.lower()
    compartido = next(
        (que for marcas, que in COMPARTIDO_POR_CLASE if any(m in minuscula for m in marcas)),
        "una dependencia, la configuracion o el host que comparten",
    )
    return (
        f"No es de {exp.componente}: {len(componentes)} componentes de {exp.app} "
        f"({', '.join(componentes)}) abrieron `{clase}` en {lejos:.0f} s. Procesos distintos "
        f"fallando igual y a la vez apuntan a algo que COMPARTEN —lo mas probable, {compartido}— "
        f"y no a ninguno de ellos: es un fallo visto {len(componentes)} veces.",
        "media",
    )


def _empezo_a_mitad_de_un_proceso(exp: Expediente) -> tuple[str, Confianza] | None:
    """El fallo empieza con el proceso ya en marcha, sin cambio de version (B-38).

    Entonces no fue un despliegue de este codigo: lo que cambio esta FUERA del
    proceso —el esquema de la base, la configuracion, una dependencia— y el
    proceso sigue con lo que cargo al arrancar.

    Sale del `NOT NULL` de Veritium del 14/10: una instancia viva desde las
    02:06 empieza a fallar a las 11:10, nueve horas despues, y su sustituta de
    las 11:32 no falla ninguna. La causa confirmada era una migracion aplicada
    con la API de la version anterior todavia corriendo.

    Dos guardas, y las dos evitan el mismo error — llamar «cambio» a algo que
    no cambio:

    - **No es cronico.** Un `ReadTimeout` que pasa todos los dias tambien
      «empieza» a mitad de cualquier proceso viejo; eso no es un cambio.
    - **Una sola version.** Si la version cambio en la ventana, eso ES un
      despliegue, y lo que hay que mirar es el despliegue.
    """
    c = exp.cambios
    m = c.minutos_en_marcha_antes_del_fallo
    if not c.consultada or m is None or m < MINUTOS_SIN_FALLAR:
        return None
    if len(c.versiones) > 1 or exp.historia.es_cronico or exp.historia.dias_con_errores > 1:
        return None

    horas = m / 60
    eslabon = exp.arbol.eslabon_mas_profundo
    clase = (eslabon.excepcion or eslabon.clase) if eslabon else exp.firma
    clase_corta = _ultimo_segmento(_clase_de(clase or ""))

    base = (
        f"El fallo (`{clase_corta}`) empieza con el proceso de {exp.componente} ya en marcha desde hace "
        f"al menos {horas:.0f} h y sin cambio de version ({', '.join(sorted(c.versiones)) or '?'}): no fue un "
        "despliegue de este codigo. Lo que cambio esta FUERA del proceso, y el proceso sigue con lo que "
        "cargo al arrancar."
    )
    if any(m_ in (clase or "").lower() for m_ in DE_ESQUEMA):
        return (
            base + " Siendo un error de esquema de la base, lo mas probable es una migracion aplicada "
            "con este proceso todavia corriendo el codigo anterior: reiniciarlo con el codigo nuevo "
            "deberia cortarlo.",
            "media",
        )
    return (base + " Mira que cambio en su configuracion o en sus dependencias a esa hora.", "media")


def _la_firma_nombra_el_sintoma(exp: Expediente) -> tuple[str, Confianza] | None:
    """El eslabon mas profundo tiene otra clase que la firma del aviso.

    Entonces el aviso esta nombrando lo de fuera —el 502 que ve el cliente— y
    la causa es la de dentro. Medido: solo 13 de 8.622 trazas con error de la
    ultima semana tienen mas de una clase, asi que esta regla dispara poco. Se
    queda porque cuando dispara, cambia el diagnostico entero.
    """
    eslabon = exp.arbol.eslabon_mas_profundo
    if eslabon is None or not eslabon.clase:
        return None
    clase_firma = _clase_de(exp.firma)
    if _ultimo_segmento(eslabon.clase) == _ultimo_segmento(clase_firma):
        return None
    return (
        f"El aviso dice `{clase_firma}`, que es lo que ve quien llama. Dentro de la "
        f"traza el fallo mas profundo es `{eslabon.clase}` en «{eslabon.nombre}»"
        + (f" ({eslabon.ruta})" if eslabon.ruta else "")
        + ". La causa es esa, no la firma.",
        "media",
    )


def _clase_de(firma: str) -> str:
    """La clase de una firma, aunque la firma sea un mensaje.

    Antes de D-118 la firma era el mensaje entero —`TypeError: GenAISpan.set()
    got an unexpected keyword…`— y esta regla la comparaba tal cual con la
    clase del span. Nunca coincidian, asi que la regla disparaba en TODOS los
    incidentes de esa epoca, repetia la firma entera en el veredicto y
    concluia que la causa era la clase del span. En el `PendingRollbackError`
    eso es decir que la causa es el sintoma (D-134).
    """
    return firma.split(":", 1)[0].strip() if ":" in firma else firma


def _ultimo_segmento(clase: str) -> str:
    """`sqlalchemy.exc.PendingRollbackError` y `PendingRollbackError` son la misma clase (B-33)."""
    return clase.rsplit(".", 1)[-1]


def _hay_una_excepcion_con_nombre(exp: Expediente) -> tuple[str, Confianza] | None:
    """Una excepcion con tipo y mensaje en el span que falla.

    Es la unica regla que usa texto libre, y solo del ejemplar: el mensaje
    EXPLICA bien y AGRUPA fatal —el `DETAIL:` de Postgres trae los UUID de la
    fila, y meterlo donde se agrupa convirtio un error en 335 firmas (D-118)—.
    Aqui no se agrupa nada, asi que entra.
    """
    for span in exp.arbol.cadena_del_fallo:
        if not (span.excepcion and span.mensaje):
            continue
        mensaje = _sin_volcado(span.mensaje)
        # SQLAlchemy envuelve: un `PendingRollbackError` lleva dentro la
        # excepcion que deshizo la sesion, y ESA es la causa. El banco lo
        # destapo con un mensaje en el que la original empezaba pasado el
        # caracter 280 — justo donde esta regla lo recortaba.
        if "Original exception was:" in mensaje:
            _, original = mensaje.split("Original exception was:", 1)
            return (
                f"`{_ultimo_segmento(span.excepcion)}` en «{span.nombre}» ({span.servicio}) es un "
                f"SINTOMA: la sesion se deshizo por una excepcion anterior. La causa es esa "
                f"excepcion anterior: {original.strip()[:300]}",
                "media",
            )
        return (
            f"`{span.excepcion}` en «{span.nombre}» ({span.servicio}): {mensaje[:300]}",
            "media",
        )
    return None


def _sin_volcado(mensaje: str) -> str:
    """El mensaje sin el volcado de la fila ni de la sentencia.

    Postgres añade `DETAIL: Failing row contains (…)` y SQLAlchemy `[SQL: …]
    [parameters: (…)]`, con los VALORES de la fila: UUID, y en el caso de
    Veritium un codigo de empleado. Esto acaba en `root_cause`, o sea en un
    mensaje de Telegram; lo que explica el fallo esta antes, y lo de despues no
    tiene por que salir del almacen.
    """
    for corte in ("\nDETAIL:", "DETAIL:  ", "[SQL:", "[parameters:", "(Background on this error"):
        if corte in mensaje:
            mensaje = mensaje.split(corte, 1)[0]
    return mensaje.strip()


def _una_dependencia_falla_a_la_vez(exp: Expediente) -> tuple[str, Confianza] | None:
    """Algo declarado aguas arriba tiene errores en la misma ventana.

    Confianza media: coincidir en el tiempo y estar declarado como dependencia
    es una pista fuerte, pero sigue siendo correlacion. El motor del alert-bus
    ya suprime por topologia cuando los dos tienen incidente abierto; esto coge
    el caso en que la dependencia falla sin llegar a abrir incidente propio.
    """
    # No para un silencio: un componente callado no esta fallando llamadas, y
    # culpar a una dependencia por sus errores en esa ventana es correlacionar
    # dos cosas que no se tocan. Asi fallo el primer caso no visto de silencio:
    # el gateway con seis errores, culpado de que veritium-api no emitiera.
    if exp.tipo_senal == "silence" or "silencio" in exp.firma:
        return None
    arriba = [c for c in exp.concurrentes if c.aguas_arriba and c.errores]
    if not arriba:
        return None
    peor = arriba[0]
    return (
        f"{peor.servicio}, del que {exp.app} depende, acumula {peor.errores} error(es) "
        f"de clase `{peor.clase or '?'}` en la misma ventana. Es la dependencia mas "
        "probable del fallo, aunque esto es correlacion temporal y no una cadena causal "
        "observada: la traza no cruza el servicio en este stack.",
        "media",
    )


def _ya_paso_y_alguien_anoto_la_causa(exp: Expediente) -> tuple[str, Confianza] | None:
    """Esta misma firma ya abrio incidente y entonces se anoto una causa.

    Es donde la memoria empieza a pagar, y con la unica forma que esta
    disponible hoy: firma EXACTA. El «top-N de parecidos» de F3-06 necesita
    antes que las huellas identifiquen clases y no ocurrencias (B-32).
    """
    if not exp.historia.causas_anteriores:
        return None
    return (
        f"Ya paso: esta firma ha abierto {exp.historia.veces} incidentes y en uno "
        f"anterior se anoto «{exp.historia.causas_anteriores[0]}». Confianza media "
        "porque la firma coincide, no el contexto.",
        "media",
    )


# --- Evidencia ---------------------------------------------------------------


def _evidencia(exp: Expediente) -> list[str]:
    """Los hechos que sostienen —o permiten dudar de— la causa.

    Va SIEMPRE, incluso sin causa. Es lo que convierte un «investigando…» en
    algo que una persona puede usar: cuantas peticiones, desde cuando, que mas
    estaba roto. Y es lo que permite no creerse la causa, que es el otro uso.
    """
    hechos: list[str] = []

    a = exp.alcance
    if a.totales:
        pct = f"{a.fraccion * 100:.1f}%" if a.fraccion is not None else "?"
        hechos.append(f"{a.fallidas} de {a.totales} peticiones fallaron en {a.ventana_min} min ({pct})")
        if a.es_total:
            hechos.append("fallaron TODAS las peticiones observadas: no es un caso raro")
        for ruta, n in sorted(a.rutas.items(), key=lambda kv: -kv[1])[:2]:
            hechos.append(f"{n} fallo(s) en {ruta}")

    # «¿Es nuevo?» lo contestan primero las TRAZAS. Esta linea decia «primera
    # vez que esta firma abre incidente» leyendo solo el almacen de
    # incidentes, que hasta D-133 guardaba una fila por huella y pisaba las
    # reaperturas. Se lo dijo a una persona sobre un `ReadTimeout` que tiene
    # trazas todos los dias desde hace un mes.
    h = exp.historia
    if h.es_cronico:
        hechos.append(
            f"CRONICO: errores de esta clase en {h.dias_con_errores} de los ultimos "
            f"{h.dias_mirados} dias ({h.errores_en_trazas} en total) — no empezo con este incidente"
        )
    elif h.dias_con_errores > 1:
        hechos.append(f"no es nuevo: errores de esta clase en {h.dias_con_errores} de los ultimos {h.dias_mirados} dias")
    elif h.veces > 1 and h.primera_vez:
        hechos.append(f"no es nuevo: {h.veces} incidentes con esta firma desde {h.primera_vez:%Y-%m-%d}")
    elif h.dias_mirados:
        cuantos = "ninguno" if not h.dias_con_errores else "solo 1"
        hechos.append(f"errores de esta clase en {cuantos} de los {h.dias_mirados} dias anteriores")
    else:
        # Sin trazas que mirar, solo queda el almacen de incidentes, y de el
        # no se puede deducir «primera vez»: solo que no hay registro.
        hechos.append("sin incidentes anteriores registrados con esta firma")

    e = exp.emision
    if e.consultada and not e.nunca and e.ultima:
        hechos.append(f"emitia: {e.total} datos en {e.dias} dias, el ultimo el {e.ultima:%Y-%m-%d %H:%M}Z")

    for c in exp.concurrentes[:2]:
        marca = " (dependencia declarada)" if c.aguas_arriba else ""
        hechos.append(f"a la vez: {c.servicio} con {c.errores} error(es) `{c.clase or '?'}`{marca}")

    if exp.hermanos:
        otros = ", ".join(h.componente for h in exp.hermanos[:5])
        hechos.append(f"a la vez en {exp.app}: {len(exp.hermanos)} componente(s) mas con la misma clase ({otros})")

    if exp.arbol.trace_id:
        hechos.append(f"traza de ejemplo: {exp.arbol.trace_id}")

    # Las lagunas son evidencia. Un diagnostico hecho sin el arbol no vale lo
    # mismo que uno hecho con el, y quien lee el aviso tiene derecho a saber
    # con que se conto.
    for laguna in exp.lagunas:
        hechos.append(f"NO DISPONIBLE — {laguna}")

    return hechos
