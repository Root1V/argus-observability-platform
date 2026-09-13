"""Correlacion por topologia: el aviso que importa no queda enterrado.

Cuando falla una dependencia, TODO lo que depende de ella falla tambien. Sin
correlacion, una caida de Postgres genera un aviso por cada aplicacion que lo
usa, y el aviso util —el de Postgres— queda sepultado entre los sintomas.

El fallo PELIGROSO de la correlacion es el contrario: convertir "esto tiene
explicacion" en "esto no hace falta mirarlo". Por eso hay tantos tests sobre
cuando NO se debe suprimir.
"""

from __future__ import annotations

from datetime import timedelta

from alert_bus.engine import Engine
from alert_bus.sinks import MemorySink
from argus_schemas import Signal, SignalKind


def senal(app: str, component: str, signature: str = "timeout") -> Signal:
    return Signal(
        kind=SignalKind.ERROR,
        app=app,
        component=component,
        signature=signature,
        title=f"{signature} en {component}",
    )


# --- El caso que justifica todo ---------------------------------------------


def test_el_sintoma_se_suprime_cuando_hay_causa_aguas_arriba(engine: Engine, sink: MemorySink) -> None:
    """Postgres cae; la IDP falla porque depende de el. UN aviso, no dos."""
    engine.ingest([senal("postgres-main", "postgres-main")])
    assert len(sink.opened) == 1

    engine.ingest([senal("intelligent-document-platform", "idp-api")])

    # El sintoma existe, pero no genero aviso.
    assert len(sink.opened) == 1
    abiertos = {i.app: i for i in engine.open_incidents()}
    sintoma = abiertos["intelligent-document-platform"]
    assert sintoma.is_symptom
    assert sintoma.suppressed_by == abiertos["postgres-main"].id


def test_la_causa_registra_sus_sintomas(engine: Engine, sink: MemorySink) -> None:
    """El informe puede decir "y ademas esta afectando a X e Y"."""
    engine.ingest([senal("postgres-main", "postgres-main")])
    engine.ingest([senal("intelligent-document-platform", "idp-api")])
    engine.ingest([senal("aeon-ai", "control-plane")])

    causa = next(i for i in engine.open_incidents() if i.app == "postgres-main")
    assert len(causa.symptoms) == 2
    assert engine.stats["symptoms_suppressed"] == 2


def test_sin_causa_aguas_arriba_el_incidente_avisa_normalmente(engine: Engine, sink: MemorySink) -> None:
    engine.ingest([senal("intelligent-document-platform", "idp-api")])
    assert len(sink.opened) == 1
    assert not sink.opened[0].is_symptom


# --- Cuando NO se debe suprimir ---------------------------------------------


def test_no_se_suprime_contra_una_causa_antigua(registry, sink: MemorySink) -> None:
    """"Hay algo roto arriba" deja de ser explicacion y pasa a ser excusa.

    Un incidente aguas arriba abierto hace horas no puede seguir explicando lo
    que esta pasando ahora.
    """
    engine = Engine(registry, [sink], correlation_window_s=300)

    engine.ingest([senal("postgres-main", "postgres-main")])
    causa = engine.open_incidents()[0]
    causa.opened_at -= timedelta(seconds=600)      # fuera de ventana

    engine.ingest([senal("intelligent-document-platform", "idp-api")])

    sintoma = next(i for i in engine.open_incidents() if i.app == "intelligent-document-platform")
    assert not sintoma.is_symptom
    assert len(sink.opened) == 2


def test_no_se_suprime_contra_una_app_que_no_es_dependencia(engine: Engine, sink: MemorySink) -> None:
    """Dos fallos simultaneos sin relacion son dos problemas."""
    engine.ingest([senal("llm-benchmark", "bench-runner")])
    engine.ingest([senal("intelligent-document-platform", "idp-api")])

    sintoma = next(i for i in engine.open_incidents() if i.app == "intelligent-document-platform")
    assert not sintoma.is_symptom


