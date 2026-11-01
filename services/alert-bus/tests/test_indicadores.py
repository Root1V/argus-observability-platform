"""B-48: los indicadores, y sobre todo que no restan horas de relojes distintos."""

from __future__ import annotations

from alert_bus.indicadores import calcular, informe, tiempos


def _doc(i: str, **campos) -> dict:
    base = {"id": i, "severity": "page", "count": 3, "signature": "503", "kind": "error", "opened_at": "2026-11-01T10:00:00+00:00",
            "notified_at": "2026-11-01T10:00:01+00:00", "suppressed_by": None}
    return {**base, **campos}


def test_mtta_y_diagnostico_con_el_reloj_del_alert_bus() -> None:
    docs = [
        _doc("a", acknowledged_at="2026-11-01T10:02:01+00:00", enriched_at="2026-11-01T10:00:11+00:00"),
        _doc("b", acknowledged_at="2026-11-01T10:06:01+00:00"),
        _doc("c"),
    ]
    ind = calcular(docs, {})
    assert ind["mtta"] == {"n": 2, "de": 3, "mediana_s": 240.0, "p90_s": 360.0}
    assert ind["hasta_diagnostico"]["mediana_s"] == 10.0
    assert ind["volumen"]["senales"] == 9


def test_el_emisor_adelantado_no_entra_en_el_mttd_y_se_cuenta() -> None:
    """B-34: en 523 de 1.966 incidentes `opened_at` del emisor va despues de `notified_at`."""
    docs = [_doc("a", opened_at="2026-11-01T10:00:00+00:00"), _doc("b", opened_at="2026-11-01T10:05:00+00:00")]
    eventos = {
        "a": [{"tipo": "abierto", "cuando": "2026-11-01T10:00:02+00:00"},
              {"tipo": "notificado", "cuando": "2026-11-01T10:00:02.100000+00:00"}],
        "b": [{"tipo": "abierto", "cuando": "2026-11-01T10:00:02+00:00"}],
    }
    ind = calcular(docs, eventos)
    assert ind["mttd_caliente"]["n"] == 1 and ind["mttd_caliente"]["mediana_s"] == 2.0
    assert ind["mttd_caliente"]["desfases_de_reloj"] == 1
    assert ind["hasta_notificar"]["mediana_s"] == 0.1


def test_el_camino_templado_se_mide_con_startsat_y_no_consigo_mismo() -> None:
    """Antes daba 0 ms: `opened_at` de una alerta de vmalert ES su recepcion."""
    docs = [_doc("a", kind="burn_rate", opened_at="2026-11-01T10:00:02+00:00",
                 attributes={"starts_at": "2026-11-01T09:58:02+00:00"}),
            _doc("b", kind="burn_rate", opened_at="2026-11-01T10:00:02+00:00")]
    eventos = {i: [{"tipo": "abierto", "cuando": "2026-11-01T10:00:02+00:00"}] for i in ("a", "b")}
    ind = calcular(docs, eventos)
    assert ind["mttd_templado"]["n"] == 1 and ind["mttd_templado"]["mediana_s"] == 120.0
    assert ind["mttd_caliente"]["n"] == 0, "una alerta templada no es deteccion del camino caliente"


def test_las_pruebas_no_cuentan() -> None:
    docs = [_doc("a"), _doc("b", signature="PruebaBotonesB46", feedback={"util": False}),
            _doc("c", signature="503", attributes={"labels": {"argus_prueba": "true"}})]
    ind = calcular(docs, {})
    assert ind["incidentes"] == 1 and ind["pruebas_excluidas"] == 2
    assert ind["precision_alertas"]["de"] == 0


def test_un_incidente_de_una_senal_dura_cero() -> None:
    docs = [_doc("a", notified_at="2026-11-01T10:00:01.050+00:00", last_seen_at="2026-11-01T10:00:01+00:00")]
    assert calcular(docs, {})["duracion_visible_page"] == {"n": 1, "de": 1, "mediana_s": 0.0, "p90_s": 0.0}


def test_precision_y_acierto_salen_del_feedback() -> None:
    docs = [
        _doc("a", feedback={"util": True, "causa": "correcta"}, root_cause="x"),
        _doc("b", feedback={"util": False, "causa": "incorrecta", "causa_real": "y"}, root_cause="x"),
        _doc("c", feedback={"util": True}),
        _doc("d"),
    ]
    ind = calcular(docs, {})
    assert ind["precision_alertas"] == {"si": 2, "de": 3, "pct": 66.7}
    assert ind["acierto_causa"] == {"si": 1, "de": 2, "pct": 50.0}
    assert ind["feedback_cobertura"]["de"] == 4


def test_sin_datos_lo_dice_en_vez_de_inventar_un_cero() -> None:
    assert tiempos([], de=5) == {"n": 0, "de": 5}
    texto = informe(calcular([_doc("a")], {}), 7)
    assert "MTTA" in texto and "sin datos (0 de 1)" in texto


def test_los_sintomas_no_cuentan_como_page_a_reconocer() -> None:
    docs = [_doc("a"), _doc("b", suppressed_by="a")]
    assert calcular(docs, {})["mtta"]["de"] == 1


def test_api_de_indicadores(registry, tmp_path) -> None:
    from alert_bus.app import create_app
    from alert_bus.config import Settings
    from alert_bus.engine import Engine
    from alert_bus.sinks import MemorySink
    from alert_bus.store import IncidentStore
    from argus_schemas import Signal, SignalKind
    from fastapi.testclient import TestClient

    e = Engine(registry, [MemorySink()], store=IncidentStore(tmp_path / "i.sqlite"))
    e.ingest([Signal(kind=SignalKind.ERROR, app="intelligent-document-platform", component="idp-api",
                     signature="503", title="falla")])
    e.acknowledge(e.open_incidents()[0].id)
    r = TestClient(create_app(Settings(), engine=e)).get("/indicadores?dias=7").json()
    assert r["incidentes"] == 1 and r["mtta"]["n"] == 1 and r["con_eventos"] == 1
