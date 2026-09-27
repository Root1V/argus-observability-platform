"""Lo que el modelo DECLARA para cada métrica tiene que ser lo que se emite.

El modelo es la fuente de verdad, pero solo lo es de verdad si algo comprueba
que el código la respeta. Cuando arreglamos `record_ttft` para que aceptara
`operation` (D-082) nos dejamos el modelo sin actualizar: durante dos versiones
declaró tres atributos y se emitían cuatro. Nadie se enteró porque no había
nada mirando (D-085).

Es el mismo hueco que `test_documentos_vs_modelo.py` cierra por el lado de la
documentación, ahora por el lado del código.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from argus_semconv import attributes as A
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

MODELO = Path(__file__).resolve().parents[2] / "semconv-model" / "argus.yaml"

# Atributos que el modelo declara y `record_*` no puede poner, cada uno con su
# motivo. Una exención sin razón escrita es donde se esconde el siguiente error.
NO_LOS_PONE_LA_LIBRERIA = {
    # Solo se conoce si hubo fallo, y lo sabe quien llama.
    "error.type": "condicional, lo pasa quien llama",
    # La dirección del backend la sabe la aplicación, no la convención.
    "server.address": "lo pone la aplicación",
    # Viene del RESOURCE, no del punto de datos. En el almacén acaba siendo una
    # etiqueta de la serie igual, así que declararlo es honesto; lo que no puede
    # es salir de `record_cost`.
    "deployment.environment.name": "atributo de Resource, lo pone el SDK",
}


def _declarados() -> dict[str, set[str]]:
    yaml = pytest.importorskip("yaml")
    modelo = yaml.safe_load(MODELO.read_text(encoding="utf-8"))
    return {m["name"]: set(m.get("attributes") or []) for m in modelo.get("metrics", [])}


@pytest.fixture(scope="module")
def emitido(lector_metricas) -> dict[str, set[str]]:
    """Emite una vez cada métrica y recoge los atributos que salieron."""
    lector = lector_metricas

    from argus_semconv import metrics as M

    M.record_duration(operation="chat", provider="p", model="m", seconds=0.1)
    M.record_tokens(operation="chat", provider="p", model="m", input_tokens=1, output_tokens=1)
    M.record_ttft(provider="p", model="m", seconds=0.1, operation="chat", backend_id="b")
    M.record_cost(cost_usd=0.1, model="m", app="a", feature="f", use_case="u")

    salida: dict[str, set[str]] = {}
    datos = lector.get_metrics_data()
    for rm in datos.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                for punto in m.data.data_points:
                    salida.setdefault(m.name, set()).update(dict(punto.attributes))
    return salida


@pytest.mark.skipif(not MODELO.is_file(), reason="sin el modelo en una instalación del paquete")
def test_no_se_emite_nada_que_el_modelo_no_declare(emitido) -> None:
    """Un atributo emitido y no declarado es un contrato roto sin avisar.

    Es el lado que más importa: llega al almacén, alguien lo consulta, y su
    significado no vive en ninguna parte — igual que pasó con
    `argus.sampling.baseline_pct` (D-081).
    """
    declarados = _declarados()
    sobran: dict[str, set[str]] = {}
    for nombre, attrs in emitido.items():
        if nombre not in declarados:
            continue
        extra = attrs - declarados[nombre]
        if extra:
            sobran[nombre] = extra
    assert not sobran, (
        "se emiten atributos que el modelo no declara:\n"
        + "\n".join(f"  {n}: {sorted(a)}" for n, a in sobran.items())
        + "\n\nAñádelos a libs/semconv-model/argus.yaml o deja de emitirlos."
    )


@pytest.mark.skipif(not MODELO.is_file(), reason="sin el modelo")
def test_lo_que_el_modelo_promete_se_emite(emitido) -> None:
    """El lado inverso: declarar y no emitir es prometer una dimensión que no existe.

    Quien escriba `sum by (...)` sobre ella obtiene una sola serie vacía y
    ninguna pista de por qué.
    """
    declarados = _declarados()
    faltan: dict[str, set[str]] = {}
    for nombre, attrs in declarados.items():
        if nombre not in emitido:
            continue
        hueco = attrs - emitido[nombre] - set(NO_LOS_PONE_LA_LIBRERIA)
        if hueco:
            faltan[nombre] = hueco
    assert not faltan, (
        "el modelo declara atributos que nadie emite:\n"
        + "\n".join(f"  {n}: {sorted(a)}" for n, a in faltan.items())
    )


@pytest.mark.skipif(not MODELO.is_file(), reason="sin el modelo")
def test_el_ttft_declara_la_operacion() -> None:
    """El caso concreto que se nos quedó descolgado dos versiones."""
    assert A.GEN_AI_OPERATION_NAME in _declarados()[A.M_GEN_AI_SERVER_TIME_TO_FIRST_TOKEN]


@pytest.mark.skipif(not MODELO.is_file(), reason="sin el modelo")
def test_las_exenciones_siguen_haciendo_falta() -> None:
    """Una exención que ya no aplica tapa la siguiente que sí importa."""
    declarados = set()
    for attrs in _declarados().values():
        declarados |= attrs
    sobran = {a for a in NO_LOS_PONE_LA_LIBRERIA if a not in declarados}
    assert not sobran, f"exenciones que el modelo ya no declara, quitalas: {sorted(sobran)}"
