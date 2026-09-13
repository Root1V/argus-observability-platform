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
| **F1** | Librerías y primeras apps | 🚧 | 12/14 |
| **F2** | Detección en tiempo real y alertas | ✅ | 10/10 |
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
| `F1-08` | ✅ | **Runbook de migración de máquina** | Ensayado **y ejecutado de verdad**: 448 spans antes y después |
| `F1-09` | ✅ | **Prueba de la cola persistente** | 100/100 spans; sobrevive al reinicio del propio agente |
| `F1-10` | ⏳ | Desplegar Collector agente en una **segunda máquina** | Valida D-003 de verdad |
| `F1-11` | 🚧 | Adoptar en 3 apps reales | **Piloto: `auth-service` de Prometheus.** Trazas llegando; a la espera de un cambio de una línea de su equipo |
| `F1-12` | ✅ | Perfil `genai`: Langfuse arrancado y verificado | Provisionado sin UI; mismo `trace_id` en ClickHouse y Langfuse — D-039 |
| `F1-13` | ✅ | **Librerías instalables desde fuera del workspace** | `make wheels` y etiquetas de git; nombres `argus-obs-*` — D-035, D-036 |
| `F1-14` | ✅ | **Dashboards** | Grafana provisionado: una aplicación y la plataforma — D-037 |
| `F1-15` | ✅ | **Canal de notificación verificable** | `make channel-test` mide entrega por canal; destapó 3 fallos — D-041, D-042, D-043 |
| `F1-16` | ✅ | **Canal del piloto: correo** | Chat pide Workspace; correo enhebrado verificado con SMTP local — D-044, D-045 |
| `F1-17` | ✅ | **Renombrado sin corte: Prometheus activo** | Alias en el registro, identidad normalizada en la huella, componentes sin conectar exentos — D-046, D-047 |
| `F1-18` | ✅ | **La sonda de silencio funciona de verdad** | Medía puntos y las series acumulativas se reexportan: un muerto parecía sano. Ahora mide crecimiento — D-049 |
| `F1-19` | ✅ | **Seudonimización implementada** | Estaba diseñada y no existía: UUIDs de usuario en crudo. Hasheados con sal, verificado — D-050 |
| `F1-20` | ✅ | **Canal de coordinación con Prometheus** | Fichero compartido; 5 respuestas y 8 entradas nuevas, incluido un incidente real suyo — D-052 |

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

## F2 · Detección en tiempo real y alertas ✅

**El punto de mayor retorno por esfuerzo de todo el plan.** Ya te enteras
cuando algo se rompe, agrupado y sin ruido: **23 señales entrantes producen 2
notificaciones**, y un error llega al aviso en **120 ms (p95)**.

