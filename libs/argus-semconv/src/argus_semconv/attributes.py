"""
GENERADO AUTOMATICAMENTE. NO EDITAR A MANO.

Fuente: libs/semconv-model/argus.yaml
Regenerar: python tools/gen_semconv.py
"""

from __future__ import annotations

from typing import Final

SEMCONV_VERSION: Final[str] = "1.0.0"
GENAI_SEMCONV_VERSION: Final[str] = "1.39.0"


# --------------------------------------------------------------------------
# identity: Identidad de aplicacion y componente
# --------------------------------------------------------------------------
SERVICE_NAMESPACE: Final[str] = "service.namespace"
SERVICE_NAME: Final[str] = "service.name"
SERVICE_VERSION: Final[str] = "service.version"
SERVICE_INSTANCE_ID: Final[str] = "service.instance.id"
DEPLOYMENT_ENVIRONMENT_NAME: Final[str] = "deployment.environment.name"
ARGUS_COMPONENT_ROLE: Final[str] = "argus.component.role"
ARGUS_COMPONENT_ROLE_VALUES: Final[tuple[str, ...]] = ("api", "worker", "scheduler", "cli", "model-server", "frontend", "library",)

# --------------------------------------------------------------------------
# langfuse_bridge: Atributos que Langfuse interpreta de forma especial
# --------------------------------------------------------------------------
LANGFUSE_ENVIRONMENT: Final[str] = "langfuse.environment"
LANGFUSE_RELEASE: Final[str] = "langfuse.release"
LANGFUSE_OBSERVATION_TYPE: Final[str] = "langfuse.observation.type"
LANGFUSE_TRACE_NAME: Final[str] = "langfuse.trace.name"
LANGFUSE_TRACE_TAGS: Final[str] = "langfuse.trace.tags"

# --------------------------------------------------------------------------
# attribution: Etiquetas de atribucion para FinOps y analisis
# --------------------------------------------------------------------------
ARGUS_APP: Final[str] = "argus.app"
ARGUS_FEATURE: Final[str] = "argus.feature"
ARGUS_USE_CASE: Final[str] = "argus.use_case"
ARGUS_RUN_ID: Final[str] = "argus.run.id"
ARGUS_TENANT: Final[str] = "argus.tenant"

# --------------------------------------------------------------------------
# wide_event: Campos del evento ancho canonico
# --------------------------------------------------------------------------
ARGUS_EVENT: Final[str] = "argus.event"
ARGUS_OUTCOME: Final[str] = "argus.outcome"
ARGUS_OUTCOME_VALUES: Final[tuple[str, ...]] = ("ok", "error", "timeout", "cancelled", "degraded",)
ARGUS_DURATION_MS: Final[str] = "argus.duration_ms"
ARGUS_STEP_DEPTH: Final[str] = "argus.step.depth"

# --------------------------------------------------------------------------
# hotpath: Marcas que enrutan al camino caliente de deteccion
# --------------------------------------------------------------------------
ARGUS_HOT: Final[str] = "argus.hot"
ARGUS_SLO_BREACHED: Final[str] = "argus.slo.breached"
ARGUS_SLO_THRESHOLD_MS: Final[str] = "argus.slo.threshold_ms"
ARGUS_GUARDRAIL: Final[str] = "argus.guardrail"

# --------------------------------------------------------------------------
# errors: Clasificacion de errores
# --------------------------------------------------------------------------
ERROR_TYPE: Final[str] = "error.type"
ARGUS_ERROR_RETRYABLE: Final[str] = "argus.error.retryable"
ARGUS_ERROR_RETRY_POLICY: Final[str] = "argus.error.retry_policy"

# --------------------------------------------------------------------------
# genai: Convenciones GenAI de OpenTelemetry
# --------------------------------------------------------------------------
GEN_AI_OPERATION_NAME: Final[str] = "gen_ai.operation.name"
GEN_AI_OPERATION_NAME_VALUES: Final[tuple[str, ...]] = ("chat", "text_completion", "embeddings", "generate_content", "retrieval", "execute_tool", "invoke_agent", "create_agent",)
GEN_AI_PROVIDER_NAME: Final[str] = "gen_ai.provider.name"
GEN_AI_REQUEST_MODEL: Final[str] = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL: Final[str] = "gen_ai.response.model"
GEN_AI_RESPONSE_ID: Final[str] = "gen_ai.response.id"
GEN_AI_RESPONSE_FINISH_REASONS: Final[str] = "gen_ai.response.finish_reasons"
GEN_AI_USAGE_INPUT_TOKENS: Final[str] = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS: Final[str] = "gen_ai.usage.output_tokens"
GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS: Final[str] = "gen_ai.usage.cache_read.input_tokens"
GEN_AI_REQUEST_TEMPERATURE: Final[str] = "gen_ai.request.temperature"
GEN_AI_REQUEST_MAX_TOKENS: Final[str] = "gen_ai.request.max_tokens"
GEN_AI_REQUEST_TOP_P: Final[str] = "gen_ai.request.top_p"
GEN_AI_CONVERSATION_ID: Final[str] = "gen_ai.conversation.id"
GEN_AI_AGENT_NAME: Final[str] = "gen_ai.agent.name"
GEN_AI_AGENT_ID: Final[str] = "gen_ai.agent.id"
GEN_AI_TOOL_NAME: Final[str] = "gen_ai.tool.name"
GEN_AI_TOOL_TYPE: Final[str] = "gen_ai.tool.type"
GEN_AI_TOOL_CALL_ID: Final[str] = "gen_ai.tool.call.id"
GEN_AI_INPUT_MESSAGES: Final[str] = "gen_ai.input.messages"
GEN_AI_OUTPUT_MESSAGES: Final[str] = "gen_ai.output.messages"
GEN_AI_SYSTEM_INSTRUCTIONS: Final[str] = "gen_ai.system_instructions"
GEN_AI_TOOL_CALL_ARGUMENTS: Final[str] = "gen_ai.tool.call.arguments"
GEN_AI_TOOL_CALL_RESULT: Final[str] = "gen_ai.tool.call.result"
GEN_AI_CONTENT_TRUNCATED: Final[str] = "gen_ai.content.truncated"

