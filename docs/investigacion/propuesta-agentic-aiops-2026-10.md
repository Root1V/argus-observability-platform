# Análisis: «Propuesta de plataforma de Observabilidad Inteligente y Operaciones Autónomas (Agentic AIOps)»

> Documento de otro equipo, octubre de 2026, 39 páginas, orientado a banca
> (BBVA). Lo leyó Argus el 31/10/2026 para completar su backlog y revisar su
> objetivo. Las decisiones que salen de aquí están en **D-157**.
>
> **Corregido el mismo día por el dueño**: la primera versión descartaba Jev
> «por ser un proveedor externo y por la regla de Axonium». Lo correcto: Axonium
> sirve los modelos que **provee Prometheus**, y Prometheus ofrece modelos
> System One **abiertos** (Laya y Von). Jev es el privado de esa familia.

**Dos avisos sobre la fuente:**

- Las páginas 11–13, 17–19 y 21–23 son el CSS de tres diagramas Mermaid mal
  exportados. De ellas solo se aprovecha el texto de los nodos que quedó al
  final: la arquitectura de dos planos, el grafo de relaciones del ejemplo y la
  coordinación del orquestador.
- Las fechas y capacidades de productos (Toto 2.0, Chronos-2, TimesFM-3, Jev de
  TypeSafe AI, nightshift de Elastic, OTel graduado en CNCF) son las que afirma
  el documento. **No se han verificado de forma independiente.** Nada de lo que
  se adopta aquí depende de que sean exactas.

---

## 1. Qué propone el documento, sección por sección

| § | Págs. | Tema | Idea central |
|---|---|---|---|
| 1 | 1–2 | Resumen ejecutivo | Pasar de «las herramientas generan alertas» a «la plataforma entiende, investiga y coordina». Ciclo **observar → detectar → entender → actuar → aprender**. |
| 2 | 3–4 | Capacidades | Diez capacidades con un **contexto operacional común**, más una transversal: **observabilidad de servicios de negocio** («¿se están procesando transferencias?»). |
| 3 | 4–6 | Mercado | Dynatrace (IA causal sobre topología), Datadog (investigación por hipótesis), ServiceNow (procesos y responsables), Grafana/OTel (telemetría abierta), PagerDuty (guardias), Elastic (logs). **No sustituir lo existente: construir encima.** |
| 4 | 6–11 | Qué IA usar | **Cuatro familias, no un LLM para todo**: ML clásico para detectar; modelos fundacionales de series temporales para **prever** (la anomalía se construye sobre el error de la previsión); modelos de decisión tipados para clasificar; LLM para investigar y explicar. **Las acciones críticas, con políticas deterministas.** |
| 5 | 11–19 | Arquitectura | Plano de observabilidad y plano de inteligencia separados; **los agentes no son una segunda copia de los logs**. Bus de eventos con un ciclo de vida (`AnomalyDetected → … → RecoveryVerified`). **Grafo de contexto operacional**, no solo base vectorial, con fuente de verdad por relación. |
| 6 | 19–23 | Multiagente | 14 agentes; **no todos con LLM**. Orquestación en paralelo, no en cadena. **La notificación crítica nunca espera a la IA.** |
| 7 | 24–25 | Ejemplo | Un caso de autenticación tras un despliegue. **Correlación no es causalidad**: hay que buscar una cadena verificable, incluidos los servicios *no modificados* con el mismo fallo. Estados de evidencia: hecho observado frente a hipótesis. |
| 8 | 25–27 | Integraciones | CI/CD, ITSM, CMDB, guardias, chat, documentación, infraestructura, feature flags, IAM. **El LLM no inventa responsables.** El registro de cambios recoge los formales **y los reales detectados en producción**. |
| 9 | 27–29 | Stack | OTel, Mimir/VictoriaMetrics, Loki/OpenSearch, Tempo, Kafka (Flink si hace falta), LangGraph, Temporal, PostgreSQL, S3, pgvector, OPA. **Cada detector con su versión, evaluación y rollback.** |
| 10 | 29–31 | Gobierno | **Niveles de autonomía 0–3** (observación, coordinación, remediación asistida, autónoma limitada). Controles: el modelo propone y la política decide, mínimo privilegio, aprobaciones trazables, reversibilidad, PII filtrada antes de la IA, salidas de herramientas como datos no confiables, auditoría completa y **alertas críticas independientes de la IA**. |
| 11 | 31–32 | Diferenciales | SLO de negocio, predicción, **inteligencia de cambios** (regresión antes y después de un despliegue), **memoria con la resolución real** («no narraciones de agentes») y **observabilidad de la propia IA**. |
| 12 | 32–33 | Consola | Centrada en el incidente; **conversar con los agentes**, con cada respuesta enlazando la consulta y la evidencia para reproducirla. |
| 13 | 33–34 | Comprar o construir | Una capa propia de inteligencia sobre las plataformas existentes. |
| 14 | 34–35 | Roadmap | No catorce agentes a la vez. Piloto con 3–5 servicios, un flujo de negocio de extremo a extremo, **reproducción de incidentes históricos y simulaciones** antes de activar nada en producción. |
| 15 | 36–37 | Indicadores | **MTTD, MTTA, MTTR**, precisión y recall de alertas, reducción de duplicados, **Top-1/Top-3 de causa raíz**, tiempo hasta la primera evidencia útil, exactitud de asignación, precisión de correlación con cambios, % de incidentes con evidencia, coste por servicio. Objetivos **solo después de una línea base**. **Feedback de operadores desde el primer día.** |
| 16 | 37–39 | Propuesta final | Seis preguntas que la plataforma tiene que contestar con evidencia: **qué falla, a quién afecta, qué cambió, cuál es la causa más probable, quién lo atiende y qué opciones seguras hay**. La autonomía viene después. |

