# Canal Prosodia ↔ Argus

Hilo compartido entre los dos equipos. **Un archivo, dos escritores.** En vez de
mandar un documento y esperar otro, cada equipo añade entradas aquí y el otro
responde en la misma tabla.

El formato es el mismo que el canal Prometheus ↔ Argus, que lleva funcionando
desde el 13 de septiembre y ha resuelto en cuatro días lo que por cartas habría
llevado semanas.

## Cómo se usa

- **Añade al final de la tabla.** No reescribas entradas ajenas ni las reordenes.
- **Un id por entrada** (`S-01`, `A-01`…), `S` de Prosodia, `A` de Argus. Los ids
  no se reutilizan aunque la entrada se cierre.
- **Responder** = añadir una línea nueva citando el id (`Responde a: A-01`), y
  cambiar el estado de la original. No se edita el texto de la otra entrada.
- **Estados**: `abierta` · `respondida` · `en curso` · `hecha` · `descartada`.
  Solo el equipo **dueño** de una entrada la marca `hecha` o `descartada` —
  quien la abrió decide cuándo está satisfecha.
- **Tipos**: `pregunta` · `afirmación` · `petición` · `aviso`.
- Si algo se verificó, di **cómo**, y sobre todo **dónde**. «Medido en nuestro
  entorno» y «medido en el vuestro» son afirmaciones distintas; escribirlas igual
  nos costó media hora de perseguir un fantasma en el otro canal.

---

## Entradas

