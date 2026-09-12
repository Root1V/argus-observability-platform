# Decisiones de arquitectura

Registro de las decisiones importantes de Argus, con el porqué y lo que cuestan.

**Para qué sirve este documento**: dentro de seis meses, cuando alguien —tú
incluido— se pregunte "¿por qué está hecho así?", la respuesta está aquí en vez
de perdida en un hilo de chat. Y cuando una decisión deje de tener sentido,
poder revocarla sabiendo qué se rompe.

**Formato**: cada decisión lleva estado, contexto, la decisión en sí, y sus
consecuencias — incluidas las malas. Una decisión sin coste declarado suele ser
una decisión mal entendida.

| Estado | Significado |
|---|---|
| ✅ Vigente | En efecto y verificada |
| 🧪 Vigente sin validar | Tomada e implementada, pero aún sin prueba que la respalde |
| 🔄 Revisada | Sustituida por otra; se conserva por el razonamiento |
| ⚠️ Revocada | Ya no aplica |

---

## D-001 · Instrumentar contra OpenTelemetry, no contra un proveedor

**Estado**: ✅ Vigente

**Contexto**. Ocho de los repos ya tenían OTel, cada uno exportando a su propio
backend aislado. La alternativa era adoptar una plataforma comercial y usar su
SDK.

**Decisión**. Instrumentar contra OpenTelemetry puro, con las semconv GenAI de
2026. El backend es un exportador reemplazable.

**Por qué**. El consenso de 2026 es no atar la observabilidad a ningún proveedor
en la capa que importa. Y OTel se graduó de la CNCF en mayo de 2026, al nivel de
Kubernetes y Prometheus: la apuesta es segura.

**Consecuencias**. Más trabajo inicial que adoptar un SDK propietario. A cambio,
cambiar de backend no toca ni una aplicación. Las semconv GenAI siguen siendo
**experimentales**, así que su versión se fija en el modelo (`argus.yaml`) y las
subidas se tratan como migraciones planificadas.

---

## D-002 · Dos paquetes: `argus-semconv` para librerías, `argus-sdk` para aplicaciones

**Estado**: ✅ Vigente · verificada por `test_import_does_not_pull_in_sdk`

**Contexto**. Surgió de una pregunta concreta: *¿Axonium debe importar el SDK de
observabilidad?* Axonium es el cliente de inferencia local que muchas
aplicaciones importan.

**Decisión**. **No.** Las librerías importan `argus-semconv`, que depende **solo
de `opentelemetry-api`**. Solo las aplicaciones importan `argus-sdk`.

**Por qué**. La especificación de OpenTelemetry es tajante: una librería que
depende del SDK *"impone en silencio su versión del SDK y sus opiniones sobre
exportadores a todo el que dependa de ella"*. Con quince o más aplicaciones
importando Axonium, eso es un infierno de dependencias garantizado.

Y la API es un conjunto de abstracciones con implementaciones no operativas: si
la aplicación no inicializó un SDK, la instrumentación **no hace nada y no tiene
impacto en el rendimiento**.

**Consecuencias**. La mejor de todas: instrumentar Axonium una vez da trazas
GenAI completas a **toda** aplicación que lo use y haya llamado a `init()`, y
cuesta cero en las que no. Ninguna aplicación tiene que enrutar tráfico por
ningún sitio — es apalancamiento sin acoplamiento.

El coste: dos paquetes que mantener, y la disciplina de que `argus-semconv`
nunca declare una versión exacta de `opentelemetry-api`, solo un rango.

---

## D-003 · Las aplicaciones exportan siempre a `localhost`

**Estado**: ✅ Vigente

**Contexto**. El plano central vive hoy en una MacBook Pro y mañana en un iMac.
Las aplicaciones pueden estar en esa misma Mac, en otra, o en un servidor.

**Decisión**. Topología de **dos niveles de Collector**. Las aplicaciones
exportan siempre a `http://localhost:4317` y **nunca conocen la dirección del
plano central**. Un Collector agente por máquina reenvía al gateway.

**Por qué**. Da tres propiedades que se pierden si las apps apuntan al central:

1. Mover el plano central de una máquina a otra **no toca ni una aplicación**.
2. Si el central está suspendido, las apps no se enteran ni se ralentizan.
3. Añadir una máquina es desplegar un agente, no reconfigurar apps.

