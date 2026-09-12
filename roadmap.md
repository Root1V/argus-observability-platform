# Roadmap y backlog — Argus

Estado del proyecto por fases. Cada elemento tiene un **código estable** que no
cambia aunque se reordene el trabajo, para poder referenciarlo desde commits,
issues y `docs/decisions.md`.

> **Plan completo**: `docs/PLAN.md` · **Decisiones**: `docs/decisions.md`

---

## Leyenda

| Estado | Significado |
|---|---|
| ✅ | Hecho y verificado |
| 🧪 | Implementado, pendiente de verificación real |
| 🚧 | En curso |
| ⏳ | Pendiente, planificado |
| 💭 | Backlog: sin decidir si se hace |
| ❌ | Descartado (se conserva el porqué) |

**Códigos**: `F<fase>-<nn>` para trabajo planificado, `B-<nn>` para backlog
abierto, `X-<nn>` para deuda técnica y cosas descubiertas sobre la marcha.

---

## Resumen

| Fase | Título | Estado | Avance |
|---|---|---|---|
| **F0** | Plano central | ✅ | 7/7 |
| **F1** | Librerías y primeras apps | 🚧 | 7/12 |
| **F2** | Detección en tiempo real y alertas | 🚧 | 5/10 |
| **F3** | Diagnóstico L3 y memoria de incidentes | ⏳ | 0/8 |
| **F4** | Investigación agéntica L4 | ⏳ | 0/7 |
| **F5** | Calidad, coste y deriva | ⏳ | 0/8 |
| **F6** | Explicabilidad, postmortem y L5 | ⏳ | 0/7 |
| **F7** | Resto del portafolio | ⏳ | 0/5 |

**Dónde parar si hace falta**: al terminar **F2** ya tienes el 70 % del
beneficio por el 30 % del esfuerzo. Todo lo agéntico es la guinda.

---

## F0 · Plano central ✅

Objetivo: un endpoint OTLP al que cualquier app puede apuntar, desde cualquier
máquina.

| Código | Estado | Elemento | Notas |
|---|---|---|---|
| `F0-01` | ✅ | Workspace `uv` con `libs/` y `services/` | `pyproject.toml` raíz |
| `F0-02` | ✅ | ClickHouse + VictoriaMetrics + vmalert en compose | Perfil `lean`, 4 contenedores |
| `F0-03` | ✅ | Collector **gateway**: redacción, pseudonimización, tail sampling, fan-out | `platform/collector/gateway.yaml` |
| `F0-04` | ✅ | Collector **agente**: bifurcación caliente/frío, cola en disco | `platform/collector/agent.yaml` |
| `F0-05` | ✅ | Autenticación por token en el endpoint OTLP | Verificado: rechaza sin token |
| `F0-06` | ✅ | Reglas SLO burn-rate multi-ventana | `platform/rules/slo.yaml` |
| `F0-07` | ✅ | Validación de configs contra el binario real del Collector | `platform/scripts/validate-collector.sh` |

---

## F1 · Librerías y primeras apps 🚧

Objetivo: telemetría unificada de tres apps, en al menos dos máquinas.

| Código | Estado | Elemento | Notas |
|---|---|---|---|
| `F1-01` | ✅ | Modelo de convenciones en YAML, fuente de verdad | `libs/semconv-model/argus.yaml` |
| `F1-02` | ✅ | Generador de constantes Python y Go + test de deriva | `tools/gen_semconv.py` |
| `F1-03` | ✅ | `argus-semconv`: solo `opentelemetry-api` | D-002 |
| `F1-04` | ✅ | `argus-sdk`: `init()`, resource, trazas, métricas, logs | Contrato verificado por tests |
| `F1-05` | ✅ | Propagación fuera de HTTP: Kafka, Celery, colas, CLI | `argus/propagate.py` |
| `F1-06` | ✅ | Middleware ASGI con modos de confianza | D-010 |
| `F1-07` | ✅ | Prueba de humo end-to-end contra el stack real | `scripts/e2e_smoke.py` — 7/7 |
| `F1-08` | ⏳ | **Runbook de migración de máquina, ensayado en frío** | Requisito de F0 que quedó pendiente; ver `X-01` |
| `F1-09` | ⏳ | **Prueba de la cola persistente**: parar el central, generar tráfico, arrancar, no perder nada | Valida D-004 |
| `F1-10` | ⏳ | Desplegar Collector agente en una **segunda máquina** | Valida D-003 de verdad |
| `F1-11` | ⏳ | Adoptar en 3 apps reales: una API, una con worker, una Go | Go con instrumentación en compilación |
| `F1-12` | ⏳ | Perfil `genai`: Langfuse arrancado y verificado | Configurado, sin probar |

