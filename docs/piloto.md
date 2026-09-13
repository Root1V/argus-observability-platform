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

Google Chat es el más rápido — un webhook de espacio, sin aprobación de
proveedor ni coste por mensaje:

1. En el espacio de Google Chat → *Apps y integraciones* → *Webhooks* →
   *Añadir webhook*. Copia la URL.
2. En `platform/.env`:

```bash
ALERTBUS_SINKS=console,json,gchat
ALERTBUS_GCHAT_WEBHOOK=https://chat.googleapis.com/v1/spaces/XXX/messages?key=...&token=...
```

3. `docker compose -f platform/compose.yaml --profile lean up -d alert-bus`

Comprueba que llegó:

```bash
make pilot-check          # debe decir "Listo para el piloto"
```

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

De tu portafolio, `intelligent_document_platform` encaja bien: tiene API,
worker y un componente de OCR, usa LLM, y ya tiene OpenTelemetry (así que el
primer paso es gratis).

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

El propio comando imprime al final las dos formas de instalarla, con la ruta y
la versión ya rellenadas. Cópialas de ahí en vez de escribirlas a mano.

**Desde la etiqueta de git** — es lo que usarás si la app vive en otra máquina:

```bash
uv pip install "argus-obs-sdk[asgi,client,sql] @ git+<repo>@v1.0.0a2#subdirectory=libs/argus-sdk"
```

**Desde las ruedas locales** — más rápido para iterar en la misma máquina:

```bash
make wheels
uv pip install --find-links /ruta/a/app_monitoring_explainability/dist \
  'argus-obs-sdk[asgi,client,sql]==1.0.0a2'
```

> **La versión va explícita, y no es opcional.** Con un prelanzamiento
> (`1.0.0aN`), `uv pip install argus-obs-sdk` a secas falla con
> *«pre-releases weren't enabled»*, que no apunta a la causa. Poner `==1.0.0a2`
> lo resuelve. Es la contrapartida de que un `pip install` normal nunca se lleve
> un prelanzamiento por accidente.

Cuando ajustemos algo del SDK: `make release V=1.0.0a3` y tu app sube la
versión.

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
docker compose -f platform/compose.yaml -f platform/compose.genai.yaml \
  --profile genai up -d
```

Crea un proyecto en Langfuse (`http://localhost:3000`), copia sus claves y
ponlas en `platform/.env` como `ARGUS_LANGFUSE_AUTH` (base64 de
`public:secret`). Reinicia el Collector.

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

## Del piloto al rollout

Cuando el piloto lleve un par de semanas sin sorpresas:

1. **Levanta el índice privado** (`B-10`). Con `--find-links` a una ruta local
   no escalas a quince repos, y menos dentro de contenedores.
2. **Monta la red privada** (`B-11`). Sin un nombre estable, mover el plano
   central obliga a reconfigurar todos los agentes.
3. **Aplica la plantilla** al resto, priorizando por criticidad del registro y
   por volumen de tráfico LLM, no alfabéticamente.
4. **Despliega un agente por máquina** conforme aparezcan apps fuera de esta.

El detalle está en `roadmap.md`, fase F7.
