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

## `ArgusCollectorSinAutoMetricas`

**Esta es la alerta que vigila a las otras dos.** Sin las métricas internas del
Collector, las dos de arriba no pueden disparar nunca, y volvemos al punto ciego
exacto de `D-079`: dos días perdiendo el 100% de la telemetría sin que nada
avise.

Que no llegue significa una de dos cosas, y las dos son graves:

1. El receptor `prometheus/interno` no está en la tubería de métricas de
   `platform/collector/agent.yaml` o `gateway.yaml`.
2. El Collector no está corriendo.

```bash
grep -n "prometheus/interno" platform/collector/*.yaml   # debe salir 4 veces: 2 receptores, 2 tuberías
docker exec argus-vmalert-1 wget -qO- http://127.0.0.1:8880/api/v1/rules | grep -c ArgusCollector
```

Trátala como `page` aunque todo lo demás parezca bien. Es la única regla que
distingue **«no pasa nada»** de **«no me está llegando nada»**.