**Consecuencias**. Un proceso más por máquina. A cambio, la portabilidad deja de
ser un proyecto y pasa a ser un cambio de DNS.

---

## D-004 · Cola persistente en disco en cada agente

**Estado**: ✅ Vigente · pendiente de la prueba de suspensión (`F1-09`)

**Contexto**. El plano central vive en un portátil que se cierra, cambia de red
y a veces está apagado.

**Decisión**. Cada Collector agente usa la extensión de almacenamiento en
fichero, con la cola de envío respaldada en disco y `max_elapsed_time: 24h`.

**Por qué**. La cola escribe a un WAL en disco **antes** de intentar exportar,
así que absorbe cortes sin perder datos, y al reiniciar recoge lo pendiente. Sin
esto, cada suspensión es pérdida de datos — y peor, deja **huecos que el agente
de RCA leerá como silencio en vez de como ausencia de datos**.

**Consecuencias**. Hay un límite real: si el disco se llena o el central sigue
inalcanzable más allá de los reintentos, se pierde igual. Por eso la ocupación
de la cola es una alerta por sí misma (`ArgusAgentQueueGrowing`).

---

## D-005 · Tres caminos con latencias distintas

**Estado**: 🧪 Vigente sin validar · el camino caliente no se puede medir hasta `F2-01`

**Contexto**. El usuario pidió que la solución fuera lo más de tiempo real
posible. Una tubería convencional acumula latencia en cada salto: procesador de
spans ~5 s, batch del agente ~5 s, **tail sampling esperando hasta 30 s**, batch
del gateway 5 s, scrape 15 s, evaluación de reglas 15–60 s. Detectar tarda más
de un minuto.

El conflicto es de fondo: **el tail sampling tiene que esperar a que la traza
cierre** para decidir si la guarda. Correcto para almacenar, inaceptable para
detectar.

**Decisión**. No meter todo por la misma tubería.

| Camino | Presupuesto | Qué lleva | A dónde |
|---|---|---|---|
| Caliente | ~2 s | Errores, SLO roto, guardarraíl | Directo al `alert-bus`, **sin tocar la base de datos** |
| Templado | 2–15 s | Métricas RED derivadas en vuelo | VictoriaMetrics, vistas materializadas |
| Frío | 30–60 s | Traza completa, muestreada | ClickHouse + Langfuse |

**Por qué**. La detección deja de consultar la base de datos y pasa a ocurrir
**sobre el flujo**. Eso es lo que separa segundos de minutos. El volumen del
camino caliente es bajo por definición —los errores son la excepción—, así que
bajar el lote a 200 ms no cuesta nada.

**Consecuencias**, dichas sin adornos:
- **Más falsos positivos**: decidir en 2 s es decidir con menos evidencia. Por
  eso la notificación inmediata se reserva a condiciones inequívocas, no a
  umbrales estadísticos.
- **El camino caliente no ve la traza completa**, solo spans sueltos. Sirve para
  decir *"algo falla aquí"*, no *por qué*. El porqué llega por el camino frío, y
  eso está bien: son dos preguntas con urgencias distintas.
- Hay un suelo: si el plano central está suspendido, la detección espera.

---

## D-006 · Fan-out, no `routing` connector

**Estado**: ✅ Vigente

**Contexto**. Lo natural parecería enrutar los spans GenAI a Langfuse y el resto
a ClickHouse.

**Decisión**. **No usar el `routing` connector.** ClickHouse recibe la traza
**completa**; Langfuse recibe solo el subárbol GenAI; los `trace_id` son
idénticos en ambos.

**Por qué**. El `routing` connector con `context: span` separa spans del **mismo
trace** hacia tuberías distintas: el span `chat qwen` se iría a Langfuse y su
padre HTTP a ClickHouse, dejando la traza de ClickHouse con un hueco. Y
reconstruir ese árbol completo es justo lo que el agente de RCA necesita para
poder decir *"esta latencia de LLM causó aquel timeout"*.

**Consecuencias**. Los spans GenAI se procesan dos veces. A cambio, deep-link
bidireccional gratis entre ambas UIs y una traza reconstruible.

---

## D-007 · Identidad de dos niveles

**Estado**: ✅ Vigente

