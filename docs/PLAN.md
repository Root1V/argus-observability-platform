# Argus — plataforma de observabilidad, AIOps agéntico y explicabilidad

> Proyecto: `app_monitoring_explainability` (directorio vacío — se construye desde cero)
> Plan v3. Cambios sobre v2: topología multi-host portátil (macOS ahora, no Linux), catálogo abierto de aplicaciones, ruta de migración de bajo coste, y el modelo de capas API/SDK que resuelve el caso Axonium.

---

## Resumen en 12 líneas

1. **El plano central es portátil**: hoy MacBook Pro M4 Max, mañana iMac, y las apps pueden estar en esa misma Mac, en otra, o en un servidor. Esto obliga a una topología de **dos niveles de Collector** donde las apps apuntan siempre a `localhost` y nunca saben dónde está el central.
2. **Corrección importante frente al plan anterior: eBPF no funciona en macOS.** Es tecnología del kernel Linux. El nivel "sin tocar código" se resuelve de otra forma en Mac (§5.3).
3. **El Collector local guarda en disco lo que no puede enviar.** Con un portátil que se suspende y cambia de red, sin cola persistente pierdes telemetría cada vez que cierras la tapa.
4. **El número de aplicaciones es abierto**, no quince. El registro se auto-alimenta: un servicio nuevo que emite telemetría aparece solo, como *provisional*, y la plataforma avisa en vez de aceptarlo en silencio.
5. **Migrar una app existente cuesta minutos, no días**: cambiar una variable de entorno si ya tiene OTel, y un puente de logging que hace que tus `logger.info()` actuales sigan funcionando y ganen correlación con trazas sin reescribir ninguna llamada.
6. **Axonium NO importa el SDK de observabilidad.** Importa solo la API de OTel más un paquete de convenciones minúsculo. Esa es la respuesta correcta y tiene consecuencias grandes (§6).
7. **Una librería propia y ligera**, desde cero, con el contrato de diseño de las librerías de instrumentación: *segura*, *idempotente*, *no-op si no está configurada*.
8. **Identidad de dos niveles**: `service.namespace` = la aplicación, `service.name` = el sub-componente. Es lo que hace consultable un portafolio que crece.
9. **Tiempo real por diseño: tres caminos, no uno.** El caliente detecta **sobre el flujo, sin tocar la base de datos** y sin esperar al tail sampling → **~2 s del fallo al aviso**. El templado deriva métricas en vuelo. El frío almacena la traza completa para los agentes. Sin esa separación, detectar tarda minuto y medio (§7.0).
10. **Notificación de divulgación progresiva**: un solo hilo que se actualiza — aviso en segundos, informe del agente cuando esté. Nunca dos notificaciones para el mismo incidente.
11. **Go deja de ser el caso difícil**: la v1 estable de instrumentación en compilación (julio 2026) lo instrumenta con un comando en el build, sin agente, sin eBPF, y **funciona en macOS**. Rust es ahora el único punto frágil.
12. **Una flota de agentes, no dos.** 17 roles evaluados, 8 recomendados. El de **memoria de incidentes** es el de mayor retorno después del de RCA, y casi nadie lo construye. **Para en la Fase 2 y ya has ganado**: 30 % del esfuerzo, 70 % del beneficio.

---

## 1. Contexto

### El problema

Tienes aplicaciones desplegables repartidas en ~40 directorios de proyecto, y el número **crece**. Casi todas usan IA: LLM local (llama.cpp, vLLM, Ollama), agentes (LangGraph, CrewAI, frameworks propios), RAG sobre pgvector y Qdrant, ML tradicional con torch. Cada una tiene sub-componentes: API, worker, scheduler, CLI, servidor de modelos, frontend.

Hoy cada aplicación se monitorea por separado, o no. Ocho repos tienen OpenTelemetry pero cada uno exporta a un backend aislado, así que **no existe una sola traza que cruce dos servicios**. El logging no tiene estándar. No hay alertamiento en ninguna. No hay trazabilidad de prompts, tokens ni coste en producción. Y no hay nadie de guardia.

El coste real no es la falta de dashboards: es que **tu atención no escala**, y menos a un portafolio que crece.

### Restricciones de diseño que impone tu realidad

- **El plano central es móvil.** Hoy MacBook Pro M4 Max, luego iMac. Se suspende, cambia de red, y algún día se migra entero a otra máquina. El diseño tiene que sobrevivir a las tres cosas sin tocar ninguna aplicación.
- **Las aplicaciones están repartidas.** Misma Mac, otra Mac, u otro servidor. Redes distintas, sistemas operativos distintos.
- **macOS, no Linux.** Esto invalida una de las piezas del plan anterior (§5.3) y hay que decirlo claro en vez de descubrirlo al implementar.
- **El catálogo es abierto.** No son quince apps fijas: añadir la dieciséis tiene que costar lo mismo que la tercera.
- **Independencia total entre apps.** No hay punto común por el que pase todo. Nada de arquitecturas con embudo único.
- **Sub-componentes heterogéneos.** API, worker, CLI y servidor de modelos no se instrumentan igual ni tienen el mismo ciclo de vida.
- **Poliglota.** Python 3.11–3.13 domina, pero hay Go, Rust, Java y TypeScript. El contrato es de variables de entorno y convenciones, no una API de Python.
- **Migración voluntaria y de bajo coste.** Puedes pedir a las apps existentes que migren, pero solo si el coste es de minutos. Es el requisito nº 1.
- **Tienes SDKs propios en medio.** Axonium (consumo de inferencia local de Prometheus), synaptum (agentes), y otros. Son librerías que muchas apps importan, y eso abre una decisión de arquitectura concreta (§6).

### Decisiones ya tomadas

| Decisión | Elección |
|---|---|
| Dónde corre el plano central | **MacBook Pro M4 Max → iMac**, self-hosted, podman/docker-compose, con portabilidad como requisito |
| Dónde están las apps | Misma Mac, otras Macs, otros servidores — topología mixta |
| Backend de telemetría | **ClickHouse unificado**: ClickStack (infra) + Langfuse v3 (GenAI), misma base, arranque en modo ligero |
| Autonomía de agentes | **Escalonado L3 → L4 → L5** |
| Canales | **Google Chat**, **Email (SMTP)**, **WhatsApp Business API** |
| Origen del código | **Todo desde cero.** Nada se reutiliza de los proyectos existentes |

---

## 2. Estado del arte 2026

Lo que condiciona el diseño. Fuentes en §13.

### 2.1 Instrumenta contra un estándar; el backend es un detalle

El consenso de 2026 es no atar la observabilidad a ningún proveedor en la capa que importa. Para IA ese estándar son las **GenAI semantic conventions de OTel**, que se movieron a su propio repositorio y siguen **experimentales** — hay que fijar la versión y tratar las subidas como migraciones planificadas.

### 2.2 Las convenciones GenAI

