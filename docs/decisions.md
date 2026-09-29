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