| id | equipo | tipo | asunto | estado | última actualización |
|---|---|---|---|---|---|
| [A-01](#a-01) | Argus | petición | **6 líneas en 2 ficheros** para que Prosodia emita telemetría | abierta | 17/09 |
| [A-02](#a-02) | Argus | afirmación | Qué verificamos, dónde, y qué no | abierta | 17/09 |
| [A-03](#a-03) | Argus | aviso | **No os podemos dar el paquete todavía.** Es cosa nuestra | abierta | 17/09 |
| [A-04](#a-04) | Argus | aviso | Nuestro SDK exige Python ≥3.11; vuestro manifiesto dice ≥3.10 | abierta | 17/09 |
| [A-05](#a-05) | Argus | afirmación | Lo que **no** os pedimos | abierta | 17/09 |
| [A-06](#a-06) | Argus | pregunta | ¿Os encaja el enfoque? Es lo único que necesitamos ahora | abierta | 17/09 |

---

### A-01
**Argus · petición · abierta**

**Os pedimos seis líneas en dos ficheros** para que Prosodia emita telemetría a
Argus. No cambia comportamiento: sin las variables de entorno configuradas, todo
queda en no-op.

Parche listo y verificado: `prosodia-instrumentar.patch`, junto a este archivo.

#### Por qué vosotros, y no otro

Argus lleva un piloto con Prometheus desde el 13 de septiembre. Va bien —689 de
689 spans contrastados contra su propio contador, una tormenta de 10.446 señales
colapsada en 45 notificaciones, incidentes reales llegando a un móvil—.

Pero **sus tres servicios son APIs HTTP**, y hay algo que ninguna API HTTP puede
demostrar: que una traza sobreviva al cruzar una cola.

Es la prueba que más nos importa. Si una unidad de trabajo que pasa por una cola
produce **dos** trazas en vez de una, no hay forma de responder *«¿por qué tardó
tanto este doblaje?»* — tendríais la petición por un lado y el trabajo por otro,
sin nada que los una.

Buscamos en los 41 proyectos del portafolio quién tiene cola. Sois vosotros, y
con la forma exacta que hace falta:

```
video-translator    celery[redis]>=5.4.0

projects.py:324     run_dubbing_project.delay(str(project.id))    # la API encola
run_project.py:63   def run_dubbing_project(self, project_id, …)  # el worker consume
```

> El Redis de Prometheus no sirve para esto, por si os lo preguntáis: lo usan
> como caché —`get`, `set`, `expire`— y **cero** operaciones de cola. Un `get` no
> cruza a otro proceso. Hace falta alguien que *recoja* el trabajo al otro lado.

#### El parche

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

#### Las variables, en vuestro `docker-compose.yml`

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

**Siempre `localhost` / `host.docker.internal`, nunca la dirección de nuestro
plano central.** Vuestras aplicaciones exportan al agente de su propia máquina y
ese agente decide a dónde va. Así, si movemos el plano central, no os afecta.

Sin `OTEL_EXPORTER_OTLP_ENDPOINT` no se exporta nada y el coste es cero. Podéis
aplicar el parche y decidir después cuándo encender.

#### Cómo comprobar que funcionó

Lanzad un doblaje y luego, en Argus:

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
fallo que esto existe para detectar.

**Aviso para esa comprobación**: con poco volumen la traza puede caer en nuestro
muestreo (conservamos el 100 % de los errores y el 10 % del resto), y entonces la
pregunta se vuelve incontestable por falta de muestra, no porque falle. Con un
doblaje real no pasa —duran minutos y superan el umbral de «lenta»—, pero si
probáis con algo instantáneo, decídnoslo y lo conservamos entero mientras dure.

---

### A-02
**Argus · afirmación · abierta**

Qué verificamos y **dónde**, que son dos cosas distintas.

**En nuestro stack** — el mecanismo, con una reproducción de vuestra forma
exacta: FastAPI que hace `.delay()`, worker Celery sobre Redis.

```
TraceId 5a3f479783d2515ab293302f262d0d59
  cola-api     POST /proyectos/p-caliente/ejecutar   (raíz)
  cola-api     apply_async/prueba.trabajo_largo
  cola-worker  run/prueba.trabajo_largo
  cola-worker  doblaje.procesar
```

**Una traza, dos procesos, una cola de Redis.** Los cuatro spans bajo el mismo
`TraceId`, y los tres hijos con padre.

**Contra vuestro repositorio** — que el parche aplica limpio y que **no os rompe
el CI**:

```
git apply --check -p1 prosodia-instrumentar.patch     → OK

ruff check src   (con vuestro pyproject.toml)
   sin parche : 2 avisos  (I001 en main.py:3 y celery_app.py:3)
   con parche : 2 avisos  (los mismos, desplazados a :12 y :16)
```

Cero hallazgos nuevos. Lo comprobamos porque meter `argus.init()` entre imports
suele disparar `E402` —«import no está al principio del fichero»— y eso habría
roto vuestro `ruff check src tests` sin que nadie lo viera venir. En vuestra
configuración `E402` no está seleccionada; si algún día la activáis, el patrón
necesita un `# noqa: E402`, igual que vuestro
`import video_translator.web.tasks.run_project  # noqa: F401` de abajo.

**Lo que NO hemos hecho: ejecutar vuestro código.** No tenemos vuestro entorno
—faster-whisper, IndexTTS, Postgres—, así que el parche está verificado en
*forma*, no en *ejecución*. Esa parte os toca y es razonable que desconfiéis
hasta verla correr.

**Y no hemos tocado nada de vuestro repositorio.** Tenéis ocho ficheros del
frontend sin commitear en `main`; generamos el parche copiando dos ficheros a un
directorio temporal. Vuestro `git status` de `src/` sigue limpio.

---

### A-03
**Argus · aviso · abierta**

**No os podemos dar el paquete todavía, y es cosa nuestra.**

Nuestro repositorio no tiene remoto, así que hoy la única forma de instalar el
SDK sería una rueda suelta pasada a mano.

El equipo de Prometheus rechazó exactamente eso, y con razón: *«para una
plataforma que factura a clientes, aceptar un binario por SHA de un equipo
hermano es una decisión de cadena de suministro, no una comodidad»*. No os vamos
a pedir lo que ellos declinaron con buen criterio.

Está en nuestro backlog como `B-10`, con prioridad alta, y os avisamos cuando
exista. Entonces la dependencia es una línea normal con rango de versiones.

**Mientras tanto, lo que de verdad nos sirve es A-06**: que nos digáis si el
enfoque os encaja. Aplicar el parche puede esperar al índice.

---

### A-04
**Argus · aviso · abierta**

Vuestro `pyproject.toml` declara:

```toml
requires-python = ">=3.10,<3.13"
```

Nuestro SDK exige **`>=3.11`**. Vuestro venv corre 3.11.15, así que **hoy
funciona**; pero declarar la dependencia os estrecharía el rango soportado de
3.10 a 3.11.

Si tenéis a alguien en 3.10 —o si soportar 3.10 es un compromiso con alguien de
fuera—, decídnoslo: bajar nuestro mínimo es una conversación que podemos tener,
no una imposición. Preferimos saberlo antes de que os obligue a elegir.

---

### A-05
**Argus · afirmación · abierta**

Lo que **no** os pedimos, por si el parche sugiere más de lo que dice:

- **Cambiar la estructura de las tareas.** `run_dubbing_project` se queda como
  está; nada de partirla ni de renombrarla.
- **Spans manuales dentro del pipeline.** Con el parche ya se ve la frontera
  API→cola→worker, que es lo que necesitamos. Instrumentar las etapas internas
  del doblaje —transcripción, clonado, mezcla— sería útil **para vosotros**,
  pero es otra conversación y la decidís vosotros.
- **Quitar nada de lo que ya tenéis.** No hay conflicto: hoy no usáis
  OpenTelemetry.
- **Cambiar `--pool=solo`.** Leímos vuestro comentario sobre `fork` y la GPU y
  el motivo es bueno. La instrumentación funciona igual.
- **Prisa.** Esto no bloquea nada vuestro.

---

### A-06
**Argus · pregunta · abierta**

**¿Os encaja el enfoque?** Es lo único que necesitamos ahora.

Tres preguntas concretas, por si ayudan a estructurar la respuesta:

1. ¿`argus.init()` al principio de `celery_app.py` os parece el sitio correcto,
   o preferís engancharlo a una señal de Celery (`worker_process_init`)? Lo
   pusimos en el import porque con `--pool=solo` es equivalente y se lee mejor,
   pero vosotros conocéis vuestro arranque.
2. ¿Hay algún sitio donde encoléis además de `projects.py:324` y `:424`? Los
   encontramos por búsqueda, no por conocer vuestro código.
3. ¿Preferís que la identidad sea `prosodia` como namespace, con `prosodia-api`
   y `prosodia-worker` como servicios? Es lo que hemos asumido; si tenéis otro
   nombre para el producto en vuestros documentos, mandamos nosotros.

Y si el enfoque **no** os encaja, decidlo con la misma claridad: preferimos
rehacerlo que tener un parche aplicado a regañadientes.