- **Span**: `{gen_ai.operation.name} {gen_ai.request.model}` → `chat qwen2.5-coder-7b`.
- **Operaciones**: `chat`, `text_completion`, `embeddings`, `generate_content`, `retrieval`, `execute_tool`, `create_agent`, `invoke_agent`.
- **Obligatorios**: `gen_ai.provider.name`, `gen_ai.request.model`. (`gen_ai.system` está obsoleto.)
- **Recomendados**: `gen_ai.response.model` (difiere del pedido por alias y routing, y esa diferencia explica incidentes), `gen_ai.response.id`, `gen_ai.response.finish_reasons`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.cache_read.input_tokens`.
- **Métricas**: `gen_ai.client.operation.duration` y `gen_ai.client.token.usage`, histogramas, desde el día uno. `gen_ai.token.type` como **dimensión**, no dos instrumentos.
- **Agentes**: span padre `invoke_agent` con hijos alternados `chat` y `execute_tool`. Tools con `gen_ai.tool.name`, `gen_ai.tool.type`, `gen_ai.tool.call.id`. Multi-turno con `gen_ai.conversation.id`.
- **Contenido**: la guía canónica dice **eventos de span**. **Langfuse no lo implementa así** — ver §5.7.
- **Opt-in en Python**: `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`.

### 2.3 API vs SDK: la regla que decide el caso Axonium

Esta es la pieza de la especificación que responde a tu pregunta, y es tajante:

- **Una librería debe depender solo de la API de OpenTelemetry, nunca del SDK.** La API es un conjunto de abstracciones con implementaciones no operativas.
- Si la aplicación que la importa **no** inicializa un SDK, la instrumentación **no hace nada y no tiene impacto en el rendimiento**. El compilador puede a menudo eliminar la cadena de llamadas entera.
- Si **sí** lo inicializa, la instrumentación se enciende sola, **usando la configuración, los backends y las reglas de muestreo de la aplicación**.
- Y el motivo por el que la regla es dura: *una librería que depende del SDK en vez de la API rompe todo el sentido de la separación — impone en silencio su versión del SDK y sus opiniones sobre exportadores a todo el que dependa de ella.*

Con quince o más aplicaciones que importan Axonium, esto no es teoría: es la diferencia entre una migración tranquila y un infierno de dependencias.

### 2.4 Diseño de librerías de instrumentación

Los principios que la comunidad OTel da a autores de librerías:

- **Segura**: nunca tumbar la aplicación; degradar en silencio.
- **Difícil de usar mal**: instrumentación idempotente.
- **No sorprendente**: simple, idiomática, escueta.

Y la estrategia de adopción: **empezar por auto-instrumentación para tener línea base, y añadir manual solo para las operaciones de negocio que la automática no puede capturar.**

### 2.5 La propagación de contexto es donde todo se rompe

W3C Trace Context funciona solo en HTTP. Fuera de HTTP no: **hacer que `traceparent` fluya por las cabeceras de mensajes de Kafka, para que los consumidores se unan a la traza de origen en vez de empezar una nueva, requiere trabajo explícito en casi todos los servicios.** Igual con Celery y colas de Redis.

Y los propagadores deben configurarse **de forma idéntica en todos los servicios y lenguajes**: si uno usa W3C y otro no, la traza se parte y nadie se entera.

### 2.6 Zero-code: qué cubre y dónde no llega

- **OBI / eBPF** (antes Grafana Beyla, donado a OTel): captura spans, métricas RED y relaciones de red sin tocar código ni reiniciar, y **se auto-desactiva en los procesos que ya emiten telemetría**. Pero funciona **solo sobre cargas de trabajo Linux soportadas** — es tecnología del kernel Linux. Ver la consecuencia en §5.3.
- **`opentelemetry-instrument` CLI**: envuelve el arranque de una app Python y auto-instrumenta las librerías conocidas. Funciona en macOS.

### 2.7 Resiliencia del Collector

La extensión de almacenamiento da persistencia en disco para no perder telemetría durante cortes de red, caídas del backend o reinicios del Collector. La cola de envío escribe a un **WAL en disco** (bbolt) antes de intentar exportar, así que **absorbe cortes de backend y redes lentas sin pérdida**. Si el Collector muere con elementos en la cola, al arrancar los recoge y continúa.

Para un plano central que vive en un portátil que se suspende, esto no es un extra: es el mecanismo que hace viable la arquitectura.

### 2.8 Observabilidad de IA: los modos de fallo son otros

APM tradicional no sirve para agentes: **un agente puede entrar en bucle, llamar a la herramienta equivocada o alucinar, y devolver un 200 con latencia normal.** Hay que vigilar bucles y trayectorias anómalas (tool calls sobre el P99), errores de tool-calling, **fuga de coste** (10× el coste por ejecución en silencio), y **alucinación silenciosa** — sin evaluación solo puedes alertar de latencia y errores, nunca de calidad.

### 2.9 Evaluación online

Muestrear trazas de producción y puntuarlas en línea (LLM-as-judge, código, modelos pequeños). Distinguir alucinación factual, no soportada por el contexto recuperado (groundedness), e inconsistente con el contexto previo.

**Regla de oro**: alertar sobre la **condición conjunta** de deriva de entrada + caída medible de eval. Deriva sin impacto en eval es falsa alarma y quema la guardia.

### 2.10 AIOps agéntico: la escalera y la flota

La "AI Investigation Capability Ladder": L0 manual, L1 correlación, L2 timeline resumido, L3 diagnóstico de un disparo, L4 investigación agéntica multi-paso, L5 bucle cerrado con aprobación. AWS DevOps Agent y Azure SRE Agent llegaron a GA en marzo de 2026 y operan en **L4**. La industria no está en L5.

El patrón multi-agente ya es producción: el ciclo del incidente se descompone en **especialistas que coordinan** — detección, correlación, investigación, remediación — en vez de un agente monolítico.

Advertencia crítica: la correlación solo funciona cuando el sistema **conoce la topología real entre servicios**. Sin mapa, correlaciona ruido.

### 2.11 Memoria de incidentes: lo más infravalorado

Ante un incidente nuevo, el agente busca en **memoria episódica** eventos pasados similares. Si hay coincidencia, **corta el diagnóstico en seco**: *"la última vez que vi este patrón, la causa fue X y lo arregló Y"*.

La búsqueda es por **similitud semántica vectorial**, no textual: una alerta sobre *"API gateway devolviendo 502"* encuentra *"timeout de servicio upstream causando fallos de gateway"* porque el significado subyacente es el mismo aunque el vocabulario sea distinto.

Barato de construir, mejora solo con el tiempo, y casi nadie lo hace.

### 2.12 En 2026 el problema dominante es la fatiga de alertas

Textualmente: *para la mayoría de los equipos en 2026, el problema principal es la fatiga de alertas más que las caídas.* Hace falta ajuste, líneas base y telemetría limpia **antes** de poder depender de la predicción.

Lo que funciona: deduplicación, agrupación, filtrado, enrutamiento por rol, y **SLO multi-ventana con burn-rate** en vez de umbrales estáticos. Para anomalía estadística, bandas con recording rules — pero modelar estacionalidad diaria necesita **≥8 semanas de histórico**.

### 2.13 TokenOps

Los precios por token se desploman **y sin embargo las facturas de IA suben**, porque las cargas agénticas multiplican los tokens por tarea más rápido de lo que caen los precios unitarios. Una sola tarea de usuario dispara **10–20 llamadas al LLM**: planificación, selección de herramienta, procesamiento del resultado, validación, reintentos.

La práctica que define la disciplina: **etiquetar cada llamada con metadatos** — equipo, funcionalidad, entorno, caso de uso — para poder **atribuir** el consumo en vez de recibir un número opaco a fin de mes.

### 2.14 Cumplimiento

Anexo III del AI Act europeo en vigor el **2 de agosto de 2026**, sanciones hasta 15 M€ o 3 % de facturación. **Art. 12**: registro automático de eventos durante toda la vida del sistema. **Arts. 19 y 26**: retención mínima de **6 meses**. **Art. 72**: monitoreo post-mercado continuo. Y supervisión humana efectiva: la salida no puede ser una caja negra, con SHAP/LIME en dashboards como vía citada.

### 2.15 Backend

ClickHouse adquirió Langfuse en enero de 2026. Langfuse v3 movió las trazas a ClickHouse y trata OTel como ingesta de primera clase — **OTLP sobre HTTP, gRPC todavía no**. Frente a LGTM (cuatro sistemas, cardinalidad problemática en Loki, correlación frágil), ClickHouse unificado da **SQL arbitrario sobre wide events**, que es exactamente lo que el agente de RCA necesita. Langfuse v3 self-hosted pide Postgres + ClickHouse + Redis + S3, mínimo 4 vCPU y 16 GiB.

### 2.16 Madurez poliglota: el mapa real en 2026

**OpenTelemetry se graduó de la CNCF en mayo de 2026**, alcanzando el nivel máximo de madurez de la fundación junto a Kubernetes y Prometheus. La apuesta es segura.

Pero la madurez **no es uniforme por lenguaje ni por señal**: las trazas son estables en casi todos, las métricas en la mayoría, y **los logs siguen por detrás** en varios SDKs.

Y hay una novedad de 2026 que cambia el plan anterior: **en julio de 2026 salió la v1 estable de OpenTelemetry Go Compile-Time Instrumentation**, lanzada conjuntamente por Alibaba y Datadog. Instrumenta una aplicación Go **con un solo comando, sin tocar código de negocio**:

- Se engancha al proceso de build con `-toolexec` e **inyecta la instrumentación al compilar**. El binario resultante la lleva dentro: sin agente aparte, sin sidecar, sin programa eBPF que cargar.
- **Sin sobrecoste de runtime añadido**, porque el código inyectado se beneficia de las mismas optimizaciones que el resto de la aplicación. En comparativas de rendimiento el enfoque de compilación logra el mayor rendimiento, mientras que el de eBPF queda por debajo por el coste de cruzar la frontera del kernel en cada llamada interceptada.
- **Funciona donde eBPF no**: eBPF necesita Linux y privilegios elevados; la instrumentación en compilación funciona en un rango mucho más amplio de entornos — **incluido macOS**.
- v1 cubre `net/http`, `database/sql`, gRPC, Redis y métricas del runtime de Go.
- Y **se combinan**: la instrumentación en compilación aporta los spans genéricos de la librería estándar y las dependencias, la manual añade los de semántica de negocio, y ambos conjuntos **se cosen en una sola traza automáticamente**.

**Rust es el punto débil, y conviene decirlo**: tiene un perfil de madurez inverso al resto — logs y métricas se estabilizaron **antes** que las trazas — y **todos los crates siguen pre-1.0**, incluso los de las señales estables.

### 2.17 Tiempo real: dónde está la latencia

Una tubería de observabilidad "normal" acumula latencia en cada salto, y la suma es de minutos. Lo que la investigación de 2026 dice sobre cómo comprimirla:

- **Las vistas materializadas de ClickHouse funcionan como procesadores de flujo ligeros**: ejecutan transformaciones **en el momento de la inserción**, desplazando el trabajo del tiempo de consulta al de ingesta. Con eso se construye una tubería de alertamiento que **detecta y notifica en segundos desde que ocurre el evento**.
- **Los connectors del Collector derivan señales en vuelo**, sin pasar por la base de datos. El `spanmetrics` connector agrega spans en métricas RED (peticiones, errores, duración) dentro del propio Collector. El **`signaltometrics`** connector generaliza la idea con expresiones OTTL configurables, permitiendo extraer métricas arbitrarias de trazas y logs sobre la marcha.
- Un connector es **el exportador de una tubería y el receptor de otra**, que es exactamente el mecanismo para separar caminos con latencias distintas dentro de un mismo Collector.

### 2.18 Wide events y deriva

**Wide events** (*canonical log line* de Stripe): una línea estructurada y ancha por unidad de trabajo, al final, con todo el contexto. Las trazas dan el flujo **entre** servicios; los wide events, el contexto **dentro** de uno. Lo ideal es que tus wide events **sean** tus spans enriquecidos.

**Deriva**: *data drift* = cambio en P(X); *concept drift* = cambio en la relación entrada→salida; *model drift* = el síntoma en las métricas. Evidently para distribución; **NannyML para estimar rendimiento sin etiquetas de verdad**, que es lo normal en producción.

---

## 3. Topología: el plano central es portátil

Esta sección es nueva y es la que más cambia respecto al plan anterior.

### 3.1 Dos niveles de Collector, y las apps nunca saben dónde está el central

```
┌─ Mac de desarrollo ────────┐   ┌─ iMac / otro Mac ─────────┐   ┌─ Servidor Linux ─────────┐
│  app-a ─┐                  │   │  app-d ─┐                 │   │  app-f ─┐                │
│  app-b ─┼─► :4318          │   │  app-e ─┼─► :4318         │   │  app-g ─┼─► :4318        │
│  app-c ─┘   Collector      │   │           Collector       │   │   OBI ──┘   Collector    │
│             AGENTE         │   │           AGENTE          │   │             AGENTE       │
│             ↓ cola disco   │   │           ↓ cola disco    │   │             ↓ cola disco │
└─────────────┼──────────────┘   └───────────┼───────────────┘   └─────────────┼────────────┘
              │                              │                                 │
              └──────────────┬───────────────┴─────────────────────────────────┘
                             ▼  OTLP sobre la red privada, con token
              ┌──────────────────────────────────────┐
              │  Collector GATEWAY (plano central)    │
              │  redacción · sampling · fan-out       │
              │      ├─► ClickHouse                   │
              │      └─► Langfuse                     │
              └──────────────────────────────────────┘
```

**Las aplicaciones siempre exportan a `http://localhost:4318`.** Nunca conocen la dirección del plano central. Esta única decisión te da tres propiedades que importan mucho en tu caso:

1. **Mover el central de la MacBook al iMac no requiere tocar ni una aplicación.** Solo cambia la configuración de los Collector agente.
2. **Si el central está apagado, suspendido o en otra red, las apps no se enteran ni se ralentizan.** El agente local acepta la telemetría, la escribe a disco y la envía cuando vuelve a haber conexión.
3. **Añadir una máquina nueva es desplegar un agente**, no reconfigurar apps.

### 3.2 La cola persistente no es opcional aquí

Tu plano central vive en un portátil. Se suspende cuando cierras la tapa, cambia de red entre casa y oficina, y a veces estará apagado. Sin cola persistente, cada uno de esos eventos es pérdida de datos — y peor, **huecos en la telemetría que el agente de RCA interpretará como silencio en vez de como ausencia de datos.**

Cada Collector agente lleva la extensión de almacenamiento en fichero, con la cola de envío respaldada en disco. Escribe a un WAL antes de intentar exportar, así que absorbe el corte; y si el agente muere con elementos en cola, al arrancar los recoge y continúa. Se dimensiona la cola para el peor caso realista (un fin de semana con el central apagado) y se vigila su ocupación como métrica.

Límite honesto: si el disco se llena o el central sigue inalcanzable más allá de los reintentos, **se pierde igual**. Por eso la ocupación de cola es una alerta por sí misma.

### 3.3 Direccionamiento estable entre máquinas

Las apps pueden estar en Macs distintas, en otro servidor, y en redes que cambian. Lo que necesitas es un nombre estable que no dependa de la IP ni de estar en la misma LAN.

**Recomendación: una red privada tipo Tailscale (WireGuard).** Da nombres DNS estables entre máquinas, cifrado extremo a extremo, funciona igual en macOS y Linux, atraviesa NAT sin abrir puertos, y sobrevive a cambios de red. El plano central se publica como un nombre fijo; migrarlo de la MacBook al iMac es mover el servicio y conservar el nombre.

Aun dentro de una red privada, el endpoint OTLP lleva **autenticación por token** (extensión de autenticación del Collector). La red privada protege el transporte; el token protege de que cualquier proceso de una máquina autorizada inyecte telemetría falsa. Con agentes de IA que leen esa telemetría y sacan conclusiones, la integridad de lo que entra importa.

### 3.4 Modo ligero: el plano central arranca pequeño

El stack completo (ClickHouse + Langfuse con sus cuatro dependencias + Temporal + Prometheus + HyperDX) es demasiado para arrancar en un portátil que además corre inferencia local. Se escalona:

| Etapa | Contenedores | Cuándo |
|---|---|---|
| **Ligero** | Collector gateway + ClickHouse + HyperDX + VictoriaMetrics | Fase 0. Cuatro contenedores. Ya te da logs, trazas, métricas y consultas SQL |
| **+ GenAI** | + Langfuse (web, worker, Postgres, Redis, MinIO) | Fase 1, cuando la trazabilidad de prompts aporta |
| **+ Agentes** | + Temporal y su Postgres | Fase 3, cuando llegan los agentes |

**VictoriaMetrics en lugar de Prometheus** para la parte de métricas: un solo binario, mucho menos memoria, compatible con PromQL y con reglas de alerta. En un portátil la diferencia se nota.

Todas las imágenes necesarias tienen build arm64, así que Apple Silicon no es un problema — pero conviene **fijar versiones y verificar la arquitectura** en el primer despliegue en vez de asumirlo.

### 3.5 Portabilidad: migrar de MacBook a iMac

Requisito de diseño desde el día uno, no una preocupación futura:

- **Todo el estado en volúmenes nombrados**, declarados en un solo `compose.yaml`. Nada en rutas absolutas del host.
- **Toda la configuración en el repositorio**, versionada. El plano central se reconstruye con `git clone` + `compose up`.
- **Secretos fuera del repo**, en un `.env` con su plantilla `.env.example` versionada.
- **Procedimiento de migración probado**, no improvisado: parar, exportar volúmenes, restaurar en destino, arrancar, mover el nombre DNS. Es un runbook, y se ensaya una vez antes de necesitarlo.
- Retención consciente de que el disco de un portátil es finito: TTL agresivo en caliente, y volcado a almacenamiento de objetos para lo que deba conservarse seis meses por cumplimiento.

---

## 4. Arquitectura funcional

```
┌──────────────────────────────────────────────────────────────────────────┐
│  PLANO 1 — EMISIÓN (en cada app, por sub-componente)                     │
│   Nivel 2  argus.step / agent / tool / genai / retrieval                 │
│   Nivel 1  argus.init()   ← una línea                                    │
│   Nivel 0  opentelemetry-instrument (macOS) · OBI/eBPF (solo Linux)      │
│            · sondas sintéticas (cualquier SO)                            │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │ OTLP → localhost
┌────────────────────────────────▼─────────────────────────────────────────┐
│  PLANO 2 — INGESTA                                                       │
│  Collector AGENTE por host: batch + cola persistente en disco            │
│  Collector GATEWAY central: redacción · pseudonimización · tail sampling │
│       ├──► ClickHouse `otel`  — traza COMPLETA, logs, métricas            │
│       └──► Langfuse v3         — subárbol GenAI, mismo trace_id           │
│  + VictoriaMetrics + Registro de aplicaciones (auto-alimentado)          │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │
┌────────────────────────────────▼─────────────────────────────────────────┐
│  PLANO 3 — DETECCIÓN                                                     │
│  SLO burn-rate · anomalía · evals online · deriva ML · coste             │
│         →  Alert Bus (normaliza, dedup, agrupa, correlaciona)            │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │ incidente enriquecido
┌────────────────────────────────▼─────────────────────────────────────────┐
│  PLANO 4 — FLOTA DE AGENTES (§8)                                         │
│  Núcleo:     triage → memoria → investigación → notificación → remediar  │
│  Continuos:  calidad · deriva · coste · seguridad                        │
│  Periódicos: postmortem · poda de alertas · riesgo de cambio · runbooks  │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │
┌────────────────────────────────▼─────────────────────────────────────────┐
│  PLANO 5 — ENTREGA                                                       │
│  Google Chat · Email · WhatsApp   ·   Portal de explicabilidad           │
└──────────────────────────────────────────────────────────────────────────┘
```

