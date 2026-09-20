# argus-obs-sdk

SDK de observabilidad de Argus para **aplicaciones**.

```bash
pip install argus-obs-sdk
```
```python
import argus
```

> **El nombre de instalación y el de importación no coinciden, y conviene
> decirlo antes que nada.** Se instala `argus-obs-sdk` y se importa `argus`.
>
> El prefijo `-obs-` es deliberado: elegimos nombres que **no existían** en
> PyPI para que un fallo del índice no acabara instalando el paquete de un
> desconocido con el nombre que esperábamos. El módulo conservó el nombre
> corto porque es lo que se escribe cien veces.
>
> Va arriba porque es lo primero con lo que tropieza quien lo adopta.

```python
import argus
argus.init()
```

Eso configura resource, trazas, métricas, logs, propagadores y todas las
auto-instrumentaciones disponibles, leyendo la configuración del entorno.

Para servicios ASGI, una línea más:

```python
app.add_middleware(argus.middleware(app).__class__)   # o ASGIMiddleware directo
```

## Si escribes una librería, no uses este paquete

Usa [`argus-semconv`](../argus-semconv), que depende solo de
`opentelemetry-api`. Una librería que depende del SDK impone en silencio su
versión del SDK y sus opiniones sobre exportadores a todo el que dependa de
ella.

## El contrato

Verificado por tests en `tests/test_contract.py`, no por buenas intenciones:

1. **Nunca tumba la aplicación.** Si el Collector está caído, la app pierde
   telemetría, nunca latencia ni memoria.
2. **Idempotente.** `init()` dos veces es no-op la segunda.
3. **No-op sin configurar.** Los decoradores funcionan con coste cero.
4. **Cero configuración en el caso normal.** Todo viene del entorno.
5. **Superficie pública mínima**, fijada por test.

## Variables de entorno

El contrato es de entorno, no de API de Python: un componente Go y uno Python
se configuran copiando el mismo bloque.

| Variable | Significado | Por defecto |
|---|---|---|
| `ARGUS_SERVICE` | El sub-componente (`service.name`) | `unknown-service` |
| `ARGUS_NAMESPACE` | La aplicación (`service.namespace`) | = servicio |
| `ARGUS_ROLE` | `api`/`worker`/`scheduler`/`cli`/`model-server`/`frontend` | `api` |
| `ARGUS_VERSION` | Versión del componente | — |
| `ARGUS_ENVIRONMENT` | `mac-dev`, `imac`, `server-1`, `ci` | `local` |
| `ARGUS_ENDPOINT` | Collector **agente local** | `http://localhost:4317` |
| `ARGUS_PROTOCOL` | `grpc` \| `http/protobuf` | `grpc` |
| `ARGUS_PROPAGATE` | `never` \| `trusted` \| `always` | `never` |
| `ARGUS_TRUSTED_CIDRS` | CIDRs de confianza, separados por coma | — |
| `ARGUS_CAPTURE_CONTENT` | Capturar prompts y respuestas | `false` |
| `ARGUS_SLO_MS` | Umbral de latencia del componente | `0` (sin umbral) |
| `ARGUS_DISABLED` | Apagar toda la telemetría | `false` |

Las estándar de OpenTelemetry (`OTEL_SERVICE_NAME`,
`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_RESOURCE_ATTRIBUTES`) también se respetan,
para que una app que ya tiene OTel migre sin tocar código.

## Por qué `localhost` y no el plano central

Las aplicaciones exportan **siempre** al Collector agente de su propia máquina.
Nunca conocen la dirección del plano central. Eso da tres propiedades:

- Mover el plano central de una máquina a otra no toca ni una aplicación.
- Si el central está suspendido, las apps no se enteran ni se ralentizan: el
  agente local escribe a disco y envía cuando vuelve la conexión.
- Añadir una máquina es desplegar un agente, no reconfigurar apps.
