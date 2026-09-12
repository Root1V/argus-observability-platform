# alert-bus

El receptor del **camino caliente**. Recibe spans del Collector agente por OTLP,
decide si constituyen un incidente y a quién avisar — **sin tocar la base de
datos**.

## Por qué existe y por qué es un receptor OTLP

Una tubería convencional detecta consultando el almacén, y eso cuesta minutos:
el tail sampling del gateway espera hasta 30 s a que la traza cierre, la
inserción se bufferiza, las reglas se evalúan cada 15–60 s.

Aquí la detección ocurre **sobre el flujo**. El Collector agente filtra lo que
puede ser un incidente —`argus.hot`, `status=ERROR`, guardarraíl roto— y lo manda
directo aquí con lotes de 200 ms. Presupuesto: **~2 s del fallo al aviso**.

El volumen es bajo por definición: los errores son la excepción, así que lotes
pequeños no cuestan nada.

## Qué no hace

No almacena. No investiga. No decide por su cuenta a quién avisar — eso es una
regla del registro de aplicaciones, no un juicio (D-014). Su trabajo es:

1. **Normalizar** spans OTLP a `Signal`
2. **Deduplicar** por huella de cardinalidad cerrada
3. **Agrupar** señales en un `Incident` que se actualiza
4. **Enrutar** a los *sinks* según severidad

## Entradas

| Ruta | Origen | Camino |
|---|---|---|
| `POST /v1/traces` | Collector agente (OTLP/HTTP, protobuf o JSON) | Caliente, ~2 s |
| `POST /api/v2/alerts` | vmalert (formato Alertmanager) | Templado, 2–15 s |

## Salidas

Los *sinks* son una interfaz. Hoy hay uno de consola para desarrollo; el
`notifier` con Google Chat y SMTP llega en `F2-04`. Enchufar
[Keep](https://www.keephq.dev/) o PagerDuty más adelante es escribir un
adaptador, no rehacer la capa (D-025).