**Decisión**. `service.namespace` es la **aplicación**; `service.name` es el
**sub-componente**; `argus.component.role` dice qué forma tiene.

**Por qué**. Las aplicaciones tienen sub-componentes (API, worker, scheduler,
CLI, servidor de modelos). Sin la separación acabas con decenas de servicios
planos sin forma de agruparlos por aplicación, y **el problema empeora con cada
app nueva**.

**Consecuencias**. *"¿Cómo está la IDP?"* es un `WHERE service.namespace`, y
*"¿qué componente falla?"* un `GROUP BY service.name`.

---

## D-008 · El contenido de prompts va en atributos, no en eventos de span

**Estado**: ✅ Vigente · verificada por `test_content_goes_to_attributes_not_events`

**Contexto**. La guía canónica de las semconv GenAI dice que el contenido debe ir
en **eventos** de span, para poder descartarlo en el Collector sin tocar código.
El razonamiento es bueno.

**Decisión**. Emitirlo en **atributos**, contra la guía.

**Por qué**. Langfuse lee `gen_ai.input.messages` y `gen_ai.output.messages` de
los **atributos**. Si seguimos la guía al pie de la letra, Langfuse ingiere el
span y lo muestra **sin input ni output** — es decir, inútil.

**Cómo se recupera lo que la guía buscaba**: el contenido se borra en la rama de
ClickHouse con un `attributes` processor. Así existe en **exactamente un sitio**
(Langfuse, con control de acceso por proyecto), y ClickHouse guarda estructura y
métricas sin payloads de decenas de KB.

**Consecuencias**. Si algún día se cambia de Langfuse a un backend que sí lea
eventos, hay que revisar esta decisión. Está aislada en `_content.py`.

---

## D-009 · `Resource.create()`, nunca el constructor

**Estado**: ✅ Vigente · verificada por `test_resource_create_honours_otel_resource_attributes`

**Decisión**. Construir el Resource siempre con `Resource.create()`.

**Por qué**. El constructor directo **salta los detectores y la variable
`OTEL_RESOURCE_ATTRIBUTES`**, en silencio. Y esa variable es justo el mecanismo
por el que se inyecta la versión y el entorno desde el compose sin tocar código.

Es un fallo de una línea con impacto en todos los repos, y **no da ningún
error**: simplemente los atributos no aparecen.

---

## D-010 · Confianza de red, no de identidad, para adoptar `traceparent`

**Estado**: ✅ Vigente · verificada por los tests de `test_asgi_trust.py`

**Contexto**. Adoptar el `traceparent` entrante es lo que hace que una traza
cruce servicios, y a la vez es una superficie de ataque si el servicio está
expuesto (OWASP A03). La respuesta habitual —desactivarlo— deja sin trazas
distribuidas, que es el problema que teníamos.

**Decisión**. Tres modos: `never` para lo expuesto a internet, `trusted` (adopta
solo desde CIDRs internos y loopback) para el caso normal, `always` para redes
privadas.

**Por qué**. Las aplicaciones hablan entre ellas dentro de la red privada; el
mundo exterior no. La confianza es de **red**, no de identidad — lo cual además
evita tener que reordenar middlewares para que la adopción ocurra después de la
autenticación.

---

## D-011 · Convenciones generadas desde un único YAML

**Estado**: ✅ Vigente · verificada por `test_generated_constants_match_the_model`

**Decisión**. `libs/semconv-model/argus.yaml` es la fuente de verdad;
`tools/gen_semconv.py` genera las constantes de Python y Go. Un test de CI falla
si lo generado no coincide con lo commiteado.

**Por qué**. Con cinco lenguajes, el fallo que rompe las consultas agregadas **no
es que un servicio no emita**, sino que dos emitan lo mismo con nombres
distintos. Generar en vez de escribir a mano lo hace imposible.

---

## D-012 · Temporal como runtime de los agentes

**Estado**: 🧪 Vigente sin validar · se ejerce en `F3`

**Decisión**. Temporal, que ya está desplegado en `aeon-ai`. Worker propio con
task queue propia.

**Por qué**. El requisito duro es la **durabilidad** y la **espera de aprobación
humana**, y es exactamente el problema que Temporal resuelve y que synaptum,
LangGraph y el Claude Agent SDK no resuelven sin que lo construyas tú. Además,
el plano central es un portátil que se suspende: un workflow durable sobrevive a
eso; un bucle en memoria, no.

