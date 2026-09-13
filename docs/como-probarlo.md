# Cómo probarlo

Desde cero hasta ver tus propias trazas. Cada sección dice **qué demuestra**,
porque una prueba que no sabes qué demuestra no sirve de nada.

---

## 0. Lo mínimo: dos comandos

```bash
make setup   # instala dependencias y genera platform/.env con secretos
make check   # verificación rápida, sin Docker (~1 min)
```

`make check` corre las 69 pruebas, el linter, el test de deriva de convenciones
y el ejemplo. **Si esto pasa, las librerías funcionan.**

---

## 1. La demostración más corta: el apalancamiento de librerías

```bash
make demo
```

Verás cuatro spans. Fíjate en cuáles:

```
chat qwen2.5-coder-7b       tokens_salida=312
execute_tool                tool=buscar_documentos
documento.procesar          27ms  outcome=ok
invoke_agent
```

**Qué demuestra**. La aplicación ([examples/aplicacion_instrumentada.py](../examples/aplicacion_instrumentada.py))
escribió **dos** líneas de instrumentación: `argus.agent(...)` y
`argus.step(...)`. Los spans `chat` y `execute_tool` los emitió la **librería**
([examples/libreria_instrumentada.py](../examples/libreria_instrumentada.py)),
que es el equivalente de Axonium.

Ahora comprueba la otra mitad del contrato:

```bash
# La misma librería, en un script que NUNCA llama a argus.init()
uv run python examples/libreria_instrumentada.py
```

Funciona igual y **no emite nada**. Esa es la razón de que Axonium pueda
importar `argus-semconv` sin imponer telemetría a nadie (D-002).

---

## 2. El plano central

```bash
make up    # 4 contenedores, espera a que estén sanos
make ps
```

Deberías ver `clickhouse`, `collector`, `victoriametrics` y `vmalert` arriba.

```bash
curl -sf http://127.0.0.1:13133/ && echo "collector OK"
curl -sf http://127.0.0.1:8428/health && echo "victoriametrics OK"
curl -sf http://127.0.0.1:8123/ping && echo "clickhouse OK"
```

**Qué demuestra**. Que el plano central arranca en modo ligero, que es lo que
cabe en un portátil que además corre inferencia local (D-017).

---

## 3. La verificación completa

```bash
make verify
```

Seis bloques. Los dos últimos son los que no puede hacer `make check`:

**Configs del Collector contra su binario real.** Las tres configuraciones
(agente, gateway ligero, gateway + overlay GenAI) se validan con el `validate`
del propio Collector. Un fallo aquí es un contenedor en bucle de reinicio a las
tres de la mañana (D-023).

**Prueba end-to-end contra el stack real.** Escribe en la ClickHouse de verdad
y consulta lo que llegó:

```
OK  El endpoint OTLP rechaza peticiones sin token
OK  Las trazas llegan a ClickHouse
OK  API -> cola -> worker produce UNA sola traza
OK  El span GenAI llega con nombre, tokens y modelo servido
OK  Un error marca argus.hot para el camino caliente
OK  La PII del prompt no llega en claro al almacen
OK  La identidad de dos niveles permite agrupar por aplicacion
```

Tarda un par de minutos, casi todo esperando al `decision_wait` de 30 s del tail
sampling. **Ese es exactamente el motivo de que la detección en tiempo real no
pase por ahí** (D-005).

Sin mocks, y no por purismo: **cinco de las veinticuatro decisiones de
[decisions.md](decisions.md) las encontró esta prueba**.

---

## 4. Ver los datos con tus propios ojos

```bash
# Cuántos spans hay y de qué aplicaciones
make query SQL="
  SELECT ResourceAttributes['service.namespace'] AS app,
         ServiceName AS componente,
         count() AS spans
  FROM otel.otel_traces
  GROUP BY app, componente ORDER BY spans DESC FORMAT PrettyCompact"
```

Esta consulta es en sí una prueba de la **identidad de dos niveles** (D-007):
agrupa por aplicación y desglosa por componente. Sin esa separación tendrías
decenas de servicios planos.

```bash
# Una traza completa, con su jerarquía
make query SQL="
  SELECT SpanName, ParentSpanId != '' AS tiene_padre, Duration/1e6 AS ms, StatusCode
  FROM otel.otel_traces
  WHERE TraceId = (SELECT TraceId FROM otel.otel_traces ORDER BY Timestamp DESC LIMIT 1)
  ORDER BY Timestamp FORMAT PrettyCompact"
```

```bash
# Lo que el camino caliente filtraría hacia el alert-bus
make query SQL="
  SELECT ServiceName, SpanName, SpanAttributes['error.type'] AS error
  FROM otel.otel_traces
  WHERE SpanAttributes['argus.hot'] = 'true'
  ORDER BY Timestamp DESC LIMIT 10 FORMAT PrettyCompact"
```

```bash
# Los atributos GenAI que emitió la librería
make query SQL="
  SELECT SpanName,
         SpanAttributes['gen_ai.request.model']  AS pedido,
         SpanAttributes['gen_ai.response.model'] AS servido,
         SpanAttributes['gen_ai.usage.output_tokens'] AS tokens
  FROM otel.otel_traces
  WHERE SpanAttributes['gen_ai.operation.name'] = 'chat'
  ORDER BY Timestamp DESC LIMIT 5 FORMAT PrettyCompact"
```

