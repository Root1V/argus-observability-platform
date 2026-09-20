"""Lo que un equipo consumidor encontró probando el alfa (P-29 / D-082).

Tres defectos distintos, los tres invisibles desde dentro: solo aparecen cuando
alguien que sirve inferencia intenta usar el paquete de verdad.
"""

from __future__ import annotations

import inspect
import warnings

import pytest
from argus_semconv import attributes as A

# --- El scope no puede mentir sobre qué versión emitió ----------------------

def test_el_scope_declara_la_version_del_paquete_no_la_del_modelo():
    """Un alfa y una estable declaraban `argus-semconv 1.0.0` por igual.

    El modelo de convenciones lleva en `1.0.0` desde hace tiempo, así que
    registrar ESA como versión del scope hacía imposible distinguir en el
    almacén lo emitido por un prelanzamiento de lo emitido por la estable — que
    es justo lo que quieres poder hacer cuando algo no cuadra durante una
    adopción.
    """
    from argus_semconv import _scope

    version = _scope.version_paquete()
    assert version != A.SEMCONV_VERSION or version == "0.0.0.dev0", (
        "la version del scope volvio a ser la del modelo"
    )
    # La del modelo sigue estando, como atributo, que es donde pertenece.
    assert _scope.atributos()["argus.semconv.version"] == A.SEMCONV_VERSION


def test_los_atributos_de_scope_van_por_NOMBRE():
    """El tercer posicional de `get_tracer` es el PROVIDER, no `schema_url`.

    Pasarlos por posición mete el diccionario en `schema_url`, y el fallo no
    salta al llamar: salta luego, al hashear el scope, con un
    `TypeError: unhashable type: 'dict'` que no apunta a la causa. Un proveedor
    no-op se lo traga entero, así que este test necesita uno de verdad.
    """
    from opentelemetry import trace

    firma = inspect.signature(trace.get_tracer)
    posicionales = list(firma.parameters)
    assert posicionales[2] != "schema_url", "la firma cambio; revisa las llamadas"


# --- record_ttft tiene que poder trocearse por operación -------------------

def test_el_ttft_lleva_la_operacion():
    """Duration y tokens la llevaban; el TTFT la construía y luego la borraba.

    Con chat, embeddings y rerank conviviendo en el mismo despliegue, ese es el
    corte que más falta hace, y era el único que no se podía pedir.

    Aquí se comprueba la FIRMA. Que el atributo llegue de verdad a la serie se
    comprueba en `test_metricas_genai.py`, que es quien posee el MeterProvider
    del proceso.
    """
    from argus_semconv import metrics as M

    firma = inspect.signature(M.record_ttft)
    assert "operation" in firma.parameters, "record_ttft volvio a descartar la operacion"
    assert firma.parameters["operation"].default == "chat"


# --- El módulo es público --------------------------------------------------

def test_metrics_es_publico_y_el_modulo_viejo_avisa():
    import argus_semconv as S

    assert hasattr(S, "metrics")
    assert "metrics" in S.__all__

    with warnings.catch_warnings(record=True) as avisos:
        warnings.simplefilter("always")
        import importlib

        import argus_semconv._metrics_api as viejo

        importlib.reload(viejo)

    assert any(issubclass(a.category, DeprecationWarning) for a in avisos)
    # Reexporta, no duplica: los instrumentos tienen que ser los mismos.
    assert viejo.record_ttft is S.metrics.record_ttft


# --- El renombrado de A-25 -------------------------------------------------

@pytest.mark.parametrize(
    "constante,esperado",
    [
        ("ARGUS_INFERENCE_BACKEND_ID", "argus.inference.backend_id"),
        ("ARGUS_INFERENCE_TTFT_MS", "argus.inference.ttft_ms"),
        ("ARGUS_INFERENCE_FIRST_TOKEN_MS", "argus.inference.first_token_ms"),
    ],
)
def test_los_nombres_son_los_que_emite_el_equipo_de_inferencia(constante, esperado):
    """Estos tres los emite Prometheus en producción desde el 17/09.

    Se los dimos escritos a mano en un documento, sin contrastarlos con el
    modelo, y durante días nuestro propio paquete no los conocía. El modelo se
    mudó a los suyos porque eran mejores; esto impide que se vuelvan a separar.
    """
    assert getattr(A, constante) == esperado
