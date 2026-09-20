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