Fíjate en que **`pedido` y `servido` difieren**. Esa diferencia —alias, routing,
cuantización— explica incidentes, y por eso se registran por separado.

---

## 4b. El camino caliente: de un error a un aviso

Necesita el Collector agente, que es quien filtra y alimenta el `alert-bus`:

```bash
make agent        # arranca el agente en esta máquina
make latency      # mide el presupuesto, 10 muestras
```

Salida real de esta máquina:

```
  p50        112 ms
  p95        120 ms
  peor       120 ms

Dentro del presupuesto de 2 s.
```

**Qué demuestra**. Que la detección ocurre **sobre el flujo**, sin tocar la base
de datos. El tail sampling del gateway sigue esperando sus 30 s para almacenar,
y eso ya no retrasa el aviso (D-005).

Dos matices honestos sobre ese número:

- La medición fuerza el envío (`force_flush`). En producción el procesador de
  spans añade hasta `schedule_delay_ms` (200 ms por defecto), así que el caso
  real está más cerca de **320 ms en el peor caso** — aún muy dentro.
- Todo corre en una máquina, sobre la red de Docker. Cruzar máquinas por la red
  privada añadirá latencia de red (`F1-10`).

### Ver la deduplicación funcionando de verdad

```bash
make incidents    # antes
```

Ahora provoca una tormenta:

```bash
set -a && . platform/.env && set +a
ARGUS_ENDPOINT=http://127.0.0.1:4318 ARGUS_PROTOCOL=http/protobuf \
OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer $ARGUS_GATEWAY_TOKEN" \
uv run --with 'opentelemetry-exporter-otlp-proto-http' python -c "
import warnings; warnings.simplefilter('ignore')
import argus
h = argus.init('dedup-probe', namespace='intelligent-document-platform',
               role='model-server', json_logs=False)
for i in range(20):
    with argus.step('ocr.extract') as s:
        s.error('timeout', retryable=True)
h.force_flush(timeout_millis=5000)
"
make incidents
```

Veinte errores idénticos producen **un** incidente con `count: 20`, no veinte
avisos. Eso es la definición operativa de *"sin fatiga de alertas"*: si esto
falla, la plataforma manda spam y la gente deja de mirarla.

---

## 4c. Correlación: que el aviso útil no quede enterrado

```bash
make e2e-f2
```

Comprueba los cuatro comportamientos que definen *"detección sin ruido"*:

```
OK  Una alerta abre un incidente
OK  Veinte alertas idénticas son UN incidente
OK  El síntoma se suprime cuando hay causa aguas arriba
OK  La causa registra a qué está afectando
OK  La investigación enriquece el MISMO incidente
OK  Y no abre uno nuevo

  señales entrantes:    23
  notificaciones:       2
```

**23 señales → 2 notificaciones.** Ese cociente es toda la Fase 2 en un número.

La correlación usa el grafo `depende_de` del registro: cuando falla Postgres,
el incidente de la IDP se marca como síntoma y no avisa, porque el aviso útil
—el de Postgres— ya salió. Sin esto, una caída de Postgres genera un aviso por
cada aplicación que lo usa y el que importa queda sepultado.

Hay un matiz que merece la pena conocer, porque es el fallo **peligroso** de la
correlación: si la causa se resuelve pero el síntoma sigue fallando, el síntoma
se **promueve** y avisa. Sin eso, "esto tiene explicación" se convertiría en
"esto no hace falta mirarlo".

---

## 4d. Los canales de notificación

Por defecto sale todo por consola. Para activar Google Chat, en `platform/.env`:

```bash
ALERTBUS_SINKS=console,json,gchat
ALERTBUS_GCHAT_WEBHOOK=https://chat.googleapis.com/v1/spaces/XXX/messages?key=...&token=...
```

Y para correo:

```bash
ALERTBUS_SINKS=console,json,gchat,email
ALERTBUS_SMTP_HOST=smtp.gmail.com
ALERTBUS_SMTP_USER=tu@correo.com
ALERTBUS_SMTP_PASSWORD=una-contraseña-de-aplicación
ALERTBUS_SMTP_TO=ops@tu-dominio.com
```

Un canal sin credenciales **se omite con un aviso**, no se construye a medias:
un *sink* que falla en cada envío llena el log y da la falsa impresión de que
la plataforma está avisando.

Comprueba qué canales están activos:

```bash
curl -s http://127.0.0.1:8080/stats | python3 -m json.tool
```

**Sobre WhatsApp**: úsalo solo para crítico fuera de horario. Cuesta por
mensaje, exige una plantilla preaprobada —clasifícala como *utility*, no
*marketing*—, y desde el 1 de octubre de 2026 ni las respuestas dentro de la
ventana de 24 h son gratuitas. Por eso ese canal manda solo el titular y un
enlace: el informe completo va por correo y por Chat, que no cuestan por
mensaje.

---

## 4e. El canario: que el silencio no parezca salud

