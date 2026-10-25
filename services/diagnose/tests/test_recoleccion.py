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

    def __init__(self, *, spans=None, alcance=None, concurrentes=None, contraparte=None) -> None:
        self._spans = spans if spans is not None else TRAZA_DEL_502
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