### Decisiones de stack

| Componente | Elección | Por qué |
|---|---|---|
| Emisión | **`argus-semconv`** (solo API OTel) + **`argus-sdk`** (apps) | Contrato único para un portafolio que crece; resuelve el caso Axonium (§5.1) |
| Convenciones poliglotas | YAML único → constantes generadas por lenguaje | Imposibilita que Python y Go emitan el mismo atributo con nombres distintos (§5.9) |
| Cero-código, Go | **Compile-time instrumentation v1** | Un comando en el build, sin agente, sin eBPF, **funciona en macOS** |
| Cero-código, Python/Java | `opentelemetry-instrument` / agente JAR | Las auto-instrumentaciones más maduras |
| Cero-código, Linux | OTel eBPF Instrumentation (OBI) | Solo donde haya Linux; se auto-desactiva donde ya hay telemetría |
| Transporte | **OTLP/gRPC** entre procesos; HTTP solo donde se exige | Menor latencia para el camino caliente; Langfuse no soporta gRPC |
| Recolección | Collector **agente** por host + **gateway** central | Portabilidad y cola persistente (§3) |
| **Detección en tiempo real** | Camino caliente: `filter` → OTLP → `alert-bus` | Detecta **sobre el flujo**, sin base de datos ni tail sampling; ~2 s (§7.0) |
| Métricas derivadas | connectors `spanmetrics` + `signaltometrics` | Métricas RED en vuelo, sin pasar por el almacén |
| Agregación continua | Vistas materializadas de ClickHouse | Agregan en la inserción; burn-rate precomputado, no calculado al consultar |
| Almacén infra | ClickHouse (ClickStack / HyperDX) | SQL arbitrario sobre wide events para el agente de RCA |
| Almacén GenAI | Langfuse v3, misma ClickHouse, base separada | Prompts, coste, evals, versionado; ingesta OTLP nativa |
| Métricas y reglas | VictoriaMetrics | Un binario, mucha menos memoria que Prometheus, PromQL y reglas — importa en un portátil |
| Correlación | `alert-bus` propio (receptor OTLP) | Depende del registro propio; [Keep](https://www.keephq.dev/) a evaluar en Fase 2 |
| Memoria de incidentes | pgvector o Qdrant + embeddings | Agente de memoria (§8.1) |
| Deriva ML | Evidently + NannyML | NannyML estima rendimiento sin ground truth |
| Runtime de agentes | Temporal | Durabilidad real y espera de aprobación humana; sobrevive a que el portátil se suspenda (§8.6) |
| Red entre máquinas | Red privada tipo Tailscale + token en el endpoint | Nombres estables, cifrado, atraviesa NAT, sobrevive a cambios de red |

> **Descartado**: Grafana OnCall — OSS archivado el 24 de marzo de 2026; las notificaciones SMS/push por Cloud Connection dejaron de funcionar ese día.

### Principio rector

**El coste de integración por aplicación es el requisito nº 1.** Con un portafolio abierto, apps independientes y sin embudo común, cualquier diseño que exija más de unos minutos por repo no se adopta — y una plataforma que nadie adopta no existe. Todo lo demás se subordina a eso.

---

## 5. Plano 1 — `argus`, la librería

Todo desde cero, sin heredar decisiones de los paquetes de telemetría existentes.

### 5.1 Dos paquetes, no uno — y esto es lo que resuelve el caso Axonium

La decisión de arquitectura más importante de toda la librería:

| Paquete | Quién lo importa | De qué depende | Qué hace si no hay SDK |
|---|---|---|---|
| **`argus-semconv`** | **Librerías**: Axonium, synaptum, cualquier SDK tuyo | **Solo `opentelemetry-api`** | Nada. No-op con coste cero |
| **`argus-sdk`** | **Aplicaciones**: servicios, workers, CLIs | SDK, exportadores, auto-instrumentaciones | Es quien enciende todo |

**La regla es dura y no admite excepciones: ninguna librería tuya importa `argus-sdk`.** Una librería que depende del SDK impone en silencio su versión del SDK y sus opiniones sobre exportadores a todo el que dependa de ella — y con quince o más apps importando Axonium, eso es exactamente el infierno de dependencias que quieres evitar.

`argus-semconv` es minúsculo a propósito: constantes de atributos, los helpers `genai()` / `retrieval()` / `tool()`, y nada más. Depende solo de la API de OTel, que es un conjunto de abstracciones con implementaciones no operativas.

### 5.2 La respuesta concreta a "¿Axonium importa el nuevo SDK?"

**No. Axonium importa `argus-semconv`, que a su vez solo depende de `opentelemetry-api`.**

Qué pasa entonces, según quién use Axonium:

| Situación | Comportamiento |
|---|---|
| App **con** `argus.init()` | Los spans de Axonium aparecen automáticamente en la plataforma, **usando la configuración, el endpoint y el muestreo de esa app**. Cero configuración adicional en Axonium |
| App **sin** `argus.init()` | Los spans son no-ops. **Coste cero.** Axonium funciona exactamente igual que hoy |
| App de un tercero que use Axonium | Igual: no se le impone ni un exportador ni una versión de SDK |
| Tests de Axonium | Corren sin backend, sin configuración, sin mocks |

**Por qué esto es tan valioso en tu caso concreto.** Axonium es el cliente de inferencia local de Prometheus, así que **sabe cosas que ninguna aplicación sabe**: el modelo realmente servido, el backend que respondió, los tokens de entrada y salida, el tiempo hasta el primer token, si hubo fallback entre backends, si saltó el circuit breaker. Instrumentarlo una vez significa que **toda app que lo use obtiene trazas GenAI completas sin escribir una línea de instrumentación**.

Y ojo con la distinción importante frente al plan anterior: **esto no es un embudo de servicio.** No obliga a ninguna app a enrutar su tráfico por ningún sitio. Es instrumentación **a nivel de librería**: las apps que ya usan Axonium ganan telemetría por el hecho de usarla, y las que no, no pierden nada. Es apalancamiento sin acoplamiento.

Lo mismo aplica a `synaptum` (spans `invoke_agent` y `execute_tool` en las fronteras de turno, planificación y delegación) y a cualquier SDK futuro tuyo.

**Qué instrumenta cada capa, para no duplicar:**

| Capa | Emite |
|---|---|
| **Axonium** | `chat`, `embeddings` — modelo, proveedor, tokens, duración, backend, TTFT, finish reason, errores |
| **synaptum** | `invoke_agent`, `execute_tool` — nombre de agente, herramienta, id de llamada |
| **La aplicación** | `service.namespace`, `service.name`, spans de negocio, `gen_ai.conversation.id`, `argus.feature`, `argus.use_case` |

Las tres se componen en un solo árbol porque comparten contexto. La app no repite lo que Axonium ya sabe; Axonium no inventa lo que solo la app sabe.

**Regla de versionado**: `argus-semconv` declara `opentelemetry-api >= X, < X+1` y **nunca** fija una versión exacta. Es la única forma de que quince apps con resoluciones de dependencias distintas puedan convivir.

### 5.3 Los tres niveles de cobertura — con la corrección de macOS

**Nivel 0 — sin tocar código.** Para apps que nunca instrumentarás (laboratorios, experimentos, código de terceros) y como red de seguridad durante la migración.

**Corrección importante frente al plan anterior: eBPF/OBI no funciona en macOS.** Es tecnología del kernel Linux, y solo cubre cargas de trabajo Linux soportadas. Eso significa que en tu Mac de desarrollo y en el iMac **no hay red de seguridad basada en eBPF**. Lo que sí funciona en macOS:

| Técnica | Cubre | Dónde |
|---|---|---|
| **Go compile-time instrumentation** | `net/http`, `database/sql`, gRPC, Redis, runtime de Go — **sin tocar código**, solo el build | **macOS y Linux** |
| `opentelemetry-instrument` envolviendo el arranque | Apps Python que puedes lanzar pero no modificar | macOS y Linux |
| Agente JAR de Java | Todo el ecosistema Java, la más madura de todas | macOS y Linux |
| **Sondas sintéticas** desde el Collector o el canario | Disponibilidad y latencia de cualquier app con endpoint HTTP | Cualquier SO |
| `hostmetrics` + `filelog` del Collector agente | CPU, memoria, disco, red del host; logs de ficheros y contenedores | macOS y Linux |
| **OBI / eBPF** | Métricas RED, spans HTTP/gRPC, grafo de red, sin tocar nada | **Solo Linux** |

El nivel 0 es **mejor de lo que parecía** una vez descartado eBPF: Go, Java y Python tienen cero-código que funciona en macOS. El hueco real es Python cuando no controlas el arranque, y Rust.

Consecuencia práctica y honesta: **en macOS, para Python, la instrumentación de nivel 1 es el camino.** Para Go ya no hace falta. El canario sintético cubre disponibilidad de todo lo demás.

**Nivel 1 — una línea.** El objetivo real:

```python
import argus
argus.init()          # lee ARGUS_* y OTEL_* del entorno
```

Configura resource, trazas, métricas, logs, propagadores y **todas las auto-instrumentaciones disponibles**, detectadas por introspección de los paquetes instalados: FastAPI/Starlette, httpx, requests, SQLAlchemy, asyncpg, psycopg, redis, celery, aiokafka, boto3, y las de GenAI.

Para servicios ASGI, una línea más: `app.add_middleware(argus.ASGIMiddleware)`.

**Nivel 2 — semántica propia.** Opcional, solo donde aporta:

```python
@argus.step("ocr.extract")            # unidad de trabajo de negocio
@argus.agent("rca-investigator")      # → span invoke_agent
@argus.tool("clickhouse_query")       # → span execute_tool
with argus.genai("chat", model="qwen2.5-7b", provider="openai") as g:
    g.usage(input_tokens=1200, output_tokens=340)
with argus.retrieval("pgvector", top_k=8) as r:
    r.documents(docs_with_scores)
```

Todo esto también existe en `argus-semconv`, así que **funciona igual dentro de tus librerías**, como no-op si la app no inicializó nada.

### 5.4 Contrato de diseño

Cinco reglas verificadas con tests, no con buenas intenciones:

1. **Nunca tumba la app.** Inicialización dentro de `try/except` que degrada a no-op y avisa una vez. Colas acotadas con descarte al llenarse: si el Collector cae, la app pierde telemetría, **nunca latencia ni memoria**.
2. **Idempotente.** `init()` dos veces es no-op la segunda, con aviso, sin excepción, sin spans duplicados.
3. **No-op si no está configurada.** Sin endpoint, los decoradores funcionan con coste cero.
4. **Cero configuración en el caso normal.** `argus.init()` sin argumentos lee todo del entorno. Los argumentos son para sobrescribir, no para usarse.
5. **Superficie pública mínima y fijada por test.** Si la API cabe en una pantalla, se adopta.

### 5.5 Identidad de dos niveles

| Atributo | Significado | Ejemplo |
|---|---|---|
| `service.namespace` | **la aplicación** | `intelligent-document-platform` |
| `service.name` | **el sub-componente** | `idp-api`, `idp-worker`, `idp-ocr` |
| `argus.component.role` | forma del componente | `api` \| `worker` \| `scheduler` \| `cli` \| `model-server` \| `frontend` |
| `service.version` | versión | `0.4.2` |
| `service.instance.id` | instancia concreta | `idp-worker-3` |
| `deployment.environment.name` | entorno | `mac-dev` \| `imac` \| `server-1` \| `ci` |
| `host.name`, `os.type`, `process.pid` | detectores del SDK | — |
| `langfuse.environment`, `langfuse.release` | espejo, porque Langfuse los lee literalmente | — |

"¿Cómo está la IDP?" es `WHERE service.namespace = '...'`; "¿qué componente falla?" es un `GROUP BY service.name`. Sin los dos niveles tendrías decenas de servicios planos sin forma de agruparlos por aplicación — y el problema empeora con cada app nueva.

**Siempre `Resource.create()`**, nunca el constructor directo: es la diferencia entre que `OTEL_RESOURCE_ATTRIBUTES` y los detectores funcionen o se ignoren en silencio.

### 5.6 Propagación de contexto: el trabajo de verdad

| Frontera | Dónde aparece | Qué da el SDK |
|---|---|---|
| HTTP entrante | todas las FastAPI | `ASGIMiddleware` extrae `traceparent` + `baggage` |
| HTTP saliente | httpx, requests | auto-instrumentación, inyecta cabeceras |
| gRPC | `AIBank` | interceptores cliente y servidor |
| **Celery** | `video-translator` | señales `before_task_publish` / `task_prerun` |
| **Kafka / Redpanda** | `AIBank` | `inject_headers()` / `extract_headers()` |
| **Colas Redis propias** | varios workers | helpers explícitos, contexto en el payload |
| CLI / batch | benchmarks, scripts | `argus.run()` abre traza raíz y hace `force_flush()` al salir |
| Frontera de agente | LangGraph, CrewAI, synaptum | `gen_ai.conversation.id` en baggage |

**Propagador compuesto** (`tracecontext` + `baggage`) idéntico en todos los servicios y lenguajes. **Baggage** lleva `argus.app`, `argus.run.id` y `argus.tenant` — pequeño, acotado y **sin PII**, porque viaja en cabeceras entre procesos.

**Nota de migración**: los repos con middleware de trazas propio pueden estar **anulando la propagación entrante** si arrancan siempre un span raíz. En `edge-ai-inference` eso es explícito y deliberado, por seguridad (OWASP A03), y está documentado en el spec del gateway. Al migrar se retira ese middleware y se usa el modo de confianza del SDK: `ARGUS_PROPAGATE=trusted` adopta `traceparent` solo desde redes internas, `never` para lo expuesto a internet, `always` para redes privadas. La garantía de seguridad se conserva **y** la traza cruza servicios.

### 5.7 Logs, wide events y GenAI

**Wide events**: una línea estructurada y ancha por unidad de trabajo, al final, con todo el contexto. Las líneas de debug siguen existiendo pero se muestrean.

```python
with argus.step("document.process") as s:
    s.set(pages=42, ocr_engine="paddle", model="qwen2.5-7b", cache_hit=False)
    ...
    s.set(outcome="ok", extracted_fields=17)
# emite UN log JSON ancho + cierra el span con los mismos campos
```

El mismo objeto alimenta span y log, que es lo que hace que **los wide events sean tus spans enriquecidos** en vez de un canal paralelo. Todos los logs llevan `trace_id` y `span_id`: sin eso no se salta de un log a su traza, la operación más frecuente en un incidente.

**GenAI — la contradicción atributos vs eventos, resuelta.** La guía canónica dice eventos de span. Pero **Langfuse lee `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.tool.call.arguments` y `gen_ai.tool.call.result` desde los ATRIBUTOS**. Si sigues la guía al pie de la letra, Langfuse ingiere el span y lo muestra sin input ni output — inútil.

1. **Emitir en atributos**, JSON con formato semconv 2026.
2. **Gatear por entorno** (`ARGUS_CAPTURE_CONTENT`, default `false`): dev y staging sí, producción selectivo por app.
3. **Truncar en origen** (`ARGUS_CONTENT_MAX_BYTES=8192`) con marca `gen_ai.content.truncated`.
4. **Enmascarar en origen** con un `mask` por defecto (email, tarjeta, teléfono, DNI, IBAN, tokens).
5. **Borrar en la rama ClickHouse**: en Langfuse el contenido es el producto, en ClickHouse son decenas de KB por span sin consulta que los use.

Dos detalles más de Langfuse: su tabla de mapeo de operaciones es **cerrada** y **no incluye `retrieval`** — para RAG hay que emitir además `langfuse.observation.type = "retriever"`; y lee `langfuse.environment` y `langfuse.release` literalmente, por eso van en el Resource.

**Auto-instrumentación GenAI**: `argus.init()` activa las que existan. **OpenLIT** cubre 25+ proveedores (Ollama, vLLM, OpenAI, Anthropic, Bedrock) y 20+ frameworks (LangChain, LangGraph, CrewAI, LlamaIndex, Pydantic AI). Más `opentelemetry-instrumentation-genai-langchain`. La instrumentación manual queda para tus SDKs propios (§5.2) y la semántica de negocio.

**Etiquetado para atribución de coste**: cada span GenAI lleva `argus.app`, `argus.feature`, `argus.use_case` y el entorno. Sin esto el coste llega como un número opaco a fin de mes.

### 5.8 Empaquetado

```
libs/
├── argus-semconv/src/argus_semconv/     # SOLO opentelemetry-api
│   ├── attributes.py                    # constantes
│   ├── genai.py                         # genai(), retrieval(), tool(), agent()
│   └── steps.py                         # step() — no-op sin SDK
└── argus-sdk/src/argus/                 # aplicaciones
    ├── __init__.py                      # init(), superficie fijada por test
    ├── _resource.py  _tracing.py  _metrics.py  _logging.py
    ├── propagate.py                     # Celery / Kafka / Redis / manual
    ├── asgi.py                          # middleware con modos de confianza
    └── autoinst.py                      # detección y activación
```

- `requires-python = ">=3.11"`.
- `argus-sdk` con núcleo de tres dependencias; el resto en extras: `[asgi]`, `[celery]`, `[kafka]`, `[sql]`, `[genai]`, `[logging]`, `[all]`.
- `init()` devuelve un handle con `shutdown()` y `force_flush()`, imprescindible en CLIs y workers.
- Sampler por defecto `ParentBased(ALWAYS_ON)`: el muestreo real es **tail-based en el Collector**.
- **Distribución**: índice PyPI privado (`devpi` o `pypiserver`) como contenedor del plano central. Un directorio plano de wheels no sobrevive a un portafolio que crece ni funciona bien dentro de contenedores.
- **Hermanos poliglotas** con el mismo contrato de variables de entorno: `argus-go`, `argus-ts`. El contrato es el entorno, no la API.

---

### 5.9 Arquitectura poliglota

Cinco lenguajes en el portafolio. La regla que lo hace manejable: **el contrato es el entorno y las convenciones, nunca una API de Python.** Una app Go y una Python instrumentadas correctamente son indistinguibles desde el backend.

**Mapa real de madurez y de cómo instrumentar cada uno:**

| Lenguaje | Dónde aparece | Cero-código | Madurez | Recomendación |
|---|---|---|---|---|
| **Python** | ~25 repos | `opentelemetry-instrument` CLI, de las más maduras | Trazas y métricas estables; logs por detrás | `argus.init()` + auto-instrumentación |
| **Go** | `aeon-ai`, `AIBank` | **Compile-time v1 estable** — un comando en el build, sin agente, sin eBPF, **funciona en macOS** | Estable | Compile-time + manual para negocio. **Se cosen en una traza sola** |
| **Node / TS** | frontends, Next.js | `auto-instrumentations-node`, de las más maduras | Estable | Auto + `argus-ts` para semántica |
| **Java** | `opencode_basic` | Agente JAR, la más madura de todas | Estable | Solo el agente; no merece más |
| **Rust** | núcleo de `AIBank` | No hay | **Todos los crates pre-1.0**; logs y métricas estabilizaron **antes** que trazas | Manual, expectativas bajas, **versiones fijadas** |

Dos consecuencias que conviene asumir de antemano:

- **Go deja de ser el caso difícil.** En el plan anterior Go exigía instrumentación manual o eBPF (que no funciona en Mac). Con la v1 de compilación, `aeon-ai` y `AIBank` se instrumentan con un cambio en el build y **cero líneas de código**, en macOS igual que en Linux. Es la mejor noticia de esta revisión.
- **Rust es el punto frágil.** El núcleo Rust de `AIBank` tendrá instrumentación manual sobre crates pre-1.0. La mitigación es acotar: instrumentar solo las fronteras (entrada, salida, errores) y dejar el interior sin cubrir, en vez de pelearse con un ecosistema que aún se mueve.

**Una sola fuente de verdad para las convenciones.** Con cinco lenguajes, el riesgo no es que uno no emita, sino que **emitan lo mismo con nombres distintos** — y eso rompe cualquier consulta agregada. La solución es la que usa el propio OpenTelemetry para sus semconv: **definir los atributos en un fichero YAML y generar las constantes de cada lenguaje desde ahí**.

```
libs/semconv-model/argus.yaml      ← fuente de verdad, versionada
        ├── genera → argus_semconv/attributes.py
        ├── genera → argus-go/semconv/attributes.go
        ├── genera → argus-ts/src/attributes.ts
        └── genera → argus-rs/src/attributes.rs
```

Un test de CI verifica que lo generado coincide con lo commiteado. Así, añadir un atributo es editar un YAML, y es **imposible** que Python emita `argus.use_case` y Go emita `argus.usecase`.

**Contrato de variables de entorno**, idéntico en los cinco lenguajes: las estándar de OTel (`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES`, `OTEL_SEMCONV_STABILITY_OPT_IN`) más las propias (`ARGUS_PROPAGATE`, `ARGUS_CAPTURE_CONTENT`, `ARGUS_CONTENT_MAX_BYTES`). Un componente nuevo en cualquier lenguaje se configura copiando el mismo bloque.

**Propagador compuesto** (`tracecontext` + `baggage`) configurado igual en los cinco. Es la condición para que la traza cruce de Python a Go a Rust sin partirse.

**Transporte: gRPC entre procesos, HTTP donde toque.** OTLP/gRPC de la app al agente local y del agente al gateway, por menor sobrecoste y latencia — importa para el camino caliente (§7.0). HTTP/protobuf solo donde el destino lo exige: **Langfuse no soporta gRPC**.

**Qué se construye por lenguaje, y qué no.** No hay que portar la librería entera cinco veces:

| | Python | Go | TS | Java | Rust |
|---|---|---|---|---|---|
| `argus-semconv` (constantes + helpers) | ✅ | ✅ | ✅ | ⬜ generadas, sin helpers | ✅ mínimo |
| `argus-sdk` (`init()` completo) | ✅ | ✅ | ✅ | ⬜ basta el agente | ⬜ manual |
| Decoradores de nivel 2 | ✅ | ✅ | ✅ | ⬜ | ⬜ |

Python y Go primero, porque son el 90 % del portafolio. TS cuando haya frontend que instrumentar. Java y Rust se quedan en lo mínimo a propósito: un repo cada uno no justifica mantener una librería.

---

## 6. Migrar apps existentes y añadir apps nuevas

Las dos operaciones que más veces se van a repetir. Si son caras, la plataforma fracasa.

### 6.1 Migración con overhead mínimo

Cuatro casos, de más barato a más caro:

**a) La app ya tiene OTel** (ocho de tus repos). **Coste: cero líneas de código.** Solo variables de entorno:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   # el agente local
OTEL_SERVICE_NAME=idp-api
OTEL_RESOURCE_ATTRIBUTES=service.namespace=intelligent-document-platform,argus.component.role=api
```
Se retira el Tempo/Jaeger local y listo. Más adelante se cambia a `argus.init()` para ganar métricas, logs correlacionados y GenAI, pero **no es un requisito para empezar a ver datos**.

**b) La app tiene logging propio** (structlog, colorlog, `logging` stdlib). **Coste: una línea.** `argus.init()` instala un puente que capta los logs de la librería estándar, los correlaciona con la traza activa y los exporta. **Tus `logger.info()` actuales siguen funcionando exactamente igual** y ganan `trace_id` sin reescribir ninguna llamada. Los wide events son una mejora posterior y opcional.

**c) La app no tiene nada.** **Coste: dos líneas** (`argus.init()` + middleware) y cuatro variables de entorno.

**d) La app tiene middleware de trazas propio.** El único caso con trabajo real: hay que retirarlo, porque si arranca siempre un span raíz **anula la propagación entrante** y parte las trazas. Se sustituye por `ASGIMiddleware` con el modo de confianza adecuado.

**Coexistencia durante la transición.** Si al llamar `init()` ya existe un proveedor de OTel configurado, el SDK **no lo pisa**: avisa y se limita a añadir el exportador hacia el agente local. Así una app puede seguir enviando a su backend viejo mientras empieza a enviar al nuevo, y la migración se corta cuando tú decidas, no cuando el SDK lo imponga.

### 6.2 Añadir aplicaciones nuevas es el caso normal, no la excepción

El portafolio crece. El diseño lo asume:

- **Auto-descubrimiento.** Un servicio que empieza a emitir telemetría con un `service.namespace` desconocido aparece **automáticamente** en el registro como *provisional*, con SLOs por defecto según su `argus.component.role`. Empieza a ser visible desde el primer span.
- **Pero no en silencio.** *"Servicio no registrado emitiendo telemetría"* es una alerta de baja severidad. Descubrir sin avisar es cómo un registro se llena de basura; avisar sin descubrir es cómo se queda vacío.
- **Plantilla de alta.** `argus scaffold <app>` genera la entrada del registro, el bloque de variables de entorno para el compose, y un runbook vacío. Un comando, no un formulario.
- **SLOs por defecto según el rol**, para que una app nueva tenga alertamiento razonable desde el minuto uno sin configurar nada. Se afinan después.
- **El registro es datos, no código**, y se recarga en caliente. Añadir una app no reinicia nada.
- **Máquina nueva = desplegar un agente.** Un solo `compose.yaml` de agente, parametrizado por host, idéntico en macOS y Linux.

Esto además revaloriza el **agente de onboarding** (§8.4, nº 17), que en el plan anterior estaba marcado como dudoso: con un catálogo cerrado de quince no compensaba; con uno abierto que crece, sí.

---

## 7. Planos 2 y 3 — Ingesta en tiempo real, detección y alertamiento

### 7.0 Tres caminos con latencias distintas

Este es el cambio estructural que impone el requisito de tiempo real.

El problema de una tubería de observabilidad convencional es que **acumula latencia en cada salto**, y la suma es de minutos: el procesador de spans del SDK espera ~5 s, el Collector agente agrupa otros ~5 s, el tail sampling del gateway espera hasta **30 s** a que la traza cierre, el gateway agrupa otros ~5 s, la inserción en ClickHouse se bufferiza, Prometheus scrapea cada 15 s y evalúa reglas cada 15–60 s. **Detectar una caída puede tardar un minuto y medio.**

Y el conflicto es real: **el tail sampling necesita esperar a que la traza termine para decidir si la guarda.** Esa espera es correcta para almacenar, y es inaceptable para detectar.

La solución es no meter todo por la misma tubería. **Tres caminos, con presupuesto de latencia explícito:**

```
                          ┌──── CAMINO CALIENTE  (<2 s) ────────────────────┐
                          │  filter: errores · SLO roto · guardarraíl       │
   app ──OTLP/gRPC──►  Collector      sin tail sampling · batch 200 ms      │
                       AGENTE         ──OTLP──►  alert-bus (receptor OTLP)  │
                          │                            │                     │
                          │                            └──► notificación ────┘
                          │
                          ├──── CAMINO TEMPLADO  (2–15 s) ──────────────────┐
                          │  spanmetrics + signaltometrics connectors       │
                          │  ──► VictoriaMetrics (eval 10 s)                │
                          │  ──► ClickHouse + vistas materializadas         │
                          │      (agregación continua en la inserción)      │
                          │            → burn-rate, anomalía, tasas         │
                          │
                          └──── CAMINO FRÍO  (30–60 s) ─────────────────────┐
                             gateway: redacción · tail sampling · batch     │
                             ──► ClickHouse (traza completa) + Langfuse     │
                                  → investigación forense y agentes          │
