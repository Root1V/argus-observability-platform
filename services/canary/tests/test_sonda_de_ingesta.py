"""La sonda que comprueba que lo enviado llega al almacen.

Existe por una frase de Prometheus: *una sonda de disponibilidad que ejercita
menos superficie que el servicio declara es una sonda que certifica lo que no
probo*. Les paso con un modelo de vision que llevaba meses sin poder ver
mientras su sonda decia sano — porque una sonda de disponibilidad manda texto.

Las cuatro sondas HTTP de este canario tienen esa forma: `/healthz`, `/ping`,
`/health` y el `health_check` del Collector. Las cuatro dicen que un proceso
contesta; **ninguna dice que la telemetria llegue al almacen**, que es la unica
funcion distintiva de esta plataforma.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from canary.probes import ATRIBUTO_MARCA, EVENTO_INGESTA, Resultado, SondaDeIngesta


def _sonda(**kw) -> SondaDeIngesta:
    base = dict(
        app="argus", component="ingesta",
        clickhouse_url="http://clickhouse:8123/", usuario="argus", password="x",
    )
    return SondaDeIngesta(**{**base, **kw})


class _RespuestaFalsa:
    def __init__(self, texto: str) -> None:
        self.text = texto

    def raise_for_status(self) -> None:
        return None


def _cliente_que_devuelve(texto: str):
    """Doble del cliente HTTP. Devuelve lo que ClickHouse diria."""
    class Cliente:
        def __init__(self, *a, **k) -> None: ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return _RespuestaFalsa(texto)
    return Cliente


# --- Lo que la sonda afirma -------------------------------------------------


def test_si_el_span_llega_la_sonda_pasa() -> None:
    _reloj = _Reloj()
    s = _sonda()
    with patch.object(s, "_emitir"), \
         patch("canary.probes.httpx.AsyncClient", _cliente_que_devuelve("1\n")), \
         patch("canary.probes.asyncio.sleep", new=_reloj.dormir), \
         patch("canary.probes.time.monotonic", _reloj.monotonic):
        m = asyncio.run(s.ejecutar())
    assert m.resultado is Resultado.OK


def test_si_el_span_NO_llega_la_sonda_dice_CAIDO() -> None:
    """La propiedad entera. Si esto no falla, la sonda es decoracion."""
    _reloj = _Reloj()
    s = _sonda(plazo_s=45)
    with patch.object(s, "_emitir"), \
         patch("canary.probes.httpx.AsyncClient", _cliente_que_devuelve("0\n")), \
         patch("canary.probes.asyncio.sleep", new=_reloj.dormir), \
         patch("canary.probes.time.monotonic", _reloj.monotonic):
        m = asyncio.run(s.ejecutar())
    assert m.resultado is Resultado.CAIDO
    assert "no esta llegando al almacen" in (m.detalle or "")


def test_un_fallo_al_emitir_tambien_es_CAIDO() -> None:
    """Si no se puede ni mandar el span, el camino esta roto igual — y la sonda
    tiene que distinguirlo en el detalle, porque la reparacion es otra."""
    s = _sonda()
    def explota(_marca: str) -> None:
        raise ConnectionRefusedError("sin colector")
    with patch.object(s, "_emitir", explota):
        m = asyncio.run(s.ejecutar())
    assert m.resultado is Resultado.CAIDO
    assert "no se pudo emitir" in (m.detalle or "")


def test_un_fallo_al_consultar_no_se_confunde_con_telemetria_perdida() -> None:
    """ClickHouse inalcanzable NO es «la telemetria no llega»: es que no se
    puede saber. Confundirlos manda a buscar el fallo al sitio equivocado."""
    _reloj = _Reloj()
    s = _sonda()
    class Cliente:
        def __init__(self, *a, **k) -> None: ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): raise TimeoutError("sin almacen")
    with patch.object(s, "_emitir"), \
         patch("canary.probes.httpx.AsyncClient", Cliente), \
         patch("canary.probes.asyncio.sleep", new=_reloj.dormir), \
         patch("canary.probes.time.monotonic", _reloj.monotonic):
        m = asyncio.run(s.ejecutar())
    assert m.resultado is Resultado.CAIDO
    assert "no se pudo consultar el almacen" in (m.detalle or "")


# --- Las dos trampas que la sonda tiene que evitar --------------------------


def test_la_marca_es_UNICA_por_ejecucion() -> None:
    """Sin marca unica, una fila de hace una hora hace pasar la sonda mientras
    la ingesta esta parada — la misma sonda que certifica lo que no probo,
    dentro de la clase escrita para evitarlo."""
    _reloj = _Reloj()
    vistas: list[str] = []
    s = _sonda()
    with patch.object(s, "_emitir", lambda marca: vistas.append(marca)), \
         patch("canary.probes.httpx.AsyncClient", _cliente_que_devuelve("1\n")), \
         patch("canary.probes.asyncio.sleep", new=_reloj.dormir), \
         patch("canary.probes.time.monotonic", _reloj.monotonic):
        asyncio.run(s.ejecutar())
        asyncio.run(s.ejecutar())
    assert len(vistas) == 2
    assert vistas[0] != vistas[1], "la marca se repite entre ejecuciones"
    assert len(vistas[0]) >= 16


def test_la_consulta_filtra_POR_esa_marca() -> None:
    """Y por la marca, no solo por el evento: si filtrara por `argus.event`,
    cualquier ejecucion anterior la haria pasar."""
    _reloj = _Reloj()
    s = _sonda()
    consultas: list[str] = []

    class Cliente:
        def __init__(self, *a, **k) -> None: ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k):
            consultas.append(k.get("content") or a[1])
            return _RespuestaFalsa("1\n")

    with patch.object(s, "_emitir"), \
         patch("canary.probes.httpx.AsyncClient", Cliente), \
         patch("canary.probes.asyncio.sleep", new=_reloj.dormir), \
         patch("canary.probes.time.monotonic", _reloj.monotonic):
        asyncio.run(s.ejecutar())

    assert consultas
    assert ATRIBUTO_MARCA in consultas[0]
    import re
    assert re.search(r"= '[0-9a-f]{32}'", consultas[0]), (
        f"la consulta no filtra por una marca concreta:\n{consultas[0]}"
    )


def test_el_plazo_no_puede_bajar_del_decision_wait() -> None:
    """El muestreo del gateway espera 30 s a que la traza cierre antes de
    decidir. Un plazo mas corto convierte la sonda en un generador de falsos
    caidos, y el canario que grita por nada es el que se silencia."""
    s = _sonda(plazo_s=5)
    assert s.plazo_s >= SondaDeIngesta.ESPERA_MINIMA_S + 15


def test_el_evento_es_el_que_la_politica_de_muestreo_conserva() -> None:
    """Si estos dos se separan, la sonda cae al baseline del 10 % y reporta la
    plataforma caida nueve de cada diez veces."""
    import pathlib

    import yaml

    raiz = pathlib.Path(__file__).resolve().parents[3]
    cfg = yaml.safe_load((raiz / "platform" / "collector" / "gateway.yaml").read_text(encoding="utf-8"))
    politicas = cfg["processors"]["tail_sampling"]["policies"]
    canario = [p for p in politicas if "canario" in p["name"]]
    assert canario, "no hay politica de muestreo para la sonda de ingesta"
    condiciones = " ".join(canario[0]["ottl_condition"]["span"])
    assert EVENTO_INGESTA in condiciones, (
        f"la politica conserva otro evento que el que emite la sonda: {condiciones}"
    )
    nombres = [p["name"] for p in politicas]
    assert nombres.index(canario[0]["name"]) < nombres.index("baseline")


class _Reloj:
    """Reloj falso que avanza cuando la sonda espera.

    Parchear `asyncio.sleep` a un no-op y dejar `time.monotonic` real convierte
    el bucle de la sonda en una espera activa de 45 segundos — la suite tardaba
    eso. El tiempo tiene que AVANZAR, no desaparecer: lo que se simula es que
    pasa rapido, no que no pasa.
    """

    def __init__(self) -> None:
        self.ahora = 1000.0

    async def dormir(self, segundos: float) -> None:
        self.ahora += segundos

    def monotonic(self) -> float:
        return self.ahora
