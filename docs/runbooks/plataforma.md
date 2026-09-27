# La plataforma no está recogiendo lo que le mandan

Este runbook cubre el modo de fallo más engañoso que tenemos: **no se cae nada,
ninguna aplicación da errores, y los paneles se quedan tranquilamente vacíos.**
El silencio parece salud.

Ocurrió de verdad entre el 17 y el 19 de septiembre de 2026 (`D-079`): el
Collector agente descartó el 100% de lo que le mandaron durante dos días.

---

## `ArgusCollectorDescartaTelemetria` {#collector-descarta}

El Collector recibe, acepta, y **no puede entregar**. Las aplicaciones no se
enteran: exportan con normalidad y reciben 200 del agente local.

### 1. Mira el motivo exacto antes de tocar nada

```bash
docker logs --since 10m argus-agent-agent-1 2>&1 | grep -oE 'HTTP Status Code [0-9]+' | sort | uniq -c
```

| Código | Causa casi segura |
|---|---|
| **401** | El token del agente no coincide con el del gateway → sección 2 |
| 404 | La ruta del exportador está mal (`/v1/traces` duplicado o ausente) |
| 413 | Lote demasiado grande: baja `send_batch_max_size` |
| 429 / 503 | El gateway va saturado. Mira su memoria antes que su configuración |
| Nada, error de red | El destino no resuelve o no escucha |

### 2. Si es 401: el token se desincronizó

Compara lo que tiene cada uno. **Nunca imprimas el token entero.**

```bash
docker inspect argus-agent-agent-1 --format '{{range .Config.Env}}{{println .}}{{end}}' | grep TOKEN | cut -c1-30
docker inspect argus-collector-1   --format '{{range .Config.Env}}{{println .}}{{end}}' | grep TOKEN | cut -c1-30
```

Si difieren, la fuente de verdad es siempre `platform/.env`. El arreglo es
reiniciar el agente, que **regenera `platform/.env.agent` desde `.env`**:

```bash
make agent
```

> `platform/.env.agent` es un fichero **derivado**. Nunca lo edites a mano: la
> próxima ejecución de `make agent` lo sobrescribe. Si te ves editándolo, lo que
> quieres cambiar está en `platform/.env`.

### 3. Comprueba que vuelve a fluir

No te fíes de que la alerta se apague; míralo en la fuente:

```bash
curl -s http://127.0.0.1:8889/metrics | grep -E 'otelcol_exporter_(sent|send_failed)_spans_total'
```

`send_failed` tiene que quedarse quieto y `sent` tiene que subir. Luego confirma
que llega al almacén:

```bash
docker exec argus-clickhouse-1 clickhouse-client -q \
  "SELECT count() FROM otel.otel_traces WHERE Timestamp > now() - INTERVAL 5 MINUTE"
```

### 4. Qué se perdió

Lo que descartó **no vuelve**. La cola persistente cubre el caso de que el
destino esté caído o lento, no el de que rechace el envío: un 401 no es
reintentable y se tira al momento.

Deja constancia del hueco: un agente de investigación que mire ese rango leerá
el silencio como «no pasaba nada», que es justo lo contrario de lo que pasó.

---

## `ArgusColaDelAgenteCreciendo`

El destino lleva rato inalcanzable o lento y la cola en disco se está llenando.
**Mientras quepa no se pierde nada** — es exactamente para lo que existe, y una
tapa de portátil cerrada un fin de semana es el caso normal.

```bash
curl -s http://127.0.0.1:8889/metrics | grep -E 'otelcol_exporter_queue_(size|capacity)'
```

Si el gateway está levantado y la cola sigue subiendo, el problema es de
capacidad, no de conectividad: mira `ArgusCollectorDescartaTelemetria` primero.

---

## `ArgusAgenteSinAutoMetricas` / `ArgusGatewaySinAutoMetricas`

**Son las alertas que vigilan a las otras dos.** Sin las métricas internas del
Collector, `ArgusCollectorDescartaTelemetria` no puede disparar nunca, y volvemos
al punto ciego exacto de `D-079`: dos días perdiendo el 100% de la telemetría sin
que nada avise.

Y la del **agente** es la única que puede cazar un agente roto, porque el agente
manda sus propias métricas por el mismo exportador que se le rompe. Su silencio
*es* la señal.

### Antes de investigar: ¿llevaba la máquina despierta?

**No debería dispararte por eso**, y si lo hace es un fallo de la regla, no del
Collector. Las dos llevan una puerta:

```promql
absent_over_time(otelcol_exporter_sent_spans_total{argus_collector_tier="agent"}[10m])
and on() (count_over_time(argus:observador_despierto[10m]) > 30)
```

`argus:observador_despierto` es una regla de grabación que vale **1 siempre**.
Cuando el portátil duerme, vmalert tampoco evalúa, así que no deja muestras: al
despertar, `count_over_time(...)` es pequeño y la puerta está cerrada. Se abre
sola a los ~7 minutos y medio de estar despierta, que es de sobra para que un
Collector sano haya reportado.

Existe porque `for:` **no** protege de esto: se evalúa contra el reloj de pared,
así que un sueño de seis horas supera cualquier `for` en la primera evaluación
tras despertar. Estas dos reglas llevaban cinco días disparando por eso (`D-087`).

