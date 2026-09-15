# Piloto: conectar la primera aplicación real

Guía para conectar **una** aplicación y ver el flujo entero funcionando, antes
de extenderlo al resto del portafolio.

```bash
make pilot-check     # ¿está todo listo? Te dice exactamente qué falta
```

---

## Lo único que falta hacer tú

**Configurar un canal de notificación.** Es lo único que no puedo dejar hecho:
necesita una credencial tuya.

**Telegram**, dos minutos y sin depender de ninguna cuenta corporativa. Los
detalles están en [`canales.md`](canales.md); el resumen:

1. `/newbot` con [@BotFather](https://t.me/BotFather) → copia el token.
2. Busca tu bot y pulsa **Iniciar**. Sin eso Telegram le impide escribirte.
3. `ALERTBUS_TELEGRAM_TOKEN=...` en `platform/.env`, y luego:

```bash
make telegram-setup   # espera a que pulses Iniciar y rellena el chat_id
make up && make channel-test
```

> **Google Chat sigue implementado pero probablemente no te sirva**: sus
> webhooks entrantes son una función de Google Workspace, y una cuenta personal
> muestra la opción en gris (D-044).

---

## Qué aplicación elegir

La mejor candidata para un piloto **no es la más importante**, es la que más
enseña con menos riesgo:

| Prefiere… | Porque… |
|---|---|
| Una que uses a diario | Verás datos reales sin fabricar tráfico |
| Con un worker o una cola | Ejercita la propagación fuera de HTTP, que es donde las trazas se parten |
| Que falle de vez en cuando | Sin errores no ves la mitad del sistema |
| **No** la más crítica | Si algo va mal, que no sea la que no puede fallar |

**El piloto en curso es `auth-service`, de Prometheus** —la plataforma de
inferencia local, cuyo repositorio está en `edge-ai-inference/`—. Encaja bien:
2.067 líneas frente a las 9.510 del gateway, usa el paquete de telemetría
compartido de la plataforma (así que lo aprendido sirve para los demás módulos),
y es dependencia del gateway, con lo que además ejercita el grafo de
correlación.

> **Ojo con el nombre**: *Prometheus* aquí es el proyecto de inferencia de tu
> portafolio, no el TSDB. En Argus conviven los dos, así que conviene
> desambiguar al escribir.

---

## Paso 1 — Ver datos sin tocar código

**Si la app ya tiene OpenTelemetry**, empieza aquí. Cero líneas de código:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_SERVICE_NAME=idp-api
export OTEL_RESOURCE_ATTRIBUTES=service.namespace=intelligent-document-platform,argus.component.role=api,deployment.environment.name=mac-dev
```

Arranca la app, úsala un poco, y mira:

```bash
make query SQL="
  SELECT SpanName, count() AS n
  FROM otel.otel_traces
  WHERE ResourceAttributes['service.namespace'] = 'intelligent-document-platform'
  GROUP BY SpanName ORDER BY n DESC LIMIT 10 FORMAT PrettyCompact"
```

**Si ves spans, el camino funciona y el resto es mejora incremental.** Si no,
para aquí y averigua por qué: todo lo demás depende de esto.

---

## Paso 2 — Instalar la librería

Durante el piloto se instala desde una **etiqueta de git**: da versionado real,
es reproducible, funciona dentro de contenedores y no necesita infraestructura.

```bash
# En el repo de Argus: etiqueta una versión
make release V=1.0.0a2
```

Y luego, para saber **qué teclear exactamente** en el repo de tu aplicación:

```bash
make install-cmd
```

Imprime el comando con la ruta absoluta y la versión ya rellenadas. **Cópialo
de ahí**, no de esta guía: una ruta escrita a mano es la forma más tonta de
perder media hora.

Sale algo así:

```
uv pip install --find-links /Users/tu-usuario/…/app_monitoring_explainability/dist \
  'argus-obs-sdk[asgi,client,sql]==1.0.0a2'
```

Los extras, según lo que use tu app:

| Extra | Para |
|---|---|
| `asgi` | FastAPI, Starlette |
| `client` | httpx, requests |
| `sql` | SQLAlchemy, asyncpg |
| `celery` | Colas Celery |
| `kafka` | Kafka, Redpanda |
| `genai` | LangChain, LangGraph, Ollama, vLLM… (activa OpenLIT) |

> **La versión va explícita, y no es opcional.** Con un prelanzamiento
> (`1.0.0aN`), `uv pip install argus-obs-sdk` a secas falla con
> *«pre-releases weren't enabled»*, que no apunta a la causa. Poner `==1.0.0a2`
> lo resuelve. Es la contrapartida —deseada— de que un `pip install` normal
> nunca se lleve un prelanzamiento por accidente (D-038).

Si la aplicación vive en **otra máquina**, `make install-cmd` imprime también la
variante desde etiqueta de git, que no necesita acceso al directorio `dist`.

Cuando ajustemos algo del SDK: `make release V=1.0.0a3`, y tu app sube la
versión en su comando de instalación.

> **No usamos TestPyPI**, aunque parezca hecho para esto: sus dependencias no
> están ahí de forma fiable, así que haría falta apuntar también a PyPI real —
> y eso reintroduce justo la confusión de dependencias que el renombrado
> eliminó (D-036).

Los extras según lo que use tu app: `asgi` para FastAPI, `client` para
httpx/requests, `sql` para SQLAlchemy, `celery`, `kafka`, `redis`, `genai`.

> **Ojo con el nombre.** Es `argus-obs-sdk`, no `argus-sdk`: ese último existe
> en PyPI y es de otra persona (D-035). El nombre de import sí es `argus`.

---

## Paso 3 — Las dos líneas

```python
# main.py, antes de cualquier import que loguee
import argus

argus.init(
    "idp-api",                                   # el COMPONENTE
    namespace="intelligent-document-platform",   # la APLICACIÓN
    role="api",                                  # api|worker|scheduler|cli|model-server
    version="0.4.2",
)

app.add_middleware(argus.ASGIMiddleware, propagate_mode="trusted")
```

Y en el entorno:

```bash
ARGUS_ENDPOINT=http://localhost:4317
ARGUS_ENVIRONMENT=mac-dev
ARGUS_SLO_MS=8000        # por encima de esto, el span se marca para detección
```

Eso te da: trazas, métricas, logs correlacionados, y auto-instrumentación de
todo lo que tengas instalado.

### Si la app tiene worker o cola

Es lo que más valor aporta del piloto, porque es donde las trazas se parten:

```python
from argus import propagate

# Al encolar
mensaje = propagate.inject_payload({"doc_id": 7})

# En el worker
with propagate.extract_payload(mensaje, name="documento.procesar"):
    procesar()
```

Con Celery basta `propagate.instrument_celery()`.

**La prueba de que funciona**: una unidad de trabajo que cruza API → cola →
worker produce **una sola traza**. Si salen dos, la propagación no está bien.

```bash
make query SQL="
  SELECT TraceId, count() AS spans, groupArray(SpanName) AS nombres
  FROM otel.otel_traces
  WHERE ResourceAttributes['service.namespace'] = 'intelligent-document-platform'
  GROUP BY TraceId ORDER BY spans DESC LIMIT 3 FORMAT Vertical"
```

---

## Paso 4 — Registrar la aplicación

En `platform/registry/apps.yaml`, cambia su `estado` a `activo`:

```yaml
- id: intelligent-document-platform
  estado: activo          # <- era `planificado`
  criticidad: alta
  canales: {page: [gchat], ticket: [gchat]}
  componentes:
    - {id: idp-api, rol: api, slo: {p95_ms: 8000}}
    - {id: idp-worker, rol: worker}
  depende_de: [postgres-main, minio-main]
```

Esto hace tres cosas: el canario empieza a vigilar su silencio, la correlación
puede usar sus dependencias, y las notificaciones saben a dónde ir.

Reinicia el `alert-bus` y el `canary` para que lo recojan.

---

## Paso 4b — Dónde lo miras

```bash
make dash        # abre Grafana e imprime la credencial
```

Dos dashboards, que responden preguntas distintas:

**`Argus · una aplicación`** — *«¿cómo va mi aplicación?»*. Elige la tuya en el
selector de arriba. De arriba abajo: los seis números del estado actual, luego
tasa de error y latencia en el tiempo, luego el desglose por componente y por
tipo de error, luego tokens y coste, y al final una tabla de trazas con error o
lentas.

Esa tabla es el puente entre las dos preguntas: pulsa un `TraceId` y saltas de
*«cómo va»* a *«qué pasó exactamente en esta petición»*.

**`Argus · la plataforma`** — *«¿está la plataforma mirando?»*. Ningún otro
dashboard puede responder eso, porque todos los demás asumen que sí. Si
`Descartes` sube de cero, hay huecos en tus datos — y un hueco parece silencio.

Y tiene un límite honesto: si la plataforma cae del todo, este dashboard
tampoco se ve. Para eso está el *dead man's switch*, que vive fuera.

### Para verlos con datos antes de conectar nada

```bash
make demo-traffic M=2
```

Genera tráfico de una aplicación simulada —tres componentes, algo de error,
llamadas a un LLM— para que puedas mirar los dashboards y decidir si te sirven
antes de tocar una app real.

---

## Paso 5 — Provocar un incidente a propósito

**No des el piloto por bueno hasta haber visto llegar un aviso.** Un sistema de
detección que nunca ha detectado nada no está probado, está sin usar.

```bash
# Haz fallar algo de verdad: para su base de datos, mete un timeout de 1 ms,
# apunta a un endpoint que no existe…
```

Deberías ver, en este orden:

1. **En segundos**, un mensaje en Google Chat. El camino caliente va de
   `span.end()` al aviso en ~120 ms (p95), más lo que tarde el lote.
2. `make incidents` muestra el incidente con su cuenta de señales.
3. Si el error se repite, la cuenta sube y **no llegan más mensajes**.

Si algo de eso no pasa, es mejor descubrirlo ahora que el día que importe.

---

## Paso 6 — Si la app usa LLM

```bash
make genai          # arranca Langfuse y te da la credencial
make genai-check    # verifica que las trazas de prompts llegan
```

**No hay que crear el proyecto ni copiar claves**: la organización, el proyecto
y las claves de API se provisionan desde el `.env`, y `ARGUS_LANGFUSE_AUTH` se
calcula solo (D-039). La primera vez tarda un poco, porque Langfuse migra sus
tablas de ClickHouse.

Lo que verás cuando funcione: **la misma traza en los dos sitios**. ClickHouse
tiene el árbol completo —la petición HTTP, la cola, el worker, la llamada al
modelo—; Langfuse tiene solo el subárbol GenAI, pero con los prompts, el coste y
la evaluación. Comparten `trace_id`, así que saltar de uno a otro es seguir un
identificador.

Y el contenido de los prompts vive **solo** en Langfuse: en ClickHouse se borra,
donde serían decenas de KB por span sin ninguna consulta que los use.

Para instrumentar las llamadas: si la app usa **Axonium**, instrumentar Axonium
una vez da trazas GenAI a **todas** las apps que lo usen, sin tocarlas
(D-002). Si usa LangChain, LangGraph, Ollama o vLLM directamente,
`argus.init()` ya activa OpenLIT si está instalado (`[genai]`).

---

## Qué mirar durante el piloto

Dale una o dos semanas. Lo que hay que vigilar no es si funciona el primer día:

| Pregunta | Cómo responderla |
|---|---|
| ¿Cuánta telemetría genera? | `make query SQL="SELECT count() FROM otel.otel_traces"` — decide la retención con datos |
| ¿Cuántas alertas por semana? | Más de unas pocas y hay que afinar SLO antes del rollout |
| ¿Alguna alerta fue inútil? | Es la señal más valiosa del piloto: afina umbrales o criticidad |
| ¿La cola crece? | `curl -s localhost:8889/metrics \| grep queue_size` |
| ¿Cuánto ocupa el disco? | El plano central vive en un portátil |

---

## Cuándo se puede cerrar el piloto

La primera versión de este documento decía «cuando lleve un par de semanas sin
sorpresas». Eso no es comprobable, y un criterio que no se puede comprobar se
cumple el día que alguien tiene prisa. Estos sí:

### Lo que el piloto tiene que haber demostrado

| | criterio | cómo se comprueba |
|---|---|---|
| 1 | Una aplicación real emite con identidad correcta | `service.namespace`, `service.name`, rol y entorno en el almacén |
| 2 | Un incidente real llega a una persona | El aviso en el canal, no en el log |
| 3 | Una tormenta real se deduplica | Miles de señales → decenas de notificaciones |
| 4 | El silencio de un servicio real abre incidente | Pararlo y esperar la ventana |
| 5 | **Una traza cruza una frontera que no es HTTP** | Una unidad de trabajo API→cola→worker en **una sola** traza |
| 6 | **Un segundo host** manda telemetría | Un agente en otra máquina (`F1-10`) |
| 7 | **Hay red de seguridad externa** | El *dead man's switch* corriendo fuera de los contenedores (`B-16`) |
| 8 | **Catorce días sin un fallo nuevo de la plataforma** | Ninguna decisión nueva del tipo «esto no hacía lo que decía» |

Los cuatro primeros están **demostrados con datos** (§ «Qué mirar»). Los cuatro
últimos, no.

El 5 es el que más importa y es el que el plan llamaba *la prueba que define la
fase*: si una unidad de trabajo que cruza una cola produce **dos** trazas en vez
de una, la propagación fuera de HTTP no funciona y nada de lo que se construya
encima sirve. Los tres servicios del piloto actual son APIs HTTP, así que ese
caso **sigue sin ejercitarse con tráfico real**.

El 8 no es burocracia. Entre el 13 y el 15 de septiembre el piloto destapó, solo
de nuestro lado: la seudonimización diseñada y nunca implementada, la sonda de
silencio dando por vivo a un muerto (dos veces, por causas distintas), el camino
templado incapaz de entregar una sola alerta, toda regla convertida en `page`
dijera lo que dijera, y el token del gateway versionado en git. **Cerrar el
piloto mientras sigue encontrando cosas a ese ritmo es declarar terminada una
plataforma que aún no lo está.**

### Y entonces, el rollout

1. **Levanta el índice privado** (`B-10`). Con `--find-links` a una ruta local
   no escalas a quince repos, y menos dentro de contenedores. Ya hay un equipo
   que lo pidió explícitamente antes de adoptar el paquete.
2. **Monta la red privada** (`B-11`). Sin un nombre estable, mover el plano
   central obliga a reconfigurar todos los agentes.
3. **Aplica la plantilla** al resto, priorizando por criticidad del registro y
   por volumen de tráfico LLM, no alfabéticamente.
4. **Despliega un agente por máquina** conforme aparezcan apps fuera de esta.

El detalle está en `roadmap.md`, fase F7.
