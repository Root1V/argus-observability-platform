"""F3-03 y la sombra de L4: que se investiga, que no, por que, y que la cola no crece sin limite."""

from __future__ import annotations

import json

from diagnose.expediente import Emision, Expediente, Hermano, Plataforma
from diagnose.investigador import Resultado
from diagnose.juicio import Veredicto
from diagnose.sombra_l4 import SombraL4
from diagnose.triage import merece_investigacion


def _exp(**campos) -> Expediente:
    base = dict(incidente_id="i", huella="h", app="centinela", componente="centinela-notifier",
                firma="TimeoutError", tipo_senal="error", severidad="page")
    return Expediente(**{**base, **campos})


SIN_CAUSA = Veredicto(root_cause=None)


def test_un_page_sin_causa_se_investiga() -> None:
    assert merece_investigacion(_exp(), SIN_CAUSA) == (True, "las reglas no encontraron causa")


def test_lo_que_no_se_investiga_dice_por_que() -> None:
    casos = [
        (_exp(severidad="ticket"), SIN_CAUSA, "solo los `page`"),
        (_exp(plataforma=Plataforma(consultada=True, minutos_con_datos=10)), SIN_CAUSA, "la plataforma entera"),
        (_exp(emision=Emision(consultada=True)), SIN_CAUSA, "nunca ha emitido"),
        (_exp(), Veredicto(root_cause="x", confidence="alta"), "confianza alta"),
        (_exp(hermanos=[Hermano(componente="centinela-api", firma="TimeoutError")]), SIN_CAUSA, "centinela-api"),
    ]
    for exp, v, motivo in casos:
        si, por_que = merece_investigacion(exp, v)
        assert not si and motivo in por_que, por_que


def test_de_los_hermanos_se_investiga_uno_y_todos_eligen_el_mismo() -> None:
    a = _exp(componente="centinela-api", hermanos=[Hermano(componente="centinela-notifier", firma="TimeoutError")])
    b = _exp(componente="centinela-notifier", hermanos=[Hermano(componente="centinela-api", firma="TimeoutError")])

    assert merece_investigacion(a, SIN_CAUSA)[0] is True
    assert merece_investigacion(b, SIN_CAUSA)[0] is False


def test_la_sombra_de_l4_investiga_registra_y_no_repite(tmp_path) -> None:
    ruta = tmp_path / "l4.jsonl"
    vistas: list[str] = []

    def investigar(huella: str, run_id: str) -> Resultado:
        vistas.append(huella)
        return Resultado(huella=huella, causa="el Valkey compartido", confianza="media", origen="l4:doble")

    sombra = SombraL4(investigar, ruta=str(ruta))
    assert sombra.encolar(_exp(), SIN_CAUSA, "las reglas no encontraron causa")
    assert not sombra.encolar(_exp(), SIN_CAUSA, "otra vez")       # la misma huella: no
    sombra.esperar(timeout=5)

    assert vistas == ["h"]
    registro = json.loads(ruta.read_text().splitlines()[0])
    assert registro["l4"]["causa"] == "el Valkey compartido"
    assert registro["motivo"] == "las reglas no encontraron causa"
    assert sombra.stats["repetidas"] == 1


def test_lo_que_no_cabe_en_la_cola_se_cuenta_y_se_tira() -> None:
    import threading

    bloqueo = threading.Event()

    def lenta(huella: str, run_id: str) -> Resultado:
        bloqueo.wait(5)
        return Resultado(huella=huella)

    sombra = SombraL4(lenta, max_cola=1)
    resultados = [sombra.encolar(_exp(huella=f"h{i}"), SIN_CAUSA, "m") for i in range(4)]
    bloqueo.set()
    sombra.esperar(timeout=5)

    assert resultados.count(True) <= 2
    assert sombra.stats["descartadas_cola_llena"] >= 2


def test_lo_pendiente_sobrevive_a_un_reinicio_y_se_retoma_con_su_run_id(tmp_path) -> None:
    """F4-01: si el proceso muere con algo en cola, al arrancar se retoma con el MISMO run_id."""
    import threading

    pendientes = str(tmp_path / "l4.sqlite")
    nunca = threading.Event()

    def colgada(huella: str, run_id: str) -> Resultado:
        nunca.wait(0.5)
        raise SystemExit  # el proceso «muere» sin terminarla: no se marca

    primera = SombraL4(colgada, pendientes=pendientes, nuevo_run_id=lambda h, e: f"run-{h}")
    primera.encolar(_exp(huella="h7"), SIN_CAUSA, "m")

    retomadas: list[tuple[str, str]] = []

    def investigar(huella: str, run_id: str) -> Resultado:
        retomadas.append((huella, run_id))
        return Resultado(huella=huella)

    segunda = SombraL4(investigar, pendientes=pendientes)
    segunda.esperar(timeout=5)

    assert retomadas == [("h7", "run-h7")]
    assert segunda.stats["retomadas"] == 1
    tercera = SombraL4(investigar, pendientes=pendientes)               # ya terminada: no se repite
    tercera.esperar(timeout=5)
    assert len(retomadas) == 1