```bash
docker compose -f platform/compose.yaml logs canary --tail 20
```

Dos tipos de sonda:

- **HTTP** (declaradas en `platform/registry/probes.yaml`): comprueban que un
  endpoint responde y en cuánto tiempo. Sirven para apps sin instrumentar.
- **De silencio** (derivadas solas del registro): comprueban que una app
  **sigue emitiendo**. Cierran el punto ciego que ningún sistema basado solo en
  lo que llega puede ver.

Pruébalo tirando algo:

```bash
docker compose -f platform/compose.yaml stop victoriametrics
# espera dos rondas del canario (CANARY_INTERVALO_S, 300 s por defecto)
make incidents
docker compose -f platform/compose.yaml start victoriametrics
```

Hacen falta **dos** fallos consecutivos antes de que alerte. Cambia tiempo de
detección por precisión, que en un canario es la moneda correcta: uno que grita
por cada microcorte de red acaba silenciado, y un canario silenciado no vale
nada el día que grite por algo real.

---

## 5. Instrumentar una app tuya de verdad

Es la prueba que más te va a decir. Coge una app pequeña con FastAPI:

```python
# Al principio de tu main.py, antes de cualquier import que loguee
import argus
argus.init(
    "mi-api",                  # el COMPONENTE
    namespace="mi-aplicacion", # la APLICACIÓN
    role="api",
    version="0.1.0",
)

app.add_middleware(argus.ASGIMiddleware, propagate_mode="trusted")
```

Y en el entorno:

```bash
export ARGUS_ENDPOINT=http://localhost:4317
export ARGUS_ENVIRONMENT=mac-dev
```

Para que resuelva `argus` mientras no haya índice privado (B-10):

```bash
uv pip install -e /ruta/a/app_monitoring_explainability/libs/argus-semconv
uv pip install -e /ruta/a/app_monitoring_explainability/libs/argus-sdk
```

Lanza tu app, hazle unas peticiones, y consulta:

```bash
make query SQL="
  SELECT SpanName, count() FROM otel.otel_traces
  WHERE ResourceAttributes['service.namespace'] = 'mi-aplicacion'
  GROUP BY SpanName FORMAT PrettyCompact"
```

**Si tu app ya tiene OpenTelemetry**, no hace falta `argus.init()` para empezar
a ver datos. Solo tres variables:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
export OTEL_SERVICE_NAME=mi-api
export OTEL_RESOURCE_ATTRIBUTES=service.namespace=mi-aplicacion,argus.component.role=api
```

Cero líneas de código. Es el camino de migración más barato (`F7-02`).

---

## 6. Probar las garantías, no solo el camino feliz

Lo que más confianza da es ver que **falla bien**.

### El Collector caído no afecta a tu app

```bash
make down
uv run python -c "
import time, warnings; warnings.simplefilter('ignore')
import argus
h = argus.init('prueba', endpoint='http://127.0.0.1:1')
t = time.perf_counter()
for i in range(10_000):
    with argus.step('trabajo') as s: s.set(i=i)
print(f'10.000 spans con el Collector caido: {(time.perf_counter()-t)*1000:.0f} ms')
h.force_flush(timeout_millis=200)
print('La aplicacion no se entero.')
"
make up
```

**Qué demuestra**. Regla 1 del contrato: si el Collector cae, la app pierde
telemetría, **nunca latencia ni memoria**. La cola es acotada y descarta.

### La PII no llega al almacén

Ya lo cubre la prueba end-to-end, pero puedes verlo directamente:

```bash
uv run python -c "
from argus_semconv import mask
for v in ['ana@test.com', '4111 1111 1111 1111', '+34 612 345 678',
          'smoke-1789238954', 'v20260912']:
    print(f'{v:25} -> {mask(v)}')
"
```

Los tres primeros se enmascaran; los dos últimos **no**. Eso es D-018: el patrón
de teléfono capturaba cualquier tirada de diez dígitos y estaba destrozando
`service.namespace`.

### Llamar a `init()` dos veces no rompe nada

```bash
uv run python -m pytest libs/argus-sdk/tests/test_contract.py -v 2>&1 | grep -E "PASSED|FAILED"
```

Son las cinco reglas del contrato convertidas en tests: no tumbar la app,
idempotencia, no-op sin configurar, configuración por entorno, y superficie
pública fijada.

---

## 7. Qué NO se puede probar todavía

Honestamente, porque saber dónde está el borde importa:

| No probado | Por qué | Código |
|---|---|---|
| El presupuesto de ~2 s del camino caliente | El `alert-bus` no existe aún | `F2-09` |
| La cola persistente con un corte real | Falta parar el central y medir | `F1-09` |
| Multi-máquina | Todo corre en una sola | `F1-10` |
| Langfuse | Perfil configurado, nunca arrancado | `F1-12` |
| Temporal y los agentes | Fase 3 en adelante | `F3-01` |
| Migrar el plano central a otra máquina | El runbook está pendiente | `F1-08` |

El estado completo está en [roadmap.md](../roadmap.md).

---

## Limpiar

```bash
make down    # para, conserva los datos
make clean   # para y BORRA los volúmenes
```