### F1b · Los SDKs propios ⏳

La fase de **mayor apalancamiento por línea escrita**: toda app que los use gana
trazas GenAI sin tocar nada.

| Código | Estado | Elemento | Notas |
|---|---|---|---|
| `F1b-01` | ⏳ | Instrumentar **Axonium** con `argus-semconv` | Ejemplo listo en `examples/` |
| `F1b-02` | ⏳ | Instrumentar **synaptum** (`invoke_agent`, `execute_tool`) | Su roadmap tiene SYN-37 pendiente |
| `F1b-03` | ⏳ | Verificar overhead no medible sin `init()` | Benchmark |
| `F1b-04` | ⏳ | Verificar que `pip install axonium` no arrastra el SDK de OTel | Test en el repo de Axonium |

---

## F2 · Detección en tiempo real y alertas ⏳

**El punto de mayor retorno por esfuerzo de todo el plan.** Al terminar esta
fase ya te enteras cuando algo se rompe, agrupado y sin ruido.

| Código | Estado | Elemento | Notas |
|---|---|---|---|
| `F2-01` | ✅ | `alert-bus` como **receptor OTLP** | Keep evaluado y descartado para esta capa — D-025 |
| `F2-02` | ✅ | Normalización, deduplicación por huella, agrupación temporal | Huella de cardinalidad cerrada |
| `F2-03` | ⏳ | Correlación por topología: suprimir síntomas con causa aguas arriba | El grafo ya está en el registro |
| `F2-04` | ⏳ | `notifier`: Google Chat (Cards V2) + SMTP | Los *sinks* ya son una interfaz |
| `F2-05` | ✅ | **Divulgación progresiva**: un hilo que se actualiza | D-015 · `POST /incidents/{huella}/enrich` |
| `F2-06` | ⏳ | Canario sintético | En macOS es buena parte del nivel 0 |
| `F2-07` | ⏳ | Detectores de bucle de agente y fuga de coste | `tool_calls_per_run > P99` |
| `F2-08` | ✅ | Registro de aplicaciones con auto-descubrimiento | 11 apps · provisional + aviso |
| `F2-09` | ✅ | **Medir el presupuesto de latencia**: error → aviso | **p95 = 120 ms** contra 2 s de presupuesto · `scripts/measure_latency.py` |
| `F2-10` | ⏳ | *Dead man's switch* externo | Sin esto, un fallo de la plataforma parece silencio |

---

## F3 · Diagnóstico L3 y memoria de incidentes ⏳

| Código | Estado | Elemento | Notas |
|---|---|---|---|
| `F3-01` | ⏳ | Perfil `agents`: Temporal arrancado | |
| `F3-02` | ⏳ | Worker Temporal propio, dependencias mínimas | D-012: no compartir árbol con `aeon-ai` |
| `F3-03` | ⏳ | Agente de **triage** barato, modelo pequeño | Filtra antes de invocar al caro |
| `F3-04` | ⏳ | **L3**: diagnóstico de un disparo como activity suelta | D-013 |
| `F3-05` | ⏳ | Almacén de incidentes con **búsqueda vectorial** | pgvector; arranca vacío y se llena solo |
| `F3-06` | ⏳ | Agente de **memoria**: top-N incidentes similares | El de mayor retorno después del de RCA |
| `F3-07` | ⏳ | `mcp-obs`: herramientas de solo lectura sobre telemetría | |
| `F3-08` | ⏳ | **Banco de pruebas de incidentes** (5–10 con causa conocida) | Única forma honesta de medir |

