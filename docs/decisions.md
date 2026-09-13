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
