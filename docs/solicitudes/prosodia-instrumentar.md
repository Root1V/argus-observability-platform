# Solicitud al equipo de Prosodia

**Para**: equipo de **Prosodia** — *AI Video Dubbing Pipeline*
(el paquete y la CLI conservan el nombre técnico `video-translator`)
**De**: Argus — plataforma de observabilidad
**Fecha**: 17 de septiembre de 2026
**Impacto**: 2 ficheros, 6 líneas · **Urgencia**: ninguna, decidís vosotros
**Parche listo**: [`prosodia-instrumentar.patch`](prosodia-instrumentar.patch)

> **Este documento se ha trasladado al canal compartido**
> `~/Documents/Victor/prosodia_argus/canal-prosodia-argus.md`, en las entradas
> `A-01` a `A-06`. Se conserva aquí como referencia; **las respuestas van al
> canal**, no a este fichero.
>
> Copia en el repositorio: [`canal-prosodia-argus.md`](canal-prosodia-argus.md).

---

## Resumen

Os pedimos **seis líneas en dos ficheros** para que Prosodia emita telemetría a
Argus. No cambia comportamiento: sin las variables de entorno configuradas, todo
queda en no-op.

Lo pedimos porque Prosodia es **la única aplicación del portafolio con una
cola**, y eso la convierte en la única que puede validar la parte del diseño que
hoy no está validada con tráfico real.

---

## Por qué vosotros

Argus lleva un piloto con Prometheus desde el 13 de septiembre. Va bien: 689 de
689 spans contrastados contra su propio contador, una tormenta de 10.446 señales
colapsada en 45 notificaciones, incidentes reales llegando al móvil.

Pero **sus tres servicios son APIs HTTP**, y hay una cosa que ninguna API HTTP
puede demostrar: que una traza sobreviva al cruzar una cola.

Es la prueba que más nos importa. Si una unidad de trabajo que pasa por una cola
produce **dos** trazas en vez de una, no hay forma de responder «¿por qué tardó
tanto este doblaje?» — tendrías la petición por un lado y el trabajo por otro,
sin nada que los una.

Buscamos en los 41 proyectos del portafolio quién tiene cola. Sois vosotros:

```
video-translator   celery[redis]>=5.4.0
```

Y con la forma exacta que hace falta:

```python
projects.py:324     run_dubbing_project.delay(str(project.id))   # la API encola
run_project.py:63   def run_dubbing_project(self, project_id, …) # el worker consume
```

> El Redis de Prometheus no sirve para esto, por si os lo preguntáis: lo usan
> como caché —`get`, `set`, `expire`— y cero operaciones de cola. Un `get` no
> cruza a otro proceso; hace falta alguien que *recoja* el trabajo al otro lado.

---

## Qué os pedimos

### 1 · El parche (6 líneas, 2 ficheros)

**`web/main.py`** — `init()` antes de los routers, y el middleware:

```python
from __future__ import annotations

import argus

argus.init()

from fastapi import FastAPI
...
app = FastAPI(title="Prosodia Web API")

app.add_middleware(argus.ASGIMiddleware)
```

`init()` va **antes** de importar los routers a propósito: instala el puente de
logging, y lo que se importe antes se queda con el handler viejo.

**`web/tasks/celery_app.py`** — la mitad que de verdad importa:

```python
from __future__ import annotations

import argus

argus.init()

from celery import Celery
```

El worker es un **proceso aparte**: no hereda nada de la API. Sin esta llamada,
encolar produce una traza y procesar produce otra.

En el proceso de la API esto también se ejecuta —el router importa la tarea— y
es inofensivo: `init()` es idempotente y `main.py` ya la llamó antes.

**No hay que escribir propagación a mano.** `init()` detecta Celery entre
vuestras dependencias y activa `CeleryInstrumentor`, que mete el contexto en las
cabeceras de la tarea.

### 2 · La dependencia

```toml
dependencies = [
  "argus-obs-sdk[asgi,celery] @ <pendiente: ver «lo que aún no podemos daros»>",
]
```

### 3 · Las variables, en vuestro `docker-compose.yml`

En **`api`**:

```yaml
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://host.docker.internal:4318
      - OTEL_SERVICE_NAME=prosodia-api
      - OTEL_RESOURCE_ATTRIBUTES=service.namespace=prosodia,argus.component.role=api,service.version=${PROSODIA_VERSION:-dev}
```

En **`worker`**, lo mismo cambiando dos valores:

```yaml
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://host.docker.internal:4318
      - OTEL_SERVICE_NAME=prosodia-worker
      - OTEL_RESOURCE_ATTRIBUTES=service.namespace=prosodia,argus.component.role=worker,service.version=${PROSODIA_VERSION:-dev}
```

**Siempre `localhost` / `host.docker.internal`, nunca la dirección del plano
central.** Vuestras aplicaciones exportan al agente de su propia máquina y ese
agente decide a dónde va. Así, mover el plano central no os afecta.

Sin `OTEL_EXPORTER_OTLP_ENDPOINT` no se exporta nada y el coste es cero. Podéis
aplicar el parche y decidir después cuándo encender.

---

## Qué verificamos, y dónde

Distinguimos las dos cosas porque nos costó un malentendido con el otro equipo.

**Verificado en nuestro stack**: el mecanismo, con una reproducción de vuestra
forma exacta —FastAPI que hace `.delay()`, worker Celery sobre Redis—:

```
TraceId 5a3f479783d2515ab293302f262d0d59
  cola-api     POST /proyectos/p-caliente/ejecutar   (raíz)
  cola-api     apply_async/prueba.trabajo_largo
  cola-worker  run/prueba.trabajo_largo
  cola-worker  doblaje.procesar
```