```

**Camino caliente — detección en el flujo, no en la base de datos.** Un `filter` en el Collector agente deja pasar solo lo que puede ser un incidente: spans con `status=ERROR`, spans que superan el umbral de SLO de su componente, breches de guardarraíl de coste, y logs de severidad `ERROR`/`FATAL`. Eso va **directo al `alert-bus`, que es un receptor OTLP**, sin tocar el almacén y sin pasar por el tail sampling. Volumen bajo por definición (los errores son la excepción), así que el batch se baja a 200 ms sin coste.

La clave: **la detección no consulta la base de datos, ocurre sobre el flujo.** Es lo que separa segundos de minutos.

**Camino templado — métricas derivadas en vuelo.** Los connectors `spanmetrics` y `signaltometrics` convierten spans en métricas RED **dentro del Collector**, sin pasar por la base de datos, y las empujan a VictoriaMetrics con temporalidad delta (push, no scrape — ahorra el intervalo de scrape entero). En paralelo, las **vistas materializadas de ClickHouse agregan en el momento de la inserción**, así que burn-rate, tasas de error por componente y ventanas de comparación están precomputadas y se leen en milisegundos en vez de calcularse al consultar.

**Camino frío — lo que hoy ya es correcto.** Traza completa, redactada, tail-sampled y agrupada, hacia ClickHouse y Langfuse. Sirve a la investigación agéntica y al análisis forense, donde 30 s de retraso no importan nada.

### 7.0b Presupuesto de latencia, de punta a punta

| Tramo | Presupuesto | Cómo se consigue |
|---|---|---|
| App → Collector agente | < 100 ms | OTLP/**gRPC** sobre `localhost`; `BatchSpanProcessor` con `schedule_delay=200ms` para el camino caliente |
| Agente → gateway / alert-bus | < 200 ms | gRPC, batch 200 ms, sin tail sampling en esta rama |
| Evaluación en el alert-bus | < 100 ms | Detección en memoria sobre el flujo, no consultas |
| Decisión de notificar | **0 s o 60 s** | Ver la regla de abajo |
| Entrega a Google Chat | < 1 s | Webhook, un POST |
| **Total, fallo duro → aviso** | **~2 s** | |
| Investigación agéntica | 30 s – 5 min | No es tiempo real, y no debe serlo |

**La regla que resuelve la tensión entre rapidez y agrupación**: agrupar alertas durante 60 s reduce el ruido pero mata el tiempo real. La salida es **divulgación progresiva**:

1. Severidad `page`: **se notifica de inmediato**, en cuanto el primer evento cruza el umbral, con lo poco que se sabe y una marca explícita de *"investigando"*.
2. Los eventos siguientes de la misma huella **no generan mensajes nuevos**: actualizan el mismo hilo.
3. Cuando el agente de RCA termina, **edita ese mismo mensaje** con la causa raíz, la evidencia y la sección de incidentes similares.

Así el primer aviso llega en segundos y el informe completo llega cuando está listo, **sin duplicar notificaciones**. Severidades menores sí se agrupan con ventana, porque ahí la rapidez no compra nada.

### 7.0c Lo que el tiempo real cuesta, dicho claro

- **Batches pequeños significan más peticiones y más CPU.** Por eso el camino caliente solo lleva errores y violaciones de SLO — un volumen que es una fracción del total. Aplicar batch de 200 ms a todo el tráfico sería caro y no compraría nada.
- **Más falsos positivos.** Detectar en 2 s en vez de en 60 s significa decidir con menos evidencia. Se compensa exigiendo confirmación por ventana corta en las señales ruidosas y reservando la notificación inmediata para condiciones inequívocas (errores duros, componente caído, guardarraíl roto), no para umbrales estadísticos.
- **El camino caliente no ve la traza completa**, solo spans sueltos, porque no espera a que cierre. Es suficiente para decir *"algo está fallando aquí"*, y no para decir por qué. El *por qué* llega por el camino frío, y eso está bien: son dos preguntas distintas con urgencias distintas.
- **Hay un suelo que no se puede bajar**: si el plano central está suspendido, la detección espera a que vuelva. El tiempo real y la topología portátil conviven, pero no son gratis juntos.

### 7.1 El Collector gateway: fan-out, no routing

**No usar el `routing` connector.** Con `context: span` separa spans del mismo trace hacia pipelines distintos — el span `chat qwen` iría a Langfuse y su padre HTTP a ClickHouse, dejando la traza de ClickHouse con un hueco. Reconstruir ese árbol completo es justo lo que el agente de RCA necesita para poder decir *"esta latencia de LLM causó aquel timeout"*.

```
                    ┌─ traces/clickhouse ──[traza COMPLETA]────────► ClickHouse (db otel)