---

## 2. Contraste con Argus, capacidad por capacidad

✅ cubierto · 🟡 en parte · ❌ hueco · ➖ no aplica a este contexto

| Capacidad (§2) | Argus hoy | Estado | Qué falta |
|---|---|---|---|
| Observabilidad unificada | OTel con trazas, métricas y logs en ClickHouse y VictoriaMetrics. GenAI con tokens y coste. | 🟡 | Perfiles (profiling) y RUM: no hacen falta todavía (§5). |
| Detección inteligente | SLO burn-rate multi-ventana (F0-06), canario, sonda de silencio, bucle y fuga de coste de agentes (F2-07). | 🟡 | Bandas estadísticas y modelos de previsión: F5-08 (necesita 8 semanas de histórico); TSFM en **B-53**. |
| Correlación de eventos | Huella, agrupación de 60 s, supresión por topología; 23 señales → 2 avisos (F2). Varios componentes con el mismo fallo (B-40). | ✅ | Medirla de forma continua: **B-48**. |
| Inteligencia de contexto | Registro de apps y dependencias; el expediente ve procesos, versiones y reinicios (B-38) y la línea base (B-35). | 🟡 | Cambios: hoy se ven como un reinicio, no se comparan ventanas antes/después → **B-51**. |
| Investigación agéntica | L3 de reglas en producción; juez con modelo y L4 con `mcp-obs` en sombra; banco de 11 casos; defensas contra inyección (F4-06). | 🟡 | Saber si acierta en la vida real: sin feedback no hay Top-1 real → **B-46**. |
| Gestión de incidentes | Dueño y canal por app; divulgación progresiva; `POST /incidents/{id}/ack`. | 🟡 | El acuse no se puede pulsar desde el aviso, no guarda **cuándo** y no hay escalado → **B-47**. |
| Remediación asistida | — | ❌ | F6-05 (`mcp-remediate` con aprobación) y F6-06 (L5 reversible). |
| Prevención | — | ❌ | F5-08 y **B-51**. |
| Aprendizaje operacional | Memoria por clase y por parecido (F3-05, F3-06); el aviso muestra «VISTO ANTES». | 🟡 | La memoria guarda lo que **L3 anotó, sin confirmar**. El documento pide la resolución real → **B-46**. Postmortem: F6-04. |
| Gobierno y seguridad | Redacción y seudonimización en el Collector, herramientas de solo lectura, delimitadores neutralizados, citas y cifras verificadas, presupuesto duro, diarios de synaptum. | ✅ | Autonomía formalizada en niveles para cuando llegue F6 (nota en F6-05/06). |
| *Transversal:* servicios de negocio | SLO por app; sonda de silencio. | ❌ | «¿Prosodia está doblando? ¿Veritium verifica?» → **B-50** (generaliza F2-19). |

**Los 14 agentes (§6) frente a Argus:**

