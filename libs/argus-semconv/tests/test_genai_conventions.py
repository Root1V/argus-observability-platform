"""Con SDK activo, verifica que lo emitido cumple las convenciones."""

from __future__ import annotations

import pytest
from argus_semconv import agent, documents, genai, retrieval, step, tool
from argus_semconv import attributes as A


def test_span_name_follows_operation_model_convention(spans) -> None:
    """El nombre debe ser `{operacion} {modelo}`, p. ej. `chat qwen2.5-7b`."""
    with genai("chat", provider="ollama", request_model="qwen2.5-7b"):
        pass

    (finished,) = spans.get_finished_spans()
    assert finished.name == "chat qwen2.5-7b"


def test_span_name_without_model_is_just_the_operation(spans) -> None:
    with genai("invoke_agent", provider="local"):
        pass

    (finished,) = spans.get_finished_spans()
    assert finished.name == "invoke_agent"


def test_required_attributes_are_present(spans) -> None:
    with genai("chat", provider="anthropic", request_model="claude-opus-5") as g:
        g.usage(input_tokens=1200, output_tokens=340)
        g.response(model="claude-opus-5-20260101", finish_reasons=["stop"])

    attrs = spans.get_finished_spans()[0].attributes
    assert attrs[A.GEN_AI_OPERATION_NAME] == "chat"
    assert attrs[A.GEN_AI_PROVIDER_NAME] == "anthropic"
    assert attrs[A.GEN_AI_REQUEST_MODEL] == "claude-opus-5"
    assert attrs[A.GEN_AI_USAGE_INPUT_TOKENS] == 1200
    assert attrs[A.GEN_AI_USAGE_OUTPUT_TOKENS] == 340
    # El modelo servido difiere del pedido: esa diferencia explica incidentes.
    assert attrs[A.GEN_AI_RESPONSE_MODEL] == "claude-opus-5-20260101"


def test_gen_ai_system_is_never_emitted(spans) -> None:
    """`gen_ai.system` esta obsoleto; se sustituye por `gen_ai.provider.name`."""
    with genai("chat", provider="openai", request_model="gpt-4o"):
        pass

    assert "gen_ai.system" not in spans.get_finished_spans()[0].attributes


def test_retrieval_carries_langfuse_observation_type(spans) -> None:
    """Sin esto, un span RAG no se renderiza como tal en Langfuse.

    Su tabla de mapeo de operaciones es cerrada y no incluye `retrieval`.
    """
    with retrieval("pgvector", top_k=8) as r:
        documents(r, [("a", 0.9), ("b", 0.7), ("c", 0.5)])

    attrs = spans.get_finished_spans()[0].attributes
    assert attrs[A.GEN_AI_OPERATION_NAME] == "retrieval"
    assert attrs[A.LANGFUSE_OBSERVATION_TYPE] == "retriever"
    assert attrs[A.ARGUS_RETRIEVAL_DOC_COUNT] == 3
    assert attrs[A.ARGUS_RETRIEVAL_SCORE_MIN] == 0.5
    assert attrs[A.ARGUS_RETRIEVAL_SCORE_MAX] == 0.9


@pytest.mark.parametrize(
    ("factory", "expected_type"),
    [
        (lambda: tool("clickhouse_query"), "tool"),
        (lambda: agent("rca-investigator"), "agent"),
    ],
)
def test_langfuse_type_mapping(spans, factory, expected_type) -> None:
    with factory():
        pass
    assert spans.get_finished_spans()[0].attributes[A.LANGFUSE_OBSERVATION_TYPE] == expected_type


def test_agent_tool_hierarchy_forms_one_tree(spans) -> None:
    """`invoke_agent` padre con hijos `chat` y `execute_tool` alternados.

    Es la estructura que permite detectar bucles contando tool calls por
    ejecucion, que es la señal de un agente atascado.
    """
    with agent("rca-investigator", agent_id="run-abc"):
        with genai("chat", provider="ollama", request_model="qwen2.5-7b"):
            pass
        with tool("logs_aggregate", call_id="call-01"):
            pass

    finished = spans.get_finished_spans()
    by_name = {s.name: s for s in finished}
    parent = by_name["invoke_agent"]

    assert by_name["chat qwen2.5-7b"].parent.span_id == parent.context.span_id
    assert by_name["execute_tool"].parent.span_id == parent.context.span_id
    # Todo el arbol comparte traza: es lo que hace reconstruible la trayectoria.
    assert len({s.context.trace_id for s in finished}) == 1


