"""Las métricas GenAI se emiten SOLAS.

Regresión encontrada al construir el dashboard: `gen_ai.client.token.usage` y
`gen_ai.client.operation.duration` estaban definidas, tenían buckets y vistas…
y **nada las emitía**. Había que llamar a la API de métricas a mano.

Las convenciones dicen que esas dos van desde el día uno, y eso solo se cumple
si salen del mismo context manager que ya se usa para las trazas. Si hubiera que
emitirlas aparte, nadie las emitiría.
"""

from __future__ import annotations

import pytest
from argus_semconv import agent, genai, tool
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader


@pytest.fixture(scope="module")
def lector() -> InMemoryMetricReader:
    """Un único MeterProvider de módulo.

    Igual que con las trazas, OpenTelemetry solo deja fijar el proveedor global
    una vez por proceso.
    """
    lector = InMemoryMetricReader()
    metrics.set_meter_provider(MeterProvider(metric_readers=[lector]))
    return lector


def recoger(lector: InMemoryMetricReader) -> dict[str, list]:
    """Devuelve {nombre de métrica: [puntos]}."""
    datos = lector.get_metrics_data()
    salida: dict[str, list] = {}
    if datos is None:
        return salida
    for recurso in datos.resource_metrics:
        for scope in recurso.scope_metrics:
            for metrica in scope.metrics:
                salida.setdefault(metrica.name, []).extend(metrica.data.data_points)
    return salida


def test_la_duracion_se_emite_sin_pedirlo(lector) -> None:
    with genai("chat", provider="ollama", request_model="qwen2.5-7b"):
        pass

    puntos = recoger(lector).get("gen_ai.client.operation.duration", [])
    assert puntos, "nadie emitió la duración"
    attrs = dict(puntos[-1].attributes)
    assert attrs["gen_ai.operation.name"] == "chat"
    assert attrs["gen_ai.request.model"] == "qwen2.5-7b"


def test_la_duracion_se_emite_tambien_al_fallar(lector) -> None:
    """Es justo el caso en que interesa saber cuánto tardó en fallar."""
    with genai("chat", provider="ollama", request_model="m") as g:
        g.error("timeout")

    puntos = recoger(lector).get("gen_ai.client.operation.duration", [])
    con_error = [p for p in puntos if dict(p.attributes).get("error.type") == "timeout"]
    assert con_error, "la duración no se emitió al fallar"


def test_los_tokens_llevan_el_tipo_como_dimension(lector) -> None:
    """Es lo que dice la especificación, y lo que permite agregar por modelo
    sin unir series a mano."""
    with genai("chat", provider="ollama", request_model="m") as g:
        g.usage(input_tokens=1200, output_tokens=340)

    puntos = recoger(lector).get("gen_ai.client.token.usage", [])
    por_tipo = {dict(p.attributes).get("gen_ai.token.type"): p for p in puntos}
    assert "input" in por_tipo and "output" in por_tipo
    assert por_tipo["input"].sum == 1200
    assert por_tipo["output"].sum == 340


def test_el_coste_lleva_las_dimensiones_de_atribucion(lector) -> None:
    """Sin app, funcionalidad y caso de uso, el coste llega como un número
    opaco a fin de mes y no se puede optimizar nada."""
    with genai("chat", provider="openai", request_model="gpt-4o") as g:
        g.attribution(feature="extraccion", use_case="documentos")
        g.backend(cost_usd=0.0042)

    puntos = recoger(lector).get("argus.cost.usd", [])
    assert puntos, "nadie emitió el coste"
    attrs = dict(puntos[-1].attributes)
    assert attrs["argus.feature"] == "extraccion"
    assert attrs["argus.use_case"] == "documentos"
    assert attrs["gen_ai.request.model"] == "gpt-4o"


def test_el_ttft_se_emite_desde_backend(lector) -> None:
    with genai("chat", provider="ollama", request_model="m") as g:
        g.backend(backend_id="llama-0", ttft_ms=87)

    puntos = recoger(lector).get("gen_ai.server.time_to_first_token", [])
    assert puntos
    assert dict(puntos[-1].attributes)["argus.backend.id"] == "llama-0"


def test_las_herramientas_tambien_emiten_duracion(lector) -> None:
    """`execute_tool` es una operación GenAI más: su latencia importa igual."""
    with agent("bot"), tool("buscar", args={"q": "x"}):
        pass

    puntos = recoger(lector).get("gen_ai.client.operation.duration", [])
    operaciones = {dict(p.attributes).get("gen_ai.operation.name") for p in puntos}
    assert "execute_tool" in operaciones
    assert "invoke_agent" in operaciones
