"""El bucle, la muestra y el almacen, sin ClickHouse."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from evaluator.almacen import SPANS_TERMINADOS, Almacen, ErrorDeAlmacen, _como_parametro
from evaluator.bucle import Cursor, Evaluador, _fecha
from evaluator.evals import Span
from evaluator.muestreo import MINIMO_PARA_P99, Muestreo, _cubeta, lineas_base

AHORA = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def _span(fin: datetime, **cambios) -> Span:
    base = Span(trace_id="t-" + fin.isoformat(), span_id="s", fin=fin.strftime("%Y-%m-%d %H:%M:%S.%f000"),
                app="veritium", componente="veritium-api", operacion="chat", modelo="m", modelo_pedido="m",
                finish_reasons='["stop"]', tokens_salida="10")
    return replace(base, **cambios)


class AlmacenFalso:
    def __init__(self, spans: list[Span]) -> None:
        self.spans = spans
        self.guardadas: list = []
        self.ventanas: list = []
        self.falla_en: int | None = None

    def spans_terminados(self, desde, hasta):
        self.ventanas.append((desde, hasta))
        if self.falla_en is not None and len(self.ventanas) == self.falla_en:
            raise ErrorDeAlmacen("caido")
        return [s for s in self.spans if desde < _fecha(s.fin) <= hasta]

    def p99_por_modelo(self, hasta):
        return [{"componente": "veritium-api", "operacion": "chat", "modelo": "m", "p99": "9e9", "n": "500"}]

    def guardar(self, filas, evaluado_en):
        self.guardadas.extend(filas)


class ContadorFalso:
    def __init__(self) -> None:
        self.sumas: list[dict] = []

    def add(self, amount, attributes):
        self.sumas.append(attributes)


def _evaluador(almacen, tmp_path, **kw):
    return Evaluador(almacen, Cursor(tmp_path / "cursor.json"), reloj=lambda: AHORA, **kw)


def test_sin_cursor_rellena_y_deja_el_cursor_en_ahora_menos_el_retraso(tmp_path):
    viejo, reciente = _span(AHORA - timedelta(hours=5)), _span(AHORA - timedelta(minutes=8))
    alm = AlmacenFalso([viejo, reciente])
    ev = _evaluador(alm, tmp_path)
    assert ev.vuelta() == 2
    assert {s.trace_id for s, _, _ in alm.guardadas} == {viejo.trace_id, reciente.trace_id}
    assert ev.cursor.leer() == AHORA - timedelta(minutes=5)
    # Una segunda vuelta en el mismo instante no vuelve a mirar nada.
    alm.ventanas.clear()
    assert ev.vuelta() == 0 and alm.ventanas == []


def test_lo_que_aun_no_ha_llegado_no_se_da_por_visto(tmp_path):
    alm = AlmacenFalso([_span(AHORA - timedelta(minutes=2))])
    ev = _evaluador(alm, tmp_path)
    ev.vuelta()
    assert alm.guardadas == []
    assert ev.cursor.leer() < _fecha(alm.spans[0].fin)


def test_si_el_almacen_falla_el_cursor_no_pasa_de_la_ventana_fallida(tmp_path):
    alm = AlmacenFalso([])
    alm.falla_en = 3
    ev = _evaluador(alm, tmp_path, relleno=timedelta(hours=1))
    ev.vuelta()
    desde_fallida = alm.ventanas[2][0]
    assert ev.cursor.leer() == desde_fallida
    assert ev.stats["errores_de_almacen"] == 1


def test_las_metricas_solo_cuentan_el_presente(tmp_path):
    viejo, reciente = _span(AHORA - timedelta(hours=5)), _span(AHORA - timedelta(minutes=8))
    contador = ContadorFalso()
    _evaluador(AlmacenFalso([viejo, reciente]), tmp_path, contador=contador).vuelta()
    # Cuatro evaluaciones del reciente; el relleno va a la tabla pero no al contador.
    assert len(contador.sumas) == 4
    assert {a["argus.eval.result"] for a in contador.sumas} == {"pasa"}
    assert contador.sumas[0]["argus.app"] == "veritium"


def test_la_muestra_errores_p99_y_azar():
    m = Muestreo(porcentaje=0.0, p99={("veritium-api", "chat", "m"): 1000})
    s = _span(AHORA)
    assert m.motivo(replace(s, error=True)) == "error"
    assert m.motivo(replace(s, duracion_ns=1000)) == "p99"
    assert m.motivo(replace(s, duracion_ns=999)) == ""
    assert Muestreo(porcentaje=100.0).motivo(s) == "azar"


def test_el_azar_es_por_traza_y_ronda_el_porcentaje():
    assert _cubeta("abc") == _cubeta("abc")
    dentro = sum(Muestreo(porcentaje=5.0).motivo(_span(AHORA, trace_id=f"t{i}")) == "azar" for i in range(10_000))
    assert 400 < dentro < 600


def test_sin_suficientes_llamadas_no_hay_p99():
    filas = [{"componente": "c", "operacion": "chat", "modelo": "m", "p99": "5e9", "n": str(MINIMO_PARA_P99 - 1)},
             {"componente": "c", "operacion": "chat", "modelo": "n", "p99": "5e9", "n": str(MINIMO_PARA_P99)}]
    assert lineas_base(filas) == {("c", "chat", "n"): 5_000_000_000}


def test_fecha_con_nanosegundos():
    assert _fecha("2026-10-27 14:09:01.926187123") == datetime(2026, 10, 27, 14, 9, 1, 926187, tzinfo=UTC)
    assert _fecha("2026-10-27 14:09:01") == datetime(2026, 10, 27, 14, 9, 1, tzinfo=UTC)


def test_el_almacen_solo_lee_con_consulta():
    alm = Almacen("http://nadie:1", "", "")
    with pytest.raises(ErrorDeAlmacen, match="solo lee"):
        alm.consulta("INSERT INTO otel.otel_traces VALUES (1)")


def test_la_consulta_va_por_el_fin_del_span_y_parametrizada():
    assert "Timestamp + toIntervalNanosecond(Duration) > {desde:DateTime64(9)}" in SPANS_TERMINADOS
    assert "{hasta:DateTime64(9)}" in SPANS_TERMINADOS


def test_parametros_en_utc_y_escapados():
    assert _como_parametro(datetime(2026, 10, 27, 14, 0, tzinfo=UTC)) == "2026-10-27 14:00:00.000000"
    assert _como_parametro("a\nb") == "a\\nb"
