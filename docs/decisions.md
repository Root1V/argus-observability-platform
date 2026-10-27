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

**Estado**: ⛔ Sustituida por D-135 (25/10): los agentes se construyen con synaptum. Se conserva el razonamiento sobre durabilidad, que synaptum cubre

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

## D-026 · En la máquina del plano central, el agente se queda con 4317/4318

**Estado**: ✅ Vigente · **descubierta durante la implementación**

**Contexto**. Al desplegar el Collector agente en la misma máquina que el
gateway, el arranque falló: *"Bind for 0.0.0.0:4317 failed: port is already
allocated"*. Los dos quieren los puertos OTLP estándar.

**Decisión**. **Gana el agente.** El gateway pasa a publicarse en 14317/14318.

**Por qué**. El invariante de D-003 —*las aplicaciones exportan siempre a
`localhost:4317`*— tiene que valer en **todas** las máquinas, incluida la que
aloja el plano central. Si ahí fuera distinto, esa máquina sería la excepción
que hay que recordar, y las excepciones que hay que recordar se olvidan.

Al gateway solo lo alcanzan Collector agente, y esos usan un endpoint
configurable: cambiarles el puerto no cuesta nada.

**Relacionado**. El agente tenía sus receptores atados a `127.0.0.1`, que
**dentro de un contenedor es el loopback del contenedor**: el mapeo de puertos
de Docker nunca le habría llegado. Quien restringe la exposición es el mapeo del
host (`127.0.0.1:4317:4317`), no la config del Collector.

---

## D-027 · Agente → gateway por OTLP/HTTP, no gRPC

**Estado**: ✅ Vigente · **descubierta durante la implementación**

**Contexto**. El agente no arrancaba: *"grpc: the credentials require transport
level security"*. gRPC se niega a enviar credenciales por un canal sin TLS.

**Y hace bien.** No puede verificar que el transporte esté cifrado, así que
asume lo peor. Es una decisión de diseño correcta de gRPC, no un obstáculo.

**Decisión**. El camino frío del agente al gateway usa **OTLP/HTTP** con el
token en la cabecera.

**Por qué, y qué se está asumiendo**. Nuestro modelo de seguridad separa dos
cosas que suelen confundirse:

- **La confidencialidad la pone la red privada** (WireGuard). El túnel cifra.
- **El token protege la integridad** de lo que entra, no su confidencialidad.
  Importa porque los agentes de IA leen esta telemetría y sacan conclusiones:
  aceptar señales de cualquiera es aceptar conclusiones de cualquiera.

Con HTTP esa separación queda explícita en la configuración en vez de escondida
detrás de un `insecure: true`.

**Cuándo deja de valer**. Si la telemetría sale alguna vez a una red que no
controlas, **el token solo no basta**: hay que poner TLS. Está anotado en la
propia config para que quien la lea lo vea.

**Consecuencia menor**. HTTP tiene algo más de sobrecoste que gRPC. Con el
volumen del camino frío es irrelevante, y la medición del camino caliente
(120 ms p95 contra un presupuesto de 2 s) deja margen de sobra.

---

## D-029 · La notificación son *sinks* en proceso, no un servicio aparte

**Estado**: ✅ Vigente · **revisa lo que decía el plan**

**Contexto**. `docs/PLAN.md` y el roadmap listaban `services/notifier` como un
servicio propio. Al ir a construirlo, la separación no se sostiene.

**Decisión**. Los canales son **implementaciones del `Sink`** dentro del
`alert-bus`, despachadas por una **cola con hilo trabajador**.

**Por qué no un servicio aparte**:
- Añade un salto de red en el camino crítico, con un presupuesto de 2 s que
  ahora sabemos que se gasta en 120 ms — pero que no conviene malgastar.
- Un proceso más en una máquina que ya corre inferencia local (D-017).
- Y un modo de fallo nuevo: el `alert-bus` detecta el incidente y no puede
  avisar porque el notificador no responde.

**Por qué la cola con trabajador sí es imprescindible**. El argumento real a
favor de separar era el **aislamiento**: un webhook lento no puede bloquear la
detección. Eso se consigue con una cola en proceso, que es mucho más barato que
un servicio. El envío ocurre fuera del camino de ingesta, y un canal que tarda
treinta segundos no retrasa ni un milisegundo la detección del siguiente
incidente.

**Cuándo habría que revisarlo**. Si los canales crecen hasta necesitar
reintentos persistentes entre reinicios, o si alguien quiere notificar desde
algo que no sea el `alert-bus`. Ninguna de las dos pasa hoy.

**Consecuencia**. `services/notifier/` no se crea. Los canales viven en
`alert_bus/sinks/`, y el roadmap (`F2-04`) se actualiza para reflejarlo.

---

## D-030 · El registro distingue `activo` de `planificado`

**Estado**: ✅ Vigente · **descubierta durante la implementación**

**Contexto**. Al arrancar el canario por primera vez, empezó a reportar como
silenciosas todas las aplicaciones del portafolio. Correctamente: están en el
registro pero **aún no están instrumentadas**.

**Decisión**. `estado` tiene tres valores, y solo uno genera vigilancia:

| Estado | Significado |
|---|---|
| `activo` | Emite telemetría **hoy**. Su silencio es un incidente |
| `planificado` | Está en el roadmap, aún no instrumentada. **No se vigila** |
| `retirado` | Ya no existe |

**Por qué importa más de lo que parece**. Un canario que grita cada cinco
minutos por cosas que **sabes** que no están es un canario que se silencia. Y
un canario silenciado no vale nada el día que grite por algo real.

La distinción permite que el registro liste el portafolio **entero** como hoja
de ruta —que es útil para el grafo de dependencias y para saber qué falta— sin
que eso genere guardias.

---

## D-031 · La sonda de silencio consulta métricas, no trazas

**Estado**: ✅ Vigente · **descubierta durante la implementación**

**Contexto**. El canario reportaba silencio de servicios que estaban vivos y
emitiendo.

**Causa**. La sonda consultaba `otel_traces`, y **las trazas pasan por tail
sampling**. Un servicio con poco tráfico puede tener todos sus spans
descartados de forma legítima y parecer muerto.

**Decisión**. La sonda consulta las tablas de **métricas**.

**Por qué funciona**. El pipeline del gateway deriva las métricas RED de
**todas** las trazas **antes** de muestrear — fue una decisión deliberada al
construirlo, para que las métricas no salieran sesgadas. Eso las convierte en
la única fuente que responde *"¿ha estado activo?"* sin sesgo.

**La lección general**. Muestrear es correcto para almacenar y desastroso para
preguntar *"¿existe esto?"*. Cualquier comprobación de presencia tiene que
apoyarse en una señal no muestreada, y conviene recordarlo antes de escribir la
siguiente.

**Relacionado**. El `alert-bus` no se estaba trazando a sí mismo: le faltaba el
middleware ASGI. Si la pieza que detecta incidentes es la única sin telemetría,
su propia degradación es invisible.

---

## D-032 · Los guardarraíles de agentes viven en el SDK, no en el backend

**Estado**: ✅ Vigente

**Contexto**. Un agente puede entrar en bucle, llamar a la herramienta
equivocada o alucinar, y devolver un 200 con latencia normal. El APM tradicional
no ve nada de eso.

**Decisión**. Los presupuestos **absolutos** —máximo de llamadas, coste, tokens,
repeticiones idénticas— viven en `argus-semconv`, dentro de `agent()` y
`tool()`. Los **estadísticos** —P99 de llamadas, coste sobre la mediana
histórica— viven en vmalert.

**Por qué la división**. En el SDK **se puede parar el bucle**. Detectarlo desde
el backend llega tarde: para cuando la telemetría ha viajado, el agente lleva
veinte llamadas más, y un agente descontrolado cuesta dinero cada segundo. Lo
estadístico, en cambio, necesita histórico y no puede vivir en proceso.

**Dos decisiones dentro de la decisión**:

- **Por defecto marca, no para.** Parar una ejecución es una decisión del
  producto, no de la librería de observabilidad. Un guardarraíl que corta
  producción sin que nadie lo haya pedido es peor que el bucle.
- **Sin `args`, no se detectan bucles.** Tratar las llamadas sin argumentos como
  idénticas entre sí produciría falsos positivos: un agente que llama diez veces
  a la misma herramienta con argumentos que no nos ha dicho está trabajando, no
  atascado. Y un falso positivo en algo que puede **parar producción** es mucho
  peor que un bucle no detectado.

**La señal que sí es inequívoca**: la misma herramienta con los **mismos
argumentos** varias veces. Muchas llamadas pueden ser trabajo legítimo; repetir
idéntico no lo es nunca.

---

## D-033 · El *dead man's switch* no puede compartir nada con lo que vigila

**Estado**: ✅ Vigente · verificado tirando el `alert-bus` de verdad

**Contexto**. Es el único problema que la plataforma no puede resolverse a sí
misma: si Argus cae, deja de avisar, y su silencio es indistinguible de que todo
va bien.

**Decisión**. `deadman/deadman.py`: un fichero, **cero dependencias externas**,
fuera del compose, ejecutado por launchd o cron.

**Por qué cada restricción**:
- **Sin dependencias**: si dependiera de algo que se instala, compartiría modos
  de fallo con lo que vigila. Verificado con un test que inspecciona sus
  imports.
- **Fuera del compose**: un vigilante dentro del contenedor que vigila se cae
  con él.
- **Idealmente en otra máquina**: en la misma detecta que el servicio murió; en
  otra detecta además que la máquina murió, que es cuando más falta hace.

**Avisa por todos los canales, no por el primero que funcione.** Si la
plataforma está caída no se sabe cuál sigue en pie. Duplicar un aviso es
molesto; no recibirlo es el fallo que existe para evitar.

---

## D-034 · El endpoint de alertas acepta las dos formas de Alertmanager

**Estado**: ✅ Vigente · **descubierta durante la implementación**

**Contexto**. vmalert llevaba un rato fallando al entregar sus alertas con un
**422**, y los tests pasaban.

**Causa**. vmalert manda un **array JSON pelado** `[{...}]`, que es el formato
de la **API v2** de Alertmanager. Mi endpoint esperaba `{"alerts": [...]}`, que
es el formato de **webhook**. Son dos cosas distintas con el mismo nombre.

**Decisión**. Aceptar ambas.

**La lección, que vale más que el arreglo**: los tests hablaban **mi** formato
en vez del suyo, así que pasaban mientras la integración real estaba rota. Un
test que inventa el formato del otro extremo no prueba nada sobre la
integración. Ahora hay un test con el payload literal que vmalert emite.

---

## D-035 · Los paquetes se llaman `argus-obs-*`, y el import sigue siendo `argus`

**Estado**: ✅ Vigente · **descubierta preparando el piloto**

**Contexto**. Al comprobar si una aplicación fuera del workspace podía instalar
la librería, `uv pip install argus-sdk` **funcionó** — y trajo un paquete
ajeno: `argus-sdk` 0.2.1, de otra persona, que requiere `anthropic`, `click` y
`httpx`.

**El riesgo tiene nombre: confusión de dependencias.** Si el índice privado está
caído o mal configurado, el instalador cae a PyPI y se trae código de un
desconocido **en silencio**. No es hipotético: es una clase de ataque de cadena
de suministro conocida.

**Decisión**:

| | Nombre | Por qué |
|---|---|---|
| Distribución | `argus-obs-sdk`, `argus-obs-semconv`, `argus-obs-schemas` | **No existen en PyPI**, así que un fallo del índice privado falla ruidosamente en vez de instalar a un desconocido |
| Import | `argus`, `argus_semconv`, `argus_schemas` | Ergonomía; `import argus` se escribe muchas veces |

**Sobre conservar `argus` como import**: existe un paquete `argus` en PyPI, pero
es una librería de calibración de cámaras — nada que ninguna de tus aplicaciones
vaya a instalar. El riesgo es real pero remoto, y el coste de renombrar crece
con cada aplicación conectada. Si algún día una app necesita ese paquete, se
renombra entonces.

**Verificado en una instalación limpia**, no solo en tests: `argus-obs-semconv`
trae exactamente **una** dependencia, `opentelemetry-api`. Es D-002 comprobado a
nivel de distribución.

---

## D-036 · Nada publica todavía en un índice; se instala desde etiquetas de git

**Estado**: ✅ Vigente · se revisa antes del rollout

**Contexto**. Durante el piloto las aplicaciones necesitan instalar las
librerías e iterar con ellas. ¿Publicamos prelanzamientos en TestPyPI?

**Decisión**. **No TestPyPI.** Durante el piloto se instala desde **etiquetas de
git**; antes del rollout, índice privado.

**Por qué TestPyPI es mala idea aquí, aunque parezca hecho para esto**:
- Las dependencias (`opentelemetry-api` y compañía) **no están** de forma fiable
  en TestPyPI, así que haría falta `--extra-index-url` apuntando a PyPI real —
  y eso **reintroduce exactamente la confusión de dependencias** que D-035
  acaba de eliminar.
- TestPyPI purga paquetes periódicamente. Un piloto que dura semanas no puede
  apoyarse en algo que puede desaparecer.

**Qué sí, y por qué es suficiente**:

```bash
uv pip install "argus-obs-sdk[asgi] @ git+<repo>@v1.0.0a2#subdirectory=libs/argus-sdk"
```

Da versionado real, es reproducible, funciona dentro de contenedores y **no
necesita infraestructura**.

**Dos detalles de PEP 440 que costaron un error real** (ver D-038). `scripts/release.sh` etiqueta, construye y corre las
pruebas —publicar una versión que alguien va a instalar no puede saltarse los
tests—, y usa PEP 440 (`1.0.0a1`) para que un `pip install` sin `--pre` nunca se
lleve un prelanzamiento por accidente.

**Pendiente, y conviene no olvidarlo**: los nombres `argus-obs-*` están libres
en PyPI, y de eso depende la protección de D-035. **Si alguien los registra, la
protección se invierte en vulnerabilidad.** Registrarlos defensivamente —aunque
sea con un `0.0.1` vacío— es barato y cierra esa puerta. Está en el backlog
como `B-14`.

---

## D-037 · Los dashboards son código, provisionados desde disco

**Estado**: ✅ Vigente

**Decisión**. Fuentes de datos y dashboards se provisionan desde
`platform/grafana/`, con `allowUiUpdates: false`.

**Por qué**. Un dashboard que depende de que alguien configurara una fuente de
datos hace seis meses es un dashboard que se rompe al migrar de máquina — y
migrar de máquina es un requisito explícito (D-003). Editar en la UI produce
cambios que nadie revisa y que se pierden en el siguiente despliegue.

**Dos dashboards, dos preguntas distintas**:

| Dashboard | Responde |
|---|---|
| **Una aplicación** | ¿Cómo va mi aplicación? RED, latencia, errores, tokens, coste |
| **La plataforma** | ¿Está la plataforma mirando? Descartes, colas, exportación |

El segundo existe porque **ningún otro dashboard puede responder esa pregunta**:
todos los demás asumen que la plataforma funciona. Y tiene un límite honesto: si
la plataforma cae del todo, tampoco se ve. Para eso está el *dead man's switch*,
que vive fuera (D-033).

**Sobre el diseño de los paneles**, siguiendo las reglas de visualización:
- Un valor suelto es un **tile**, no un gráfico de una barra.
- **Ningún eje doble.** Tokens y coste van en paneles separados: alinear dos
  escalas distintas inventa una correlación que no está en los datos.
- Las barras por categoría nominal usan **un solo color**: un degradado por
  valor doble-codifica la longitud en el tono y quema el único canal libre.
- La paleta de series se validó con el verificador (separación CVD ΔE 9.4,
  visión normal 26.5, contraste sobre fondo oscuro ≥3:1).

---

## D-038 · Los prelanzamientos exigen límites inferiores con prelanzamiento

**Estado**: ✅ Vigente · **descubierta al publicar la primera versión**

**Contexto**. `make release V=1.0.0a1` terminó bien —etiquetó, construyó,
pruebas en verde— y las librerías resultantes **no podían instalarse entre
ellas**.

**Causa**. PEP 440: un prelanzamiento **solo satisface un especificador que
mencione un prelanzamiento**. `argus-obs-sdk` declaraba
`argus-obs-semconv>=1.0,<2`, y `1.0.0a1` no cumple `>=1.0`. Publicamos tres
paquetes mutuamente inalcanzables.

Y el error no ayuda: *«pre-releases weren't enabled»* apunta al invocante, no a
la dependencia interna mal declarada.

**Decisión, dos partes**:

1. `release.sh` fija el límite inferior de las dependencias **internas** a la
   versión que se publica: `argus-obs-semconv>=1.0.0a2,<2`.
2. Instalar un prelanzamiento exige la **versión explícita**:
   `argus-obs-sdk[asgi]==1.0.0a2`. Es la contrapartida deseada de que un
   `pip install` normal nunca se lleve un prelanzamiento por accidente, así que
   se documenta en vez de eliminarse.

**Lo que lo detectó**: `make pilot-check`, que instala en un entorno limpio con
el comando que teclearía una persona. Las 190 pruebas no lo vieron porque todas
corren dentro del workspace, donde las dependencias se resuelven por otra vía.

**Relacionado**. La versión de los paquetes salía de una constante en el código
y se quedó en `1.0.0` mientras la distribución era `1.0.0a1`. Ahora sale de
`importlib.metadata`. En `argus_semconv` conviven dos números distintos a
propósito: `__version__` es la del paquete y `SEMCONV_VERSION` la del modelo de
convenciones, que cambia por otros motivos.

---

## D-039 · Langfuse se provisiona por variables de entorno, no por la UI

**Estado**: ✅ Vigente

**Decisión**. La organización, el proyecto y **las claves de API** de Langfuse se
provisionan con `LANGFUSE_INIT_*` desde el `.env`. Nadie hace clic por la UI
para que la plataforma funcione.

**Por qué**. Es la misma razón que con los dashboards (D-037): lo que depende de
que alguien hiciera clic hace seis meses se rompe al migrar de máquina. Y aquí
había un paso manual especialmente molesto —*«crea un proyecto, copia las
claves, codifícalas en base64 y pégalas en el .env»*— que ahora desaparece:
`ARGUS_LANGFUSE_AUTH` se calcula al generar las credenciales.

**Consecuencia**. Levantar el perfil GenAI en una máquina nueva es
`make genai`, y el Collector se autentica desde el primer arranque.

---

## D-040 · MinIO viene de quay.io, no de Docker Hub

**Estado**: ✅ Vigente · **descubierta al arrancar el perfil GenAI**

**Contexto**. El primer arranque falló con `pull access denied for minio/minio,
repository does not exist or may require 'docker login'`.

**Causa**. MinIO **retiró sus imágenes de Docker Hub**. Ninguna etiqueta existe
ya ahí, ni siquiera `latest`.

**Por qué el error confunde**. *«pull access denied … or may require docker
login»* sugiere un problema de credenciales sobre una imagen privada, no que el
repositorio entero se haya mudado. Perseguir ese mensaje lleva a intentar
autenticarse en vez de a cambiar de registro.

**Decisión**. `quay.io/minio/minio`, con versión fijada como todas las demás.

**La lección general**: fijar versiones protege de que una imagen cambie bajo
tus pies, pero **no** de que desaparezca. Un `compose` que funcionaba hace
meses puede dejar de funcionar sin que nada del repositorio cambie, y el error
no siempre dice por qué.

---

## D-025 · `alert-bus` propio, con Keep como posible consumidor aguas abajo

**Estado**: ✅ Vigente · evaluación exigida por `F2-01` antes de escribir código

**Contexto**. El roadmap marcaba evaluar [Keep](https://www.keephq.dev/) antes de
construir nada, precisamente para no reimplementar una plataforma AIOps madura
que ya hace deduplicación, correlación, enriquecimiento y workflows.

**Qué se encontró**. Keep opera a nivel de **alerta**, no de **span**. Ingiere
desde proveedores de monitorización —Datadog, Grafana, CloudWatch, PagerDuty,
Sentry— mediante integraciones bidireccionales. Usa OpenTelemetry para **su
propia** observabilidad, no como vía de ingesta.

Nuestro camino caliente necesita otra cosa: un **receptor OTLP que reciba spans
crudos** del Collector agente y decida en memoria si constituyen un incidente,
sin pasar por la base de datos. Keep no hace eso, y no es un hueco de Keep — es
que está una capa por encima.

**Decisión**. Construir `alert-bus` como receptor OTLP, y **dejar la interfaz de
salida abierta** para que Keep, PagerDuty o lo que venga puedan ser consumidores
aguas abajo.

**Por qué no Keep para la capa de correlación tampoco, por ahora**:
- Su stack son Redis + Postgres/MySQL + backend + frontend. En un portátil que
  ya corre inferencia local, eso pesa (D-017).
- La correlación depende del registro de aplicaciones propio y de su grafo
  `depende_de`. Meter Keep significaría mantener ese grafo en dos sitios.
- Añade una segunda fuente de verdad para los incidentes.

**Consecuencias**. Construimos solo la parte que Keep no cubre, y no cerramos la
puerta: los *sinks* son una interfaz, así que enchufar Keep más adelante es
escribir un adaptador, no rehacer la capa. **Reevaluar cuando el volumen de
alertas justifique una UI dedicada**, que hoy no es el caso.

---

## D-028 · El agente no exige token; el gateway sí

**Estado**: ✅ Vigente · **descubierta durante la implementación**

**Contexto**. Al mover el gateway a 14317/14318 (D-026), la prueba de humo
empezó a fallar en *"el endpoint OTLP rechaza peticiones sin token"*: ahora
apuntaba al **agente**, que no pide token.

No era un fallo, era una pregunta de diseño sin responder.

**Decisión**. Dos receptores, dos posturas:

| Receptor | Expuesto en | ¿Token? |
|---|---|---|
| Collector **agente** | `127.0.0.1` de su máquina | **No** |
| Collector **gateway** | La red privada, entre máquinas | **Sí** |

**Por qué el agente no.** Escucha solo en loopback: una aplicación que puede
hablarle ya está dentro de esa máquina. Exigirle token significaría **repartir
el secreto por quince o más repositorios** —en variables de entorno, en
ficheros de compose, en CI—, y un secreto en quince sitios es peor postura de
seguridad que no tenerlo. La frontera de confianza aquí es la máquina.

**Por qué el gateway sí.** Lo alcanzan agentes de otras máquinas, y lo que entra
alimenta las conclusiones de los agentes de IA. Aceptar señales de cualquiera es
aceptar conclusiones de cualquiera.

**Consecuencia**. La prueba de humo comprueba el token contra el gateway
(`:14318`) y envía su telemetría por el agente (`:4318`), que es el camino real
de una aplicación. Antes probaba las dos cosas contra el mismo puerto y por eso
la distinción no se veía.

---

## Decisiones aún por tomar

Se resuelven con datos, no de antemano. Están en el backlog (`roadmap.md`).

| Decisión | Cuándo resolverla |
|---|---|
| ¿Retención real de cada señal? | Medir volumen en `F1`; el disco de un portátil es finito |
| ¿Qué modelo para el agente de RCA? | Fijar el techo con uno remoto en `F4`, luego medir cuánto se pierde con uno local |
| ¿Merece la pena el agente de onboarding? | Reevaluar cuando el catálogo pase de veinte apps |

---

## D-041 · Configurar un canal son dos pasos, y la prueba verifica los dos

**Contexto**. `make channel-test` daba verde con `gchat` cargado y el aviso
saliendo únicamente por consola. El sink estaba construido y aparecía en
`/stats`, pero el registro enrutaba `argus/page` a `[console]`, así que el
motor nunca le pasó el incidente. La prueba comprobaba «sinks cargados» y
«envíos fallidos = 0» — y un canal al que nunca se intenta enviar no falla.

**Decisión**. Cargar un canal (credenciales en `.env`) y enrutar hacia él
(registro) son pasos distintos, y la verificación mide el resultado, no la
configuración: `despacho_por_canal` cuenta envíos y fallos **por sink**, y la
prueba exige que un canal humano incremente su contador durante la prueba.

**Consecuencias**.
- `Dispatcher` e `InlineDispatcher` llevan `por_canal`; `/stats` lo expone.
- El snapshot del registro incluye `canales`, para poder comprobar el
  enrutamiento desde fuera del proceso.
- El motor avisa con `notify.channel_not_loaded` cuando el registro nombra un
  canal sin credenciales: el aviso sale por los demás, pero deja rastro.
- Las entradas del registro listan varios canales a propósito; los no
  configurados se omiten. Así configurar el `.env` basta, sin segunda edición.

**Lo que no se hizo**. Que el enrutamiento cayera automáticamente a «todos los
canales cargados» cuando el registro dice `console`. Sería conveniente y
rompería D-014: a quién se avisa es una regla explícita, no una inferencia.

---

## D-042 · La URL de edición de Google Chat conserva `key` y `token`

**Contexto**. `_update` construía la URL desde el host pelado
(`self._url.split("/spaces/")[0]`), descartando la query. Pero en un webhook de
Google Chat las credenciales **viajan en la query**, no en una cabecera: el PUT
habría dado 401, la edición habría degradado a publicar en el hilo, y la
divulgación progresiva (D-015) se habría convertido en un mensaje por
actualización. Con el fallo capturado en un `except`, solo se vería mirando el
chat.

**Decisión**. La URL de edición se construye conservando `key` y `token` del
webhook original y añadiendo `updateMask=cardsV2`.

**Consecuencias**. Un test fija la forma de la URL. No se detectó con el
servidor de pruebas porque aceptaba cualquier petición: un doble permisivo
verifica que llamas, no que llamas bien.

---

## D-043 · La recarga del registro vive en el `tick`, no en cada consulta

**Contexto**. `reload_if_changed()` existía, tenía tests, y **nadie la
llamaba**. Cambiar un canal en `apps.yaml` no surtía efecto hasta reiniciar,
que es justo lo que la recarga en caliente promete evitar. El método correcto
y el cableado ausente son dos cosas distintas, y la suite solo probaba el
primero.

**Decisión**. El `_ticker` la invoca en cada vuelta. Y hay un test que fija el
**cableado**, no el método: arranca el ticker de verdad, toca el fichero y
espera el cambio.

**Consecuencias**. El registro cambia a ritmo humano, así que un `stat()` cada
`tick_s` es gratis; hacerlo en cada consulta lo pondría en el camino caliente,
donde no sobra tiempo.

---

## D-044 · Los webhooks de Google Chat son de Workspace; el canal del piloto es el correo

**Contexto**. El plan designaba Google Chat como canal principal por ser el de
menor fricción (§9.3). Al configurarlo, «Agregar webhooks» aparece en gris: los
webhooks entrantes son una función de **Google Workspace**, y la cuenta es una
`@gmail.com` personal. La sección existe en la interfaz, lo que hace que parezca
un permiso ajustable cuando no lo es.

**Decisión**. El canal del piloto es el **correo por SMTP**. Google Chat sigue
implementado y probado; se activa el día que haya un Workspace detrás.

**Consecuencias**.
- La divulgación progresiva por correo va por cabeceras (`Message-ID`,
  `In-Reply-To`, `References`) en vez de por edición de mensaje: dos correos en
  una conversación en lugar de un mensaje que se reescribe. Es peor, y es lo
  que hay sin API de edición.
- Se pierden los **botones**, así que la aprobación humana de L5 (F6-06)
  necesitará otro camino —un enlace firmado en el correo— mientras no haya
  Workspace.
- El registro lista `[gchat, email, console]`: cambiar de canal es editar el
  `.env`, sin tocar el registro.

---

## D-045 · Todo ajuste de la configuración debe llegar al contenedor, y hay un test que lo fija

**Contexto**. `ALERTBUS_SMTP_TLS` existía en `Settings`, estaba documentado, y
`compose.yaml` no lo pasaba. Ponerlo en el `.env` no hacía nada, y el síntoma
—«STARTTLS extension not supported by server»— no se parece en nada a la causa.
Había siete ajustes más en la misma situación.

**Decisión**. Un test compara los campos de `Settings` con las variables que
compose declara, con una lista explícita de los que la imagen fija a propósito
(`host`, `port`, `registry_path`).

**Consecuencias**. Añadir un ajuste sin exponerlo rompe la suite. Es la clase de
fallo que solo aparece al usar la documentación al pie de la letra, porque el
código está bien y la suite pasa.

---

## D-046 · Somos el único destino de Prometheus, y eso cambia quién responde del silencio

**Contexto**. El 13/09/2026 el equipo de Prometheus aplicó `Resource.create()`
y, por decisión propia, **retiró su pila de observabilidad entera** —Loki,
Promtail, Tempo y su Grafana—. Con ella se fue el colector por defecto: su
código tenía `http://tempo:4318` codificado como respaldo y ya no lo tiene. Sin
`OTEL_EXPORTER_OTLP_ENDPOINT`, **no exportan nada, en silencio y a propósito**.

**Decisión**. `prometheus-inference-platform` pasa a `activo`, que es lo que
enciende la sonda de silencio: 15 minutos sin métricas de un componente abren
un incidente `page`. La sonda mira **métricas y no trazas**, porque las trazas
pasan por muestreo y un servicio con poco tráfico puede tener todos sus spans
descartados legítimamente.

**Consecuencias**. Ya no hay una segunda pila que desmienta un silencio, así que
el canario deja de ser una red de seguridad y pasa a ser **la** red.

**El límite, dicho claro**: la sonda detecta que un servicio **deja** de emitir,
no que **nunca** empezó. Un endpoint mal configurado desde el arranque es
indistinguible de «no está desplegado». El primer despliegue hay que
confirmarlo mirando.

---

## D-047 · Renombrar una aplicación es una ventana, no un corte

**Contexto**. Prometheus pidió cambiar `service.namespace` de
`edge-ai-inference` a `prometheus-inference-platform` —y no a `prometheus` a
secas, porque aquí dentro conviven PromQL, `prometheusremotewrite` y
VictoriaMetrics—. La variable la pone **su** despliegue, así que entre el
acuerdo y su redespliegue llegan los dos nombres a la vez.

**Decisión**. El registro admite `alias: [...]`. Un namespace en la lista se
atiende con la identidad nueva: conserva criticidad, canales y runbook, y no
dispara el aviso de «servicio no registrado».

**Y el arreglo que hizo falta de verdad**: la señal se **normaliza a la
identidad resuelta** antes de calcular la huella. La huella se construye con la
aplicación, así que sin eso el mismo fallo del mismo servicio abría **dos**
incidentes —uno por nombre— y notificaba dos veces durante toda la ventana. La
correlación por topología tampoco habría encontrado las dependencias, que el
registro declara con el nombre nuevo.

**Consecuencias**. Un alias que choca con el id de otra aplicación se descarta
con un error en el log: perder el renombrado es mucho menos malo que dejar una
aplicación entera en la sombra. El alias es temporal por definición y se retira
cuando el nombre viejo deja de aparecer (S-04).

---

## D-048 · Un componente declarado pero sin conectar no se vigila

**Contexto**. Marcar Prometheus como `activo` habría abierto tres incidentes en
el primer minuto: `gateway`, `manager-api` y `manager-core` están en el registro
—son el plan del piloto— pero nunca han emitido.

**Decisión**. Los componentes admiten `estado`. Solo los `activo` generan sonda
de silencio.

**Consecuencias**. El registro sigue siendo el catálogo completo, incluido lo
planificado, sin que planificar cueste alertas. Alertar de que calla algo que
nunca ha hablado es como se le enseña a la guardia a ignorar al canario, y la
fatiga de alertas es el problema dominante de 2026 (§2.12 del plan).

---

## D-049 · La sonda de silencio mide crecimiento, no presencia de puntos

> **Fallo de plataforma** · descubierto 2026-09-13 · la sonda de silencio contaba puntos: un muerto parecía sano

**Contexto**. Al activar Prometheus (D-046) comprobamos la sonda contra el
estado real y dijo que `auth-service` estaba sano. Llevaba **tres horas sin
emitir una sola traza**.

La sonda contaba puntos de métrica: 1.080 en quince minutos. Todos eran la misma
serie, `traces.span.metrics.calls`, del connector `spanmetrics` — que es
**acumulativa** y por definicion reexporta su valor en cada intervalo aunque no
haya ocurrido nada. La suma llevaba congelada en 1.200 desde las 13:38.

**Un servicio muerto parecia sano, que es exactamente el fallo que esta sonda
existe para impedir.**

**Decisión**. Actividad = el contador **creció** durante la ventana, y la
fórmula depende de la temporalidad:

- **Delta** (`AggregationTemporality = 1`): cada punto es lo ocurrido en su
  intervalo; la suma sirve tal cual.
- **Acumulativa** (`= 2`): `max(total) - min(total)` sobre la ventana. Un
  reinicio pone el contador a cero y también cuenta como actividad, que es
  correcto: reiniciar es actividad.

**Consecuencias**. Verificado contra el estado real: `auth-service` da 0 y los
servicios vivos dan valores positivos. Desplegado, el canario confirmó el
silencio en el segundo ciclo y abrió un incidente `page`, que es el bucle
entero funcionando por primera vez sobre un silencio de verdad.

**Por qué la suite no lo vio**. Las pruebas devolvían `"1247"` o `"0"` desde un
ClickHouse falso: verificaban que la sonda **interpreta** la respuesta, nunca
que **pregunta lo correcto**. Ahora hay una prueba que fija la forma de la
consulta, porque el fallo estaba ahí y `count()` no puede volver.

**El límite que sigue en pie**: la sonda detecta que un servicio **deja** de
emitir, no que **nunca** empezó (D-046).

---

## D-050 · La seudonimización estaba diseñada y nunca implementada

> **Fallo de plataforma** · descubierto 2026-09-13 · seudonimización diseñada y nunca implementada

**Contexto**. Al revisar el tráfico real de Prometheus encontramos `user_id` y
`jwt.subject` —el mismo UUID de 36 caracteres— **en crudo** en ClickHouse. El
plan decía «`user.id` y `client.id` hasheados con sal» (§7.2) y el procesador
`transform/pseudonymize` solo borraba la cabecera `authorization` y `url.query`.
El nombre del procesador prometía algo que no hacía.

**Decisión**. Los identificadores de persona se **hashean** con SHA256 y una sal
del entorno; los correos se borran. Se cubren las dos grafías: la canónica de
OTel (`user.id`) y la de guiones bajos (`user_id`), que es la que trae el
tráfico real.

**Hashear y no borrar** es deliberado: el mismo usuario da siempre el mismo
hash, así que «¿le pasa a uno o a todos?» —de las primeras preguntas de
cualquier investigación— se sigue pudiendo responder sin que el almacén sepa
quién es.

**Desviación consciente del plan**: `client_id` NO se hashea. La evidencia
mandó: los valores reales son 2 distintos de 9 caracteres, o sea la aplicación
OAuth y no una persona. Hashearlo destruiría una agrupación útil sin proteger a
nadie.

**Consecuencias**. `ARGUS_PSEUDONYM_SALT` es obligatoria y el gateway no arranca
sin ella. Cambiarla reescribe todos los hashes futuros y rompe la agrupación con
lo ya almacenado: es una decisión, no un ajuste.

**La lección**: una regla de privacidad escrita solo para la grafía canónica
protege de la telemetría que escribes tú, no de la que recibes. Y un procesador
con nombre de hacer algo no prueba que lo haga — esto llevaba semanas activo en
la tubería.

---

## D-051 · El canario recarga su registro; un método sin llamar es un bug

**Contexto**. El canario derivaba sus sondas del registro **solo al arrancar**.
Al pasar `gateway` y `manager-api` a `activo` no se vigilaban, y lo peor es
cómo falla: `docker compose up -d` sin cambios **no reinicia el contenedor**,
así que editar el registro y redesplegar parece funcionar y no hace nada.

Es el mismo fallo que D-043 en otro servicio, lo que lo convierte en un patrón
y no en un descuido.

**Decisión**. El bucle compara el `mtime` del registro y de las sondas HTTP, y
reconstruye. La recarga **conserva el contador de fallos consecutivos** de los
objetivos que siguen vigilados: si se reiniciara en cada recarga, un registro
que se edita a menudo haría que ningún fallo llegara nunca a dos consecutivos y
el canario dejaría de alertar sin dejar rastro.

**Consecuencias**. Verificado en vivo además de con pruebas: tocar el fichero
produjo `canary.probes_reloaded` en el ciclo siguiente. **Las pruebas fijan el
cableado, no el método** — es la segunda vez hoy que un método correcto que
nadie invocaba pasa la suite entera.

---

## D-052 · El dato de inferencia se emite donde nace: el gateway, no el cliente

**Contexto**. Recomendamos instrumentar Axonium porque «sabe el modelo servido,
el backend, el TTFT, si hubo fallback». El equipo de Prometheus nos corrigió:
esos datos los produce **su gateway** y se los entrega a Axonium. Axonium es un
cliente SDK.

**Decisión**. El dato se emite donde nace. El gateway emite las convenciones
GenAI; Axonium instrumenta solo lo que únicamente el cliente ve — que la llamada
se intentó, la latencia de punta a punta desde el llamante, y los errores que
nunca llegan al servidor (timeout del cliente, DNS, conexión rechazada).

**Consecuencias**. No cambia el principio de apalancamiento —instrumentar una
pieza compartida en vez de quince aplicaciones—, cambia **cuál** es la pieza. Y
esta divide mejor: el gateway es de un equipo con el que ya hablamos, mientras
que Axonium está en cambio.

**La evidencia que lo cierra**: en tres horas de su tráfico, los atributos
`gen_ai.*` son cero. Su span `inference.request` lleva `model`, `client_id` y
`user_id`, pero ni tokens, ni proveedor, ni TTFT, ni motivo de finalización.
Nada de eso se reconstruye desde fuera.

---

## D-053 · El canal del piloto es Telegram, y resulta ser el mejor de los tres

**Contexto**. Google bloqueó los dos caminos: los webhooks entrantes de Chat son
una función de Workspace (D-044), y las contraseñas de aplicación siguen sin
estar disponibles en la cuenta incluso con la verificación en dos pasos activada
— «La opción de configuración que buscas no está disponible para tu cuenta».
Perseguir la tercera variante de Google habría sido perseverar en lo que ya
había fallado dos veces.

**Decisión**. Telegram. Un bot con BotFather, sin proveedor de identidad de por
medio, sin aprobación de nadie.

**Y no es solo un apaño**: es el único de los tres que permite **editar un
mensaje ya enviado**. Eso hace que la divulgación progresiva (D-015) sea UN
mensaje que se actualiza —el aviso se convierte en el informe del agente— en
vez de dos correos en un hilo, que es el techo del correo. Verificado contra un
servidor que imita la API: `sendMessage` y después `editMessageText` sobre el
mismo `message_id`.

Admite además botones, así que es el sitio natural para la aprobación humana de
remediaciones (F6-06), que con el correo habría necesitado enlaces firmados.

**Consecuencias y límites**.
- El token viaja **en la URL**, así que el sink nunca registra la URL en el log:
  solo el método y el error. Hay una prueba que lo fija.
- Se usa HTML y no `MarkdownV2`: este último exige escapar dieciocho caracteres
  —`.`, `-` y `!` incluidos— y un descuido devuelve 400 y pierde el aviso. HTML
  necesita tres.
- Límite de 4096 caracteres: un informe largo se recorta, y se dice que se
  recortó. El correo no tiene ese límite, así que los dos juntos son mejores que
  cualquiera solo: Telegram avisa, el correo guarda el informe entero.
- El bot no puede escribir a nadie que no le haya dado a **Iniciar** primero.
  Es una protección de Telegram, y es la causa más probable de que no llegue
  nada la primera vez.

**Lo que encontró probarlo**: el emoji de severidad salía **dos veces**
(`🔴 🔴 [app/componente]`). `titular()` ya lo incluye y el sink lo añadía otra
vez. El formato vive en `render.py` precisamente para que los canales no
discrepen entre sí, y un canal que se añade adornos por su cuenta rompe esa
propiedad. Hay una prueba que lo fija.

---

## D-054 · El sesgo del muestreo viaja en el span

> **Fallo de plataforma** · descubierto 2026-09-14 · concluimos sobre tráfico ajeno sin corregir nuestro propio sesgo

**Contexto**. Analizando el tráfico de Prometheus concluimos que sondeaban un
backend roto **ocho veces más a menudo** que los sanos, y se lo dijimos con
aire de dato medido. Su equipo nos corrigió con su código en la mano: el bucle
es uniforme, un sondeo cada 10 segundos por backend, sin backoff ni ramas por
resultado.

Tenían razón. Nuestro tail sampling conserva el **100 % de los spans con error**
y el **10 % del resto**, así que la proporción que leímos estaba inflada diez
veces en contra de lo que funciona. Corregido:

| | almacenado | real | por endpoint |
|---|---|---|---|
| 404 del backend roto | 190 | ~190 | 5,6/min |
| 200 de los sanos | 115 | ~1150 | 6,8/min |

Contraste independiente: el gateway emitió 1256 spans según `spanmetrics`
—derivado **antes** de muestrear— y solo 331 llegaron a `otel_traces`.

**Decisión**. Cada span lleva `argus.sampling.baseline_pct`. Y la regla: **las
tasas y las proporciones salen de métricas, nunca de la tabla de trazas**.

**Por qué importa más de lo que parece**. Un agente de RCA leyendo `otel_traces`
habría sacado exactamente nuestra conclusión, y con más aplomo que una persona.
El sesgo no era descubrible desde el dato: había que conocer la configuración
del colector. Ahora viaja con él.

**Lo incómodo, que es lo instructivo**: defendimos que la sonda de silencio
mirase métricas y no trazas «porque las trazas pasan por muestreo» (D-049), y
horas después leímos proporciones de la tabla de trazas sin corregir. Saber una
regla y aplicársela a uno mismo son dos cosas distintas.

---

## D-055 · Una rueda suelta no es un canal de distribución

**Contexto**. Ofrecimos `argus-obs-semconv` como rueda con su SHA, porque
nuestro repositorio no tiene remoto. Prometheus la rechazó: *«para una
plataforma que factura a clientes, aceptar un binario por SHA de un equipo
hermano es una decisión de cadena de suministro, no una comodidad»*. No
instalaron nada y lo dijeron explícitamente en vez de dejarlo ambiguo.

**Decisión**. Aceptado sin discusión, y `B-10` —el índice privado— sube a
prioridad alta. Deja de ser higiene y pasa a ser lo que bloquea la adopción del
paquete por el primer equipo externo.

**Consecuencias**. Mientras tanto el contrato es la **tabla de atributos**
(A-10), que ellos ya cumplen emitiéndolos a mano. Funciona, pero el
mantenimiento de los nombres se queda de su lado, que es justo lo que el
paquete existía para evitar: cuando las convenciones GenAI cambien —siguen
siendo experimentales— habrá que avisar en vez de publicar una versión.

**La lección**: un paquete con la arquitectura correcta y sin forma de
entregarlo no está terminado. La dependencia única de `opentelemetry-api` les
convenció en treinta segundos; lo que les frenó fue el `pip install` de un
fichero.

---

## D-056 · Un destino de pruebas que sobrevive al despliegue rompe el canal en silencio

> **Fallo de plataforma** · descubierto 2026-09-14 · un destino de pruebas sobrevivió al despliegue

**Contexto**. Para probar el sink de Telegram sin cuenta real añadí
`telegram_api_base` y desplegué el alert-bus apuntando a un servidor falso. Al
día siguiente el contenedor **seguía apuntando ahí**, con el servidor ya muerto:
el sink cargado, el registro enrutando, y dos notificaciones reales perdidas con
`Connection refused`. Habría roto la configuración real del canal sin que nada
lo dijera.

**Decisión**. `/stats` expone `destinos_de_prueba`, y `make channel-test` falla
si alguno apunta fuera del proveedor real.

**Consecuencias**. La comprobación es barata y cubre la clase entera: cualquier
`*_API_BASE` que no sea la de producción se denuncia. La alternativa —no añadir
la opción— habría dejado el sink sin forma de probarse, que es peor.

---

## D-057 · Todo `page` enruta al canal humano, sin excepción

> **Fallo de plataforma** · descubierto 2026-09-14 · el `page` de tres apps no llegaba a ningún canal humano

**Contexto**. Al añadir Telegram lo puse en el `page` de `argus` y, por un
reemplazo mal acotado, solo en el `ticket` de las demás. Resultado: el incidente
`page` real del gateway de Prometheus —8.000 errores de conexión— enrutó a
`[gchat, email, console]`, ninguno configurado salvo consola.

**Decisión**. Toda aplicación con canales declarados lleva el canal humano en su
lista de `page`. Un `page` que solo llega a consola no es un aviso: es un log.

**Lo que esto dice del diseño**. Separar «cargar el canal» de «enrutar hacia él»
(D-041) es correcto y hace posible este fallo. La prueba de canal lo detecta
para la aplicación que prueba —`argus`—, y **no para las demás**. Pendiente:
que `make channel-test` recorra todas las aplicaciones activas, no solo una.

---

## D-058 · Medir antes de pedir: la medición dijo que no pidiéramos

**Contexto**. Llevábamos tres intercambios preparando una solicitud para que
Prometheus adoptase `traceparent` en modo `trusted`. Prometimos medir el
beneficio antes de pedir trabajo ajeno. Medido: en 12 horas y **11.091 trazas
suyas, cero cruzan dos servicios** — su equipo ya lo sabía y nos explicó por
qué: su inferencia no hace saltos HTTP entre servicios, el gateway valida el JWT
contra un JWKS cacheado.

**Decisión**. **No pedirlo.** `trusted` habría dado trazas del sync y del panel
de administración, no de inferencia. Y el incidente de A-18 lo remata: era un
`ConnectError`, así que no hubo span de servidor que unir. El fallo cruzado más
grave de esas 12 horas es justo el que la propagación no habría iluminado.

**Lo que sí se pide en su lugar**: que el span de servidor lo abra la
instrumentación de ASGI. Dos de sus tres servicios no emiten **ningún** atributo
HTTP, así que no hay métricas RED por endpoint ni SLO por ruta. Y arregla de
paso su `OTEL_SEMCONV_STABILITY_OPT_IN`, que hoy no tiene sobre qué actuar.

**La lección**: la medición no siempre confirma la petición. Aquí la retiró, y
descubrió que dos hilos abiertos —el opt-in de convenciones y los spans sin
atributos— eran el mismo problema.

---

## D-059 · La deduplicación aguantó una tormenta real

**Contexto**. El 14/09 a las 10:58 UTC, `manager-api` de Prometheus estuvo caído
dos minutos. Su gateway reaccionó con **102 intentos de conexión por segundo**
sin espera entre reintentos —8.876 spans en dos minutos— y arrastró también a
`auth-service`, que pasó de 0 a 848 spans.

**Resultado**: 10.446 señales entraron al bus y salieron **45 notificaciones**.
99,6 % de deduplicación sobre una tormenta que no fabricamos nosotros.

**Por qué se anota**. Era el número que la fase 2 prometía y solo lo habíamos
visto con tráfico propio. Un incidente real de otro equipo es la primera
validación honesta.

**Y el límite que enseñó**: el incidente que quedó abierto al mirar no era el de
la tormenta sino uno posterior. La tormenta abrió el suyo a las 10:58, dejó de
alimentarse, y `resolve_after_s` lo cerró a los 15 minutos. Correcto, pero
significa que **`/incidents` no sirve para investigar el pasado**: hace falta
consultar los incidentes resueltos, que hoy no se exponen.

---

## D-060 · El crecimiento se mide por serie, no sobre la suma del instante

> **Fallo de plataforma** · descubierto 2026-09-15 · la sonda volvió a dar por vivo a un muerto, por otra vía

**Contexto**. Al verificar la entrega de Prometheus leímos las métricas GenAI y
concluimos que había ~500 llamadas de inferencia en la última hora mientras las
trazas no mostraban ninguna. Estuvimos a punto de avisarles de que perdíamos su
telemetría GenAI. La serie temporal lo desmintió:

```
10:20  204   10:05  204   09:20  408  ←   08:35  204
```

**Congelada en 204 durante tres horas.** El 408 de las 09:20 es el exportador
escribiendo **el mismo punto dos veces** —misma serie, mismo valor, mismo
`TimeUnix`—. Nuestra fórmula sumaba todas las series de un instante y restaba
`max − min`, así que leyó la duplicación como 204 llamadas nuevas.

**Decisión**. El crecimiento se calcula **por serie**, deduplicando puntos
idénticos con `max` antes de comparar, y se suma **después** de restar:

```sql
sum(crecimiento) FROM (
  SELECT Attributes, max(v) - min(v) AS crecimiento FROM (
    SELECT Attributes, TimeUnix, max(toFloat64(Value)) AS v   -- dedup
    ... GROUP BY Attributes, TimeUnix)
  GROUP BY Attributes)
```

Sobre la misma ventana real: fórmula vieja **204**, nueva **0**.

**Por qué importa**. Esa fórmula es la de la sonda de silencio. Un servicio
muerto con una sola duplicación en la ventana habría parecido vivo — el fallo
exacto que D-049 arregló, **reintroducido por el arreglo de D-049**.

**La lección, que ya va por la tercera forma**: en una serie acumulativa, que un
valor exista no significa que haya pasado algo. Lo escribimos hace dos días y
nos volvió a morder, esta vez porque la duplicación de un punto es
indistinguible de actividad si agregas en el orden equivocado. `sum` antes de
`max−min` y después de `max−min` no son la misma operación.

**Cómo se encontró**: mirando la serie temporal antes de mandar la acusación, no
antes de escribir el código. La suite seguía en verde.

---

## D-061 · Dos tiempos de primer token, porque son dos preguntas

**Contexto**. Pedimos a Prometheus una distribución de `ttft_ms` y nos
respondieron que llegaría casi vacía: **13 de 247 peticiones**. Su `ttft_ms` se
fija con el primer token *visible*, y los modelos que razonan emiten decenas de
chunks de razonamiento antes. Medido por ellos: un stream de `qwen3-0.6b` con
`max_tokens=32` emite **30 chunks de razonamiento, 1 de contenido**. Ningún
`qwen3` produce jamás el atributo.

Ofrecieron tres opciones y recomendaron la tercera. Aceptada.

**Decisión**. Dos atributos, porque son dos preguntas y las dos son legítimas:

| atributo | qué mide | para qué |
|---|---|---|
| `argus.ttft_ms` | primer token **visible** | experiencia: cuánto tarda alguien en ver algo |
| `argus.first_token_ms` | primer token de **cualquier** tipo | salud del backend: cuánto tarda el modelo en empezar |

**Y el histograma de latencia de inferencia pasa a alimentarse de
`first_token_ms`**, con `ttft_ms` como respaldo. El motivo no es preferencia: un
histograma construido sobre el 5 % de las peticiones —y ese 5 % elegido por
cuánto razona el modelo, no por nosotros— es una submuestra sesgada que invita
a conclusiones falsas. Un número que siempre está vale más que uno mejor que
casi nunca aparece.

**Lo que no se hizo**: redefinir `ttft_ms`. Habría movido en silencio todo el
histórico que ya cuelga de él, que es exactamente el cambio-por-efecto-secundario
que venimos evitando.

---

## D-062 · El nombre del span sigue la convención; la agregación, el modelo servido

**Contexto**. Prometheus descubrió que `gen_ai.response.model` **no puede
diferir nunca** de `gen_ai.request.model` en su gateway: los dos salen de la
misma variable ya resuelta. El caso que dijimos que «explica la mitad de los
incidentes de inferencia» era invisible por construcción — y sin su aviso
habríamos mirado 30 minutos de datos y concluido que no ocurre.

Al arreglarlo, el nombre del span pasa a ser `{operación} {request.model}`, así
que un cliente que use un alias produce un nombre distinto para el mismo modelo.
Nos ofrecieron apartarse de la convención para mantenernos la cardinalidad
estable.

**Decisión**. **Que sigan la convención.** La cardinalidad es nuestro problema,
no suyo, y se resuelve de nuestro lado: `gen_ai.response.model` pasa a ser
dimensión de las métricas RED, así que agregamos por el modelo **servido** —eje
estable— mientras el nombre del span dice qué **pidió** el cliente.

**Por qué importa el principio**. Pedir a un equipo que se aparte de un estándar
para ahorrarnos trabajo de agregación cambia un coste nuestro, acotado y
resoluble, por una deuda suya, permanente y que afecta a cualquier otra
herramienta que lea su telemetría. Si la cardinalidad de alias se dispara, el
arreglo sigue siendo nuestro: normalizar en el colector.

**Efecto lateral útil**: cuando empiecen a aparecer pares que difieren, la lista
de alias que los producen les dice **qué alias siguen vivos en clientes reales**,
que es información que hoy no tienen.

---

## D-063 · Un proceso girando en vacío 62 horas, y la plataforma no lo vio

> **Fallo de plataforma** · descubierto 2026-09-15 · `hostmetrics` mide la VM de Docker, no el Mac

**Contexto**. El usuario preguntó por un proceso de fondo que llevaba 62 horas.
Resultó ser un `python3 -` lanzado desde un heredoc, **al 98,7 % de un núcleo**,
en la misma Mac que corre la inferencia local y todo Argus. El trabajo que iba a
hacer estaba terminado y commiteado desde tres días antes. Matarlo bajó la carga
de 4,24 a 3,90.

**Lo grave no es el proceso: es que no lo detectáramos.** Tenemos 371.712 puntos
de `system.cpu.time` cubriendo justo esa ventana.

**Y la razón por la que ninguna regla habría servido**: el `hostmetrics` corre
**dentro del contenedor del agente**, que no monta `/proc` del host ni usa
`pid: host`. En macOS eso significa que mide la **VM Linux de Docker Desktop**,
no el Mac — comprobado: **1,7 % de CPU** mientras el Mac tenía un núcleo al
98,7 %.

En macOS no tiene arreglo montando nada: la VM es una frontera real. El único
sitio desde el que se ve el Mac es un proceso **nativo** en el Mac.

**Decisión**. Tres cosas, y la última es la que importa:

1. Reglas de saturación (`platform/rules/capacidad.yaml`) para la VM de
   contenedores, que **sí** vale la pena vigilar: ahí viven ClickHouse y el
   Collector.
2. El fichero y el runbook dicen **explícitamente lo que no ven**, para que un
   silencio no se lea como salud.
3. **B-15**: métricas del host real por un proceso nativo. El *dead man's
   switch* es el candidato natural —ya está escrito para correr fuera de los
   contenedores y sin dependencias—, pero hoy **ni siquiera está instalado**
   como servicio.

**Corrección al plan**: §5.3 dice que `hostmetrics` cubre «CPU, memoria, disco,
red del host» en el nivel cero-código de macOS. Es falso tal y como está
desplegado.

---

## D-064 · vmalert no podía entregar ni una alerta, y el síntoma era el silencio

> **Fallo de plataforma** · descubierto 2026-09-15 · vmalert no podía entregar ni una alerta

**Contexto**. Investigando lo anterior encontramos en el log de vmalert errores
`401` del alert-bus: *«token invalido o ausente»*. `vmalert` no llevaba ninguna
configuración de autenticación y el endpoint la exige.

**Todo el camino templado estaba muerto**: burn-rate, bandas de anomalía y las
reglas de capacidad recién escritas evaluaban, disparaban, y la notificación se
perdía en un 401 visible solo en el log de vmalert.

**Lo que hizo difícil verlo**: `grep 401` sobre las últimas 24 horas daba
**cero**. No porque funcionara, sino porque **no había disparado ninguna alerta**.
El fallo solo es visible cuando algo va mal, que es exactamente cuando ya es
tarde.

**Decisión**. `--notifier.bearerTokenFile`, con el token en un fichero montado y
fuera del repositorio. Verificado con una regla temporal que dispara siempre:
llegó al bus, 3 señales, 0 errores.

**Y de paso**: `--configCheckInterval=30s`. Sin él, añadir un fichero de reglas
exige `up -d --force-recreate` — un `restart` no basta, y eso hace que un cambio
parezca aplicado sin estarlo. Es la tercera vez esta semana que un cambio en un
fichero montado no llega al proceso.

---

## D-065 · La severidad que pide una regla se respeta, acotada por la criticidad

> **Fallo de plataforma** · descubierto 2026-09-15 · toda regla del camino templado salía como `page`

**Contexto**. La alerta de prueba llegó como `page` pese a que la regla decía
`severity: ticket`. `signals_from_alertmanager` **nunca leía esa etiqueta**: la
severidad salía de una tabla por tipo de señal, y `BURN_RATE` → `page`.

O sea que **toda regla del camino templado despertaba a alguien**, dijera lo que
dijera. Con la fatiga de alertas como el problema dominante de 2026 (§2.12), una
plataforma que convierte todo en `page` se silencia sola en semanas.

**Decisión**. `Signal` lleva una `severity` opcional. Cuando la regla la declara,
manda: su autor conoce su urgencia mejor que una tabla por tipo. La criticidad
de la aplicación **la sigue acotando**, así que es una petición y no una orden —
un laboratorio no despierta a nadie ni pidiéndolo.

Un valor mal escrito no rompe la ingesta: se avisa y se cae al comportamiento
por tipo.

**Cómo se encontró**: verificando que una alerta de prueba llegaba. Llegó, y
llegó mal. Comprobar la entrega y no solo la ausencia de error es lo que enseñó
la diferencia.

---

## D-066 · El token del gateway estaba versionado

> **Fallo de plataforma** · descubierto 2026-09-15 · el token del gateway estaba versionado

**Contexto**. Al comprobar que el nuevo fichero de secreto de vmalert no se
colaba al repositorio, la misma comprobación encontró otra cosa:
`platform/.env.agent` con el `ARGUS_GATEWAY_TOKEN` real **estaba rastreado en
git**, y lo estaba desde el commit `78918b9`.

El `.gitignore` cubría `.env` y no `.env.agent`. Un patrón que cubre el fichero
que imaginaste y no la familia a la que pertenece.

**Decisión**. Fuera del seguimiento, `.gitignore` pasa a `.env.*` con excepción
explícita para las plantillas, y se versiona `platform/.env.agent.example` con
el porqué escrito dentro.

**Sobre rotar**. El token sigue en el historial. El alcance está acotado —el
repositorio no tiene remoto— y, lo que más importa para decidir: **ningún equipo
externo lo necesita.** El receptor OTLP del agente no exige autenticación, así
que las aplicaciones —las de Prometheus incluidas— exportan sin token; la
frontera la pone que el puerto solo escuche en `127.0.0.1`. El token solo viaja
agente→gateway y hacia la API del alert-bus.

Así que rotar es una operación **interna y sin coordinación**. Queda en
`make rotate-token`, y la decisión de cuándo es del dueño del entorno: es su
credencial y reinicia su stack.

**La lección**: la comprobación que encontró esto —«¿puede este secreto llegar
al repositorio?»— se escribió para el fichero que acababa de crear. Encontró uno
que llevaba días. Vale la pena correrla sobre todo, no sobre lo último que
tocaste.

---

## D-067 · El contrato que se manda fuera se escribe a mano, y eso anula el generador

> **Fallo de plataforma** · descubierto 2026-09-15 · mandamos fuera nombres de atributo que no existen

**Contexto**. Al verificar la ventana de tráfico de Prometheus consultamos
`argus.ttft_ms` y `argus.first_token_ms` y salió **cero** en 689 spans. Estuvimos
a punto de escribirles que su arreglo no había llegado. Volvimos a consultar con
los nombres que les habíamos **pedido** y estaban los 689.

Nosotros les dimos `argus.inference.backend_id`, `argus.inference.ttft_ms` y
`argus.inference.first_token_ms`. Nuestro modelo de convenciones define
`argus.backend.id`, `argus.ttft_ms` y `argus.first_token_ms`.

**Ellos implementaron fielmente lo que les pusimos en una tabla.** El error es
entero nuestro.

**Lo que lo hace grave**: existe `libs/semconv-model/argus.yaml` como fuente de
verdad, con un generador que produce las constantes de cada lenguaje y un test
de CI que falla si lo generado no coincide con lo commiteado. Todo ese aparato
existe para que dos lenguajes no emitan el mismo atributo con nombres distintos.

Y luego escribimos el contrato **a mano en un documento**, sin comprobarlo contra
el modelo. **El generador no sirve de nada si el contrato que mandas fuera se
escribe en otro sitio.**

**Decisión propuesta a Prometheus (A-25)**: adaptarnos nosotros. Sus nombres son
mejores —`argus.inference.*` dice de qué dominio es el atributo, el nuestro lo
dejaba suelto en la raíz— y el coste de cambiar es asimétrico: para ellos es un
redespliegue, para nosotros un renombrado en un paquete que aún no usa nadie más.

Pendiente de su respuesta antes de tocar nada, para que sea un cambio y no dos.

**Lo que falta por construir**: que un documento que declare atributos se valide
contra el modelo. Hoy nada impide escribir un nombre inventado en una tabla de
Markdown y mandárselo a otro equipo. **B-18**.

---

## D-068 · Dos contadores independientes con el mismo número

**Contexto**. Prometheus mandó 30 minutos de tráfico y contó **en su generador**
qué salió; nosotros contamos **en nuestro almacén** qué llegó. Deliberadamente
separado: en P-20 nos habían dado cifras de su receptor local como si pudiéramos
verificarlas, y perdimos media hora persiguiendo spans que nunca cruzaron.

| | su generador | nuestro almacén |
|---|---|---|
| peticiones / spans | 689 | **689** |
| `qwen3-0.6b` | 248 | **248** |
| `qwen3-8b-q6` | 227 | **227** |
| `gpt-oss-20b-mxfp4` | 214 | **214** |
| abandonos / `client_disconnected` | 125 | **125** |

**Es la mejor prueba que hemos tenido de que la tubería no pierde nada.** No es
un test nuestro comprobando nuestro código: son dos contadores independientes, a
cada lado de la frontera, coincidiendo al dedillo.

Confirma además que `genai-always` conserva el 100 % de los spans GenAI: con
muestreo probabilístico habríamos visto ~70 de 689.

**La práctica que lo hizo posible, y que adoptamos**: cada medición dice **dónde
se tomó**. «Medido en nuestro generador» y «medido en vuestro almacén» son
afirmaciones distintas, y escribirlas igual fue lo que costó la media hora.

---

## D-069 · Un criterio de cierre que no se puede comprobar no es un criterio

**Contexto**. `docs/piloto.md` decía que el piloto se cierra «cuando lleve un par
de semanas sin sorpresas». Al preguntarse si ya se podía cerrar, esa frase no
respondía nada: ¿qué es una sorpresa? ¿cuenta un fallo nuestro? ¿y uno que
encontró el otro equipo?

Un criterio así se cumple el día que alguien tiene prisa.

**Decisión**. Ocho criterios comprobables, cada uno con cómo se comprueba. Cuatro
están demostrados con datos; cuatro no:

- **5 · Una traza cruzando una frontera que no es HTTP.** El plan la llamaba *la
  prueba que define la fase*. Los tres servicios del piloto son APIs HTTP, así
  que sigue sin ejercitarse con tráfico real.
- **6 · Un segundo host.** D-003 —la topología portátil, el motivo de que las
  apps exporten siempre a `localhost`— no se ha validado nunca.
- **7 · Red de seguridad externa.** El *dead man's switch* está escrito, sin
  dependencias y pensado para correr fuera de los contenedores. **No está
  instalado.** Hoy, si la plataforma cae, su silencio es indistinguible de que
  todo va bien — que es exactamente lo que ese fichero existe para impedir.
- **8 · Catorce días sin un fallo nuevo de la plataforma.**

**Sobre el octavo**, que es el que decide. Entre el 13 y el 15 de septiembre el
piloto destapó, **solo de nuestro lado**: la seudonimización diseñada y nunca
implementada (D-050), la sonda de silencio dando por vivo a un muerto dos veces
por causas distintas (D-049, D-060), el camino templado incapaz de entregar una
sola alerta (D-064), toda regla convertida en `page` (D-065), y el token del
gateway versionado (D-066).

**Eso no es un piloto que va mal: es un piloto haciendo su trabajo.** Pero
cerrarlo mientras encuentra a ese ritmo sería declarar terminada una plataforma
cuyo camino templado estaba muerto esta misma mañana.

**Lo que el piloto SÍ ha demostrado**, y conviene no minimizarlo: tres servicios
reales emitiendo con identidad correcta, 689 de 689 spans GenAI contrastados
contra un contador independiente del otro equipo, una tormenta real de 10.446
señales colapsada en 45 notificaciones, silencio real detectado y confirmado, y
un incidente real de la aplicación piloto entregado en el móvil con divulgación
progresiva.

---

## D-070 · El dead man's switch vive fuera del repositorio, y sabe hablar Telegram

> **Fallo de plataforma** · descubierto 2026-09-16 · el dead man's switch no sabía avisar y launchd no podía ejecutarlo

**Contexto**. El criterio 7 del piloto (D-069) pedía red de seguridad externa.
El fichero existía desde F2-10 —sin dependencias, pensado para correr fuera de
los contenedores— y **nunca se había instalado**. Al ir a hacerlo salieron dos
cosas que lo habrían dejado inútil:

**1 · No sabía hablar por el único canal configurado.** Soportaba WhatsApp,
correo y Google Chat. El canal del piloto es Telegram (D-053). Un vigilante
instalado que detecta la caída y avisa a nadie es peor que no tenerlo: ocupa el
sitio de la red de seguridad sin serlo.

**2 · Instalado en el repositorio, `launchd` no puede ejecutarlo.** macOS no le
da acceso a `~/Documents` sin *acceso total al disco*:

```
Operation not permitted
```

Y se veía en `launchctl list`: código de salida **2** en cada ciclo.

**Decisión**. Telegram añadido —quince líneas, solo `urllib`, sin `parse_mode`
porque un HTML mal formado daría 400 justo cuando el aviso importa—, y la
instalación copia a `~/Library/Application Support/argus-deadman/`.

**Sacarlo del repositorio es mejor que el permiso, por dos razones**: conceder
acceso total al disco a un intérprete genérico es peor que el problema que
resuelve; y el vigilante **no debe depender de que el repositorio siga donde
está**. Si se mueve el proyecto o se hace `git clean`, el centinela sigue en pie.

**Consecuencia que hay que recordar**: la copia instalada no se actualiza sola.
Cambiar la configuración exige `make deadman-setup` otra vez, y el target lo
dice al terminar.

**Verificado de punta a punta, no solo instalado**: los cuatro objetivos en
verde, parada real del `alert-bus` → detectado en el ciclo 1 sin avisar,
confirmado y avisado en el 2, y aviso de recuperación al volver. `launchctl
list` da **0**.

**Se añadió `victoriametrics` a los objetivos**: sin él no hay camino templado,
y es la pieza que más recientemente estuvo rota sin que nadie lo supiera (D-064).

---

## D-071 · El reloj de los catorce días lo reinicia cada fallo, y se mide solo

**Contexto**. «¿Cómo va el monitoreo de dos semanas?» no tenía respuesta: había
que leer el historial de git y decidir a ojo qué contaba como fallo. Eso es el
mismo defecto que D-069 arregló en el criterio, reaparecido en la medición.

**Decisión**. Las decisiones que registran un fallo **nuestro** llevan una marca
legible por máquina justo bajo el título:

```
> **Fallo de plataforma** · descubierto 2026-09-16 · qué fue
```

`make pilot-status` cuenta los días desde el más reciente y comprueba
mecánicamente los criterios que se pueden comprobar. Los que no —el 4, el 5 y el
6— salen como `?` en vez de como verdes, porque un criterio que nadie verifica y
sale en verde es peor que uno que sale en amarillo.

**Marcar un fallo nuevo reinicia el reloj a cero.** No es una penalización: es la
definición. Si la plataforma sigue descubriendo que no hacía lo que decía, no
está lista, por muchos días que lleve encendida.

**Estado al escribir esto**: 12 fallos registrados, el último **hoy mismo** —el
dead man's switch que no sabía avisar—. El reloj lleva cinco horas.

**Lo que hace honesta a esta métrica**: quien la reinicia es quien encuentra el
fallo, y hasta ahora ese hemos sido nosotros en los doce casos. La tentación de
no marcar uno para no perder la racha es real, y por eso la marca va **en la
decisión**, donde ya hay que escribir lo que pasó, y no en un contador aparte
que se pueda olvidar.

---

## D-072 · Un relleno nuestro no puede pisar `OTEL_RESOURCE_ATTRIBUTES`

> **Fallo de plataforma** · descubierto 2026-09-16 · nuestro SDK ignoraba la variable estándar, el mismo fallo que pedimos arreglar fuera

**Contexto**. Montando la reproducción de una cola para el criterio 5, puse
`OTEL_RESOURCE_ATTRIBUTES=service.namespace=prueba-cola,argus.component.role=worker`
y las trazas llegaron con `service.namespace=cola-api` y `role=api`. La variable
no hacía nada.

**Es el mismo fallo por el que escribimos una solicitud, un parche y tests al
equipo de Prometheus** (`docs/solicitudes/prometheus-resource-create.md`). Allí
la causa era el constructor directo de `Resource`; aquí usamos `Resource.create()`
—precisamente para evitarlo— y el síntoma es idéntico.

**La causa**. `Resource.create()` fusiona, y lo que se le pasa **gana**. Eso es
correcto para lo que alguien eligió, y es un fallo para lo que rellenamos
nosotros: `namespace` cae a `service` cuando nadie lo dice, y `role` a `"api"`.
Esos rellenos se pasaban igual que un valor elegido, así que pisaban la variable.

**Un valor por defecto disfrazado de elección.**

**Decisión**. `Config` recuerda qué atributos salieron de una elección real —un
argumento o una variable `ARGUS_*`— y `build_resource` retira de lo que pasa a
`Resource.create()` los rellenos que el entorno ya declara. Resultado:

| | gana |
|---|---|
| `ARGUS_NAMESPACE=x` + `OTEL_RESOURCE_ATTRIBUTES=service.namespace=y` | `x` — lo elegido manda |
| solo `OTEL_RESOURCE_ATTRIBUTES=service.namespace=y` | `y` — el relleno cede |
| ninguno de los dos | `service.name` — como siempre |

**Por qué importa más de lo que parece**. El caso *a* de la guía de migración
—«la app ya tiene OTel: coste cero, solo variables de entorno»— **no funcionaba**
para ninguna aplicación que además llamara a `argus.init()`. Su
`OTEL_RESOURCE_ATTRIBUTES` se ignoraba en silencio y el servicio aparecía bajo
un namespace equivocado, que es exactamente el síntoma que llevó a `unregistered`
en el piloto de Prometheus.

**Cómo apareció**: no en la suite, sino al usar la plataforma como la usaría
alguien de fuera. Había dos tests del Resource y ninguno probaba la interacción
con la variable estándar, porque siempre le pasábamos la configuración por
`ARGUS_*`.

---

## D-073 · El criterio 5 está demostrado: una traza cruza la cola

**Contexto**. El criterio 5 —una traza cruzando una frontera que no es HTTP— era
el único de los ocho que además era un riesgo: el plan lo llama *la prueba que
define la fase*, y si la propagación fuera de HTTP no funciona, nada de lo
construido encima sirve.

Los tres servicios del piloto son APIs HTTP, así que no lo ejercitaban. Del
portafolio, **`video-translator` (el proyecto se llama Prosodia) es la única
aplicación con cola**: `celery[redis]>=5.4.0`, con la API encolando en
`projects.py:324` y el worker consumiendo en `run_project.py:63`.

**Y Redis no basta.** Prometheus también usa Redis, pero solo como caché —`get`,
`set`, `expire`, `sadd`, `incr`— y **cero** operaciones de cola. Un `get` no
cruza a otro proceso: el span se queda en la misma traza. Hace falta alguien que
*recoja* el trabajo al otro lado.

**Decisión**. Antes de pedirle nada a su equipo, reproducir su forma exacta
—FastAPI que hace `.delay()`, worker Celery sobre Redis— y medirlo contra el
stack real. Resultado:

```
TraceId 5a3f479783d2515ab293302f262d0d59
  cola-api     POST /proyectos/p-caliente/ejecutar   (raíz)
  cola-api     apply_async/prueba.trabajo_largo
  cola-worker  run/prueba.trabajo_largo
  cola-worker  doblaje.procesar
```

**Una traza, dos procesos, a través de una cola de Redis.** `argus.init()`
activa `CeleryInstrumentor` por detección automática, así que no hace falta
escribir propagación a mano.

**Un detalle que costó encontrarlo**: con tres peticiones normales la traza caía
en el 10 % probabilístico del tail sampling y la pregunta se volvía
incontestable por falta de muestra. Marcar el span con `argus.hot` la conserva
entera. Conviene recordarlo al verificar cualquier cosa de bajo volumen.

---

## D-074 · Un canal por equipo, no una carta por tema

**Contexto**. La coordinación con Prometheus empezó como cartas: un documento
nuestro, una respuesta suya, otro documento. Su equipo propuso sustituirlo por
un **fichero compartido con dos escritores**, y en cuatro días resolvió lo que
por cartas habría llevado semanas: 27 entradas de ellos, 25 nuestras, con
estados explícitos y sin un solo «¿esto ya está hecho?».

**Decisión**. Mismo formato para Prosodia, en
`~/Documents/Victor/prosodia_argus/`. La solicitud suelta se traslada al canal
como entradas `A-01` a `A-06` y queda en `docs/solicitudes/` solo como
referencia, con un aviso de que las respuestas van al canal.

**Lo que hace que funcione**, y que conviene no perder al replicarlo:

- **Solo el dueño de una entrada la cierra.** Quien la abrió decide cuándo está
  satisfecha, así que nadie se marca deberes a sí mismo.
- **No se edita el texto ajeno**, solo se añade debajo y se cambia el estado.
- **Un id por entrada, que no se reutiliza.** Permite citar «responde a A-03»
  meses después.
- **Si algo se verificó, decir cómo y dónde.** Añadimos el «dónde» por
  experiencia: en el canal de Prometheus les dimos cifras de su receptor local
  como si pudiéramos verificarlas, y perdimos media hora persiguiendo spans que
  nunca cruzaron.

**Consecuencia para nosotros**: una copia del canal vive en el repositorio
(`docs/solicitudes/`) para que el historial de decisiones no dependa de una
carpeta fuera de git. Es copia, no fuente: la que se edita es la de Victor.

---

## D-075 · El índice de paquetes es estático, y pip verifica lo que uv no

**Contexto**. Dos equipos bloqueados en lo mismo. Prometheus rechazó instalar una
rueda suelta —*«aceptar un binario por SHA de un equipo hermano es una decisión
de cadena de suministro, no una comodidad»*— y Prosodia marcó su tarea `RM-41`
como **bloqueada** por la misma razón. `B-10` dejó de ser higiene y pasó a ser el
camino crítico del rollout.

**Decisión: un índice PEP 503 estático**, no `devpi` como decía el plan.

- El plano central tiene que ser portátil (D-003). Un índice estático son
  ficheros en un volumen: se mueve con `cp -r`, sin estado que migrar.
- `devpi` aporta subida por `twine`, réplica de PyPI y varios índices. Nada de
  eso hace falta para tres paquetes y dos consumidores, y cada pieza que no hace
  falta es una que hay que actualizar.
- Si algún día hace falta, `devpi` entra **detrás de la misma URL** y nadie de
  fuera se entera. La decisión no se cierra, se aplaza.

**Lo que se aprendió probándolo, y que cambia lo que se les puede prometer:**

**1 · `--extra-index-url`, nunca `--index-url`.** El primer intento con
`--index-url` falló: nuestro índice no replica PyPI, así que sustituirlo deja
sin resolver `opentelemetry-*`, `fastapi` y `celery`. Obvio al verlo y no antes.

**2 · pip verifica el `#sha256=`; uv NO lo hace por defecto.** Esto casi se
convierte en una afirmación falsa mandada a otro equipo. Probado sustituyendo la
rueda del índice por otra reconstruida —mismo nombre, mismos metadatos, bytes
distintos—:

| | resultado |
|---|---|
| `pip install` | **rechaza**: `THESE PACKAGES DO NOT MATCH THE HASHES` |
| `uv pip install` | **instala** sin decir nada |

Dos intentos previos de corromper el fichero no probaban nada: añadir bytes lo
rompe como ZIP y sustituirlo por otro paquete lo rompe en los metadatos. Las dos
veces la herramienta lo rechazó **por otro motivo**, y eso habría pasado por
verificación de integridad sin serlo.

**Mitigación para quien use uv**: `uv pip compile --generate-hashes` y luego
`--require-hashes`. Verificado: 6 hashes en el lockfile, instalación correcta.

**Lo que sigue abierto**: `--extra-index-url` deja la puerta a la confusión de
dependencias —si alguien registra `argus-obs-sdk` en PyPI, pip podría preferirlo—.
La mitigación real es `B-14`: registrar los nombres defensivamente. Sube de
prioridad ahora que hay consumidores externos.

**Cómo se encontró todo esto**: instalando desde el índice en un entorno limpio,
como lo haría alguien de fuera. Ninguno de los tres hallazgos sale de leer la
especificación de PEP 503.

---

## D-076 · Publicar en PyPI, no montar un índice privado

**Contexto**. `B-10` llevaba días como «índice PyPI privado», y lo construimos
(D-075). Funcionaba. Pero al escribirle a los dos equipos cómo usarlo, la
explicación necesitaba tres advertencias —`--extra-index-url` y no
`--index-url`, pip verifica el hash pero uv no, y la confusión de dependencias
sigue abierta— y eso es la señal de que la solución no era la buena.

**Decisión**. PyPI y TestPyPI, por *trusted publishing* desde GitHub Actions.

**Lo que resuelve de golpe, y que el índice privado no:**

| | índice privado | PyPI |
|---|---|---|
| Instalación | `--extra-index-url` + avisos | `pip install argus-obs-sdk` |
| Integridad | pip sí, uv no | ambos, siempre |
| Confusión de dependencias | abierta (`B-14`) | **cerrada**: los nombres son nuestros |
| Auditoría del código | ninguna | `sdist` publicado |
| Procedencia | ninguna | OIDC: verificable sin confiar en nosotros |

**El punto que de verdad importaba** no era la comodidad de instalar: era la
objeción de Prometheus, *«aceptar un binario por SHA de un equipo hermano es una
decisión de cadena de suministro»*. El índice privado la esquivaba; el *trusted
publishing* la responde — **no existe ningún token**, PyPI verifica la identidad
del workflow y cualquiera puede comprobar de qué repositorio salió el artefacto.

**Consecuencias.**
- `B-14` (registrar los nombres defensivamente) se cierra por publicar: ya son
  nuestros en los dos índices.
- El índice estático se queda, pero deja de ser el camino recomendado: sirve
  para probar una versión sin quemar un número.
- Un entorno de GitHub **por paquete** (`pypi-argus-obs-sdk`, etc.). PyPI exige
  que un `pending publisher` sea único por (owner, repo, workflow, entorno), así
  que con un solo entorno el primer paquete se registra y los otros dos no
  pueden. Efecto lateral bueno: cada trabajo sube una sola carpeta y ninguno
  puede publicar un paquete que no le toca.

**Por qué `1.0.0a3` y no `a2`**: la rueda que circuló entre los equipos tiene un
`sha256` distinto del que se construye ahora —se le añadieron autor, URLs y
clasificadores—. En PyPI las versiones son **inmutables**, así que publicar `a2`
habría dejado dos artefactos distintos reclamando el mismo número para siempre.
Quemar un número es barato; esa ambigüedad se descubre en el peor momento.

Que Prometheus declinara instalar la rueda **y lo dijera explícitamente** evitó
el problema. Si la hubieran instalado en silencio, hoy tendrían un `a2` que no
coincide con el `a2` público.

---

## D-077 · El núcleo del SDK no depende de protobuf

> **Fallo de plataforma** · descubierto 2026-09-19 · nuestro SDK no se podía instalar en una app real por un conflicto de protobuf que no habíamos mirado

Prosodia no pudo declarar `argus-obs-sdk` como dependencia. No es una
preferencia suya: `uv lock` **falla**, con «your project's requirements are
unsatisfiable».

```
argus-obs-sdk[celery] → opentelemetry-exporter-otlp-proto-grpc
                      → opentelemetry-proto → protobuf >=5.0,<8.0

indextts (motor de doblaje) → descript-audiotools → protobuf >=3.9.2,<3.20
```

Los rangos son disjuntos. No hay versión que satisfaga a los dos.

**Las dos salidas que parecían obvias no lo son.** Comprobadas contra los
metadatos reales de PyPI, no supuestas:

| Salida | Por qué no |
|---|---|
| Cambiar gRPC por HTTP | `opentelemetry-exporter-otlp-proto-http` depende del **mismo** `opentelemetry-proto`. El límite es del formato, no del transporte. Lo dijo Prosodia antes que nosotros |
| «Soportar protobuf 3.x» | Exigiría `opentelemetry-proto<1.28`, o sea OTel ≤1.27 (septiembre de 2024), con un solapamiento de un hilo en `protobuf==3.19.x`. Es anclar toda la pila dos años atrás para siempre |

**La decisión: el nucleo no lleva ningún exportador OTLP oficial.** Pasan a
extras `[grpc]` y `[http]`, y el SDK trae **su propio exportador OTLP/HTTP con
codificación JSON**, escrito sobre la biblioteca estándar.

Se puede porque **`opentelemetry-sdk` no depende de protobuf** —solo de la API,
las semconv y `typing-extensions`—, y porque OTLP define JSON sobre HTTP como
transporte de primera clase. Son unos cientos de líneas de `json` y `urllib`.

El transporte se elige por lo que esté instalado: gRPC si está, HTTP/protobuf si
está, y JSON si no. **Quien ya tenía el extra no cambia de comportamiento.** El
puerto por defecto sigue al transporte (4317 gRPC, 4318 HTTP): heredar el de
gRPC al caer a JSON sería degradar a un exportador que apunta a un puerto que no
contesta, y eso no da error, da silencio.

**Verificado de punta a punta**, no comprobado de forma. En un venv limpio con
`protobuf<3.20`, contra el Collector real y consultando ClickHouse:

| | resultado |
|---|---|
| Resolución | `protobuf 3.19.6` con OTel 1.44.0, sin `opentelemetry-proto` ni `grpcio` |
| Trazas | 3 spans, jerarquía padre-hijo, `StatusCode=Error` con su mensaje, evento `exception` |
| Atributos | `int`, `float`, `bool`, lista y cadena, todos con su tipo intacto |
| Recurso | `service.namespace`, `argus.component.role`, `service.version` |
| Logs | cuerpo, severidad y correlación con `trace_id` **y** `span_id` |
| Métricas | histogramas GenAI en VictoriaMetrics con `gen_ai.token.type` como dimensión |

**Dos defectos salieron solo por ejecutarlo**, y ninguno de los dos habría
salido de una revisión:

1. **Los logs llegaban con `ServiceName` vacío.** El SDK movió el `Resource` de
   dentro de `.log_record` al envoltorio del lote. Mirar solo en un sitio no
   falla: el Collector contesta 200 y los logs llegan sin dueño. Hay un test que
   falla sin el arreglo — comprobado quitándolo.
2. **La marca de tiempo de los logs JSON era un literal `.f`.** `formatTime`
   llama por dentro a `time.strftime`, que no conoce `%f` y lo copia tal cual.
   Llevábamos así desde el principio: ningún log tenía subsegundo, así que dos
   líneas de la misma petición no se podían ordenar.

**Lo que esto cuesta**, dicho claro: JSON pesa más que protobuf y no se ha
medido la diferencia de sobrecoste. Para el camino caliente, gRPC sigue siendo
lo recomendado y basta con instalar el extra. La prioridad aquí era que
instalarlo fuera **posible**.

---

## D-078 · Un aviso que sale siempre no es un aviso

> **Fallo de plataforma** · descubierto 2026-09-19 · avisábamos en cada arranque de un caso normal, enseñando a ignorar los avisos

`argus.init()` emitía un `RuntimeWarning` en la segunda llamada. La regla 2 del
contrato dice «idempotente, con aviso», y eso escribimos.

Prosodia lo señaló con el caso que lo rompe: **en su API se llama dos veces
siempre**, porque el router importa el módulo de tareas y ese módulo inicializa.
Así que el aviso sale en cada arranque y en cada corrida de tests.

Un aviso que sale siempre enseña a ignorar los avisos. El día que uno importe de
verdad, estará entre el ruido que ya nadie lee.

**La distinción que faltaba**: llamar dos veces con la misma configuración es
*normal*. Llamar dos veces con configuración *distinta* no lo es — esos
argumentos se descartan en silencio y el proceso queda configurado de una forma
que quien escribió esa línea no espera.

Así que el aviso ahora es condicional, y **nombra los argumentos que se tiran**:

```
argus.init() ya se habia llamado en este proceso con otra configuracion.
La segunda llamada es un no-op y estos argumentos se descartan: endpoint, namespace.
```

La repetición idéntica pasa a `debug`. La propiedad de seguridad se conserva
entera: lo que se pierde es solo el ruido.

---

## D-079 · Un fichero derivado con vida propia, y dos días de silencio

> **Fallo de plataforma** · descubierto 2026-09-19 · el Collector agente descartó el 100% de la telemetría durante dos días y la regla que debía avisar miraba métricas inexistentes

El peor fallo de la plataforma hasta la fecha, y salió por casualidad: al probar
el exportador JSON contra el Collector real, los spans no llegaban a ClickHouse.

**El Collector agente llevaba desde el 17 de septiembre devolviendo 401 en cada
exportación y descartando el 100% de lo que le mandaban las aplicaciones.**
Trazas, métricas y logs. Dos días y cinco horas.

### El fallo

`platform/.env.agent` es un fichero **derivado**: se genera a partir de
`platform/.env` para pasarle el token al contenedor del agente. La receta lo
generaba así:

```make
@test -f platform/.env.agent || { ... generar ... }
```

Solo si faltaba. Al rotar el token del gateway (D-074, antes de publicar el
repositorio) el gateway se reinició con el nuevo y **el agente siguió con el
viejo**, porque su fichero derivado ya existía. Un derivado que se cachea deja
de ser un derivado.

Arreglo: se regenera **siempre**. Es barato y no puede desincronizarse.

### El fallo de verdad: por qué nadie se enteró

Lo del token es un descuido. Que estuviera **dos días sin que nada avisara** es
un fallo de diseño, y es el que importa.

Y lo peor: **la regla existía.** `ArgusCollectorDroppingData` llevaba en
`slo.yaml` desde la Fase 2, con el comentario *«La plataforma se vigila a sí
misma»*. Falló por dos motivos independientes, y cada uno bastaba:

**1. Nadie recogía las métricas.** El Collector las exponía en `:8888` desde el
primer día, y `otelcol_exporter_send_failed_spans_total` marcaba el problema con
toda claridad. No estaban en VictoriaMetrics, así que ninguna regla podía
mirarlas.

**2. Los nombres de métrica no existían.** La regla preguntaba por
`otelcol_exporter_send_failed_spans` y `otelcol_processor_dropped_spans`. Los
reales llevan sufijo `_total`. Una regla contra una métrica inexistente **no da
error**: se queda en «sin datos», que se parece muchísimo a «todo bien».

Es el mismo error que con los atributos `argus.inference.*` que inventamos para
Prometheus: escribir un nombre plausible y no contrastarlo con lo que el sistema
emite de verdad.

Ni el canario ni el *dead man's switch* podían cazarlo: el canario prueba que las
aplicaciones responden, y el interruptor vigila que la plataforma reporte. Las
dos cosas eran ciertas. Lo que fallaba estaba en medio.

**Es el modo de fallo peor de todos**: no se cae nada, no hay errores en ninguna
aplicación, y los paneles se quedan tranquilamente vacíos. El silencio parece
salud — que es exactamente lo que el plan dice que no puede pasar.

### Lo que se perdió, medido

No es una estimación. Es la cuenta de spans por día y por aplicación:

| Día | `prometheus-inference-platform` | `argus` (la propia plataforma) |
|---|---:|---:|
| 15/09 | 30.143 | 129 |
| 16/09 | 5.945 | 197 |
| 17/09 | **9.921** | 216 |
| 18/09 | **0** | 19 |
| 19/09 | **0** | 19 |
| 20/09 (tras el arreglo) | 123 y subiendo | 3 |

**Dos días completos del tráfico real del piloto, perdidos.** Es justo la
evidencia sobre la que se estaban midiendo los criterios.

Y la columna de la derecha explica por qué todo parecía en orden: los servicios
de la propia plataforma exportan **directos al gateway**, sin pasar por el
agente. Siguieron reportando sus 19 spans al día sin inmutarse. El canario veía
las aplicaciones responder, el *dead man's switch* veía la plataforma reportar, y
los dos tenían razón. **La plataforma podía hablar consigo misma, y eso bastaba
para que nada pareciera roto.**

### El arreglo, y el agujero que tenía el primer intento

1. Cada Collector se raspa a sí mismo (`prometheus/interno` → `127.0.0.1:8888`)
   y mete sus métricas en su propia tubería. 59 series nuevas en VictoriaMetrics.
2. `platform/rules/plataforma.yaml`, con los nombres **verificados contra las
   series reales**, no escritos de memoria. Las dos reglas muertas de `slo.yaml`
   se retiran: dejar una regla que no puede disparar es peor que no tenerla.

El primer intento de detector tenía el mismo punto ciego que el original, y solo
salió al reproducir el fallo a propósito: **el agente manda sus propias métricas
por el mismo exportador que se le rompe.** Cuando falla, sus series dejan de
llegar — así que `send_failed > 0` nunca puede dispararse por él, porque no hay
a quien mirar. Solo la **ausencia** lo detecta.

Y la ausencia hay que mirarla **por nivel**, no global: mientras el gateway siga
reportando, un `absent()` sin filtro no ve nada raro. Un `absent()` global
habría dejado pasar exactamente este incidente otra vez.

| Regla | Qué caza | Por qué hace falta |
|---|---|---|
| `ArgusCollectorDescartaTelemetria` | fallos de exportación sostenidos | Caza al gateway, y al agente solo si aún puede reportar |
| `ArgusColaDelAgenteCreciendo` | cola por encima del 80% | Mientras quepa no se pierde; cuando se llene, sí |
| `ArgusAgenteSinAutoMetricas` | el agente calla | **La única que caza este incidente** |
| `ArgusGatewaySinAutoMetricas` | el gateway calla | Sin sus series, las otras tres están ciegas |

Verificado reproduciendo el fallo: se arrancó el agente con un token equivocado a
propósito y se comprobó que la expresión de ausencia devuelve `1` con el agente
roto y **nada** con el agente sano. Una regla que nunca se ha visto disparar es
una hipótesis, no un detector — y esta plataforma ya tenía una de esas.

### Lo que esto le cuesta al piloto

El reloj de 14 días vuelve a **0**. Tres fallos hoy, y el tercero es de los que
justifican que el reloj exista.

---

## D-080 · Seis incidentes detectados, cero entregados

> **Fallo de plataforma** · descubierto 2026-09-19 · la detección funcionó perfectamente durante el apagón y ninguna de las seis notificaciones llegó

Investigando `D-079` salió la pregunta obvia: la sonda de silencio vigila los
tres componentes de Prometheus, sus métricas se fueron a cero dos días enteros
— **¿por qué no avisó?**

Resulta que **sí avisó**. Seis incidentes, correctamente detectados:

```
2026-09-17T21:22  x3      2026-09-18T16:36  x1
2026-09-17T22:17  x1      2026-09-19T07:13  x1
2026-09-18T07:34  x1      2026-09-19T15:55  x1
```

Y **ninguno se entregó**:

```
telegram  -> <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED]
             certificate verify failed: self-signed certificate in certificate chain>
email, gchat -> notify.channel_not_loaded
```

Telegram es el único canal humano configurado —de `gchat` y `email` ya sabíamos
(D-064)—, así que su fallo es el fallo entero.

**La causa del SSL no es un bug nuestro**: un certificado autofirmado en la
cadena es una red con inspección TLS por el medio. Comprobado ahora desde el
host y desde el contenedor: las dos funcionan. Es intermitente, y depende de a
qué wifi esté enganchado el portátil.

### Lo que sí es nuestro, y es lo importante

```python
try:
    sink.send(incident, update=update)
except Exception as exc:
    log.error("dispatch.send_failed", ...)      # y aqui se acaba
```

**Un intento. Sin reintento, sin cola, sin nada.** El incidente se pierde.

Y puesto al lado de lo que hacemos con la telemetría, la asimetría no se
sostiene:

| | si la red falla |
|---|---|
| Telemetría (entrada) | cola en disco con WAL, reintentos, sobrevive a un fin de semana |
| **Notificación (salida)** | **un intento y a la basura** |

Construimos toda la resiliencia en la entrada del sistema y ninguna en la
salida — cuando la salida es, literalmente, para lo que existe el sistema.
Da igual lo bien que detectes si nadie se entera.

### El arreglo, y el que casi meto de paso

Tres intentos con retroceso exponencial y jitter (1 s y 2 s). Los fallos reales
fueron momentos sueltos, no cortes de horas, así que esa ventana los cubre.

**El primer intento estaba mal**, y lo cazó un test que ya existía: dormir el
retroceso **dentro del hilo trabajador**. Con un solo worker, un canal muerto
se queda tres segundos reintentando y **retrasa la entrega de todos los demás**
— que es exactamente la propiedad que esa cola existe para proteger (D-029).

`test_un_canal_caido_no_impide_que_los_demas_reciban` falló, y tenía razón.

El reintento se **reprograma** con un temporizador y devuelve el envío a la
cola; el hilo sigue trabajando. `drain()` cuenta también los reintentos
pendientes: con la cola vacía y un temporizador esperando, «drenado»
significaría «entregado» cuando en realidad significa «todavía no lo he vuelto
a intentar».

### Lo que queda abierto, dicho claro

Tres intentos en tres segundos cubren un parpadeo de red. **No cubren un
portátil que pasa la noche en una red con inspección TLS.** Para eso haría falta
persistir la notificación pendiente, como hace la telemetría.

No se hace ahora porque no es el fallo que hemos visto, y porque una cola
durable de notificaciones trae su propia pregunta —qué hacer con un aviso de
hace ocho horas cuando el incidente ya se resolvió— que merece decidirse a
propósito y no de pasada. Queda como `B-19`.

Mientras tanto, `dispatch.send_failed` sigue siendo la señal de que esto ha
pasado, y ahora dice cuántos intentos se hicieron.

---

## D-081 · El modelo se muda a los nombres que inventamos para otros

> **Fallo de plataforma** · descubierto 2026-09-19 · escribimos nombres de atributo a mano en un documento, otro equipo los implementó fielmente, y nuestro propio paquete no los conocía

Tenemos una fuente de verdad —`libs/semconv-model/argus.yaml`— con generación
automática de constantes para Python y Go, **precisamente** para que sea
imposible que dos lenguajes emitan el mismo atributo con nombres distintos.

Y luego escribimos los nombres **a mano en un documento** para el equipo de
Prometheus, sin contrastarlos con ella:

| Les pedimos | El modelo definía |
|---|---|
| `argus.inference.backend_id` | `argus.backend.id` |
| `argus.inference.ttft_ms` | `argus.ttft_ms` |
| `argus.inference.first_token_ms` | `argus.first_token_ms` |

Lo implementaron fielmente. Durante días emitieron bajo nombres que nuestro
paquete no conocía, y **lo descubrimos de la peor forma**: midiendo su ventana
con *nuestros* nombres y viendo cero. Estuvimos a punto de escribirles que su
arreglo no había llegado. Volvimos a consultar con los nombres que les pedimos
y estaban los 689.

**La decisión: el modelo se muda a la versión de ellos.** No porque cueste menos
—cuesta lo mismo— sino porque **es mejor**. `argus.inference.*` dice de qué
dominio es el atributo; el nuestro los dejaba sueltos en la raíz junto a cosas
que no tienen nada que ver, y `argus.cost_usd` además se confundía a ojo con la
métrica `argus.cost.usd`, que es otra cosa.

Se mueve **el grupo entero**, no solo los tres: dejar `argus.backend.circuit_state`
donde estaba mientras `backend.id` se va a `argus.inference.*` es peor que
cualquiera de las dos opciones. Nadie emite los otros cuatro todavía, así que
mover cuesta cero.

### El guardarraíl, que es lo que de verdad importa

La lección incómoda no es el renombrado: es que **el generador no sirve de nada
si el contrato que mandas fuera se escribe en otro sitio.**

`test_documentos_vs_modelo.py` recorre `docs/**.md`, extrae todo lo que tenga
forma de atributo `argus` entre comillas invertidas y falla si no existe en el modelo. Con lista de
permitidos —cada excepción con su motivo escrito— y un segundo test que falla si
un permitido deja de hacer falta, porque una excepción caducada es el sitio
donde se esconde el siguiente error.

Comprobado metiendo en un documento un nombre parecido a `ttft_ms` pero con otro sufijo: falla y dice
en qué fichero.

Encontró algo de paso: **`argus.sampling.baseline_pct` lo escribe el Collector y
no estaba declarado en ninguna parte.** Un atributo que llega al almacén y que
alguien consulta, cuyo significado vivía solo en un fichero de configuración.
Ahora hay un grupo `platform` para los que pone la plataforma y no la
aplicación, con ese y con `argus.collector.tier`.

---

## D-082 · Tres defectos que solo se ven desde fuera

> **Fallo de plataforma** · descubierto 2026-09-19 · el equipo que más necesita las métricas GenAI tenía que importarlas de un módulo privado, el TTFT no se podía trocear por operación, y el coste rellenaba con `'unknown'`

Prometheus instaló `1.0.0a3` en un entorno desechable y lo **ejercitó**, métricas
incluidas contra un `InMemoryMetricReader`. Los tres hallazgos son invisibles
desde dentro: solo aparecen cuando alguien que sirve inferencia intenta usarlo.

**1 · El módulo que más necesita una plataforma de inferencia era el privado.**
`_metrics_api` emite exactamente las cuatro métricas que deberían emitir. Y
nuestro propio docstring dice por qué vive ahí: *«si las métricas hubiera que
emitirlas a mano, nadie las emitiría»*. Con la puerta marcada como privada, para
usarlas había que escribir `from argus_semconv._metrics_api import record_ttft`,
que es justo lo que un consumidor no debe hacer. Ahora es `argus_semconv.metrics`,
con puente de compatibilidad que avisa y **reexporta en vez de duplicar**: los
instrumentos tienen que ser los mismos objetos o se emiten dos series.

**2 · `record_ttft` construía la operación y luego la borraba.**

```python
attrs = _base("chat", provider, model)
attrs.pop(A.GEN_AI_OPERATION_NAME, None)   # <- deliberado, y equivocado
```

Duration y tokens sí la llevaban, así que el TTFT era la **única** métrica que no
se podía pedir por tipo de operación. En un despliegue donde chat, embeddings y
rerank conviven, ése es el corte que más falta hace.

**3 · `argus.cost.usd` rellenaba con `'unknown'`.** Con eso, «nadie atribuyó esta
llamada» y «se atribuyó a algo que se llama unknown» son indistinguibles, las dos
generan serie temporal y las dos cuestan cardinalidad. Es la enfermedad de los
tres estados —la misma del `DEFAULT 0` de Aeon y del `sum(cost or 0)` de la
propia factura de Prometheus— ahora en una etiqueta de métrica. Omitir cuesta lo
mismo y no miente.

**4 · Y uno más, que es el que peor pinta tiene**: `SEMCONV_VERSION` vale `1.0.0`
—es la versión del *modelo*— y con ella registrábamos el scope del tracer y del
meter. Así que un prelanzamiento y una estable declaraban exactamente lo mismo,
`scope: argus-semconv 1.0.0`, y en el almacén no había forma de separarlos: justo
lo que quieres poder hacer cuando algo no cuadra durante una adopción.

Ahora el scope lleva la versión del **paquete** y la del modelo viaja como
atributo del scope, que es donde pertenece.

Al arreglarlo apareció una trampa que conviene dejar escrita: el tercer
posicional de `get_tracer` es el **provider**, no `schema_url`. Pasar los
atributos por posición mete el diccionario en `schema_url` y el fallo no salta al
llamar — salta después, al hashear el scope, con un `TypeError: unhashable type:
'dict'` que no apunta a la causa. Y un proveedor no-op se lo traga entero, así
que la primera comprobación que hicimos dio verde sin comprobar nada. Hay test de
firma para eso.

Los cuatro con test que falla sin el arreglo, comprobado quitándolo.

---

## D-083 · El muestreo decidía antes de que el trabajo empezara

> **Fallo de plataforma** · descubierto 2026-09-26 · una traza que publica en una cola se juzgaba con 30 segundos de evidencia, y el trabajo de verdad cerraba 21 minutos después

Prosodia corrió un doblaje real de punta a punta —1269 segundos, salida
correcta— y **no quedó ni un span del worker en el almacén**.

El mecanismo, y no es que los spans tardíos no lleguen:

1. La traza arranca con `POST /api/projects`, que cierra en milisegundos.
2. A los 30 segundos (`decision_wait`) el tail sampling decide con lo único que
   tiene delante: una petición HTTP rápida, sin error. Cae en el 10% base y se
   **descarta**.
3. El span del worker cierra **21 minutos después**. Llega —lo medimos en el
   agente— pero ya hay una decisión de descarte registrada para ese `TraceId`.

Ellos lo aislaron con dos experimentos controlados y **lo reprodujimos aquí**
antes de tocar nada:

| experimento | resultado |
|---|---|
| raíz rápida sin error + span que cierra a los 45 s | **se pierden los dos** |
| raíz con error (decisión: conservar) + span a los 45 s | **se guardan los dos** |

El segundo confirma además que la caché de decisiones funciona: un span tardío
de una traza conservada entra sin problema. Lo que falla es **la decisión**, no
el transporte.

### Por qué esto era peor de lo que parece

Suyo, y es el argumento que más duele: buscamos en 41 proyectos quién tenía cola
y los elegimos a ellos **porque son los únicos con trabajo asíncrono largo**. Es
decir, nos interesaba exactamente la forma que nuestra ventana de 30 segundos no
puede ver. Y la pregunta con la que justificamos el piloto entero en A-01
—*«¿por qué tardó tanto este doblaje?»*— es literalmente la que el muestreo
dejaba sin muestra.

### El arreglo: decidir con evidencia que sí llega a tiempo

Subir `decision_wait` no vale: nadie va a bufferizar 21 minutos de trazas en
memoria, y siempre habrá un trabajo más largo.

Lo que sí llega dentro de la ventana es el **span del productor**. La
instrumentación de Celery lo crea en el proceso de la API, en el mismo
milisegundo que la petición, y lleva `messaging.destination`. Con eso basta para
saber que la traza va a continuar en otro sitio, que es justo lo que hay que
decidir.

```yaml
- name: async-handoff
  type: string_attribute
  string_attribute:
    key: messaging.destination
    values: [".*"]
    enabled_regex_matching: true
```

**Lo que cuesta, medido y no estimado**: sobre 7 días, 5 de 8640 trazas tocan una
cola — un 0,06%. Guardarlas enteras no mueve la aguja, y es el 0,06% que
responde la pregunta del piloto. (El número está medido sobre trazas
*almacenadas*, que ya pasaron por el muestreo; el real es algo mayor, porque
justamente estas se estaban tirando. El orden de magnitud no cambia.)

Dos cosas más:

- Se añade la política gemela sobre `messaging.destination.name`, la grafía
  nueva de las semconv. Las instrumentaciones están migrando; tener las dos
  evita que una actualización nos deje sin política y sin avisar.
- `decision_cache` pasa a estar **explícito**. De él depende que el span de los
  21 minutos encuentre la decisión todavía en memoria, y dejar eso a un valor
  por defecto que nadie ha mirado es cómo se construye el siguiente D-079.

**Verificado con el arreglo puesto**: una raíz rápida y sin error, pero con salto
a cola, conserva los tres spans incluido el que cierra a los 45 s. La misma
traza sin salto a cola se sigue muestreando igual, así que la política
discrimina en vez de conservarlo todo.

**No hace falta que Prosodia ponga `ARGUS_SLO_MS`.** Preguntaron dónde fijarlo y
la respuesta es que en ningún sitio: el arreglo es nuestro y ya está.

---

## D-084 · El `elif` que nunca se ejecutó, y todos los INFO que se perdieron

> **Fallo de plataforma** · descubierto 2026-09-26 · `argus.init()` dejaba el logger raíz en WARNING, así que toda aplicación que nos adoptaba perdía el 100% de sus `logger.info()` en silencio

Prosodia avisó de que hay que fijar el nivel a mano, y fueron generosos con la
explicación: dijeron que solo lo bajamos *«si estaba en NOTSET, cosa que
agradecemos, respeta la configuración ajena»*.

Al ir a documentarlo resultó ser peor. El código era:

```python
elif root.level == logging.NOTSET:
    root.setLevel(logging.INFO)
```

Y **el logger raíz de Python arranca en `WARNING`, nunca en `NOTSET`**:

```
nivel del root recien arrancado: 30 = WARNING
es NOTSET?: False
```

La rama no se ejecutaba jamás. No es que respetáramos una decisión ajena: es que
dejábamos el nivel de fábrica y se perdía todo lo que no fuera warning.
Comprobado emitiendo un `info()` y un `warning()` tras `argus.init()`: solo salía
el segundo.

**No lo vimos nosotros porque nuestros propios servicios fijan el nivel por su
cuenta.** Es exactamente el hueco que solo se ve desde fuera, como los cuatro de
D-082.

La regla ahora distingue el WARNING de fábrica del WARNING elegido:

| situación | qué hace |
|---|---|
| argumento `logging_level` o `ARGUS_LOG_LEVEL` | manda eso |
| root intacto: `NOTSET`, o `WARNING` **sin handlers** | baja a `INFO` |
| cualquier otra cosa | no se toca |

Lo de «WARNING sin handlers» es heurística y lo es a propósito: `basicConfig()`
deja WARNING **y** un handler, así que un WARNING deliberado se distingue del de
fábrica por la huella que deja al configurarse. Pisar una decisión de la
aplicación sería el error contrario, y también sería nuestro.

### Lo que Prosodia arregló de su lado, y que conviene documentar

Su aplicación escribe con structlog a consola y fichero, con un processor final
que corta la cadena con `DropEvent` y **nunca pasa por stdlib**. Nuestro puente
se engancha al logger raíz de stdlib, así que para nosotros sus logs no existían.

No es un fallo nuestro —no hay forma de capturar lo que no pasa por ahí— pero es
un patrón que va a repetirse, así que va a la guía: quien use structlog con
`DropEvent` necesita un logger de stdlib dedicado, con `propagate = False` para
no imprimir dos veces.

---

## D-085 · Qué «primer token», y el modelo que no lo decía

> **Fallo de plataforma** · descubierto 2026-09-27 · el modelo declaraba la métrica de TTFT sin decir cuál de nuestras dos medidas la alimenta, así que el equipo que la emite tuvo que elegir por su cuenta

Prometheus emite `gen_ai.server.time_to_first_token` con
`argus.inference.first_token_ms` —primer token de cualquier tipo, razonamiento
incluido— y no con `argus.inference.ttft_ms`, que es el primer token **visible**.
Nos preguntaron si preferíamos que la métrica siguiera al atributo o al estándar.

**La respuesta es que tienen razón, y la evidencia es nuestra.**

### Lo que dice el estándar, literalmente

```
gen_ai.server.time_to_first_token
  "Time to generate first token for successful responses."
```

Sin adjetivo. No dice visible, ni de contenido. Su lectura es la literal.

### Lo que dicen nuestros propios números

De A-24, medido sobre 689 spans en nuestro almacén:

| | spans | cobertura |
|---|---|---|
| `first_token_ms` | 439 | 63,7 % — el **100 %** de las peticiones en streaming |
| `ttft_ms` (visible) | 30 | 4,4 % |

Y a quién pertenecía ese 4,4 %:

| modelo | con `ttft_ms` | p95 de `first_token_ms` |
|---|---|---|
| `gpt-oss-20b-mxfp4` | 26 | 161 ms |
| `qwen3-0.6b` | 4 | 53 ms |
| `qwen3-8b-q6` | **0** | **283 ms** |

El modelo de peor cola es exactamente el que nunca aparece. Una métrica estándar
construida sobre la medida visible se habría llenado de `gpt-oss` y **habría
declarado sana la plataforma justo mientras el modelo más lento se degradaba.**
Ese argumento lo escribimos nosotros en A-24; ellos solo lo han aplicado un nivel
más arriba.

### El estándar no tiene sitio para «visible», y conviene saberlo

Comprobado en los dos candidatos, no supuesto:

| métrica | qué mide | sirve para «visible» |
|---|---|---|
| `gen_ai.server.time_to_first_token` | **servidor**: generar el primer token | no, es cualquier token |
| `gen_ai.client.operation.time_to_first_chunk` | **cliente**: recibir el primer trozo del stream | no — para un modelo que razona, el primer trozo es razonamiento |

Así que la medida visible **no tiene nombre estándar** y no debe forzarse dentro
de uno. Se queda como atributo por span, donde responde «¿cuánto tardó este
usuario en ver algo?» sin pretender ser una serie agregable. Si algún día hace
falta agregada, su nombre tendrá que ser `argus.*` y nunca `gen_ai.*`.

### La parte que es un fallo nuestro

**El modelo no decía cuál de las dos alimenta la métrica.** Solo el nombre, el
instrumento y la unidad. Con dos medidas casi homónimas en el mismo grupo, eso no
es una omisión menor: es dejar la decisión en manos de quien emite, que es
precisamente lo que un modelo de convenciones existe para evitar.

Arreglado con un `brief` explícito y el comentario de por qué.

Y al mirarlo salió otro descolgado: **el modelo declaraba tres atributos para esa
métrica y se emitían cuatro.** Cuando arreglamos `record_ttft` para que aceptara
`operation` (D-082) no actualizamos el modelo. Dos versiones declarando una cosa
y emitiendo otra, sin que nada lo notara.

`test_modelo_vs_emision.py` cierra ese hueco en los dos sentidos: emitir lo que
el modelo no declara, y declarar lo que nadie emite. Es el mismo guardarraíl que
`test_documentos_vs_modelo.py` pero por el lado del código en vez del de la
documentación.

Encontró algo en su primera ejecución: el modelo declara
`deployment.environment.name` en `argus.cost.usd` y `record_cost` no lo emite —
viene del Resource. Queda como exención **con el motivo escrito**, y hay un tercer
test que falla si una exención deja de hacer falta.

Y de paso destapó una colisión de estado global: dos módulos de test fijando cada
uno su `MeterProvider`, con el segundo ignorado en silencio. Ahora hay una
fixture de sesión en `conftest.py`, igual que la que ya existía para trazas y por
el mismo motivo.

---

## D-086 · La auditoría: el vocabulario es nuestro, el registro no

> **Fallo de plataforma** · descubierto 2026-09-27 · seudonimizábamos `user.id` EN SITIO, así que el span salía con un atributo que prometía un identificador y contenía un hash

Prometheus puso log de auditoría en su gateway y pidió cuatro atributos porque
nuestras convenciones no podían expresar **quién**: `argus.actor.id`,
`argus.actor.kind`, `argus.actor.email` y `argus.action`. No los inventaron en
nuestro namespace — los trajeron a preguntar.

### Tres de los cuatro ya existen, y uno ya lo están emitiendo

Fuimos a mirar el estándar antes de dar nombres, que es la lección de D-081:

| lo que pedían | existe como | nota |
|---|---|---|
| `argus.actor.id` | **`user.id`** | del estándar |
| `argus.actor.email` | **`user.email`** | del estándar |
| `argus.action` | **`http.request.method` + `http.route`** | **estable**, no experimental |
| `argus.actor.kind` | — | **no existe. Este sí es nuestro** |

El de `argus.action` es el hallazgo: **`http.route` ya es la plantilla de ruta**,
no el path resuelto, y es exactamente el argumento que ellos daban para no meter
el id dentro. Y no hay que añadir nada, porque **ya lo emiten**: comprobado en sus
propios spans de `manager-api` en nuestro almacén, `http.route = /v1/backends`
junto a `http.request.method = GET`.

Inventar `argus.action` habría sido D-081 al revés: poner nombre propio a algo
que ya tiene uno estable y que la instrumentación de ASGI rellena sola.

Del tipo de actor no hay nada —no existe `user.type` ni equivalente— y sin él un
identificador no se puede interpretar: `svc-7` no se lee igual si es una persona
o una credencial de máquina. Ese entra como `argus.actor.kind`, con `unknown`
como valor **legítimo y no relleno**: en un registro de auditoría «no consta» es
un hecho, y hay que poder distinguirlo de «nadie lo puso». Es la enfermedad de
los tres estados otra vez, y esta vez la evitamos de entrada.

### El fallo nuestro: el atributo mentía

Al mirar qué le pasa a un actor cuando cruza el gateway salió esto:

```yaml
set(attributes["user.id"], SHA256(Concat([attributes["user.id"], <sal>], "")))
```

Se hasheaba **en sitio**. El span salía con `user.id = <64 caracteres hex>`, que
no es un identificador de usuario. El atributo prometía una cosa y contenía otra,
y quien lo leyera —una persona o el agente de RCA— concluiría que ese es el id.

El estándar tiene el nombre exacto para esto: **`enduser.pseudo.id`**. Ahora el
hash va ahí y el original se borra.

Con precedencia explícita entre las cuatro grafías (`user.id`, `user_id`,
`enduser.id`, `jwt.subject`): si un span trae dos, gana la primera y las demás no
sobreescriben. Sin eso, dos fuentes pelearían por el mismo destino según el orden
de llegada.

**Verificado de punta a punta**, emitiendo un actor real y consultando el almacén:

```
user.id             (vacío)   ← borrado
user.email          (vacío)   ← borrado
jwt.subject         (vacío)   ← borrado
enduser.pseudo.id   a87d91d9057ae000…
http.route          /admin/api/nodes/{node_id}/deactivate
argus.actor.kind    user
```

Salía gratis: nada en paneles, servicios, MCP ni runbooks leía `user.id`, y había
**cero** spans con él en 14 días. Ningún dato que migrar y ninguna consulta que
arreglar.

### Y la respuesta a lo que preguntaban, por escrito

Ellos decidieron que **el registro de verdad es su tabla y nosotros recibimos una
copia**, con el argumento de que un pipeline de observabilidad es con pérdidas por
diseño y un rastro que puede perder eventos no es un rastro de auditoría.

Es correcto, y la razón de verdad es más dura que la suya: **no es que podamos
perder eventos, es que destruimos al actor a propósito.** El id se hashea y el
correo se borra. Nuestra copia puede responder «¿fue el mismo actor?» y nunca
«quién». Eso no es una limitación que arreglaríamos: es la capa de privacidad
funcionando.

Así que la línea queda escrita:

- **El vocabulario de auditoría sí es nuestro.** Si no, el siguiente equipo que
  necesite auditar se inventa otros cuatro nombres, que es exactamente lo que el
  modelo de convenciones existe para evitar.
- **El registro de auditoría no.** No somos el sistema de registro de nada
  auditable, y no queremos serlo.

---

## D-087 · vmalert evaluaba con 5,4 días de atraso, y las alertas de SLO no podían disparar

> **Fallo de plataforma** · descubierto 2026-09-27 · el evaluador de reglas se quedó usando un reloj atrasado, diciendo `health: ok`, y todo lo que escribía caía en el pasado

### Empecé con el diagnóstico equivocado

Las dos reglas `*SinAutoMetricas` llevaban cinco días disparando. Miré los huecos
de las series y encontré que `count(up)` tenía **exactamente los mismos** que las
métricas del Collector, así que concluí: se para la máquina entera, el portátil
duerme, y mi regla pagina cada vez que se cierra la tapa.

Era plausible, encajaba con el plan —el plano central es portátil— y era **falso**.

### Lo que pasaba de verdad

Al construir el arreglo añadí una regla de grabación de latido, `vector(1)`, que
por construcción no puede devolver vacío. No aparecía en VictoriaMetrics.

Y ahí se deshizo el ovillo:

| comprobación | resultado |
|---|---|
| `vmalert_remotewrite_sent_rows_total` | 79.647, **0 errores** |
| `vm_rows_inserted_total{type="promremotewrite"}` | 83,8 millones |
| `/api/v1/query` de `argus:observador_despierto` | **0 resultados** |
| `/api/v1/export` de la misma serie | **existe**, 11 muestras |
| marca de tiempo de esas muestras | **2026-09-22T08:09:30Z** |
| reloj del host y de los contenedores | idénticos, al día |

Los relojes del sistema coincidían. Lo que estaba atrasado era **el reloj de
evaluación de vmalert**: escribía sus muestras 5,4 días en el pasado, donde
ninguna consulta a `now` las encuentra.

Se arregla reiniciándolo. Se comprobó inmediatamente: tras el reinicio, el latido
pasó a ser consultable en el instante correcto.

### El daño, que era mucho mayor que dos alertas ruidosas

**Ninguna regla de grabación era consultable a `now`.** Y las alertas de burn-rate
leen justo eso:

```yaml
expr: argus:error_ratio:rate5m > 0.144 and argus:error_ratio:rate1h > 0.144
```

Aquí conviene ser exacto, porque la primera conclusión fue demasiado fuerte. **No
es que las alertas de SLO no pudieran disparar**: `ArgusSLOBurnRateSlow` disparó
una vez esa semana. vmalert leía *y* escribía en el mismo instante atrasado, así
que su mundo era internamente consistente.

Lo que pasaba es peor de explicar y igual de malo:

| | |
|---|---|
| vmalert evaluaba | contra datos de hace 5,4 días |
| así que alertaba | sobre condiciones **del pasado**, no de ahora |
| y cualquier cosa que consultara a `now` | Grafana, un panel, una consulta a mano: **no veía nada** |

O sea: el camino templado seguía funcionando, pero describiendo la semana pasada.
Un incidente de hoy no lo habría visto, y uno ya resuelto podía seguir
disparando. Verificado tras el reinicio: las dos series vuelven a responder a
`now`.

Y explica el síntoma que me llevó por el camino equivocado: las reglas de
ausencia se evaluaban contra datos de hace cinco días, donde
`argus_collector_tier="agent"` **todavía no existía** —esa etiqueta nació con el
arreglo de D-079 la semana pasada—. Así que disparaban con razón, sobre un pasado
en el que la serie no estaba. No tenía nada que ver con dormir.

### Por qué no lo cazaba nada, y no podía cazarlo

**La víctima era el evaluador.** Una regla que preguntara «¿estoy evaluando a la
hora correcta?» la habría evaluado el mismo reloj atrasado, y desde ahí todo es
consistente. No hay expresión PromQL que detecte esto, porque el que la ejecuta
es el que está mal.

Tiene que mirarlo alguien de fuera. Es el mismo argumento que el *dead man's
switch*, y la misma razón por la que la industria pone el watchdog fuera: **una
regla nunca debe preguntarse si ella misma está viva.**

### Los tres arreglos

**1 · El latido y la puerta.** `argus:observador_despierto` vale 1 siempre, y las
dos reglas de ausencia se condicionan a que haya suficientes muestras recientes:

```promql
absent_over_time(otelcol_exporter_sent_spans_total{argus_collector_tier="agent"}[10m])
and on() (count_over_time(argus:observador_despierto[10m]) > 30)
```

Responde «¿cuánto lleva despierto el observador?», que es la precondición para que
un silencio signifique algo.

**Y conviene ser preciso sobre qué cubre, porque la primera versión de esta nota
prometía más de lo que da.** La puerta se cierra cuando el latido tiene pocas
muestras *relativas al instante en que se evalúa*:

| situación | puerta |
|---|---|
| vmalert acaba de arrancar o reiniciarse | **cerrada** ✓ |
| su reloj salta a un instante anterior a que el latido existiera | **cerrada** ✓ — es lo que pasó hoy |
| su reloj lleva atrasado de forma estable, con latido ya escrito en ese pasado | **abierta** ✗ |

O sea: cubre la **transición**, no el régimen permanente. Si el atraso se
estabiliza, vmalert lee su propio latido en el pasado, lo encuentra completo, y la
puerta se abre otra vez.

Eso **no** es un agujero del diseño: es la razón por la que el arreglo 2 existe y
no es opcional. El régimen permanente lo cubre el vigilante externo, que es el
único que mira con un reloj que no es el de vmalert.

Y de paso arregla lo que `for:` **no** puede: `for` se evalúa contra el reloj de
pared, así que un sueño más largo que el `for` lo supera en la primera evaluación
tras despertar. Ese sí era un problema real, aunque no fuera el que estaba
pasando.

**2 · La puerta no puede tapar el fallo.** Si la puerta cierra por vmalert
atrasado, las alertas callan — que es correcto, pero entonces nadie sabe que
vmalert está mal. Por eso el *dead man's switch* gana
`comprobar_frescura_de_reglas()`: lee `/api/v1/rules` desde el host, busca el
`lastEvaluation` más reciente y avisa si supera el umbral. Sin dependencias y
fuera del compose, como el resto del fichero.

Comprobado en los tres estados: sano («atraso 3 s»), atrasado, y con vmalert
inalcanzable.

**3 · El puerto.** vmalert no publicaba el 8880, así que el vigilante del host no
podía leerlo. Ahora sí, atado al loopback como el resto.

### Lo que cuesta, dicho claro

La detección pasa de ~5 minutos a ~12: `absent_over_time(...[10m])` necesita diez
minutos sin muestras, más el `for: 2m`.

Es un cambio que vale la pena y conviene razonarlo en vez de asumirlo. El fallo
que estas reglas existen para cazar —el agente descartando el 100% de la
telemetría— estuvo **dos días** sin detectarse. Entre 5 y 12 minutos no hay
diferencia práctica para eso, y a cambio se elimina la clase de falso positivo
que enseña a ignorar la alerta.

Si algún día hiciera falta detección más rápida, el camino no es estrechar la
ventana: es que el agente reporte por un canal que no dependa de su propio
exportador roto. Eso es trabajo de verdad y no está hecho.

### Lo que me llevo

Dos veces hoy el primer diagnóstico era el plausible y no el correcto. La
diferencia la marcó una regla que **no puede devolver vacío**: en cuanto
`vector(1)` no apareció, la hipótesis del sueño dejó de sostenerse.

Meter en el sistema algo cuyo valor se conoce de antemano es más barato que
razonar sobre huecos, y no admite interpretaciones.

---

## D-088 · El vigilante llevaba once días sin actualizarse, y luego lo rompí tres veces

> **Fallo de plataforma** · descubierto 2026-09-27 · el dead man's switch que corre de verdad es una copia fuera del repositorio, y estaba obsoleta: el arreglo de D-087 existía y no hacía nada

Al añadir `comprobar_frescura_de_reglas()` (D-087) fui a comprobar que el
vigilante la ejecutaba. No la ejecutaba:

```
~/Library/Application Support/argus-deadman/deadman.py   16 sep
grep -c comprobar_frescura_de_reglas                     0
```

**Once días de deriva.** La receta `make deadman-setup` copia siempre y hasta
avisa por escrito de que hay que reejecutarla — pero nadie lo hizo, y **nada lo
detectaba**. Es el patrón de `.env.agent` de D-079 por segunda vez: una copia
derivada que vive fuera del repositorio y se queda atrás en silencio.

`make pilot-status` ahora compara los `sha256` de las dos copias. El criterio 7
deja de decir «hay red de seguridad» por el hecho de estar cargado en launchd:
cargado no es lo mismo que al día.

### Y entonces empezaron mis fallos, que son los interesantes

**1 · `ruff --fix` lo rompió y estuvo reventando en cada ejecución.**

Con `target-version = py311`, la regla UP017 cambió `timezone.utc` por
`datetime.UTC`. Pero **launchd invoca `/usr/bin/python3`, que en este Mac es el
3.9.6 de Xcode**, y `datetime.UTC` no existe ahí:

```
ImportError: cannot import name 'UTC' from 'datetime'
  (.../Python3.framework/Versions/3.9/lib/python3.9/datetime.py)
```

El error solo aparecía en `/tmp/argus-deadman.err`. `launchctl list` mostraba el
trabajo cargado y `launchd` registraba salida 1, que nadie mira.

Lo doloroso es lo que contradice: el fichero entero existe bajo la premisa de
**correr sin nada instalado**, para no compartir modos de fallo con lo que
vigila. Aplicarle la versión de Python del proyecto rompe exactamente esa
premisa. Ahora `deadman/**` está exento de `UP` en la configuración de ruff, con
el motivo escrito, y hay tres tests que lo compilan e importan **con
`/usr/bin/python3`**, no con el nuestro.

Comprobado volviendo a meter `datetime.UTC`: el test falla.

**2 · Mi parser fallaba a ratos, que es peor que fallar siempre.**

vmalert está escrito en Go y emite nanosegundos:

```
"lastEvaluation": "2026-09-27T18:16:48.198507418Z"
```

El `fromisoformat` de Python 3.9 solo acepta 3 o 6 dígitos de fracción. Con 9
lanza `ValueError`. **Y Go recorta los ceros del final**, así que la misma marca
tiene 9 dígitos unas veces y 6 o 7 otras: el parser funcionaba a ratos. En un
vigilante, «a ratos» significa avisos equivocados en momentos aleatorios.

**3 · Y lo callaba, que es el fallo de verdad.**

```python
except ValueError:
    continue
```

Con eso, «no sé leer la fecha» se convertía en «no hay fechas», y de ahí en
«vmalert no ha evaluado nunca» — tres cosas distintas colapsadas en una. Mandó un
aviso por Telegram diciendo algo que no era cierto.

Ahora los ilegibles se cuentan aparte y el mensaje dice qué pasó de verdad. Quien
llama decide; el parser no interpreta.

### Lo que sí funcionó, y conviene anotarlo

La cadena de notificación completa quedó probada sin buscarlo, en los dos
sentidos: el aviso equivocado **llegó a Telegram**, y al restaurar el agente llegó
el de recuperación. Los reintentos de D-080 siguen en su sitio.

### Verificación de D-087, de punta a punta

| | |
|---|---|
| puerta cerrada tras arrancar | no pagina ✓ |
| puerta abierta + agente sano | `inactive` — sin falso positivo ✓ |
| puerta abierta + agente roto a propósito | **`firing` a los 13 min** ✓ |
| agente restaurado | se apaga y avisa de la recuperación ✓ |
| vigilante con Python 3.9 | 5 comprobaciones OK, salida 0 ✓ |

### Y una cuarta, ya casi de chiste

Los tres tests que acabo de escribir para que esto no se repita **tampoco se
ejecutaban**: `testpaths` de pytest lista servicios y librerías, y el vigilante no
es ninguna de las dos cosas. Estaban escritos, en verde al invocarlos a mano, y
fuera de la suite.

Lo vi porque el total de tests no subió: 276 antes de escribirlos y 276 después.
Añadido a `testpaths`, son 279.

### La lección, que es la misma cuatro veces

Hoy he escrito cuatro cosas que parecían correctas y no corrían: un arreglo en una
copia que nadie actualizó, uno bajo un intérprete que no era el real, un parser
que solo funcionaba con ciertos datos, y unos tests fuera de la lista.

**Los tres se descubrieron ejecutando la cosa de verdad**, no leyéndola. Y el
tercero solo porque fui a mirar por qué un aviso decía algo raro en vez de dar por
bueno que el vigilante funcionaba.

---

## D-089 · Mis propias alertas de plataforma no podían paginar

> **Fallo de plataforma** · descubierto 2026-09-27 · las reglas de D-079 y D-087 llevaban etiquetas que el normalizador no lee, así que sus incidentes llegaban como `unregistered/unknown` y su `page` se recortaba a `ticket`

Al ir a cerrar el piloto miré los incidentes abiertos y encontré esto:

```
app=unregistered  componente=unknown  severidad=ticket  "El Collector agente lleva 5 minutos sin reportarse"
app=unregistered  componente=unknown  severidad=ticket  "El gateway lleva 5 minutos sin reportarse"
```

Las dos son mías, y las dos declaran `severity: page` en la regla.

### La cadena, entera

Mis reglas llevaban:

```yaml
labels:
  severity: page
  argus_app: argus-platform
  argus_component: collector
```

Y el normalizador lee otras:

```python
app=labels.get("service_namespace") or labels.get("app") or "unregistered",
component=labels.get("service_name") or labels.get("component") or "unknown",
```

`argus_app` no está en esa lista. De ahí sale todo lo demás: sin app resuelta la
criticidad cae al mínimo, y el motor **recorta** la severidad por criticidad —que
es correcto y lo escribimos nosotros—, así que un `page` acaba en `ticket`.

**Resultado: las alertas que vigilan la salud de la plataforma no podían
despertar a nadie.** Y llevaban así desde D-079, hace nueve días.

De dónde salió `argus_app`: de `agents.yaml`, donde aparece en un `sum by
(argus_app, argus_feature, ...)`. Pero ahí es una etiqueta de **métrica**, que
viene de la telemetría. Una etiqueta de **alerta** es otra cosa. Confundí las dos
y no lo contrasté con quien las consume — el mismo error que con los nombres de
atributo de D-081, ahora en el otro extremo del sistema.

### Dos arreglos, no uno

**1 · Las etiquetas que el normalizador lee de verdad.** `service_namespace:
argus` y `service_name`. Y en las dos reglas que valen para **cualquiera** de los
dos Collector, el nivel se **deriva** en vez de fijarse:

```yaml
service_name: 'collector-{{ $labels.argus_collector_tier }}'
```

Poner uno a mano atribuiría al gateway un fallo del agente, que es peor que no
atribuirlo.

**2 · Los Collector, en el registro.** No estaban, así que ni con las etiquetas
buenas habría identidad que resolver. Entran con rol `infra`, y ese rol importa:
`ROLES_SIN_LATIDO` lo excluye de la sonda de silencio del canario. Su ausencia ya
la vigilan `ArgusAgenteSinAutoMetricas` y `ArgusGatewaySinAutoMetricas`, que además
saben distinguir «callado» de «el observador acaba de despertar». Dos vigilantes
sobre lo mismo son dos avisos por incidente.

**Verificado** mandando una alerta con las etiquetas nuevas al `/api/v2/alerts`
real:

```
app=argus  comp=collector-agent  sev=page
```

### Lo que esto dice del piloto

El criterio 2 —«un incidente real llega a una persona»— estaba en verde, y lo
está: un incidente de **una aplicación** llega. Lo que no llegaba era un incidente
de **la plataforma sobre sí misma**, que es una categoría que el criterio no
distingue.

No se puede cerrar un piloto de observabilidad el mismo día que descubres que las
alertas sobre la propia plataforma no despiertan a nadie.

---

## D-090 · Un reinicio borra el tablero de incidentes y nadie lo repuebla

> **Fallo de plataforma** · descubierto 2026-09-27 · los incidentes viven solo en memoria del alert-bus, y el canario no reenvía lo que ya reportó: tras reiniciar cualquiera de los dos, un problema abierto desaparece sin haberse resuelto

Fui a verificar el criterio 4 —«el silencio de un servicio real abre incidente»—
con un caso que se dio solo: los tres servicios de Prometheus llevaban **entre 24
y 38 horas sin emitir**. La sonda debía estar gritando. No había ni un incidente.

### Lo que sí funciona, comprobado paso a paso

No es la sonda. Lo verifiqué de abajo arriba:

| | |
|---|---|
| ¿corren las sondas? | sí — 10 POST a ClickHouse en dos ciclos, 200 OK |
| ¿recargó el registro? | sí — `canary.probes_reloaded` |
| ¿qué devuelve la consulta para un servicio mudo? | **`0`** |
| ¿y con 0? | `actividad <= 0` → `Resultado.SILENCIO` |

La sonda detecta el silencio correctamente. El corte está **después**.

### El corte: dos memorias, ninguna persistencia

```python
# canary/runner.py
if objetivo in self._alertados:
    continue        # ya alertado; no se reenvia
```

```python
# alert_bus/engine.py
self._incidents: dict[str, Incident] = {}   # huella -> incidente abierto
```

Las dos son estructuras **en memoria**. Y encajan mal:

1. El canario reporta el silencio una vez y apunta el objetivo en `_alertados`.
2. El alert-bus crea el incidente y lo guarda en un `dict`.
3. **Se reinicia el alert-bus** → el `dict` se vacía.
4. El canario sigue viendo el problema, pero como está en `_alertados`, **no
   reenvía nada**.

Resultado: el problema sigue ahí, el tablero de incidentes está vacío, y las dos
mitades creen estar en lo correcto. La única salida es que el servicio se
recupere y vuelva a fallar.

Lo descubrí porque **yo mismo lo provoqué**: reinicié el alert-bus para cargar el
registro nuevo de D-089 y con eso borré la evidencia que estaba buscando. La
lección es doble — el fallo existe, y por poco no lo veo porque lo causé.

### Por qué cada mitad tiene razón por separado

Ninguna de las dos decisiones es tonta:

- Que el canario no reenvíe cada cinco minutos para siempre es correcto: el
  comentario en el código lo dice y es cierto.
- Que el alert-bus guarde en memoria fue deliberado en la Fase 2: sin un almacén
  de incidentes, arrancar era mucho más simple.

Lo que falla es la **junta**: el dedup del emisor asume que el receptor no olvida,
y el receptor olvida en cada despliegue.

### Qué se hace, y qué no se hace ahora

**No se arregla con un `dict` más grande ni quitando el dedup del canario.** Sin
dedup, un servicio caído genera un aviso cada cinco minutos para siempre, que es
la fatiga de alertas que el propio plan señala como el problema dominante.

La forma correcta es que el emisor **reconcilie** en vez de recordar: el canario
manda su estado actual completo cada ciclo y el alert-bus decide qué es nuevo.
Es el modelo de Alertmanager y de Kubernetes, y hace irrelevante quién se
reinicie.

Queda como `B-20`, y con ella la persistencia de incidentes, que ya estaba abierta
como `F2-15`. Las dos son la misma pieza y conviene hacerlas juntas.

**Mitigación mientras tanto**, escrita en el runbook porque no es obvia: tras
reiniciar el alert-bus, el tablero está vacío y **no refleja la realidad**. Para
repoblarlo hay que reiniciar también el canario, que es lo que limpia
`_alertados`.

### Lo que esto le hace al criterio 4

Se queda en «?» y con una razón mejor que «verificado a mano el 13/09»: la
detección está probada, la **entrega sostenida** no. Un incidente que desaparece
sin resolverse es exactamente el silencio que parece salud.

---

## D-091 · vmalert dice la verdad sobre sí mismo y escribe 102 minutos en el pasado

> **Fallo de plataforma** · descubierto 2026-09-27 · el desfase de D-087 no era el reloj de evaluación, vuelve solo a las pocas horas, y la comprobación que escribí esta mañana no lo caza

D-087 dijo que vmalert evaluaba con un reloj atrasado y que reiniciarlo lo
arreglaba. **Cuatro horas después estaba roto otra vez, y el diagnóstico no
encaja.**

### Lo que se midió, y lo que descarta cada dato

| medida | valor | qué descarta |
|---|---|---|
| reloj del host, de vmalert y de VictoriaMetrics | **idénticos** | no es desincronización de relojes |
| `lastEvaluation` de vmalert | **al segundo** | no es que deje de evaluar |
| `lastSamples` de cada regla de grabación | **1** | no es que las reglas no produzcan nada |
| `vmalert_remotewrite_errors_total` / `dropped_rows` | **0 / 0** | no es que falle al escribir |
| `sent_rows_total` | **avanza** | no está atascado |
| duración de iteración del grupo | **24 ms** con intervalo de 15 s | no hay *overrun* en operación normal |
| marca de la última muestra en el almacén | **102 min en el pasado** | ← el síntoma |

**Todo lo que vmalert dice de sí mismo es cierto, y lo que escribe es
inservible.** La serie existe en `/api/v1/export` y está ausente en
`/api/v1/query`, porque cae fuera de la ventana que mira una consulta a `now`.

### Hipótesis probada y descartada

Parecía que un fallo del notificador bloqueaba la iteración y desplazaba el
calendario del grupo: había un `failed to send alerts: context deadline
exceeded` a las 20:25:58, justo cuando se pararon las escrituras.

**No cuadra con los números.** En las cinco horas de vida de esa instancia hubo
**tres** errores de notificación. Tres bloqueos no producen 102 minutos de
desfase salvo que cada uno costara media hora, y el timeout no es de media hora.
La correlación temporal era casualidad.

### Lo que sí está caracterizado

- El desfase **se acumula**: ~1 min recién arrancado, ~102 min a las cinco horas.
- Un **reinicio lo resetea**: medido inmediatamente después, 0,9 min y estable
  durante dos minutos de observación.
- Afecta a **todas** las reglas de grabación, no solo al latido: ni
  `argus:error_ratio:*` ni `argus:host_cpu_busy:ratio5m` son consultables.
- No depende de errores: crece con `vmalert_execution_errors_total = 0`.

### La causa: el desfase ES el tiempo que la máquina ha dormido

Y la caracterización de arriba fue justo lo que permitió encontrarla. «Se acumula,
un reinicio lo resetea, no depende de errores» describe un contador que solo
avanza mientras algo está parado.

```
ventana medida       13:03 -> 17:06 local  (243 min de reloj)
episodios de sueño   26
tiempo dormido total 6096 s = 101,6 min
desfase observado           101,7 min
```

**Seis segundos de diferencia en cuatro horas, sobre 26 episodios.** No es
coincidencia.

Esta MacBook entra en *Maintenance Sleep* continuamente —26 veces en cuatro
horas, el 42% del tiempo— y vmalert programa sus evaluaciones con un reloj
**monótono**, que no avanza mientras la máquina duerme. La marca con la que
escribe cada muestra se deriva de ese calendario, así que cada segundo dormido
desplaza su línea temporal un segundo hacia atrás, para siempre.

Eso explica **todo** lo observado, sin dejar nada suelto:

| observación | por qué |
|---|---|
| `lastEvaluation` correcto | lo reporta con la hora de pared |
| la muestra escrita, atrasada | la marca sale del calendario monótono |
| se acumula | cada sueño suma |
| un reinicio lo resetea | el ticker arranca de cero |
| no correlaciona con errores | no tiene nada que ver con errores |
| afecta solo a lo que escribe vmalert | el Collector sella con hora de pared |
| los 5,4 días de D-087 | una semana de portátil durmiendo |

Y cierra el círculo con lo de esta mañana: mi primer instinto —«esto tiene que ver
con que la máquina duerme»— **era correcto**, y lo descarté porque el mecanismo
que había imaginado (un falso positivo al despertar) no era el real. Tenía razón
sobre la causa y me equivoqué sobre el cómo, así que la abandoné entera.

### Qué se hace con esto

`B-21` deja de ser «investigar» y pasa a ser una decisión con dos opciones:

1. **Que vmalert no acumule**: es comportamiento suyo, así que o hay una opción
   que no hemos encontrado, o es un informe aguas arriba.
2. **Reiniciarlo cuando se detecte**: el vigilante ya lo detecta con
   `comprobar_latido_en_el_almacen()`. Curarlo automáticamente es un paso más, y
   el runbook ya advierte de no ponerlo en bucle sin entender la causa — que
   ahora sí se entiende.

Lo que **no** se hace es tratarlo como un misterio: está medido y explicado.

### Corrección: una de las mediciones estaba contaminada

Al seguir investigando medí otras rutas con `/api/v1/export` **sin rango** y salió
esto:

```
otelcol_exporter_sent_spans_total    224 min de atraso
traces_span_metrics_calls_total      350 min
system_cpu_load_average_1m         21491 min  (15 dias)
```

Estuve a punto de concluir que el atraso era de toda la plataforma y no de
vmalert. **Es falso, y el error es de medición**: `export` sin `start`/`end`
trunca, así que la «última» marca es la última de un trozo antiguo. Con rango
explícito, esas mismas series tienen **0,2 minutos** de atraso.

Lo que sí es válido de lo medido antes:

| medida | ¿de fiar? |
|---|---|
| `query` a `now()` devolviendo **0 series** para las reglas de grabación | **sí** — no depende de `export` |
| `export` del latido (604 y 809 muestras) | **sí** — por debajo del truncado, y corroborado por el `query` vacío |
| `export` de series grandes sin rango | **no** — es el artefacto |

Así que el alcance correcto es el que ya estaba escrito arriba: **afecta a las
reglas de grabación que escribe vmalert**, no a las métricas que empuja el
Collector. Esas llegan frescas.

**La trampa, para no repetirla**: en VictoriaMetrics, `export` sin ventana no es
«dame todo». Para preguntar «¿cuándo fue la última muestra?» hay que dar rango, o
mejor usar `query` a `now()`, que es lo que hacen las reglas de verdad.

### El daño, que es peor de lo que parecía esta mañana

Cuando el desfase crece, las series grabadas desaparecen de `now`. Y eso incluye
`argus:observador_despierto`, **que es la puerta que yo mismo puse esta mañana
para que las alertas de ausencia no dieran falsos positivos**.

Puerta cerrada = las alertas no pueden disparar. Así que el arreglo de D-087
convierte este fallo en un apagado silencioso de la vigilancia del Collector.
Ahora mismo el agente está genuinamente ausente y no hay nada mirándolo.

Es el acoplamiento que la puerta pretendía evitar, introducido al construirla.

### Y mi comprobación de esta mañana no lo caza

`comprobar_frescura_de_reglas()` mira lo que vmalert **dice de sí mismo**. Con
este fallo vmalert dice la verdad: «evaluando, atraso de 1 s». La comprobación da
verde mientras la plataforma está ciega.

Un componente puede informar correctamente de su salud y producir basura.

Por eso el vigilante gana `comprobar_latido_en_el_almacen()`, que pregunta al
**otro extremo**: ¿está `argus:observador_despierto` en VictoriaMetrics a esta
hora? Vale 1 siempre y no depende de que haya tráfico, así que su ausencia solo
puede significar que la cadena está rota — y da igual dónde.

**Prueba la cadena, no un eslabón.** Es la diferencia entre preguntarle a alguien
si está trabajando y mirar si el trabajo está hecho.

Comprobado en sus tres estados: al día (0 s), almacén inalcanzable, y serie
ausente.

---

## D-092 · El objeto de la acción se llama `argus.target.type` + `argus.target.id`, y buscándole nombre salió un agujero de seudonimización

> **Fallo de plataforma** · descubierto 2026-09-27 · `transform/pseudonymize` no cubría el ámbito `spanevent`, así que un identificador de persona en un evento de auditoría llegaba en crudo al almacén; y OTTL dejaba la sal en claro en los logs del Collector

**Contexto**: en `P-32` Prometheus pide nombre para el único atributo que les
queda bajo su namespace: **sobre qué** se hizo el cambio administrativo. Lo
emiten como JSON de los parámetros de ruta y ofrecen dos formas:

```
target  {"node_id": "8ed68951-d6cb-44f8-9335-6ddba3dde1fc"}     ← lo que hay
        argus.target.type=node + argus.target.id=8ed68951…      ← lo que ofrecen
```

### La decisión: el par plano, por su propio argumento

Ellos defendieron —y con razón— que `http.route` va separado del path resuelto
porque, junto, cada id es una acción distinta y no se puede contar nada. **El
mismo argumento, un nivel más abajo, decide entre sus dos opciones**: en un JSON,
tipo e identificador vuelven a ser un solo valor.

| | cardinalidad | para qué sirve |
|---|---|---|
| `argus.target.type` | cerrada (`node`, `client`, `api_key`, `user`) | **agrupar**: «cuántas desactivaciones de nodo» |
| `argus.target.id` | abierta, opaca | **filtrar** un objeto concreto; nunca agrupar |

El JSON además obliga a conocer la forma por ruta —`node_id` aquí, `client_id`
allá— así que una consulta sobre «el objeto» tendría que enumerar las rutas. Es
el problema de la plantilla, movido de sitio.

### Y buscándole nombre apareció lo importante

La pregunta «¿y si el objeto es una persona?» llevó a comprobar qué le pasa a un
identificador que viaja en un **evento de span**. Medido contra ClickHouse:

```
span    user.id  ->  enduser.pseudo.id = 4a573e50…     correcto
evento  user.id  ->  usuario-en-el-evento              EN CRUDO
```

`transform/pseudonymize` cubría `context: span` y `context: log`. **Los atributos
de un evento de span son un ámbito OTTL distinto (`spanevent`) y no los tocaba
nadie.**

Importa aquí y no en abstracto porque el registro de auditoría de Prometheus vive
exactamente ahí: `P-32 §2` cuenta que arreglaron el evento para que cuelgue del
span de servidor, con el actor en los atributos **del evento**.

Lo que les dijimos en `A-32` era falso justo donde ellos lo aplican:

| les dijimos | de verdad, en el evento |
|---|---|
| «mandad `user.id` en crudo, el gateway lo convierte» | no lo convertía: llegaba en crudo al almacén |
| «consultad `enduser.pseudo.id` para el actor» | el campo no existía en el evento |
| «el correo se borra en el gateway» | **media verdad**: el `redaction` sí recorre los eventos y lo dejaba en `****`, pero la clave sobrevivía |

El correo nunca llegó legible: la capa 2 lo salvó.

**Y el identificador tampoco, medido antes de escribirlo.** En treinta días de
almacén hay **un** evento `audit.admin_action` de Prometheus —el login fallido de
su verificación— y venía **sin** `user.id`, porque no había claims. Cero
identificadores en crudo.

La puerta estuvo abierta y no pasó nadie, y el motivo es incómodo: no pasó nadie
porque el fallo que ellos confiesan en `P-32 §0` —el gateway sin exportar—
impidió que su auditoría llegara. **Su fallo tapó el nuestro** durante todo el
tiempo que ambos estuvieron vivos, y los dos se arreglaron el mismo día.

Que no haya daño no cambia nada del arreglo: lo que se corrige es una regla
falsa, no un incidente.

### El mismo agujero un nivel más abajo, y por eso el tipo decide

`argus.target.id` es opaco por definición —salvo cuando lo que se administra es
un usuario—. `/admin/users/{user_id}/disable` metería un identificador de persona
en un atributo que ninguna regla del actor mira. De ahí que la regla se
condicione por el tipo, que es el único campo que sabe si el id de al lado es una
cosa o alguien:

```
set(spanevent.attributes["argus.target.id"], SHA256(...))
  where spanevent.attributes["argus.target.type"] == "user"
```

Verificado con dos eventos de auditoría en la misma traza:

| objeto | `argus.target.id` en ClickHouse |
|---|---|
| `node` | `8ed68951-…` en claro — es un objeto |
| `user` | `78cbcb77…` hasheado, y distinto del hash del actor |

### De paso: la sal estaba en los logs del Collector

OTTL acepta `attributes[...]` sin prefijo de ámbito, lo reescribe solo, y **al
hacerlo registra la sentencia reescrita** — con `${env:ARGUS_PSEUDONYM_SALT}` ya
expandido. La sal llevaba en claro en los logs del contenedor desde que existe la
seudonimización, once veces.

No llegó a ClickHouse porque el agente no tiene receptor `filelog`. Pero el plan
contempla añadirlo, y ese día sal y hashes acabarían en el mismo almacén, que es
literalmente lo que la sal existe para impedir. Todas las rutas llevan ya su
prefijo (`span.`, `spanevent.`, `log.`) y el aviso no vuelve a emitirse.

### El guardarraíl, que es lo que faltaba de verdad

**La configuración del Collector no la leía ninguna prueba.** Es donde vive la
privacidad entera y solo se comprobaba mirando ClickHouse a mano.

`platform/tests/test_seudonimizacion.py` recorre `transform/pseudonymize` y exige,
para cada ámbito con atributos, que las cuatro grafías de identificador se
conviertan **y** se borren, que el correo se borre, que la regla del objeto-persona
esté, que ningún hash vaya sin sal, y que ninguna ruta vaya sin prefijo.

Comprobado que falla sin el arreglo: 5 de 11 en rojo, incluida la que nombra el
ámbito que faltaba. `platform/tests` añadido a `testpaths` — la lección de D-088
es que una prueba fuera de la lista es un fichero de texto.

### El patrón, que ya va tres veces

Una regla escrita para la telemetría que uno **imagina**, no para la que **llega**:

- D-086: `user.id` cubierto, `user_id` y `jwt.subject` no — y Prometheus mandaba ésos
- D-089: etiquetas `argus_app`/`argus_component` que el normalizador no lee
- D-092: ámbito `span` cubierto, `spanevent` no — y el evento de auditoría vive ahí

Las tres se descubrieron mirando el otro extremo, ninguna leyendo la configuración.
Ésta es la primera que deja una prueba detrás.

**Publicado `1.0.0a8`** con `ARGUS_TARGET_TYPE` y `ARGUS_TARGET_ID`, para que
Prometheus los importe en vez de reteclearlos, como ya hacen con
`ARGUS_ACTOR_KIND_VALUES`.

---

## D-093 · Un test intermitente que era un fallo del código: `drain()` no contaba el envío en vuelo

> **Fallo de plataforma** · descubierto 2026-09-27 · `drain()` daba por drenado el reparto mientras un canal seguía entregando, así que en el apagado `stop()` podía matar una notificación a medio entregar sin contarla como fallida

Sale de lo de D-092 y se cuenta aparte, porque es un fallo distinto y en otro
componente. Al correr la suite completa tras lo anterior falló
`test_un_canal_caido_no_impide_que_los_demas_reciban`, y volvió a pasar al
repetirla. La tentación evidente era subirle el plazo y seguir.

No era el test. `drain()` consideraba «no queda trabajo» cuando la cola estaba
vacía y no había reintentos programados, y **se dejaba fuera el envío en vuelo**:
`_cola.empty()` es cierto desde el instante del `get()`, no desde que el envío
acaba. Entre sacarlo y entregarlo cabe una llamada de red entera.

Con un canal caído —que es cuando más tarda— la ventana es de segundos. Y lo que
provoca en el apagado es el fallo exacto que este despachador existe para
impedir: `drain` devuelve, `stop` mata los hilos, y la notificación que se estaba
entregando se pierde **sin contarse como fallida** (D-080).

`unfinished_tasks` cubre cola y vuelo a la vez, porque sube en el `put` y solo
baja en el `task_done`, que va en un `finally`. Un contador aparte tendría su
propia carrera entre el `get` y el incremento.

La prueba nueva no usa relojes —el canal se bloquea en un `Event` que suelta el
test— por dos motivos: la fixture del fichero anula `time.sleep`, y un fallo que
depende de lo cargada que esté la máquina es el que acabas subiéndole el plazo.
Comprobado que falla sin el arreglo.

**Un test intermitente es una hipótesis sobre el código, no una molestia.**

---

## D-094 · Prosodia se integró bien y nosotros la teníamos muda: `provisional` sin canales no avisa a nadie

> **Fallo de plataforma** · descubierto 2026-09-28 · siete aplicaciones `activo` —Prosodia y seis de infraestructura, incluida ClickHouse con criticidad `alta` y sonda HTTP— tenían `canales` vacío, así que `channels_for` caía al `["console"]` por defecto y su aviso moría en el log del alert-bus

**Contexto**: Prosodia cierra `S-06` diciendo que la integración está completa.
Lo comprobamos contra nuestro almacén antes de darlo por bueno, y sus tres
afirmaciones son ciertas. Lo que no era cierto es que la integración estuviera
completa **de nuestro lado**.

### Lo suyo: verificado, y con mejor evidencia de la que ellos tenían

| lo que afirman | medido |
|---|---|
| una sola traza, tres spans, API → worker por Celery | ✅ `5371c056…`, 3 spans, 1 raíz, 2 servicios |
| 462,9 s y sin error | ✅ 462 969,4 ms, todos los spans `Unset` |
| la conserva `async-handoff`, no otra política | ✅ **por contador, no por deducción** |
| 35 de 39 registros correlacionados | ✅ 61 de 67 sumando las dos corridas |

Su argumento de por qué `async-handoff` era la única política aplicable es
correcto, pero por una razón más fina que la que dan. Escriben que `slow` no
aplica *«porque la raíz dura 0 s»*. `slow` está en **3 s** y el span del worker
duró **463 s**: habría encajado de sobra. No encajó porque **a los 30 segundos
ese span todavía no existía**, y la política solo ve lo que hay en la ventana.

Es exactamente la asimetría que hace falta `async-handoff`, así que el
razonamiento llega al sitio correcto por el camino de al lado.

Y no hace falta deducirlo, porque el muestreador lo cuenta:

```
async-handoff              sampled=true    2     ← las dos corridas de doblaje
early_releases_from_cache  sampled=true    2     ← los dos spans tardíos del worker
slow                       sampled=true  943     ← ninguna de las dos
```

### Lo nuestro, primero: Prosodia no avisaba a nadie

Entraba por auto-descubrimiento como `provisional`, y eso la dejaba **muda dos
veces**:

- `provisional` fuerza `criticidad: media`, cuyo techo es `ticket`;
- sin `canales` declarados, `channels_for` devuelve `["console"]`.

La consola es el log del propio alert-bus. Un fallo de un doblaje se detectaba,
se normalizaba, se correlacionaba y se escribía en un fichero.

Es el modo de fallo de **D-089** con otra causa: allí las etiquetas impedían
resolver la identidad, aquí la identidad resuelve bien y no hay destino. En los
dos casos la detección funcionaba y la entrega no.

### Y no era solo Prosodia

Al listar el enrutamiento de las doce aplicaciones salieron **seis más**:

| aplicación | criticidad | iba a |
|---|---|---|
| `clickhouse` | **alta** | `["console"]` |
| `postgres-main` | **alta** | `["console"]` |
| `victoriametrics`, `redis-main`, `minio-main`, `temporal` | media | `["console"]` |

`clickhouse` y `victoriametrics` **tienen sonda HTTP** en `probes.yaml`. La
caída del almacén —que deja la plataforma entera sin datos— no avisaba a nadie.

No fue descuido de nadie en concreto: el bloque de infraestructura se añadió
*«para que `depende_de` tenga grafo y para poder sondearlas»*, y el
enrutamiento no entró en esa conversación. Lo que convierte el olvido en fallo
es que **el valor por defecto es silencioso**.

### La distinción que faltaba en el modelo: `activo` significaba dos cosas

Al declarar Prosodia `activo` —necesario para que sus avisos salgan de la
consola— se encendió su sonda de silencio. Y Prosodia **está callada la mayor
parte del día**, porque es un pipeline que alguien lanza: al comprobarlo llevaba
56 minutos sin emitir, con todo funcionando.

`activo` significaba a la vez *«emite hoy»* y *«su silencio es un incidente»*.
Valen juntas para una API que corre como servicio; no valen para un trabajo bajo
demanda. Y el registro ya avisa, en su propia cabecera, de lo que pasa cuando se
confunden: *un canario que grita por cosas que sabes es un canario que se
silencia*.

Se separan en dos ejes: `estado` decide si está integrada, `latido` por
componente decide si su silencio es un incidente. Prosodia va `activo` con
`latido: false`.

Lo que **sí** habría que vigilar en un caso así es un doblaje que empieza y no
termina. Es otra sonda y no existe: anotada como **F2-16**.

### Lo que casi me hace dar por bueno un arreglo que no corría

Tras el cambio, el canario seguía sondeando `prosodia/prosodia-worker`. El
registro es datos montados y se recargó solo; el código va **dentro de la
imagen**, y `docker restart` reejecuta la misma. Las pruebas pasaban y el
contenedor corría lo de antes.

Es el patrón de **D-090** —la copia instalada del vigilante, once días
obsoleta—, en otro componente y el mismo día de la semana. Reconstruida la
imagen:

```
sondas cargadas   11 -> 9     (las dos de prosodia, fuera)
menciones a prosodia en el ciclo    2 -> 0
```

**Una prueba verde dice que el código es correcto, no que sea el que corre.**

### El guardarraíl

`platform/tests/test_enrutamiento.py` exige que toda aplicación `activo` tenga,
para el techo de severidad que su criticidad permite, al menos un canal que no
sea la consola; que declare canales para ese techo aunque hoy no lo alcance; y
que `latido` sea booleano —un `latido: "false"` en YAML es una cadena verdadera y
la sonda se encendería igual—.

Comprobado que falla sin el arreglo: **12 de 17 en rojo**.

### Lo que esto dice del piloto

De los tres criterios que quedaban abiertos, éste no era ninguno. Prosodia hizo
su parte y la hizo bien; el hueco estaba en el lado que ya dábamos por hecho.

Y el hueco no era de detección —de eso va casi todo lo construido— sino de
**entrega**, que es el tercer fallo seguido de la misma familia: D-080 (la
notificación no se reintentaba), D-089 (no resolvía identidad), D-094 (no tiene
destino). La detección está mucho mejor probada que el camino que va de la
detección a una persona.

---

## D-095 · El contexto no cruza a otro hilo, y eso rompe los logs sin romper la traza

> **Fallo de plataforma** · descubierto 2026-09-28 · el SDK no ofrecía forma de cruzar la frontera de hilo ni la de subproceso, así que los registros emitidos desde un `ThreadPoolExecutor` salían con `trace_id = none` y nadie tenía manera de arreglarlo sin escribirlo a mano

**Contexto**: al verificar el cierre de Prosodia (`S-06`, D-094) quedaron cuatro
registros sin correlacionar. Ellos los dieron por resto — *«35 de 39»*—. Al
ordenarlos por tiempo el patrón era inequívoco:

```
11:25:17.655  SIN TRAZA   mlx_whisper.detected_language
11:25:17.656  ok          pipeline.stage_finished   stage=transcription
11:25:31.549  SIN TRAZA   diarization.done
11:25:31.549  ok          pipeline.stage_finished   stage=diarization
```

**Un milisegundo de diferencia y uno lleva traza y el otro no.**

### La causa, reproducida antes de arreglar nada

El contexto de OTel vive en un `contextvars.ContextVar`. `asyncio` copia el
contexto al crear una tarea; **`ThreadPoolExecutor` no lo copia al hilo
trabajador**. Por eso el pipeline —asíncrono— correlacionaba y solo fallaba lo
que pasaba por el pool:

```
en el hilo del pipeline : a0b137c2daae675344d461d8b3ebc27b
dentro del executor     : none
```

Es la misma familia que Celery o Kafka, con la frontera más barata de todas: ni
una máquina, ni un proceso, un hilo.

### Por qué no es cosmético

Los cuatro registros son de **transcripción y diarización**, las dos etapas más
lentas de un doblaje. Los registros que explican *por qué tardó tanto* son justo
los que se quedan sin traza — y responder eso era la justificación del piloto
entero (`A-01`).

Y el fallo es invisible por construcción: no hay error, no falta ningún dato en
la traza, solo unos logs que no se pueden saltar a su span. Se ve si vas a mirar
**cuáles** son los huérfanos, no cuántos.

### El arreglo es del SDK, no de su repositorio

`argus/propagate.py` cubría cabeceras, payloads, Celery y CLIs, y **no cubría ni el
hilo ni el subproceso**. Ese hueco es nuestro: sin él, cada equipo que use un
pool tiene que escribir el `copy_context()` a mano y acordarse en cada sitio.

| pieza | frontera | cómo se usa |
|---|---|---|
| `propagate.Executor` | otro hilo | sustituye a `ThreadPoolExecutor`; `submit` y `map` propagan solos |
| `propagate.with_context(fn)` | otro hilo | para un pool que ya existe y no se puede cambiar |
| `propagate.inject_env` / `extract_env` | otro **proceso** | `traceparent` por variables de entorno |

`Executor` es un **reemplazo y no un ayudante** a propósito. Un ayudante hay que
recordarlo en cada llamada, y el olvido no da error: solo un log huérfano que
nadie mira hasta que hace falta. Es el principio de «difícil de usar mal» del
contrato del SDK, aplicado al fallo que acabamos de ver.

`with_context` captura al **envolver**, no al ejecutar. Si capturara al
ejecutar se ataría al contexto del hilo trabajador —que no tiene ninguno— y no
arreglaría nada. Hay una prueba solo para eso.

### Verificado en un proceso de verdad

El subproceso no se simula pasando un diccionario: lo que hay que comprobar es
que la cadena sobrevive al `execve`.

```
padre  traza: f311093c78a37417723609cd66e0219b
TRACEPARENT : 00-f311093c78a37417723609cd66e0219b-00eaf127fe2e80d4-03
hijo   traza: f311093c78a37417723609cd66e0219b
```

### La prueba que fija el FALLO, no solo el arreglo

`test_el_executor_de_la_libreria_estandar_pierde_la_traza` afirma que
`ThreadPoolExecutor` devuelve `none`. Si algún día `concurrent.futures` propaga
contexto por su cuenta, esa prueba se pone roja y avisa de que
`propagate.Executor` ya no hace falta — en vez de quedarse para siempre
resolviendo un problema que dejó de existir.

Publicado en `1.0.0a9`. El cambio en el pipeline de Prosodia es suyo y es una
línea: cambiar la clase del pool.

---

## D-096 · Aeon se integra, y tres de nuestros artefactos afirmaban cosas que el código no hacía

> **Fallo de plataforma** · descubierto 2026-09-28 · `argus.outcome` estaba declarado `enum` en el modelo y `Step.outcome()` aceptaba cualquier cadena; el modelo daba como ejemplo de `argus.guardrail` un `cost-per-run` que el código no emite; y que `ARGUS_PROPAGATE=never` no afecta a la inyección saliente no lo fijaba ninguna prueba

**Contexto**: Aeon (`aeon-ai`) abre canal ya integrado —plano de control en Go,
workers Python sobre Temporal— y con la integración verificada A/B contra el
stack real. Traen tres modos de fallo silenciosos, una autocrítica y cuatro
preguntas.

Su marco, que conviene citar porque es el que encontró lo nuestro: *«un
artefacto que afirma algo que el código no hace»*, y *«las seis se encontraron
ejecutando, ninguna leyendo»*.

Aplicado a nosotros, encontró tres.

### 1 · `argus.outcome` era un enum que no lo era

Declarado `enum` en el modelo desde el principio, con `ARGUS_OUTCOME_VALUES`
generado y todo. `Step.outcome(value)` aceptaba cualquier cadena sin mirar.

El coste no se ve, que es lo que lo hace caro: un valor inventado se escribe
igual de bien que uno bueno. Lo que desaparece es la capacidad de agregarlo,
que es la única razón por la que un campo se declara cerrado. Ahora avisa —no
levanta, porque el contrato es no tumbar la aplicación— y escribe el valor
igualmente.

Su pregunta era literalmente *«¿hay vocabulario definido?»*. La respuesta
honesta era «en el modelo sí, en el código no».

### 2 · El modelo daba un nombre de guardarraíl que no existe

`examples: ["cost-per-run", ...]`. El código emite `cost-budget`.

Aeon acertó **porque leyó `guardrails.py` y no el modelo**, y lo dicen ellos
mismos en su autocrítica. Si hubieran confiado en el sitio que parece la fuente
de verdad, habrían emitido un nombre que no casa con ninguna regla nuestra.

`argus.guardrail` pasa además de `string` con ejemplos a **enum cerrado**, que
es lo que el campo necesitaba para que `GROUP BY` signifique algo.

### 3 · La propiedad central de Aeon no la fijaba nada

Preguntan si `ARGUS_PROPAGATE=never` gobierna solo la entrada, porque su
propiedad central es que un run sea UNA traza cruzando Python → Go.

Lo gobierna solo la entrada: el modo de confianza vive entero en el middleware
ASGI, y la inyección saliente no lo consulta. Correcto — **y sin una sola
prueba que lo sujetara**. Los siete tests de confianza cubrían la entrada. El
día que alguien «endureciera» `never` para cubrir también la salida, todas las
trazas distribuidas del portafolio se partirían y los siete seguirían verdes.

Ahora hay una prueba por los tres modos.

### Qué se decide sobre sus cuatro nombres de guardarraíl

Proponen `policy-denied`, `approval-required`, `fan-out-budget`,
`destination-not-declared`, y preguntan si prefijarlos con `aeon.`.

**Sin prefijo.** El valor de este campo es agregarlo sobre todo el portafolio;
prefijar por equipo daría un cubo distinto por equipo para el mismo concepto, y
convertiría el vocabulario compartido en cuatro vocabularios privados. Es D-081
otra vez con otra ropa.

**Tres se adoptan. `approval-required` no, y el motivo es de arquitectura, no
de nomenclatura.** El Collector agente enruta a `traces/hot` cualquier span con
`argus.guardrail`:

```
attributes["argus.hot"] == true
or status.code == STATUS_CODE_ERROR
or attributes["argus.guardrail"] != nil
```

Poner el atributo **es** pedir una notificación en ~2 s. Una espera de
aprobación es la operación normal de L5, y marcarla así paginaría en cada
aprobación — la fatiga de alertas que la plataforma existe para evitar,
construida desde dentro.

El campo significa «este run se paró y hay que mirarlo ya», y eso no estaba
escrito en ningún sitio. Ahora sí.

### Y su pregunta 4 se resuelve con UN valor nuevo, no con cinco

Proponen `result` · `denied_by_policy` · `approval_granted` · `approval_denied`
· `approval_expired`. Cuatro caben ya:

| suyo | nuestro | por qué |
|---|---|---|
| `result` | `ok` | mismo hecho |
| `approval_granted` | `ok` | el paso siguió |
| `approval_expired` | `timeout` | una aprobación que caduca es un plazo agotado |
| `approval_denied` | **`denied`** | nuevo |
| `denied_by_policy` | **`denied`** | nuevo |

**`denied` es el que faltaba de verdad**, y es de ellos: un rechazo por política
no es un `error` —el sistema hizo lo correcto— ni un `cancelled`, que sugiere
que alguien se arrepintió. Sin él, una denegación se contaba como fallo y
ensuciaba cualquier tasa de error con decisiones correctas.

Lo que se pierde al colapsar es *quién* denegó. Se les pregunta antes de
inventar el nombre.

### Sus tres fallos silenciosos: dos se arreglan en el arranque

Los tres comparten forma —la aplicación funciona, el exportador reintenta de
fondo, no llega un span, y no hay ni excepción ni log— que es justo lo que
nadie mira en los minutos siguientes a montar el trazado.

| lo que reportan | qué se hace |
|---|---|
| `localhost` es cierto en el host y falso en un contenedor | aviso al arrancar si el endpoint es local **y** el proceso parece estar en un contenedor |
| protocolo autodetectado gRPC contra el puerto 4318 | aviso si el puerto es el del *otro* transporte OTLP |
| el SDK exporta también logs y métricas | documentado; el 404 en bucle es del exportador de OTel y no lo controlamos |

El aviso del puerto **solo** salta si el puerto es 4317 o 4318. La primera
versión avisaba con cualquier puerto distinto del esperado, y una prueba con
`:8080` lo cazó: un 8080 es un proxy o un sidecar, una elección deliberada. Un
aviso que salta cuando no debe se aprende a ignorar, y entonces no protege del
caso en que sí debe.

### Su pregunta 1, que es la que más trabajo trae: el modelo se publica

Cuatro de sus seis servicios son Go y hoy copian nuestros nombres a mano. Piden
*«un artefacto legible por máquina que podamos ejecutar contra nuestra
implementación»*.

`modelo.json` se genera desde el mismo YAML y **viaja dentro de la rueda de
`argus-obs-semconv`**, accesible con `argus_semconv.modelo()`. En JSON y no en
YAML porque la biblioteca estándar de Go trae uno y no el otro.

Dentro del paquete y no solo en el repositorio, que es la parte que importa:
**un fichero en la rama principal describe lo que habrá, no lo que corre.**

Publicado en `1.0.0a10`.

### Lo que esto dice de nosotros

Tres defectos, los tres de la misma familia, y ninguno lo habría encontrado yo
leyendo mi propio código —llevo días leyéndolo—. Los encontró un equipo que se
integró de verdad y preguntó por escrito qué significaba cada campo.

Es la tercera vez esta semana que el hallazgo llega de fuera: Prosodia encontró
el nivel de log y el muestreo asíncrono, Prometheus encontró que llevábamos
cinco días sin ver dos de sus servicios, y ahora esto.

**La documentación no se valida leyéndola.** Se valida cuando alguien la usa
para construir algo y vuelve a contarte en qué se equivocó.

---

## D-097 · No poner desenlace no deja el campo vacío: lo pone en `ok`

> **Fallo de plataforma** · descubierto 2026-09-29 · `Step._finalize()` hace `setdefault(argus.outcome, "ok")`, así que un paso que termina sin desenlace explícito —incluido uno suspendido esperando a una persona— queda registrado como completado con éxito, y eso no estaba escrito en ningún sitio

**Contexto**: Aeon corrige su propia pregunta 4 y plantea una nueva: cómo debe
reportar `argus.outcome` un paso suspendido a la espera de una persona. Su
propuesta es **no poner nada**, razonando que un paso que espera no ha acabado,
y su única duda es si la ausencia se leería como un hueco.

No hay ausencia que leer.

```python
def _finalize(self) -> None:
    self._fields.setdefault(A.ARGUS_OUTCOME, "ok")
```

**Cada espera de aprobación se habría registrado como un éxito.** Su
preocupación era que el pipeline confundiera «no consta» con «hueco»; lo que
pasa de verdad es peor y en la dirección contraria.

El `setdefault` es correcto y se queda: un paso que llega al final sin fallar es
un paso que fue bien. Lo que faltaba es que estuviera dicho, y un valor para el
caso que no encaja.

### `suspended`, y es el argumento de Prometheus reutilizado

Aeon propuso `degraded` como alternativa. No vale: `degraded` significa que
funcionó peor, no que está pendiente.

El valor nuevo es `suspended`, y la razón es la que Prometheus dio en `P-31`
para que `unknown` fuera un valor legítimo de `argus.actor.kind`: **en un
registro, «todavía no ha acabado» es un hecho**, y tiene que poder distinguirse
de «nadie lo puso» — y sobre todo, aquí, de «acabó bien».

Encaja además con la arquitectura que ya estaba escrita: en L5 el workflow
espera en `workflow.await` a un signal que puede tardar días (§8.6 del plan). Un
span abierto tres días no sirve para nada, así que el paso que espera **cierra**,
y su cierre necesita un nombre.

### Por qué esto es una versión aparte y no iba en `a10`

Porque me lo perdí. La corrección de Aeon estaba en el canal cuando empecé a
trabajar y la leí al final, al ir a responder. Todo `a10` se construyó sobre su
primera versión de las preguntas.

No cambió ninguna de las respuestas —la corrección de ellos y mi trabajo
apuntaban al mismo sitio— pero podría haberlo hecho, y entonces habría mandado
respuestas a preguntas retiradas.

**Releer el canal antes de empezar, no antes de contestar.** Un hilo compartido
con dos escritores cambia mientras trabajas, que es justamente para lo que
sirve.

---

## D-098 · El registro de auditoría se estaba muestreando al 10%, y el seudónimo tenía un punto ciego

> **Fallo de plataforma** · descubierto 2026-09-29 · ninguna política de tail sampling cubría las acciones administrativas, así que caían al `baseline` probabilístico; y la regla de seudonimización cubría `user` y no `client`, con lo que el mismo sujeto quedaba hasheado en una fila y en claro en otra

**Contexto**: Prometheus adopta `argus.target.*` (`P-33`), ejecuta **dos
acciones admin reales** y nos pide que confirmemos contra el almacén que el
seudónimo funciona sobre tráfico suyo.

Ninguna de las dos llegó.

```
c1fe6008a2cabe9d70a88d5c10dfc22f   0 spans
744c92ad7994e25e297206be944db1eb   0 spans
```

### Un registro de auditoría al 10% no es un registro de auditoría

Repasando las políticas contra lo que es un `PATCH` administrativo:

| política | ¿aplica? |
|---|---|
| `errors` | no — no falla |
| `slow` | no — milisegundos |
| `genai`, `agent-runs` | no |
| `async-handoff` | no — no toca cola |
| `marked-hot` | no |
| **`baseline`** | **sí — 10%** |

Dos acciones al 10% cada una: 81% de probabilidad de perder las dos. Lo que
observamos.

**El coste de equivocarse no es simétrico**, y ahí está el error de diseño. Una
traza normal perdida es una muestra menos de una distribución. Una acción
administrativa perdida es un hueco en el registro de quién hizo qué — y el
Anexo III del AI Act obliga a conservarlo seis meses.

Política `auditoria`, y de tipo `ottl_condition` porque el evento vive en el
ámbito `spanevent` y las políticas de atributo solo miran el span. **Es el
mismo ámbito que se escapó en D-092**, en otro procesador: la primera vez en la
privacidad, ésta en el muestreo.

Verificado con seis acciones rápidas y sin error, que antes habrían sobrevivido
~0,6 de media:

```
politica auditoria   sampled=true   6
trazas en el almacen                6/6
```

### El punto ciego de `client`, que es de ellos el hallazgo

En sus rutas el **mismo** `client_id` sale con dos tipos según el recurso:

```
/admin/api/users/{client_id}                      -> user     hasheado
/admin/api/billing/clients/{client_id}/settings   -> client   EN CLARO
```

Nuestra regla miraba solo `user`. El almacén acababa con **el hash de un sujeto
en una fila y su identificador en claro en otra**.

Y eso no es medio problema, es el problema entero: **un seudónimo vale lo que
vale el sitio menos protegido donde aparece ese sujeto.** La fila en claro deja
sin sentido a la hasheada sin necesidad de cruzar nada.

Lo peor es que `client` estaba **en nuestra propia lista de ejemplo** del
modelo (`node`, `client`, `api_key`, `user`). Escribimos el tipo y no lo
clasificamos.

### La clasificación es de la plataforma, no del emisor

Esta es la regla que faltaba, y generaliza:

**Basta con que un tipo pueda designar a una persona en UNA aplicación para que
haya que hashearlo en TODAS**, porque el almacén es compartido. Que en otra
plataforma `client` sea siempre una credencial de máquina no cambia nada: si en
la de Prometheus puede ser una persona, en nuestro almacén es dato personal.

Vive en el modelo como `ARGUS_TARGET_TYPE_PRINCIPALS = ("user", "client")`. El
OTTL del gateway no puede recorrer una lista, así que la enumera — y una prueba
comprueba que la enumeración **coincide exactamente** con el modelo. Añadir un
tipo principal sin tocar el gateway rompe el build.

Comprobado con el mismo sujeto bajo los tres tipos:

| tipo | en el almacén |
|---|---|
| `client` | `11bbe290…` |
| `user` | `11bbe290…` **el mismo hash** |
| `node` | `nodo-7`, en claro |

El mismo hash es la parte buena: agrupa como un solo sujeto sin que el almacén
sepa quién es, que era el objetivo original de la seudonimización.

### Y una cosa que les decimos y no es nuestra

Su `P-33 §5` arregla el `trace_id: "none"` de la línea de log de auditoría.
El arreglo es correcto y **hoy no tiene efecto observable**: no llega una sola
línea de log suya a nuestro almacén, en catorce días, bajo ningún nombre de
servicio.

Hipótesis, y es mitad nuestra: usan un `configure_logging` propio que
probablemente no instala el puente OTLP, y **nuestro Collector agente no tiene
receptor `filelog`**, así que de stdout no lo recoge nadie. Su registro de
auditoría existe, es correcto, y vive en el terminal de quien arrancó el
proceso.

### Y el proceso, otra vez

Escribí `A-35` sin haber leído `P-33`, que llevaba un día en el canal. Es
literalmente lo que documenté en D-097 hace una hora, en el otro canal.

La regla no basta con escribirla. Lo que la hace cumplirse es mirar el índice
del canal antes de empezar, no el final del fichero — `P-33` estaba en la tabla
y yo fui directo a la cola.

---

## D-099 · El consejo que le dimos a Aeon cambiaba una notificación de más por un registro incompleto al 90%

> **Fallo de plataforma** · descubierto 2026-09-29 · la política `auditoria` de D-098 cubría acciones administrativas y no los desenlaces de gobierno, así que una denegación emitida como `argus.outcome=denied` sin `argus.guardrail` —que es exactamente lo que les recomendamos— caía al `baseline` del 10%

**Contexto**: en la respuesta a Aeon razonamos que `argus.guardrail` enciende el
camino caliente, y que marcar así una denegación de política Cedar pagaría en
cada una. La recomendación fue:

> *«Empezad sin el atributo —solo `argus.outcome=denied`—, medimos la tasa una
> semana, y lo promovemos a guardrail si resulta raro e interesante.»*

El razonamiento sobre el ruido es correcto. La recomendación, medida, no:

```
8 denegaciones con solo `argus.outcome=denied`
conservadas por el muestreo:  0 / 8
```

Ninguna política aplicaba. Les habríamos pedido que **midieran una tasa sobre
un registro del que falta el 90%** — y peor: durante esa semana su registro de
denegaciones de política habría tenido huecos, sin que nadie lo notara.

### La asimetría que no vi al aconsejar

Es la misma de D-098 y no la apliqué al caso siguiente, una hora después.

**El ruido se nota y un registro con huecos no.** Una notificación de más la ve
una persona y se queja; un registro incompleto se descubre el día que hace
falta reconstruir qué pasó, que es justamente el día en que ya no se puede.

Así que la elección que le planteé a Aeon era falsa. No era «paginar o
esperar»: era «paginar, o perder el 90% del registro». La opción buena —
registro completo **sin** notificación — no existía y había que construirla.

### Qué se añade

`denied` y `suspended` a la política `auditoria`, en los dos ámbitos. Los dos
son desenlaces de **gobierno**: uno dice que el sistema se negó, el otro que un
run espera a una persona. Los dos son raros por construcción y los dos hay que
poder reconstruirlos enteros.

```
antes:   0 / 8 denegaciones conservadas
después: 8 / 8
```

Ahora la recomendación se sostiene: `argus.outcome=denied` sin
`argus.guardrail` da **registro completo y cero notificaciones**, que es lo que
queríamos ofrecer desde el principio.

### Y un fallo que me hice a mí mismo escribiéndolo

Al añadir las condiciones escribí un **segundo bloque `spanevent:`** en vez de
ampliar el que ya existía. YAML acepta claves duplicadas y se queda con la
última: el fichero seguía siendo válido, el Collector lo habría arrancado sin
una queja, y la condición del evento de auditoría —lo que D-098 arregló ayer—
habría desaparecido.

Se vio **imprimiendo el YAML parseado y no el texto**, que es la única forma de
ver una clave que ya no está.

`test_el_fichero_no_tiene_claves_duplicadas` carga el gateway entero con un
loader que rechaza duplicados, así que esto vale para cualquier bloque del
fichero y no solo para esta política.

**Editar configuración es programar sin compilador.** La única verificación
disponible es leer el resultado parseado, y hay que hacerla siempre.

---

## D-100 · El canario reconcilia en vez de recordar (cierra `B-20`)

**Contexto**: el fallo estaba descrito y pendiente desde `B-20`. El canario
reportaba un problema **una vez** y lo apuntaba en `_alertados`; el alert-bus
guardaba el incidente en un `dict` en memoria. Al reiniciar el alert-bus, su
tablero se vaciaba, el canario seguía viendo el problema y **no reenviaba
nada**. El problema abierto desaparecía sin haberse resuelto.

### Lo que había que cambiar era menos de lo que parecía

El receptor **ya reconciliaba**: `Engine.sweep()` cierra por `last_seen_at`, así
que una señal repetida mantiene vivo el incidente y su ausencia lo cierra. Lo
que faltaba era que el emisor la mandara.

Así que el arreglo es quitar una puerta, no construir un mecanismo:

```python
# antes
if objetivo in self._alertados:
    continue          # <- el problema

# ahora
if objetivo not in self._alertados:
    self._alertados.add(objetivo)
    self.stats["alertas"] += 1
else:
    self.stats["reenvios"] += 1
senales.append(self._a_senal(medicion))   # SIEMPRE
```

`_alertados` deja de ser una puerta y pasa a ser la marca de transición: sirve
para contar alertas nuevas aparte de reenvíos, y para detectar la recuperación.

### La premisa de la que dependía todo

Reenviar cada ciclo solo es correcto si **no produce ruido**, o el arreglo sería
peor que el fallo: sería exactamente la fatiga de alertas que el dedup del
canario evitaba.

Lo es, y por dos mecanismos que ya existían: `_absorb()` mete la señal repetida
en el incidente que ya está abierto, y `needs_notification` exige
`notified_at is None`. Hay una prueba solo para esa premisa, porque si algún día
deja de cumplirse el resto se convierte en un generador de spam.

### Verificado contra el sistema vivo, no solo en tests

Ejecutando el `Runner` real contra el alert-bus real, con una sonda a un puerto
muerto:

```
antes del reinicio          1 incidente del canario
justo tras el reinicio      0 — el tablero esta vacio
tras un ciclo del canario   1 — repoblado, sin tocar el canario
                            intelligent-document-platform/idp-ocr no responde
```

La tercera línea es lo que no pasaba. Y **se puede retirar del runbook la
mitigación** que decía que, tras reiniciar el alert-bus, hay que reiniciar
también el canario para repoblar el tablero.

### Una prueba verde que fijaba el fallo

`test_no_repite_la_alerta_mientras_siga_caido` afirmaba que un objetivo caído se
reporta **una sola vez**, con el argumento de que no tiene sentido molestar cada
cinco minutos para siempre.

El argumento es correcto. La conclusión no: **quien decide si molesta es el
receptor**. La prueba estaba verde, era razonable, y fijaba el comportamiento
que rompía la junta.

Sustituida por cuatro que fijan el contrato nuevo: se reenvía cada ciclo, el
reenvío no cuenta como alerta, la transición vuelve a contar si recae, y lo
recuperado deja de reenviarse — esta última importa porque seguir mandando algo
que ya funciona impediría al `sweep()` cerrarlo nunca.

### Lo que esto NO arregla, y hay que decirlo

Tras un reinicio el incidente es **nuevo** para el receptor, así que **vuelve a
notificar**. Es ruido, y es preferible al silencio.

Quitarlo necesita persistir los incidentes (`F2-15`), para que el receptor
reconozca el que ya tenía y conserve su `opened_at`, su acuse y el informe del
agente. **Reconciliar da la corrección; persistir dará la educación.** Hay una
prueba que fija el re-aviso como comportamiento esperado, para que el día que se
implemente la persistencia se vea que cambia.

### El criterio 4 del piloto

Pasa de «?» a comprobable: la entrega sostenida ya no depende de que nadie
reinicie nada. Lo que queda para darlo por bueno es verlo sobrevivir a un
reinicio real en la ventana de catorce días, no en una prueba provocada.

### Y un efecto que debí anticipar

La verificación contra el sistema vivo **mandó cuatro notificaciones reales por
Telegram**: la del incidente falso que provoqué y las que se re-notificaron al
repoblarse el tablero tras el reinicio.

Es la consecuencia directa de lo que acababa de escribir dos párrafos más
arriba —repoblar re-notifica— aplicada a mí mismo sin verla venir. Para probar
esto en el sistema vivo hace falta un modo que enrute a `console` sin tocar el
registro; hoy no existe y la única alternativa era no probarlo en vivo, que
habría sido peor.

Queda anotado como lo siguiente que hay que construir antes de la próxima
verificación de este tipo.

---

## D-101 · El tablero marcaba en rojo al vigilante justo cuando acababa de funcionar

> **Fallo de plataforma** · descubierto 2026-09-29 · `pilot_status` leía el código de salida 1 del dead man's switch como «está roto», cuando es lo que devuelve al **detectar** un objetivo caído; y el propio vigilante ignoraba en silencio las claves de configuración que no conoce

**Contexto**: al revisar el estado del piloto, el criterio 7 —*«hay red de
seguridad externa»*— salía en **NO**.

No estaba roto. Había detectado un problema real: `argus:observador_despierto`
no estaba en el almacén, o sea **D-091 otra vez** — la máquina durmió de
madrugada (904 s y 597 s en dos episodios) y vmalert volvió a escribir en el
pasado. El vigilante hizo exactamente aquello para lo que existe.

```
MAL  latido en el almacen   el latido de vmalert NO esta en el almacen a esta hora
1 objetivo(s) caido(s)
```

### Por qué es el peor sitio posible para este error

El criterio pregunta si **hay** red de seguridad, no si esa red no ha
encontrado nada. `launchctl list` da el código de la última ejecución, y el
contrato del vigilante es:

| código | significa |
|---|---|
| `0` | ejecutó y todo bien |
| `1` | ejecutó y **hay algo caído** — el vigilante funciona |
| `2+` | no pudo ejecutarse — el vigilante está roto |

Leer el 1 como rojo enseña a desconfiar del único componente cuya credibilidad
sostiene todo lo demás. Un tablero que marca en rojo al vigilante por
funcionar es peor que no tener tablero.

Y se aprende rápido: si el criterio 7 parpadea en rojo cada vez que el
vigilante encuentra algo, en dos semanas nadie lo mira.

### El contrato se fija en los dos lados

Una prueba **ejecuta** el vigilante contra un puerto muerto y exige salida 1;
otra exige que `pilot_status` acepte `("0", "1")` como sano. Un solo lado no
basta: el error estaba en que las dos mitades tenían ideas distintas de lo
mismo, que es el patrón de D-089 y D-100.

La prueba ejecuta en vez de leer la fuente a propósito — un `grep` de
`return 1` pasaría aunque esa rama fuera inalcanzable.

### Y escribiendo esa prueba salió lo otro

Puse `umbral_fallos` en la config de prueba. El nombre real es
`fallos_para_avisar`. **El vigilante ignoró la clave en silencio**, aplicó su
defecto de 2, detectó el objetivo caído, y salió con **0** — diciendo que todo
bien.

Tardé un rato en entender que lo roto era mi prueba y no el contrato, y ése es
justamente el coste: una configuración con una errata que se aplica a medias es
peor que una que no arranca.

Es el modo de fallo que llevo toda la semana arreglando en otros sitios —el
ámbito OTTL, las etiquetas de las alertas, el `filelog` que no existe— y aquí
duele más, porque este proceso existe para ser lo único creíble cuando lo demás
calla.

`revisar_config()` avisa de las claves que no entiende y **no impide arrancar**:
negarse a correr por una errata convertiría al vigilante en otra cosa que se
puede caer, y su premisa es correr siempre.

```
AVISO  claves de configuracion que no entiendo y se IGNORAN: umbral_fallos
       (conocidas: avisos, estado, fallos_para_avisar, latido, objetivos, reglas, timeout_s)
```

### Lo que queda abierto de B-21

El desfase de vmalert se resolvió reiniciándolo, como dice el runbook. Que la
máquina duerma va a seguir pasando, así que la decisión de `B-21` sigue en pie:
informar aguas arriba, o que el vigilante reinicie vmalert al detectarlo.

Hoy tiene un argumento más a favor de automatizarlo: **la detección funciona y
la reparación es un reinicio de un contenedor**, que es reversible y acotado.
Pero un vigilante que actúa deja de ser solo un observador, y eso merece
decidirse aparte y no de pasada.

---

## D-102 · El criterio 5 llevaba cumplido desde el 28 y el tablero decía que no

> **Fallo de plataforma** · descubierto 2026-09-29 · el criterio 5 del piloto era una frase escrita a mano —«los 3 servicios del piloto son APIs HTTP»— que dejó de ser cierta cuando Prosodia cerró `S-06`, y nadie la volvió a mirar

**Contexto**: revisando el estado del piloto, el criterio 5 —*«una traza cruza
una frontera que NO es HTTP»*— seguía en «?» con ese texto.

Es falso desde el 28 de septiembre. El doblaje real de Prosodia produce
exactamente eso: una traza, dos servicios, la frontera cruzada por Celery. Lo
verificamos nosotros mismos al revisar su `S-06`, y no actualizamos el tablero.

```
prosodia-api -> prosodia-worker por `celery` en una sola traza (5371c056ae37…)
```

### El patrón, que es el de toda la semana visto desde dentro

Aeon lo describió así: *«un artefacto que afirma algo que el código no hace»*.
Éste es la variante temporal: un artefacto que **era** cierto y dejó de serlo,
y que nadie revisa porque no falla nada cuando miente.

Tres criterios del piloto eran texto fijo con una nota y un código de tarea.
Uno de ellos llevaba un día mintiendo en la dirección **pesimista**, que es la
menos visible: un tablero que dice que algo no está hecho no molesta a nadie.

### Qué se hace

El criterio 5 pasa de frase a **consulta**. Pide lo que el criterio pide de
verdad: una traza, dos servicios, y al menos un span con
`messaging.destination` — o sea una frontera que no es HTTP **y** que no
partió la traza.

Devuelve `None` y no `False` cuando no puede consultar el almacén: sin datos no
se puede afirmar que no exista, y confundir «no lo sé» con «no» es el error de
fondo de toda esta entrada.

### Lo que queda escrito a mano, y por qué

Los criterios 4 y 6 siguen siendo texto:

- **6 · un segundo host** no se puede medir desde aquí porque no hay segundo
  host: la consulta daría siempre «no» y no aportaría nada sobre si el trabajo
  está hecho.
- **4 · el silencio abre incidente** ahora sí se podría medir, y es lo
  siguiente: con la reconciliación de `D-100` el incidente sobrevive a un
  reinicio, así que hay algo estable que consultar.

**Un criterio que se comprueba a mano se comprueba una vez.** Los tres que
están en verde se recalculan en cada ejecución; los que llevan nota no.

---

## D-103 · Los incidentes sobreviven al reinicio, y el criterio 4 deja de ser una nota (cierra `F2-15`)

**Contexto**: `D-100` hizo que el canario reconcilie, así que tras reiniciar el
alert-bus el tablero se repuebla solo. Quedó escrito allí lo que **no**
arreglaba: para el proceso recién arrancado el incidente es nuevo, así que
vuelve a notificar, olvida el acuse de recibo y tira el informe del agente.

*Reconciliar dio la corrección; persistir da la educación.*

### Dónde se escribe, que es la decisión de diseño

El motor guarda los incidentes en memoria a propósito: el camino caliente no
puede pagar un viaje a disco por señal. Eso sigue siendo cierto, y por eso
**absorber una señal repetida no escribe**: lo único que cambia es
`last_seen_at`, y perderlo no cuesta nada porque la siguiente señal lo repone.

Se escribe solo cuando cambia algo que **no se puede reconstruir desde el
flujo**: abrir, notificar, acusar recibo, enriquecer, resolver. Hay una prueba
que manda diez señales repetidas y exige **cero** escrituras.

### SQLite de la biblioteca estándar

El alert-bus es lo que avisa cuando algo se rompe. Darle una dependencia de red
sería compartir modos de fallo con lo que vigila — el mismo argumento por el
que el dead man's switch no importa nada.

Y un fallo del almacén no puede tumbar la detección: todo va en `try/except`
con aviso. **Se prefiere un alert-bus que detecta y no recuerda a uno que no
detecta.** Hay una prueba con una ruta inservible que lo comprueba.

### Verificado desplegando, y ahí apareció lo que los tests no podían ver

```
antes      id=5d9ca31d1f41  abierto=12:47:04  notificado=True
tras reiniciar el alert-bus, SIN reconciliar:  1 incidente, recuperado del almacen
despues    id=5d9ca31d1f41  abierto=12:47:04  notificado=True
notificaciones tras el reinicio: 0
```

Misma identidad, mismo `opened_at`, y **cero re-notificaciones** — que es
exactamente el ruido que `D-100` dejó abierto.

Pero el primer despliegue salió con `persistencia: False`:

```
store.unavailable  ruta=/var/lib/argus/incidentes.sqlite
                   error="unable to open database file"
```

El volumen nombrado nace propiedad de root y el proceso corre como `argus`.
**Los tests no podían verlo**: allí el almacén es un `tmp_path` del propio
usuario. Se arregla creando el directorio **en la imagen** con su dueño, porque
Docker copia esa propiedad al crear el volumen la primera vez.

Y el servicio degradó exactamente como está diseñado —detectando y sin
recordar— lo cual es lo correcto y también la razón de que hubiera pasado
desapercibido sin mirar `persistencia` en `/stats`.

### El criterio 4, que era lo que se venía a cerrar

Llevaba dos semanas y media en «?» con la nota *«verificado el 13/09 con
auth-service; no se re-comprueba solo»*. La nota era honesta: **no se podía**
re-comprobar. Su éxito es un EVENTO —el silencio de un servicio abrió un
incidente— y el estado actual no lo puede responder, porque lo deseable es que
ahora mismo no esté pasando.

Con el histórico ya hay algo que consultar. Y tiene tres respuestas, no dos:

| respuesta | cuándo |
|---|---|
| `OK` | hay silencios registrados en 30 días |
| `NO` | el registro lleva más de 24 h y no hay ninguno |
| `?` | el registro es más joven que 24 h |

La tercera es la que importa. **«No ha pasado» y «llevo cinco minutos mirando»
no son lo mismo**, y confundirlos es literalmente el error que D-102 documenta.
Ahora mismo dice *«el registro lleva 0.0 h: aún no es evidencia de ausencia»*,
que es la verdad.

### Dos veces el mismo fallo en una sesión

Al añadir el volumen escribí un segundo bloque `volumes:` en `compose.yaml`
sobre un servicio que ya tenía uno. YAML se queda con la última clave: el
volumen se perdió, el fichero siguió siendo válido, y lo vi imprimiendo el YAML
parseado.

Es **exactamente** lo que me había pasado una hora antes en `gateway.yaml`, y
para lo que había escrito una prueba… acotada a `gateway.yaml`.

Dos veces en una sesión no es falta de cuidado, es un guardarraíl mal acotado.
`test_yaml_sin_duplicados.py` cubre ahora **todos** los YAML de `platform/`,
descubiertos solos para que un fichero nuevo entre sin que nadie se acuerde.

Y tenía un segundo defecto: se **saltaba `compose.yaml`**, el único fichero
donde el fallo había ocurrido de verdad. Las claves de fusión `<<: *restart`
hacían saltar un `ConstructorError` y el `pytest.skip` lo tapaba. Ahora el
loader llama a `flatten_mapping` y un fichero que no se puede cargar **falla**
en vez de saltarse.

*Una prueba que se salta el caso que la motivó es un fichero de texto.* Es
D-088 por tercera vez.

### Y un error mío de manos

Ejecuté `git checkout platform/compose.yaml` como limpieza de una prueba,
sobre un fichero que tenía trabajo sin confirmar. Perdí los tres cambios y los
rehice. No hay daño más allá del tiempo, pero queda escrito: **un `checkout`
de limpieza sobre un fichero con cambios sin confirmar es un `rm` con otro
nombre.**

---

## D-104 · El criterio 6 no esperaba una segunda máquina: esperaba poder distinguirla

**Contexto**: el criterio 6 del piloto —*«un segundo host manda telemetría»*—
llevaba semanas en «?» con la nota «un solo agente desplegado». Suena a que
falta hardware.

Al ir a medirlo apareció otra cosa:

```
host.name       nivel   servicios   spans
2a3bc2822dfb    agent          16   22377
eb3f7dd3361d    agent           1       1
```

**Todo el almacén llevaba un id de contenedor como `host.name`.** Dieciséis
servicios distintos bajo un identificador que cambia en cada recreación.

Así que el criterio no estaba bloqueado por la máquina que falta: aunque
hubiera existido, **no habría forma de distinguir su telemetría de la de esta**.

### La causa

`resourcedetection` con `hostname_sources: [os]` devuelve, dentro de un
contenedor, el nombre del sistema — que es el id del contenedor. El agente
corre en uno, así que sellaba el suyo sobre todo lo que pasaba.

Y `override: false` no salvaba nada: una aplicación en un contenedor tampoco
conoce su máquina, cree ser su propio contenedor. Fue lo que le encontramos a
Prosodia hace dos días sin ver que era nuestro, no suyo.

### El arreglo, y por qué es obligatorio y no un defecto

El agente **sí** sabe en qué máquina está, porque hay uno por host y alguien
tuvo que desplegarlo ahí. Ahora sella `host.name` con `ARGUS_HOST`, en
`upsert`: pisa lo que traiga la aplicación, precisamente porque la aplicación
no lo sabe.

`ARGUS_HOST` **no tiene valor por defecto** en `compose.agent.yaml`. Adivinarlo
es lo que trajo todo esto: un defecto «razonable» habría seguido sellando algo
inútil sin que nadie lo notara. Nombrar la máquina es parte de desplegar un
agente.

El `make agent` local sí lo resuelve con `hostname -s`, y la diferencia
importa: **preguntar al sistema estando en la máquina no es adivinar**;
adivinar era dejar que contestara el contenedor.

### Lo que sí se pudo verificar sin segunda máquina

Se desplegó un agente que se comporta como remoto: **sin la red compartida**,
alcanzando al gateway por el puerto publicado del host y con token — el camino
real de un agente remoto, donde lo único que cambiaría en una máquina de verdad
es un nombre de Tailscale en vez de `host.docker.internal`.

| | |
|---|---|
| autenticación por token sobre el puerto publicado | ✅ |
| identidad propia, distinguible del host local | ✅ `imac-simulado` junto a `MacBook-Pro-…` |
| cola persistente con el plano central caído | ✅ **0/5 con el gateway parado, 5/5 al volver** |

Queda exactamente una cosa: una máquina de verdad y un nombre estable de red
privada. Todo lo demás del camino está ejercitado.

### Y el agente simulado se retiró, a propósito

Dejarlo corriendo habría dejado el criterio 6 en verde sobre una premisa falsa
—las dos «máquinas» eran la misma— y eso es fabricar deliberadamente el
artefacto que miente que llevo toda la semana arreglando. El criterio dice
ahora `NO · un solo host identificado`, que es la verdad.

### Al retirarlo apareció el hallazgo de verdad

Con el contenedor borrado, sus métricas **seguían llegando con marca de tiempo
actual**. No era retraso: `spanmetrics` mantiene y reemite cada serie que ha
visto, para siempre.

Lo que eso rompe no es un panel. Es **cualquier cosa que cuente «qué está
vivo»** a partir de métricas derivadas de spans: una aplicación retirada, un
host que ya no existe o un endpoint que se dejó de servir parecen activos
indefinidamente. Y *parecer vivo* es justo lo que esta plataforma existe para
no creerse.

Dos cambios, y hacen falta los dos:

- `metrics_expiration: 15m` en el conector, para que una serie sin spans deje
  de emitirse.
- El criterio 6 cuenta **métricas internas del Collector** (`otelcol_*`), no
  derivadas de spans. Las de un agente paran cuando el agente para, sin
  depender de la configuración de ningún conector.

### Y una medición que era mala de otra forma

La primera versión contaba hosts en **trazas y 24 h**. Una sola petición de
prueba desde un host cumplía el criterio y lo dejaba verde un día entero,
aunque ese host ya no existiera.

*«Manda telemetría» es presente.* Una prueba puntual no es un host.

### Lo que queda escrito a mano

Ninguno: los criterios 1–7 se recalculan en cada ejecución. El 8 es un contador
sobre este mismo fichero.

---

## D-105 · Un bucle de espera que cantaba éxito cuando no podía preguntar

**Contexto**: un bucle de verificación que quedó corriendo de fondo desde
primera hora terminó con `LLEGO` y código 0. La traza que esperaba tiene
**cero spans** en el almacén. No llegó nunca.

```bash
until [ "$(docker exec argus-clickhouse-1 clickhouse-client -q "SELECT count() …")" != "0" ]
do sleep 3; done
echo LLEGO
```

Si `docker exec` falla —y ClickHouse y el gateway se reiniciaron muchas veces
hoy— la sustitución devuelve **cadena vacía**. Y `"" != "0"` es **verdadero**:
el bucle sale y canta éxito.

La condición no dice «ha llegado». Dice «he conseguido leer algo distinto de
cero», y no poder leer entra en esa definición.

### Es la tercera vez con la misma forma

| | |
|---|---|
| D-091 | `except ValueError: continue` convertía «no puedo parsear» en «no hay evaluaciones», y mandó una alerta falsa por Telegram |
| a9 | el bucle de espera de PyPI reventaba con `JSONDecodeError` sobre un cuerpo vacío |
| **D-105** | `""  != "0"` convierte «no pude preguntar» en «la respuesta es sí» |

Las tres son **«no lo sé» tratado como una respuesta**, que es literalmente lo
que llevo toda la semana corrigiendo en el producto —`None` frente a `False`
en los criterios 4 y 6, la seudonimización que no distinguía ámbitos— sin
aplicarlo a mis propias herramientas de comprobación.

### Qué se comprobó, y qué no hace falta rehacer

El repositorio **no** usa ese patrón: sus esperas son `until curl -sf …`, y
`curl -sf` devuelve no-cero cuando falla, así que sigue esperando. El fallo
estaba solo en mis comandos de verificación.

Y las afirmaciones que dependían de un bucle se re-comprobaron contra el
almacén, sin bucle de por medio:

```
politica de auditoria       6/6
denegaciones conservadas    8/8
cola con el gateway caido   5/5
```

Se sostienen porque en esos casos el bucle solo esperaba, y **la evidencia que
publiqué salió de una consulta posterior que imprimía el dato**. Eso es lo que
salvó las conclusiones, no el bucle.

### La regla

**Un bucle de espera no puede ser la evidencia.** Espera; después se pregunta,
se imprime el número y ése es el resultado. Si el bucle es lo único que
consultó, un fallo transitorio de red se lee como confirmación.

Y para las esperas: comparar numéricamente (`-ge`), que falla sobre una cadena
vacía en vez de darla por buena.

---

## D-106 · El entorno no necesitaba vocabulario: ya tenía uno, y era del estándar

**Contexto**: al medir el cambio de `host.name` salió que los tres equipos
mandaban tres valores distintos en `deployment.environment.name`, más el
nuestro:

```
argus        mac-dev
prosodia     local
aeon-ai      local
prometheus   bare-metal
```

Les dije a los tres que iba a *cerrar el vocabulario y proponérselo*. Fui a
mirar el estándar antes de inventarlo, y **ya está cerrado**:

```python
class DeploymentEnvironmentNameValues(Enum):
    PRODUCTION = "production"
    STAGING    = "staging"
    TEST       = "test"
    DEVELOPMENT = "development"
```

Así que no había nada que diseñar. Es D-081 otra vez —*poner nombre propio a
algo que ya lo tiene*— y esta vez a punto de cometerlo yo, con tres equipos
esperando mi propuesta.

### El motivo de la deriva, que no es descuido de nadie

Ninguno de los cuatro valores es tonto. `local`, `mac-dev` y `bare-metal` son
respuestas razonables… **a otra pregunta**: *¿dónde / cómo corre esto?*

Y esa pregunta tiene su propio campo, `host.name`, que hasta ayer devolvía el
id de un contenedor (D-104). O sea: el campo del entorno estaba absorbiendo un
significado porque el campo correcto **no funcionaba**.

Arreglado `host.name`, la deriva se explica sola y se corrige sola.

### Y el primer valor inventado lo escribí yo

`docs/PLAN.md` decía, desde el principio:

```
| deployment.environment.name | entorno | mac-dev | imac | server-1 | ci |
```

Cuatro valores, ninguno del estándar, y **mezclando máquina con nivel en la
misma columna**. Los equipos leyeron eso e hicieron lo razonable: inventar el
suyo.

No fue que tres equipos derivaran de un vocabulario bueno. **Fue que no había
vocabulario, y el mío era el peor de los cuatro** porque además era el que los
demás tomaban como referencia.

### Lo incómodo de la corrección

Los cuatro valores mapean a `development`. Todos. La plataforma entera corre
en un portátil.

O sea que el campo **no estaba distinguiendo nada** y llevaba semanas
pareciendo que sí, porque tenía cuatro valores distintos. Cuatro valores no son
información: son cuatro formas de decir lo mismo.

Lo que la gente quería expresar —qué máquina, cómo corre— ya tiene sitio:
`host.name`, y `argus.component.role` para la forma.

### Qué se hace

- El modelo declara el campo como **enum con los cuatro del estándar**, con un
  comentario que dice explícitamente que no son nuestros.
- `argus.init()` **avisa** si el valor no está en el vocabulario, y conserva el
  valor: un entorno mal escrito es un dato peor, no un motivo para no arrancar.
- El aviso es **distinto** para `local`, `mac-dev` y `bare-metal`: dice
  *«si lo que querías decir es en qué máquina corre, eso es `host.name`»*.
  Quien escribe eso no está eligiendo mal entre cuatro niveles — está
  respondiendo a otra pregunta, y un «no está en la lista» a secas le haría
  elegir uno al azar.
- Nuestro propio `mac-dev` pasa a `development`, en el compose y en los tres
  documentos que lo proponían.
- Y se corrige `PLAN.md`, que es donde empezó.

Publicado en `1.0.0a13`.

### Lo que esto dice del método

Iba a diseñar un vocabulario para un campo que ya lo tenía, a petición mía, con
tres equipos esperando. Lo único que lo evitó fue **mirar el estándar antes de
escribir la propuesta** — y lo miré porque D-081 me había mordido con lo mismo.

Las convenciones propias hay que justificarlas contra el estándar **cada vez**,
no solo cuando el nombre suena a que podría existir ya.

### Y una coda, porque cambiar el valor no bastó

Tras cambiar el compose, los contenedores seguían con `mac-dev`:

```
argus-alert-bus-1: ARGUS_ENVIRONMENT=mac-dev
```

`docker restart` **conserva la configuración del contenedor**; solo un
`up -d` lo recrea con el entorno nuevo. Así que el valor estaba corregido en
el fichero, en verde en los tests, y sin efecto en lo que corría.

Es la tercera forma distinta del mismo fallo esta semana: la copia instalada
del vigilante (D-090), la imagen del canario sin reconstruir (D-094), y ahora
un contenedor sin recrear. **Editar la fuente no es desplegar**, y las tres
veces lo que lo destapó fue ir a mirar el proceso en marcha en vez de fiarme
del fichero.

---

## D-107 · Le pusimos el mismo nombre a dos cosas distintas, y costó tiempo a quien nos adoptó

**Contexto**: Prosodia adopta `argus.propagate.Executor` (D-095) y reporta, de
paso, algo que no veníamos a buscar:

> *«Vuestro `init()` loguea `"propagate": "never"`. Es `ARGUS_PROPAGATE`, o sea
> si confiar en un `traceparent` que llegue de fuera por HTTP — nada que ver
> con la propagación entre hilos del módulo que se llama igual. Tardamos un
> rato en separarlo.»*

No es confusión suya. Son dos cosas sin relación:

| | qué es |
|---|---|
| `argus.propagate` | el **módulo**: lleva el contexto entre hilos, procesos y colas |
| `ARGUS_PROPAGATE` | si **confiar** en un `traceparent` ajeno — decisión de seguridad |

Y el log las ponía una al lado de la otra, el mismo día en que ellos acababan
de tocar la primera.

### El arreglo no puede romper a quien ya nos usa

`ARGUS_PROPAGATE` está en tres repositorios que no controlamos. **Romperlo
para arreglar una palabra sería cobrarles a ellos nuestro error.**

- `ARGUS_TRUST_INBOUND` es el nombre nuevo y dice lo que hace.
- `ARGUS_PROPAGATE` sigue funcionando, sin aviso: tenerlo puesto no es un
  error, es haber llegado antes.
- Si están **los dos y en desacuerdo**, gana el nuevo y se avisa. Un
  desacuerdo silencioso aquí decide si se adopta un `traceparent` de fuera, y
  ése es el peor sitio posible para elegir callando.
- El log de `init()` dice `trust_inbound`, y `Config.trust_inbound` existe
  junto a `propagate`, que conserva el nombre porque lo leen el middleware
  ASGI y los tests.

### Y de paso, un silencio que no habíamos visto

El valor inválido se coercionaba a `"never"` **sin decir nada**. Caer al modo
más seguro es correcto; no decirlo convierte una errata en una decisión
invisible — alguien escribe `allways` y se queda sin trazas distribuidas
creyendo que las tiene. Ahora avisa y sigue cayendo a `never`.

### La receta del `Executor` estaba mal dada

Dijimos *«cambiar la clase es todo lo que hay que hacer»*. Es falso si el SDK
es una dependencia **opcional**, que es lo correcto para un SDK de
observabilidad: la aplicación tiene que arrancar sin él.

Prosodia lo tiene en un extra `web`, y la misma función la corre también una
CLI que no lo instala. Un import a secas la rompe al arrancar, en una ruta que
no toca telemetría. Su patrón, en el README con su nombre:

```python
from concurrent.futures import ThreadPoolExecutor   # el caso base, primero

try:
    from argus.propagate import Executor as ThreadPoolExecutor
except ImportError:
    pass
```

**El orden es la mitad que hace que funcione** y no se nos habría ocurrido: al
revés, `mypy` lo rechaza porque asigna un supertipo sobre un subtipo. Declarar
el estándar y estrecharlo después es lo que comprueba.

### Lo que esto dice del método

Los dos hallazgos salen de la misma frase de un equipo que estaba haciendo
otra cosa. Ninguno se ve leyendo el código: el primero necesita que alguien
lea un log en un momento concreto, y el segundo que alguien tenga una
topología de dependencias que nosotros no tenemos.

Es la cuarta vez esta semana que un consumidor encuentra algo que yo no
encontraría nunca. **La documentación de un SDK no la valida quien lo escribe.**

Publicado en `1.0.0a14`. Y el README arrastraba todavía el vocabulario de
entorno que D-106 retiró — corregido ahí también.

---

## D-108 · El inquilino era el mismo sujeto, en claro, dos campos más allá

> **Fallo de plataforma** · descubierto 2026-10-01 · `argus.tenant` no se seudonimizaba, y en el registro de auditoría de Prometheus contenía exactamente el mismo identificador que `enduser.pseudo.id` estaba protegiendo en el mismo evento; además el modelo lo declaraba apto para viajar en baggage, contra nuestra propia regla dura

**Contexto**: Prometheus pidió en `P-35` que verificáramos el seudónimo de su
objetivo sobre tráfico real. La verificación **pasa** —el `argus.target.id` de
un `type = user` sale hasheado y distinto del hash del actor— y en el mismo
evento estaba esto:

```
enduser.pseudo.id = 9b154d1bde622b3b…    el actor, protegido
argus.tenant      = d2e0e23d-8599-4cab…  EL MISMO SUJETO, en claro
```

Hasheando ese UUID con nuestra sal:

```
sha256("d2e0e23d-8599-4cab-a730-aafb82f964d5" + sal)
  = 9b154d1bde622b3bb77b371cd9f304efc5ac596bb08dda3575c569f49a1d3fea
```

Idéntico al seudónimo de al lado. **La seudonimización del actor quedaba
anulada dentro del mismo evento.**

### Tercera vez con la misma forma, y la regla ya estaba escrita

| | el sitio sin cubrir |
|---|---|
| D-092 | el ámbito `spanevent`: cubríamos `span` y `log` |
| D-098 | el tipo `client`: cubríamos `user` |
| **D-108** | el campo `argus.tenant`: cubríamos el actor |

Y la frase que las explica la escribimos en D-098: **un seudónimo vale lo que
vale el sitio menos protegido donde aparece ese sujeto.** Tres veces la misma
regla, tres sitios distintos, y ninguna la encontró leyendo.

### Y es nuestro, no suyo

En `P-32` les dijimos que `prometheus.audit.actor.client_id` sobraba *«porque
`argus.tenant` ya lleva el cliente»*. Retiraron su atributo y usaron el
nuestro. **Hicieron exactamente lo que documentamos**, y lo que documentamos
metía un identificador de persona en un campo sin proteger.

### El segundo defecto, peor que el primero

El modelo decía: *«Inquilino, si aplica. **Viaja en baggage.**»*

Y `propagate.set_baggage` lo listaba como seguro:

```
REGLA DURA: el baggage es pequeno, acotado y SIN PII. […]
Lo que si va: `argus.app`, `argus.run.id`, `argus.tenant`.
```

**La regla dura y su violación, en el mismo docstring, separadas por dos
párrafos.** Es literalmente lo que Aeon nos describió encontrando en su propio
`MarkGuardrail` —*«una contradicción a tres funciones de distancia»*— y que
celebramos como hallazgo suyo sin mirar el nuestro.

El baggage cruza procesos y máquinas en cabeceras, o sea sitios que no
controlamos. Ahí no puede ir un identificador que pueda ser de una persona.

### Qué se hace

- **Se hashea en sitio**, en los tres ámbitos, como `session.id`. No se borra:
  un inquilino seudonimizado sigue agrupando, que es para lo que el campo
  existe — y atribuir coste por inquilino sigue funcionando.
- **Se retira de la lista de baggage**, con el motivo escrito.
- El modelo dice que puede ser una persona, y por qué: donde un principal se
  autentica con contraseña **o** con secreto de máquina, el identificador es el
  mismo y el campo no puede saber cuál es.
- Cubierto también en `log`, donde hoy nadie lo manda. El hueco de D-092 fue
  exactamente eso: unos ámbitos sí y otros no.

Verificado con el mismo sujeto en los dos campos:

```
argus.tenant      cce50cb26c1f0ee2…
enduser.pseudo.id cce50cb26c1f0ee2…    el mismo hash
```

Que den **el mismo** hash es la parte buena: se sigue viendo que el actor y el
inquilino son el mismo sujeto, sin que ninguno esté en claro.

### Lo que no se repara

Los seis eventos de auditoría que ya están en el almacén llevan el valor en
claro. No se reescriben: son suyos y de hace horas, y borrarlos a mano de una
tabla de ClickHouse es más riesgo que el que corrigen. Quedan ahí y está dicho.

Publicado en `1.0.0a15`.

### Lo que esto dice del método

La verificación que me pidieron **pasó**. Si me hubiera limitado a contestar
«confirmado, el hash sale bien y es distinto del actor», habría sido cierto y
habría dejado la fuga en pie — porque mi atención estaba en el campo que ellos
me señalaron.

**Lo encontré mirando el evento entero en vez de el campo de la pregunta.**
Y la única razón de mirarlo entero es que las dos veces anteriores el fallo
estaba justo al lado de donde miraba.

---

## D-109 · El aviso que faltaba, y el valor que este SDK se inventaba

> **Fallo de plataforma** · descubierto 2026-10-01 · `argus.init()` no avisaba cuando faltaba el nombre de servicio o el namespace; y rellenaba `deployment.environment.name` con `local` —un valor que no está en el vocabulario del estándar— sin que el aviso de D-106 pudiera verlo, porque miraba la variable de entorno y no el valor resuelto

**Contexto**: Prosodia reporta en `S-09` un fallo que no veníamos a buscar:

> *«Lo arrancamos sin `OTEL_SERVICE_NAME`. Los dos servicios salieron como
> `unknown-service`, **la telemetría se exportó igual y no hubo ningún error**.
> La única pista fue la línea de `argus.init`, que lo dice si uno la lee.»*

Y nos pasaron la cifra: 450 registros suyos en el almacén bajo
`unknown-service`, de los que **ellos solo reconocían dos**. Fuimos a mirar los
otros 448 y eran suyos también, de otra corrida. *Ni ellos podían atribuirse sus
propios registros, porque el campo que lo diría es el que faltaba.*

### El aviso, y por qué cubre dos cosas

`service.name` ausente y `service.namespace` ausente son el mismo fallo con
consecuencias distintas, y los dos han mordido de verdad:

| falta | qué pasa | a quién le pasó |
|---|---|---|
| `ARGUS_SERVICE` | `unknown-service`: nada se puede atribuir ni correlacionar | Prosodia, `S-09` |
| `ARGUS_NAMESPACE` | cae al nombre del servicio: la identidad de dos niveles desaparece, y en el registro entras como provisional **sin canales** | Aeon, durante días (D-094) |

Un solo aviso para los dos: son la misma categoría —quién eres— y dos avisos
seguidos por el mismo arranque se leen como ruido.

Y dice explícitamente *«la telemetría se exportará igual y sin ningún error»*,
porque es la frase que separa esto de un error de configuración normal. Quien lo
lee tiene que entender que **no va a ver un fallo después**.

### Y escribiéndolo salió lo otro, que es peor

Una prueba falló afirmando `cfg.namespace == ""`. No queda vacío: cae al nombre
del servicio. Correcto y documentado. Pero tres líneas más abajo:

```python
if not cfg.environment:
    cfg.environment = "local"
```

**Este SDK rellenaba el entorno con `local`.** Un valor que no está en el
vocabulario del estándar —el que cerramos ayer en D-106— y que el aviso de
D-106 **no podía ver**, porque comprobaba la variable de entorno y no el valor
resuelto.

Lo incómodo es lo que eso le hace a D-106. Ahí escribimos:

> *«Los tres equipos mandaban `local`, `local` y `bare-metal`»* … *«Leísteis eso
> e hicisteis lo razonable: inventar el vuestro.»*

**Dos de esos tres `local` los ponía este código.** Prosodia incluso aceptó la
culpa —*«escribimos `local` respondiendo a ¿dónde corre esto?»*— por un valor
que nunca escribieron. Les atribuí una decisión que había tomado mi propio
default.

### Qué se hace

- **El entorno no se inventa.** Ausente queda ausente, y se avisa. *Ausente es
  honesto; adivinado era el fallo* — es la misma regla que hizo `ARGUS_HOST`
  obligatorio en D-104, aplicada un día tarde al campo de al lado.
- **El aviso del entorno mira el valor resuelto**, no la variable. Un valor
  inválido escrito a mano ya avisaba desde `a13`; uno puesto por el propio SDK,
  no. El único sitio donde se ven los dos es el final.
- `SERVICIO_DESCONOCIDO` deja de ser una cadena suelta, para que la prueba y el
  código no puedan discrepar sobre cuál es el centinela.

### Tres pruebas que pasaban por el motivo equivocado

Al añadir los avisos, tres pruebas de `a14` se pusieron rojas **sin que su tema
cambiara**. Afirmaban `avisos == []` queriendo decir «esta combinación no avisa
de la confianza», y comprobaban «este arranque no avisa de nada».

Es el mismo error que un `pytest.raises(Exception)`: pasa hasta que algo se
mueve al lado. Ahora filtran por el aviso que les importa.

Y una prueba de `a13` decía lo contrario de lo correcto:

> *«Es opcional. Avisar de que falta sería ruido en cualquier script.»*

Suena razonable y era falso, porque el campo **no quedaba vacío**: se rellenaba
con `local`. El silencio que esa prueba protegía era el silencio con el que se
sellaba un valor inválido.

Publicado en `1.0.0a16`.

### El patrón, que ya es una lista

Cuatro avisos de arranque, y los cuatro los encontró alguien que nos adoptó:

| aviso | de quién salió |
|---|---|
| `localhost` desde un contenedor | Aeon |
| protocolo contra el puerto del otro transporte | Aeon |
| entorno fuera de vocabulario | de medir a los tres |
| **identidad ausente** | Prosodia |

Ninguno de los cuatro salió de nuestras pruebas, y los cuatro son el mismo modo
de fallo: **la aplicación funciona, los datos llegan, y no sirven.** Es el único
tipo de defecto que no se encuentra usando tu propio software, porque tú ya
tienes las variables puestas.

---

## D-110 · El mismo error que el aviso venía a arreglar, un nivel más afuera y en el mismo cambio

> **Fallo de plataforma** · descubierto 2026-10-02 · el aviso de identidad de `a16` comprobaba `ARGUS_SERVICE`/`ARGUS_NAMESPACE` y no `OTEL_RESOURCE_ATTRIBUTES`, así que daba **dos falsos positivos** contra la configuración de Prometheus — el único equipo que lo tiene todo puesto

**Contexto**: antes de avisar a los tres equipos de `a16`, fui a comprobar qué
vería cada uno. Prometheus pone su identidad por la vía estándar:

```
OTEL_SERVICE_NAME=gateway
OTEL_RESOURCE_ATTRIBUTES=service.namespace=prometheus-inference-platform,
                         deployment.environment.name=bare-metal
```

Y mi aviso nuevo decía:

```
Argus: arrancando sin identidad.
  - ARGUS_NAMESPACE: … tus incidentes NO llegan a ningun canal humano
Argus: sin `deployment.environment.name`. …
```

**Las dos cosas son falsas.** Las tienen puestas.

### Es literalmente el mismo bug, en el mismo commit

D-109 arregló que el aviso del entorno mirase la variable en vez del valor
resuelto. Y en ese mismo cambio escribí el aviso de identidad mirando **las
variables que yo esperaba** en vez de la identidad real.

Un nivel más afuera, con la misma forma, sin verlo. La corrección y la
repetición del error entraron juntas.

### Y avisa justo a quien siguió la ruta que le dimos

Nuestro propio README dice:

> *«Las estándar de OpenTelemetry (`OTEL_SERVICE_NAME`,
> `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_RESOURCE_ATTRIBUTES`) también se
> respetan, para que una app que ya tiene OTel migre sin tocar código.»*

Prometheus hizo exactamente eso. El aviso les habría dicho que no tienen
identidad **por usar la vía de migración que documentamos**, que es la peor
clase de falso positivo: castiga seguir las instrucciones.

### Había dos parsers y no coincidían

`_resource.py` leía `OTEL_RESOURCE_ATTRIBUTES` desde D-072 y sabía no pisar lo
que trae. Mi aviso no lo sabía. **El recurso que llegaba al almacén estaba
bien y el aviso sobre ese mismo recurso estaba mal**, en el mismo proceso.

Ahora hay un parser, `atributos_del_entorno_otel()`, y `_resource` lo usa.
Una prueba comprueba que son el mismo objeto, porque dos parsers que no
coinciden es lo que acaba de pasar.

### Lo que se arregla además del aviso

`Config.environment` ahora **adopta** el valor de la vía estándar, no solo lo
consulta para avisar. Antes quedaba vacío aunque el almacén recibiera
`bare-metal`, así que `langfuse.environment` —que lo espeja— decía otra cosa
que el recurso.

Adoptarlo no es aceptarlo: `bare-metal` llega igual y **sigue avisando**, que
es el aviso verdadero y el único que Prometheus debería ver.

### Lo que me salvó

Ir a medir qué vería cada equipo antes de escribirles. No una prueba: las
pruebas de `a16` pasaban todas, porque las escribí yo con las mismas
suposiciones que el código.

**Un falso positivo contra el equipo mejor configurado es el peor resultado
posible de un aviso**, y habría llegado con el mensaje «vuestros incidentes no
llegan a ningún canal humano» a un equipo cuyos incidentes sí llegan.

Publicado en `1.0.0a17`. `a16` estuvo publicada veinte minutos y nadie la
adoptó; se queda en PyPI porque retirarla rompería a quien la hubiera cogido
entre medias, y lo que hace mal es avisar de más, no romper nada.

---

## D-111 · La frase no era falsa: era ambigua, y la lectura cómoda costó 1.797 registros

> **Fallo de plataforma** · descubierto 2026-10-02 · el punto 3 del contrato del SDK decía «No-op sin configurar», que se lee como «sin endpoint no se exporta» — falso— además de como «sin `init()` no pasa nada» — cierto y lo único que la prueba verifica

**Contexto**: Prosodia encontró el origen de 1.797 registros suyos en nuestro
almacén. Eran corridas de `pytest`: el `conftest.py` importa la app, la app
llama a `argus.init()` al importarse, y **sin endpoint configurado el SDK no se
calla: cae a `localhost:4318` y exporta de verdad.**

Su `.env.example` decía *«sin `OTEL_EXPORTER_OTLP_ENDPOINT` no se exporta nada
y el coste es cero»*. Lo corrigieron. Y escribieron:

> *«Lo escribimos nosotros, es falso, y es justo la frase que impide buscar
> este fallo.»*

### Lo mismo estaba en nuestro contrato, y conviene ser exacto

```
3. **No-op sin configurar.** Los decoradores funcionan con coste cero.
```

**No es falso.** `test_helpers_work_without_init` lo verifica: sin llamar a
`init()`, los decoradores no hacen nada. Es la propiedad que permite
instrumentar librerías propias sin imponer telemetría, y es correcta.

El defecto es que *«sin configurar»* admite dos lecturas:

| lectura | ¿cierta? | ¿la verifica la prueba? |
|---|---|---|
| sin llamar a `init()` | sí | sí |
| sin configurar un endpoint | **no** | no |

Y las cinco líneas del contrato van bajo *«Verificado por tests, no por buenas
intenciones»*, así que la lectura falsa venía con un sello de garantía que no
le correspondía.

**Una frase ambigua con una lectura peligrosa es peor que una frase falsa**,
porque la falsa se descubre al contrastarla y la ambigua se confirma sola:
quien la lee mal encuentra apoyo en el texto.

### Qué se hace

- El punto 3 dice ahora *«No-op si NO llamas a `init()`»*, y lleva debajo la
  advertencia explícita de la otra lectura con el nombre de la única forma de
  apagar: `ARGUS_DISABLED=1`.
- Lo mismo en `PLAN.md`, que tenía la misma frase.
- **Tres pruebas nuevas** que fijan la verdad incómoda: que sin endpoint se
  exporta, que lo único que apaga es `ARGUS_DISABLED`, y que el README no
  vuelve a la redacción ambigua.

La tercera comprueba el **texto**, y es a propósito: el defecto nunca estuvo en
el código. El código siempre hizo esto.

### Y cuatro cosas más que Prosodia pagó por descubrir

Van al README con su nombre, porque las cuatro fallan en silencio:

| | |
|---|---|
| **Tests y CI** | si tu suite importa la app, tu suite exporta. `ARGUS_DISABLED=1` en el `conftest` raíz |
| **CLIs y lotes** | `init()` antes de tu logging; la traza raíz se abre a mano; el `namespace` explícito o se auto-excluye |
| **ASGI** | uvicorn hace `dictConfig` **después** del import, así que enganchar los loggers del servidor ahí se pierde sin error |
| **Muestreo** | es de trazas, no de registros: 40 peticiones dieron 40 registros y **2 spans** |

La del muestreo es la que más cambia el consejo que damos. *Si una ruta
importa, sus spans solos no la cubren* — y eso no estaba dicho en ningún sitio.

### Los 1.797 registros

Se quedan. No son suyos para borrar —lo dicen ellos y tienen razón— y tampoco
nuestros para borrar sin pensarlo: están en la misma tabla que todo lo demás y
una consulta mal escrita se lleva por delante lo que no toca. Están
identificados (`unknown-service`, rutas de `pytest-of-…` en el cuerpo) y ahí
quedan.

### Lo que me corrijo a mí mismo

Al revisar el canal le dije al usuario que mi documentación *«dice exactamente
lo mismo que ellos corrigieron»* y que era **falsa**. No lo era: era ambigua, y
la prueba que decía verificarla verificaba la lectura buena.

La diferencia importa para el arreglo —no había que cambiar el código— y
importa para el relato: Prosodia escribió algo falso en su `.env.example`;
nosotros escribimos algo cierto de una forma que invitaba a escribir eso.

---

## D-112 · `argus.denied_by`: el campo que existe porque nuestro propio consejo dejó un hueco

**Fecha**: 2026-10-02 · **Versión**: `1.0.0a18`

### Por qué existe el campo

Aeon nos trajo cinco desenlaces (`result` · `denied_by_policy` ·
`approval_granted` · `approval_denied` · `approval_expired`) y los colapsamos a
tres valores que ya teníamos más uno nuevo, `denied`. El colapso fue correcto y
**perdía una cosa: quién denegó.**

Y por separado les recomendamos que una denegación de política **no** llevara
`argus.guardrail`, porque ese campo enciende el camino caliente y marcarla así
paginaría en cada denegación.

Las dos decisiones por separado eran buenas. Juntas dejaron la denegación sin
**ningún** portador de quién la hizo, y eso no lo vimos nosotros: lo vio Aeon,
razonando sobre la consecuencia de nuestro propio consejo.

> *«Como ya no ponemos `argus.guardrail`, no queda ningún portador de quién
> denegó.»*

Es la segunda vez que un consejo nuestro a este equipo tiene un efecto que no
habíamos calculado (la primera fue D-099, donde el mismo consejo dejó sus
denegaciones al 10 %). El patrón merece el nombre: **una recomendación que
quita algo tiene que decir qué ocupa su sitio.** Quitamos el guardarraíl de la
denegación y no pusimos nada donde estaba la atribución.

### El vocabulario

`policy · human · budget`, cerrado, los tres que ellos pidieron.

`human` significa **una persona autenticada**, y eso es cierto solo desde que
Aeon cerró su clase de llamador: antes su aprobación la podía conceder
cualquiera que alcanzase el puerto. Un `human` que en realidad significa «algo
llegó al puerto» es peor que no tener el campo, porque se lee como atribución.

**Dice la clase, nunca cuál.** El *quién* concreto es `user.id` /
`enduser.pseudo.id`, que son del estándar y que el gateway seudonimiza. La
cardinalidad cerrada de aquí es lo que permite agrupar sin tocar a nadie — es
D-086 otra vez: no inventar nombre propio para lo que ya lo tiene.

### Lo que se fija no es el vocabulario, es el PAR

El vocabulario es la parte fácil. El defecto real es **medio par**, y tiene dos
formas que se escriben las dos sin error:

| lo que falta | qué se rompe |
|---|---|
| `denied` sin `denied_by` | la denegación no se puede atribuir |
| `denied_by` sin `denied` | la dimensión cuelga de un paso que siguió, y contamina el recuento de denegaciones |

La primera es la peligrosa, y por el motivo de siempre: **el hueco no se ve al
consultar.** Un `GROUP BY` sobre un campo ausente devuelve una fila, no una
queja. Si el defecto no duele en la emisión, no duele en ningún sitio.

Por eso `Step.denied(by=...)` pone las dos mitades en **una** llamada, y por eso
el aviso existe además para quien escriba el atributo a mano — que es como lo
hacen los cuatro servicios Go de Aeon, leyendo `modelo.json`.

### Dónde va el aviso: en el cierre, no en la escritura

En `_finalize`, sobre el estado **resuelto**. Es la lección de D-110 aplicada
antes de repetirla: un aviso que mira un campo antes de que el paso acabe se
dispara sobre quien va a ponerlo bien en la línea siguiente. Hay una prueba de
eso —poner el par en dos llamadas no avisa— y es la prueba que la versión a16
no tenía.

**Y no se rellena.** La tentación era hacer `setdefault("unknown")`, que es
exactamente el `setdefault("ok")` de D-097 con otro campo: inventar un hecho
para tapar una ausencia. Avisar y escribir lo que haya.

### Medido antes de escribir a nadie

La regla de D-110: comprobar qué vería cada equipo **antes** de mandarles la
versión. Catorce días de almacén:

```
quién emite `argus.outcome`      ámbito span        ámbito spanevent
aeon-*                           denied, suspended, timeout, ok    —
gateway (Prometheus)             —                  ok, error  (24)
Prosodia                         —                  —
```

Ningún equipo emite `denied` hoy, así que **el aviso nuevo no se dispara en
ninguno**. El único que lo verá es Aeon, cuando emita lo que pidió.

### Verificado en el almacén, no en la configuración

Cuatro denegaciones reales por la tubería completa:

- las tres atribuidas llegan con su `argus.denied_by`
- **ninguna** lleva `argus.hot` ni `argus.guardrail` — no pagina
- la no atribuida **llega igual**, con el campo vacío: avisar no es descartar
- la política `auditoria` del muestreo las conserva: su contador subió a 16,
  y 4 de 4 no es sobrevivir a un baseline del 10 %

Las dos propiedades juntas —no paginar y no muestrear— son el punto de D-099:
se cambió una notificación de más por un registro completo, porque el ruido se
nota y un registro con huecos no.

### Lo que esto tardó

Dije *«os aviso al publicarlo»* el 30 de septiembre. Se publica el 2 de
octubre, dos días después, y en medio salieron a14, a15, a16 y a17 — ninguna lo
llevaba. Lo que se colaba delante cada vez era un defecto recién encontrado, y
un defecto recién encontrado siempre parece más urgente que una promesa vieja.
No lo es cuando el defecto no afecta a nadie todavía y la promesa bloquea a un
equipo.

---

## D-113 · Tercera vez que `spanevent` se queda fuera de la medición

> **Fallo de plataforma** · descubierto 2026-10-02 · la consulta con la que mido qué emite cada equipo leía `SpanAttributes` y no `Events.Attributes`, así que afirmé por escrito a Prometheus que no emiten `argus.outcome` cuando lo emiten 24 veces en catorce días

**Fecha**: 2026-10-02

### El error

Midiendo qué vería cada equipo con `denied_by` (D-112), la primera consulta
dio esto:

```sql
SELECT SpanAttributes['argus.outcome'] ... FROM otel.otel_traces
```

Y con ella concluí que Prometheus no emite `argus.outcome`. **Lo emite**: 24
veces en catorce días, en su evento `audit.admin_action`, que vive en el ámbito
**spanevent** y no en los atributos del span.

Lo peor no es la consulta: es que **ya se lo había dicho a ellos**. En `A-35`
escribí *«ninguna de las validaciones nuevas os toca, porque no emitís ni
`argus.outcome` ni `argus.guardrail`»*, apoyado en la misma medición ciega. Hay
que corregirlo en el canal.

### Es la tercera

| | dónde se escapó |
|---|---|
| D-092 | la seudonimización cubría `span` y `log`, no `spanevent` — un seudónimo roto por el ámbito que faltaba |
| D-098 | la política `auditoria` necesitaba `ottl_condition` porque `string_attribute` solo mira el span |
| **D-113** | la **medición** mira `SpanAttributes` y no `Events.Attributes` |

Las tres tienen la misma forma: **el evento de auditoría de Prometheus vive en
el ámbito que nadie mira por defecto**, y es justo el dato que más importa
reconstruir. Y las tres veces lo encontré después de afirmar algo.

La diferencia esta vez es el sitio: ya no es el código, es **cómo mido**. Un
defecto en el código lo caza una prueba; un defecto en la consulta con la que
compruebo cosas se propaga a todo lo que afirmo con ella, y pasa el filtro
porque el número sale.

### Qué se hace

- Corregir `A-35` en el canal de Prometheus, con el número.
- Y la consecuencia que importa para ellos: **el vocabulario cerrado de
  `argus.outcome` no se les comprueba en el SDK**, porque `Step.outcome()`
  escribe atributos de span y su evento de auditoría lo construyen a mano.
  Sus valores de hoy (`ok`, `error`) son válidos; lo que no existe es nada que
  lo garantice mañana. Es el argumento con el que Aeon nos pidió validación en
  la ingesta, y ahora hay un segundo equipo en el mismo caso.

---

## D-114 · El factor del sesgo era una constante, y el sesgo no lo es

> **Fallo de plataforma** · descubierto 2026-10-02 · `argus.sampling.baseline_pct` valía 10 en TODOS los spans, también en los errores y las denegaciones que el muestreo conserva al 100 %, así que corregir un recuento con ese factor lo multiplicaba por diez justo donde ya estaba completo

**Fecha**: 2026-10-03 · **Versión**: `1.0.0a19`

### El defecto

D-054 puso el factor de muestreo dentro del dato por un motivo bueno: el 13/09
concluimos que Prometheus sondeaba ocho veces más un backend roto que uno sano,
su bucle era uniforme, y el sesgo era nuestro. La lección fue que el sesgo tenía
que ser **descubrible sin conocer la configuración del Collector**.

Pero lo que se escribió fue una constante:

```yaml
- set(span.attributes["argus.sampling.baseline_pct"], 10)
```

Y el muestreo **no es uniforme**: `tail_sampling` conserva el 100 % por cinco
políticas distintas (`errors`, `auditoria`, `genai-always`, `marked-hot`,
`slow`) y el 10 % por `baseline`. Un error y una petición normal llevaban el
mismo 10.

**Resuelve la mitad del problema que lo motivó.** La tabla de corrección de
D-054 lo enseña sin querer:

| | almacenado | real |
|---|---|---|
| 404 del backend roto | 190 | ~190 |
| 200 de los sanos | 115 | ~1150 |

Esa corrección se hizo **a mano**, sabiendo cuáles filas eran errores. El
atributo que nació de ese incidente no permite saberlo.

No era un número falso: era **ambiguo** —«la tasa del baseline es 10» frente a
«esta traza sobrevivió a un 10 %»— y la lectura cómoda es la equivocada. Es
D-111 otra vez, en un número en vez de en una frase. Y como allí, el sello de
garantía lo daba el sitio: un atributo que viaja en el dato se lee como un
hecho sobre ese dato.

Salió a flote verificando `argus.denied_by` (D-112): las denegaciones son una
dimensión nueva que los equipos van a contar, y las conserva `auditoria` al
100 %.

### El arreglo

`argus.sampling.retained_pct` — qué porcentaje de trazas **como esta** se
conservó — derivado de la política que de verdad la conservó, más
`argus.sampling.policy` como evidencia. El factor de corrección es
`100 / retained_pct`, y vale 1 para todo lo que se guarda entero.

Medido en el almacén, con la tubería real:

```
trabajo.normal      baseline    10     8 almacenados
fallo.duro          errors     100     1
decision.denegada   auditoria  100     1
```

Con el atributo viejo las tres filas decían 10.

### Tres cosas que se decidieron y por qué

**No se rellena cuando no se sabe.** Si la puerta del Collector está apagada,
no hay política y **no se escribe nada**. Un factor inventado rompe el cálculo
en silencio; su ausencia manda a quien consulta a las métricas, que es donde
D-054 dice que viven las tasas. Es la misma decisión que no hacer
`setdefault("unknown")` en `argus.denied_by`.

**Se sustituye, no se añade al lado.** Dejar `baseline_pct` junto a
`retained_pct` sería dejar la trampa con una señal al lado. Queda declarado
como nombre retirado en el test de documentos, porque D-054 y D-081 lo citan y
la historia no se reescribe.

**La prueba que hace fiable el número.** `retained_pct` es *derivado*: el 10 del
transform y el `sampling_percentage: 10` de la política son dos sitios del
mismo fichero. Una prueba los compara. Sin ella, bajar el baseline al 5 % dejaría
el almacén diciendo 10 y ninguna corrección fallaría de forma visible.

### Lo que costó, que es lo instructivo

**1 · El arreglo falló en silencio, igual que el defecto.** La puerta
`recordpolicy` no escribe en el span: escribe en el **InstrumentationScope**. Lo
escribí como `span.attributes["tailsampling.policy"]`, y `error_mode: ignore`
más una comparación contra `nil` **no es un error** — es un `where` que nunca se
cumple. Cero errores en el log, atributo ausente, y los tests de configuración
en verde porque comprueban el fichero y no el resultado.

Es la cuarta vez que un ámbito equivocado cuesta tiempo (D-092 `spanevent` en la
seudonimización, D-098 `string_attribute` en el muestreo, D-113 la consulta de
medición) y la primera en `scope`. El patrón ya no es casualidad: **en este
Collector, lo que se escribe en un ámbito distinto del que miras no da ningún
error, da un vacío.**

**2 · `docker compose up -d` no redesplegó nada.** El fichero va montado, así
que cambiarlo no cambia la definición del servicio y compose deja el contenedor
como está — sin decirlo. Verifiqué contra el almacén, vi los atributos vacíos, y
estuve un rato buscando el fallo en el OTTL que ya estaba bien. Lo delató
`StartedAt`. Tercera forma distinta de «editar la fuente no es desplegar»
(copia vieja del vigilante, imagen del canario sin reconstruir, y ahora
contenedor sin recrear): **un cambio de configuración necesita `restart`
explícito.**

Las dos juntas dan el aviso que importa: **este arreglo sin su despliegue es
peor que el defecto.** Sin la puerta encendida no hay política, no se escribe
el factor, y se pasa de un número equivocado a ningún número. Por eso hay una
prueba de que la puerta está en el `command`, y otra de que vive **fuera** de
`ARGUS_COLLECTOR_CONFIGS` — metida dentro, el overlay de GenAI la perdería al
sobreescribir esa variable entera, también en silencio.

---

## D-115 · 747 errores sueltos donde el 89 % era uno, y debajo seis días de fallos que nadie vio

**Fecha**: 2026-10-03 · **Versión**: `1.0.0a20`

### El problema, medido

La plataforma lleva semanas acumulando errores sin diagnóstico. El usuario lo
planteó así —*«ya existen muchos errores en diversos componentes y no tenemos
diagnóstico, análisis y explicación»*— y propuso ir de menos a más. Tenía razón,
y mi respuesta anterior (esperar tres semanas de histórico antes de la Fase 3)
optimizaba el futuro mientras el presente estaba a oscuras.

```
747 spans con status=Error en 7 días, 48 servicios
663 (89 %)  un solo patrón: el long-poll de vigilancia de Prometheus
 46         marcados Error SIN error.type  -> inagrupables
  4 de 747  llevan evento ancho            -> casi ninguno con contexto
735 / 703   sí tienen StatusMessage / evento de excepción
```

La última línea es la que decide el diseño: **la materia prima estaba.** No
faltaba capturarla, faltaba poder agruparla.

Debajo de ese 89 % había `TypeError` de Aeon, un `http-502` de una app que ni
estaba identificada, y errores en `auth.validate` y `token.issuance` **sin tipo**
con fecha de hoy. **Seis días invisibles**, no por falta de datos sino porque
663 líneas iguales los tapaban.

### El 89 % resultó ser nuestro portátil

Diagnosticado con dos consultas y sin ningún agente:

```
long-polls a /v1/backends?index=…&wait=60    n      p50      p95       max
  terminan bien                            4.233   60,4 s   61,0 s    72,6 s
  ReadTimeout                                662    566 s  1.062 s  22.343 s
```

El contrato se honra: p50 de 60,4 s en 4.233 vueltas. Y cruzando los fallos con
`pmset -g log`: **519 de 662 (78 %) solapan un episodio de sueño de este Mac**,
con dos coincidencias que no admiten duda —sueño de 371,8 min contra span de
372,4 min, y sueño de 325,7 min contra span de **325,7 min**—. El span está
abierto cuando la máquina duerme, el reloj corre, y al despertar el cliente
levanta `ReadTimeout`. **La duración del error es la duración del sueño.**

**Y casi se lo mandé a Prometheus como un bug suyo.** Tenía escrita la pregunta
de por qué su watch colgaba once veces más de lo que promete. Es `A-16` otra
vez —concluir sobre su tráfico sin mirar lo nuestro— y solo se cazó porque 6,2
horas era una cifra demasiado redonda para ser un bug. Tercera vez que lo que
salva es *la cifra es demasiado limpia*, no una prueba.

Quedan 143 sin explicar, mediana 8 min: ocho veces el contrato, y preguntados.

### Lo que hace la industria, y el orden importa

- **Sentry agrupa antes de razonar.** Fingerprint por error, los iguales a la
  misma *issue*, jerarquía determinista: pila → tipo de excepción → mensaje
  normalizado. **El modelo de ML entra solo como último recurso**, cuando la
  huella léxica no encuentra pareja. Primero lo barato y exacto.
- **New Relic** lo empaqueta como *errors inbox*: agrupación más análisis de
  impacto, «patrones en vez de duplicados».
- **Los exemplars de OTel** dan métrica → traza → log por `trace_id`, sin ML.
- **Los modelos de madurez coinciden en que no se saltan niveles**: instrumentar
  → agrupar y clasificar → evaluar → controlar.

### Qué se hace

`argus.error.kind` y `argus.error.fingerprint`, en el **gateway**.

**En el gateway y no en el SDK**, y es la decisión que importa: es el único
sitio que ve a todos los emisores. Cuatro servicios Go de Aeon y el evento de
auditoría de Prometheus construyen sus atributos a mano, así que una huella
calculada en el SDK de Python dejaría fuera justo a quien más falta le hace —el
mismo argumento por el que la validación de vocabulario en la ingesta sigue
siendo necesaria (D-113).

La huella es `(service.namespace, service.name, argus.error.kind, nombre del
span)`. **No lleva URL ni identificadores**, con prueba: el cursor del long-poll
cambia en cada vuelta, así que incluirlo daría 663 huellas para un problema.

Y la clase se rescata en dos pasos: `error.type` si está, y si no el
`exception.type` del **evento**, que es otro ámbito. Esa segunda mitad es la que
recupera los 46 inagrupables. `unclassified` es un valor y no un hueco, por el
criterio de `unknown` en `argus.actor.kind`.

Verificado con las formas reales: **35 errores sueltos, 30 URLs distintas → 3
líneas.**

### Lo que esto cambia respecto a lo que dije

Argumenté que la Fase 3 debía esperar porque con 39 incidentes y 39 huellas
distintas el agente de memoria no tendría nada que reconocer. Eso era cierto y
la conclusión estaba mal: **el problema no era la falta de histórico, era la
falta de agrupación.** Sin huella, cada incidente es único por construcción y
ninguna cantidad de tiempo lo arregla.

Así que esto no se desvía de la Fase 3: es su prerrequisito, y lo era desde el
principio sin que lo viera.

### Y un hallazgo del camino

El gateway borra `url.query` —0 supervivientes, comprobado— y **4.665 spans
llevan la query string dentro de `url.full`**, en claro. Hoy no hay nada
sensible ahí (`index`, `wait`, `include_hidden`, `repo_id`, `q`; cero `token`,
`key`, `secret`, `password`, arrobas), y el «hoy» es todo el problema. Cuarta
vez: **la protección vale lo que vale el sitio menos protegido donde aparece el
dato.** Queda como `B-26`.

---

## D-116 · El evento ancho existía y no se emitía donde las cosas fallan

> **Fallo de plataforma** · descubierto 2026-10-04 · de 747 spans con error en siete días, **cuatro** llevaban evento ancho y los cuatro eran sondas nuestras del mismo día: `emit_wide_event` es API pública que la aplicación tiene que llamar, y ninguna la llamaba

**Fecha**: 2026-10-04 · **Versión**: `1.0.0a21`

### El defecto

El plan describe los wide events como pieza central —*«que tus wide events SEAN
tus spans enriquecidos»*— y el SDK los implementa desde la Fase 1. Medido:

```
747 spans con error en 7 días, 48 servicios
  4 con `argus.event`   ->  y los cuatro son sondas nuestras del 03/10
735 con StatusMessage
703 con evento de excepción
```

**Ningún error real de ningún equipo tenía evento ancho.** No por falta de
datos: `emit_wide_event` está exportado en `__all__`, documentado, y **nadie en
el SDK lo llama**. Un patrón que hay que invocar a mano en el sitio donde las
cosas fallan es un patrón que no existe donde hace falta.

Es la tercera forma de lo mismo esta semana: D-109 (el aviso de identidad que
no existía), D-115 (la huella que había que poder calcular), y esta. Las tres
son *la capacidad estaba construida y el camino por defecto no la usaba*.

### La decisión que importa: un `SpanProcessor`, no un decorador

**Los errores no ocurren donde pusiste el decorador.** De los 747, los que más
importaban salían de spans auto-instrumentados —un `GET` de httpx, un
`auth.validate`, una activity de Temporal— que ningún `argus.step` envuelve.

Un `SpanProcessor.on_end` ve **cada** span que cierra en el proceso,
instrumentado por nosotros o no, así que la cobertura deja de depender de que
alguien se acordara. Y se engancha también en la rama de coexistencia —cuando
la app ya tenía su propio `TracerProvider`— porque una app a medio migrar es
justo donde menos contexto hay cuando algo falla.

Lo que lleva, y por qué cada cosa:

| | |
|---|---|
| la clase | `error.type`, y si no el `exception.type` del **evento**. Misma jerarquía que el gateway, y la segunda mitad rescata los 46 inagrupables |
| `unclassified` | un **valor**, no un hueco. Tercera vez que esa decisión se repite |
| el contexto de la instrumentación | ruta, método, código, sistema de BD. Gratis, y es lo que explica |
| **nunca** el contenido GenAI | decenas de KB por span, y esto existe para leerse. En trazas ya se borra; aquí no se copia |
| recortes | 512 caracteres por valor, 48 campos, y `argus.wide.truncated` si se corta — truncar en silencio es peor que no truncar |

### Cinco capas del mismo defecto de correlación

Lo instructivo de esta entrada. Un evento ancho que no lleva a su traza no
sirve para lo único que se le pide, y en `on_end` **el span que falló ya se
cerró**: el activo es el padre o ninguno. Eso rompe a la vez todo lo que lee el
contexto ambiental, y lo descubrí capa por capa:

| capa | qué decía | cómo se vio |
|---|---|---|
| el `LogRecord` | **bien**, porque puse `trace_id` a mano | prueba en verde |
| la línea JSON formateada | `trace_id: none` | mirando la salida real de la sonda |
| el campo `TraceId` de OTLP | **vacío** — y es el que usa el almacén para el `JOIN` | consultando ClickHouse |

Las dos primeras pruebas pasaban mientras la columna del almacén llegaba vacía.
**Parchear cada consumidor era perseguir capas**; lo correcto es re-activar el
contexto del span que falló mientras se emite, y entonces las tres aciertan
solas. Hay una prueba por capa, y la de OTLP es la única que se ve desde el
almacén.

De paso quedó un arreglo general en el filtro de correlación: **lo explícito
gana a lo ambiental.** Quien emite puede saber más que el contexto activo.

### Verificado en el almacén

```
evento          clase            mensaje         span al que salta   huella        politica
GET             ReadTimeout                      GET                 3149609a09ef  errors
auth.validate   PermissionError  scope ausente   auth.validate       e765dc07162e  errors
```

Un `INNER JOIN` por `TraceId` + `SpanId`. De un fallo salen ahora **la línea que
lo explica, la traza exacta, la huella con la que contarlo, y la política que
dice que no se muestreó** — las tres piezas de esta semana compuestas.

### Lo que no cubre, dicho claro

- **Solo en proceso.** Un error que nunca llega a un span de Python no deja
  evento ancho. Los servicios Go de Aeon necesitan el equivalente en su lado.
- **El evento ancho no se muestrea; su traza sí.** Para un error no importa
  —`errors` conserva el 100 %— pero si alguna vez se emiten eventos anchos de
  operaciones normales, la línea estará y su traza puede no estar. Va al README.
- **Siete propiedades con prueba, y las siete verificadas mutando el código.**
  Dos de las mutaciones estaban mal escritas y las tomé por pruebas que no
  cazaban: una rompía la sintaxis y otra no coincidía con el texto. **Una
  mutación que no muta no demuestra nada**, y se parece mucho a una prueba
  sólida.

---

## D-117 · `http.route` llevaba el id dentro, y la clave que lo evitaba no existía

> **Fallo de plataforma** · descubierto 2026-10-14 · `ASGIMiddleware` leía `scope.get("route_path")` para la plantilla de ruta, y esa clave no existe en ASGI ni en Starlette: aparecía una sola vez en todo el repositorio, en la línea que la leía, así que siempre caía al path resuelto y `POST /v1/cases/{case_id}/reprocess` llegaba al almacén con el UUID dentro

**Fecha**: 2026-10-14 · **Versión**: `1.0.0a22`

### El defecto

```python
route = scope.get("route_path") or path     # `route_path` NO EXISTE
```

Starlette expone la ruta resuelta en `scope["route"]`, un objeto con `.path` =
la plantilla. `route_path` no es de nadie. El `or path` lo tapaba.

Medido sobre el tráfico real de Veritium el día que se integraron:

```
http.route  /v1/cases/c1919a15-e1f0-4711-8f6f-2dcd5436cdd9/reprocess
SpanName    POST /v1/cases/c1919a15-e1f0-4711-8f6f-2dcd5436cdd9/reprocess
```

**Rompe tres cosas, las tres en silencio:**

1. `GROUP BY http.route` da **una fila por caso**, o sea ninguna respuesta.
2. `spanmetrics` deriva **una serie por caso**: cardinalidad sin techo en el
   almacén de métricas.
3. Y `argus.error.fingerprint` incluye el nombre del span, así que **cada
   expediente que fallara tendría una huella distinta** — exactamente lo que
   D-115 construyó para evitar, derrotado por un campo de más arriba.

La tercera es la que más duele: el arreglo de la semana pasada quedaba anulado
por un defecto de hace meses, y ninguno de los dos daba error.

### Por qué duró tanto

**Con una ruta estática el path resuelto ES la plantilla.** Así que `/mcp`
salía perfecto, los diez tests del middleware pasaban, y solo fallaba en las
rutas con parámetro — que son las que importan.

Y los tests no podían cazarlo por construcción: **construyen el `scope` a
mano**, como un diccionario, y la plantilla la pone el **router**. Un `scope`
inventado nunca tiene `route`, así que la rama buena jamás se ejercitaba. Es
la cuarta vez esta semana que el instrumento decide la conclusión: aquí el
instrumento era un diccionario literal.

La prueba nueva monta una aplicación FastAPI de verdad. Con el defecto puesto
falla; los diez viejos siguen pasando, que es la demostración de que no lo
cubrían.

### Y el otro, que es D-110 por tercera vez

Veritium reportó además que `argus.init("veritium-api", namespace="veritium")`
—**la forma que documenta la sección 3 de nuestra propia guía del piloto**—
disparaba *«arrancando sin identidad»* con el namespace bien puesto.

`Config.from_env` resolvía el namespace **solo desde el entorno** y avisaba
antes de aplicar los argumentos de `init()`. El aviso movido tras los
argumentos, y **antes** del relleno que hace caer el namespace al nombre del
servicio, porque después no distinguiría «lo puso» de «se lo rellenamos».

Tercera vez con esta forma exacta: `a16` con `OTEL_RESOURCE_ATTRIBUTES`, luego
el entorno, y ahora los argumentos. **Las tres veces el aviso era correcto en
su intención y falso en su momento.** Hay prueba de las dos mitades: que la
forma documentada no avise, y que siga avisando cuando la identidad falta de
verdad — un arreglo que quita el falso positivo quitando la comprobación es
peor que el falso positivo.

### Lo que el mismo día cerró el criterio 5 del piloto

Veritium entregó la traza `5fada06a68c2852fd0a3f3919ce73b88`, y es la primera
vez que ese criterio se cumple **con tráfico real** y no con una reproducción:

```
217 spans · 1 traza · 1 raiz (POST .../reprocess en veritium-api)
veritium-api -> aeon -> Temporal -> veritium-worker (3 activities)
conservada al 100% por la politica `slow`
```

**Una unidad de trabajo que cruza API → cola → worker produce UNA sola traza.**
Con el `TracingInterceptor` de `temporalio`, que es lo que les recomendamos.

Con una observación que hay que decir: la traza tiene **3 padres huérfanos**, y
son el salto de aeon. aeon **propaga el contexto y no emite nada** para
`POST /runs` —cero spans de esa ruta en doce horas— así que el `trace_id`
sobrevive y la cadena se sostiene, pero una vista de cascada muestra tres
subárboles flotando. No es defecto de Veritium ni del muestreo. Va avisado a
aeon.

### El registro

`intelligent-document-platform` pasa a `veritium`, y **sin alias del nombre
viejo**: se comprobó primero que ese namespace nunca emitió —cero spans en todo
el histórico— así que un alias sería una entrada que no protege de nada y que
alguien tendría que retirar. Es la diferencia con Prometheus, donde el nombre
viejo sí estaba en uso.

`idp-ocr` retirado a petición suya: es una librería que importan la API y el
worker, sin punto de entrada propio. Un componente es un proceso desplegable
por separado. Es lo mismo que Prometheus pidió con `manager-core`.

`veritium-worker` con `latido: false` porque los casos llegan a ráfagas, y
`veritium-api` con **un SLO medido y no supuesto**: el `reprocess` encola y
vuelve en **77 ms** —los 16,6 s se los lleva el worker— así que heredar los
8000 ms de `idp-api` era un umbral diez veces más flojo de lo necesario, que no
avisa de nada. Queda en 1000.

Y pasa a `activo` en el orden que les dijimos: cablean, lo verificamos en el
almacén, y entonces se activa. Verificado además que el registro **se recargó
en caliente** en el alert-bus (`registry.reloaded`, `apps: 12`) y no solo en el
fichero.

---

## D-118 · El componente cuyo trabajo es deduplicar multiplicó por 335

> **Fallo de plataforma** · descubierto 2026-10-14 · la firma de un error en el `alert-bus` era `error_type or span.status.message`, y el respaldo es texto libre: 460 errores idénticos de Postgres sin `error.type` dieron **335 firmas distintas**, 335 incidentes y 335 notificaciones, hasta saturar el canal humano

**Fecha**: 2026-10-14 · **Versión**: ninguna — el arreglo está en el
`alert-bus`, que es un servicio que desplegamos nosotros y no una librería
publicada

> **Corrección.** Esta entrada decía `1.0.0a23`, y esa versión **no existe**:
> las librerías están en `a22` y este cambio no toca ninguna. Se lo afirmé
> además a Veritium en `ARG-VRT-003`, corregido allí también. Escribí un número
> de versión por costumbre, en el único sitio donde no tocaba — y para ellos la
> diferencia importa, porque no tienen nada que instalar.

### Lo que pasó

Veritium —integrados el día anterior— emitió 460 errores en 22 minutos: la
columna `tenant` a null en `webhook_deliveries`. Un solo defecto suyo, repetido.

El dueño de la plataforma pidió apagar las notificaciones porque Telegram
estaba saturado. **Y la saturación no la causó el volumen de Veritium: la causó
nuestra deduplicación.**

```python
signature = str(error_type or span.status.message or "error")
```

El comentario de la línea de arriba decía, **con razón**, que `error.type` «es
de cardinalidad cerrada, así que sirve de firma». El respaldo era el mensaje,
que tiene la propiedad opuesta. Y Postgres adjunta al mensaje una línea
`DETAIL:` con los UUID de la fila que falló y su timestamp:

```
DETAIL:  Failing row contains (b929521c-…, 77e22778-…, 2026-10-14 11:18:11.307931+00, …).
```

Una firma por ocurrencia. **El razonamiento correcto pegado a una línea que
hacía lo contrario** — la forma de D-111, otra vez.

Medido: `error.type` presente en **2 de 460**; el evento de excepción, en **460
de 460**. La materia prima acotada estaba ahí y no se miraba.

### Tercera vez esta semana que algo se agrupa por un identificador

| | dónde |
|---|---|
| D-115 | el cursor del long-poll iba a entrar en la huella: 663 huellas para un problema |
| D-117 | la plantilla de ruta llevaba el UUID del caso, y con ella el nombre del span |
| **D-118** | la firma del incidente llevaba el `DETAIL:` de Postgres |

Las tres son la misma frase: **lo que agrupa no puede contener lo que
distingue.** Y en esta el coste fue inmediato y sobre una persona.

### El arreglo

Jerarquía acotada, la misma que ya aplican el gateway y el evento ancho:
`error.type` → `argus.error.kind` → `exception.type` del evento → `argus.event`
o el nombre del span → `"error"`. **Nunca el mensaje.**

El mensaje **sí** va al título, en una línea y recortado a 160 caracteres: es
lo que una persona necesita leer una vez, y sin él el incidente diría
`NotNullViolationError en veritium-api` sin decir de qué columna habla. Acotar
la firma no puede costar la información.

**Es la tercera vez que escribo esa jerarquía** —gateway, SDK y ahora
alert-bus— y eso es una señal: debería vivir en `argus-obs-semconv`, que es lo
único que los tres pueden importar. Queda como `B-27` y no se hace de paso.

La prueba reproduce el caso real: 400 ocurrencias con `DETAIL:` variable. Con
el defecto da **400 firmas**; con el arreglo, **una**.

### El silencio, y por qué es un campo y no un canal mal puesto

Telegram apagado para Veritium a petición del dueño. Eso rompió
`test_cada_activa_avisa_a_alguien`, y el test tenía razón: *«la consola es el
log del alert-bus, no la mira nadie»*. Dejarlo pasar habría convertido el
invariante en papel.

Así que el silencio se **declara**:

```yaml
silenciado:
  desde: 2026-10-14
  quien: emeric
  motivo: …
  reactivar_cuando: …
```

Y la prueba acepta la excepción **solo** si el campo está completo. **Un
silencio sin condición de vuelta es un silencio permanente que nadie recuerda
haber decidido.** Verificado quitando `reactivar_cuando`: la prueba falla.

Es el mismo principio de toda la semana —`unclassified`, `unknown`, ausente y
no cero—: **un hecho declarado, no una ausencia que se interpreta.**

### Lo que no se hizo, y es de ellos

El `tenant` null es código de Veritium y no se toca desde aquí. Se les pide
con el dato que tenemos: 460 ocurrencias entre las 11:10 y las 11:32, en
`webhook_deliveries`, y nada después. Telegram vuelve cuando lo arreglen y se
verifique 24 h limpias — las dos condiciones, escritas en el registro.

---

## D-119 · El SDK no decía qué versión era, y D-082 ya lo había arreglado en el otro paquete

> **Fallo de plataforma** · descubierto 2026-10-14 · los tres `get_tracer("argus-sdk")` del SDK no pasaban versión de scope, así que ningún span dice qué versión de nuestro código lo emitió: a la pregunta «¿qué versión de argus corre cada equipo?» solo se podía contestar de uno de los cuatro, y de rebote

**Fecha**: 2026-10-14 · **Versión**: `1.0.0a23`

### Cómo se notó

El dueño de la plataforma pidió la lista de qué espero y de quién, para
revisarlo con cada equipo. Una fila obvia de esa lista es **qué versión corre
cada uno**, y resultó que no se podía medir:

```
veritium    argus-semconv 1.0.0a21   <- por `semconv`, no por el SDK
veritium    argus-sdk     (vacio)
```

De los cuatro equipos integrados, la versión solo se pudo medir de **uno**, y
de rebote: Veritium usa `argus-semconv` además del SDK. De Prometheus, Prosodia
y Aeon la respuesta era **lo que nos habían dicho, no lo que medimos** — que es
exactamente la distinción que llevo toda la semana cobrándole a otros.

### Y el arreglo ya existía, en el otro paquete

`argus-semconv` declara la versión del paquete en el scope **desde `1.0.0a4`**,
y lo hace porque se arregló: hasta entonces registraba ahí la versión del
MODELO, que vale `1.0.0` desde hace mucho, así que un prelanzamiento y una
estable declaraban lo mismo y en el almacén no se podían separar (D-082).

**Ese arreglo no se aplicó al SDK.** Y el SDK quedó peor que el estado que
D-082 corrigió: allí había un número equivocado, aquí **no había número**.

Es la misma forma que llevo coleccionando toda la semana, y esta vez contra mí
mismo con la lección ya escrita: *una verdad en dos sitios, y el que nadie toca
es el que se consulta.* El fichero `_scope.py` de `semconv` explica el problema
y la solución en su docstring. Lo había leído.

### Qué se hace

`libs/argus-sdk/src/argus/_scope.py`: la versión del paquete en el scope, y
**nada más**.

La primera versión publicaba además `argus.sdk.package` como atributo de scope,
espejo de lo que hace `semconv`. **Lo cazó la prueba de documentos** —un
`argus.*` citado en `decisions.md` y no declarado en el modelo— y al ir a
declararlo apareció el motivo real para no tenerlo:

```
columnas de Scope en otel.otel_traces:  ScopeName, ScopeVersion
columnas para los atributos del scope:  ninguna
```

**Los atributos de scope no tienen dónde caer.** Y lo que ese atributo decía
—qué paquete emitió— ya lo dice `ScopeName`.

Con una consecuencia que no era mía y que conviene no perder:
`argus-semconv` publica `argus.semconv.version` desde `1.0.0a4`, por este mismo
D-082, y **no está en ninguno de sus 3.406 spans almacenados**. Veinte versiones
emitiendo a la nada. Queda como `B-28`, y no se toca de paso porque es el otro
paquete y la decisión tiene tres salidas distintas.

Así que la prueba de documentos, que existe por D-081 para que no mandemos
fuera un nombre que el paquete no conoce, acabó encontrando un atributo que
**ningún** consumidor podía conocer. Es el mejor resultado que ha dado.

**Una propiedad útil del arreglo: la ausencia pasa a ser informativa.** Un span
de `argus-sdk` sin versión de scope es, desde ahora, un emisor anterior a
`a23`. Así que la lista que no se podía hacer se puede hacer incluso para quien
no haya subido.

### La prueba, y por qué recorre el árbol y no el texto

Lo que falló no fue un tracer, fue **que uno de los tres se quedó atrás**. Así
que la prueba no comprueba un tracer concreto: recorre el paquete y exige que
**no haya ninguno** sin versión.

La primera versión era una expresión regular sobre el fuente, y su único
hallazgo fue **el docstring de `_scope.py`**, que cita la línea mala para
explicarla. Un `grep` no distingue código de prosa, y en este repositorio la
prosa habla mucho del código — ya me costó una vez una comprobación que casaba
dentro de una cadena. Reescrita con `ast`, que solo ve llamadas.

Y hay una prueba de la prueba: que el docstring **siga citando** la línea mala.
Si esta comprobación volviera a ser un `grep`, lo probable es que alguien
borrara la cita en vez de la expresión regular.

### Y mi mutación estaba rota otra vez

Al verificar que la prueba caza una regresión, la primera mutación **no se
aplicó** —la cadena no casaba por la indentación— y la prueba pasó. La tomé por
buena durante un momento.

**Tercera vez esta semana.** Una mutación que no muta no demuestra nada y se
parece mucho a una prueba sólida. Lo que lo cazó fue comprobar que el fichero
cambió antes de leer el resultado, que es lo que debería hacer siempre y no
cuando me acuerdo.

---

## D-121 · Nuestra red de privacidad era de un país, y eso lo decide quién la escribió

**Fecha**: 2026-10-15 · **Versión**: `1.0.0a24`

### Lo que Veritium encontró

El `redaction` del gateway es la segunda capa de privacidad, y nuestra propia
guía dice que **es la que importa**: *un fallo de instrumentación en una app no
se convierte en una fuga en el almacén*. Para Veritium no actuaba.

```
patron de documento:  \b\d{8}[A-HJ-NP-TV-Z]\b   <- ocho digitos Y UNA LETRA
```

Es el DNI **español**. El peruano son ocho dígitos solos y no lo reconoce.
Tampoco el carné de extranjería (9–12) ni el RUC (11). Y el de teléfono exige
separadores, así que un celular escrito seguido pasa.

**La capa que existe para cuando la primera falla no conocía los
identificadores del país de una de las cuatro aplicaciones integradas.** Para
esa aplicación, la red no estaba.

Y no es hipotético: el `NotNullViolationError` que saturó el canal (D-118)
traía el `DETAIL:` de Postgres con **la fila entera**.

### Por qué no se amplía el patrón global

Veritium lo propuso como alternativa y lo descartó en el mismo mensaje, con
razón: un `\b\d{8}\b` para todos taparía fechas como `20261015` y cualquier
contador de ocho cifras de cualquier equipo. Nuestro propio comentario del
teléfono dice lo mismo desde hace semanas — *una tirada suelta de dígitos es
casi siempre un identificador, un timestamp o un contador* — así que su
observación y nuestro razonamiento coincidían; lo que faltaba era la vía.

### Lo que se hace, y una cosa que no es lo que pidieron

`transform/redaccion_por_app`, con OTTL acotado por `service.namespace`.
**No se puede hacer con el processor `redaction`**: sus `blocked_values` son
globales a la instancia.

Cubre los tres ámbitos donde vive el texto libre: `span.status.message` y
`error.message` en el span, `exception.message` y `exception.stacktrace` en el
**evento** —el ámbito que se escapó en D-092, D-098 y D-113— y el cuerpo y
`error.message` en los registros.

**Pidieron que los patrones vivieran en el registro de la app y están en la
configuración del Collector.** El motivo es el de esta semana: el registro es
dato y se **recarga en caliente**; esta configuración es estática y necesita
reiniciar. Un patrón de privacidad declarado en el registro **se leería como
activo mientras no hace nada** hasta el siguiente reinicio, y para un control
que protege a personas ese es el peor modo de fallo que se puede elegir. Es
D-114 y D-117 aplicados a algo que importa más.

### El tercer criterio no se cumplía, y era el valioso

Su criterio tenía tres partes y **la tercera no se cumplía**: *«el conteo de
redacciones sube»*. Ese contador es del processor `redaction`; `transform` no
cuenta nada.

Y es la parte que de verdad sirve, por nuestro propio plan: *un pico en el
conteo de redacciones significa que alguna app empezó a filtrar PII*. Un caso
suelto no dice nada; el agregado es la única forma de enterarse de que la
primera capa falló.

Así que `argus.redaction.app_pattern`, puesto con `IsMatch` **antes** del
reemplazo —después no queda nada que detectar— y declarado en el modelo.
Verificado en el almacén: `veritium: 2`, y la otra app sin marca.

### Lo que esto NO arregla, dicho antes de que lo descubran

El `mask` del SDK sigue con los patrones globales. Así que un dato peruano
enmascarado en origen y uno enmascarado en el gateway **no salen iguales**, y
eso contradice el comentario de `_content.py` que dice que las dos capas tienen
que coincidir *«o los datos salen distintos según por dónde pasen»*.

Se acepta porque esta capa es la red y la de origen es suya —tienen su
`scrub.py`— pero es una asimetría real, y la escribo aquí porque la alternativa
es que la encuentren ellos.

### Y el carné de extranjería se queda fuera a propósito

Pidieron DNI (8), CE (9–12) y RUC (11). Van el de 8 y el de 11. **El rango 9–12
incluye 10, que es un timestamp epoch en segundos**, así que redactarlo
convertiría cada marca de tiempo en `[CE]`. Queda como decisión suya con el
dato delante, no como olvido.

---

## D-122 · La plataforma no se observaba a sí misma, el aviso lo decía, y nadie leía stderr

> **Fallo de plataforma** · descubierto 2026-10-15 · `canary` y `alert-bus` exportaban OTLP/HTTP JSON al puerto **4317**, que es el de gRPC, así que no llegó ni un span al almacén desde el **20 de septiembre** — 609 horas. El aviso de arranque del SDK lo detectaba correctamente en cada arranque y nadie lo leía, porque es un `RuntimeWarning` a stderr

**Fecha**: 2026-10-15 · **Versión**: `1.0.0a25`

### La frase que lo encontró es de Prometheus

> *«Una sonda de disponibilidad que ejercita menos superficie que el servicio
> declara es una sonda que certifica lo que no probó.»*

Les pasó con un modelo de visión que llevaba meses sin poder ver mientras su
sonda decía sano — porque una sonda de disponibilidad manda texto. Lo apunté
como deuda nuestra porque **nuestro canario tenía esa forma exacta**: cuatro
sondas HTTP contra `/healthz`, `/ping`, `/health` y el `health_check` del
Collector. Las cuatro dicen que un proceso contesta.

**Ninguna decía que la telemetría llegue al almacén**, que es la única función
distintiva de esta plataforma.

### Lo que la sonda nueva encontró en su primer ciclo

```
spans de `canary` y `alert-bus` en el almacen
  ultimo:  2026-09-20 02:08   ->  609 horas
mientras, en la ultima hora:
  veritium 12.084 · prometheus-inference-platform 3.209
```

**Argus no se observaba a sí misma desde hacía 25 días.** Y la cadena de verdes
es completa: `/healthz` 200, `/ping` 200, el `health_check` del Collector 200, y
las sondas de silencio de `argus` no aplican porque sus dos componentes son de
rol `infra`, que está en `ROLES_SIN_LATIDO`.

### La causa, y es de las que dan vergüenza

```
otelcol_receiver_accepted_spans_total{transport="http"}   25.828
otelcol_receiver_refused_spans_total{transport="http"}         0
gRPC:  ningun contador — no habia llegado nada nunca
```

Los dos servicios no instalan el extra `grpc`, así que el SDK habla **OTLP/HTTP
JSON** y lo mandaban al **4317**, que es el puerto de gRPC. Nada llegaba, y no
había error: un POST HTTP a un puerto gRPC se pierde sin ruido.

### Y lo peor: el aviso existía, funcionaba, y lo escribí yo

```
RuntimeWarning: Argus: protocolo 'http/json' contra el puerto 4317, que es el
de grpc (http/json usa el 4318). Si no lo has elegido a proposito, el protocolo
se autodetecto por los paquetes instalados: fija ARGUS_PROTOCOL.
```

Ese aviso lo construí en `a16` **para exactamente este caso**, y llevaba
imprimiéndose en cada arranque del canario desde entonces. Diagnóstico perfecto,
texto exacto, acción concreta.

**Nadie lo leyó**, porque un `RuntimeWarning` va a stderr: no llega al almacén,
no abre incidente, no despierta a nadie y no aparece en ningún tablero.

Es la versión más dura de lo que Axonium me dijo esta semana —*«un aviso que no
entiendes no se silencia»*— porque aquí no es que no se entendiera: **es que no
se veía**. Y la ironía exacta es que el servicio ciego era el canario: el
proceso cuyo trabajo es que ningún silencio pase inadvertido.

### Lo que se hace

**El arreglo inmediato**: los dos al 4318. Verificado — el aviso dejó de tener
motivo y la telemetría de la plataforma vuelve a llegar.

**El arreglo que importa**: `SondaDeIngesta`. Manda un span con una marca
**única por ejecución** y comprueba que se puede consultar en ClickHouse. Si no
aparece en el plazo, `CAIDO`.

Tres decisiones que la hacen una sonda y no un adorno:

| | |
|---|---|
| **marca única** | sin ella, una fila de hace una hora la haría pasar con la ingesta parada — la misma sonda que certifica lo que no probó, dentro de la clase escrita para evitarlo |
| **política de muestreo propia** | sin ella sería una tirada de dados del 10 % y reportaría caído nueve de cada diez veces: un canario que se silencia solo |
| **plazo con suelo** | el muestreo espera 30 s a que la traza cierre. Un plazo menor es un generador de falsos caídos, y la sonda se niega a aceptarlo |

Y **no se llama «extremo a extremo»**, que es lo que me salía escribir. Mide
`gateway → muestreo → ClickHouse`; el salto del Collector **agente** no lo
cubre, porque este canario exporta al gateway. Nombrarla por más superficie de
la que ejercita sería repetir el defecto que viene a arreglar.

### Lo que queda abierto, y es más grande que esto

**Los avisos de arranque del SDK no son una señal de la plataforma.** Son
`warnings` de Python. Todo lo que construí en `a16`, `a17` y `a23` —identidad
ausente, entorno fuera de vocabulario, protocolo contra el puerto equivocado—
vive en stderr, en los logs de un contenedor que nadie abre.

Cuatro equipos integrados y cada uno de esos avisos es exactamente el tipo de
cosa que uno quiere ver **el día que integra**. Queda como `B-30`: que un aviso
de arranque llegue al almacén como evento y pueda abrir un incidente de
severidad baja. Hoy el único que lo habría visto es quien ya estaba mirando el
sitio correcto.

---

## D-123 · La protección estaba en el comentario y no en el campo, y notificó a una persona

> **Fallo de plataforma** · descubierto 2026-10-24 · el componente `tei` se declaró con `estado: planificado` **en un comentario** y sin el campo, así que la sonda de silencio lo vigiló y abrió `tei dejó de emitir telemetría` con severidad `page`, notificando a la 01:36 por un componente que nunca se cableó

**Fecha**: 2026-10-24

### Lo que escribí

```yaml
# Declarado en `planificado` y no en `activo` a proposito: `activo`
# enciende la sonda de silencio, y un componente que todavia no esta
# cableado gritaria cada cinco minutos por algo sabido (D-094).
- {id: tei, rol: model-server, slo: {p95_ms: 500}}
```

El comentario afirma una protección. **La línea no la implementa.** Y el
mecanismo existía: `build.py` lee un `estado` por componente y salta el que no
esté `activo`. Lo tenía a mano y escribí la protección en prosa.

Coste: un `page` real a una persona, a la 01:36, por un componente que
Prometheus aún no ha cableado — exactamente el ruido que el comentario decía
estar evitando. Es D-094 citado en el comentario que lo reintroduce.

### Y la forma es la de toda la semana, en su versión más corta

| | |
|---|---|
| D-117 | `scope.get("route_path")`: una clave que no existe, con el `or` de al lado tapándolo |
| D-118 | «`error.type` es de cardinalidad cerrada, así que sirve de firma» — y el respaldo era el mensaje |
| D-122 | un aviso que detectaba el defecto perfectamente, en stderr, 25 días |
| **D-123** | la protección en el comentario y no en el campo |

Las cuatro: **la prosa correcta al lado de la línea que hace lo contrario.** Y
en esta la prosa citaba la decisión que la línea violaba.

### Qué se hace

`estado: planificado` en el campo. Verificado: el canario pasa de una sonda de
silencio sobre `tei` a cero.

### Y una corrección sobre la caída de hoy

Al revisar esto afirmé que mi plataforma había visto la caída de
`POST /oauth2/token` de Prometheus **y no había dicho nada**. Era falso.

El incidente de quema de presupuesto sobre su gateway abrió a las **12:06:46**
—los 500 estaban en la hora de las 12— y **notificó a las 12:07:51**, un minuto
después. Lo que hice mal fue consultar los incidentes *por error* y no la tasa:
la señal existía en el camino que no miré.

Cuarta vez esta semana que mi primera consulta contesta una pregunta distinta de
la que hice. Y van dos en las que concluí contra mí mismo por eso.

---

## D-124 · «Verificado» era verificado en el árbol, no en el proceso

> **Fallo de plataforma** · descubierto 2026-10-24 · D-123 cerró con «verificado:
> el canario pasa de una sonda de silencio sobre `tei` a cero», y **el canario
> siguió sondeando `tei` más de quince horas**: el incidente abierto a la 01:36
> llegaba a `count` 166, y mientras lo miraba subió de 165 a 166 — una señal
> nueva por cada ciclo de cinco minutos del canario

**Fecha**: 2026-10-24

### Lo medido

| | |
|---|---|
| `apps.yaml` en el host | 19.463 B, `md5 7517bfc4…`, modificado hoy 16:46 UTC |
| `apps.yaml` **dentro del canario** | 18.968 B, `md5 3b88c116…`, `st_mtime` del **15/10 11:42** |
| Última recarga del canario | `canary.probes_reloaded` del **15/10 11:43** — nueve días |
| Código del canario | imagen construida el 15/10 11:37, **sin el `estado` por componente de hoy** |

El alert-bus, con un montaje idéntico del mismo fichero, **sí** recargó: a las
16:46:18, cuatro segundos después de la edición, de 13 a 15 aplicaciones. El
canario no.

### La causa

Dos capas, y hacían falta las dos:

1. **El proceso corría código de hace nueve días.** El `estado` por componente
   que `build.py` lee es un cambio de HOY. El contenedor llevaba `Up 9 days`:
   la comprobación de que `build.py` soporta el campo la hice sobre el fichero,
   y el intérprete tenía el módulo de octubre en memoria.
2. **Un bind mount de un fichero suelto fija el inodo, no la ruta.** Editar
   reemplazando el fichero deja al contenedor pegado al inodo viejo — que para
   él sigue existiendo, con su tamaño y su `st_mtime` de entonces. Y `firma()`
   es exactamente `stat().st_mtime`, así que la recarga automática que D-094
   introdujo *para no tener que reiniciar* **queda congelada en silencio**.

Lo segundo es lo que convierte un despliegue olvidado en un fallo permanente:
sin recarga, el canario no se corrige nunca solo.

### Lo que esto hace con D-123

No lo contradice: el campo estaba bien puesto. Lo que estaba mal era la palabra
**verificado**. Verifiqué el árbol y las pruebas, y escribí una frase sobre el
sistema en marcha.

Es la quinta forma de *editar no es desplegar* de esta semana, y la primera en
la que **el verificador era yo**:

| | |
|---|---|
| Veritium | el proceso antiguo seguía en pie |
| yo, el 14/10 | `up -d` no recreó el Collector con la config montada |
| Prometheus | fusionado y no desplegado |
| aeon | `uv.lock` sin regenerar |
| **yo, hoy** | **verificado en el árbol, con el proceso corriendo lo de hace nueve días** |

### Qué se hace

- `docker compose build canary` + `up -d --force-recreate canary`. Verificado
  **en el proceso**: los dos `md5` coinciden, el canario nuevo no menciona `tei`
  ni una vez, y su `count` se quedó clavado en el 166 del canario viejo en vez
  de subir al ciclo siguiente. El barrido lo resolvió a los 900 s, y el tablero
  de incidentes abiertos ya no lo lleva — comprobado, no deducido.
- El montaje de fichero suelto pasa a `B-31`: mientras siga así, cualquier
  edición del registro puede dejar de propagarse sin que nada lo diga.
- Y la regla, que es la que vale: **una afirmación sobre el sistema en marcha se
  comprueba contra el sistema en marcha.** `md5` dentro del contenedor, no `git
  diff`.

---

## D-125 · La jerarquía del error pasa a un sitio, y dos de las tres copias ya habían divergido

**Estado**: ✅ Vigente · `B-27` cerrado

**Fecha**: 2026-10-24

**Contexto**. Lo que agrupa los errores no es un campo: es un **orden de
preferencia** entre campos. Ese orden estaba escrito tres veces —el OTTL del
gateway, `_wide.py` del SDK, `normalize.py` del alert-bus— y las tres copias
llevaban un comentario afirmando que coincidían.

**Lo que se encontró al reunirlas**:

| | pasos | valor terminal |
|---|---|---|
| gateway | `error.type` → `exception.type` | `unclassified` |
| SDK | `error.type` → `exception.type` | `unclassified` |
| alert-bus | + `argus.error.kind`, + la operación | **`error`** |

Un valor terminal distinto en una de las tres **parte en dos el `GROUP BY` de
todo lo inclasificable**. Y no lo cazaba nada: el docstring del evento ancho
afirmaba que *«hay una prueba que compara la jerarquía de las dos»* y **esa
prueba no existía**. Lo que había eran dos pruebas de comportamiento con la
frase «misma precedencia que en el gateway» escrita en prosa, y ninguna que
leyera `gateway.yaml`.

**Decisión**. La parte canónica vive en `argus_semconv.errors`, que es lo único
que el SDK y el alert-bus pueden importar los dos. El gateway no puede importar
Python, así que su copia en OTTL se queda — y lo que la ata es
`test_la_jerarquia_del_error_es_una`, que **lee `gateway.yaml`** y compara el
orden contra `ORDEN`.

**El matiz que importa**. El respaldo del alert-bus a la operación no se
elimina: una señal tiene que llegar con firma siempre. Pero pasa a ser una capa
**explícita encima** de la jerarquía, no otra versión de ella. Lo compartido es
lo que debe coincidir; lo propio se ve.

**Verificado con mutaciones**, porque una prueba que no puede fallar no prueba:
invertir `ORDEN`, cambiar el terminal del gateway y volver a teclear
`exception.type` en el SDK hacen fallar cada una su prueba, y solo la suya.

---

## D-126 · `argus.semconv.version` se retira: el sitio donde «pertenecía» no existe

**Estado**: ✅ Vigente · `B-28` cerrado

**Fecha**: 2026-10-24

**Contexto**. D-082 movió la versión del modelo al atributo del scope, «que es
donde pertenece». La frase era razonable. El sitio no existe: la tabla de
trazas tiene `ScopeName` y `ScopeVersion` y **ninguna columna para los
atributos del scope**. Medido: 3.406 spans lo llevaban desde la `a4` y **en
ninguno estaba almacenado**. Mes y medio emitiendo a la nada.

**Decisión**. Retirarlo, que era la tercera de las tres salidas que `B-28`
planteaba (columna, atributo de span, o retirar). Y se elige por la **segunda**
razón, no por la primera: `modelo.json` viaja DENTRO del paquete, así que una
versión del paquete determina exactamente una versión del modelo.
`ScopeVersion` ya responde las dos preguntas. Hacerle una columna habría sido
darle sitio a un dato redundante.

**Lo que sujeta el argumento**: una prueba comprueba que `modelo.json` sigue
viajando en el paquete. Si algún día el modelo se pudiera instalar aparte, la
versión del paquete dejaría de determinar la del modelo y habría que volver a
mirarlo.

Los dos paquetes dicen ahora lo mismo: el SDK llegó a esta conclusión por su
cuenta en D-119 y lo documenta en `argus/_scope.py`.

---

## D-127 · La query string se borraba del campo donde no estaba

**Estado**: ✅ Vigente · `B-26` cerrado

> **Fuga de datos** · `url.query` tenía **0 supervivientes** en 30 días y el
> comentario daba el trabajo por hecho. En la misma ventana y el mismo almacén:
> **32.997 spans con la query dentro de `url.full`** y **68.246 dentro de
> `http.url`**, en claro

**Fecha**: 2026-10-24

**Lo que estaba mal**. `transform/pseudonymize` borraba `url.query` —el campo
dedicado— y dejaba intacto el campo **compuesto**, que es por donde la query
viaja de verdad. Tres agujeros, no uno:

1. `url.full` sin tocar, 32.997 spans.
2. `http.url` sin tocar, 68.246 spans. Es la grafía **deprecada** de OTel y es
   la que más tráfico trae, porque la instrumentación de cliente que no se ha
   actualizado sigue poniendo esa.
3. El ámbito de **registros** no borraba `url.query` en absoluto —el de spans sí
   y ese no, con el mismo processor en los dos pipelines—. Y es por ahí por
   donde sale el evento ancho del SDK, que copia **todos** los atributos del
   span que falló a una línea de registro.

**Y el aviso estaba al lado.** Veinte líneas más abajo, en el mismo fichero:
*«una regla que solo cubre la grafía canónica protege de la telemetría que
escribes tú y no de la que recibes»* — escrito para `user.id`/`user_id`. El
mismo fallo, un campo más allá, con su propia lección en pantalla.

**Qué se filtraba hoy**: `index`, `wait`, `include_hidden`, `repo_id`, `q`. Nada
sensible, y eso es exactamente lo que hacía que no se mirara. El «hoy» es el
problema: un `?token=` de un equipo nuevo entra sin tocar nada y sin que nada
avise.

**Decisión**. `replace_pattern` sobre `url.full` y `http.url` recortando desde
el `?`, en los **tres** ámbitos, más el borrado de `url.query` en los dos que no
lo tenían.

**Verificado contra el stack en marcha** —no contra el árbol, que es la lección
de D-124—: span enviado al gateway recreado con `?token=SECRETO_*` en los cuatro
sitios, y en ClickHouse `url.full` y `http.url` llegan sin query, `url.query`
vacío, el `url.full` del evento de excepción recortado, y **cero apariciones**
de la cadena en atributos de span, de evento y en el mensaje de estado.

---

## D-128 · El validador de configs no validaba una de las dos configs

**Estado**: ✅ Vigente

**Fecha**: 2026-10-24

`platform/scripts/validate-collector.sh` exporta ocho variables de relleno para
que el binario del Collector pueda resolver las configs. **No exportaba
`ARGUS_HOST`**, que `agent.yaml` exige para el `upsert` de `host.name` (D-104).

Efecto: el validador salía con `FALLO agent.yaml` en **cada** pasada, decía la
causa exacta en la línea siguiente, y `F0-07` —«validación de configs contra el
binario real»— seguía marcado hecho. De las dos configs del plano central, la
del agente **no se había validado nunca** por el script que existe para eso.

Lo encontré porque lo ejecuté para comprobar mi propio cambio en el gateway, y
tuve que mirar dos veces para convencerme de que el fallo no era mío.

Es la forma de D-122 en una herramienta: **un aviso correcto, en un sitio que
nadie lee, repetido sin cansarse.** Y añade una propia: un verificador que
siempre falla un poco deja de ser un verificador, porque el rojo pasa a ser el
color normal.

Arreglado con una línea. Las tres configs validan y el script sale con 0.

---

## D-129 · Dos relojes en la misma línea de tiempo, y una alarma mía que no era

**Estado**: 🚧 Medido, arreglo en `B-34`

**Fecha**: 2026-10-24

### Primero, la corrección

Al volver una orden que se había ido a segundo plano, leí que host y contenedor
daban horas distintas —22:19:58 frente a 02:59:37— y escribí que **«el reloj del
Mac saltó 4 h 38 min»**. No hubo salto. `pmset -g log` lo dice entero:

```
21:52:59  Sleep      Entering Sleep state due to 'Maintenance Sleep'
21:55:17  Sleep      …
21:57:38  Sleep      …
21:59:25  Wake       Wake from Deep Idle … lid … HID Activity
```

La máquina estuvo durmiendo por ciclos y despertó a las 21:59. **El tiempo pasó
de verdad**, que es exactamente lo que había concluido media hora antes y lo que
me desdije a mí mismo sin necesidad. Y la primera comprobación que hice para
descartarlo —si `.env` afectaba a `date`— **contestaba otra pregunta**: quinta
vez esta semana.

También supuse que el desfase había resuelto en masa los cinco incidentes de
Veritium. **Falso, y se mide**: los cinco se resolvieron con un hueco de
**15,1 min** sobre un umbral de 15,0. Barrido normal; los errores dejaron de
llegar.

### Y lo que sí había debajo

La lectura rara era síntoma de algo real. El incidente que quedó abierto al
despertar —`ReadTimeout` en el gateway de Prometheus, severidad `page`,
notificado— tiene esto:

```
notificado : 2026-10-25T02:42:55Z
abierto    : 2026-10-25T02:50:15Z     ← 7,3 min DESPUES de notificarse
```

**Porque son dos relojes.** `opened_at` es `signal.occurred_at`, la marca que
pone el **emisor**; `notified_at` la pone el **motor** con la suya. Y aquí el
emisor es el centinela del long-poll, que corre **nativo** en el Mac, mientras
el alert-bus vive dentro de la VM de Docker — los dos relojes que vi diferir
4 h 39 min en una sola orden durante el despertar.

Medido sobre el almacén: **4 de 562** incidentes notificados tienen
`notified_at` anterior a `opened_at`, hasta 13,6 min, y **los cuatro del mismo
camino**. Poco, y no es cosmético:

- `age_seconds` es `_now() - opened_at`, así que puede ser **negativo**.
- La ventana de agrupación compara `ahora - opened_at >= group_window`. Con una
  marca futura eso es falso siempre, así que un incidente de severidad menor
  **se queda dentro de la ventana** hasta que el tiempo real lo alcanza. Los
  `page` se salvan solo porque D-015 los notifica de inmediato.

No se arregla de paso: toca **cuándo se notifica**, y eso se decide aparte
—igual que `B-33` toca cómo se agrupa—. Queda en `B-34`.

### Y el `ReadTimeout` sigue siendo `B-25`

El incidente que abrió al despertar es el falso positivo que `B-25` describe
desde el 14/10: el long-poll del centinela caduca cuando la máquina duerme, y
eso **no es un fallo del servicio de Prometheus**. Esta vez con severidad `page`
y notificado a una persona. Un instancia más, y la segunda página falsa del día
después de `tei` — las dos por algo que la plataforma sabe de sí misma y todavía
no usa para callarse.

---

## D-130 · El titular propone una causa; no la identifica

**Estado**: ✅ Vigente · verificado en el contenedor que corre

**Decisión**. El titular de un aviso tiene **tres** estados, no dos, y los
decide la pareja `(root_cause, confidence)`:

| | titular |
|---|---|
| sin causa | `investigando…` |
| causa, confianza `baja` | `causa posible` |
| causa, confianza `media` o `alta` | `causa probable` |

Y **nunca** «causa identificada».

**Por qué**. Decía dos cosas, y el problema no apareció leyendo `render.py`
sino escribiendo quién rellena ese campo. L3 tiene un caso frecuente:
recolecta el expediente entero y **ninguna regla concluye**. Con dos estados,
la única forma de entregar ese contexto era escribir una descripción del
síntoma en `root_cause` — y entonces el titular anunciaba una causa
identificada que nadie había identificado.

La alternativa era dejar `root_cause` vacío y no mandar nada, que es la que se
tomó para el campo: **`root_cause` se queda en `None` y la evidencia se manda
igual**. Un aviso que dice «investigando…» y debajo trae «0 de 44 peticiones
fallaron en 5 min» y «primera vez que esta firma abre incidente» ya es
utilizable; no hacía falta mentir en la primera línea para conseguirlo.

Lo de «identificada» va aparte y es lo que más importa de las tres filas:
identificada la deja **una persona** al confirmarla. L3 propone. El titular es
lo único que se lee en el móvil, y un aviso que afirma de más manda a alguien
a mirar donde no es.

**Consecuencias**. `test_el_titular_cambia_cuando_hay_causa` pasó a esperar
`causa probable` y a comprobar que `identificada` **no** aparece; los tres
estados y sus bordes viven en
`test_el_titular_no_afirma_mas_de_lo_que_sabe.py`. Un `confidence` ausente
—el endpoint `enrich` acepta cualquier subconjunto— cuenta como probable: quien
escribió una causa sin calificarla no estaba diciendo que dudara de ella.

---

## D-131 · El ensayo contra el stack real encontró tres fallos que ninguna prueba podía encontrar

**Estado**: ✅ Vigente · los tres corregidos y vueltos a medir

**Decisión**. L3 no se da por hecho con la suite en verde. Hay un **ensayo en
seco contra el stack real** —recolecta y juzga, no enriquece— y es lo que
decide si las consultas y las reglas valen.

**Por qué**. Porque la suite estaba en verde, 48 pruebas, y las tres cosas de
abajo estaban rotas a la vez. Las tres son del mismo tipo: **no son errores de
lógica que un dato inventado revele, son errores sobre cómo es el mundo.**

### 1. Las seis consultas devolvían `400`, y la degradación lo tapó

`Almacen.consulta` mandaba el SQL y los parámetros juntos como formulario
—`data={"query": …, "param_x": …}`—. ClickHouse recibe entonces
`query=SELECT…&param_x=…` **como la sentencia entera** y responde `400` a
todo. Lo correcto es el SQL en el **cuerpo** y los parámetros en la **URL**.

Lo que lo hace interesante no es el fallo, es cómo se vio. Las pruebas usan un
almacén falso, que es lo correcto: no quiero una suite que necesite ClickHouse
arrancado. Así que la primera señal fue el ensayo — y lo que el ensayo imprimió
fue un expediente **educadamente vacío**, con las seis lagunas anotadas y su
motivo. La degradación funcionó tan bien que, sin leer el ensayo, habría
parecido que funcionaba todo.

### 2. Un `404` saliente no significa «la petición nunca llegó»

La regla del puente vacío concluía «el fallo ocurrió **antes** de llegar a la
aplicación destino». Para un `404` eso es casi lo contrario de la verdad: **un
código de estado es una RESPUESTA, y una respuesta prueba que alguien
contestó.** Lo que falla con un `404` saliente es la petición — una ruta que el
destino no tiene— y eso apunta a quien llama.

Partido en dos reglas, con la clase del fallo como condición: `_el_destino_
respondio_un_error` para los códigos HTTP, y `_la_llamada_no_obtuvo_respuesta`
solo para las clases que significan que no hubo respuesta (`timeout`,
`connect`, `protocol`, `ssl`, `closed`, `refused`, `reset`, `eof`).

### 3. Y la peor: un destino lento diagnosticado como una petición mala

Un `ReadTimeout` del gateway contra `/v1/backends` lo cogía la regla **más
fuerte** —la discrepancia de marcas— y concluía *«la causa está en lo que el
gateway está ENVIANDO»*. Los datos decían otra cosa, y a la vista:

```
manager-api  GET /v1/backends   codigo 200   81.471 ms
gateway      GET (ReadTimeout)               75.236 ms   ← se rinde antes
```

La petición estaba perfecta. **El destino es lento.** La discrepancia estaba
bien detectada; la conclusión estaba mal, porque «te lo dio por bueno» solo
significa «pediste mal» cuando quien llama **recibió** una respuesta. Si no
recibió ninguna, el visto bueno llegó tarde — y llegar tarde *es* el fallo.

De ahí salió `_el_destino_tardo_mas_de_lo_que_se_espero`, que es la única regla
que entrega una **cadena causal medida**: dos duraciones de dos spans
distintos, y una mayor que la otra. No es correlación ni una heurística sobre
nombres.

Comprobado aparte, y es más grande que el incidente: `manager-api` sirvió
`/v1/backends` **7.298 veces en siete días con p50 = 60,2 s, p95 = 71,6 s y un
máximo de 1.093 s**. El plazo del gateway son 75 s, justo por encima del p95:
los `ReadTimeout` no son un incidente, son una configuración garantizada a
fallar. Va a Prometheus, no lo toco yo.

**Consecuencias**. Las dos correcciones de reglas llevan prueba propia, y las
dos pruebas dicen en el docstring que salieron de un diagnóstico equivocado
sobre un incidente real — porque son exactamente las que no se escriben
pensando, sino mirando.

---

## D-132 · El puente: la traza no cruza el servicio, así que el salto se da a mano

**Estado**: ✅ Vigente · medido sobre 132.000 trazas

**Decisión**. El expediente de L3 lleva un bloque `contraparte` que busca
**quién atendió la llamada saliente que falló**, casando por **ruta** y
**ventana de ±3 s** sobre spans de lado servidor. No por `trace_id`.

**Por qué**. Porque por `trace_id` no se encuentra nada. Medido sobre dos días:

| | |
|---|---|
| trazas totales | ~132.000 |
| trazas con spans de **más de un servicio** | **3** |
| spans `Server` de `auth-service` | 2.369 |
| …de ellos **sin padre** (son raíz) | **2.369 (100%)** |

El gateway llama a `auth-service` por HTTP y tiene su span `Client`; el
`auth-service` recibe la petición y **arranca una traza nueva**. Nunca adopta el
`traceparent` entrante.

La causa es el valor por defecto del `ASGIMiddleware`: `propagate_mode="never"`,
que es el correcto para lo expuesto a internet y el equivocado para dos
servicios que se hablan por loopback. Con `trusted` funcionaría sin tocar nada
más —el loopback siempre es de confianza en ese modo— pero **eso es
instrumentación de Prometheus y no la toco yo**: va al canal como petición.

**El matiz que lo hace útil.** Que el puente **no encuentre nada** es un
hallazgo, no un bloque vacío: si nadie registró haber atendido la llamada que
falló, el fallo ocurrió antes de llegar a la aplicación destino. Con tres
lecturas posibles que el veredicto dice en voz alta en vez de esconder: que
nadie la atendiera, que el destino no esté instrumentado, o que nombre esa ruta
con otra plantilla.

**Y se casa por ruta y no por nombre de span** porque los dos lados la nombran
distinto: el cliente del gateway emite un span llamado `POST` a secas y guarda
el destino en `url.full`, mientras el servidor de `auth-service` lo llama
`POST /oauth2/token`. Casar por nombre no encontraría nunca nada — y como «no
encontrar nada» aquí *significa* algo, un empalme mal hecho no se vería como un
fallo sino como un diagnóstico.

Su límite es el mismo borde que D-117: las rutas con parámetro, que el servidor
nombra con la plantilla y el cliente con el valor resuelto.

**Lo que se descartó**. Caminar el árbol buscando el error más profundo como
mecanismo principal. Medido antes de construirlo: de **8.622 trazas con error**
de los últimos siete días, **8.609 tienen una sola clase de error** y solo 13
tienen dos o más. El árbol resuelve el 0,15% de los casos. Se queda en el
expediente porque cuando dispara cambia el diagnóstico entero, pero el trabajo
de cruzar servicios lo hace el puente.

### Añadido el mismo día · el puente vacío es mucho más débil de lo que escribí

Las tres lecturas de un puente vacío que dejé arriba —nadie la atendió, el
destino no está instrumentado, o nombra la ruta con otra plantilla— se dejan
una cuarta, y **es la que domina**.

Las políticas de muestreo de cola del gateway son `errors` (por `status_code`),
`slow` (por latencia), `genai-always` y `baseline` al **10%**. El span del
destino **atendiendo bien** una llamada que al cliente le falló no es ninguna
de las tres primeras: un 404 servido es `Unset` para OTel —correctamente, el
servidor hizo su trabajo— y 7 ms no es lento. Así que se descarta con
probabilidad **~0,9**.

Se ve en los dos casos reales a la vez:

| llamada | lo que vio el cliente | el otro lado |
|---|---|---|
| `GET /v1/backends` | `ReadTimeout` a los 75 s | **encontrado** — 200 en 81 s, retenido por la política `slow` |
| `GET /admin/api/clients/names` | `404` en 7 ms | **no encontrado** — ni error, ni lento, ni GenAI |

El primero apareció porque era lento. El segundo no apareció porque era rápido
y correcto. Nada de eso dice si alguien atendió la llamada.

**Consecuencia**: ninguna regla concluye a partir del puente vacío. Lo que
sostiene `_la_llamada_no_obtuvo_respuesta` es la **clase** del fallo —un
`ReadTimeout` *es* una ausencia de respuesta, por definición— y el puente solo
aporta confirmación cuando aparece. El veredicto lo escribe en el texto: *«no
se pudo ver el otro lado, y eso NO lo confirma»*, con el 0,9 dentro.

Es el mismo error que acabo de corregir dos veces hoy en este mismo módulo:
tener el dato y leerlo como si dijera más de lo que dice.

---

## D-133 · El histórico de incidentes guardaba una fila por huella, y reabrir un problema borraba el anterior

**Estado**: ✅ Vigente · migrado en el almacén real (copia previa guardada)

**Decisión**. La clave primaria del almacén de incidentes pasa de la **huella** al
**id**. Una fila por incidente, no por problema. Al arrancar, un almacén con la
clave vieja se migra en una transacción, sin perder ninguna fila de las que
quedaban.

**Por qué**. Salió buscando casos para el banco de `F3-08`, que necesitaba el
histórico, y es de los que no dan error:

```sql
huella TEXT PRIMARY KEY
INSERT … ON CONFLICT(huella) DO UPDATE SET id=excluded.id, …
```

Un incidente que se reabre tiene la misma huella que el anterior, así que el
`INSERT` **pisaba la fila**. El almacén se llamaba «histórico» y guardaba, de
cada problema, **solo su última vez**. El `ReadTimeout` del gateway de las 02:50
del 25/10 —el que analicé en D-129— desapareció cuando el mismo fallo reabrió a
las 08:26.

Y ese fallo tiene trazas **todos los días desde hace un mes** (27 de 30, 2.295
errores). Las tres consecuencias, de menos a más:

1. **El criterio 4 del piloto** («¿ha abierto alguna vez el silencio un
   incidente?») seguía bien: «alguna vez» sobrevive aunque solo quede la última.
2. **`B-32` estaba mal razonado.** Contaba *«571 incidentes, 571 huellas
   distintas»* como prueba de que la huella identifica la ocurrencia y no la
   clase. Con la huella como clave primaria, esas dos cifras son iguales **por
   construcción**. Lo que sí era cierto de B-32 —468 firmas distintas para un
   solo `NOT NULL`, de cuando la firma llevaba el mensaje— sigue en pie, y es
   el único problema que tiene.
3. **L3 le dijo una cosa falsa a una persona.** Su evidencia decía *«primera vez
   que esta firma abre incidente»* sobre ese `ReadTimeout`, leyendo un histórico
   que no podía saberlo.

**Lo que cambió en L3 por esto**. «¿Es nuevo?» ya no lo contesta el almacén de
incidentes sino las **trazas**, que no se pisan: una séptima consulta cuenta en
cuántos de los 30 días anteriores al incidente hubo errores de esa clase, y un
fallo con errores en al menos un tercio de los días sale como `CRONICO`. Y la
evidencia nunca vuelve a decir «primera vez»: sin trazas que mirar, solo dice
*«sin incidentes anteriores registrados»*, que es lo único que el almacén puede
sostener.

Dos bordes de esa consulta que salieron al capturar el banco, y que son la
misma lección:

- **Cero días es una contradicción, no un «nunca».** Un incidente de tipo
  `error` es él mismo un error dentro de la ventana; si la consulta devuelve
  cero es que la clase no aparece como `error.type`. La primera captura escribió
  *«ninguno de los 30 días anteriores»* para dos incidentes de antes de D-118,
  cuya firma es el mensaje entero. Ahora es una laguna con su motivo.
- **Un salto de línea en el parámetro rompía la consulta.** ClickHouse lee los
  parámetros HTTP en TSV escapado; las firmas con la línea `DETAIL:` de Postgres
  daban `500`. Se escapa en un sitio.

**Lo que no vuelve**. Lo pisado antes de hoy está perdido: la migración conserva
lo que quedaba —una fila por huella— y desde ahí se acumula. Antes de migrar se
guardó una copia del fichero.

---

## D-134 · El banco de F3-08, y su primera medida: cero aciertos en lo no visto

**Estado**: ✅ Vigente · 7 casos, 2 no vistos

**Decisión**. `services/diagnose/banco/` guarda incidentes reales con su
expediente **congelado** y la causa establecida **fuera de L3**: por el equipo
dueño del servicio (`confirmada`) o por una medición independiente de la regla
que se evalúa (`medida`). `python -m diagnose.banco evaluar` pasa un juez por
todos y separa **casos de diseño** de **casos no vistos**. La cifra que vale es
la segunda.

| caso | causa | de dónde | |
|---|---|---|---|
| `aeon-genaispan-kwargs` | `GenAISpan.set` no acepta argumentos por nombre | aeon, 30/09 | diseño* |
| `veritium-tenant-proceso-viejo` | migración aplicada con la API de la versión anterior viva | Veritium, 14/10 | **no visto** |
| `veritium-pendingrollback-sintoma` | síntoma del `NOT NULL` anterior en la misma sesión | Veritium + el mensaje | diseño* |
| `tei-silencio-sin-cablear` | instancias paradas, nunca emitió | Prometheus, P-42 | **no visto** |
| `prometheus-backends-lento` | `manager-api` tarda más que el plazo del gateway | medida (D-131) | diseño |
| `prometheus-clients-names-puerto` | la ruta, en el puerto equivocado | medida (A-51) | diseño |
| `prometheus-502-por-oauth-400` | la llamada a `/oauth2/token` recibe 400 | la traza | diseño |

\* entraron como no vistos y pasaron a diseño — abajo, por qué.

**Por qué congelado.** Las trazas caducan a los 30 días. Un banco que recolecta en
vivo deja de existir en un mes y, peor, **cambia de contenido sin avisar**: el
juez mejoraría o empeoraría sin haber cambiado. El caso de aeon caducaba el
30/10; se capturó cinco días antes.

**Por qué la causa fuera de L3.** Dos de los incidentes los había diagnosticado
L3 antes de capturarlos. Si su `root_cause` siguiera en el incidente, el caso
sería un examen con las soluciones grapadas. Se quitan al capturar, y el
histórico se corta en el incidente: el juez solo ve lo que se sabía entonces.

### La primera medida, y lo que costó llegar a ella

La primera pasada dio **2 aciertos de 4** en lo no visto. Los dos eran **eco**:

- La regla `_la_firma_nombra_el_sintoma` comparaba la clase del span con la
  firma **entera**. Antes de D-118 la firma era el mensaje, así que nunca
  coincidían: la regla disparaba en todos los incidentes de esa época y
  **repetía la firma en el veredicto**.
- El calificador buscaba «GenAISpan.set», «previous exception» y «tenant»… y los
  encontraba dentro de la cita.

Y uno de esos dos «aciertos» terminaba diciendo *«la causa es
`PendingRollbackError`, no la firma»*: **el síntoma como causa**, puntuado
como acierto.

El orden en que se arregló es lo que hace que la cifra valga algo:

1. **Primero el calificador** —solo puntúa lo que el juez *añade*, no lo que
   cita del incidente—, y se volvió a medir **sin tocar ninguna regla**:

   ```
   no vistos  4 casos · acierta 0 · parcial 3 · se abstiene 1 · falla 0
   ```

   **Esa es la primera medida honesta de L3 sobre lo que no ha visto: cero
   aciertos.** Y uno de los tres «parcial» esconde una afirmación invertida que
   el calificador por palabras no puede ver — ese es su límite, y queda dicho.

2. **Después las reglas**: la firma se compara por su clase, y un
   `PendingRollbackError` se diagnostica como síntoma de la excepción original
   que lleva dentro del mensaje — que empezaba pasado el carácter 280, justo
   donde la regla recortaba.

3. **Y los dos casos que destaparon esos arreglos pasan a diseño.** Ya no miden
   nada que el juez no haya visto.

```
no vistos  2 casos · acierta 0 · parcial 1 · se abstiene 1 · falla 0 (con confianza alta: 0)
diseño     5 casos · acierta 4 · parcial 1 · se abstiene 0 · falla 0 (con confianza alta: 0)
```

Dos casos no vistos es muy poco, y es el estado real. Lo que dicen los dos es lo
más útil del banco, porque **ninguno es un fallo del juez**:

- `tenant`: el mejor juez posible se queda en `parcial`, porque **ningún bloque
  del expediente puede ver un despliegue o una migración**. Es un hueco del
  expediente (`B-38`).
- `tei`: abstenerse **es** la respuesta correcta con este expediente, y el juez
  se abstiene en vez de culpar al gateway, que tenía 3.356 `PermissionError` en
  la misma ventana. Lo que lo resolvería es una consulta que nadie hace: *«¿ha
  emitido alguna vez este componente?»* (`B-39`).

### De paso, una fuga a los avisos

Arreglando la regla de la excepción, el veredicto del `PendingRollbackError`
llevaba dentro el `DETAIL:` de Postgres y el `[parameters: …]` de SQLAlchemy:
UUID y **un código de empleado** de Veritium. Eso es `root_cause`, o sea un
mensaje de Telegram. El mensaje se corta antes del volcado de la fila; lo que
explica el fallo está antes.

**Lo que no se fija en las pruebas**: la puntuación. Un banco que crece con casos
nuevos y no vistos **tiene** que poder bajar la cifra, y una prueba que lo
impidiera sería un incentivo para no añadir casos difíciles. Se fija la
invariante —**ningún `falla` con confianza `alta`**— y que el banco sea lo que
dice ser.

---

## D-135 · Los agentes se construyen con synaptum

**Estado**: ✅ Vigente · decisión del dueño del proyecto, 25/10 · sustituye la parte de *framework* de D-012

**Decisión**. Los agentes de Argus —el juez con modelo de L3, la investigación de
L4, el postmortem y la remediación de L5— se construyen con **synaptum**
(`synaptum-framework`, hoy `1.0.0rc6`).

**Por qué cambia lo que decía el plan**. `PLAN.md §8.6` y D-012 descartaron
synaptum frente a Temporal con una tabla: *no sobrevive a la caída del worker,
esperar una aprobación hay que construirlo, MCP y observabilidad pendientes*.
Esa tabla describía la línea 0.x. Leído su roadmap el 25/10, las cuatro filas
están cerradas:

| lo que faltaba | hoy |
|---|---|
| sobrevivir a una caída | `Checkpointer` SQLite (`SYN-24`): al reanudar con el mismo `run_id`, **una inferencia ya pagada no se vuelve a pagar** |
| esperar una aprobación | `ApprovalStep` y la disposición `require_approval` (`SYN-05`, `H4`): interrumpir es dejar de iterar, reanudar es volver a llamar |
| MCP | cliente MCP como extra opcional (`SYN-39`) |
| observabilidad | instrumentación OTel GenAI (`SYN-37`), aplazada a propósito para esperar a esta plataforma |

Y la razón de D-012 que más pesaba —*el plano central es un portátil que se
suspende*— la cubre el mismo mecanismo: un run interrumpido por el sueño se
reanuda con su `run_id` sin repetir lo hecho. El matiz de D-012 sobre L4 («una
activity por herramienta, no un bucle dentro de una activity») sale gratis:
**cada `yield` del bucle es una frontera de checkpoint.**

**Lo que no cambia**:

- **D-013 sigue en pie.** L3 no es un agente: el juez con modelo es **una**
  llamada con salida estructurada sobre el expediente. Con synaptum eso es un
  run de un solo paso de modelo, sin herramientas, detrás de la interfaz `Juez`
  que ya existe — y se mide en el banco de F3-08 contra el juez de reglas.
- **Worker propio, árbol de dependencias propio.** synaptum no tiene
  dependencias duras (`SYN-30`) y los proveedores van por extras, así que no
  hereda el conflicto `crewai`/`openai-agents` de `aeon-ai`.
- **No se toca synaptum desde aquí.** Si falta algo, va como petición a su
  equipo, igual que con el resto del portafolio.

**Dos consecuencias que hay que tener presentes**:

1. **Python ≥ 3.13.** synaptum lo exige y el espacio de trabajo declara
   `>=3.11`. Los servicios que lo usen (`diagnose` y los que vengan) subirán su
   `requires-python`; las imágenes ya corren 3.13. Las librerías que importan
   las aplicaciones (`argus-semconv`, `argus-sdk`) **no** dependen de synaptum
   y se quedan en 3.11.
2. **El modelo local pasa por Axonium.** El roadmap de synaptum fija una regla
   del dueño: *toda la inferencia local se resuelve en Prometheus, y la única
   forma de llegar a sus modelos es el SDK Axonium* — que synaptum trae como
   extra (`[axonium]`, `SYN-52`). Elegir el modelo del juez sigue pendiente;
   si es local, el camino ya está decidido.

**Lo que queda de Temporal**: nada en el camino de los agentes. Si algún día
hace falta orquestar procesos largos que no sean agentes, se decidirá entonces;
hoy no hay ninguno.

---

## D-136 · Tres bloques nuevos en el expediente, y cómo se midieron sin hacerse trampas

**Estado**: ✅ Vigente · banco: 10 casos, 2 no vistos, los dos acertados

**Decisión**. El expediente de L3 gana tres bloques, cada uno con su regla:

| bloque | pregunta | regla | sale de |
|---|---|---|---|
| `emision` | ¿ha emitido **alguna vez** este componente? | `_nunca_ha_emitido` · alta | `B-39` · caso `tei` |
| `cambios` | ¿empezó el fallo con un proceso nuevo, o a mitad de uno viejo? | `_empezo_a_mitad_de_un_proceso` · media | `B-38` · caso `tenant` |
| `plataforma` | ¿recibía datos la plataforma **entera** antes del silencio? | `_calla_toda_la_plataforma` · media | caso no visto **fallado** |

Y una guarda: la regla de «una dependencia falla a la vez» **no se aplica a un
silencio**. Un componente callado no está fallando llamadas.

### El problema de método, y el orden que lo resolvió

`B-38` y `B-39` salieron de los dos únicos casos no vistos del banco. Escribir
sus reglas mirándolos los convierte en casos de diseño y deja el banco **sin
nada no visto**. Así que, antes de escribir una línea:

1. **Buscar casos nuevos con causa conocida y etiquetarlos antes de que exista
   el código.** El primero: el silencio de `veritium-api` del 23/10 a las
   10:50, con la verdad medida aparte —huecos simultáneos en **todos** los
   servicios y `pmset -g log` con el Mac en `Sleep`/`DarkWake`—.
2. **Pasarle el juez de entonces, sin tocar nada.** **Falló**, con confianza
   media: culpó al gateway por seis `PermissionError` —una dependencia
   declarada con errores en la ventana— mientras la plataforma llevaba 45 de
   los 60 minutos anteriores sin recibir un span de nadie.
3. **Implementar `B-39`** y comprobar que no rompe el caso no visto. No lo
   rompe: el componente emitía hasta las 10:50 y la regla no dice «nunca», que
   era el falso positivo que ese caso estaba ahí para cazar.
4. **Implementar `B-38`.** Sobre `tenant`, el veredicto reproduce desde la
   telemetría la causa que confirmó Veritium:

   ```
   veritium-api  instancia 7e6f05f3  02:06 → 11:32   548 errores   v0.3.0
   veritium-api  instancia 4d56e252  11:32 → 12:53     0 errores   v0.3.0
   ```

   El fallo empieza nueve horas después de que arrancara el proceso y sin
   cambio de versión: lo que cambió está **fuera** del proceso, y siendo un
   error de esquema, lo probable es una migración con el código viejo todavía
   corriendo. En el momento del diagnóstico la segunda fila no se ve —sería ver
   el futuro—; la inferencia sale de la primera.
5. **Arreglar el fallo del paso 2**, sabiendo que eso gasta el último caso no
   visto. Se hizo igual: el juez culpaba a una dependencia en **cada** noche en
   que el portátil duerme, y eso pesa más que la pureza del banco.
6. **Y reponer casos no vistos** con verdad **independiente de lo que mide la
   regla**. La regla mira telemetría; la verdad viene del registro de energía
   del Mac. Dos silencios más —el gateway el 23/10 a las 01:13 UTC y el propio
   canario el 25/10 a las 03:03—, etiquetados con `pmset` **antes** de pasarles
   el juez. Los dos, acertados.

### Por qué el umbral de la plataforma está donde está

Medido sobre una semana, las horas son de dos clases y casi no hay término
medio: **60 de 60** minutos con datos (despierta) o **menos de 20** (el host
dormido, despertando unos tres minutos cada dieciséis). De 162 horas, solo 13
caen entre 20 y 49. El umbral —menos de la mitad— cae en el hueco. Los dos casos
no vistos dieron 11 y 13 minutos de 60.

### Lo que el banco dice ahora, y lo que no

```
no vistos  2 casos · acierta 2 · parcial 0 · se abstiene 0 · falla 0 (con confianza alta: 0)
diseño     8 casos · acierta 7 · parcial 1 · se abstiene 0 · falla 0 (con confianza alta: 0)
```

**Los dos no vistos son del mismo tipo que el caso del que salió la regla.**
Prueban que el umbral generaliza a otras noches, no que el juez sepa hacer algo
distinto. Hace falta un caso no visto de **otra clase** antes de leer esta cifra
como una tasa de acierto.

Y dos precisiones que el propio trabajo obligó a hacer:

- La edad de un proceso es la de su **primer span observado**, no su arranque:
  un proceso sin tráfico no emite. Es una cota inferior, y el veredicto dice
  «al menos».
- `recapturar` rehace el expediente de un caso **a fecha del incidente** sin
  tocar su etiqueta. La verdad de un caso no cambia porque el expediente sepa
  más; lo que cambia es lo que el juez puede ver.

---

## D-137 · El juez con modelo: `synaptum.generate`, en sombra, esperando una credencial

**Estado**: ✅ Construido y desplegado · ⏳ sin modelo hasta que Prometheus dé un cliente (A-52)

**Decisión**. El juez con modelo es `JuezConModelo`, detrás de la misma interfaz
`Juez` que el de reglas, construido con **`synaptum.generate`**: una petición que
devuelve un objeto validado contra un esquema (`Dictamen`), y que si la salida no
valida la vuelve a pedir enseñándole el error al modelo, con techo de tres turnos.
Sin herramientas y sin bucle de agente. Es D-013 escrito con D-135.

**En sombra**. Corre después de las reglas, se registra al lado en
`sombra.jsonl` —una línea por juicio, con los dos veredictos— y **no toca el
aviso**. Es lo que pedía `F4-03`, y la razón es la de siempre en este proyecto:
un modelo que escribe en el móvil de una persona sin haber pasado el banco es
justo el cambio que hemos aprendido a no hacer. Un fallo de la sombra (red,
credencial, un modelo que no valida) no le cuesta nada al diagnóstico de las
reglas: está dentro de un `try` y se cuenta aparte.

**Por qué `generate` y no `Agent`**. Los dos están en synaptum. `Agent` es el
bucle con herramientas, que es lo que necesitará L4. `generate` es la llamada
suelta con diario y validación. Usar `Agent` aquí sería pagar orquestación por
algo que es una petición, que es exactamente lo que D-013 dijo que no.

**Lo que no se le confía al modelo**. Todo lo que se puede comprobar con código
se comprueba con código:

- La confianza tiene que ser `alta`, `media` o `baja`; cualquier otra cosa es
  `baja`.
- Una causa vacía es una abstención, y una abstención no puede ser `alta`.
- La evidencia se acota a cinco hechos y se le quita el volcado de filas. El
  prompt lo pide; esto lo garantiza.
- Una salida que nunca valida acaba en abstención con un `NO DISPONIBLE` en la
  evidencia, no en una excepción.

**El expediente es dato.** Trae texto escrito por aplicaciones ajenas, y va
entre delimitadores y etiquetado como datos. No es una defensa completa contra
inyección —`F4-06` existe para probarla en serio—, pero es la parte que cuesta
una línea.

**Temperatura cero.** El banco tiene que poder repetir una medida y obtener la
misma cifra. El `run_id` es un hash del modelo, la versión de las instrucciones
y la petición: el mismo expediente es el mismo run, y con un checkpointer no se
paga dos veces.

### Lo que falta: una credencial, y no la creo yo

La inferencia local pasa por Axonium, y Axonium pide un cliente OAuth
`client_credentials` del gateway de Prometheus. **Argus no tiene ninguno**, y
registrar uno es una acción sobre la plataforma de otro equipo: va como petición
(`A-52`), con el volumen medido —media de 36 incidentes al día, pico de 124, techo
de 3 cada 10 s, ~1.700 tokens de entrada— y la pregunta de qué modelo recomiendan.
La credencial la pone el dueño en `.env`; el servicio no la ve en ningún otro sitio.

Mientras tanto, desplegado y comprobado: el juez en sombra **no arranca sin
credencial y lo dice** (`sombra.sin_credencial`), las reglas siguen diagnosticando,
y desde dentro del contenedor el gateway de Prometheus responde en
`host.docker.internal:8020`. Ese último detalle no es menor: el valor por defecto
de Axonium es `127.0.0.1:8020`, que dentro del contenedor es el contenedor.

### Dos consecuencias

- **El espacio de trabajo pasa a Python ≥ 3.13.** uv toma la intersección de los
  miembros, y synaptum lo exige. Las librerías que instalan las aplicaciones
  siguen declarando 3.11 en sus propias ruedas, así que a ellas no les cambia
  nada; lo que cambia es con qué se prueban aquí.
- **El modelo ha leído las lecciones de los casos de diseño.** Las instrucciones
  dicen «un código HTTP es una respuesta» y «si calla toda la plataforma no es el
  servicio», que salieron de esos casos. Así que, igual que con las reglas, **la
  cifra del modelo que vale es la de los no vistos**.

### Añadido el mismo día · dos «ruff limpio» que no lo eran

Los mensajes de `2c0e5c4` (D-134) y `15773d9` (D-136) dicen *«ruff limpio»*. **No
lo estaban**: 1 y 3 errores, de estilo —orden de imports y una variable sin usar—,
ninguno de comportamiento. La comprobación era `ruff check . | tail -4 && echo
RUFF-OK`, y en una tubería el código de salida es el de `tail`: imprimía OK
pasara lo que pasara. La misma salida que lo decía traía, tres líneas más
arriba, `Found 3 errors.`

Es la familia de «verificado era verificado en el árbol» (D-124), un escalón más
abajo: aquí ni siquiera el árbol. Desde este commit la comprobación lee el
código de salida (`set -o pipefail`), y los tres errores están corregidos.

---

## D-138 · La primera medida del modelo: peor que las reglas, y el calificador tampoco sirve para él

**Estado**: ✅ Medido · el modelo se queda en sombra

**Contexto**. El 26/10 el dueño puso en `.env` el cliente de Axonium pedido en
A-52. Prometheus ofrece **un solo modelo**, `gpt-oss-20b-mxfp4`, el que ya estaba
por defecto. Se pasó el banco entero, caso a caso, con temperatura cero.

### La cifra automática

```
no vistos  2 · acierta 0 · parcial 0 · se abstiene 1 · falla 1 (con confianza alta: 0)
diseño     8 · acierta 2 · parcial 3 · se abstiene 1 · falla 2 (con confianza alta: 1)
```

Frente a las reglas —no vistos 2 de 2, diseño 7 de 8— es claramente peor. Y
tiene un `falla` con confianza alta, que es lo único que el banco no tolera.

### Pero la cifra automática miente en las dos direcciones

D-134 avisó: calificar por palabras clave funciona con plantillas y es frágil
con un modelo, que dice lo mismo con otras palabras. **Pasó en tres casos**, y en
los tres el modelo tenía razón:

- `veritium-pendingrollback-sintoma`, el «falla con alta»: el modelo dio como
  causa *«null value in column "tenant"… violates not-null constraint»*, que
  **es** la excepción original. Falló porque no escribió «rollback».
- `canario-silencio-host-dormido`: *«host o ingesta»*. Correcto y escueto.
- `veritium-tenant-proceso-viejo`: *«sin valor para la columna tenant»* en vez
  de «null value».

Y en la otra dirección, el modelo **falla de verdad y siempre igual: no usa los
bloques decisivos del expediente**.

| caso | lo que tenía delante | lo que hizo |
|---|---|---|
| `tei` | `EMISION ANTERIOR: NADA en los 30 días anteriores` | se abstuvo |
| `gateway-silencio` | `la plataforma ENTERA recibió datos en 11 de los 60 minutos` | se abstuvo |
| `tenant` | `su primer error llegó 542 min después de arrancar` | se quedó en el síntoma |

Las reglas aciertan exactamente esos tres. Leído a mano —un juicio, no una
medida—, el modelo acierta lo que se resuelve leyendo el árbol (el 502 y el
`/v1/backends`) y pierde todo lo que depende de los bloques nuevos.

### Lo que vio en producción

Arrancado en sombra, las tres primeras comparaciones sobre incidentes reales
traen dos defectos más:

1. **Escribe en inglés** pese a que la regla 8 de sus instrucciones dice
   «escribe en español».
2. En un incidente de burn-rate de `prosodia-worker` **toma una coincidencia
   temporal por causa**, con confianza media —*«gateway errors coinciding with
   worker incidents suggest…»*—, que es lo que prohíbe la regla 5. Las reglas se
   abstienen. Sin causa conocida no se puede decir quién acierta; el
   razonamiento es el que este proyecto lleva semanas desaprendiendo.

Y lo que el diseño tenía que garantizar, se cumple: esos avisos siguen diciendo
«investigando…». Lo que propuso el modelo no llegó a nadie.

### Qué se decide

- **El modelo se queda en sombra.** Para eso existe la sombra.
- **No se ajusta el prompt contra el banco.** Con 10 casos, ajustar las
  instrucciones mirando los fallos los convertiría todos en casos de diseño, y
  el modelo acabaría parafraseando las reglas.
- **Hace falta un calificador que entienda paráfrasis** antes de leer cualquier
  cifra del modelo como medida, y ese calificador hay que medirlo contra las
  etiquetas humanas del banco (`B-41`).
- **Una opción a evaluar**: darle al modelo el veredicto de las reglas como
  parte del expediente. Ya no competiría con ellas: las completaría donde se
  abstienen. Cambia la pregunta de «¿quién acierta más?» a «¿añade algo?»
  (`B-42`).

---

## D-139 · B-41: el calificador se mide antes de creerle, y D-138 exageraba

**Estado**: 🚧 Calificador por palabras medido · el calificador con modelo, escrito y sin medir

**Contexto**. D-138 dijo que tres «fallos» del modelo eran «respuestas correctas
dichas con otras palabras» y pidió un calificador que entendiera paráfrasis
(`B-41`). Al etiquetar esos veredictos criterio a criterio, la afirmación no se
sostiene, y lo que sale es más útil que lo que se buscaba.

### Lo que de verdad fallaba: la composición, no la paráfrasis

| caso | lo que dijo el modelo | por qué salía `falla` |
|---|---|---|
| `canario-silencio` | «host o ingesta» | Media causa sin síntoma. `parcial` exigía el síntoma ENTERO |
| `pendingrollback` | la excepción original | Da la excepción como causa, pero no dice que el `PendingRollbackError` sea su consecuencia. Media causa otra vez |
| `tenant` | «sin valor para la columna tenant» | **Esta sí** es paráfrasis: la etiqueta esperaba «null value» |

Dos de los tres eran **respuestas parciales** que la regla de composición
hundía por debajo de no decir nada. Ninguno de los tres era un acierto, y el
«falla con confianza alta» de D-138 es un **parcial** con confianza alta. La
corrección de D-138 tenía razón en la dirección y se pasó en la cifra.

### Qué cambia

1. **Calificar son dos pasos, separados** (`banco.componer`): decidir criterio a
   criterio si el texto afirma algo, y componer esas decisiones. Solo el primero
   depende de cómo se dice algo.
2. **Media causa es `parcial`**, no `falla`.
3. **El eco casi literal también es eco.** Al etiquetar salió que las reglas
   «acertaban» `aeon` diciendo *«GenAISpan.set() got an unexpected keyword
   argument 'argus.run.id'»*: la firma del incidente sin su `TypeError: `. La
   detección de D-134 solo quitaba la cita exacta. Ahora quita también el tramo
   que repite el 80 % de la firma seguido. Citar una PARTE sigue contando, y a
   propósito: sacar la excepción original de dentro del `PendingRollbackError`
   es justo el diagnóstico que ese caso pide.
   **Las reglas bajan en diseño de 7 a 6 aciertos** (`aeon` pasa a `parcial`).
   Los no vistos siguen en 2 de 2.
4. **Un corpus etiquetado** (`services/diagnose/banco/calificador/`): 21
   veredictos reales (10 de las reglas, 10 del modelo y 1 con razonamiento alto)
   y 11 escritos a mano. Las etiquetas se pusieron criterio a criterio **antes**
   de pasar ningún calificador, sobre el texto ya sin eco. Las puso Claude y
   están **pendientes de revisión por el dueño**: es el punto débil de la
   medida y se dice.
5. **Un calificador con modelo** (`calificador.ConModelo`, synaptum). Responde
   «sí» o «no» con una cita, y **un «sí» cuya cita no está en el texto cuenta
   como «no»**, con la misma desconfianza que `a_veredicto` aplica al juez. Si
   no pudo decidir, tampoco aprueba.

### La medida del calificador por palabras

```
real    pares 85/88 · falsos sí 1 · falsos no 2 · resultado igual 20/21 (inflado 0, desinflado 1)
a-mano  pares 32/45 · falsos sí 6 · falsos no 7 · resultado igual 5/11  (inflado 1, desinflado 5)
```

**Con la composición corregida, las palabras clave aciertan el resultado en 20
de los 21 veredictos reales y no inflan ninguno.** La que falla es `tenant`, la
única paráfrasis de verdad. Los escritos a mano —negaciones, paráfrasis sin
ninguna palabra clave, una frase en inglés— muestran que **puede** fallar: en 6
de 11. Pero los escribió quien conocía las palabras clave, así que dicen que
falla, no con qué frecuencia. La cifra que cuenta es la real.

### Lo que falta

**Medir el calificador con modelo contra el mismo corpus.** El 26/10 a las
19:00 UTC, a mitad de la medida con razonamiento alto, Prometheus dejó de servir
`gpt-oss-20b-mxfp4`: `UnknownModelError` y después «circuit half-open». El
modelo sigue en `/v1/models`. El dueño está gestionando el cambio de modelo con
ellos. La orden queda lista:

```
python -m diagnose.calificador medir --calificador modelo
```

**Criterio para adoptarlo**: cero inflados en el corpus real, y menos
desinflados que las palabras clave. Mientras no lo cumpla, el banco sigue
calificando por palabras, que hoy no inflan.

**El razonamiento alto, a medio medir.** Contestó 3 casos antes del corte. En
`aeon` tardó **74 s** (15 s sin fijarlo) y respondió reescribiendo el mensaje
del propio incidente: lo afirma, pero es un eco que la detección por texto no
puede ver. No hay medida todavía. El juez lo acepta como
`JuezConModelo(razonamiento=...)`: va dentro de `chat_template_kwargs`, porque en
el nivel superior de la petición Axonium midió que no hace nada.


### Addendum (26/10, noche): el calificador con modelo, medido, no se adopta

Con `qwen3-30b-a3b-q4-k-m-local` (`gpt-oss` dejó de estar disponible), en tres
pasadas:

| pasada | resultados reales iguales | inflados | desinflados | citas rechazadas |
|---|---|---|---|---|
| sin razonamiento, campo `cita` | 9/21 | 1 | 11 | 14 |
| sin razonamiento, cita antes que el veredicto | 9/21 | 1 | 11 | 15 |
| con razonamiento, el HECHO antes del TEXTO y `fragmento_del_texto` | **14/21** | **0** | 7 | 1 |
| *(palabras clave)* | *20/21* | *0* | *1* | — |

**La comprobación de citas hizo su trabajo.** En las dos primeras pasadas Qwen
decidía bien y copiaba **el hecho** como cita. Las 14 y 15 citas rechazadas eran
decisiones correctas con la cita inventada, y por eso salían como «no». Cambiar
el nombre del campo y el orden de la pregunta lo arregló: una cita rechazada en
toda la pasada.

**No cumple el criterio.** No infla nada, que era lo importante, pero desinfla
siete veces más que las palabras clave. El banco sigue calificando por palabras.

**La discrepancia que más se repite es de etiqueta, no del calificador.** En 3
de los 8 falsos «no», Qwen no acepta que *«la plataforma ENTERA recibió datos
solo en 13 de 60 minutos»* afirme *«todos los servicios callaron a la vez»*. Yo
lo etiqueté como sí, y es discutible: lo implica, pero no lo dice. Queda para la
revisión del dueño.
---

## D-140 · B-40: los hermanos de un incidente, y por qué agrupar con el reloj del emisor

**Estado**: ✅ En producción · sin caso en el banco

**Contexto**. El 25/10 varios componentes de Centinela abrieron
`redis.exceptions.TimeoutError` a la vez y L3 los diagnosticó uno por uno:
«Timeout reading from socket». El bloque `concurrentes` mira OTROS servicios en
las trazas y la correlación del motor depende de dependencias declaradas, y el
registro no modela el Valkey de Centinela.

### Qué se hizo

- **Bloque nuevo, `hermanos`**: otros componentes de la MISMA app que abrieron
  incidente con la misma clase (último segmento, B-33) a 5 min o menos. Sale
  del histórico de incidentes, que ya se consultaba: no añade ninguna consulta.
  Las clases sin nombre (`error`, `Exception`) no cuentan, porque tres `error` a
  la vez comparten un SDK, no una causa.
- **Regla `_varios_componentes_el_mismo_fallo`**: con tres o más componentes
  dice «es un fallo visto N veces» y apunta a lo que comparten (Redis/Valkey,
  base de datos, cola o «una dependencia, la configuración o el host»), con
  confianza **media**: el recurso compartido no se ve, se deduce. Con un solo
  hermano no salta. Va antes de las reglas que leen la excepción, que dirían lo
  mismo cinco veces.
- **Captura del banco**: los hermanos pueden abrir en el mismo segundo o poco
  después. Se incluyen los de la ventana, sin lo que L3 les escribió después.

### El reloj

Lo anotado en `B-40` («entre las 08:48:54 y las 08:49:00») no es lo que hay en el
histórico. Los cuatro hermanos tienen `opened_at` **09:07:57**, el mismo segundo
los cuatro, y su fila en el almacén se creó a las **08:49:00** (`abierto_en`).
Son 19 minutos entre el reloj del emisor y el del bus en el mismo incidente:
es `B-34`. Se agrupa con `opened_at` porque es lo que el histórico expone y es
coherente entre los hermanos. El precio es que `centinela-api`, que abrió a las
08:48:54 según su emisor, queda fuera del grupo.

### Verificado en vivo

Con el histórico y ClickHouse reales, el incidente de `centinela-notifier` de
las 09:07:57 pasa de «Timeout reading from socket» a *«No es de
centinela-notifier: 4 componentes de centinela (…) abrieron
`redis.exceptions.TimeoutError` en 0 s. Procesos distintos fallando igual y a
la vez apuntan a algo que COMPARTEN —lo más probable, su Redis/Valkey— …»*.

**No hay caso en el banco** porque la causa no está establecida fuera de L3:
que fuera el Valkey es una deducción, y Centinela no lo ha confirmado. La regla
dice «lo más probable» con confianza media y no afirma más.

---

## D-141 · B-35: la línea base del destino, ponderada por el muestreo

**Estado**: ✅ En producción

**Contexto**. La regla del destino lento comparaba dos duraciones de una sola
petición («tardó 81 s, el gateway esperó 75 s») y terminaba con un «o… o…»: la
latencia del destino, o un plazo más corto que su tiempo de respuesta normal.
Son dos diagnósticos con dos arreglos distintos, y el expediente no tenía con
qué elegir.

### Qué se hizo

- **Consulta nueva, `Almacen.linea_base`**: p50, p95 y cuántas peticiones
  pasaron del plazo de quien llama, para el servicio que atendió y esa ruta,
  en los 7 días **anteriores a la llamada** que falló. Tarda unos 20 ms.
- **Ponderada por el muestreo de cola.** El colector guarda toda traza de 3 s o
  más o con error, y un 10 % del resto. Sin corregirlo, en una ruta rápida las
  peticiones rápidas estarían diez veces infrarrepresentadas. Cada span rápido
  y sin error cuenta por 10. Es aproximado: la política mira la duración de la
  traza y no la del span. En `/oauth2/token` (rápida) la corrección importa:
  317 spans guardados representan ~2.774 peticiones. En `/v1/backends` (lenta)
  casi no cambia nada.
- **El bloque `contraparte` lo enseña**, y con menos de 20 peticiones estimadas
  se anota como laguna en vez de como dato.
- **La regla elige**: si el 1 % o más de las respuestas habituales pasa del
  plazo, dice *«no es una anomalía… plazo metido dentro de su tiempo de
  respuesta normal»*. Si no, dice *«fue una excepción»*. Sin línea base, sigue
  diciendo las dos posibilidades.

### Lo que dice el caso del banco

`prometheus-backends-lento`, recapturado con su etiqueta intacta: p50 60,2 s,
p95 60,6 s sobre ~8.734 peticiones, y **el 4,1 % pasa de los 75 s**. Sigue en
`acierta`, ahora con el diagnóstico que pedía B-35. El p95 de 71,6 s que citaba
B-35 venía de otra ventana y sin ponderar, así que no es comparable con este. La
forma sí coincide: la mayoría en torno a un minuto, con una cola que cruza el
plazo.

---

## D-142 · F3-06: la memoria agrupa por episodios, y la regla «ya pasó» se citaba a sí misma

**Estado**: ✅ En producción · por clase, sin vectores (F3-05 sigue pendiente)

**Contexto**. B-32 bloqueaba F3-06: sobre el histórico, un «top-N de
parecidos» habría devuelto 454 copias del mismo `NOT NULL` como 454
precedentes, de cuando la firma era el mensaje entero.

### Qué se hizo

- **Clase normalizada**: el último segmento de lo que va antes de los dos
  puntos (`_ultimo_segmento(_clase_de(firma))`), lo mismo que ya usaban las
  reglas para B-33.
- **Episodio = componente + día.** Medido sobre el histórico del 26/10: **853
  incidentes, 151 clases, 203 episodios**. Los 454 `NotNullViolationError` de
  `veritium-api` del 14/10 son **un** precedente con 453 incidentes.
- **Bloque `memoria`** del expediente: hasta 3 episodios anteriores de la misma
  clase en la misma app. Primero los que tienen causa anotada, después el mismo
  componente y después los más recientes. Solo lo abierto **antes** del
  incidente.
- **Va al aviso**: el veredicto lleva `similar_incidents`, que el alert-bus ya
  sabía pintar como «VISTO ANTES». Lo añade el bucle, sea cual sea el juez,
  porque es un hecho del histórico y no una opinión.

### El defecto que destapó

Al probarlo con el `ReadTimeout` real del gateway, la causa anotada del
episodio era *«Ya pasó: … se anotó «Ya pasó: … se anotó «manager-api…»»»*. La
regla `_ya_paso_y_alguien_anoto_la_causa` cita la causa del incidente anterior,
y cuando esa causa era a su vez un «ya pasó», cada reapertura añadía un nivel.
Estaba **en producción**, en los avisos. Ahora `causa_original` desenvuelve
hasta la causa que de verdad se diagnosticó, tanto en la historia como en la
memoria.

**Límite**: la búsqueda es por clase EXACTA dentro de la app. No encuentra «lo
mismo con otra clase» ni «lo mismo en otra app»: eso es F3-05, con vectores, y
necesita un modelo de embeddings servido por Prometheus.

---

## D-143 · F3-07: `mcp-obs`, consultas fijas como herramientas MCP de solo lectura

**Estado**: ✅ Verificado de punta a punta · todavía no lo usa nadie (lo usará L4, F4-01)

**Qué es**. `python -m diagnose.mcp_obs`: un servidor MCP por stdio con nueve
herramientas: `incidentes_abiertos`, `incidente`, `expediente`, `traza`,
`alcance`, `recurrencia`, `linea_base`, `concurrentes` y `registro`. Vive dentro
de `diagnose` para reutilizar el almacén y la recolección de L3: el
`expediente` que devuelve es **el mismo texto** que lee el juez.

**Decisiones**:

- **Consultas fijas, no SQL libre.** Un agente con SQL contra ClickHouse puede
  leer cualquier fila de cualquier equipo y tumbar el almacén con una consulta
  sin `WHERE`. Es la misma razón de D-013.
- **Solo lectura, declarado y garantizado.** Cada herramienta lleva
  `readOnlyHint=true` y `destructiveHint=false`, y synaptum las clasifica como
  `READ`. Lo verificado por el cliente: 9 de 9. Que no escriban lo garantiza el
  código, no la pista: `Almacen.consulta` rechaza todo lo que no sea `SELECT` y
  del alert-bus solo se hacen `GET`.
- **Sin volcados de filas**, línea a línea. El primer test lo cazó: `sanear`
  sobre el expediente entero cortaba en el primer `DETAIL:` y se llevaba todo lo
  que venía detrás.
- **Ventanas acotadas**: un modelo puede pedir 3.650 días de recurrencia, y el
  almacén guarda 30.
- **`mcp` 2.3.** Es la versión que resolvió uv. `FastMCP` pasa a llamarse
  `MCPServer`, y el cliente de synaptum (`ClientSession`, `stdio_client`) sigue
  funcionando con ella.

**Verificado** con `synaptum.mcp.MCPTools.stdio` contra el stack real: los
incidentes abiertos, la línea base de `/v1/backends` (p50 60,2 s, 352 de ~9.664
por encima de 75 s) y el expediente de un incidente abierto, con su bloque
«VISTO ANTES».

---

## D-144 · L4 existe: investigador con synaptum, triage sin modelo y sombra en segundo plano

**Estado**: 🚧 Escrito y probado con dobles · apagado en producción hasta medirlo

**Las piezas**:

- **El investigador** (`investigador.py`, F4-01) es un `Agent` de synaptum con
  las herramientas de `mcp-obs` (D-143) y salida tipada: causa, confianza,
  evidencia citando la herramienta de la que sale y **alternativas
  descartadas** (F4-05). Empieza siempre por el `expediente`, que es lo que ya
  leyó L3.
- **Presupuesto duro** (F4-02): 40 llamadas a herramientas, 300.000 tokens y 15
  minutos. Al agotarse uno, el run se corta y sale un informe **parcial sin
  causa**: cortar no es fallar, y fingir una conclusión sería peor. Un detalle
  que costó un test: synaptum cede cada paso de herramienta dos veces
  (intención y resultado), y contarlas las dos habría agotado el presupuesto a
  la mitad.
- **No arranca con una herramienta que escriba.** La pista la da el servidor y
  synaptum no se la cree; si alguna llega con un riesgo distinto de `READ`, el
  investigador se niega a construirse.
- **El triage** (`triage.py`, F3-03) es **determinista, sin modelo**. Las
  razones para no investigar son hechos que el expediente ya mide: no es
  `page`, la plataforma entera callaba, el componente nunca emitió, las reglas
  ya tienen causa con confianza alta, o es un hermano (B-40) de otro que ya se
  investiga (de cada grupo se queda el primero por nombre, la misma elección
  desde cualquiera de ellos). Pagar un modelo para que adivine eso sería pagar
  por lo que ya se sabe. Siempre devuelve el motivo, también cuando dice que no.
- **La sombra de L4** (`sombra_l4.py`, F4-03) corre en un hilo con una cola de
  5: una investigación tarda minutos y L3 diagnostica cada 10 s. Lo que no cabe
  se cuenta y se tira, porque si el triage deja pasar más de lo que la GPU puede
  investigar, lo que hay que afinar es el triage. La misma huella no se vuelve a
  investigar en 6 h. Escribe en `sombra-l4.jsonl` y nunca notifica.

**Apagado** (`DIAGNOSE_INVESTIGADOR` vacío) hasta tener una medida. El banco de
F3-08 mide jueces de un solo paso, y L4 necesita su propia medida.

---

## D-145 · L3 se tomaba a sí mismo por el otro lado de la llamada, con confianza alta

**Estado**: ✅ Corregido y desplegado

**Cómo se vio**. Al probar L4 en vivo (D-146) sobre un `page` real (`503` del
gateway, 26/10), lo que había que contrastar era el expediente, y el expediente
destapó el fallo:

- **Lo que pasaba**: el gateway llamaba a `127.0.0.1:8087/v1/chat/completions` y
  recibía `ConnectError: All connection attempts failed`, tres veces en la misma
  petición. En ese puerto no escuchaba nadie.
- **Lo que decía L3, con confianza alta**: «gateway atendió la llamada… pero tardó
  1 s, y gateway se rindió a los 0 s. Y no es una anomalía: el 98,5 % de sus
  respuestas llega después de los 0 s…».

**Dos defectos encadenados**:

1. **El puente se empalmaba con el que llama.** `Almacen.contraparte` buscaba
   spans `Server` por RUTA, y el gateway **sirve** `/v1/chat/completions` además
   de **llamarlo**. Su llamada fallida se emparejaba con su propio span de
   servidor. No era nuevo: la causa anotada el 25/10 («tardó 23 s y el gateway
   se rindió a los 19 s») salió del mismo emparejamiento.
2. **La regla del destino lento aceptaba cualquier «sin respuesta»**, incluido el
   `ConnectError`. Con un error de conexión el destino no aceptó nada, así que
   no pudo tardar. La línea base de D-141 heredó el error: un plazo de 2 ms daba
   «el 98,5 % tarda más de 0,0 s».

**Arreglo**:

- La contraparte **excluye el servicio que llama**, en la consulta y en el
  emparejamiento.
- «El destino tardó» y la línea base son **solo para timeouts**.
- **Regla nueva, `_nadie_acepto_la_conexion`**, con confianza alta: la clase del
  error es la propia medición del cliente HTTP. Ahora el mismo incidente dice
  *«no pudo abrir conexión con http://127.0.0.1:8087/… (3 intentos en la misma
  petición): `ConnectError`. En esa dirección no escuchaba nadie…»*.
- **Las causas guardadas se etiquetan como «L3 anotó, sin confirmar»**, en
  HISTORIA y en VISTO ANTES. Hoy ninguna persona anota causas, así que toda
  causa guardada es de L3, y algunas son de este defecto. L4 repitió una de ellas
  como si fuera un hecho.

El banco no cambia: 2 de 2 en no vistos y 6 aciertos en diseño. Ningún caso
tenía un `ConnectError`, y ese es justo el hueco por el que entró esto. **Hace
falta un caso de banco con un destino caído.** El incidente de hoy lo es, pero su
causa la ha establecido L3, no una fuente independiente.


### Addendum (26/10): el caso del banco, con la causa medida fuera de L3

`prometheus-503-backend-caido` entra en el banco con nivel **`medida`** y como
caso de **diseño**: la regla nueva se escribió mirando este incidente. La causa
sale de tres fuentes que no son L3:

- **El sistema operativo.** El `llama-server` de `gpt-oss-20b-mxfp4` que escucha
  hoy en `:8087` arrancó a las 14:28:18 hora local, 19:28:18 UTC (`ps -o
  lstart`).
- **Las llamadas del gateway a `:8087` por minuto**: correctas hasta las 19:22,
  tres `ConnectError` a las 19:27 (los tres intentos de la petición de ejemplo) y
  correctas otra vez desde las 19:29.
- **Lo que Axonium nos respondió en esa ventana**: `UnknownModelError` y después
  `BackendUnavailableError` con «circuit half-open».

**Lo que no se pudo establecer**: los `503` de 19:30 a 20:07 no llegan a llamar
al backend (el gateway responde en 2 ms), lo que encaja con su disyuntor
recuperándose. Todos vienen de `127.0.0.1`, y con Docker Desktop eso no distingue
a `diagnose` —cuya sombra siguió apuntando a `gpt-oss` y reintentando hasta las
20:07— de cualquier otro cliente nativo. Que parte de esa carga fuera nuestra es
**posible y no está demostrado**, y la etiqueta no lo afirma.

**El criterio prohibido es exactamente el defecto de D-145**: «dice que el
destino tardó o que es latencia o plazo». Las reglas, después de D-145, aciertan
con confianza alta. Qwen compitiendo da un parcial: nombra el error de conexión
sin decir que el destino estaba caído. Completando, mantiene a las reglas y
acierta. Ninguno de los dos repitió las causas antiguas equivocadas que trae la
HISTORIA congelada.

**Procesos anonimizados.** Al capturar salieron 8 `service.instance.id`, que ya
estaban en otros 6 casos. No son filas de nadie (son UUID que el SDK genera al
arrancar cada proceso), pero son identificadores de otros equipos, y el juez solo
necesita cuántos procesos hubo, de qué versión y desde cuándo. Ahora se guardan
como `proceso-1…N` y un test impide que entre cualquier UUID. Los que se subieron
antes siguen en el historial de git.

**El banco**: 11 casos. No vistos, 2 de 2. Diseño, 7 aciertos y 2 parciales.
Sigue faltando un caso no visto de un tipo distinto a los silencios del host.
---

## D-146 · Qwen3 medido como juez y como investigador: se queda en sombra, L4 apagado

**Estado**: ✅ Medido · sombra de L3 con Qwen en modo «completa» · L4 apagado

`gpt-oss-20b-mxfp4` dejó de estar disponible el 26/10. El dueño gestionó
`qwen3-30b-a3b-q4-k-m-local` con Prometheus y el permiso para nuestro cliente.

### El juez, en el banco

| juez | no vistos (aciertos) | diseño | fallos con confianza alta |
|---|---|---|---|
| **reglas** | **2/2** | **6 aciertos**, 2 parciales | 0 |
| Qwen compite, sin razonamiento | 0/2 (se abstiene) | 0 aciertos, 4 parciales, 2 fallos | **2** |
| Qwen compite, con razonamiento | 1/2 | 2 aciertos, 3 fallos | 1 |
| Qwen completa a las reglas, sin razonamiento | 2/2 | 4 aciertos, 4 parciales | 0 |
| Qwen completa a las reglas, con razonamiento | 2/2 | 3 aciertos, 5 parciales | 0 |

**B-42 respondido: completar no añade nada y quita algo.** Donde las reglas
aciertan a medias (`aeon`, `clients-names`), el modelo tampoco llega. Donde
aciertan, a veces reescribe y pierde lo que hacía buena la causa: en `tenant` se
cae «proceso con el código anterior», y en `pendingrollback`, «consecuencia de
una excepción anterior». Sin razonamiento, el modelo tarda unos 3 s.

**En producción** la sombra pasa a Qwen en modo «completa». Es lo único que el
banco no puede medir: el banco no tiene casos donde las reglas se abstengan, y
en producción sí los hay. La primera comparación real es justo uno de esos, y el
modelo devolvió el síntoma como causa: *«no reporta métricas durante 5
minutos»*, con confianza media. Escribe en español, que `gpt-oss` no hacía.

### L4, en vivo

Sobre el `503` del gateway (D-145):

1. **Sin `submit_tool`, el agente no investigaba.** synaptum pedía
   `response_format: json_schema` y la gramática del servidor obligaba a
   contestar el JSON a la primera. Resultado: «no hay información suficiente» en 3
   s y sin una sola herramienta. Con el informe entregado como herramienta, sí
   investiga: `incidente`, `expediente`, `linea_base` y `concurrentes`, en 8 s y
   ~35.000 tokens sin razonamiento, o 40 s con él.
2. **Lo que concluyó, en las tres pasadas, fue falso**: «latencia alta, el
   gateway se rinde». Antes de D-145 lo copiaba del diagnóstico erróneo de L3.
   Después, de las causas antiguas guardadas, que eran de ese mismo defecto.
3. **Atribuye evidencia a herramientas que no llamó**: cita `[traza]`,
   `[alcance]` y `[linea_base]` habiendo llamado solo a `expediente`. Y dice que
   no hubo errores concurrentes cuando el expediente los listaba.

**L4 se queda apagado.** En sombra no avisaría a nadie, pero cada investigación
gasta GPU de la misma plataforma que usan Centinela y Veritium, y hoy produciría
conclusiones falsas. Lo que falta antes de encenderlo:

- comprobar con código que cada `[herramienta]` citada se llamó de verdad, igual
  que se comprueban las citas del calificador;
- un banco para L4;
- y un modelo que razone mejor sobre el expediente. Ninguno de los dos
  disponibles lo hace hoy.

---

## D-147 · El banco de L4: herramientas congeladas, y L4 con Qwen3 no supera a L3

**Estado**: ✅ Banco construido y medido · L4 sigue apagado

### El banco

L4 no lee un expediente: pregunta. Y lo que una herramienta contesta hoy no es
lo que habría contestado a la hora del incidente. Un banco contra el stack vivo
mediría a la vez al agente y al día en que se pasa.

`banco_l4.py` graba, para cada uno de los 11 casos de F3-08 y **a la hora del
incidente**, lo que contestaría cada consulta razonable: la traza del ejemplo,
el alcance, la recurrencia de sus clases, la línea base de la contraparte, los
concurrentes y el registro. En la evaluación lo sirve **el mismo servidor
`mcp-obs`** con un almacén congelado debajo, así que el agente no distingue el
banco del sistema real. El expediente que sirve es el congelado del caso, el
mismo que lee L3: los dos parten de la misma información, y lo que L4 añade es
solo lo que va a buscar. Lo que el agente pregunte y no esté grabado se contesta
vacío y **se cuenta**.

**Lo que destapó al grabarlo**: el `StatusMessage` de un span de Veritium traía
`case=<uuid> run=<uuid>`, identificadores de su negocio. `sanear` enmascara ya
cualquier UUID, también en lo que `mcp-obs` le pasa a un modelo, y un test lo
vigila en los dos bancos.

### La medida

| | no vistos | diseño | fallos con confianza alta | herramientas por caso |
|---|---|---|---|---|
| **reglas (L3)** | **2/2** | **7 aciertos**, 2 parciales | 0 | — |
| L4 Qwen3, sin razonamiento | 2/2 (los dos con confianza baja) | 0 aciertos, 6 parciales, 3 fallos | **1** | 3–6 |
| L4 Qwen3, con razonamiento | 1/2 | 2 aciertos, 4 parciales, 3 fallos | **1** | 1 (solo `expediente`) |

- **Los dos fallos con confianza alta son los dos casos que mejor separan causa de
  síntoma.** Sin razonamiento, en el destino caído vuelve a decir *«latencia
  excesiva… tiempo límite»*, cuando el expediente dice que nadie aceptaba la
  conexión. Con razonamiento, en `tenant` dice *«las solicitudes no incluyen
  tenant en su payload»*, cuando la causa es una migración con el proceso viejo
  vivo.
- **Sin razonamiento investiga más y concluye peor.** Con razonamiento lee el
  expediente y entrega, sin pedir nada más. En los dos modos el banco cubrió casi
  todo lo que el agente preguntó: 5 de unas 45 consultas sin grabar sin
  razonamiento, 2 con él.
- **Citas**: con razonamiento, 15 marcadas como de una herramienta no llamada.
  11 eran bloques del expediente rotulados con el nombre del bloque
  (`[alcance]`), con los datos correctos. Ahora se aceptan si se leyó el
  expediente. **4 nombran de verdad una herramienta que no llamó**, como
  `[traza]` sin haber pedido la traza.

### Qué se decide

**L4 sigue apagado.** No acierta más que L3 en ningún caso, y rompe la única
invariante que el banco no tolera: un fallo con confianza alta. Con este modelo,
investigar no le da lo que le falta, que es razonar bien sobre lo que ya tiene
delante.

**Lo que se queda y es lo que vale**: el banco. Cualquier modelo que llegue
—Prometheus está buscando uno— se mide con una orden y contra la misma vara que
L3:

```
python -m diagnose.banco_l4 evaluar --modelo <id> [--razonamiento high]
```

---

## D-148 · F3-05: memoria por parecido, SQLite y no pgvector, con `qwen3-embedding`

**Estado**: ✅ En producción · solo añade contexto al expediente

**Para qué.** F3-06 (D-142) encuentra episodios de la misma clase en la misma
app. Lo que no puede encontrar es el mismo problema con otra clase o en otra
app: el `PendingRollbackError` de `veritium-worker` y el `NotNullViolationError`
de `veritium-api` del 14/10 son la misma migración (VRT-57).

**Decisiones**:

- **SQLite en el volumen de `diagnose` y fuerza bruta, no pgvector.** Son unos
  850 incidentes al mes, y comparar contra miles de vectores en Python puro
  cuesta milisegundos. Un Postgres serían un servicio, unas credenciales y unas
  migraciones más para un problema que a esta escala no existe.
- **Se indexa el PROBLEMA, no el diagnóstico ni el sitio.** Tipo, firma, título,
  la descripción de la alerta y la cadena del fallo de la traza. Sin nombres de
  app ni de componente: en la primera versión pesaban tanto que el `503` del
  gateway se parecía 1,00 a un `409` del mismo gateway. Y nunca `root_cause`,
  porque parte de lo guardado son diagnósticos equivocados (D-145).
- **El embebedor es intercambiable.** El léxico, sin modelo, es la línea base.
  `qwen3-embedding` (1.024 dimensiones) llegó el 26/10 por Axonium con un límite
  de 40.000 tokens por minuto. El embebedor espera lo que dice el límite, el
  bucle indexa 32 incidentes por ciclo y el histórico se llena solo en unos
  minutos.

**La medida** (`banco/memoria/pares.json`, parejas con la misma causa establecida
fuera de L3):

| embebedor | con lo esperado entre los 3 primeros | negativos entre los 3 primeros |
|---|---|---|
| léxico | 2/3 | 0 |
| `qwen3-embedding` | 2/3 | 0 |

Son tres consultas: es una comprobación de humo, no una cifra. **No separa los
dos embebedores**, y se dice. Los dos encuentran la migración desde el
`PendingRollbackError`, aunque con el léxico es casi trampa, porque la firma
contiene literalmente `NotNullViolationError`. Los dos fallan el silencio de
`veritium-api` del 23/10 por el mismo motivo: ponen primero un silencio del
`alert-bus` del 15/10 con un texto idéntico. Si fue el host dormido, sería un
acierto sin etiquetar, pero el registro de energía del Mac solo llega al 19/10 y
no se puede comprobar.

**Se elige Qwen** por lo que se ve al leer las listas, no por la cifra. Donde el
léxico se iba a errores sin relación del mismo componente, Qwen pone otros
silencios y otras caídas.

**Riesgo bajo, por diseño.** Solo añade el bloque PARECIDOS al expediente, con el
aviso de que parecido no es misma causa. Las reglas no lo leen, así que no
cambia ningún aviso. Para el juez en sombra y para L4 es contexto.

**Lo que falta** es un banco de memoria con más parejas. Cada caso nuevo de F3-08
con una causa compartida debería añadir una.

---

## D-149 · F4-01: L4 es durable, y reanudar no repite inferencias (medido contra el stack)

**Estado**: ✅ Hecho y verificado · L4 sigue apagado por calidad (D-147), no por esto

**Lo que pedía F4-01** (D-135): investigación de varios pasos con synaptum, un
checkpoint por paso, y que reanudar con el mismo `run_id` no repita
inferencias. Lo de varios pasos ya estaba (D-144). Faltaba que fuera durable:
el investigador usaba un `run_id` aleatorio y sin diario, así que un reinicio
del contenedor a mitad de una investigación tiraba lo ya pagado.

**Qué se hizo**:

- **Diario persistente**: `SqliteCheckpointer` de synaptum en
  `/var/lib/argus/l4.sqlite`. La idempotencia la garantiza la clave primaria
  `(run_id, step_id, phase)`, no el código.
- **`run_id` determinista**, a partir del modelo, la versión de las
  instrucciones, el razonamiento, la huella y el momento en que se encoló. La
  misma petición da el mismo run; el mismo incidente pedido seis horas después da
  otro y no comparte diario.
- **Pendientes en disco.** La sombra de L4 guarda cada trabajo con su `run_id`
  antes de investigarlo y lo marca al terminar. Al arrancar, retoma los que no
  terminaron con su mismo `run_id`.
- **El resultado dice cuántos pasos salieron del diario** (`pasos_reproducidos`,
  del `replayed_steps` que synaptum pone en el paso final).

**Verificado contra Qwen3 y el stack real**: una investigación de un `page` real
(`ArgusSLOBurnRateFast` del gateway) se mató con `kill -9` con tres pasos
completados (modelo, herramienta, modelo). Al retomarla con el mismo `run_id`
informó de **3 pasos reproducidos**, siguió con lo que faltaba y entregó el
informe. Con el doble de synaptum, la misma prueba deja medido que la
reanudación hace **1 llamada al modelo de 2 y 0 ejecuciones de herramienta**.

**Un intento de prueba que no valía, y por qué**: contar las peticiones en las
trazas del gateway de Prometheus. En el mismo minuto la sombra de producción
también llamó a Qwen, y el muestreo de cola descarta parte de las peticiones de
menos de 3 s. La prueba buena es la que da el propio synaptum.

**F4-01 queda hecho como capacidad.** L4 sigue apagado por lo que midió D-147:
con los modelos disponibles no supera a L3. Cuando llegue uno mejor, la
durabilidad ya está.

---

## D-150 · F4-03: L4 en sombra en producción, con frenos, y la primera noche no tuvo modelo

**Estado**: ✅ Encendido · investiga y registra, nunca notifica

**Lo que faltaba de F4-03** era encender la sombra de L4 (D-144) en producción,
que es donde están los casos que el banco no tiene: incidentes en los que las
reglas se abstienen.

**Los frenos**, porque encenderlo tal cual habría sido una avalancha. El 26/10
hubo **145 `page`** y el 25/10, 61, muchos de ellos los mismos incidentes del
colector reabriéndose. Cada investigación son unos 35.000 tokens de la
plataforma que comparten Centinela y Veritium.

- **Por episodio, no por huella.** Lo que no se repite en 6 h es el mismo
  `(app, componente, clase)`: el colector que se reabre cada pocos minutos es un
  problema, no cuarenta.
- **Un ritmo máximo**: `DIAGNOSE_L4_POR_HORA`, 2 por defecto. Son unas 48 al
  día, cerca de 1,7 M de tokens y unos 6 minutos de GPU. Lo que no cabe se cuenta
  (`descartadas_por_ritmo`) y se tira.
- **Sin razonamiento**, con el `DIAGNOSE_RAZONAMIENTO=off` que comparte con el
  juez: unos 8 s por investigación.

**La garantía**, fijada con un test: con L4 encendido, el aviso lleva solo el
veredicto de las reglas. L4 corre en otro hilo y no tiene ningún camino hacia el
alert-bus: sus herramientas son de solo lectura y escribe únicamente en su
JSONL.

**Leerlo**: `python -m diagnose.informe_sombra` (dentro del contenedor, con
`/app/.venv/bin/python`). Pone lado a lado las reglas, el juez en sombra y L4, el
coste y en qué acabó cada investigación. Señala los incidentes en los que una
sombra propone causa y las reglas no, que son los candidatos a caso de banco.

**La primera noche, sin modelo de chat.** Las dos primeras investigaciones
acabaron en `error:ProviderError` en menos de un segundo. Prometheus había
retirado `qwen3-30b-a3b-q4-k-m-local`: no hay `llama-server` para él y responde
«not registered». Y el gateway seguía rechazando `gpt-oss-20b-mxfp4` con
«circuit half-open, recuperación más temprana a las 19:28:21» más de 7 horas
después, con el backend de `:8087` levantado desde esa hora. Parece un
disyuntor atascado. Es de Prometheus y se le dice a través del dueño. La sombra
hizo lo que tenía que hacer: dos informes parciales sin causa, registrados, y
ningún aviso tocado.

---

## D-151 · `qwen3-8-27b` y el prompt del juez revisado: deja de abstenerse, pero no es para producción

**Estado**: ✅ Medido · prompt `l3-juez-2` y `l4-investigador-2` · L4 en sombra apagado

El dueño consiguió `qwen3-8-27b-ud-iq4-xs-local` y apuntó que quizá el problema era
el prompt y no el modelo. Las dos cosas se midieron por separado.

### El modelo, con el prompt de antes (`l3-juez-1`), sin razonamiento

No vistos 0/2 (se abstiene en los dos). Diseño 1 acierto, 4 parciales, 2
abstenciones y 2 fallos, **los dos con confianza alta**. Razona mejor de lo que
dice la cifra: en el destino caído dice «no está disponible o no está escuchando
en ese puerto», y en `/v1/backends`, «el plazo del gateway es inferior al tiempo
de respuesta habitual». Las dos causas son correctas y el calificador por
palabras no las reconoce. Pero se abstiene en **los cuatro** silencios aunque el
expediente traiga escrita la conclusión.

### El prompt revisado

Cada cambio sale de un fallo en un caso de **diseño** y está escrito como
principio general. Los dos no vistos no se miraron para escribirlo:

- **`veritium-silencio`**: se abstenía con «a ráfagas: es del host o de la
  ingesta» delante. → Las líneas del expediente que concluyen algo son
  mediciones, una causa con reserva se dice con esa reserva, y solo se abstiene
  si **ningún** bloque apunta a una causa.
- **`tenant`**: no leía PROCESOS. → Se recorren los bloques en un orden fijo.
- **`aeon`**: inventaba «incompatibilidad de versión» con confianza alta. → Si el
  mensaje explica el fallo, la causa es lo que dice. La confianza es «alta» solo
  si un bloque **mide** la causa.

### El resultado (sin razonamiento)

| | no vistos | diseño | fallos con confianza alta |
|---|---|---|---|
| reglas | **2/2** | **7 aciertos** | 0 |
| 27B, prompt 1 | 0/2 (se abstiene) | 1 acierto, 2 fallos | 2 |
| 27B, prompt 2 | **1 acierto + 1 parcial** | 2 aciertos, 6 parciales, 1 fallo | **1** |

- **Deja de abstenerse**: 0 abstenciones, frente a 4. Los dos no vistos pasan a
  «es del host o de la ingesta». El parcial de `canario` es la composición: no
  dice «todos los servicios a la vez».
- **Pero el principio se pasa de frenada en `tei`**: ahora dice «host o
  ingesta», cuando la causa es que nunca emitió. Ese expediente también traía la
  plataforma a ráfagas, y EMISIÓN ANTERIOR («NADA en 30 días») debería pesar más.
  Las reglas lo resuelven por orden: `_nunca_ha_emitido` va antes que
  `_calla_toda_la_plataforma`.
- **Queda un fallo con confianza alta**, `tenant`: sigue sin ver el proceso viejo.
- **Advertencia sobre la mejora en los no vistos**: son silencios del mismo tipo
  que `veritium-silencio`, el caso de diseño que inspiró el cambio. Que el
  principio generalice a silencios de otras apps es poco: demuestra que no se
  escribió para un caso, no que valga para otros tipos de fallo.

### Lo que lo descarta para producción: el tiempo

- **Sin razonamiento**: 15–54 s por juicio. El juez en sombra corre en línea,
  después de devolver el veredicto de las reglas, así que retrasaría a los demás
  incidentes del ciclo.
- **Con razonamiento**: de 2 a 49 minutos por caso, y a partir del quinto caso
  `ProviderError`. Solo valen 4 casos de esa pasada.
- **L4**: con expedientes de ~35.000 tokens llevaba hora y media y seguía en el
  segundo caso. Se paró para no ocupar durante horas la GPU que comparten
  Centinela y Veritium.

**Decisiones**: el prompt revisado se queda, porque mejora a los dos modelos
medidos en lo que importaba (abstenerse con la respuesta delante). **L4 en
sombra se apaga**: `qwen3-30b` ya no se sirve y con el 27B no cabe. El juez en
sombra apunta todavía a `qwen3-30b`; falla al instante y deja el veredicto de
las reglas. Si se pasa al 27B, lo decide el dueño, porque le cuesta GPU y
retrasa el ciclo.
