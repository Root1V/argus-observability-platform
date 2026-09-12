"""Helpers GenAI conformes a las semconv de OpenTelemetry 2026.

Todo lo de aqui usa SOLO la API de OpenTelemetry. Si la aplicacion que importa
tu libreria no ha inicializado un SDK, estos context managers son no-ops con
coste cero: la API devuelve implementaciones no operativas.

Uso tipico dentro de una libreria de inferencia (Axonium):

    with genai("chat", provider="ollama", request_model=model) as g:
        resp = await backend.complete(...)
        g.usage(input_tokens=resp.prompt_tokens, output_tokens=resp.completion_tokens)
        g.backend(backend_id=backend.id, ttft_ms=resp.ttft_ms)
        g.messages(input=msgs, output=resp.choices)
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Literal

from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from . import attributes as A
from ._content import capture_enabled, serialize

Operation = Literal[
    "chat",
    "text_completion",
    "embeddings",
    "generate_content",
    "retrieval",
    "execute_tool",
    "invoke_agent",
    "create_agent",
]

# El tracer se obtiene de la API global. Si no hay SDK, es un no-op tracer.
_tracer = trace.get_tracer("argus-semconv", A.SEMCONV_VERSION)

# Mapeo a los tipos de observacion de Langfuse. Su tabla de mapeo es CERRADA y
# no incluye `retrieval`, asi que sin esto un span de recuperacion RAG no se
# renderiza como tal en su UI.
_LANGFUSE_TYPE: dict[str, str] = {
    "retrieval": "retriever",
    "execute_tool": "tool",
    "invoke_agent": "agent",
    "create_agent": "agent",
}


class GenAISpan:
    """Envoltorio delgado sobre un span para poblar atributos GenAI.

    Nunca lanza. Un fallo al registrar telemetria no puede propagarse a la
    logica de negocio de quien nos importa.
    """

    __slots__ = ("_span",)

    def __init__(self, span: Span) -> None:
        self._span = span

    @property
    def span(self) -> Span:
        return self._span

    def _set(self, key: str, value: Any) -> None:
        if value is None:
            return
        try:
            if self._span.is_recording():
                self._span.set_attribute(key, value)
        except Exception:  # noqa: BLE001 - la telemetria nunca tumba la app
            pass

    def usage(
        self,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_input_tokens: int | None = None,
    ) -> None:
        self._set(A.GEN_AI_USAGE_INPUT_TOKENS, input_tokens)
        self._set(A.GEN_AI_USAGE_OUTPUT_TOKENS, output_tokens)
        self._set(A.GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS, cached_input_tokens)

    def response(
        self,
        *,
        model: str | None = None,
        response_id: str | None = None,
        finish_reasons: Sequence[str] | None = None,
    ) -> None:
        """Registra la respuesta.

        `model` es el modelo REALMENTE servido. Puede diferir del pedido por
        alias o routing, y esa diferencia explica incidentes.
        """
        self._set(A.GEN_AI_RESPONSE_MODEL, model)
        self._set(A.GEN_AI_RESPONSE_ID, response_id)
        if finish_reasons:
            self._set(A.GEN_AI_RESPONSE_FINISH_REASONS, tuple(finish_reasons))

    def request_params(
        self,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
    ) -> None:
        self._set(A.GEN_AI_REQUEST_TEMPERATURE, temperature)
        self._set(A.GEN_AI_REQUEST_MAX_TOKENS, max_tokens)
        self._set(A.GEN_AI_REQUEST_TOP_P, top_p)

    def backend(
        self,
        *,
        backend_id: str | None = None,
        circuit_state: str | None = None,
        fallback: bool | None = None,
        ttft_ms: int | None = None,
        tokens_per_second: float | None = None,
        cost_usd: float | None = None,
    ) -> None:
        """Detalles de inferencia local que SOLO conoce el SDK cliente.

        Ninguna aplicacion sabe que backend respondio, si hubo fallback o si
        salto el circuit breaker. Por eso instrumentar aqui da a todas las apps
        que usen esta libreria informacion que de otro modo se pierde.
        """
        self._set(A.ARGUS_BACKEND_ID, backend_id)
        self._set(A.ARGUS_BACKEND_CIRCUIT_STATE, circuit_state)
        self._set(A.ARGUS_BACKEND_FALLBACK, fallback)
        self._set(A.ARGUS_TTFT_MS, ttft_ms)
        self._set(A.ARGUS_TOKENS_PER_SECOND, tokens_per_second)
        self._set(A.ARGUS_COST_USD, cost_usd)

    def messages(
        self,
        *,
        input: Sequence[Mapping[str, Any]] | None = None,
        output: Sequence[Mapping[str, Any]] | None = None,
        system_instructions: str | None = None,
    ) -> None:
        """Adjunta el contenido, si la captura esta activada.

        No-op cuando `ARGUS_CAPTURE_CONTENT` no esta activo, que es el caso por
        defecto. Va en atributos y no en eventos: ver `_content`.
        """
        if not capture_enabled():
            return
        truncated = False
        for payload, key in (
            (input, A.GEN_AI_INPUT_MESSAGES),
            (output, A.GEN_AI_OUTPUT_MESSAGES),
            (system_instructions, A.GEN_AI_SYSTEM_INSTRUCTIONS),
        ):
            if payload is None:
                continue
            text, was_truncated = serialize(payload)
            truncated = truncated or was_truncated
            self._set(key, text)
        if truncated:
            self._set(A.GEN_AI_CONTENT_TRUNCATED, True)

    def attribution(
        self,
        *,
        feature: str | None = None,
        use_case: str | None = None,
        conversation_id: str | None = None,
    ) -> None:
        """Etiquetas de atribucion de coste.

        Sin estas, el coste llega como un numero opaco a fin de mes y no se
        puede optimizar nada.
        """
        self._set(A.ARGUS_FEATURE, feature)
        self._set(A.ARGUS_USE_CASE, use_case)
        self._set(A.GEN_AI_CONVERSATION_ID, conversation_id)

    def error(self, error_type: str, *, retryable: bool | None = None, retry_policy: str | None = None) -> None:
        """Marca el span como fallido con un tipo de error de catalogo cerrado.

        `retryable` lo lee el agente remediador: reintentar un error no
        transitorio es hacer daño, no arreglar.
        """
        self._set(A.ERROR_TYPE, error_type)
        self._set(A.ARGUS_ERROR_RETRYABLE, retryable)
        self._set(A.ARGUS_ERROR_RETRY_POLICY, retry_policy)
        self._set(A.ARGUS_HOT, True)
        try:
            self._span.set_status(Status(StatusCode.ERROR, error_type))
        except Exception:  # noqa: BLE001
            pass

    def set(self, key: str, value: Any) -> None:
        """Escotilla de escape para atributos no cubiertos por los helpers."""
        self._set(key, value)


@contextmanager
def genai(
    operation: Operation,
    *,
    provider: str,
    request_model: str | None = None,
    agent_name: str | None = None,
    tool_name: str | None = None,
    kind: SpanKind = SpanKind.CLIENT,
) -> Iterator[GenAISpan]:
    """Abre un span GenAI conforme.

    El nombre del span sigue la convencion `{operacion} {modelo}`, p. ej.
    `chat qwen2.5-coder-7b`. Sin modelo, solo la operacion.
    """
    name = f"{operation} {request_model}" if request_model else operation

    with _tracer.start_as_current_span(name, kind=kind) as span:
        wrapper = GenAISpan(span)
        wrapper.set(A.GEN_AI_OPERATION_NAME, operation)
        wrapper.set(A.GEN_AI_PROVIDER_NAME, provider)
        wrapper.set(A.GEN_AI_REQUEST_MODEL, request_model)
        wrapper.set(A.GEN_AI_AGENT_NAME, agent_name)
        wrapper.set(A.GEN_AI_TOOL_NAME, tool_name)

        langfuse_type = _LANGFUSE_TYPE.get(operation)
        if langfuse_type:
            wrapper.set(A.LANGFUSE_OBSERVATION_TYPE, langfuse_type)

        try:
            yield wrapper
        except Exception as exc:
            wrapper.error(type(exc).__name__)
            raise


@contextmanager
def retrieval(
    store: str,
    *,
    top_k: int | None = None,
) -> Iterator[GenAISpan]:
    """Span de recuperacion para RAG.

    Emite `langfuse.observation.type=retriever` ademas de la operacion, porque
    la tabla de mapeo de Langfuse es cerrada y no incluye `retrieval`.
    """
    with genai("retrieval", provider=store, kind=SpanKind.CLIENT) as g:
        g.set(A.ARGUS_RETRIEVAL_STORE, store)
        g.set(A.ARGUS_RETRIEVAL_TOP_K, top_k)
        yield g


@contextmanager
def tool(name: str, *, call_id: str | None = None, tool_type: str = "function") -> Iterator[GenAISpan]:
    """Span de ejecucion de herramienta."""
    with genai("execute_tool", provider="local", tool_name=name, kind=SpanKind.INTERNAL) as g:
        g.set(A.GEN_AI_TOOL_TYPE, tool_type)
        g.set(A.GEN_AI_TOOL_CALL_ID, call_id)
        yield g


@contextmanager
def agent(name: str, *, agent_id: str | None = None, conversation_id: str | None = None) -> Iterator[GenAISpan]:
    """Span padre `invoke_agent` que agrupa el ciclo de razonamiento.

    Sus hijos alternan `chat` y `execute_tool`, que es la estructura que
    permite detectar bucles contando tool calls por ejecucion.
    """
    with genai("invoke_agent", provider="local", agent_name=name, kind=SpanKind.INTERNAL) as g:
        g.set(A.GEN_AI_AGENT_ID, agent_id)
        g.set(A.GEN_AI_CONVERSATION_ID, conversation_id)
        yield g


def documents(span: GenAISpan, docs: Sequence[tuple[Any, float]]) -> None:
    """Registra los documentos recuperados y el rango de sus puntuaciones."""
    if not docs:
        span.set(A.ARGUS_RETRIEVAL_DOC_COUNT, 0)
        return
    scores = [s for _, s in docs]
    span.set(A.ARGUS_RETRIEVAL_DOC_COUNT, len(docs))
    span.set(A.ARGUS_RETRIEVAL_SCORE_MIN, min(scores))
    span.set(A.ARGUS_RETRIEVAL_SCORE_MAX, max(scores))