---

## F4 · Investigación agéntica L4 ⏳

| Código | Estado | Elemento | Notas |
|---|---|---|---|
| `F4-01` | ⏳ | Workflow Temporal multi-paso, **una activity por herramienta** | D-012 |
| `F4-02` | ⏳ | Presupuesto duro: 40 tool calls, 300k tokens, 15 min | Informe parcial al agotarse |
| `F4-03` | ⏳ | **Shadow mode**: investiga y registra, no notifica | Hasta que el banco lo respalde |
| `F4-04` | ⏳ | Trazar el propio agente en Langfuse | Es una app LLM más |
| `F4-05` | ⏳ | Informe con evidencia citada, confianza y alternativas descartadas | |
| `F4-06` | ⏳ | **Prueba de inyección de prompt**: un log con instrucciones no cambia su comportamiento | |
| `F4-07` | ⏳ | WhatsApp para críticos fuera de horario | Plantilla *utility*, no *marketing* |

---

## F5 · Calidad, coste y deriva ⏳

| Código | Estado | Elemento | Notas |
|---|---|---|---|
| `F5-01` | ⏳ | `evaluator`: evals online sobre trazas muestreadas | |
| `F5-02` | ⏳ | Groundedness, consistencia, formato, rechazo | Formato con Pydantic, no LLM |
| `F5-03` | ⏳ | Alerta por **condición conjunta** deriva + caída de eval | Deriva sola es falsa alarma |
| `F5-04` | ⏳ | `drift-monitor` con Evidently | |
| `F5-05` | ⏳ | NannyML: rendimiento **sin etiquetas de verdad** | En scoring la verdad llega semanas después |
| `F5-06` | ⏳ | Agente de **FinOps**: atribución por app/funcionalidad/entorno | Las facturas suben aunque bajen los precios |
| `F5-07` | ⏳ | Agente de **poda de alertas**, semanal | Para este punto la fatiga ya duele |
| `F5-08` | ⏳ | Bandas de anomalía estadística | **No antes de 8 semanas de histórico** |

---

## F6 · Explicabilidad, postmortem y L5 ⏳

| Código | Estado | Elemento | Notas |
|---|---|---|---|
| `F6-01` | ⏳ | SHAP/Captum **desde el manejador de trazas**, no en la ruta de servicio | KernelSHAP inline es demasiado lento |
| `F6-02` | ⏳ | Deriva de atribución de features | Anticipa la degradación |
| `F6-03` | ⏳ | Portal de explicabilidad | |
| `F6-04` | ⏳ | Agente de **postmortem** desde la cadena de razonamiento | No desde el chat |
| `F6-05` | ⏳ | `mcp-remediate` con `approval_token` | |
| `F6-06` | ⏳ | **L5** solo para acciones reversibles | `set_rate_limit`, `scale_component` |
| `F6-07` | ⏳ | Informe de monitoreo post-mercado (AI Act, Art. 72) | Retención de 6 meses |

---

## F7 · Resto del portafolio ⏳

| Código | Estado | Elemento | Notas |
|---|---|---|---|
| `F7-01` | ⏳ | `argus scaffold <app>`: alta en un comando | |
| `F7-02` | ⏳ | Migrar las apps que **ya tienen OTel** | Cero líneas: solo variables de entorno |
| `F7-03` | ⏳ | Migrar las de logging propio | Una línea: el puente de logging |
| `F7-04` | ⏳ | `argus-go` y `argus-ts` | Contrato de entorno, no de API |
| `F7-05` | ⏳ | **Migrar el plano central al iMac** | Ejecutar el runbook de `F1-08` |

---

## Deuda técnica y hallazgos