| Agente del documento | En Argus |
|---|---|
| 1 Signal & Detection | vmalert, canario y SDK (deterministas) ✅ |
| 2 Correlation | `alert-bus` (determinista) ✅ |
| 3 Severity & Impact | Severidad por regla; el impacto es técnico (ALCANCE) 🟡 → B-50 |
| 4 Notification | `alert-bus` y *sinks*; el LLM no decide a quién avisar (PLAN §8.1) ✅ |
| 5 Ownership | El registro, sin escalado 🟡 → B-47 |
| 6 Investigation | L4 con `mcp-obs`, en sombra 🟡 |
| 7 Change Intelligence | Procesos y versiones en el expediente 🟡 → B-51 |
| 8 RCA | Reglas de L3 y juez con modelo 🟡 |
| 9 Knowledge & Similarity | Memoria (F3-05, F3-06) ✅ (sin resolución real: B-46) |
| 10 Remediation Planner | F6-05 ❌ |
| 11 Execution | F6-06, solo acciones reversibles ❌ |
| 12 Verification | La reconciliación cierra el incidente cuando cesan las señales; no hay verificación tras una acción ❌ (va con F6) |
| 13 Communication | Aviso progresivo ✅; resumen ejecutivo → B-48 |
| 14 Learning | Postmortem F6-04 y poda de alertas F5-07 ❌ |

