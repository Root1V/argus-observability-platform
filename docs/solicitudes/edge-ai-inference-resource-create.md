# Solicitud a Prometheus (edge-ai-inference)

**Para**: equipo de `edge-ai-inference`
**De**: plataforma Argus
**Fecha**: 13 de septiembre de 2026
**Impacto**: una línea · **Urgencia**: bloquea el piloto de observabilidad
**Parche listo**: [`edge-ai-inference-resource-create.patch`](edge-ai-inference-resource-create.patch)

---

## Resumen

`configure_tracing()` construye el `Resource` con el constructor directo, que
**descarta silenciosamente la variable estándar `OTEL_RESOURCE_ATTRIBUTES`**.
Como consecuencia, la telemetría llega pero sin la identidad de la aplicación,
y ningún servicio puede agruparse con los demás de su plataforma.

No da ningún error. Los atributos simplemente no aparecen.

---

## Dónde

`telemetry/src/prometheus_telemetry/tracing.py`, línea ~74:

```python
resource = Resource(attributes=attrs)      # descarta OTEL_RESOURCE_ATTRIBUTES
```

## El cambio

```python
resource = Resource.create(attrs)          # fusiona la variable y los detectores
```

`Resource.create()` fusiona, por este orden: los detectores del SDK (proceso,
SO, host), la variable `OTEL_RESOURCE_ATTRIBUTES`, y los atributos que se le
pasan — que siguen ganando. **El comportamiento actual se conserva**: lo que
`configure_tracing(resource_attributes=...)` recibe sigue teniendo prioridad.

---

## Por qué importa

`OTEL_RESOURCE_ATTRIBUTES` es la vía estándar por la que el despliegue inyecta
`service.namespace`, `service.version` y `deployment.environment.name` **sin
tocar código**. Sin ella:

- Los servicios de Prometheus aparecen sueltos, sin quedar agrupados bajo su
  plataforma. No se puede responder *«¿cómo va Prometheus?»*, solo *«¿cómo va
  auth-service?»*.
- El enrutamiento de avisos no encuentra al equipo responsable.
- La correlación de incidentes no puede usar el grafo de dependencias, así que
  un fallo de Redis genera un aviso por cada servicio afectado en vez de uno.

## Verificado en vivo

Con `auth-service` apuntando a nuestro Collector, **sin ningún otro cambio**:

| | `service.namespace` | componente | rol | entorno |
|---|---|---|---|---|
| Antes | `unregistered` | auth-service | — | — |
| Después | `edge-ai-inference` | auth-service | api | mac-dev |

Y con la identidad puesta, el resto de la cadena funciona sola: los spans de
`token.issuance` con `StatusCode=Error` llegaron al camino de detección y
abrieron un incidente correctamente agrupado.

---

## Tests incluidos en el parche

Dos, en el estilo de `telemetry/tests/test_tracing.py`:

- `test_configure_tracing_honours_otel_resource_attributes` — la variable llega
  al `Resource`.
- `test_configure_tracing_explicit_attributes_win_over_env` — los atributos
  explícitos siguen ganando, que es lo que garantiza que no hay regresión.

Su suite completa pasa con el cambio: **32 tests, cobertura 97 %**.

---

## Cómo aplicarlo

```bash
cd edge-ai-inference
git checkout -b fix/otel-resource-create
git apply /ruta/a/edge-ai-inference-resource-create.patch
uv run --directory telemetry python -m pytest -q
```

---

## Lo que NO pedimos

Nada más. En concreto:

- **No hace falta tocar `TraceIDMiddleware`** todavía. Sabemos que arranca
  siempre un span raíz e ignora `traceparent` entrante —una decisión deliberada
  de seguridad, documentada en el spec del gateway— y que eso impide que una
  traza cruce servicios. Es una conversación aparte, con más matices, y no
  bloquea el piloto.
- **No hace falta adoptar el SDK de Argus.** Con esta línea, `auth-service`
  funciona con su propio paquete de telemetría tal cual está.

---

## Lo que hacemos nosotros

Todo lo demás es configuración por nuestro lado:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_SERVICE_NAME=auth-service
OTEL_RESOURCE_ATTRIBUTES=service.namespace=edge-ai-inference,argus.component.role=api,deployment.environment.name=mac-dev
```
