"""El banco de L4: herramientas congeladas, servidas por el mismo `mcp-obs`.

Lo que se fija: que cada caso del banco tenga sus respuestas grabadas, que no
lleven datos de nadie, que el servidor congelado sirva el expediente del caso
—el mismo que lee L3— y que lo que no este grabado se conteste vacio y quede
anotado.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest
from diagnose.banco import cargar
from diagnose.banco_l4 import AlmacenCongelado, cargar_grabado, clave, ruta_de, servidor_congelado

CASOS = cargar()
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


@pytest.mark.parametrize("caso", CASOS, ids=lambda c: c.id)
def test_cada_caso_tiene_sus_herramientas_grabadas_y_sin_datos_de_nadie(caso) -> None:
    texto = ruta_de(caso.id).read_text(encoding="utf-8")
    assert not UUID.search(texto), f"{caso.id}: UUID en lo grabado"
    for marca in ("Failing row contains", "[parameters:", "[SQL:", "DETAIL:"):
        assert marca not in texto, f"{caso.id}: `{marca}`"


def test_la_clase_y_la_ruta_se_comparan_como_las_compara_l3() -> None:
    assert clave("recurrencia", app="a", componente="c", clase="httpx.ConnectError") == \
        clave("recurrencia", app="a", componente="c", clase="ConnectError")
    assert clave("linea_base", servicio="s", ruta="http://127.0.0.1:8091/v1/backends") == \
        clave("linea_base", servicio="s", ruta="/v1/backends")


def test_lo_no_grabado_se_contesta_vacio_y_se_anota(tmp_path) -> None:
    fallos = tmp_path / "f"
    alm = AlmacenCongelado({clave("concurrentes", componente="gateway"): [{"servicio": "x"}]}, fallos)

    assert alm.concurrentes(componente="gateway", desde=None, hasta=None) == [{"servicio": "x"}]
    assert alm.recurrencia(app="a", componente="c", clase="Nada", hasta=None, dias=30) == []
    assert fallos.read_text().splitlines() == ["recurrencia|app=a|componente=c|clase=nada"]


def _texto(resultado) -> str:
    return "\n".join(b.text for b in resultado.content if getattr(b, "type", "") == "text")


def test_el_servidor_congelado_sirve_el_expediente_que_leyo_l3() -> None:
    caso = next(c for c in CASOS if c.id == "prometheus-503-backend-caido")
    servidor = servidor_congelado(caso, cargar_grabado(caso.id))
    huella = caso.incidente["fingerprint"]

    expediente = _texto(asyncio.run(servidor.call_tool("expediente", {"huella": huella})))
    assert expediente == caso.expediente.texto()
    traza = _texto(asyncio.run(servidor.call_tool("traza", {"trace_id": caso.incidente["trace_ids"][0]})))
    assert "ConnectError" in traza


def test_el_incidente_congelado_no_trae_lo_que_escribio_l3() -> None:
    caso = CASOS[0]
    salida = json.loads(_texto(asyncio.run(servidor_congelado(caso, cargar_grabado(caso.id)).call_tool(
        "incidente", {"huella": caso.incidente["fingerprint"]}))))
    assert not salida.get("root_cause")