| Código | Estado | Elemento | Notas |
|---|---|---|---|
| `X-01` | ⏳ | El runbook de migración se planificó en F0 y no se hizo | Se ensaya **en frío**, antes de necesitarlo |
| `X-02` | ⏳ | Sin CI: los tests y la validación se corren a mano | `scripts/verify.sh` es el contenido del pipeline |
| `X-03` | ⏳ | El perfil `genai` está configurado pero nunca arrancado | `F1-12` lo cubre |
| `X-04` | ⏳ | El perfil `agents` está configurado pero nunca arrancado | `F3-01` lo cubre |
| `X-05` | 🧪 | La cola persistente no se ha probado con un corte real | `F1-09` |
| `X-06` | ✅ | El regex de teléfono enmascaraba `service.namespace` | D-018, corregido con tests |
| `X-07` | ✅ | La rama de Langfuse reintentaba contra un host inexistente | D-019, extraída a overlay |
| `X-08` | ✅ | Healthcheck del Collector fallaba: imagen distroless | D-020 |
| `X-09` | ✅ | Cola persistente sin permisos: uid 10001 vs volumen de root | D-021 |
| `X-10` | ✅ | `pytest` no cargaba el conftest raíz con rutas explícitas | D-022 |
| `X-11` | ✅ | Puerto 9000 ocupado por otro proyecto | D-024, movido a 9010 |
| `X-12` | ✅ | El agente escuchaba en `127.0.0.1` **dentro** del contenedor | Inalcanzable por el mapeo de puertos; ahora `0.0.0.0` |
| `X-13` | ✅ | Gateway y agente se disputaban 4317/4318 en la misma máquina | El gateway cede a 14317/14318 — D-026 |
| `X-14` | ✅ | gRPC rechaza credenciales sin TLS | Agente→gateway pasa a OTLP/HTTP — D-027 |
| `X-15` | ✅ | El alert-bus no descomprimía gzip | El Collector comprime por defecto; el síntoma era "Wire format corrupt" |
| `X-16` | ✅ | El SDK mandaba cabeceras gRPC en mayúscula | gRPC las rechaza; ahora se pasan en minúscula |
| `X-17` | ✅ | Sin `.dockerignore`: el `.venv` entraba en el contexto de build | Builds lentos y capas de caché engañosas |
| `X-18` | ✅ | El modelo de autenticación no distinguía agente de gateway | D-028: el agente escucha en loopback y no pide token |

---

## Backlog abierto

Ideas evaluadas que **aún no están comprometidas**. Añadir aquí lo que surja.

| Código | Estado | Elemento | Valoración |
|---|---|---|---|
| `B-01` | 💭 | Agente de **seguridad de IA**: inyección de prompt, fuga de PII | Solo si expones apps fuera |
| `B-02` | 💭 | Agente de **capacidad**: GPU, colas, *thrashing* de modelos | Cuando la plataforma compita con la inferencia |
| `B-03` | 💭 | Agente de **riesgo de cambio** por despliegue | Necesita histórico de despliegues |
| `B-04` | 💭 | Agente de **runbooks** desde incidentes resueltos | Cuando haya suficientes que destilar |
| `B-05` | 💭 | Agente de **onboarding**: PR automático para instrumentar | Reevaluar pasando de 20 apps |
| `B-06` | 💭 | **OBI/eBPF** en las máquinas Linux | No funciona en macOS |
| `B-07` | 💭 | HyperDX o Grafana como UI de exploración | Hoy se consulta por SQL |
| `B-08` | 💭 | Vistas materializadas de ClickHouse para burn-rate | Camino templado, si vmalert se queda corto |
| `B-09` | 💭 | Instrumentación mínima del núcleo **Rust** de AIBank | Crates pre-1.0; acotar a las fronteras |
| `B-10` | 💭 | Índice PyPI privado (`devpi`) en el plano central | Hoy se resuelve por workspace |
| `B-11` | 💭 | Red privada tipo Tailscale con nombre estable | Necesario antes de `F1-10` |
| `B-12` | ❌ | Grafana OnCall | OSS archivado en marzo de 2026 |
| `B-13` | ❌ | `routing` connector para separar GenAI | Partiría las trazas — D-006 |