otlp receiver ──────┤
                    └─ traces/langfuse ────[filter: solo gen_ai]───► Langfuse OTLP/HTTP
```

Un receiver en dos pipelines hace fan-out automáticamente. Los `trace_id` son idénticos → deep-link bidireccional entre ambas UIs.

Orden dentro de cada pipeline, no negociable: `memory_limiter` → `resource` → redacción PII → `tail_sampling` → `filter` (solo Langfuse) → limpieza de contenido (solo ClickHouse) → `batch`. `memory_limiter` primero, `batch` último y **después** de tail_sampling.

Detalles que conviene documentar antes de sufrirlos:

- **Imagen `contrib` con versión fijada**, y verificar arm64 en el primer despliegue.
- **Base de datos `otel` separada** de la de Langfuse en la misma ClickHouse, para no colisionar con sus migraciones.
- **Langfuse solo acepta OTLP/HTTP**, nunca gRPC. Auth `Basic`, encoding proto, gzip. Lotes más pequeños que los de ClickHouse: es Next.js, no un ingestor columnar.
- **Tail sampling exige que todos los spans de una traza lleguen al mismo Collector.** Con un gateway único, correcto. **Importante para esta topología**: los Collector agente **no** hacen tail sampling — solo batch y cola. El muestreo vive únicamente en el gateway.
- **`decision_wait` vs operaciones largas**: una generación de varios minutos se decide con lo visto y el span raíz llega después como traza nueva. Subir la ventana o emitir un evento temprano con estado provisional.

### 7.2 Privacidad: tres capas, y dónde no poner cada cosa

1. **En la app**: `mask` por defecto del SDK antes de poner el atributo.
2. **En el gateway, `redaction`**: red de seguridad por regex (email, tarjeta, teléfono, DNI, IBAN, `sk-`/`Bearer`). Con `summary: debug` emite el conteo de redacciones, que es una métrica útil: un pico significa que alguna app empezó a filtrar PII.
3. **En el gateway, pseudonimización**: `user.id` y `client.id` hasheados con sal. El agente agrupa "el mismo usuario" sin saber quién es. Se borran `authorization` y `url.query`.

**Lo que deliberadamente no va en el Collector**: NER con modelos. Demasiado caro para el camino caliente. El Collector hace regex; los modelos van en proceso.

La capa 2 es la que importa: un fallo de instrumentación en una app no se convierte en una fuga en el almacén.

### 7.3 Control de volumen

- **Head sampling** en la app: 100 % de errores, 100 % de GenAI, 10 % del tráfico normal.
- **Tail sampling** en el gateway: se queda toda traza con error, latencia sobre umbral, `error.type`, operación GenAI, o de una investigación de agente. El resto, probabilístico.
- **Métricas primero, sampling después**: derivar métricas de **todas** las trazas con el `spanmetrics` connector y reenviarlas con `forward` a un segundo pipeline donde se aplica el muestreo. Así las métricas no se sesgan.

### 7.4 Los cinco detectores

**a) Salud — SLO burn-rate multi-ventana.** Nada de umbrales estáticos. Ventana corta (5 min) **y** larga (1 h) sobre el umbral a la vez para paginar. `page` con 14.4× en 5m y 1h; `ticket` con 3× en 6h y 3d.

**b) Anomalía estadística — fase posterior.** Bandas con recording rules. **No activar antes de 8 semanas de histórico**: con menos, la estacionalidad no es fiable y solo genera ruido.

**c) Calidad de IA — evals online.** Muestreo (5 % + el 100 % de fallos y del P99) puntuado asíncronamente:

| Eval | Detecta | Cómo |
|---|---|---|
| Groundedness | Respuesta no soportada por el contexto recuperado | LLM-judge: extrae afirmaciones, verifica cada una |
| Consistencia | Contradice turnos previos | LLM-judge sobre `gen_ai.conversation.id` |
| Formato | Salida que rompe el contrato | Pydantic, no LLM — barato y determinista |
| Rechazo | Pico de negativas | Heurística sobre `finish_reasons` |

**Regla de oro**: alertar solo sobre la **condición conjunta** deriva + caída de eval.

**d) Comportamiento de agentes y coste.** Detectores baratos sobre spans: `tool_calls_per_run > P99` (sospecha de bucle); misma herramienta con mismos argumentos ≥3 veces (bucle confirmado); `coste_por_run > 3×` la mediana del agente (fuga de coste); tasa de error de `execute_tool` por herramienta con línea base propia.

**e) Deriva ML.** Evidently para distribución; **NannyML para estimar rendimiento sin ground truth** (en scoring la verdad llega semanas después); y **deriva de atribución** — cómo cambia la distribución de importancias SHAP, que suele anticipar la degradación antes de que caiga la métrica.

### 7.5 Alert Bus

Servicio FastAPI que recibe de los cinco detectores y aplica, en orden: **normalización** a esquema único → **deduplicación** por huella (`app + componente + tipo + firma`) → **agrupación temporal** (60 s) contra tormentas → **correlación por topología** (suprimir síntomas cuando hay causa aguas arriba) → **enriquecimiento** (despliegues, commits, config, dependencias) → **disparo** de la flota.

*Sin topología fiable la correlación no funciona*, por eso el registro es prerequisito (§9.2).

---

## 8. Plano 4 — La flota de agentes

Investigación y notificación eran dos ejemplos. Catálogo completo, evaluado y priorizado.

### 8.1 Los cinco del núcleo — el ciclo del incidente

| # | Agente | Entrada → Salida | Permisos |
|---|---|---|---|
| 1 | **Triage** | Alerta cruda → severidad, app/componente, ¿merece investigación? | Solo lectura del registro |
| 2 | **Memoria** | Firma → top-N incidentes pasados similares con causa y solución | Solo lectura del histórico |
| 3 | **Investigación (RCA)** | Incidente enriquecido → causa raíz + evidencia + confianza | **Solo lectura**, siempre |
| 4 | **Notificación** | Informe → mensajes por canal, a quién y cuándo | Escritura solo en canales |
| 5 | **Remediación** | Informe + aprobación humana → acción ejecutada | Lista blanca acotada, reversible |

Dos decisiones de diseño:

- **El triage debe ser barato**: un clasificador con modelo pequeño, no una investigación. Su trabajo es filtrar **antes** de invocar al caro.
- **El notificador no es un LLM suelto.** A quién avisar es una regla del registro, no un juicio. El LLM solo redacta. Esto evita el fallo clásico de *"el agente decidió no avisar a nadie"*.

**El agente de memoria es el hallazgo de esta investigación.** Busca por **similitud semántica vectorial** —no textual— en el histórico: una alerta sobre *"gateway devolviendo 502"* encuentra *"timeout de upstream causando fallos de gateway"* porque el significado es el mismo aunque el vocabulario no. Si hay coincidencia, **corta el diagnóstico en seco**. Es barato (embeddings + pgvector, que ya usas), mejora solo con el tiempo, y casi nadie lo construye. Va **antes** del de investigación: su salida es contexto de entrada del caro.

### 8.2 Los cuatro continuos

| # | Agente | Qué hace | Por qué aporta |
|---|---|---|---|
| 6 | **Calidad / evals** | Muestrea trazas GenAI y las puntúa | Sin esto solo alertas de latencia y errores, **nunca de calidad** |
| 7 | **Deriva** | Distribución de entradas, predicciones y atribuciones; rendimiento sin etiquetas | La degradación silenciosa de ML no produce ningún error |
| 8 | **FinOps / TokenOps** | Atribuye coste por app, funcionalidad, entorno y caso de uso; detecta regresiones; propone escalonado de modelos, caché, compresión de prompt | **Las facturas suben aunque los precios por token bajen**: 10–20 llamadas por tarea de usuario |
| 9 | **Seguridad de IA** | Inyección de prompt, fuga de PII en entradas y salidas, autenticación anómala, patrones de exfiltración | Tu propio `llm_security` existe porque el riesgo es real; aquí se vigila en producción |

### 8.3 Los cuatro periódicos

| # | Agente | Cadencia | Qué hace |
|---|---|---|---|
| 10 | **Postmortem** | Tras cada incidente | Redacta el retrospectivo **desde la cadena de razonamiento del agente de investigación**, no desde el chat |
| 11 | **Poda de alertas** | Semanal | Busca reglas que nunca llevaron a acción, alertas siempre silenciadas, umbrales mal puestos. **Ataca el problema dominante de 2026** |
| 12 | **Riesgo de cambio** | En cada despliegue | Correlaciona despliegue con telemetría posterior e histórico de incidentes; marca despliegues de riesgo |
| 13 | **Runbooks** | Tras incidentes resueltos | Convierte el incidente en runbook versionado que alimenta al de investigación la próxima vez |

El **10 y el 13 cierran el bucle de aprendizaje**: cada incidente deja el sistema mejor preparado. El **11** evita que la plataforma muera de su propio ruido.

### 8.4 Los cuatro de infraestructura

| # | Agente | Qué hace |
|---|---|---|
| 14 | **Canario sintético** | Ejerce cada app cada 5 min. Convierte **"la app dejó de reportar" en alerta** en vez de en silencio. En macOS es además parte del nivel 0 (§5.3) |
| 15 | **Capacidad** | Saturación, profundidad de cola, *thrashing* de carga de modelos, competencia entre inferencia local y plataforma — relevante porque comparten la misma Mac |
| 16 | **Cumplimiento** | Informe de monitoreo post-mercado por aplicación de IA (Art. 72) |
| 17 | **Onboarding** | Ante una app sin instrumentar, abre un PR con `argus.init()`, el middleware y la entrada del registro |

### 8.5 Qué construir y en qué orden

No construyas diecisiete.

**Construir (ocho)**: el núcleo completo (1–5) porque sin él no hay sistema; **memoria** (2) por retorno desproporcionado; **canario** (14) porque el silencio no puede parecer salud —y en macOS es doblemente necesario, al no haber eBPF—; y **poda de alertas** (11) porque sin él la plataforma se ahoga en su propio ruido hacia el mes tres.

**Después, por este orden**: calidad (6) → FinOps (8) → postmortem (10) → deriva (7) → riesgo de cambio (12).

**Solo si duele**: seguridad (9) si expones apps fuera; capacidad (15) cuando la plataforma compita con la inferencia local en la misma máquina; cumplimiento (16) si alguna app entra en el Anexo III; runbooks (13) cuando haya suficientes incidentes resueltos que destilar.

**Reevaluado al alza**: **onboarding (17)**. En el plan anterior lo marqué como dudoso porque con quince apps fijas compensa hacerlo a mano. **Con un catálogo abierto que crece, cambia**: un agente que instrumenta cada app nueva se amortiza a partir de las veinte o cuando el ritmo de altas es regular. Construirlo después del núcleo, no antes.

### 8.6 Runtime: Temporal

Cuatro candidatos: `synaptum-framework` propio, Temporal (ya desplegado en `aeon-ai`), Claude Agent SDK directo, LangGraph.

**Recomendación: Temporal.** *El requisito duro es la durabilidad y la espera de aprobación humana, y ese es exactamente el problema que Temporal resuelve y que ninguno de los otros resuelve sin que lo construyas tú.*

| Criterio | synaptum | **Temporal** | Claude SDK | LangGraph |
|---|---|---|---|---|
| Sobrevive a caída del worker | No (checkpoint en proceso) | **Sí, reanuda solo** | No | Supervisor propio |
| Esperar días una aprobación | Construirlo | **`workflow.await` sobre signal** | No | Persistencia propia |
| MCP | Pendiente en su roadmap | Ya existe | Nativo, el mejor | Vía adaptadores |
| Observabilidad propia | Pendiente en su roadmap | Ya emite GenAI semconv | Manual | Manual |
| Política, presupuesto, secretos | Fuera de alcance por diseño | Ya existe en aeon-ai | No | No |

Y en tu topología concreta, Temporal aporta algo extra: **el plano central es un portátil que se suspende.** Un workflow durable sobrevive a que la máquina se duerma en mitad de una investigación; un bucle en memoria, no.

Los matices pesan tanto como la elección:

- **L3 no debe ser un agente.** Es una llamada con salida estructurada sobre contexto pre-recolectado por consultas fijas. Sin bucle, sin herramientas, p95 < 10 s. Una activity suelta, no un workflow.
- **L4 = un workflow donde cada llamada a herramienta es una activity**, no un bucle de agente dentro de una sola activity. Si envuelves el bucle entero en una activity de 8 minutos y el worker se reinicia al minuto 7, Temporal reintenta **la activity completa** y vuelves a pagar todas las inferencias. Con una activity por paso, el reintento cuesta un paso.
- **L5 = el mismo workflow con `workflow.await`.** La aprobación llega como signal desde tarjeta de Google Chat, enlace firmado de email o respuesta de WhatsApp, con timeout que escala o cancela.
- **Claude Agent SDK sí, pero acotado**: para la **exploración interactiva** que haces tú durante un incidente, con los MCP conectados. Es la mejor herramienta para *"mira esto conmigo"* y la peor para *"corre desatendido a las 3 AM"*.
- **Worker Temporal propio**, con su task queue y dependencias mínimas. Comparte el servidor, **no el árbol de dependencias** — `aeon-ai` ya documenta un conflicto irresoluble entre `crewai` y `openai-agents`.

### 8.7 Herramientas: tres servidores MCP, separados por autorización

**`mcp-obs`** (solo lectura, ClickHouse `readonly=1`, timeout 30 s):
`clickhouse_query` · `traces_search` · `trace_get` · `logs_search` · `logs_aggregate` · `metrics_query_range` · `metrics_query_instant` · `compare_windows` · `service_topology` · `deploy_history` · `app_registry_lookup` · `runbook_search`

Las cuatro menos obvias y más valiosas:
- **`compare_windows`** — ventana del incidente contra la misma de hace N días, con delta y z-score. Evita que el agente "descubra" que hay más carga los lunes.
- **`service_topology`** — grafo derivado de relaciones padre-hijo cross-service. Convierte "falla A" en "sospecha de B y C", **se auto-deriva**, y contrastarlo con el registro declarado es en sí un hallazgo.
- **`deploy_history`** — cambios de `service.version`. La correlación despliegue→incidente es la causa correcta la mayoría de las veces.
- **`logs_aggregate`** — top-N por dimensión. Primera llamada de toda investigación: dice **dónde** mirar sin traerse 10.000 líneas al contexto.

**`mcp-memory`**: `incident_search_similar` (vectorial) · `incident_get` · `incident_record` · `runbook_get`.

**`mcp-langfuse`**: `traces_search` · `trace_get` (con `include_io=False` por defecto: los payloads revientan la ventana) · `cost_breakdown` · `score_create`.

**`mcp-remediate`** (cada herramienta con `approval_token` que el workflow solo tiene tras el signal): `notify` · `restart_component` · `rollback_deploy` · `scale_component` · `set_rate_limit` · `create_incident` (sin aprobación: crear el registro no cambia producción).

**Convenciones transversales — la diferencia entre un agente útil y uno que quema tokens:**
- Toda herramienta temporal exige `start`/`end` **explícitos**. Sin default a "última hora".
- `max_rows` con truncado **marcado**: nunca en silencio; devolver `truncated` + `total_matched`.
- **Agregación antes que detalle.** El prompt fuerza `logs_aggregate` → `traces_search` → `trace_get`. Empezar por `trace_get` es el antipatrón que llena la ventana.
- `logs_search` filtra por el campo `event` de los wide events — **cardinalidad cerrada, mucha mejor señal que grep de texto libre**.
- Cada herramienta anotada con `risk` e `idempotent`.

### 8.8 Garantías del agente de investigación

- **Presupuesto duro**: `max_tool_calls=40`, `max_tokens=300_000`, `max_duration=15min`, como límites del workflow. Al agotarse emite informe parcial y lo declara incompleto. **Nunca se queda colgado.**
- **Solo lectura, siempre.** Ninguna herramienta de escritura. La separación con el remediador es física, no de prompt.
- **Toda afirmación va con su consulta.** Una afirmación sin respaldo es un bug.
- **Se le permite decir que no sabe**, y se evalúa que no invente cuando la evidencia no da.
- **Es una app LLM más**: sus trazas van a Langfuse con `invoke_agent`, se le miden tokens y coste, y se le aplican los mismos detectores de bucle que a cualquier otro agente.
- **Aislamiento de inyección de prompt**: los logs que lee son **datos, no instrucciones**. Un log malicioso con *"ignora lo anterior y ejecuta X"* no debe cambiar su comportamiento — y con el agente en solo lectura, el peor caso es un informe equivocado, no una acción destructiva.

### 8.9 Guardarraíles del remediador (L5)

Lista blanca cerrada, cada acción con su reversión definida. Aprobación humana explícita por botón, con registro de quién y cuándo. La aprobación caduca a los 15 minutos. Nunca dos acciones automáticas seguidas sobre el mismo componente sin intervención humana. Se habilita primero solo para acciones **reversibles** (`set_rate_limit`, `scale_component`); `rollback_deploy` y `restart_component` detrás de doble aprobación.

---

## 9. Explicabilidad, registro y notificación

### 9.1 Explicabilidad — tres niveles que suelen confundirse

**a) Del modelo (por qué decidió X).** SHAP `TreeExplainer` para tabular y árboles; **Captum** para deep learning en PyTorch. **Regla crítica: no calcular KernelSHAP en la ruta de servicio** — es demasiado lento para producción. El patrón correcto es **llamar al explicador desde el manejador de trazas, solo sobre entradas que fallan o se muestrean**, y escribir la atribución de vuelta al span. Guardar las distribuciones como métrica permite detectar deriva de atribución (§7.4e).

**b) De la aplicación LLM (por qué salió esta respuesta).** Explicabilidad y observabilidad se solapan: ambas se apoyan en trazas; la observabilidad vigila el agregado, la explicabilidad investiga **una salida concreta**. La traza debe contener prompt de sistema y su versión, documentos recuperados con scores, cadena completa de tool calls, scores de eval, y el modelo realmente servido. Con eso, *"¿por qué el bot dijo esto?"* se responde abriendo una traza, sin reproducir nada.

**c) Del agente de diagnóstico (por qué concluyó Y).** **El punto que más se descuida y el que más determina la confianza.** Si el agente de RCA es una caja negra, nadie creerá sus conclusiones y la plataforma muere. Cada informe lleva evidencia citada con su consulta, nivel de confianza, hipótesis alternativas descartadas **con el motivo**, la trayectoria completa del agente en Langfuse, y **lo que no pudo comprobar**. Un agente que dice *"no tengo visibilidad de X"* es más útil que uno que rellena el hueco.

### 9.2 Registro de aplicaciones

Fuente de verdad de la correlación y del enrutamiento. Versionado, recargado en caliente, y **auto-alimentado** (§6.2).

```yaml
- id: intelligent-document-platform     # = service.namespace
  nombre_visible: IDP
  estado: activo                        # activo | provisional | retirado
  repo: ~/Documents/Projects/intelligent_document_platform
  criticidad: alta                      # decide si pagina o hace ticket
  dueño: emeric
  canales: {page: [gchat, whatsapp], ticket: [gchat]}
  hosts: [mac-dev, server-1]            # dónde corre, para el enrutamiento de agentes
  componentes:                          # = service.name
    - {id: idp-api,    rol: api,          slo: {disponibilidad: 99.0, p95_ms: 8000}}
    - {id: idp-worker, rol: worker,       slo: {backlog_max: 500}}
    - {id: idp-ocr,    rol: model-server, slo: {p95_ms: 30000}}
  depende_de: [postgres-main, minio-main, axonium-inference]
  tipo_ia: [llm-api, ocr, extraccion-estructurada]
  runbook: docs/runbooks/idp.md
