# Un servicio está mudo

Aparece en el registro como `activo`, la sonda de silencio ha abierto un
incidente `page`, y no llega telemetría suya.

**Antes de mirar el proceso, mira la configuración.** El orden importa: la
causa más frecuente no es que el servicio esté caído.

---

## 1. ¿Tiene endpoint configurado? — empieza SIEMPRE por aquí

Las aplicaciones que retiraron su pila propia **no tienen colector por
defecto**. Prometheus lo hizo el 13/09/2026: su código tenía
`http://tempo:4318` codificado como respaldo y lo quitaron con el resto. Sin
`OTEL_EXPORTER_OTLP_ENDPOINT`, **no exportan nada, en silencio y a propósito**
— la alternativa era cada proceso reintentando contra un nombre que no resolvía.

Eso significa que **nuestra configuración es el único camino**, y que un
servicio mudo es más probable que esté mal apuntado que caído.

```bash
# En la máquina donde corre el servicio
env | grep OTEL_
```

Lo que tiene que haber:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   # SIEMPRE localhost
OTEL_RESOURCE_ATTRIBUTES=service.namespace=<app>,argus.component.role=<rol>
OTEL_SERVICE_NAME=<componente>
```

`localhost` no es un descuido: las aplicaciones **nunca** conocen la dirección
del plano central. Exportan al agente de su propia máquina, y el agente se
encarga del resto. Si ves la IP del plano central ahí, está mal aunque funcione.

> **Atajo que funcionó la primera vez que se usó de verdad**: si OTRO servicio
> de la misma máquina sí está llegando, descarta de un plumazo la red, el agente
> y el colector. El problema es de ese proceso, y casi siempre es su endpoint.
>
> Pasó el 15/09/2026 con el `gateway` de Prometheus: `manager-api` y
> `auth-service` llegaban con normalidad y solo el `gateway` estaba mudo. Su
> endpoint apuntaba a un receptor OTLP de pruebas que alguien había levantado
> para leer el protobuf en el cable, y que nadie recordó quitar.

## 2. ¿Está el agente de esa máquina vivo?

```bash
curl -sf http://localhost:13133/ && echo "  agente OK"
```

Si el agente está caído, el servicio exporta a un puerto cerrado. Los SDK de
OTel **descartan en silencio** cuando no pueden exportar, que es lo correcto
—perder telemetría es mejor que tumbar la aplicación— y lo que hace este caso
difícil de ver desde el servicio.

## 3. ¿Llega algo con OTRA identidad?

Un servicio que emite bajo un nombre que no esperamos parece mudo:

```sql
SELECT ResourceAttributes['service.namespace'] AS ns, ServiceName, max(Timestamp)
FROM otel.otel_traces
WHERE Timestamp > now() - INTERVAL 1 HOUR
GROUP BY ns, ServiceName ORDER BY 3 DESC
```

Busca el `service.name` del servicio bajo cualquier namespace, y en especial
bajo `unregistered`. Causas típicas: `OTEL_RESOURCE_ATTRIBUTES` sin poner, un
`Resource` construido con el constructor directo en vez de `Resource.create()`
—que descarta esa variable **sin dar error**—, o un renombrado a medias, en
cuyo caso la entrada del registro necesita un `alias`.

## 4. ¿Y si emite trazas pero la sonda dice silencio?

La sonda mira **métricas**, no trazas, y mide si el contador **creció**. Si el
servicio emite trazas pero no genera métricas, revisa que el `spanmetrics`
connector esté en la tubería del agente.

## 5. Solo ahora, mira el proceso

```bash
ps aux | grep <servicio>
```

---

## Lo que este runbook NO cubre

La sonda detecta que un servicio **deja** de emitir, no que **nunca** empezó.
Un servicio mal configurado desde el arranque es indistinguible de «no está
desplegado», porque en ambos casos no hay nada con lo que comparar.

**Por eso el primer despliegue de un componente hay que confirmarlo mirando**,
y por eso un componente declarado pero nunca conectado va como
`estado: planificado` en el registro: para no alertar de que calla algo que
nunca ha hablado.