# --------------------------------------------------------------------------
# inference: Detalles de inferencia local que solo conoce el SDK cliente
# --------------------------------------------------------------------------
ARGUS_INFERENCE_BACKEND_ID: Final[str] = "argus.inference.backend_id"
ARGUS_INFERENCE_CIRCUIT_STATE: Final[str] = "argus.inference.circuit_state"
ARGUS_INFERENCE_CIRCUIT_STATE_VALUES: Final[tuple[str, ...]] = ("closed", "open", "half-open",)
ARGUS_INFERENCE_FALLBACK: Final[str] = "argus.inference.fallback"
ARGUS_INFERENCE_COST_USD: Final[str] = "argus.inference.cost_usd"
ARGUS_INFERENCE_TTFT_MS: Final[str] = "argus.inference.ttft_ms"
ARGUS_INFERENCE_FIRST_TOKEN_MS: Final[str] = "argus.inference.first_token_ms"
ARGUS_INFERENCE_TOKENS_PER_SECOND: Final[str] = "argus.inference.tokens_per_second"

# --------------------------------------------------------------------------
# platform: Puestos por el Collector, no por quien emite
# --------------------------------------------------------------------------
ARGUS_SAMPLING_BASELINE_PCT: Final[str] = "argus.sampling.baseline_pct"
ARGUS_COLLECTOR_TIER: Final[str] = "argus.collector.tier"
ARGUS_COLLECTOR_TIER_VALUES: Final[tuple[str, ...]] = ("agent", "gateway",)

# --------------------------------------------------------------------------
# retrieval: Recuperacion de documentos
# --------------------------------------------------------------------------
ARGUS_RETRIEVAL_STORE: Final[str] = "argus.retrieval.store"
ARGUS_RETRIEVAL_TOP_K: Final[str] = "argus.retrieval.top_k"
ARGUS_RETRIEVAL_DOC_COUNT: Final[str] = "argus.retrieval.doc_count"
ARGUS_RETRIEVAL_SCORE_MIN: Final[str] = "argus.retrieval.score_min"
ARGUS_RETRIEVAL_SCORE_MAX: Final[str] = "argus.retrieval.score_max"


# --------------------------------------------------------------------------
# Metricas
# --------------------------------------------------------------------------
M_GEN_AI_CLIENT_OPERATION_DURATION: Final[str] = "gen_ai.client.operation.duration"
M_GEN_AI_CLIENT_OPERATION_DURATION_BUCKETS: Final[tuple[float, ...]] = (0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56, 5.12, 10.24, 20.48, 40.96, 81.92,)
M_GEN_AI_CLIENT_OPERATION_DURATION_UNIT: Final[str] = "s"
M_GEN_AI_CLIENT_TOKEN_USAGE: Final[str] = "gen_ai.client.token.usage"
M_GEN_AI_CLIENT_TOKEN_USAGE_BUCKETS: Final[tuple[float, ...]] = (1, 4, 16, 64, 256, 1024, 4096, 16384, 65536, 262144, 1048576, 4194304, 16777216, 67108864,)
M_GEN_AI_CLIENT_TOKEN_USAGE_UNIT: Final[str] = "{token}"
M_GEN_AI_SERVER_TIME_TO_FIRST_TOKEN: Final[str] = "gen_ai.server.time_to_first_token"
M_GEN_AI_SERVER_TIME_TO_FIRST_TOKEN_UNIT: Final[str] = "s"
M_ARGUS_STEP_DURATION: Final[str] = "argus.step.duration"
M_ARGUS_STEP_DURATION_UNIT: Final[str] = "s"
M_ARGUS_COST_USD: Final[str] = "argus.cost.usd"
M_ARGUS_COST_USD_UNIT: Final[str] = "{usd}"