def test_error_marks_span_hot_for_realtime_detection(spans) -> None:
    """Un error marca `argus.hot`, que es lo que el Collector agente filtra
    para el camino caliente de deteccion (~2 s, sin tocar la base de datos)."""
    with genai("chat", provider="ollama", request_model="qwen2.5-7b") as g:
        g.error("backend-unavailable", retryable=True, retry_policy="exponential-backoff")

    attrs = spans.get_finished_spans()[0].attributes
    assert attrs[A.ERROR_TYPE] == "backend-unavailable"
    assert attrs[A.ARGUS_HOT] is True
    assert attrs[A.ARGUS_ERROR_RETRYABLE] is True


def test_backend_details_only_the_client_sdk_knows(spans) -> None:
    """Ninguna aplicacion sabe que backend respondio ni si hubo fallback.

    Por eso instrumentar en la libreria de inferencia da a todas las apps que
    la usen informacion que de otro modo se pierde.
    """
    with genai("chat", provider="ollama", request_model="qwen2.5-7b") as g:
        g.backend(backend_id="llama-0", circuit_state="half-open", fallback=True, ttft_ms=87, cost_usd=0.0)

    attrs = spans.get_finished_spans()[0].attributes
    assert attrs[A.ARGUS_INFERENCE_BACKEND_ID] == "llama-0"
    assert attrs[A.ARGUS_INFERENCE_CIRCUIT_STATE] == "half-open"
    assert attrs[A.ARGUS_INFERENCE_FALLBACK] is True
    assert attrs[A.ARGUS_INFERENCE_TTFT_MS] == 87


def test_step_emits_wide_event_fields(spans) -> None:
    with step("document.process", pages=42) as s:
        s.set(ocr_engine="paddle", cache_hit=False)
        s.set(extracted_fields=17).outcome("ok")

    attrs = spans.get_finished_spans()[0].attributes
    assert attrs[A.ARGUS_EVENT] == "document.process"
    assert attrs["pages"] == 42
    assert attrs["ocr_engine"] == "paddle"
    assert attrs[A.ARGUS_OUTCOME] == "ok"
    assert A.ARGUS_DURATION_MS in attrs


def test_slo_breach_marks_hot(spans) -> None:
    """Superar el SLO marca el span para el camino caliente, igual que un error."""
    with step("slow.operation", slo_ms=1):
        import time

        time.sleep(0.01)

    attrs = spans.get_finished_spans()[0].attributes
    assert attrs[A.ARGUS_SLO_BREACHED] is True
    assert attrs[A.ARGUS_HOT] is True
    assert attrs[A.ARGUS_SLO_THRESHOLD_MS] == 1


def test_los_dos_tiempos_de_primer_token_conviven(spans) -> None:
    """Son dos preguntas distintas y las dos son legítimas.

    `argus.ttft_ms` = primer token VISIBLE, la experiencia de quien espera.
    `argus.first_token_ms` = el primero de cualquier tipo, razonamiento
    incluido: la salud del backend.

    Con modelos que razonan dejaron de coincidir. Medido en Prometheus: un
    stream de qwen3-0.6b con `max_tokens=32` emite 30 chunks de razonamiento
    antes del primer token visible, y el TTFT visible solo aparecía en 13 de
    247 peticiones. Fundirlos en un número pierde una de las dos preguntas.
    """
    with genai("chat", provider="ollama", request_model="qwen3-0.6b") as g:
        g.backend(backend_id="llama-cpp-0", first_token_ms=180, ttft_ms=2400)

    (finished,) = spans.get_finished_spans()
    assert finished.attributes[A.ARGUS_INFERENCE_FIRST_TOKEN_MS] == 180
    assert finished.attributes[A.ARGUS_INFERENCE_TTFT_MS] == 2400


def test_la_metrica_de_latencia_prefiere_el_primer_token_de_cualquier_tipo() -> None:
    """Es la única presente en todas las peticiones.

    Un histograma construido sobre el 5 % de las peticiones —y ese 5 % elegido
    por cuánto razona el modelo, no por nosotros— invita a sacar conclusiones
    de una submuestra sesgada.
    """
    import inspect

    from argus_semconv.genai import GenAISpan

    fuente = inspect.getsource(GenAISpan.backend)
    assert "first_token_ms or ttft_ms" in fuente
