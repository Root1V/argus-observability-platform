# diagnose — L3, el diagnóstico de un incidente

Coge un incidente abierto sin causa, recolecta todo lo que se sabe de él, y
devuelve al alert-bus una **causa probable, una confianza y una evidencia**. El
aviso que ya salió se edita; no se manda otro.

Existe por una cifra: hasta hoy, **561 de 561 notificaciones** le han dicho a
una persona «investigando…» y **0 de 571 incidentes** han llegado a tener
causa. El hueco estaba recortado en el esquema —`root_cause`, `confidence`,
`evidence`, `similar_incidents` llevan semanas ahí— y no había nada que lo
rellenara.

## No es un agente, y es a propósito

`D-013`: el diagnóstico de un disparo es **una llamada con salida estructurada
sobre contexto pre-recolectado por consultas fijas**. Sin bucle de
herramientas, sin orquestación. Meterle un agente es pagar orquestación por
algo que es un prompt.

De ahí sale la división del módulo, que es la que importa entender:

| | qué es | se equivoca cuando |
|---|---|---|
| `expediente.py` · `almacen.py` · `recoleccion.py` | los **hechos**: siete consultas fijas | falta un dato |
| `juicio.py` | la **opinión**: reglas hoy, un modelo detrás de la misma interfaz | estaba el dato y se leyó mal |
| `bucle.py` | lo que los une | no llega a nadie |

Son dos arreglos distintos y por eso son dos módulos. Si fuera una función que
consulta y concluye, los dos fallos tendrían la misma pinta.

## Las siete consultas

1. **árbol** — el árbol de spans de una traza del incidente. Donde se ve que el
   `502` que avisa lleva dentro un `400` contra otro sitio.
2. **contraparte** — quién atendió esa llamada saliente. El puente.
3. **historia** — ¿es nuevo, o lleva tres semanas pasando?
4. **alcance** — ¿fallaron 12 de 12, o 12 de 4.000?
5. **concurrentes** — qué más se rompió en la misma ventana.
6. **topología** — de qué depende y quién depende de él, según el registro.
7. **recurrencia** — en cuántos de los 30 días anteriores hubo errores de esa
   clase. Contado en las **trazas**, porque el almacén de incidentes guardaba
   una fila por huella y no podía saber si algo era nuevo (D-133).

### El puente, y por qué hace falta

La traza debería cruzar el servicio sola. **En este stack no lo hace**: de
132.000 trazas de dos días, **3** tienen spans de más de un servicio, y los
2.369 spans `Server` de `auth-service` son **todos raíz** — nunca adoptan el
`traceparent` entrante. El `ASGIMiddleware` llega con `propagate_mode="never"`
por defecto, que es el correcto para lo expuesto a internet y el equivocado
para dos servicios que se hablan por loopback.

Mientras eso siga así, el árbol se corta en el borde del servicio y el salto
hay que darlo a mano: misma ruta, lado servidor, ventana de ±3 s.

Y **que no encuentre nada también es un hallazgo**: si nadie registró haber
atendido la llamada que falló, el fallo ocurrió antes de llegar a la aplicación
destino.

## El juez de reglas es el control, no el relleno

`F3-08` pide un banco de incidentes con causa conocida porque es «la única
forma honesta de medir». Falta la mitad de la frase: medir **contra qué**. Un
modelo que acierta el 70% no dice nada si cien líneas de reglas aciertan el
65%.

Así que las reglas van primero, se pasan sobre el mismo banco que el modelo, y
`Veredicto.origen` es la columna por la que se agrupa al restar.

Lo que las reglas **no** hacen es inventar: cuando ninguna dispara,
`root_cause` se queda vacío y el aviso sigue diciendo «investigando…» —con la
evidencia ya adjunta, que es lo que lo hace utilizable. Un diagnóstico
equivocado con aire de certeza es peor que no tener ninguno, porque manda a
alguien a mirar donde no es.

## El banco (`F3-08`)

`banco/` guarda incidentes **reales** con su expediente **congelado** —las
trazas caducan a los 30 días— y la causa establecida **fuera de L3**.

```bash
uv run python -m diagnose.banco evaluar
uv run python -m diagnose.banco capturar <huella> --caso <id>
```

El informe separa los casos que se usaron para escribir las reglas de los que
no. **La cifra que vale es la de los no vistos**, y un caso que motiva un
cambio de regla pasa a diseño: ya no mide nada que el juez no haya visto. La
primera medida honesta fue cero aciertos en lo no visto (D-134).

## Arrancarlo

```bash
DIAGNOSE_CLICKHOUSE_PASSWORD=… uv run python -m diagnose
```

| variable | por defecto | |
|---|---|---|
| `DIAGNOSE_ALERTBUS_URL` | `http://127.0.0.1:8080` | a quién se preguntan los incidentes |
| `DIAGNOSE_CLICKHOUSE_URL` | `http://127.0.0.1:8123` | de dónde salen las trazas |
| `DIAGNOSE_INTERVALO_S` | `10` | cada cuánto se busca trabajo |
| `DIAGNOSE_MAX_POR_CICLO` | `3` | tope de diagnósticos por ciclo |
| `DIAGNOSE_REINTENTAR_TRAS_S` | `600` | espera antes de reintentar uno sin causa |
| `DIAGNOSE_JUEZ` | `reglas` | qué juez se usa |

No necesita Temporal. El almacén de incidentes **ya es la cola** —un incidente
abierto sin causa *es* trabajo pendiente, y sobrevive a un reinicio— así que
esto es reanudable sin durabilidad de workflow. Temporal hará falta para `L4`,
que es el que espera aprobación humana.