**Una traza, dos procesos, una cola de Redis.** Los cuatro spans bajo el mismo
`TraceId`, y los tres hijos con padre.

**Verificado contra vuestro repositorio**: que el parche aplica limpio y que
**no os rompe el CI**.

```
git apply --check -p1 prosodia-instrumentar.patch     → OK
ruff check src   (con vuestro pyproject.toml)
   sin parche : 2 avisos  (I001 en main.py:3 y celery_app.py:3)
   con parche : 2 avisos  (los mismos, desplazados a :12 y :16)
```

**Cero hallazgos nuevos.** Lo comprobamos porque meter `argus.init()` entre
imports suele disparar `E402` —«import no está al principio del fichero»— y eso
habría roto vuestro `ruff check src tests` sin que nadie lo viera venir. En
vuestra configuración `E402` no está seleccionada; si algún día la activáis, el
patrón necesita un `# noqa: E402`, igual que vuestro
`import video_translator.web.tasks.run_project  # noqa: F401` de abajo.

**Lo que NO hemos hecho**: ejecutar vuestro código. No tenemos vuestro entorno
—faster-whisper, IndexTTS, Postgres—, así que el parche está verificado en
*forma*, no en *ejecución*. Esa parte os toca a vosotros y es razonable que
desconfiéis hasta verla.

**Y no hemos tocado nada de vuestro repositorio.** Tenéis ocho ficheros del
frontend sin commitear en `main`; generamos el parche copiando dos ficheros a un
directorio temporal. `git status` de `src/` sigue limpio.

---

## Cómo comprobar que funcionó

Una petición que lance un doblaje y luego, en Argus:

```sql
SELECT servicios, count() AS trazas FROM (
  SELECT TraceId, arrayStringConcat(arraySort(groupUniqArray(ServiceName)), ' + ') AS servicios
  FROM otel.otel_traces
  WHERE ResourceAttributes['service.namespace'] = 'prosodia'
    AND Timestamp > now() - INTERVAL 30 MINUTE
  GROUP BY TraceId)
GROUP BY servicios
```

**Lo que se espera**: una fila `prosodia-api + prosodia-worker`. Si salen dos
filas separadas, la propagación no cruzó y queremos saberlo — es exactamente el
fallo que esta solicitud existe para detectar.

**Aviso para esa comprobación**: con poco volumen la traza puede caer en nuestro
muestreo probabilístico (conservamos el 100 % de errores y el 10 % del resto), y
entonces la pregunta se vuelve incontestable por falta de muestra, no porque
falle. Con un doblaje de verdad no pasa —duran minutos y superan el umbral de
«lenta»—, pero si probáis con algo instantáneo, decídnoslo y lo conservamos
entero mientras dure la prueba.

---

## Lo que aún no podemos daros, y es cosa nuestra

**No hay un índice de paquetes.** Nuestro repositorio no tiene remoto, así que
hoy la única forma de instalarlo sería una rueda suelta.

El equipo de Prometheus rechazó exactamente eso, y con razón: *«aceptar un
binario por SHA de un equipo hermano es una decisión de cadena de suministro, no
una comodidad»*. No os vamos a pedir a vosotros lo que ellos declinaron con buen
criterio.

**Está en nuestro backlog como `B-10` y con prioridad alta.** Os avisamos cuando
exista y entonces la dependencia es una línea normal con rango de versiones.

Mientras tanto: **podéis revisar el parche y decirnos si el enfoque os encaja**,
que es lo que de verdad nos sirve ahora. Aplicarlo puede esperar al índice.

---

## Un aviso de compatibilidad

Vuestro `pyproject.toml` declara:

```toml
requires-python = ">=3.10,<3.13"
```

Nuestro SDK exige **`>=3.11`**. Vuestro venv corre 3.11.15, así que **hoy
funciona**; pero declarar la dependencia os estrecharía el rango soportado de
3.10 a 3.11.

Si tenéis a alguien en 3.10, decídnoslo: bajar nuestro mínimo es una
conversación que podemos tener, no una imposición.

---

## Lo que NO os pedimos

- **Cambiar la estructura de las tareas.** `run_dubbing_project` se queda como
  está; nada de partirla ni de renombrarla.
- **Spans manuales dentro del pipeline.** Con el parche ya veréis la frontera
  API→cola→worker, que es lo que necesitamos. Instrumentar las etapas internas
  del doblaje —transcripción, clonado, mezcla— sería útil para vosotros, pero es
  otra conversación y la decidís vosotros.
- **Quitar nada de lo que ya tenéis.** No hay conflicto: hoy no usáis
  OpenTelemetry.
- **Cambiar `--pool=solo`.** Lo hemos leído y entendemos el motivo (fork y GPU).
  La instrumentación funciona igual.
- **Prisa.** Esto no bloquea nada vuestro.

---

## Resumen

| | |
|---|---|
| Qué | 6 líneas en `main.py` y `celery_app.py`, más variables de entorno |
| Por qué vosotros | Única app del portafolio con cola |
| Qué ganáis | La traza completa de un doblaje, de la petición al worker |
| Coste si no configuráis las variables | Cero: no-op |
| Verificado | Mecanismo en nuestro stack; el parche aplica sobre vuestro código |
| Sin verificar | Ejecución en vuestro entorno |
| Bloqueado por | Nuestro índice de paquetes (`B-10`) |
| Qué os pedimos **ahora** | Que reviséis el enfoque. Aplicarlo puede esperar |
