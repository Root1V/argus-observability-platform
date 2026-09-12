"""El motor: deduplicacion, severidad y divulgacion progresiva.

Estos tests son la definicion operativa de "sin ruido": si fallan, la
plataforma manda spam y la gente deja de mirarla.
"""

from __future__ import annotations

from datetime import timedelta

from alert_bus.engine import Engine
from alert_bus.sinks import FailingSink, MemorySink
from argus_schemas import Severity, Signal, SignalKind


def senal(**kwargs) -> Signal:
    base = {
        "kind": SignalKind.ERROR,
        "app": "intelligent-document-platform",
        "component": "idp-ocr",
        "signature": "timeout",
        "title": "timeout en idp-ocr",
        "trace_id": "a" * 32,
    }
    return Signal(**{**base, **kwargs})


# --- Deduplicacion -----------------------------------------------------------


def test_diez_fallos_identicos_producen_una_notificacion(engine: Engine, sink: MemorySink) -> None:
    """El comportamiento que define "sin fatiga de alertas".

    Diez errores iguales son UN incidente con cuenta diez, no diez avisos.
    """
    for i in range(10):
        engine.ingest([senal(trace_id=f"{i:032x}")])

    assert len(sink.opened) == 1
    assert sink.opened[0].count == 10
    assert engine.stats["signals_deduplicated"] == 9


def test_la_huella_ignora_el_trace_id(engine: Engine) -> None:
    """Si el `trace_id` entrara en la huella, cada error seria un incidente.

    Por eso la huella se construye solo con campos de cardinalidad CERRADA.
    """
    a = senal(trace_id="1" * 32)
    b = senal(trace_id="2" * 32)
    assert a.fingerprint() == b.fingerprint()


def test_errores_distintos_son_incidentes_distintos(engine: Engine, sink: MemorySink) -> None:
    engine.ingest([senal(signature="timeout")])
    engine.ingest([senal(signature="backend-unavailable")])
    assert len(sink.opened) == 2


def test_componentes_distintos_son_incidentes_distintos(engine: Engine, sink: MemorySink) -> None:
    """Un fallo en el OCR y otro en la API son dos problemas, no uno."""
    engine.ingest([senal(component="idp-ocr")])
    engine.ingest([senal(component="idp-api")])
    assert len(sink.opened) == 2


def test_solo_se_guarda_una_muestra_de_trazas(engine: Engine, sink: MemorySink) -> None:
    """El informe necesita ejemplos, no el conjunto entero."""
    for i in range(50):
        engine.ingest([senal(trace_id=f"{i:032x}")])
    assert len(sink.opened[0].trace_ids) == 10


# --- Severidad ---------------------------------------------------------------


def test_un_error_pagina_de_inmediato(engine: Engine, sink: MemorySink) -> None:
    """Esperar a agrupar mata el tiempo real. Un `page` sale al nacer."""
    engine.ingest([senal()])
    assert len(sink.opened) == 1
    assert sink.opened[0].severity is Severity.PAGE
    assert sink.opened[0].notified_at is not None


def test_la_criticidad_baja_acota_la_severidad(engine: Engine, sink: MemorySink) -> None:
    """Un laboratorio no despierta a nadie por mucho que falle."""
    engine.ingest([senal(app="llm-benchmark", component="bench-runner")])
    assert sink.opened == []          # no es `page`, espera su ventana
    abiertos = engine.open_incidents()
    assert abiertos[0].severity is Severity.INFO


def test_un_slo_superado_es_ticket_no_page(engine: Engine, sink: MemorySink) -> None:
    """Lento no es lo mismo que roto."""
    engine.ingest([senal(kind=SignalKind.SLO_BREACH, signature="ocr.extract")])
    assert sink.opened == []
    assert engine.open_incidents()[0].severity is Severity.TICKET


def test_la_severidad_puede_subir_pero_nunca_bajar(engine: Engine) -> None:
    """Que un problema se repita no lo hace menos grave."""
    engine.ingest([senal(kind=SignalKind.SLO_BREACH, signature="mismo")])
    incidente = engine.open_incidents()[0]
    assert incidente.severity is Severity.TICKET

    # Misma huella exige mismo `kind`; forzamos la subida directamente.
    incidente.severity = Severity.PAGE
    engine.ingest([senal(kind=SignalKind.SLO_BREACH, signature="mismo")])
    assert engine.open_incidents()[0].severity is Severity.PAGE