**Lo que el documento pide y Argus ya hace igual**, a veces con un nombre
distinto: la notificación no espera a la IA (D-015 y el *dead man's switch*); el
modelo propone y el código decide (`a_veredicto`, citas y cifras verificadas);
los agentes consultan el almacén con herramientas fijas en vez de copiarlo
(D-013, `mcp-obs`); la IA está especializada por tarea (reglas, juez,
investigador, embeddings para memoria); se evalúa con incidentes reales
congelados antes de activar (banco F3-08, sombra F4-03); las salidas de
herramientas se tratan como datos no confiables (F4-06); y se observa a la
propia IA (F5-01, F5-02).

---

## 3. Lo que se adopta: entradas nuevas del backlog

En orden de valor. Las cuatro primeras se apoyan entre sí: el feedback (B-46)
da las etiquetas, el acuse y la línea de tiempo (B-47, B-49) dan los tiempos, y
el cuadro (B-48) lo mide todo.

| Código | Entrada | De qué parte del documento |
|---|---|---|
| **B-46** | **Feedback del operador desde el aviso**: «causa correcta / incorrecta / la real fue…», «alerta útil / ruido». Alimenta el banco, la memoria con causa **confirmada**, la poda de F5-07 y la precisión de B-48. | §11D, §15 («feedback desde el primer día»), §2 aprendizaje |
| **B-47** | **Acuse desde el aviso, con hora, y escalado si nadie responde.** Con un solo dueño, escalar es reenviar por otro canal a los N minutos. Da el MTTA. | §2 gestión, §8 guardias, §15 MTTA |
| **B-48** | **Cuadro de indicadores operacionales**: MTTD, MTTA, MTTR, precisión de alertas, reducción de duplicados, Top-1/Top-3 de causa (banco y feedback), tiempo hasta la primera evidencia, % de incidentes con evidencia y coste de IA por incidente. **Primero la línea base, después los objetivos.** | §15 |
| **B-49** | **Línea de tiempo del incidente como eventos**: detectado → notificado → reconocido → diagnosticado → investigado → propuesta → aprobada → ejecutada → recuperación verificada. Es la base de B-48 y del portal B-43. | §5.3 (ciclo de vida de eventos) |
| **B-50** | **Flujos de negocio por app en el registro**: un SLI de «el trabajo sale», no de «el servidor responde». Prioriza por impacto real y generaliza F2-19. | §2 transversal, §11A |
| **B-51** | **Regresión tras un cambio**: cuando cambia `service.version` (o reinicia con otra configuración), comparar errores y latencia antes y después y anotarlo en el expediente. Capta los cambios **reales**, no solo los formales; las *releases* de GitHub, como fuente formal, son opcionales. Absorbe B-03. | §7, §8, §11C |
| **B-52** | **Simulaciones de fallo controladas** sobre una app de demostración **propia**, nunca sobre la de otro equipo: casos de banco con causa conocida y MTTD medido de punta a punta. | §14 piloto, §15 |
| **B-53** 💭 | **Modelos fundacionales de series temporales** (Chronos-2, Toto) frente a las bandas estadísticas de F5-08, con fallos reales. Axonium sirve los modelos que **provee Prometheus**: habría que pedirle a su equipo que los ofrezca. | §4.2 |
| **B-54** 💭 | **Triage con un modelo de decisión System One**: Prometheus ofrece modelos System One abiertos (**Laya** y **Von**), que contestan con una elección o una probabilidad tipada en vez de texto. Se usarían por Axonium para «¿rutinario, investigable o crítico?», **solo cuando B-46 dé etiquetas con las que medirlos** contra el triage de reglas de F3-03. Hoy el cliente de Argus no los ve: hay que pedir el permiso. | §4.3 |
| **B-55** 💭 | **Profiling continuo** (perfiles de OTel), cuando aparezca una pregunta de CPU o memoria que las trazas no contesten. | §2, §5.2 |

**Notas que se añaden a entradas existentes:**

- **B-43** (portal): modo conversacional («¿qué cambió antes?») sobre `mcp-obs`,
  con cada respuesta **enlazando la consulta y la evidencia** para reproducirla
  (§12).
- **F4-04** (Langfuse para el agente): medir también los errores de
  herramientas, los fallos de conectores, la utilización del contexto y las
  acciones rechazadas (§11E).
- **F6-05 / F6-06**: los niveles de autonomía 0–3 del §10 encajan con L3/L4/L5.
  Argus está en el **nivel 0** (solo lectura) y el notificador ya es nivel 1. El
  paso a 2 y 3 exige una política de ejecución **como código auditable** y una
  verificación tras cada acción (agente 12).
- **F5-08**: compararlo con B-53 antes de quedarse con un detector.

---

## 4. Lo que no se adopta, y por qué

| Propuesta | Por qué no, aquí |
|---|---|
| Kafka (y Flink) como bus | El volumen es de un portafolio, no de un banco: el `alert-bus` procesa el flujo en memoria con un p95 de 120 ms (F2-09). Un Kafka sería una pieza más que vigilar en un portátil. Se revisa si el volumen lo pide. |
| Mimir, Loki, Tempo y OpenSearch | Argus decidió un ClickHouse unificado: SQL sobre *wide events*, que es lo que usan los agentes (PLAN §2). VictoriaMetrics ya cumple el papel de Mimir. |
| LangGraph y Temporal | Los agentes se construyen con synaptum, que trae su propia durabilidad (D-135, F4-01 verificado con `kill -9`). |
| PostgreSQL y pgvector para incidentes y memoria | SQLite basta para unos 850 incidentes al mes (D-148). Se revisa si crece. |
| CMDB, ServiceNow, PagerDuty, IAM/PAM, DORA | Un solo dueño, un portafolio personal y ninguna obligación bancaria. El registro de apps es la CMDB y Telegram, el canal. |
| Jev | Es un modelo System One **privado**. La misma familia está disponible en abierto por Prometheus (Laya, Von), que es la vía de B-54: Axonium sirve los modelos que provee Prometheus, y no hace falta un proveedor externo. |
| Grafo de contexto en una base de grafos | El registro, con sus dependencias y la topología del `alert-bus`, cubre las relaciones de hoy. Una base de grafos se justificaría con muchas más apps y relaciones (el propio documento lo condiciona a eso). |
| OPA | Hasta que exista F6 no hay acciones que autorizar. Entonces se decide entre OPA y una política propia como código. |

---

## 5. El objetivo: una reformulación propuesta

El objetivo actual de Argus (PLAN, «Resumen en 12 líneas») es sobre todo de
**arquitectura**: plano central portátil, catálogo abierto, tres caminos y una
flota de agentes. Dice **cómo**, pero no **qué tiene que poder contestar**, y
por eso no se puede medir.

El §16 del documento da esa medida. Adaptado a Argus:

> **Argus tiene que poder contestar, con evidencia reproducible, seis preguntas
> sobre cualquier incidente del portafolio: qué está fallando, a quién afecta,
> qué cambió, cuál es la causa más probable, quién debe atenderlo y qué opciones
> seguras hay para recuperarlo.** La automatización viene después de que esas
> respuestas sean fiables, y se mide con B-48.

| Pregunta | Hoy | Qué la completa |
|---|---|---|
| ¿Qué está fallando? | ✅ detección en tiempo real, agrupada (F2) | — |
| ¿A quién afecta? | 🟡 alcance técnico (ALCANCE, hermanos) | B-50 |
| ¿Qué cambió? | 🟡 procesos y versiones en el expediente (B-38) | B-51 |
| ¿Causa más probable? | 🟡 reglas en producción, modelo y L4 en sombra, banco de 11 | B-46, B-48 |
| ¿Quién lo atiende? | ✅ dueño y canal por app | B-47 |
| ¿Opciones seguras? | ❌ | F6-05, F6-06 |

Y los principios del documento que Argus ya practica, escritos para que no se
pierdan: **evidencia antes que inferencia**, **IA especializada por tarea**,
**el modelo propone y el código decide**, **la alerta crítica no depende de la
IA**, **un humano responsable de las decisiones críticas** y **evolución
incremental**, medida antes de activar.