| Código | Estado | Elemento | Notas |
|---|---|---|---|
| `F2-01` | ✅ | `alert-bus` como **receptor OTLP** | Keep evaluado y descartado para esta capa — D-025 |
| `F2-02` | ✅ | Normalización, deduplicación por huella, agrupación temporal | Huella de cardinalidad cerrada |
| `F2-03` | ✅ | Correlación por topología: suprimir síntomas con causa aguas arriba | Con promoción de síntomas huérfanos |
| `F2-04` | ✅ | Canales: Google Chat (Cards V2) + SMTP + WhatsApp | *Sinks* en proceso con cola, no servicio aparte — D-029 |
| `F2-05` | ✅ | **Divulgación progresiva**: un hilo que se actualiza | D-015 · `POST /incidents/{huella}/enrich` |
| `F2-06` | ✅ | Canario sintético | Sondas HTTP + de silencio, con confirmación |
| `F2-07` | ✅ | Detectores de bucle de agente y fuga de coste | Absolutos en el SDK, estadísticos en vmalert — D-032 |
| `F2-08` | ✅ | Registro de aplicaciones con auto-descubrimiento | 11 apps · provisional + aviso |
| `F2-09` | ✅ | **Medir el presupuesto de latencia**: error → aviso | **p95 = 120 ms** contra 2 s de presupuesto · `scripts/measure_latency.py` |
| `F2-10` | ✅ | *Dead man's switch* externo | Cero dependencias, fuera del compose — D-033 |

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
| `F5-08` | ⏳ | Bandas de anomalía estadística **y activar `platform/rules/agents.yaml`** | **No antes de 8 semanas de histórico** |

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
| `X-01` | ✅ | El runbook de migración se planificó en F0 y no se hizo | Hecho y ejecutado de verdad en `F1-08` |
| `X-02` | ⏳ | Sin CI: los tests y la validación se corren a mano | `scripts/verify.sh` es el contenido del pipeline |
| `X-03` | ✅ | El perfil `genai` está configurado pero nunca arrancado | Arrancado y provisionado en `F1-12` |
| `X-04` | ⏳ | El perfil `agents` está configurado pero nunca arrancado | `F3-01` lo cubre |
| `X-05` | ✅ | La cola persistente no se había probado con un corte real | Probada en `F1-09`, incluido reinicio del agente |
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
| `X-19` | ✅ | El registro marcaba como activas apps aún no instrumentadas | D-030: el canario las reportaba como silenciosas |
| `X-20` | ✅ | La sonda de silencio consultaba trazas, que están muestreadas | D-031: ahora consulta métricas, que no lo están |
| `X-21` | ✅ | El `alert-bus` no se trazaba a sí mismo | Le faltaba el middleware ASGI |
| `X-22` | ✅ | vmalert fallaba con 422 mientras los tests pasaban | D-034: manda array pelado, no `{"alerts": [...]}` |
| `X-23` | ✅ | La detección de bucles sin `args` daba falsos positivos | Sin argumentos no se puede saber si dos llamadas son iguales |
| `X-24` | ✅ | Las métricas del agente escuchaban en `127.0.0.1` dentro del contenedor | Mismo fallo que `X-12`, en otro sitio |
| `X-25` | ✅ | `pip install argus-sdk` traía un paquete ajeno de PyPI | D-035: confusión de dependencias |
| `X-26` | ⏳ | **Ningún canal de notificación real configurado** | Único bloqueante del piloto; necesita una credencial tuya |
| `X-27` | ✅ | Las métricas GenAI estaban definidas y **nadie las emitía** | Lo descubrió el dashboard; ahora salen solas de `genai()` |
| `X-28` | ✅ | Los prelanzamientos publicaban paquetes mutuamente inalcanzables | D-038: PEP 440 exige límites inferiores con prelanzamiento |
| `X-29` | ✅ | `.../dist` en la documentación se copiaba literal | `make install-cmd` imprime la ruta real; sin huecos que rellenar |
| `X-30` | ✅ | La imagen de MinIO ya no existe en Docker Hub | D-040: migrada a quay.io; fijar versión no protege de que desaparezca |
| `X-31` | ✅ | Langfuse intentaba crear tablas replicadas en un ClickHouse de un nodo | `CLICKHOUSE_CLUSTER_ENABLED=false` |
| `X-32` | ✅ | `argus@localhost` no pasaba la validación de Langfuse | Sin dominio de nivel superior; el error solo decía «Invalid environment variables» |
| `X-33` | ✅ | Langfuse no crea su bucket de S3: espera encontrarlo | Init de MinIO; el 500 solo se veía en los logs de Langfuse, no en los del Collector |
| `X-34` | ✅ | El registro se documentaba como recargable en caliente y no lo era | Lo descubrió el piloto; ahora recarga por `mtime` |
| `X-35` | ✅ | Modifiqué código de `edge-ai-inference` sin autorización | Revertido; convertido en solicitud con parche — ver `docs/solicitudes/` |

---

## Solicitudes a otros equipos

Cambios que la integración necesita en repositorios que **no son nuestros**. Se
preparan con parche, tests y evidencia; los aplica su equipo.

| Código | Estado | Proyecto | Petición |
|---|---|---|---|
| `S-01` | ✅ | **Prometheus** | `Resource.create()` **aplicado** el 13/09. Retiraron además su pila propia: somos el único destino — [respuesta](docs/solicitudes/prometheus-respuesta.md) |
| `S-02` | ⏳ | **Prometheus** | `traceparent`: nos piden dirigirlo. Proponemos `ARGUS_PROPAGATE=trusted` solo en `gateway`, **midiendo antes** si rinde |
| `S-04` | ⏳ | **Prometheus** | Retirar el alias `edge-ai-inference` cuando redesplieguen con el nombre nuevo |
| `S-05` | ⏳ | **Prometheus** | Rueda de `argus-obs-semconv` entregada; atributos del gateway pedidos en A-10 |
| `S-06` | ⏳ | **Prometheus** | `OTEL_SEMCONV_STABILITY_OPT_IN=http/dup`: usan las convenciones HTTP antiguas (A-11) |
| `S-07` | ⏳ | **Prometheus** | `service.version` y `service.instance.id` (A-12) |
| `S-08` | ⏳ | **Prometheus** | Sus spans de servidor no llevan atributos; probablemente el mismo middleware que S-02 (A-14) |
| `S-03` | 💭 | **Axonium** (`llm_arch_sdk/`) | Instrumentar con `argus-obs-semconv`. **En cambio ahora mismo**; esperar a que se estabilice |

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
| `B-07` | ✅ | Grafana con dashboards provisionados | Dos: una aplicación y la plataforma — D-037 |
| `B-08` | 💭 | Vistas materializadas de ClickHouse para burn-rate | Camino templado, si vmalert se queda corto |
| `B-09` | 💭 | Instrumentación mínima del núcleo **Rust** de AIBank | Crates pre-1.0; acotar a las fronteras |
| `B-10` | 💭 | Índice PyPI privado (`devpi`) en el plano central | Hoy etiquetas de git; **necesario antes del rollout** — D-036 |
| `B-14` | 💭 | **Registrar `argus-obs-*` en PyPI defensivamente** | La protección de D-035 depende de que sigan libres |
| `B-11` | 💭 | Red privada tipo Tailscale con nombre estable | Necesario antes de `F1-10` |
| `B-12` | ❌ | Grafana OnCall | OSS archivado en marzo de 2026 |
| `B-13` | ❌ | `routing` connector para separar GenAI | Partiría las trazas — D-006 |
