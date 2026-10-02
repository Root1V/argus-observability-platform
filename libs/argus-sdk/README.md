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
| `ARGUS_SERVICE` | El sub-componente (`service.name`). **Sin él se avisa**: nada se puede atribuir ni correlacionar | `unknown-service` |
| `ARGUS_NAMESPACE` | La aplicación (`service.namespace`). **Sin él se avisa**: la identidad de dos niveles desaparece y entras en el registro sin canales | = servicio |
| `ARGUS_ROLE` | `api`/`worker`/`scheduler`/`cli`/`model-server`/`frontend` | `api` |
| `ARGUS_VERSION` | Versión del componente | — |
| `ARGUS_ENVIRONMENT` | El **nivel** de despliegue: `development` \| `staging` \| `test` \| `production`. Vocabulario del estándar, no nuestro. La máquina es `host.name` y la pone el agente | **ausente** — no se inventa, y se avisa |
| `ARGUS_ENDPOINT` | Collector **agente local** | `http://localhost:4317` |
| `ARGUS_PROTOCOL` | `grpc` \| `http/protobuf` | según lo instalado |
| `ARGUS_TRUST_INBOUND` | Si adoptar un `traceparent` que llega de fuera: `never` \| `trusted` \| `always` | `never` |
| `ARGUS_PROPAGATE` | Nombre **viejo** de la anterior. Sigue funcionando | — |
| `ARGUS_TRUSTED_CIDRS` | CIDRs de confianza, separados por coma | — |
| `ARGUS_CAPTURE_CONTENT` | Capturar prompts y respuestas | `false` |
| `ARGUS_SLO_MS` | Umbral de latencia del componente | `0` (sin umbral) |
| `ARGUS_DISABLED` | Apagar toda la telemetría | `false` |

Las estándar de OpenTelemetry (`OTEL_SERVICE_NAME`,
`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_RESOURCE_ATTRIBUTES`) también se respetan,
para que una app que ya tiene OTel migre sin tocar código.



## Los avisos de arranque

`argus.init()` comprueba cuatro cosas al arrancar y **avisa sin impedir el
arranque**. Las cuatro son fallos que no dan ningún error: la aplicación
funciona, la telemetría se exporta, y los datos llegan sin servir.

| aviso | qué pasa si lo ignoras |
|---|---|
| **sin identidad** (`ARGUS_SERVICE` / `ARGUS_NAMESPACE`) | nada se puede atribuir, correlacionar ni enrutar; en el registro entras sin canales, o sea tus incidentes no llegan a nadie |
| **`localhost` desde un contenedor** | no llega ni un span, y el exportador reintenta de fondo sin error |
| **protocolo contra el puerto del otro transporte** | `UNAVAILABLE` en bucle, para las tres señales |
| **entorno fuera del vocabulario** o ausente | no se puede separar producción de desarrollo |

Los cuatro salieron de equipos que nos adoptaron, no de nuestras pruebas. Los
dos primeros los reportó un equipo que perdió una tarde con ellos; el de la
identidad, uno que descubrió 450 registros suyos que ni ellos podían
atribuirse.

**Nada se inventa para que el aviso desaparezca.** El entorno ausente queda
ausente: adivinarlo es lo que hizo que este SDK sellara `local` durante semanas
—un valor que no está en el estándar— sin que nadie lo viera.

## Propagar el contexto a otro hilo

El contexto de OpenTelemetry vive en un `contextvars.ContextVar`. `asyncio` lo
copia al crear una tarea; **`ThreadPoolExecutor` no lo copia** al hilo
trabajador. Lo que se pierde ahí no suele ser la traza entera, sino los
registros que emite la librería que corre dentro: salen con `trace_id = none`
mientras los de al lado salen bien.

```python
from argus.propagate import Executor as ThreadPoolExecutor
```

`submit` y `map` propagan solos, así que no hay que tocar ninguna llamada. Es
un reemplazo y no un ayudante a propósito: un ayudante hay que recordarlo en
cada envío, y el olvido no da error.

### Si el SDK es una dependencia **opcional** de tu aplicación

Que lo sea es lo correcto —la aplicación tiene que arrancar sin él— y entonces
el import de arriba la rompe al cargar, en una ruta que no toca telemetría.
Protégelo, **con el caso base primero**:

```python
from concurrent.futures import ThreadPoolExecutor   # sin el extra, este

try:
    from argus.propagate import Executor as ThreadPoolExecutor
except ImportError:
    pass
```

El orden no es cosmético: al revés, `mypy` lo rechaza porque asigna un
supertipo sobre un subtipo. Declarar el estándar y estrecharlo después es lo
que comprueba.

> Esta receta es de Prosodia, que la encontró adoptándolo: tienen el SDK en un
> extra `web` y la misma función la corre también una CLI que no lo instala.

### Y para un subproceso

Ahí no hay contexto que copiar: hay que pasar la cadena.

```python
subprocess.run(cmd, env=argus.propagate.inject_env(dict(os.environ)))
```

Y en el hijo, si es código tuyo:

```python
with argus.propagate.extract_env(name="mi.etapa"):
    ...
```

### Dos nombres parecidos que no tienen nada que ver

| | qué es |
|---|---|
| `argus.propagate` | el **módulo**: lleva el contexto entre hilos, procesos, colas y cabeceras |
| `ARGUS_TRUST_INBOUND` (antes `ARGUS_PROPAGATE`) | si **confiar** en un `traceparent` que llega de fuera por HTTP |

Les pusimos el mismo nombre y no debimos. El segundo es una decisión de
seguridad sobre lo que entra; el primero es cómo sale lo tuyo.

## Por qué `localhost` y no el plano central

Las aplicaciones exportan **siempre** al Collector agente de su propia máquina.
Nunca conocen la dirección del plano central. Eso da tres propiedades:

- Mover el plano central de una máquina a otra no toca ni una aplicación.
- Si el central está suspendido, las apps no se enteran ni se ralentizan: el
  agente local escribe a disco y envía cuando vuelve la conexión.
- Añadir una máquina es desplegar un agente, no reconfigurar apps.
