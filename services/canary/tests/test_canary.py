"""El canario: confirmación, recuperación y el punto ciego que cierra."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest
from canary.probes import Medicion, Resultado, SondaHTTP, SondaSilencio
from canary.runner import Runner

# --- Servidor de pruebas -----------------------------------------------------


class _Servidor(BaseHTTPRequestHandler):
    # En la clase porque BaseHTTPRequestHandler se instancia por peticion y no
    # admite pasarle estado. Es el patron estandar para capturar en tests.
    estado = 200
    demora_s = 0.0
    recibidas: ClassVar[list[dict]] = []

    def do_GET(self) -> None:
        if type(self).demora_s:
            import time
            time.sleep(type(self).demora_s)
        self.send_response(type(self).estado)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self) -> None:
        largo = int(self.headers.get("Content-Length", 0))
        type(self).recibidas.append(json.loads(self.rfile.read(largo)))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def servidor():
    _Servidor.estado = 200
    _Servidor.demora_s = 0.0
    _Servidor.recibidas = []
    httpd = HTTPServer(("127.0.0.1", 0), _Servidor)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", _Servidor
    httpd.shutdown()


# --- Sonda HTTP --------------------------------------------------------------


async def test_un_endpoint_sano_da_ok(servidor) -> None:
    url, _ = servidor
    medicion = await SondaHTTP(app="a", component="c", url=f"{url}/health").ejecutar()
    assert medicion.resultado is Resultado.OK
    assert not medicion.es_problema


async def test_un_endpoint_inalcanzable_da_caido() -> None:
    """Puerto sin nadie escuchando: el caso mas comun de una app caida."""
    medicion = await SondaHTTP(app="a", component="c", url="http://127.0.0.1:1/health", timeout_s=1).ejecutar()
    assert medicion.resultado is Resultado.CAIDO


async def test_un_5xx_es_caida(servidor) -> None:
    url, srv = servidor
    srv.estado = 503
    medicion = await SondaHTTP(app="a", component="c", url=f"{url}/health").ejecutar()
    assert medicion.resultado is Resultado.CAIDO


async def test_un_404_no_es_caida(servidor) -> None:
    """Si devuelve 404 esta VIVO y la sonda apunta mal.

    Confundir "mi sonda está mal configurada" con "la aplicación se cayó"
    genera guardias inútiles, que es como se pierde la confianza en un canario.
    """
    url, srv = servidor
    srv.estado = 404
    medicion = await SondaHTTP(app="a", component="c", url=f"{url}/noexiste").ejecutar()
    assert medicion.resultado is Resultado.OK


async def test_responder_por_encima_del_slo_es_lento_no_caido(servidor) -> None:
    url, srv = servidor
    srv.demora_s = 0.15
    medicion = await SondaHTTP(app="a", component="c", url=f"{url}/health", slo_ms=50).ejecutar()
    assert medicion.resultado is Resultado.LENTO
    assert medicion.duracion_ms >= 150


# --- Sonda de silencio: el punto ciego ---------------------------------------


async def test_sin_telemetria_reciente_es_silencio(monkeypatch) -> None:
    """El punto ciego que ningún sistema basado solo en lo que llega puede ver:
    una app caída simplemente no emite, y eso es indistinguible de «no hay
    tráfico»."""
    import httpx

    class RespuestaFalsa:
        text = "0"
        def raise_for_status(self): ...

    class ClienteFalso:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return RespuestaFalsa()

    monkeypatch.setattr(httpx, "AsyncClient", ClienteFalso)

    sonda = SondaSilencio(app="a", component="c", clickhouse_url="http://x", usuario="u", password="p")
    medicion = await sonda.ejecutar()
    assert medicion.resultado is Resultado.SILENCIO


async def test_con_telemetria_reciente_da_ok(monkeypatch) -> None:
    import httpx

    class RespuestaFalsa:
        text = "1247"
        def raise_for_status(self): ...

    class ClienteFalso:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return RespuestaFalsa()

    monkeypatch.setattr(httpx, "AsyncClient", ClienteFalso)

    sonda = SondaSilencio(app="a", component="c", clickhouse_url="http://x", usuario="u", password="p")
    medicion = await sonda.ejecutar()
    assert medicion.resultado is Resultado.OK
    assert "1247" in medicion.detalle


async def test_si_el_almacen_no_responde_no_se_inventa_un_incidente(monkeypatch) -> None:
    """No poder consultar significa «no lo sabemos», no «está caída».

    Decir que está caída sería inventarse un incidente, que es peor que no
    detectarlo: destruye la confianza en todo lo demás que reporta el canario.
    """
    import httpx

    class ClienteFalso:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): raise ConnectionError("sin ruta")

    monkeypatch.setattr(httpx, "AsyncClient", ClienteFalso)

    sonda = SondaSilencio(app="a", component="c", clickhouse_url="http://x", usuario="u", password="p")
    medicion = await sonda.ejecutar()
    assert medicion.resultado is Resultado.OK
    assert "inaccesible" in medicion.detalle


# --- Confirmación y recuperación ---------------------------------------------


class SondaFija:
    """Sonda con resultado controlado, para probar el planificador sin esperas."""

    def __init__(self, resultado: Resultado = Resultado.OK) -> None:
        self.resultado = resultado

    async def ejecutar(self) -> Medicion:
        return Medicion(objetivo="a/c", app="a", component="c", resultado=self.resultado)


async def test_un_solo_fallo_no_alerta(servidor) -> None:
    """Las redes tienen microcortes. Un canario que grita por cada hipo es un
    canario que acaba silenciado."""
    url, srv = servidor
    sonda = SondaFija(Resultado.CAIDO)
    runner = Runner([sonda], alertbus_url=url, fallos_para_alertar=2)

    await runner.ronda()
    assert srv.recibidas == []
    assert runner.stats["alertas"] == 0


async def test_dos_fallos_consecutivos_si_alertan(servidor) -> None:
    url, srv = servidor
    runner = Runner([SondaFija(Resultado.CAIDO)], alertbus_url=url, fallos_para_alertar=2)

    await runner.ronda()
    await runner.ronda()

    assert len(srv.recibidas) == 1
    alerta = srv.recibidas[0]["alerts"][0]
    assert alerta["labels"]["service_namespace"] == "a"
    assert alerta["labels"]["source"] == "canary"
    assert "no responde" in alerta["annotations"]["summary"]


async def test_un_fallo_intermitente_reinicia_la_cuenta(servidor) -> None:
    """Fallo, recuperación, fallo NO son dos fallos consecutivos."""
    url, srv = servidor
    sonda = SondaFija(Resultado.CAIDO)
    runner = Runner([sonda], alertbus_url=url, fallos_para_alertar=2)

    await runner.ronda()
    sonda.resultado = Resultado.OK
    await runner.ronda()
    sonda.resultado = Resultado.CAIDO
    await runner.ronda()

    assert srv.recibidas == []


async def test_no_repite_la_alerta_mientras_siga_caido(servidor) -> None:
    """El alert-bus deduplica igual, pero no tiene sentido mandarle la misma
    señal cada cinco minutos para siempre."""
    url, srv = servidor
    runner = Runner([SondaFija(Resultado.CAIDO)], alertbus_url=url, fallos_para_alertar=1)

    for _ in range(5):
        await runner.ronda()

    assert len(srv.recibidas) == 1


async def test_la_recuperacion_se_registra(servidor) -> None:
    """Un incidente que se resuelve solo tiene que cerrarse, o la lista de
    abiertos deja de ser útil en cuestión de días."""
    url, _ = servidor
    sonda = SondaFija(Resultado.CAIDO)
    runner = Runner([sonda], alertbus_url=url, fallos_para_alertar=1)

    await runner.ronda()
    assert runner.stats["alertas"] == 1

    sonda.resultado = Resultado.OK
    await runner.ronda()
    assert runner.stats["recuperaciones"] == 1

    # Y si vuelve a caer, vuelve a alertar.
    sonda.resultado = Resultado.CAIDO
    await runner.ronda()
    assert runner.stats["alertas"] == 2


async def test_una_sonda_que_revienta_no_tumba_la_ronda(servidor) -> None:
    """Un fallo de la sonda no es un fallo de la aplicación."""
    url, _ = servidor

    class SondaRota:
        async def ejecutar(self):
            raise RuntimeError("la sonda está mal escrita")

    runner = Runner([SondaRota(), SondaFija(Resultado.OK)], alertbus_url=url)
    mediciones = await runner.ronda()

    assert len(mediciones) == 1
    assert runner.stats["sondas_ok"] == 1


async def test_si_el_alertbus_no_responde_no_revienta() -> None:
    """Lo único peor que fallar es fallar en silencio: sale por el log."""
    runner = Runner([SondaFija(Resultado.CAIDO)], alertbus_url="http://127.0.0.1:1", fallos_para_alertar=1, timeout_s=1)
    await runner.ronda()
    assert runner.stats["alertas"] == 1


def test_un_componente_sin_conectar_no_genera_sonda_de_silencio(tmp_path) -> None:
    """Vigilar el silencio de algo que nunca ha emitido es alertar de lo sabido.

    El piloto de Prometheus conecto `auth-service` y dejo `gateway` y el
    `manager` declarados para despues. Con la aplicacion ya `activa`, sin esto
    el canario abriria tres incidentes el primer minuto — y asi es como se
    ensena a la guardia a ignorar al canario.
    """
    import yaml

    from canary.build import cargar
    from canary.config import Settings
    from canary.probes import SondaSilencio

    registro = tmp_path / "apps.yaml"
    registro.write_text(yaml.safe_dump([{
        "id": "prometheus-inference-platform",
        "estado": "activo",
        "componentes": [
            {"id": "auth-service", "rol": "api"},
            {"id": "gateway", "rol": "api", "estado": "planificado"},
        ],
    }], allow_unicode=True), encoding="utf-8")

    sondas = cargar(Settings(
        registry_path=registro,
        probes_path=tmp_path / "no-hay-sondas-http.yaml",
    ))
    vigilados = {s.component for s in sondas if isinstance(s, SondaSilencio)}

    assert vigilados == {"auth-service"}


async def test_la_sonda_mide_crecimiento_y_no_cuenta_puntos(monkeypatch) -> None:
    """Contar puntos hace que un servicio muerto parezca sano.

    Lo demostro el piloto: `auth-service` llevaba tres horas sin emitir y la
    sonda veia 1.080 puntos en quince minutos. Eran la misma serie del
    connector `spanmetrics`, que es ACUMULATIVA y reexporta su valor en cada
    intervalo aunque no pase nada.

    Esta prueba fija la forma de la consulta, porque el fallo estaba ahi y no
    en la logica: `count()` no puede volver.
    """
    import httpx

    consultas: list[str] = []

    class RespuestaFalsa:
        text = "0"
        def raise_for_status(self): ...

    class ClienteFalso:
        def __init__(self, **kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, content, headers):
            consultas.append(content)
            return RespuestaFalsa()

    monkeypatch.setattr(httpx, "AsyncClient", ClienteFalso)

    sonda = SondaSilencio(app="a", component="auth-service",
                          clickhouse_url="http://x", usuario="u", password="p")
    medicion = await sonda.ejecutar()

    consulta = consultas[0]
    assert "count()" not in consulta, "contar puntos es lo que hacia invisible un servicio muerto"
    assert "max(v) - min(v)" in consulta, "acumulativo: activo = el contador crecio"
    assert "any(temp) = 1" in consulta, "delta y acumulativo no se miden igual"
    assert "toFloat64" in consulta, "Count es UInt64 y ClickHouse se niega a restarlo de un Int64"
    assert medicion.resultado is Resultado.SILENCIO


async def test_una_serie_congelada_es_silencio(monkeypatch) -> None:
    """Un contador acumulativo que no crece es un servicio que no trabaja."""
    import httpx

    class RespuestaFalsa:
        text = "0"
        def raise_for_status(self): ...

    class ClienteFalso:
        def __init__(self, **kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return RespuestaFalsa()

    monkeypatch.setattr(httpx, "AsyncClient", ClienteFalso)

    sonda = SondaSilencio(app="a", component="c", clickhouse_url="http://x",
                          usuario="u", password="p")
    assert (await sonda.ejecutar()).resultado is Resultado.SILENCIO


async def test_el_canario_recarga_el_registro_sin_reiniciar(tmp_path) -> None:
    """Derivar las sondas del registro solo al arrancar deja el registro inerte.

    Y el modo de fallo es silencioso de la peor manera: `docker compose up -d`
    sin cambios NO reinicia el contenedor, así que editar el registro y
    redesplegar parece funcionar y no hace nada. Un componente recién conectado
    se queda sin vigilar sin que nadie lo note.
    """
    import yaml

    from canary.build import cargar, firma
    from canary.config import Settings
    from canary.probes import SondaSilencio

    registro = tmp_path / "apps.yaml"
    sondas_http = tmp_path / "probes.yaml"
    sondas_http.write_text("[]", encoding="utf-8")

    def escribir(componentes):
        registro.write_text(yaml.safe_dump([{
            "id": "p", "estado": "activo", "componentes": componentes,
        }], allow_unicode=True), encoding="utf-8")

    escribir([{"id": "auth-service", "rol": "api"}])
    settings = Settings(registry_path=registro, probes_path=sondas_http)

    runner = Runner(cargar(settings), alertbus_url="http://x")
    antes = firma(settings)

    time.sleep(0.01)
    escribir([{"id": "auth-service", "rol": "api"}, {"id": "gateway", "rol": "api"}])

    assert firma(settings) != antes, "el cambio del registro tiene que ser detectable"

    runner.recargar(cargar(settings))
    vigilados = {s.component for s in runner._sondas if isinstance(s, SondaSilencio)}
    assert vigilados == {"auth-service", "gateway"}


async def test_la_recarga_conserva_los_fallos_consecutivos(tmp_path) -> None:
    """Reiniciar el contador en cada recarga silenciaría al canario.

    Si el registro se edita más a menudo que el intervalo de sondeo, ningún
    fallo llegaría nunca a dos consecutivos y el canario dejaría de alertar sin
    dejar rastro.
    """
    from canary.probes import SondaSilencio

    def sonda(componente):
        return SondaSilencio(app="p", component=componente,
                             clickhouse_url="http://x", usuario="u", password="p")

    runner = Runner([sonda("auth-service")], alertbus_url="http://x")
    runner._consecutivos["p/auth-service"] = 1
    runner._consecutivos["p/se-va"] = 1
    runner._alertados = {"p/auth-service", "p/se-va"}

    runner.recargar([sonda("auth-service"), sonda("gateway")])

    assert runner._consecutivos["p/auth-service"] == 1, "el que sigue vigilado conserva su cuenta"
    assert "p/se-va" not in runner._consecutivos, "el que desaparece del registro se olvida"
    assert runner._alertados == {"p/auth-service"}


async def test_la_consulta_mide_crecimiento_por_serie(monkeypatch) -> None:
    """Sumar las series antes de restar convierte una duplicación en actividad.

    El exportador reescribe a veces el mismo punto: misma serie, mismo valor,
    mismo `TimeUnix`. Si se suman todas las series de un instante, ese instante
    vale el doble y la resta lo lee como crecimiento. Medido sobre una serie
    real congelada tres horas, la fórmula vieja daba 204 y la correcta 0.

    Es el mismo fallo que D-049 en otra forma, así que la prueba fija la forma
    de la consulta: agrupar por `Attributes` y deduplicar con `max` antes de
    comparar no es un detalle de estilo.
    """
    import httpx

    consultas: list[str] = []

    class RespuestaFalsa:
        text = "0"
        def raise_for_status(self): ...

    class ClienteFalso:
        def __init__(self, **kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, content, headers):
            consultas.append(content)
            return RespuestaFalsa()

    monkeypatch.setattr(httpx, "AsyncClient", ClienteFalso)

    sonda = SondaSilencio(app="a", component="c", clickhouse_url="http://x",
                          usuario="u", password="p")
    await sonda.ejecutar()

    consulta = consultas[0]
    assert "GROUP BY Attributes, TimeUnix" in consulta, "el crecimiento es por serie"
    assert "max(toFloat64" in consulta, "hay que deduplicar puntos idénticos"
    assert "sum(crecimiento)" in consulta, "se suma DESPUÉS de restar, no antes"
