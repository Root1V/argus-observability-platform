"""La recoleccion: las lagunas, y el empalme que no puede equivocarse.

El empalme por ventana de tiempo es lo mas delicado de este servicio. Un
servicio que atiende la misma ruta cada pocos segundos tiene SIEMPRE una
peticion cerca de la que fallo, y coger «la mas cercana» convertiria «nadie
atendio esto» en «lo atendieron y fue bien» — que es el error de diagnostico
mas caro que este codigo puede cometer, porque manda a mirar al lado contrario.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from diagnose.recoleccion import recolectar

from .traza_real import TRAZA_DEL_502

ABIERTO = "2026-10-25T08:02:47.784727+00:00"
# La llamada `Client` que fallo empieza a las 08:02:47.796142 y dura 189,165 ms,
# asi que su ventana es [47.796, 47.985].
LLAMADA_DESDE = datetime(2026, 10, 25, 8, 2, 47, 796142, tzinfo=UTC)


class AlmacenFalso:
    """Devuelve lo que se le diga y anota lo que se le pregunto."""

    def __init__(self, *, spans=None, alcance=None, concurrentes=None, contraparte=None, recurrencia=None, emision=None, instancias=None, minutos=60, linea_base=None) -> None:
        self._linea_base = linea_base or []
        self._spans = spans if spans is not None else TRAZA_DEL_502
        self._recurrencia = recurrencia or []
        self._emision = emision or []
        self._instancias = instancias or []
        self._minutos = minutos
        self._alcance = alcance or []
        self._concurrentes = concurrentes or []
        self._contraparte = contraparte or []
        self.preguntas: list[tuple[str, dict[str, Any]]] = []

    def spans_de_traza(self, trace_id):
        self.preguntas.append(("spans", {"traza": trace_id}))
        return self._spans

    def alcance(self, **kw):
        self.preguntas.append(("alcance", kw))
        return self._alcance

    def concurrentes(self, **kw):
        self.preguntas.append(("concurrentes", kw))
        return self._concurrentes

    def contraparte(self, **kw):
        self.preguntas.append(("contraparte", kw))
        return self._contraparte

    def recurrencia(self, **kw):
        self.preguntas.append(("recurrencia", kw))
        return self._recurrencia

    def emision(self, **kw):
        self.preguntas.append(("emision", kw))
        return self._emision

    def instancias(self, **kw):
        self.preguntas.append(("instancias", kw))
        return self._instancias

    def linea_base(self, **kw):
        self.preguntas.append(("linea_base", kw))
        return self._linea_base

    def minutos_con_datos(self, **kw):
        self.preguntas.append(("minutos_con_datos", kw))
        return self._minutos

    def cerrar(self): ...


def _incidente(**campos) -> dict[str, Any]:
    base = {
        "id": "abc123", "fingerprint": "h1", "app": "prometheus-inference-platform",
        "component": "gateway", "signature": "502", "kind": "error", "severity": "page",
        "title": "502 en gateway", "count": 3, "opened_at": ABIERTO,
        "last_seen_at": ABIERTO, "trace_ids": ["78ddf2c0b68609390c8ecf0abda29f7c"],
    }
    return {**base, **campos}


def _fila_contraparte(*, empieza: datetime, ms: float, estado: str = "Unset", codigo: str = "200"):
    return {
        "Timestamp": empieza.strftime("%Y-%m-%d %H:%M:%S.%f") + "000",
        "servicio": "auth-service", "nombre": "POST /oauth2/token",
        "Duration": int(ms * 1e6), "StatusCode": estado, "StatusMessage": "",
        "codigo": codigo, "clase": "", "TraceId": "otra",
    }


# --- Las lagunas -------------------------------------------------------------


def test_sin_almacen_el_expediente_existe_y_lo_dice() -> None:
    exp = recolectar(_incidente(), almacen=None, registro=[], historico=[])

    assert exp.app == "prometheus-inference-platform"
    assert any("sin almacen de telemetria" in x for x in exp.lagunas)


def test_no_poder_preguntar_y_no_haber_nada_son_lagunas_distintas() -> None:
    """`None` es «no pude»; `[]` es «no hay». La diferencia la lee quien juzga."""
    sin_poder = recolectar(_incidente(), almacen=None, registro=None, historico=None)
    sabiendo = recolectar(_incidente(), almacen=None, registro=[], historico=[])

    assert any("no pude leer el registro" in x for x in sin_poder.lagunas)
    assert any("no pude leer el historico" in x for x in sin_poder.lagunas)
    assert not any("registro" in x for x in sabiendo.lagunas)


def test_un_incidente_sin_trazas_lo_anota_en_vez_de_fallar() -> None:
    exp = recolectar(
        _incidente(trace_ids=[]), almacen=AlmacenFalso(), registro=[], historico=[]
    )

    assert any("no trae ninguna traza" in x for x in exp.lagunas)


def test_una_traza_que_no_esta_en_el_almacen_se_distingue_de_no_tener_traza() -> None:
    exp = recolectar(
        _incidente(), almacen=AlmacenFalso(spans=[]), registro=[], historico=[]
    )

    assert any("no esta en el almacen" in x for x in exp.lagunas)


def test_la_edad_nunca_es_negativa() -> None:
    """4 de 562 incidentes traen marcas de dos relojes distintos (B-34).

    Un expediente que dice «-818 s» se lee como roto, y el juicio que sale de
    el tambien.
    """
    futuro = (datetime.now(UTC) + timedelta(minutes=20)).isoformat()
    exp = recolectar(_incidente(opened_at=futuro), almacen=None, registro=[], historico=[])

    assert exp.edad_s == 0.0


# --- El empalme --------------------------------------------------------------


def test_el_puente_sale_de_la_llamada_que_fallo_y_busca_por_ruta() -> None:
    """Por ruta y no por nombre: los dos lados la nombran distinto.

    El cliente emite un span llamado `POST` a secas; el servidor lo llama
    `POST /oauth2/token`. Casar por nombre no encontraria nunca nada — y no
    encontrar nada es una conclusion con significado aqui, asi que el empalme
    mal hecho no se veria como un fallo sino como un diagnostico.
    """
    almacen = AlmacenFalso()
    recolectar(_incidente(), almacen=almacen, registro=[], historico=[])

    preguntas = dict(almacen.preguntas).get("contraparte") or {}
    assert preguntas.get("ruta") == "/oauth2/token"


def test_una_peticion_que_no_solapa_no_es_la_contraparte() -> None:
    """La peticion exitosa de medio segundo antes NO es la que fallo.

    Es el caso real: `auth-service` sirvio `POST /oauth2/token` a las
    08:02:47.346 durante 424 ms —acaba a las 47.770— y la llamada que fallo
    empieza a las 47.796. No se tocan. Si el empalme cogiera «la mas cercana»,
    el veredicto diria que la contraparte la dio por buena cuando en realidad
    nadie la atendio.
    """
    anterior = _fila_contraparte(
        empieza=datetime(2026, 10, 25, 8, 2, 47, 346661, tzinfo=UTC), ms=424.083
    )
    exp = recolectar(
        _incidente(), almacen=AlmacenFalso(contraparte=[anterior]), registro=[], historico=[]
    )

    assert exp.contraparte is not None
    assert exp.contraparte.encontrada is False
    assert exp.contraparte.destino == "http://127.0.0.1:9000/oauth2/token"


def test_una_peticion_que_solapa_si_es_la_contraparte_y_se_ve_la_discrepancia() -> None:
    solapa = _fila_contraparte(empieza=LLAMADA_DESDE + timedelta(milliseconds=5), ms=50)
    exp = recolectar(
        _incidente(), almacen=AlmacenFalso(contraparte=[solapa]), registro=[], historico=[]
    )

    assert exp.contraparte is not None
    assert exp.contraparte.encontrada
    assert exp.contraparte.servicio == "auth-service"
    assert exp.contraparte.discrepa, "el cliente lo cuenta como error y el servidor no"


def test_sin_llamada_saliente_que_falle_no_hay_bloque_de_contraparte() -> None:
    """No existir es distinto de existir vacio."""
    solo_servidor = [f for f in TRAZA_DEL_502 if f["SpanKind"] == "Server"]
    exp = recolectar(
        _incidente(), almacen=AlmacenFalso(spans=solo_servidor), registro=[], historico=[]
    )

    assert exp.contraparte is None


# --- Historia y topologia ----------------------------------------------------


def test_la_historia_cuenta_la_misma_firma_en_la_misma_app() -> None:
    historico = [
        {"signature": "502", "app": "prometheus-inference-platform", "component": "gateway",
         "opened_at": "2026-10-20T10:00:00+00:00", "id": "viejo1", "root_cause": "el token caducado"},
        {"signature": "502", "app": "prometheus-inference-platform", "component": "gateway",
         "opened_at": "2026-10-22T10:00:00+00:00", "id": "viejo2", "root_cause": None},
        # Misma firma, OTRA aplicacion: no cuenta.
        {"signature": "502", "app": "veritium", "component": "veritium-api",
         "opened_at": "2026-10-23T10:00:00+00:00", "id": "ajeno", "root_cause": "x"},
    ]
    exp = recolectar(_incidente(), almacen=None, registro=[], historico=historico)

    assert exp.historia.veces == 2
    assert exp.historia.causas_anteriores == ["el token caducado"]
    assert exp.historia.primera_vez is not None
    assert exp.historia.primera_vez.day == 20


def test_la_topologia_calcula_aguas_abajo_del_reves() -> None:
    """El registro solo guarda una direccion; la otra se deduce."""
    registro = [
        {"id": "prometheus-inference-platform", "depende_de": ["redis-main"],
         "componentes": ["gateway", "auth-service"]},
        {"id": "veritium", "depende_de": ["prometheus-inference-platform"],
         "componentes": ["veritium-api"]},
    ]
    exp = recolectar(_incidente(), almacen=None, registro=registro, historico=[])

    assert exp.aguas_arriba == ["redis-main"]
    assert exp.aguas_abajo == ["veritium"]


def test_un_concurrente_se_marca_aguas_arriba_traduciendo_componente_a_app() -> None:
    """El registro declara dependencias entre APPS y aqui llegan COMPONENTES.

    Sin la traduccion la marca no se activaria nunca y el bloque entero seria
    ruido con formato.
    """
    registro = [
        {"id": "veritium", "depende_de": ["prometheus-inference-platform"],
         "componentes": ["veritium-api"]},
        {"id": "prometheus-inference-platform", "depende_de": [],
         "componentes": ["gateway", "auth-service"]},
    ]
    almacen = AlmacenFalso(
        concurrentes=[
            {"servicio": "otra-cosa", "clase": "ValueError", "errores": 50},
            {"servicio": "gateway", "clase": "502", "errores": 2},
        ]
    )
    exp = recolectar(
        _incidente(app="veritium", component="veritium-api"),
        almacen=almacen, registro=registro, historico=[],
    )

    assert exp.concurrentes[0].servicio == "gateway", "la dependencia va primero"
    assert exp.concurrentes[0].aguas_arriba
    assert not exp.concurrentes[1].aguas_arriba


# --- Recurrencia: lo que el almacen de incidentes no podia contestar ----------


def test_un_fallo_de_todos_los_dias_sale_como_cronico() -> None:
    """El caso real que motivo la consulta.

    El `ReadTimeout` del gateway tiene trazas 27 de los ultimos 30 dias. El
    almacen de incidentes solo guardaba la ultima ocurrencia de cada huella, y
    L3 dijo «primera vez» (D-133).
    """
    dias = [{"dia": f"2026-10-{d:02d}", "errores": 80} for d in range(1, 28)]
    exp = recolectar(
        _incidente(signature="ReadTimeout"),
        almacen=AlmacenFalso(recurrencia=dias), registro=[], historico=[],
    )

    assert exp.historia.dias_con_errores == 27
    assert exp.historia.es_cronico
    assert not exp.historia.es_nuevo
    assert "CRONICO" in exp.texto()


def test_la_recurrencia_se_cuenta_hasta_el_incidente_y_no_hasta_hoy() -> None:
    """Un incidente de hace dos semanas no puede ver errores de despues.

    Es lo que hace honesto al banco: diagnostica incidentes pasados, y contar
    hasta hoy le daria al juez informacion que en su momento no existia.
    """
    almacen = AlmacenFalso()
    recolectar(_incidente(), almacen=almacen, registro=[], historico=[])

    kw = dict(almacen.preguntas)["recurrencia"]
    assert kw["hasta"] < datetime(2026, 10, 25, 8, 10, tzinfo=UTC)
    assert kw["clase"] == "502"


def test_una_senal_que_no_es_un_error_no_cuenta_recurrencia() -> None:
    """Para `canary-silencio` la consulta daria cero, y ese cero seria «no aplica»."""
    almacen = AlmacenFalso()
    exp = recolectar(
        _incidente(kind="silence", signature="canary-silencio"),
        almacen=almacen, registro=[], historico=[],
    )

    assert "recurrencia" not in dict(almacen.preguntas)
    assert exp.historia.dias_mirados == 0


def test_cero_dias_de_recurrencia_no_se_lee_como_nunca() -> None:
    """Un incidente de tipo `error` con cero dias de errores es una contradiccion.

    Significa que la clase no aparece como `error.type`, no que no haya
    pasado nunca. El primer banco escribio «ninguno de los 30 dias» para dos
    incidentes cuya firma era el mensaje entero.
    """
    exp = recolectar(
        _incidente(signature="NotNullViolationError: null value\nDETAIL: fila"),
        almacen=AlmacenFalso(recurrencia=[]), registro=[], historico=[],
    )

    assert exp.historia.dias_mirados == 0
    assert any("no se puede contar" in x for x in exp.lagunas)
    assert "RECURRENCIA" not in exp.texto()


def test_una_firma_que_es_un_mensaje_cuenta_por_la_clase_del_ejemplar() -> None:
    """La firma de antes de D-118 era el mensaje; lo que esta en `error.type` es la clase."""
    almacen = AlmacenFalso()
    recolectar(
        _incidente(signature="400: algo salio mal con espacios"),
        almacen=almacen, registro=[], historico=[],
    )

    # En la traza real del 502 la clase que empieza la firma es `400`.
    assert dict(almacen.preguntas)["recurrencia"]["clase"] == "400"


# --- Emision anterior: «nunca hablo» frente a «dejo de hablar» (B-39) ---------


def _filas_emision(**por_fuente):
    fuentes = ("trazas", "logs", "metricas_suma", "metricas_gauge", "metricas_histograma")
    return [
        {"fuente": f, "n": por_fuente.get(f, 0),
         "primera": "2026-10-01 00:00:00.000000000" if por_fuente.get(f) else "1970-01-01 00:00:00.000000000",
         "ultima": "2026-10-23 10:45:12.000000000" if por_fuente.get(f) else "1970-01-01 00:00:00.000000000"}
        for f in fuentes
    ]


def test_un_silencio_sobre_algo_que_nunca_emitio_se_ve() -> None:
    exp = recolectar(
        _incidente(kind="burn_rate", signature="canary-silencio", trace_ids=[]),
        almacen=AlmacenFalso(emision=_filas_emision()), registro=[], historico=[],
    )

    assert exp.emision.consultada
    assert exp.emision.nunca
    assert "no consta que haya hablado nunca" in exp.texto()


def test_un_silencio_sobre_algo_que_emitia_dice_hasta_cuando() -> None:
    """Y las tablas vacias no meten su `1970` como primera fecha."""
    exp = recolectar(
        _incidente(kind="burn_rate", signature="canary-silencio", trace_ids=[]),
        almacen=AlmacenFalso(emision=_filas_emision(trazas=1200, metricas_suma=30)),
        registro=[], historico=[],
    )

    assert not exp.emision.nunca
    assert exp.emision.total == 1230
    assert exp.emision.primera is not None and exp.emision.primera.year == 2026
    assert exp.emision.ultima is not None and exp.emision.ultima.hour == 10


def test_un_error_no_pregunta_por_la_emision() -> None:
    """Si fallo, emitio: la pregunta solo tiene sentido para un silencio."""
    almacen = AlmacenFalso()
    recolectar(_incidente(), almacen=almacen, registro=[], historico=[])

    assert "emision" not in dict(almacen.preguntas)


# --- Procesos: reinicios y despliegues (B-38) ---------------------------------


def _inst(instancia, desde, hasta, errores=0, primer_error="1970-01-01 00:00:00.000000000", version="0.3.0"):
    return {"instancia": instancia, "version": version, "desde": desde, "hasta": hasta,
            "errores": errores, "primer_error": primer_error}


def test_se_ve_cuanto_llevaba_vivo_el_proceso_cuando_empezo_a_fallar() -> None:
    exp = recolectar(
        _incidente(),
        almacen=AlmacenFalso(instancias=[
            _inst("api-7e6f", "2026-10-14 02:06:42.764009000", "2026-10-14 11:14:00.000000000",
                  errores=40, primer_error="2026-10-14 11:10:37.101295000"),
        ]),
        registro=[], historico=[],
    )

    c = exp.cambios
    assert c.consultada and c.reinicios == 0
    assert c.minutos_en_marcha_antes_del_fallo is not None
    assert 540 < c.minutos_en_marcha_antes_del_fallo < 550
    assert "PROCESOS" in exp.texto()


def test_una_instancia_sin_errores_no_trae_el_1970_como_primer_error() -> None:
    exp = recolectar(
        _incidente(),
        almacen=AlmacenFalso(instancias=[_inst("a", "2026-10-14 02:00:00.000000000", "2026-10-14 11:00:00.000000000")]),
        registro=[], historico=[],
    )

    assert exp.cambios.actual is not None
    assert exp.cambios.actual.primer_error is None


def test_un_componente_sin_identidad_de_proceso_lo_dice() -> None:
    """Que no emita `service.instance.id` no es lo mismo que «no hubo reinicios»."""
    exp = recolectar(
        _incidente(),
        almacen=AlmacenFalso(instancias=[_inst("", "2026-10-14 02:00:00.000000000", "2026-10-14 11:00:00.000000000")]),
        registro=[], historico=[],
    )

    assert exp.cambios.sin_identidad
    assert "no se pueden ver reinicios" in exp.texto()


def test_la_plataforma_se_mide_hasta_la_apertura_y_no_despues() -> None:
    """Justo despues de abrirse vuelve una rafaga; medirla seria ver el futuro."""
    almacen = AlmacenFalso(minutos=14)
    exp = recolectar(
        _incidente(kind="burn_rate", signature="canary-silencio", trace_ids=[]),
        almacen=almacen, registro=[], historico=[],
    )

    assert dict(almacen.preguntas)["minutos_con_datos"]["hasta"] == datetime(2026, 10, 25, 8, 2, 47, 784727, tzinfo=UTC)
    assert exp.plataforma.a_rafagas


# --- B-40: los hermanos ---------------------------------------------------------


def _centinela(componente: str, segundos: float, firma: str = "redis.exceptions.TimeoutError") -> dict[str, Any]:
    cuando = datetime.fromisoformat(ABIERTO) + timedelta(seconds=segundos)
    return {"id": componente, "app": "centinela", "component": componente, "signature": firma,
            "opened_at": cuando.isoformat(), "root_cause": "lo que escribio L3"}


def test_los_componentes_de_la_misma_app_con_la_misma_clase_a_la_vez_son_hermanos() -> None:
    """Centinela el 25/10: cuatro componentes, `redis.exceptions.TimeoutError`, el mismo segundo."""
    historico = [
        _centinela("centinela-camera-manager", 0),
        _centinela("centinela-celery", 0),
        _centinela("centinela-enrich-worker", 2),
        _centinela("centinela-api", -1143),                                   # 19 min antes: no
        _centinela("centinela-camera-manager", 1, firma="GET"),              # otra clase: no
        {**_centinela("veritium-worker", 0), "app": "veritium"},             # otra app: no
        _centinela("centinela-notifier", 30),                                # el propio componente: no
    ]
    exp = recolectar(_incidente(app="centinela", component="centinela-notifier",
                                signature="TimeoutError", trace_ids=[]), historico=historico)

    assert [h.componente for h in exp.hermanos] == [
        "centinela-camera-manager", "centinela-celery", "centinela-enrich-worker",
    ]
    assert "4 componentes de centinela con la misma clase" in exp.texto()


def test_una_clase_sin_nombre_no_hace_hermanos() -> None:
    """Tres `error` a la vez comparten un SDK que no supo nombrar la excepcion, no una causa."""
    historico = [_centinela("a", 0, firma="error"), _centinela("b", 0, firma="error")]
    exp = recolectar(_incidente(app="centinela", component="c", signature="error", trace_ids=[]), historico=historico)

    assert exp.hermanos == []


# --- B-35: la linea base del destino ------------------------------------------


def test_la_linea_base_se_pide_al_que_atendio_hasta_la_hora_de_la_llamada() -> None:
    """Lo normal se mide ANTES del incidente: despues no lo sabia nadie."""
    alm = AlmacenFalso(
        contraparte=[_fila_contraparte(empieza=LLAMADA_DESDE, ms=100, estado="Unset", codigo="400")],
        linea_base=[{"guardadas": 40, "estimadas": 220, "p50_ms": 80.0, "p95_ms": 150.0, "sobre_plazo": 3}],
    )
    exp = recolectar(_incidente(), almacen=alm)

    kw = next(kw for q, kw in alm.preguntas if q == "linea_base")
    assert kw["servicio"] == "auth-service" and kw["ruta"] == "/oauth2/token"
    assert kw["hasta"] == LLAMADA_DESDE
    assert exp.contraparte.linea_base.p50_ms == 80.0
    assert "normalmente: p50 0.1 s" in exp.texto()


def test_una_linea_base_con_pocas_peticiones_es_una_laguna_y_no_un_dato() -> None:
    alm = AlmacenFalso(
        contraparte=[_fila_contraparte(empieza=LLAMADA_DESDE, ms=100, estado="Unset", codigo="400")],
        linea_base=[{"guardadas": 1, "estimadas": 10, "p50_ms": 80.0, "p95_ms": 80.0, "sobre_plazo": 0}],
    )
    exp = recolectar(_incidente(), almacen=alm)

    assert exp.contraparte.linea_base is None
    assert any(x.startswith("linea base: solo ~10") for x in exp.lagunas)
