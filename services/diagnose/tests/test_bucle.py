"""El bucle: a quien se diagnostica, a quien no, y cuando se vuelve.

Lo que decide `_toca` es donde se gasta el dinero. Todo lo demas de este
servicio es gratis.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from diagnose.bucle import Diagnosticador
from diagnose.config import Settings
from diagnose.expediente import Expediente
from diagnose.juicio import Veredicto


class JuezQuieto:
    """Devuelve siempre lo mismo, y cuenta cuantas veces se le pregunto."""

    nombre = "quieto"

    def __init__(self, veredicto: Veredicto | None = None) -> None:
        self.veredicto = veredicto or Veredicto(root_cause="porque si", confidence="alta")
        self.veces = 0

    def juzgar(self, expediente: Expediente) -> Veredicto:
        self.veces += 1
        return self.veredicto


def _bus(incidentes: list[dict[str, Any]], enriquecidos: list[tuple[str, dict]]) -> httpx.Client:
    """Un alert-bus de mentira que anota los `enrich` que recibe."""

    def responder(peticion: httpx.Request) -> httpx.Response:
        if peticion.url.path == "/incidents":
            return httpx.Response(200, json=incidentes)
        if peticion.url.path == "/registry":
            return httpx.Response(200, json=[])
        if peticion.url.path == "/incidents/history":
            return httpx.Response(200, json=[])
        if peticion.url.path.endswith("/enrich"):
            huella = peticion.url.path.split("/")[2]
            import json as _json

            enriquecidos.append((huella, _json.loads(peticion.content)))
            return httpx.Response(200, json={"status": "enriched"})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(responder))


def _inc(**campos) -> dict[str, Any]:
    base = {
        "id": "i1", "fingerprint": "h1", "app": "a", "component": "c", "signature": "502",
        "kind": "error", "severity": "page", "state": "open", "count": 1,
        "root_cause": None, "suppressed_by": None, "trace_ids": [],
        "opened_at": datetime.now(UTC).isoformat(), "last_seen_at": datetime.now(UTC).isoformat(),
    }
    return {**base, **campos}


def _diag(incidentes, enriquecidos, juez=None, **ajustes):
    settings = Settings(alertbus_url="http://bus", **ajustes)
    return Diagnosticador(
        settings, almacen=None, juez=juez or JuezQuieto(), cliente=_bus(incidentes, enriquecidos)
    )


def test_un_incidente_sin_causa_se_diagnostica_y_el_resultado_vuelve() -> None:
    enriquecidos: list[tuple[str, dict]] = []
    d = _diag([_inc()], enriquecidos)

    resultados = d.ciclo()

    assert len(resultados) == 1
    assert enriquecidos == [("h1", {"confidence": "alta", "evidence": [], "root_cause": "porque si"})]
    assert d.stats["con_causa"] == 1


def test_no_se_diagnostica_lo_que_ya_tiene_causa() -> None:
    enriquecidos: list[tuple[str, dict]] = []
    juez = JuezQuieto()
    d = _diag([_inc(root_cause="ya lo sabemos")], enriquecidos, juez)

    assert d.ciclo() == []
    assert juez.veces == 0


def test_no_se_diagnostica_un_sintoma() -> None:
    """Su causa tiene su propio incidente, y es ahi donde el diagnostico sirve.

    Diagnosticar los dos es pagar dos veces por una respuesta — la misma razon
    por la que un sintoma tampoco se notifica.
    """
    enriquecidos: list[tuple[str, dict]] = []
    juez = JuezQuieto()
    d = _diag([_inc(suppressed_by="otro-incidente")], enriquecidos, juez)

    assert d.ciclo() == []
    assert juez.veces == 0


def test_no_se_diagnostica_lo_resuelto() -> None:
    enriquecidos: list[tuple[str, dict]] = []
    juez = JuezQuieto()
    d = _diag([_inc(state="resolved")], enriquecidos, juez)

    assert d.ciclo() == []
    assert juez.veces == 0


def test_uno_sin_causa_no_se_rediagnostica_en_el_ciclo_siguiente() -> None:
    """Sin espera, un incidente que las reglas no explican se juzga cada 10 s para siempre."""
    enriquecidos: list[tuple[str, dict]] = []
    juez = JuezQuieto(Veredicto(root_cause=None, confidence="baja"))
    d = _diag([_inc()], enriquecidos, juez)

    d.ciclo()
    d.ciclo()
    d.ciclo()

    assert juez.veces == 1
    assert d.stats["saltados_por_espera"] == 2


def test_si_el_incidente_crece_al_doble_se_vuelve_antes_de_la_espera() -> None:
    """Mas senales es mas evidencia: puede que ahora si se explique."""
    enriquecidos: list[tuple[str, dict]] = []
    juez = JuezQuieto(Veredicto(root_cause=None, confidence="baja"))
    incidentes = [_inc(count=5)]
    d = _diag(incidentes, enriquecidos, juez)

    d.ciclo()
    incidentes[0]["count"] = 10
    d.ciclo()

    assert juez.veces == 2


def test_pasada_la_espera_se_vuelve_a_intentar() -> None:
    enriquecidos: list[tuple[str, dict]] = []
    juez = JuezQuieto(Veredicto(root_cause=None, confidence="baja"))
    d = _diag([_inc()], enriquecidos, juez, reintentar_tras_s=1)

    d.ciclo()
    d._juzgados["h1"] = (datetime.now(UTC) - timedelta(seconds=5), 1)
    d.ciclo()

    assert juez.veces == 2


def test_el_tope_por_ciclo_acota_las_consultas() -> None:
    """Una tormenta de incidentes nuevos no puede ser una tormenta de consultas.

    El almacen es el mismo del que depende el resto de la plataforma para
    mirar: saturarlo diagnosticando seria romper la herramienta con la que se
    arregla.
    """
    enriquecidos: list[tuple[str, dict]] = []
    juez = JuezQuieto()
    muchos = [_inc(id=f"i{n}", fingerprint=f"h{n}") for n in range(10)]
    d = _diag(muchos, enriquecidos, juez, max_por_ciclo=3)

    assert len(d.ciclo()) == 3
    assert juez.veces == 3


def test_un_alert_bus_caido_no_tumba_el_bucle() -> None:
    def muerto(peticion: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no hay nadie")

    d = Diagnosticador(
        Settings(alertbus_url="http://bus"),
        almacen=None,
        juez=JuezQuieto(),
        cliente=httpx.Client(transport=httpx.MockTransport(muerto)),
    )

    assert d.ciclo() == []


def test_un_enrich_que_falla_se_cuenta_pero_el_diagnostico_existe() -> None:
    """El expediente y el veredicto estan en el log; lo que falto fue llegar."""

    def responder(peticion: httpx.Request) -> httpx.Response:
        if peticion.url.path == "/incidents":
            return httpx.Response(200, json=[_inc()])
        if peticion.url.path.endswith("/enrich"):
            return httpx.Response(500, json={"detail": "boom"})
        return httpx.Response(200, json=[])

    d = Diagnosticador(
        Settings(alertbus_url="http://bus"),
        almacen=None,
        juez=JuezQuieto(),
        cliente=httpx.Client(transport=httpx.MockTransport(responder)),
    )
    resultados = d.ciclo()

    assert len(resultados) == 1
    assert d.stats["fallidos"] == 1


# --- El juez con modelo, en sombra -------------------------------------------


class JuezQueRevienta:
    nombre = "revienta"

    def juzgar(self, expediente):
        raise RuntimeError("la red se fue")


def test_la_sombra_juzga_pero_el_aviso_lleva_el_veredicto_de_las_reglas(tmp_path) -> None:
    """F4-03: el modelo no escribe en el movil de nadie hasta que el banco lo respalde."""
    import json

    enriquecidos: list[tuple[str, dict]] = []
    reglas = JuezQuieto(Veredicto(root_cause="dijo la regla", confidence="media"))
    modelo = JuezQuieto(Veredicto(root_cause="dijo el modelo", confidence="alta", origen="modelo:x"))
    ruta = tmp_path / "sombra.jsonl"
    d = Diagnosticador(
        Settings(alertbus_url="http://bus", sombra_path=str(ruta)),
        almacen=None, juez=reglas, sombra=modelo, cliente=_bus([_inc()], enriquecidos),
    )
    d.ciclo()

    assert enriquecidos[0][1]["root_cause"] == "dijo la regla"
    assert modelo.veces == 1
    fila = json.loads(ruta.read_text(encoding="utf-8").splitlines()[0])
    assert fila["reglas"]["root_cause"] == "dijo la regla"
    assert fila["modelo"]["root_cause"] == "dijo el modelo"


def test_una_sombra_que_revienta_no_le_cuesta_nada_al_diagnostico() -> None:
    enriquecidos: list[tuple[str, dict]] = []
    d = Diagnosticador(
        Settings(alertbus_url="http://bus"),
        almacen=None, juez=JuezQuieto(), sombra=JuezQueRevienta(), cliente=_bus([_inc()], enriquecidos),
    )
    resultados = d.ciclo()

    assert len(resultados) == 1
    assert enriquecidos and enriquecidos[0][1]["root_cause"] == "porque si"
    assert d.stats["sombra_fallidos"] == 1


def test_l4_en_sombra_investiga_pero_el_aviso_solo_lleva_las_reglas(tmp_path) -> None:
    """F4-03 para L4: lo que el triage deja pasar se investiga, y el aviso no se entera."""
    import json

    from diagnose.investigador import Resultado
    from diagnose.sombra_l4 import SombraL4

    enriquecidos: list[tuple[str, dict]] = []
    ruta = tmp_path / "sombra-l4.jsonl"
    investigadas: list[str] = []

    def investigar(huella: str, run_id: str) -> Resultado:
        investigadas.append(huella)
        return Resultado(huella=huella, causa="dijo L4", confianza="alta", origen="l4:x")

    l4 = SombraL4(investigar, ruta=str(ruta), por_hora=10)
    d = Diagnosticador(
        Settings(alertbus_url="http://bus"), almacen=None,
        juez=JuezQuieto(Veredicto(root_cause=None)), investigacion=l4, cliente=_bus([_inc()], enriquecidos),
    )
    d.ciclo()
    l4.esperar(timeout=5)

    assert investigadas == ["h1"]
    assert len(enriquecidos) == 1 and "root_cause" not in enriquecidos[0][1]
    assert json.loads(ruta.read_text().splitlines()[0])["l4"]["causa"] == "dijo L4"
    assert d.stats["triage_investigar"] == 1


def test_la_sombra_juzga_cada_incidente_una_vez_y_con_tope_por_hora(tmp_path) -> None:
    """El bucle rediagnostica el mismo incidente cada vez que cambian sus
    senales; con el 27B, cada juicio de la sombra es un minuto (F5-02)."""
    import json

    modelo = JuezQuieto(Veredicto(root_cause="dijo el modelo", confidence="alta", origen="modelo:x"))
    modelo.ultimo = {"entrada": "EXPEDIENTE ...", "salida": {"causa": "x"}, "error": None}
    ruta = tmp_path / "sombra.jsonl"
    d = Diagnosticador(
        Settings(alertbus_url="http://bus", sombra_path=str(ruta), sombra_por_hora=2),
        almacen=None, juez=JuezQuieto(), sombra=modelo,
        cliente=_bus([_inc(fingerprint="h1"), _inc(id="i2", fingerprint="h2"), _inc(id="i3", fingerprint="h3")], []),
    )
    for _ in range(3):
        d._juzgados.clear()
        d.ciclo()

    assert modelo.veces == 2
    assert d.stats["sombra_por_ritmo"] >= 1
    assert d.stats["sombra_repetidos"] >= 1
    fila = json.loads(ruta.read_text(encoding="utf-8").splitlines()[0])
    assert fila["llm"]["entrada"] == "EXPEDIENTE ..."