Si quieres confirmar que la puerta funciona:

```bash
docker exec argus-vmalert-1 wget -qO- 'http://127.0.0.1:8880/api/v1/query?query=count_over_time(argus:observador_despierto[10m])'
```

### Si la puerta estaba abierta, entonces sí hay algo

Dos causas, y las dos son graves:

1. El receptor `prometheus/interno` no está en la tubería de métricas de
   `platform/collector/agent.yaml` o `gateway.yaml`.
2. El Collector no está corriendo.

```bash
grep -c "prometheus/interno" platform/collector/*.yaml   # 2 en cada fichero: receptor y tubería
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E "argus-(agent-)?agent|argus-collector"
```

Trátalas como `page` aunque todo lo demás parezca bien. Son las únicas reglas que
distinguen **«no pasa nada»** de **«no me está llegando nada»**.

### Lo que NO cubren, a propósito

**Que la máquina esté apagada o dormida.** Eso no es un incidente para un plano
central que vive en un portátil, y detectarlo desde dentro es imposible: si la
máquina duerme, quien mira duerme con ella.

De eso se encarga el *dead man's switch* de `deadman/`, que corre fuera del
compose y sin dependencias. Su alcance es «Argus dejó de reportar **mientras la
máquina vivía**», que es el alcance correcto — y es el mismo patrón que usa la
industria: un watchdog externo, nunca una regla que se pregunte si ella misma
está viva.

---

## «frescura de vmalert» en el dead man's switch {#frescura-vmalert}

El vigilante ha avisado de que **vmalert evalúa con horas de atraso**. No es que
esté caído: responde, dice `health: ok`, y sigue escribiendo. Lo que escribe cae
en el pasado.

**Esto es grave y no lo parece.** Mientras dure:

- Ninguna regla de grabación es consultable a `now`, así que las **alertas de
  burn-rate de SLO no pueden disparar** — leen `argus:error_ratio:*`.
- Las reglas de ausencia se evalúan contra datos viejos y pueden disparar por
  series que en aquel momento no existían.

### El arreglo

```bash
docker compose -f platform/compose.yaml --env-file platform/.env --profile lean restart vmalert
```

Y se comprueba que volvió, en la fuente y no en la alerta:

```bash
curl -s --get 'http://127.0.0.1:8428/api/v1/query' \
  --data-urlencode 'query=argus:observador_despierto' | python3 -m json.tool
```

Tiene que devolver un resultado con una marca de tiempo **de ahora**. Si devuelve
vacío, vmalert sigue atrasado.

Confirma también que las de SLO vuelven:

```bash
curl -s --get 'http://127.0.0.1:8428/api/v1/query' \
  --data-urlencode 'query=argus:error_ratio:rate5m'
```

### Por qué lo avisa el vigilante y no una alerta

Porque **la víctima es el evaluador**. Una regla que preguntara por su propio
atraso la evaluaría el mismo reloj atrasado, y desde ahí todo parece consistente.
No hay expresión PromQL que lo detecte.

Es el mismo motivo por el que el watchdog va fuera: una regla nunca debe
preguntarse si ella misma está viva (`D-087`).

### Si vuelve a pasar a menudo

No hay curación automática, a propósito: un reinicio en bucle taparía la causa.
Si se repite, lo que hay que averiguar es **por qué** el reloj de evaluación se
atrasa —la sospecha es la suspensión del portátil— y no poner un reinicio
periódico encima.

---

## Tras reiniciar el alert-bus, el tablero de incidentes miente {#tablero-vacio}

**Si acabas de reiniciar `alert-bus`, `/incidents` está vacío y eso NO significa
que no haya problemas.**

Los incidentes viven en memoria (`self._incidents`, un `dict`). Al reiniciar se
pierden. Y el canario **no los vuelve a mandar**, porque recuerda en su propio
`_alertados` lo que ya reportó:

```python
if objetivo in self._alertados:
    continue        # ya alertado; no se reenvia
```

Así que el problema sigue ahí, el tablero está vacío, y las dos mitades creen
estar en lo correcto (`D-090`).

### Para repoblarlo

Reinicia **también** el canario, que es lo que limpia su memoria de alertados:

```bash
docker compose -f platform/compose.yaml --env-file platform/.env --profile lean restart canary
```

En el siguiente ciclo (5 min por defecto) vuelve a reportar lo que siga mal.

### Cómo saber si el tablero es de fiar

Compara la hora de arranque de los dos. Si el alert-bus arrancó **después** del
canario, el tablero está incompleto:

```bash
docker inspect argus-alert-bus-1 argus-canary-1 --format '{{.Name}} {{.State.StartedAt}}'
```

### Por qué no está arreglado todavía

Porque el arreglo bueno no es quitar el dedup —sin él, un servicio caído avisa
cada cinco minutos para siempre— sino que el canario **reconcilie**: que mande su
estado completo cada ciclo y el alert-bus decida qué es nuevo. Eso hace irrelevante
quién se reinicie, y va junto con persistir los incidentes (`B-20` y `F2-15`).