```

`depende_de` alimenta la supresión de alertas síntoma. Empezar con dependencias declaradas a mano; luego contrastarlas con el grafo que `service_topology` deriva del tráfico real — **una discrepancia entre topología declarada y observada es en sí un hallazgo útil**.

### 9.3 Notificación

| Severidad | Canal | Contenido |
|---|---|---|
| `info` | Ninguno, solo digest diario | Resumen agregado |
| `ticket` | Google Chat (hilo del espacio) | Tarjeta con resumen + enlace |
| `page` (laboral) | Google Chat + Email | Tarjeta + informe completo |
| `page` (fuera de horario) | + **WhatsApp** | Solo titular y enlace |

- **Google Chat** — webhook entrante, **Cards V2** (el legacy está obsoleto). Secciones, widgets y botones lo hacen el canal natural para la **aprobación humana de L5**. Canal principal: cero fricción.
- **Email (SMTP)** — informe completo con evidencia, y digest diario/semanal.
- **WhatsApp (Cloud API)** — **solo crítico fuera de horario**, con dos advertencias de coste: un aviso iniciado por el negocio requiere **plantilla preaprobada**, y clasificarla como *utility* y no *marketing* evita pagar de más; y desde el **1 de octubre de 2026** las respuestas dentro de la ventana de 24 h dejan de ser gratuitas. No es un canal de volumen.

**Divulgación progresiva, para que el tiempo real no cueste ruido.** Un incidente `page` produce **un solo hilo** que se actualiza, no una cascada de mensajes:

```
t+2s     🔴 [IDP / idp-ocr] Errores en idp-ocr — investigando…
            12 errores en 4s · TimeoutError · SLO 1%
            [Ver en vivo] [Reconocer]

