"""El arbol del ejemplar: lo que L3 lee antes de opinar."""

from __future__ import annotations

from datetime import UTC, datetime

from diagnose.expediente import MAX_SPANS, Arbol, Expediente, Span, arbol_desde_filas

from .traza_real import TRAZA_DEL_502


def test_el_arbol_sale_de_una_traza_real_con_padres_e_hijos() -> None:
    """Siete spans, una raiz, seis hijos a profundidad 1."""
    arbol = arbol_desde_filas(TRAZA_DEL_502)

    assert len(arbol.spans) == 7
    assert arbol.spans[0].nombre == "POST /admin/api/assistant/chat"
    assert arbol.spans[0].profundidad == 0
    assert all(s.profundidad == 1 for s in arbol.spans[1:])
    assert not arbol.truncado


def test_el_eslabon_mas_profundo_es_la_llamada_que_sale_y_no_la_copia_del_middleware() -> None:
    """El desempate por tipo es lo que hace util a este campo.

    En esta traza hay TRES spans con error a dos profundidades: el `Server` de
    la peticion (502), el `Client` de la llamada a `/oauth2/token` (400) y un
    `Internal` llamado `… http send` que copia el 502 de la respuesta.

    Los dos ultimos estan a la misma profundidad. Sin desempate, el resultado
    depende del orden en que ClickHouse devuelva las filas, y la mitad de las
    veces el «fallo mas profundo» seria la copia contable del fallo que ya
    nombra el aviso — o sea, ninguna informacion nueva.
    """
    arbol = arbol_desde_filas(TRAZA_DEL_502)
    eslabon = arbol.eslabon_mas_profundo

    assert eslabon is not None
    assert eslabon.tipo == "Client"
    assert eslabon.clase == "400"
    assert eslabon.ruta == "http://127.0.0.1:9000/oauth2/token"


def test_la_cadena_del_fallo_son_solo_los_spans_con_error() -> None:
    arbol = arbol_desde_filas(TRAZA_DEL_502)
    cadena = arbol.cadena_del_fallo

    assert [s.clase for s in cadena] == ["502", "400", "502"]
    assert all(s.error for s in cadena)


def test_un_span_huerfano_cuenta_como_raiz_y_no_se_descarta() -> None:
    """Con muestreo por cola, el padre puede no estar retenido.

    Descartar al hijo por no encontrar al padre esconderia justo el span que
    explica el fallo. Es el caso normal en un stack con muestreo, no el raro.
    """
    filas = [
        {"TraceId": "t", "SpanId": "hijo", "ParentSpanId": "padre-que-no-llego",
         "SpanName": "la llamada que fallo", "SpanKind": "Client",
         "ServiceName": "x", "Duration": 1_000_000, "StatusCode": "Error"},
    ]
    arbol = arbol_desde_filas(filas)

    assert len(arbol.spans) == 1
    assert arbol.spans[0].profundidad == 0
    assert arbol.spans[0].es_raiz


def test_el_arbol_se_recorta_y_lo_dice() -> None:
    """Un incidente grande no puede traer diez mil spans al expediente."""
    filas = [
        {"TraceId": "t", "SpanId": f"s{i}", "ParentSpanId": "", "SpanName": f"n{i}",
         "SpanKind": "Internal", "ServiceName": "x", "Duration": 0, "StatusCode": "Unset"}
        for i in range(MAX_SPANS + 10)
    ]
    arbol = arbol_desde_filas(filas)

    assert len(arbol.spans) == MAX_SPANS
    assert arbol.truncado
    assert "recortado" in arbol.texto()


def test_la_hora_del_span_llega_con_zona() -> None:
    """ClickHouse la da sin zona y la columna es UTC.

    Dejarla ingenua es como se cuelan los desfases: dos marcas sin zona se
    comparan sin quejarse, y el puente al otro lado de la llamada se decide
    comparando horas (D-129).
    """
    arbol = arbol_desde_filas(TRAZA_DEL_502)

    assert arbol.spans[0].momento is not None
    assert arbol.spans[0].momento.tzinfo is UTC


def test_el_texto_del_expediente_no_inventa_bloques_que_no_tiene() -> None:
    """Un expediente vacio no dice nada de alcance, historia ni topologia.

    Importa porque lo que sale de aqui es lo que lee el modelo: un bloque
    `ALCANCE 0 de 0` se lee como «no hay trafico», que es una afirmacion, y lo
    que hay es una ausencia de datos.
    """
    exp = Expediente(
        incidente_id="i", huella="h", app="a", componente="c",
        firma="Boom", tipo_senal="error", severidad="page",
    )
    texto = exp.texto()

    assert "INCIDENTE" in texto
    for bloque in ("ALCANCE", "HISTORIA", "TOPOLOGIA", "ARBOL", "OTROS SERVICIOS"):
        assert bloque not in texto


def test_las_lagunas_se_cuentan_en_el_texto() -> None:
    """«No hay trazas» y «no pude consultar las trazas» son cosas distintas."""
    exp = Expediente(
        incidente_id="i", huella="h", app="a", componente="c",
        firma="Boom", tipo_senal="error", severidad="page",
        lagunas=["arbol: el incidente no trae ninguna traza de ejemplo"],
    )

    assert "NO PUDE RECOLECTAR" in exp.texto()
    assert "ninguna traza" in exp.texto()


def test_el_arbol_sangra_los_hijos_para_que_se_lea() -> None:
    arbol = Arbol(
        trace_id="t",
        spans=[
            Span(span_id="a", nombre="padre", tipo="Server", servicio="s", ms=10, profundidad=0),
            Span(span_id="b", nombre="hijo", tipo="Client", servicio="s", ms=5, profundidad=1,
                 error=True, clase="400", momento=datetime.now(UTC)),
        ],
    )
    lineas = arbol.texto().splitlines()

    assert lineas[0].startswith("  padre")
    assert lineas[1].startswith("✗   hijo")
    assert "400" in lineas[1]