def test_un_sintoma_no_suprime_a_otro(engine: Engine) -> None:
    """Solo las CAUSAS suprimen. Si no, una cadena de sintomas se encadenaria
    y el aviso real quedaria igual de enterrado."""
    engine.ingest([senal("postgres-main", "postgres-main")])
    engine.ingest([senal("intelligent-document-platform", "idp-api")])

    idp = next(i for i in engine.open_incidents() if i.app == "intelligent-document-platform")
    assert idp.suppressed_by is not None

    # aeon-ai depende de postgres, no de la IDP: su causa debe ser postgres.
    engine.ingest([senal("aeon-ai", "control-plane")])
    aeon = next(i for i in engine.open_incidents() if i.app == "aeon-ai")
    postgres = next(i for i in engine.open_incidents() if i.app == "postgres-main")
    assert aeon.suppressed_by == postgres.id


# --- El fallo peligroso: sintomas que se quedan callados para siempre --------


def test_un_sintoma_huerfano_se_promueve_y_avisa(engine: Engine, sink: MemorySink) -> None:
    """El fallo PELIGROSO de la correlacion.

    Si la causa se resuelve pero el sintoma sigue fallando, callarlo para
    siempre convierte "esto tiene explicacion" en "esto no hace falta mirarlo".
    """
    engine.ingest([senal("postgres-main", "postgres-main")])
    engine.ingest([senal("intelligent-document-platform", "idp-api")])
    assert len(sink.opened) == 1     # solo la causa

    # Postgres se recupera: su incidente caduca por falta de senales.
    causa = next(i for i in engine.open_incidents() if i.app == "postgres-main")
    causa.last_seen_at -= timedelta(seconds=1200)

    # Pero la IDP SIGUE fallando.
    engine.ingest([senal("intelligent-document-platform", "idp-api")])
    engine.sweep()

    sintoma = next(i for i in engine.open_incidents() if i.app == "intelligent-document-platform")
    assert not sintoma.is_symptom, "el sintoma se quedo suprimido tras resolverse su causa"
    assert len(sink.opened) == 2, "el sintoma promovido nunca aviso"
    assert engine.stats["symptoms_promoted"] == 1


def test_el_sintoma_promovido_conserva_su_historial(engine: Engine, sink: MemorySink) -> None:
    """No se abre un incidente nuevo: se libera el que ya existia, con su cuenta."""
    engine.ingest([senal("postgres-main", "postgres-main")])
    for _ in range(5):
        engine.ingest([senal("intelligent-document-platform", "idp-api")])

    causa = next(i for i in engine.open_incidents() if i.app == "postgres-main")
    causa.last_seen_at -= timedelta(seconds=1200)
    engine.sweep()

    promovido = sink.opened[-1]
    assert promovido.app == "intelligent-document-platform"
    assert promovido.count == 5


# --- El grafo ----------------------------------------------------------------


def test_las_dependencias_son_transitivas(registry) -> None:
    """Las cadenas reales lo son, y un fallo dos saltos arriba explica igual."""
    assert "postgres-main" in registry.upstream_of("intelligent-document-platform")


def test_la_profundidad_esta_acotada(registry) -> None:
    """Mas alla de tres saltos, "A depende de B" se vuelve "todo depende de todo"."""
    app = registry.get("intelligent-document-platform")
    app.depends_on = ["a"]
    registry._apps["a"] = type(app)(id="a", depends_on=["b"])
    registry._apps["b"] = type(app)(id="b", depends_on=["c"])
    registry._apps["c"] = type(app)(id="c", depends_on=["d"])

    alcanzables = registry.upstream_of("intelligent-document-platform", max_depth=2)
    assert alcanzables == ["a", "b"]


def test_un_ciclo_en_el_grafo_no_cuelga(registry) -> None:
    """Los registros escritos a mano acaban teniendo ciclos."""
    app = registry.get("intelligent-document-platform")
    app.depends_on = ["ciclo"]
    registry._apps["ciclo"] = type(app)(id="ciclo", depends_on=["intelligent-document-platform"])

    assert registry.upstream_of("intelligent-document-platform") == ["ciclo"]