t+9s     ↻  (mismo mensaje, actualizado)
            47 errores · componentes afectados: idp-ocr, idp-api

t+1m40s  ↻  (mismo mensaje, ahora con el informe completo ↓)
```

El primer aviso llega en **segundos**; el informe del agente sustituye ese mismo mensaje cuando está listo. Nunca hay dos notificaciones para el mismo incidente.

**Y el contenido nunca es "CPU alta en servicio X":**

```
🔴 [IDP / idp-ocr] Tasa de error 12% (SLO 1%) — 8 min

CAUSA PROBABLE (confianza: alta)
El despliegue a9f3c21 (hace 14 min) cambió el timeout de
extracción de 30s a 5s. 94% de los errores son TimeoutError
en OCR, todos con documentos >20 páginas.

VISTO ANTES
Incidente #47 (12 mar): mismo patrón tras bajar un timeout.
Se resolvió revirtiendo. MTTR 22 min.

EVIDENCIA
· 1.247 spans `ocr.extract` con status=ERROR desde 14:32
· Ninguno antes del despliegue (baseline 0.1%)
· p95 de ocr.extract en documentos largos: 18s > timeout 5s

DESCARTADO
· No hay deriva de entrada (distribución de tamaños normal)
· MinIO y Postgres sanos, sin cambio de latencia

ACCIÓN SUGERIDA
Revertir a9f3c21 o subir el timeout a 30s.