**Matiz que importa tanto como la elección**: L4 es un workflow donde **cada
llamada a herramienta es una activity**, no un bucle de agente dentro de una
activity. Si envuelves el bucle entero en una activity de 8 minutos y el worker
se reinicia al minuto 7, Temporal reintenta la activity completa y vuelves a
pagar todas las inferencias.

**Consecuencias**. Worker separado obligatorio: `aeon-ai` documenta un conflicto
irresoluble entre `crewai` y `openai-agents`. Se comparte el servidor Temporal,
**no el árbol de dependencias**.

---

## D-013 · L3 no debe ser un agente

**Estado**: 🧪 Vigente sin validar · se ejerce en `F3`

**Decisión**. El diagnóstico de un disparo es una llamada con salida
estructurada sobre contexto pre-recolectado por consultas fijas. Una activity
suelta, no un workflow.

**Por qué**. Sin bucle, sin herramientas, p95 < 10 s, coste predecible. Meterle
un agente es pagar orquestación por algo que es un prompt.

---

## D-014 · El notificador no es un LLM suelto

**Estado**: 🧪 Vigente sin validar · se ejerce en `F2`

**Decisión**. A quién avisar es una **regla del registro**, no un juicio. El LLM
solo redacta el texto.

**Por qué**. Evita el fallo clásico de *"el agente decidió no avisar a nadie"*.

---

## D-015 · Divulgación progresiva en las notificaciones

**Estado**: 🧪 Vigente sin validar · se ejerce en `F2-05`

**Contexto**. Agrupar alertas 60 s reduce el ruido pero mata el tiempo real.

**Decisión**. Un `page` notifica **de inmediato** con lo poco que se sabe; los
eventos siguientes actualizan el mismo hilo; cuando el agente termina, **edita
ese mismo mensaje**.

**Por qué**. El primer aviso llega en segundos y el informe completo cuando está
listo, **sin duplicar notificaciones**. Severidades menores sí se agrupan con
ventana, porque ahí la rapidez no compra nada.

---

## D-016 · VictoriaMetrics en lugar de Prometheus

**Estado**: ✅ Vigente

**Decisión**. VictoriaMetrics single-node para métricas y reglas.

**Por qué**. Un solo binario, mucha menos memoria, PromQL compatible. El plano
central comparte máquina con inferencia local: la diferencia se nota.

---

## D-017 · Modo ligero por defecto, perfiles para crecer

**Estado**: ✅ Vigente

**Decisión**. `--profile lean` levanta cuatro contenedores. Langfuse y Temporal
llegan con `genai` y `agents`.

**Por qué**. El stack completo es demasiado para un portátil que además corre
inferencia. Y permite parar en la Fase 2 con valor neto.

---

## D-018 · La redacción nunca toca los atributos de identidad

**Estado**: ✅ Vigente · **descubierta durante la implementación**

**Contexto**. La prueba de humo end-to-end falló: `service.namespace` llegaba a
ClickHouse como `smoke-****`.

**Causa**. El patrón de teléfono capturaba **cualquier tirada de diez dígitos**,
y `smoke-1789238954` encajaba.

**Decisión**. Dos cambios. (a) Los atributos de identidad van en `ignored_keys`
del `redaction` processor. (b) El patrón de teléfono exige **separadores o
prefijo internacional**: una tirada suelta de diez dígitos es casi siempre un
identificador, un timestamp o un contador.

**Por qué importa**. Enmascarar `service.namespace` rompe agrupar por aplicación
**y** enrutar la notificación a su dueño. Destruye datos útiles sin proteger
nada.

**Lección**. Las dos capas de enmascarado —SDK y Collector— **tienen que usar
los mismos patrones**, o los datos salen distintos según por dónde pasen. Fijado
con tests de regresión en ambos lados.

---

## D-019 · La rama de Langfuse vive en un overlay

**Estado**: ✅ Vigente · **descubierta durante la implementación**

**Contexto**. En perfil ligero, el exportador de Langfuse reintentaba cada pocos
segundos contra un host que no existe, llenando el log de avisos.

**Decisión**. `gateway.genai.yaml` es un overlay que solo se carga cuando
Langfuse está desplegado, y `compose.genai.yaml` levanta las dos mitades juntas.