# --- Divulgacion progresiva (D-015) ------------------------------------------


def test_el_incidente_se_enriquece_actualizando_el_mismo_hilo(engine: Engine, sink: MemorySink) -> None:
    """La tercera fase: el agente edita el mensaje que ya salio.

    Sin esto, tiempo real significa mandar un aviso vacio y luego otro con el
    informe: dos notificaciones para un solo problema.
    """
    engine.ingest([senal()])
    huella = sink.opened[0].fingerprint
    assert len(sink.opened) == 1
    assert sink.updated == []

    engine.enrich(
        huella,
        root_cause="El despliegue a9f3c21 bajo el timeout de 30s a 5s",
        confidence="alta",
        evidence=["1.247 spans ocr.extract con status=ERROR desde 14:32"],
        similar_incidents=["#47 (12 mar): mismo patron tras bajar un timeout"],
    )

    # UN mensaje nuevo en total, y una actualizacion del existente.
    assert len(sink.opened) == 1
    assert len(sink.updated) == 1
    assert sink.updated[0].root_cause.startswith("El despliegue")
    assert sink.updated[0].similar_incidents


def test_el_thread_key_es_estable_para_la_misma_huella(engine: Engine, sink: MemorySink) -> None:
    """Es lo que permite a Google Chat editar en vez de duplicar."""
    engine.ingest([senal()])
    clave = sink.opened[0].thread_key
    assert clave and clave.endswith(sink.opened[0].fingerprint)


def test_enriquecer_un_incidente_inexistente_no_revienta(engine: Engine) -> None:
    assert engine.enrich("huella-que-no-existe", root_cause="x") is None


# --- Ventana de agrupacion ---------------------------------------------------


def test_las_severidades_menores_esperan_su_ventana(engine: Engine, sink: MemorySink) -> None:
    engine.ingest([senal(kind=SignalKind.SLO_BREACH, signature="lento")])
    assert sink.opened == []

    # Envejecemos el incidente mas alla de la ventana.
    incidente = engine.open_incidents()[0]
    incidente.opened_at -= timedelta(seconds=120)

    listos = engine.flush_grouped()
    assert len(listos) == 1
    assert len(sink.opened) == 1


def test_los_info_nunca_se_notifican_en_caliente(engine: Engine, sink: MemorySink) -> None:
    """Solo digest diario: interrumpir por un `info` es ruido por definicion."""
    engine.ingest([senal(app="llm-benchmark", component="bench-runner")])
    incidente = engine.open_incidents()[0]
    incidente.opened_at -= timedelta(seconds=600)
    assert engine.flush_grouped() == []
    assert sink.opened == []


# --- Resiliencia -------------------------------------------------------------


def test_un_sink_caido_no_impide_que_los_demas_reciban(registry, sink: MemorySink) -> None:
    """Perder un canal es malo; perder la alerta entera es peor."""
    engine = Engine(registry, [FailingSink(), sink])
    engine.ingest([senal()])
    assert len(sink.opened) == 1


def test_los_incidentes_inactivos_se_cierran(engine: Engine) -> None:
    """Sin esto, el mismo problema dos semanas despues no generaria aviso."""
    engine.ingest([senal()])
    incidente = engine.open_incidents()[0]
    incidente.last_seen_at -= timedelta(seconds=1200)

    assert engine.sweep() == 1
    assert engine.open_incidents() == []


def test_reabrir_tras_cerrar_genera_un_incidente_nuevo(engine: Engine, sink: MemorySink) -> None:
    engine.ingest([senal()])
    engine.open_incidents()[0].last_seen_at -= timedelta(seconds=1200)
    engine.sweep()

    engine.ingest([senal()])
    assert len(sink.opened) == 2


def test_la_memoria_esta_acotada(registry, sink: MemorySink) -> None:
    """Una tormenta de incidentes distintos no puede agotar la memoria."""
    engine = Engine(registry, [sink], max_incidents=50)
    for i in range(200):
        engine.ingest([senal(signature=f"error-{i}")])
    assert len(engine.open_incidents()) <= 51