[Ver traza] [Ver informe] [Reconocer] [Silenciar 1h]
```

**VISTO ANTES** es la salida del agente de memoria. Es lo que convierte el informe en algo que se lee en diez segundos.

---

## 10. Estructura y hoja de ruta

### 10.1 Estructura del repositorio

```
app_monitoring_explainability/
├── pyproject.toml                 # uv workspace
├── libs/
│   ├── argus-semconv/             # SOLO opentelemetry-api — lo importan Axonium, synaptum
│   ├── argus-sdk/                 # aplicaciones
│   └── argus-schemas/             # incidente, alerta, informe RCA (pydantic)
├── platform/
│   ├── compose.yaml               # perfiles: lean | genai | agents
│   ├── compose.agent.yaml         # Collector agente, para desplegar en cada host
│   ├── collector/{gateway,agent}.yaml
│   ├── clickhouse/                # DDL, TTL, tabla de wide events
│   ├── rules/                     # SLO burn-rate, bandas de anomalía
│   └── registry/apps.yaml         # registro (§9.2)
├── services/
│   ├── alert-bus/  agents/  notifier/  evaluator/  drift-monitor/  canary/
├── mcp/                           # mcp-obs, mcp-memory, mcp-langfuse, mcp-remediate
├── cli/                           # argus scaffold, argus migrate-check
└── docs/runbooks/
```

### 10.2 Fases

**Fase 0 — Plano central en modo ligero.** Collector gateway + ClickHouse + HyperDX + VictoriaMetrics + índice PyPI privado en la MacBook. Red privada con nombre estable y token en el endpoint OTLP. Registro con las apps y sus componentes. **Runbook de migración a otra máquina, ensayado una vez.**
→ *Un endpoint OTLP al que cualquier app puede apuntar, desde cualquier máquina.*

**Fase 1 — Las librerías y las tres primeras apps.** El YAML de convenciones y su generador de constantes **primero**, porque todo lo demás se deriva de él. Luego `argus-semconv` y `argus-sdk` en Python y Go: identidad de dos niveles, tres señales, propagación en todas las fronteras, auto-instrumentación, decoradores, wide events, GenAI. Collector agente en cada host con cola persistente. Adoptar en **tres apps de formas distintas** — una API pura, una con worker asíncrono, y una Go con instrumentación en compilación — porque eso es lo que valida el diseño de verdad.
→ *Telemetría unificada de tres apps, en al menos dos máquinas.*

**Fase 1b — Axonium y synaptum.** Instrumentar los SDKs propios con `argus-semconv`. **Es la fase de mayor apalancamiento por línea escrita**: toda app que los use gana trazas GenAI sin tocar nada.
→ *Trazabilidad de inferencia local en todo lo que pasa por Axonium.*

**Fase 2 — Detección en tiempo real y alertas, sin agentes.** Los **tres caminos separados** (§7.0): camino caliente con `filter` → `alert-bus` como receptor OTLP, camino templado con connectors y vistas materializadas, camino frío con tail sampling. SLOs y reglas burn-rate. `notifier` con Google Chat, email y **divulgación progresiva**. Canario sintético. Detectores de bucle y fuga de coste. **Se mide el presupuesto de latencia y se deja documentado.**
→ **El punto de mayor retorno por esfuerzo de todo el plan.** *Ya te enteras cuando algo se rompe, agrupado y sin ruido.*

**Fase 3 — Diagnóstico L3 + memoria.** Añadir Langfuse y Temporal al compose. Triage barato, diagnóstico de un disparo, y el **almacén de incidentes con búsqueda vectorial desde el principio** — aunque arranque vacío, cada incidente lo llena. Sirve además para construir el banco de pruebas.
→ *La notificación llega con contexto, no con un número.*

**Fase 4 — Investigación L4.** Workflow Temporal multi-paso con `mcp-obs` y `mcp-memory`. Arranca en **shadow mode**: investiga y registra, no notifica, hasta que el banco de pruebas diga que merece la pena. Trazar el agente en Langfuse. Añadir WhatsApp.
→ *Causa raíz con evidencia citada y nivel de confianza.*

**Fase 5 — Calidad, coste y deriva.** Evals online. `drift-monitor`. Agente de FinOps con atribución por app/funcionalidad/entorno. Bandas de anomalía (aquí ya hay ≥8 semanas de histórico). **Poda de alertas — para este punto la fatiga ya empieza a doler.**
→ *Te enteras cuando la IA empeora, no solo cuando falla.*

**Fase 6 — Explicabilidad, postmortem y L5.** Portal de explicabilidad. Agente de postmortem sobre la cadena de razonamiento. Remediación L5 acotada a acciones reversibles con aprobación por botón.
→ *Auditoría completa y reparación asistida para casos conocidos.*

**Fase 7 — El resto, y lo que venga.** Extender al resto del portafolio con la plantilla de dos líneas, priorizando por criticidad y volumen de tráfico LLM. A partir de aquí **añadir una app es rutina**, no un proyecto. Migrar el plano central al iMac cuando toque, ejecutando el runbook de la Fase 0.

### 10.3 Nota de calendario sobre los agentes

Los agentes no pueden ir antes de la Fase 3 por un motivo material: **necesitan varias semanas de histórico en ClickHouse** para que `compare_windows` y `deploy_history` devuelvan algo. Un agente de RCA sobre una semana de datos alucina líneas base.

---

## 11. Verificación

### Fase 0
```bash
podman-compose -f platform/compose.yaml --profile lean up -d
curl -sf http://localhost:4318/v1/traces -X POST -H 'content-type: application/json' -d @fixtures/sample-trace.json
```
- ✅ ClickHouse con las tablas creadas; HyperDX muestra la traza de prueba.
- ✅ El endpoint OTLP **rechaza** una petición sin token.
- ✅ **Prueba de portabilidad**: exportar volúmenes, restaurar en otra máquina, arrancar, y que los datos estén. Se hace una vez, en frío, antes de necesitarlo.

### Fase 1 — las librerías y la topología
- ✅ **La prueba que define la fase**: una unidad de trabajo que cruza API → cola → worker produce **una sola traza** con todos los spans. Si salen dos, la propagación fuera de HTTP no funciona y nada de lo que sigue sirve.
- ✅ **Prueba de la cola persistente**: parar el plano central, generar tráfico durante 10 minutos, arrancarlo, y confirmar que **toda** la telemetría aparece. Esta es la prueba que valida que el diseño sobrevive a cerrar la tapa del portátil.
- ✅ **Prueba de aislamiento**: con el Collector agente caído, la app no añade latencia perceptible ni crece en memoria.
- ✅ **Idempotencia**: `init()` dos veces no duplica spans.
- ✅ **No-op**: los decoradores de `argus-semconv` funcionan sin `init()` y sin coste medible.
- ✅ **Multi-host**: una app en otra máquina aparece en el mismo backend, con su `host.name` correcto.
- ✅ Una consulta agrupa por `service.namespace` y desglosa por `service.name`.
- ✅ Un `trace_id` se sigue del log al span.
- ✅ **Redacción**: una traza con email y tarjeta en el prompt llega enmascarada al almacén.

### Fase 1b — los SDKs propios
- ✅ Una app **con** `argus.init()` que use Axonium produce spans `chat {modelo}` con tokens poblados, **sin haber escrito instrumentación en la app**.
- ✅ Una app **sin** `argus.init()` que use Axonium funciona igual que antes, y un benchmark confirma **overhead no medible**.
- ✅ Los tests de Axonium pasan sin backend ni configuración.
- ✅ `pip install axonium` **no** arrastra ningún SDK de OTel ni fija versiones de exportadores.

### Fase 2 — detección y tiempo real
- ✅ **La prueba de tiempo real, medida y con número**: inyectar un error y medir el tiempo desde el `span.end()` hasta el mensaje en Google Chat. **Objetivo < 5 s, presupuesto 2 s.** Se mide con un reloj, no se estima.
- ✅ Desglosar esa latencia por tramo (app→agente, agente→bus, bus→chat) y confirmar que ningún tramo se come el presupuesto entero.
- ✅ **Prueba de que los caminos están separados**: con el tail sampling del gateway a 30 s, la alerta sigue llegando en segundos. Si se ralentiza, el camino caliente está pasando por donde no debe.
- ✅ **Divulgación progresiva**: un incidente con 50 eventos produce **un hilo** que se actualiza, no 50 mensajes ni un mensaje a los 60 s.
- ✅ Provocar un fallo real y confirmar alerta en Google Chat en <2 min.
- ✅ Diez fallos idénticos → **una** notificación, no diez.
- ✅ Alerta en un servicio dependiente **se suprime** cuando hay causa aguas arriba.
- ✅ Bucle de agente detectado por `tool_calls_per_run > P99`.
- ✅ **Apagar una app y confirmar que el canario alerta**: el silencio no puede parecer salud.
- ✅ **Arrancar un servicio nuevo no registrado** y confirmar que aparece como *provisional* **y** que genera el aviso de servicio no registrado.

### Fases 3–4 — agentes
- **Banco de pruebas de incidentes**: 5–10 incidentes reales o inyectados con causa raíz conocida. Única forma honesta de medir.
- ✅ Acierta la causa en ≥60 % del banco, y **nunca afirma con confianza alta una causa incorrecta** — esto importa más que el acierto.
- ✅ Cada traza ID y rango de métrica citados **existen de verdad**.
- ✅ El agente de memoria recupera el incidente correcto ante una redacción distinta del mismo síntoma.
- ✅ Trayectoria del agente visible en Langfuse; coste por investigación bajo presupuesto.
- ✅ **Inyección de prompt**: un log con instrucciones no cambia su comportamiento.
- ✅ **Suspender la máquina en mitad de una investigación** y confirmar que el workflow retoma al despertar.

### Fase 6
- ✅ Atribución SHAP para una predicción concreta, **sin haberla calculado en la ruta de servicio**.
- ✅ Informe post-mercado generado para una app.
- ✅ Una acción L5 exige clic de aprobación, registra quién aprobó, y es reversible.

---

## 12. Riesgos y decisiones abiertas

**Esto es mucha plataforma para una persona.** El riesgo principal no es técnico sino de alcance: una plataforma de observabilidad a medio construir es peor que no tenerla, porque genera confianza infundada. Mitigación: **puedes parar en la Fase 2 y seguir teniendo valor neto** — 30 % del esfuerzo, 70 % del beneficio.

**El plano central en un portátil es la restricción que más condiciona.** Se suspende, cambia de red, compite por recursos con la inferencia local, y algún día se migra. Las tres mitigaciones (agente local con cola persistente, nombre estable en red privada, y todo el estado en volúmenes con runbook de migración) son **requisitos de la Fase 0**, no mejoras posteriores. Si se dejan para después, se rehace todo.

**La plataforma no debe caer con lo que monitorea.** Todo en la misma máquina significa que si esa máquina cae te quedas sin telemetría justo cuando la necesitas. Mitigación mínima y barata: un *dead man's switch* **externo** que avise por WhatsApp si la plataforma deja de reportar. Sin esto, un fallo de la plataforma es indistinguible del silencio.

**El tiempo real y la topología portátil tiran en direcciones opuestas.** Conviven, pero hay un suelo: si el plano central está suspendido, la detección espera a que vuelva. Ninguna arquitectura arregla eso — lo único que se puede hacer es que la cola no pierda nada y que el *dead man's switch* externo avise de que la plataforma no está mirando.

**Rust es el hueco poliglota que quedará abierto.** Crates pre-1.0, sin cero-código, y con las trazas estabilizadas después que logs y métricas. La mitigación realista es acotar el alcance a las fronteras del núcleo de `AIBank`, no cubrirlo entero.

**Sin eBPF en macOS, la cobertura del nivel 0 es menor de lo que sería en Linux** — aunque menos de lo que temía: Go, Java y Python tienen cero-código que sí funciona en Mac. Consecuencia práctica: una app que no se instrumente en Mac será casi invisible por dentro — tendrás disponibilidad y salud del host, no latencia por endpoint ni errores internos. El canario sintético cubre parte del hueco, pero no todo. Conviene asumirlo en vez de descubrirlo.

**Los SDKs propios son un punto único de fallo para muchas apps.** Un bug en `argus-semconv` afecta a todo lo que importe Axonium. Por eso el paquete es minúsculo, depende solo de la API, y sus reglas son tests.

**Las semconv GenAI son experimentales y van a cambiar.** Por eso se fija la versión y su uso se aísla dentro de `argus-semconv`: cuando cambien, se toca un paquete, no todos los repos.

**El coste de los agentes no es despreciable.** Un agente L4 que investiga cada alerta puede consumir más tokens que las apps que vigila. De ahí el presupuesto duro y el triage determinista que filtra **antes** de invocar al caro. Con LLM locales el coste monetario es cero pero **compite por la misma máquina que la plataforma y las apps**.

**Decisiones que conviene resolver con datos, no de antemano:**
- ¿`alert-bus` propio o [Keep](https://www.keephq.dev/)? Keep ya hace correlación y enriquecimiento. Evaluarlo en la Fase 2 **antes** de escribir código.
- ¿Retención real? Seis meses para logs de decisión de IA está justificado por el AI Act; para infra, 30 días en caliente probablemente sobra — y en un portátil el disco es finito. Medir volumen en la Fase 1 y decidir entonces.
- ¿Qué modelo para el agente de RCA? Empezar con uno remoto potente para fijar el techo de calidad en el banco de pruebas, y luego medir cuánto se pierde bajando a uno local.
- ¿Merece la pena el agente de onboarding? Depende del ritmo de altas de apps. Reevaluar cuando el catálogo pase de veinte.

---

## 13. Fuentes

**Poliglota y tiempo real**
- [Language APIs & SDKs — OpenTelemetry](https://opentelemetry.io/docs/languages/)
- [Announcing v1 of OpenTelemetry Go Compile-Time Instrumentation](https://opentelemetry.io/blog/2026/go-compile-time-instrumentation-v1/)
- [Go compile-time instrumentation — OpenTelemetry](https://opentelemetry.io/docs/zero-code/go/compile-time/)
- [Compile-Time OpenTelemetry Auto-Instrumentation in Go — Dash0](https://www.dash0.com/guides/otelc-opentelemetry-go)
- [OpenTelemetry for Rust: Traces, Metrics & Logs — OpenObserve](https://openobserve.ai/opentelemetry/rust/)
- [How to use auto-instrumentation with OpenTelemetry — Red Hat Developer](https://developers.redhat.com/articles/2026/02/25/how-use-auto-instrumentation-opentelemetry)
- [How to Build Real-Time Alerting with ClickHouse](https://oneuptime.com/blog/post/2026-03-31-clickhouse-real-time-alerting/view)
- [What is Real-Time Analytics? A Complete Guide (2026) — ClickHouse](https://clickhouse.com/resources/engineering/what-is-real-time-analytics)
- [What is observability in 2026? Why it's an analytics problem — ClickHouse](https://clickhouse.com/resources/engineering/what-is-observability)
- [spanmetrics connector — opentelemetry-collector-contrib](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/connector/spanmetricsconnector/README.md)
- [How to Configure the Signal to Metrics Connector in the OpenTelemetry Collector](https://oneuptime.com/blog/post/2026-02-06-signal-to-metrics-connector-opentelemetry-collector/view)

**OpenTelemetry: API vs SDK, diseño de librerías y propagación**
- [Libraries — OpenTelemetry (principios de diseño de instrumentación)](https://opentelemetry.io/docs/concepts/instrumentation/libraries/)
- [Instrumentation — OpenTelemetry](https://opentelemetry.io/docs/concepts/instrumentation/)
- [OpenTelemetry API vs SDK — Key Differences Explained (SigNoz)](https://signoz.io/comparisons/opentelemetry-api-vs-sdk/)
- [OpenTelemetry API vs. SDK: Why the Difference (and Your Instrumentation) Matters](https://dev.to/aws-builders/opentelemetry-api-vs-sdk-why-the-difference-and-your-instrumentation-matters-44ng)
- [Create OpenTelemetry Instrumentation Libraries Shared Across Your Platform](https://oneuptime.com/blog/post/2026-02-06-shared-opentelemetry-instrumentation-libraries/view)
- [OpenTelemetry Context Propagation: W3C Trace Context and Baggage — Uptrace](https://uptrace.dev/opentelemetry/context-propagation)
- [OpenTelemetry Context Propagation: How It Works (2026) — Last9](https://last9.io/blog/opentelemetry-context-propagation/)
- [Building a Polyglot Distributed Tracing Pipeline with OpenTelemetry](https://medium.com/codex/building-a-polyglot-distributed-tracing-pipeline-with-opentelemetry-from-zero-to-end-to-end-c14a0506d50c)

**Collector: resiliencia, topología y muestreo**
- [Resiliency — OpenTelemetry Collector](https://opentelemetry.io/docs/collector/resiliency/)
- [Gateway deployment pattern — OpenTelemetry](https://opentelemetry.io/docs/collector/deploy/gateway/)
- [How to Configure the Storage Extension in the OpenTelemetry Collector](https://oneuptime.com/blog/post/2026-02-06-storage-extension-opentelemetry-collector/view)
- [exporterhelper README — persistent queue](https://github.com/open-telemetry/opentelemetry-collector/blob/main/exporter/exporterhelper/README.md?plain=1)
- [Tail-Based Sampling with the OpenTelemetry Collector](https://www.controltheory.com/resources/tail-sampling-with-the-otel-collector/)

**Zero-code y eBPF (y sus límites)**
- [Zero-code instrumentation — OpenTelemetry](https://opentelemetry.io/docs/concepts/instrumentation/zero-code/)
- [OpenTelemetry eBPF Instrumentation (OBI)](https://opentelemetry.io/docs/zero-code/obi/)
- [opentelemetry-ebpf-instrumentation (GitHub)](https://github.com/open-telemetry/opentelemetry-ebpf-instrumentation)
- [How to Use opentelemetry-instrument CLI for Zero-Code Python Instrumentation](https://oneuptime.com/blog/post/2026-02-06-opentelemetry-instrument-cli-zero-code-python/view)

**Convenciones GenAI**
- [AI Agent Observability — Evolving Standards and Best Practices (OpenTelemetry)](https://opentelemetry.io/blog/2025/ai-agent-observability/)
- [OpenTelemetry GenAI Semantic Conventions: Your LLM Traces Should Look Like This in 2026](https://dev.to/gabrielanhaia/opentelemetry-genai-semantic-conventions-your-llm-traces-should-look-like-this-in-2026-3ff6)
- [semantic-conventions-genai (repositorio oficial)](https://github.com/open-telemetry/semantic-conventions-genai)
- [OpenTelemetry for AI Systems: LLM and Agent Observability (2026) — Uptrace](https://uptrace.dev/blog/opentelemetry-ai-systems)

**Backend**
- [ClickStack: High-Performance Open Source Observability](https://clickhouse.com/clickstack)
- [Langfuse — OpenTelemetry (OTEL) for LLM Observability](https://langfuse.com/integrations/native/opentelemetry)
- [Langfuse v3 Self-Hosting — Complete LLM Tracing Guide](https://jangwook.net/en/blog/en/langfuse-self-hosted-llm-tracing-setup-guide-2026/)
- [Datadog vs SigNoz vs Grafana vs OpenObserve 2026 — APIScout](https://apiscout.dev/guides/datadog-vs-signoz-vs-grafana-vs-openobserve-2026)

**AIOps agéntico, flota de agentes y memoria de incidentes**
- [What Is Multi-Agent SRE? A Practical Introduction](https://dev.to/samson_tanimawo/what-is-multi-agent-sre-a-practical-introduction-4m6c)
- [AI SRE in Incident Management: How AI Agents Handle On-Call — Augment Code](https://www.augmentcode.com/guides/ai-sre-incident-management)
- [AI SRE: The 2026 Guide to AI-Powered Site Reliability Engineering](https://www.augmentcode.com/guides/ai-sre-ai-powered-site-reliability-engineering)
- [AI-Powered Incident Investigation: The Complete Guide for SRE Teams (2026)](https://www.arvoai.ca/blog/ai-powered-incident-investigation)
- [Building Memory for Self-Healing AI Agents](https://medium.com/@daryadi.foo/building-memory-for-self-healing-agents-7cabba799f77)
- [Automated Post-Mortem Generation: The Complete Guide for SRE Teams (2026)](https://dev.to/siddharth_singh_409bd5267/automated-post-mortem-generation-the-complete-guide-for-sre-teams-2026-55ck)
- [Predictive AI Observability Trends Shaping 2026 Ops — Rootly](https://rootly.com/sre/predictive-ai-observability-trends-shaping-2026-ops)
- [awesome-LLM-AIOps (GitHub)](https://github.com/Jun-jie-Huang/awesome-LLM-AIOps)

**Alertamiento, fatiga y MCP**
- [Alert Fatigue Is a Design Problem: Routing, Tiers, and the Weekly Prune](https://johal.in/alert-fatigue-routing-design-guide)
- [Grafana OnCall Is Gone: Picking a Self-Hosted On-Call Tool in 2026](https://www.bigiron.cc/guides/grafana-oncall-is-gone-self-hosted-on-call-in-2026)
- [Keep — Open-source AIOps platform](https://www.keephq.dev/)
- [promql-anomaly-detection (Grafana, GitHub)](https://github.com/grafana/promql-anomaly-detection)
- [What MCP Servers Are Available for Observability? — OpenObserve](https://openobserve.ai/blog/mcp-servers-observability-guide/)

**Calidad de IA, fallos de agentes, deriva y coste**
- [AI Agent Failure Modes: Tool-Calling Errors, Infinite Loops & Propagation — Openlayer](https://www.openlayer.com/blog/ai-agent-failure-modes-tool-calling-loops-propagation)
- [Agent observability: The complete guide for 2026 — Braintrust](https://www.braintrust.dev/articles/agent-observability-complete-guide-2026)
- [What are AI hallucination evaluations? Metrics and methods that work in 2026 — Braintrust](https://www.braintrust.dev/articles/ai-hallucination-evaluations-metrics-methods-2026)
- [Detecting hallucinations with LLM-as-a-judge — Datadog](https://www.datadoghq.com/blog/ai/llm-hallucination-detection/)
- [TokenOps: The Definitive Guide to FinOps for Tokens (2026)](https://www.opslyft.com/blog/token-economics-tokenops-finops-for-tokens)
- [AI Agent Cost Optimization: Token Economics and FinOps in Production — Zylos](https://zylos.ai/research/2026-02-19-ai-agent-cost-optimization-token-economics/)
- [What is data drift in ML, and how to detect and handle it — Evidently AI](https://www.evidentlyai.com/ml-in-production/data-drift)
- [Model Drift vs. Concept Drift: Detection & Mitigation for 2026 — Lumenova](https://www.lumenova.ai/blog/model-drift-concept-drift-introduction/)

**Explicabilidad y cumplimiento**
- [EU AI Act — Artículo 72: Post-Market Monitoring](https://artificialintelligenceact.eu/article/72/)
- [What the EU AI Act requires for AI agent logging — Help Net Security](https://www.helpnetsecurity.com/2026/04/16/eu-ai-act-logging-requirements/)
- [SHAP, LIME & Captum: AI Explainability Methods Compared (2026)](https://aisecurityandsafety.org/en/guides/shap-lime-explainability/)
- [Best explainable AI tools for tracing LLM decisions in 2026 — Braintrust](https://www.braintrust.dev/articles/best-explainable-ai-tools-2026)

**Formato de telemetría y canales**
- [Fast and flexible observability with canonical log lines — Stripe](https://stripe.com/blog/canonical-log-lines)
- [Wide events or pillars of observability: A decision framework — Parseable](https://www.parseable.com/blog/decision-framework-wide-events-or-traces)
- [WhatsApp Business API Pricing 2026: Conversation Categories, Costs, and What Changed](https://blueticks.co/blog/whatsapp-business-api-pricing-2026)
- [How to Use Google Chat Webhooks: A Complete Guide](https://softwareengineeringstandard.com/2025/09/01/google-chat-webhook/)
