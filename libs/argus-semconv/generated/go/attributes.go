// GENERADO AUTOMATICAMENTE. NO EDITAR A MANO.
//
// Fuente: libs/semconv-model/argus.yaml
// Regenerar: python tools/gen_semconv.py

package semconv

const (
	SemconvVersion      = "1.0.0"
	GenAISemconvVersion = "1.39.0"
)

// identity: Identidad de aplicacion y componente
const (
	ServiceNamespace = "service.namespace"
	ServiceName = "service.name"
	ServiceVersion = "service.version"
	ServiceInstanceID = "service.instance.id"
	DeploymentEnvironmentName = "deployment.environment.name"
	ArgusComponentRole = "argus.component.role"
)

// langfuse_bridge: Atributos que Langfuse interpreta de forma especial
const (
	LangfuseEnvironment = "langfuse.environment"
	LangfuseRelease = "langfuse.release"
	LangfuseObservationType = "langfuse.observation.type"
	LangfuseTraceName = "langfuse.trace.name"
	LangfuseTraceTags = "langfuse.trace.tags"
)

// attribution: Etiquetas de atribucion para FinOps y analisis
const (
	ArgusApp = "argus.app"
	ArgusFeature = "argus.feature"
	ArgusUseCase = "argus.use_case"
	ArgusRunID = "argus.run.id"
	ArgusTenant = "argus.tenant"
)

// wide_event: Campos del evento ancho canonico
const (
	ArgusEvent = "argus.event"
	ArgusOutcome = "argus.outcome"
	ArgusDurationMs = "argus.duration_ms"
	ArgusStepDepth = "argus.step.depth"
)

// hotpath: Marcas que enrutan al camino caliente de deteccion
const (
	ArgusHot = "argus.hot"
	ArgusSloBreached = "argus.slo.breached"
	ArgusSloThresholdMs = "argus.slo.threshold_ms"
	ArgusGuardrail = "argus.guardrail"
)

// errors: Clasificacion de errores
const (
	ErrorType = "error.type"
	ArgusErrorRetryable = "argus.error.retryable"
	ArgusErrorRetryPolicy = "argus.error.retry_policy"
)

// genai: Convenciones GenAI de OpenTelemetry
const (
	GenAIOperationName = "gen_ai.operation.name"
	GenAIProviderName = "gen_ai.provider.name"
	GenAIRequestModel = "gen_ai.request.model"
	GenAIResponseModel = "gen_ai.response.model"
	GenAIResponseID = "gen_ai.response.id"
	GenAIResponseFinishReasons = "gen_ai.response.finish_reasons"
	GenAIUsageInputTokens = "gen_ai.usage.input_tokens"
	GenAIUsageOutputTokens = "gen_ai.usage.output_tokens"
	GenAIUsageCacheReadInputTokens = "gen_ai.usage.cache_read.input_tokens"
	GenAIRequestTemperature = "gen_ai.request.temperature"
	GenAIRequestMaxTokens = "gen_ai.request.max_tokens"
	GenAIRequestTopP = "gen_ai.request.top_p"
	GenAIConversationID = "gen_ai.conversation.id"
	GenAIAgentName = "gen_ai.agent.name"
	GenAIAgentID = "gen_ai.agent.id"
	GenAIToolName = "gen_ai.tool.name"
	GenAIToolType = "gen_ai.tool.type"
	GenAIToolCallID = "gen_ai.tool.call.id"
	GenAIInputMessages = "gen_ai.input.messages"
	GenAIOutputMessages = "gen_ai.output.messages"
	GenAISystemInstructions = "gen_ai.system_instructions"
	GenAIToolCallArguments = "gen_ai.tool.call.arguments"
	GenAIToolCallResult = "gen_ai.tool.call.result"
	GenAIContentTruncated = "gen_ai.content.truncated"
)

// inference: Detalles de inferencia local que solo conoce el SDK cliente
const (
	ArgusBackendID = "argus.backend.id"
	ArgusBackendCircuitState = "argus.backend.circuit_state"
	ArgusBackendFallback = "argus.backend.fallback"
	ArgusCostUSD = "argus.cost_usd"
	ArgusTtftMs = "argus.ttft_ms"
	ArgusFirstTokenMs = "argus.first_token_ms"
	ArgusTokensPerSecond = "argus.tokens_per_second"
)

// retrieval: Recuperacion de documentos
const (
	ArgusRetrievalStore = "argus.retrieval.store"
	ArgusRetrievalTopK = "argus.retrieval.top_k"
	ArgusRetrievalDocCount = "argus.retrieval.doc_count"
	ArgusRetrievalScoreMin = "argus.retrieval.score_min"
	ArgusRetrievalScoreMax = "argus.retrieval.score_max"
)

// Metricas
const (
	MetricGenAIClientOperationDuration = "gen_ai.client.operation.duration"
	MetricGenAIClientTokenUsage = "gen_ai.client.token.usage"
	MetricGenAIServerTimeToFirstToken = "gen_ai.server.time_to_first_token"
	MetricArgusStepDuration = "argus.step.duration"
	MetricArgusCostUSD = "argus.cost.usd"
)
