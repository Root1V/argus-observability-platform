"""Las reglas del juez, una por una, y la que no existe.

La prueba que de verdad protege algo es la ultima: **cuando ninguna regla
dispara, `root_cause` se queda vacio**. Es la unica propiedad de este servicio
que no se puede relajar, porque `render.titular` lee ese campo para anunciar si
hay causa, y una descripcion del sintoma escrita ahi convierte el aviso en una
afirmacion falsa en el movil de una persona.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from diagnose.expediente import Alcance, Arbol, Concurrente, Contraparte, Expediente, Historia, Span
from diagnose.juicio import JuezDeReglas


def _exp(**campos) -> Expediente:
    base = {
        "incidente_id": "i1", "huella": "h1", "app": "prometheus-inference-platform",
        "componente": "gateway", "firma": "502", "tipo_senal": "error", "severidad": "page",
    }
    return Expediente(**{**base, **campos})


def test_sin_ninguna_regla_no_hay_causa_y_la_evidencia_si() -> None:
    """Lo que mas importa: no inventar, y aun asi entregar algo util.

    El aviso sigue diciendo «investigando…», que es verdad. Lo que cambia es
    que ahora lleva adjunto cuanto trafico fallo y desde cuando, que es lo que
    una persona necesita para decidir si abre el portatil.
    """
    exp = _exp(alcance=Alcance(fallidas=3, totales=120, ventana_min=5))
    v = JuezDeReglas().juzgar(exp)

    assert v.root_cause is None
    assert not v.tiene_causa
    assert v.confidence == "baja"
    assert any("3 de 120" in e for e in v.evidence)
    assert "root_cause" not in v.como_enriquecimiento()


def test_la_discrepancia_del_puente_apunta_a_quien_llama() -> None:
    """La inferencia mas fuerte sin modelo, y la contraria a lo que sugiere el aviso.

    El gateway devuelve 502 y su llamada a `/oauth2/token` da 400; auth-service
    atendio esa peticion y la dio por buena. Un 4xx es error para el cliente
    —pidio mal— y no lo es para el servidor. La causa esta en lo que el gateway
    ENVIA.
    """
    exp = _exp(
        contraparte=Contraparte(
            llamada="POST (400)", destino="http://127.0.0.1:9000/oauth2/token",
            encontrada=True, servicio="auth-service", codigo="200",
            estado="Unset", ms=424.0, discrepa=True,
        )
    )
    v = JuezDeReglas().juzgar(exp)

    assert v.confidence == "alta"
    assert v.root_cause is not None
    assert "auth-service" in v.root_cause
    assert "ENVIANDO" in v.root_cause


def _arbol_de_una_llamada(clase: str, *, excepcion: str | None = None, ms: float = 7.0) -> Arbol:
    return Arbol(
        trace_id="t",
        spans=[
            Span(span_id="a", nombre="GET", tipo="Client", servicio="gateway", ms=ms,
                 error=True, clase=clase, excepcion=excepcion,
                 ruta="http://127.0.0.1:8020/admin/api/clients/names", profundidad=0),
        ],
    )


def test_una_llamada_sin_respuesta_mueve_la_causa_al_transporte() -> None:
    """Y se queda en confianza media a proposito.

    El puente vacio puede significar tres cosas —que nadie la atendiera, que el
    destino no este instrumentado, o que nombre la ruta con otra plantilla— y
    el veredicto lo escribe en voz alta en vez de subir la confianza.
    """
    exp = _exp(
        firma="ReadTimeout",
        arbol=_arbol_de_una_llamada("ReadTimeout", excepcion="httpx.ReadTimeout", ms=75_236),
        contraparte=Contraparte(
            llamada="GET (ReadTimeout)", destino="http://127.0.0.1:8091/v1/backends",
            encontrada=False,
        ),
    )
    v = JuezDeReglas().juzgar(exp)

    assert v.confidence == "media"
    assert v.root_cause is not None
    assert "transporte o en el plazo" in v.root_cause
    assert "tras 75 s" in v.root_cause
    # La conclusion la sostiene la CLASE, no el puente vacio: el muestreo de
    # cola descarta ~9 de cada 10 trazas del destino atendiendo bien, asi que
    # «nadie lo atendio» no se puede afirmar desde ahi.
    assert "NADIE" not in v.root_cause
    assert "probabilidad ~0,9" in v.root_cause


def test_un_codigo_http_saliente_no_se_diagnostica_como_que_nunca_llego() -> None:
    """La correccion que salio del primer ensayo contra el stack real.

    Un incidente `404` del gateway salia diagnosticado como «el fallo ocurrio
    antes de llegar a la aplicacion destino». Para un 404 eso es casi lo
    contrario de la verdad: **un codigo de estado es una respuesta, y una
    respuesta prueba que alguien contesto.** Lo que falla es la peticion, no la
    disponibilidad del destino (D-131).

    Es la clase de error que ninguna prueba con datos inventados encuentra,
    porque para escribirla hay que haber visto primero el diagnostico
    equivocado sobre un incidente real.
    """
    exp = _exp(
        firma="404",
        arbol=_arbol_de_una_llamada("404"),
        contraparte=Contraparte(
            llamada="GET (404)", destino="http://127.0.0.1:8020/admin/api/clients/names",
            encontrada=False,
        ),
    )
    v = JuezDeReglas().juzgar(exp)

    assert v.root_cause is not None
    assert "antes de llegar" not in v.root_cause
    assert "es una RESPUESTA" in v.root_cause
    assert "la peticion que" in v.root_cause
    assert "no se pudo confirmar" in v.root_cause.lower()
    assert "9 de cada 10" in v.root_cause


def test_si_la_contraparte_confirma_el_codigo_se_dice_quien_lo_contesto() -> None:
    exp = _exp(
        firma="404",
        arbol=_arbol_de_una_llamada("404"),
        contraparte=Contraparte(
            llamada="GET (404)", destino="http://127.0.0.1:8020/admin/api/clients/names",
            encontrada=True, servicio="manager-api", codigo="404", estado="Unset",
        ),
    )
    v = JuezDeReglas().juzgar(exp)

    # Con `discrepa=False` no gana la regla del puente, asi que llega a esta.
    assert v.root_cause is not None
    assert "manager-api lo confirma" in v.root_cause


def test_cuando_la_firma_nombra_el_sintoma_se_dice_cual_es_la_de_dentro() -> None:
    exp = _exp(
        firma="502",
        arbol=Arbol(
            trace_id="t",
            spans=[
                Span(span_id="a", nombre="POST /chat", tipo="Server", ms=200,
                     error=True, clase="502", profundidad=0),
                Span(span_id="b", nombre="POST", tipo="Client", ms=189, error=True,
                     clase="400", ruta="http://127.0.0.1:9000/oauth2/token", profundidad=1),
            ],
        ),
    )
    v = JuezDeReglas().juzgar(exp)

    assert v.root_cause is not None
    assert "`502`" in v.root_cause
    assert "`400`" in v.root_cause


def test_una_excepcion_con_nombre_y_mensaje_es_una_causa() -> None:
    exp = _exp(
        firma="ForeignKeyViolationError",
        arbol=Arbol(
            trace_id="t",
            spans=[
                Span(span_id="a", nombre="search_chunks", tipo="Internal", servicio="veritium-worker",
                     ms=12, error=True, clase="ForeignKeyViolationError",
                     excepcion="ForeignKeyViolationError",
                     mensaje="insert or update on table violates foreign key constraint",
                     profundidad=0),
            ],
        ),
    )
    v = JuezDeReglas().juzgar(exp)

    assert v.root_cause is not None
    assert "ForeignKeyViolationError" in v.root_cause
    assert "foreign key constraint" in v.root_cause


def test_una_dependencia_que_falla_a_la_vez_es_correlacion_y_lo_dice() -> None:
    exp = _exp(
        app="veritium",
        aguas_arriba=["prometheus-inference-platform"],
        concurrentes=[
            Concurrente(servicio="gateway", clase="502", errores=14, aguas_arriba=True),
            Concurrente(servicio="otra-cosa", clase="ValueError", errores=99),
        ],
    )
    v = JuezDeReglas().juzgar(exp)

    assert v.root_cause is not None
    assert "gateway" in v.root_cause
    assert "correlacion temporal" in v.root_cause
    assert v.confidence == "media"


def test_una_causa_anotada_antes_para_la_misma_firma_se_reutiliza() -> None:
    """Donde la memoria empieza a pagar, con la unica forma disponible hoy."""
    exp = _exp(
        historia=Historia(
            veces=4,
            primera_vez=datetime(2026, 10, 1, tzinfo=UTC),
            causas_anteriores=["el pool de conexiones se agota al reindexar"],
        )
    )
    v = JuezDeReglas().juzgar(exp)

    assert v.root_cause is not None
    assert "pool de conexiones" in v.root_cause
    assert "4 incidentes" in v.root_cause


def test_las_reglas_estan_ordenadas_por_fuerza_y_la_del_puente_gana() -> None:
    """Un expediente que dispara varias reglas usa la mas fuerte.

    El orden es la parte revisable del juez: cambiarlo cambia los
    diagnosticos. Esta prueba es lo que convierte ese orden en una decision y
    no en un efecto de como quedaron escritos los `if`.
    """
    exp = _exp(
        contraparte=Contraparte(
            llamada="POST (400)", destino="http://x/y", encontrada=True,
            servicio="auth-service", codigo="200", estado="Unset", discrepa=True,
        ),
        concurrentes=[Concurrente(servicio="gateway", clase="502", errores=9, aguas_arriba=True)],
        historia=Historia(veces=3, causas_anteriores=["otra cosa"]),
    )
    v = JuezDeReglas().juzgar(exp)

    assert "auth-service" in (v.root_cause or "")
    assert v.confidence == "alta"


def test_las_lagunas_viajan_en_la_evidencia() -> None:
    """Un diagnostico hecho sin el arbol no vale lo mismo que uno hecho con el."""
    exp = _exp(lagunas=["arbol: el incidente no trae ninguna traza de ejemplo"])
    v = JuezDeReglas().juzgar(exp)

    assert any(e.startswith("NO DISPONIBLE") for e in v.evidence)


@pytest.mark.parametrize("confianza", ["alta", "media", "baja"])
def test_el_enriquecimiento_lleva_siempre_confianza_y_evidencia(confianza: str) -> None:
    from diagnose.juicio import Veredicto

    cuerpo = Veredicto(root_cause="x", confidence=confianza, evidence=["y"]).como_enriquecimiento()

    assert cuerpo["confidence"] == confianza
    assert cuerpo["evidence"] == ["y"]

def test_un_destino_lento_no_se_diagnostica_como_una_peticion_mala() -> None:
    """La segunda correccion del ensayo real, y la unica cadena causal MEDIDA.

    Un `ReadTimeout` del gateway contra `/v1/backends` salia diagnosticado como
    «la causa esta en lo que el gateway esta ENVIANDO», porque las marcas
    discrepaban: el cliente lo contaba como error y `manager-api` no.

    Los datos decian otra cosa y a la vista: `manager-api` atendio la peticion,
    devolvio 200 y tardo **81 segundos**; el gateway se rindio a los 75. La
    peticion estaba perfecta. «Te lo dio por bueno» solo significa «pediste
    mal» cuando quien llama RECIBIO la respuesta (D-131).
    """
    exp = _exp(
        firma="ReadTimeout",
        arbol=_arbol_de_una_llamada("ReadTimeout", excepcion="httpx.ReadTimeout", ms=75_236),
        contraparte=Contraparte(
            llamada="GET (ReadTimeout)", destino="http://127.0.0.1:8091/v1/backends",
            encontrada=True, servicio="manager-api", codigo="200", estado="Unset",
            ms=81_471, discrepa=True,
        ),
    )
    v = JuezDeReglas().juzgar(exp)

    assert v.confidence == "alta"
    assert v.root_cause is not None
    assert "ENVIANDO" not in v.root_cause
    assert "tardo 81 s" in v.root_cause
    assert "se rindio a los 75 s" in v.root_cause
    assert "latencia de manager-api" in v.root_cause


def test_un_destino_que_contesta_rapido_y_bien_si_apunta_a_quien_llama() -> None:
    """El caso que la regla de la discrepancia si cubre: hubo respuesta.

    Un 400 recibido significa que el destino contesto rechazando. Ahi la
    discrepancia de marcas es la inferencia correcta y la causa esta en quien
    llama.
    """
    exp = _exp(
        firma="400",
        arbol=_arbol_de_una_llamada("400", ms=189),
        contraparte=Contraparte(
            llamada="POST (400)", destino="http://127.0.0.1:9000/oauth2/token",
            encontrada=True, servicio="auth-service", codigo="200", estado="Unset",
            ms=424, discrepa=True,
        ),
    )
    v = JuezDeReglas().juzgar(exp)

    assert v.confidence == "alta"
    assert "ENVIANDO" in (v.root_cause or "")


def test_la_evidencia_no_dice_primera_vez_sin_poder_saberlo() -> None:
    """Sin trazas que mirar, el almacen de incidentes solo permite decir «sin registro».

    Hasta D-133 decia «primera vez que esta firma abre incidente» leyendo un
    almacen que pisaba las reaperturas.
    """
    v = JuezDeReglas().juzgar(_exp())

    assert not any("primera vez" in e for e in v.evidence)
    assert any("sin incidentes anteriores registrados" in e for e in v.evidence)


def test_un_fallo_cronico_se_dice_en_la_evidencia() -> None:
    exp = _exp(historia=Historia(dias_con_errores=27, dias_mirados=30, errores_en_trazas=2556))
    v = JuezDeReglas().juzgar(exp)

    assert any(e.startswith("CRONICO") and "27 de los ultimos 30" in e for e in v.evidence)


def test_una_firma_que_es_un_mensaje_no_dispara_la_regla_del_sintoma() -> None:
    """La regla comparaba la clase del span con la firma ENTERA.

    Antes de D-118 la firma era el mensaje, asi que nunca coincidian y la regla
    disparaba siempre, repitiendo el mensaje en el veredicto (D-134).
    """
    exp = _exp(
        firma="TypeError: GenAISpan.set() got an unexpected keyword argument 'argus.run.id'",
        arbol=Arbol(trace_id="t", spans=[
            Span(span_id="a", nombre="chat", tipo="Client", error=True, clase="TypeError",
                 excepcion="TypeError", mensaje="GenAISpan.set() got an unexpected keyword argument 'argus.run.id'",
                 profundidad=1),
        ]),
    )
    v = JuezDeReglas().juzgar(exp)

    assert v.root_cause is not None
    assert "La causa es esa, no la firma" not in v.root_cause
    assert "GenAISpan.set()" in v.root_cause


def test_un_pending_rollback_se_diagnostica_como_sintoma_de_la_excepcion_original() -> None:
    """El veredicto decia que la causa era `PendingRollbackError`: el sintoma."""
    mensaje = (
        "This Session's transaction has been rolled back due to a previous exception during flush. "
        "To begin a new transaction with this Session, first issue Session.rollback(). Original exception was: "
        "(sqlalchemy.dialects.postgresql.asyncpg.IntegrityError) <class 'asyncpg.exceptions.NotNullViolationError'>: "
        'null value in column "tenant" of relation "validation_issues" violates not-null constraint\n'
        "DETAIL:  Failing row contains (00000000-uuid-falso, CODIGO-FALSO-999, ...)"
    )
    exp = _exp(
        firma="sqlalchemy.exc.PendingRollbackError",
        arbol=Arbol(trace_id="t", spans=[
            Span(span_id="a", nombre="veritium.case.evaluate", tipo="Internal", servicio="veritium-worker",
                 error=True, clase="sqlalchemy.exc.PendingRollbackError",
                 excepcion="sqlalchemy.exc.PendingRollbackError", mensaje=mensaje),
        ]),
    )
    v = JuezDeReglas().juzgar(exp)

    assert v.root_cause is not None
    assert "SINTOMA" in v.root_cause
    assert '"tenant"' in v.root_cause
    # Los valores de la fila no salen al aviso.
    assert "CODIGO-FALSO-999" not in v.root_cause
    assert "uuid-falso" not in v.root_cause