**Por qué**. Un pipeline apuntando a un host inexistente es exactamente el tipo
de ruido **que te enseña a ignorar los logs**. Y el overlay hace imposible
arrancar Langfuse sin su pipeline o el pipeline sin Langfuse.

---

## D-020 · Sin healthcheck de contenedor para el Collector

**Estado**: ✅ Vigente · **descubierta durante la implementación**

**Contexto**. El healthcheck fallaba con *"wget: executable file not found"*.

**Causa**. La imagen del Collector es **distroless**: no trae `wget`, `curl` ni
shell.

**Decisión**. Sin healthcheck de contenedor. La extensión `health_check` sigue
expuesta en 13133 y se comprueba **desde fuera**, que es donde el dato sirve.

**Consecuencia**. `depends_on: service_healthy` no se puede usar contra el
Collector. Nada depende de él en el compose, así que no molesta.

---

## D-021 · Init container para la cola persistente

**Estado**: ✅ Vigente · **descubierta durante la implementación**

**Contexto**. El Collector entraba en bucle de reinicio con *"mkdir
/var/lib/argus/gateway-queue: permission denied"*.

**Causa**. El Collector corre sin privilegios (uid 10001) y un volumen nombrado
nace perteneciendo a root.

**Decisión**. Un init de `busybox` que crea el directorio y ajusta la propiedad,
con `service_completed_successfully` como dependencia.

**Alternativa descartada**: correr el Collector como root. La cola persistente
es demasiado importante (D-004) para dejarla a merced de un fallo de permisos,
pero no lo bastante como para renunciar al mínimo privilegio.

---

## D-022 · La configuración de pytest vive solo en la raíz

**Estado**: ✅ Vigente · **descubierta durante la implementación**

**Contexto**. Ejecutar un fichero de test suelto fallaba con *"fixture 'spans'
not found"*, aunque la suite completa pasaba.

**Causa**. Un `[tool.pytest.ini_options]` en un sub-paquete lo convierte en
`rootdir` cuando se le pasa una ruta explícita, y entonces pytest **deja de
cargar el `conftest.py` de la raíz**.

**Decisión**. La configuración de pytest existe **solo** en el `pyproject.toml`
de la raíz del workspace.

**Relacionado**. OpenTelemetry solo permite fijar el `TracerProvider` global
**una vez por proceso**. Por eso hay un único proveedor de sesión en el
`conftest.py` raíz: si cada módulo montara el suyo, solo el primero recibiría
spans.

---

## D-023 · Verificar contra el stack real, no con mocks

**Estado**: ✅ Vigente

**Decisión**. Las configs del Collector se validan contra su **binario real**, y
`scripts/e2e_smoke.py` escribe en la **ClickHouse de verdad** y consulta lo que
llegó.

**Por qué**. Las cinco decisiones marcadas como *descubiertas durante la
implementación* (D-018 a D-022) **las encontró esta verificación**. Ninguna
habría salido con mocks: un fallo en una config del Collector es un contenedor
en bucle de reinicio a las tres de la mañana.

**Consecuencia**. La verificación completa tarda minutos, no segundos. Vale la
pena.

---

## D-024 · Puerto 9010 para ClickHouse

**Estado**: ✅ Vigente · **descubierta durante la implementación**

El puerto nativo 9000 estaba ocupado por otro proyecto de la misma máquina.
Dentro de la red de compose sigue siendo 9000; solo cambia el mapeo al host.
Síntoma de algo más general: **el plano central comparte máquina con todo lo
demás**, y eso condiciona puertos, memoria y GPU.

---

## Decisiones aún por tomar

Se resuelven con datos, no de antemano. Están en el backlog (`roadmap.md`).

| Decisión | Cuándo resolverla |
|---|---|
| ¿`alert-bus` propio o [Keep](https://www.keephq.dev/)? | Evaluar Keep en `F2-01`, **antes** de escribir código |
| ¿Retención real de cada señal? | Medir volumen en `F1`; el disco de un portátil es finito |
| ¿Qué modelo para el agente de RCA? | Fijar el techo con uno remoto en `F4`, luego medir cuánto se pierde con uno local |
| ¿Merece la pena el agente de onboarding? | Reevaluar cuando el catálogo pase de veinte apps |
