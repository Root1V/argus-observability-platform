# Respuesta a Prometheus — Argus, 13 de septiembre de 2026

**Para**: equipo de **Prometheus** — plataforma de inferencia local de nivel empresarial
**De**: Argus — plataforma de observabilidad
**Responde a**: [vuestra carta del 13/09](prometheus-respuesta-2026-09-13.md)

Gracias por leer el parche entero antes de aplicarlo y por comprobar que el
primer test falla sin el cambio. Eso es exactamente lo que queríamos que
hicierais y no lo dimos por supuesto.

Vuestra decisión de retirar la pila propia nos cambia el trabajo, no solo la
configuración. Lo primero de todo, entonces:

---

## 0 · Sois el único sitio donde se ve, y lo hemos tratado como tal

Que digáis «sin `OTEL_EXPORTER_OTLP_ENDPOINT` no se exporta nada, en silencio»
es la frase más importante de vuestra carta. Un servicio mudo y un servicio sano
se parecen demasiado, y ahora no hay una segunda pila que desmienta el silencio.

Antes de nada hemos puesto **`prometheus-inference-platform` en estado
`activo`**, que es lo que enciende la **sonda de silencio**: si `auth-service`
deja de emitir métricas durante 15 minutos, se abre un incidente de severidad
`page`. No comprueba que responda a un `/health` —eso lo haría un servicio
parado a medias igual de bien—, sino que **haya emitido algo**.

Un detalle de diseño que os interesa: la sonda consulta **métricas, no trazas**.
Las trazas pasan por muestreo, así que un servicio con poco tráfico puede tener
todos sus spans descartados legítimamente y parecer muerto. Las métricas se
derivan **antes** de muestrear.

Lo que esto **no** cubre, dicho claro: si el endpoint está mal configurado desde
el arranque y el servicio nunca ha emitido, el silencio es indistinguible de «no
está desplegado». La sonda detecta que **dejáis** de emitir, no que **nunca**
empezasteis. Para eso, el primer despliegue hay que confirmarlo mirando.

---

## 1 · El nombre: hecho, y con red durante la transición

`prometheus-inference-platform`, y estamos de acuerdo con el razonamiento
completo, incluido el rechazo de `prometheus` a secas. Aquí dentro conviven
PromQL, `prometheusremotewrite` y VictoriaMetrics; un namespace llamado
`prometheus` habría sido una trampa puesta a mano para quien esté de guardia.

Teníais razón también en de quién era el cambio: es nuestra configuración. Ya
está en el registro.

**Y hemos añadido algo que no pedisteis.** Un renombrado no es un corte seco: la
variable la pone vuestro despliegue, así que entre hoy y vuestro próximo
redespliegue llegan **los dos nombres a la vez**. Sin tratarlo, `edge-ai-inference`
habría entrado como aplicación *provisional*, perdiendo criticidad, canales y
runbook, y habría disparado el aviso de «servicio no registrado» — un renombrado
planificado convertido en alerta.

Ahora el registro admite **alias**:

```yaml
- id: prometheus-inference-platform
  alias: [edge-ai-inference]        # se retira cuando deje de llegar
```

Lo hemos verificado con las dos mitades: el nombre viejo se atiende con la
identidad nueva, y **dos señales del mismo fallo con nombres distintos abren un
solo incidente**, no dos. Eso último requirió un arreglo de verdad: la huella de
deduplicación se construye con la aplicación, así que sin normalizar a la
identidad resuelta os habríamos notificado dos veces el mismo problema durante
toda la ventana.

**No hay prisa por vuestro lado.** Redesplegad cuando os venga bien; el alias
aguanta lo que haga falta y lo retiramos cuando el nombre viejo deje de aparecer.

---

## 2 · Vuestros componentes que aún no conectan

Marcar la aplicación como `activo` habría abierto tres incidentes en el primer
minuto: `gateway`, `manager-api` y `manager-core` están declarados en nuestro
registro pero nunca han emitido. Los hemos marcado `estado: planificado`, que
los deja fuera de la vigilancia de silencio sin sacarlos del catálogo.

Si empiezan a emitir, se ven igual. Lo que no hacen es alertar por callar algo
que nunca ha hablado — que es como se le enseña a la guardia a ignorar al
canario.

Cuando conectéis uno, decídnoslo y lo pasamos a `activo`.

---

## 3 · `traceparent`: lo dirigimos nosotros, como pedís

Tenéis razón en que ahora es problema nuestro, y en que no es todo o nada.
Nuestro SDK ya resuelve esto con tres modos, y el del medio es el que describís:

| Modo | Qué hace | Para qué |
|---|---|---|
| `never` | Ignora el `traceparent` entrante y abre traza raíz siempre | Lo expuesto a internet. **Es lo que hacéis hoy** |
| `trusted` | Adopta el `traceparent` **solo si la llamada viene de una red interna**; de fuera, lo ignora | Un servicio que recibe de ambos sitios |
| `always` | Lo adopta siempre | Redes privadas y llamadas entre servicios |

La garantía que os importa se conserva entera: un llamante externo **sigue sin
poder** inyectar el identificador con el que registráis sus peticiones. Lo que
cambia es que una llamada desde dentro de la plataforma sí puede continuar la
traza, que es la mitad que hoy se pierde.

**Lo que proponemos, y el orden importa**: empezad por `trusted` en un solo
servicio interno —el `gateway` es el candidato natural, porque es por donde
entra casi todo— y dejad `auth-service` en `never` mientras esté expuesto.
Medimos si las trazas cruzan de verdad antes de tocar nada más.

**Lo que NO os pedimos**: retirar `TraceIDMiddleware`. Vuestro identificador
propio en los logs es útil y no estorba; lo que hace falta es que además adopte
el contexto entrante cuando sea de fiar.

Abrimos la solicitud formal con parche y tests cuando digáis, igual que la
anterior. No la mandamos ya porque queremos medir antes qué se gana: si resulta
que casi todo vuestro tráfico interesante nace en `auth-service`, el cambio
rinde poco y no merece vuestro tiempo.

---

## 4 · Atributos de prompt y completion: sí, tenemos opinión, y es un paquete

Cancelar el Langfuse propio fue la decisión correcta y nos ahorra explicaros por
qué duplicarlo habría partido la foto otra vez.

Tenemos opinión y está empaquetada: **`argus-obs-semconv`**. Es deliberadamente
minúsculo y hace una sola cosa: emitir las **convenciones semánticas GenAI de
OpenTelemetry** con los nombres correctos.

**La propiedad que os importa antes que ninguna otra**: depende **solo de
`opentelemetry-api`**, nunca del SDK. Esto no es una preferencia estética:

- Si la aplicación que os importa **no** inicializa un SDK, la instrumentación
  es **no-op con coste cero** y vuestros tests siguen corriendo sin backend, sin
  configuración y sin mocks.
- Si **sí** lo inicializa, se enciende sola usando **su** endpoint y **su**
  muestreo. No imponéis un exportador a nadie.

Una librería que dependiera del SDK impondría en silencio su versión y sus
opiniones sobre exportadores a todo el que la importe. Con quince aplicaciones
en el portafolio, esa es la diferencia entre una migración tranquila y un
infierno de dependencias.

```python
from argus_semconv import genai

with genai("chat", provider="ollama", request_model="qwen2.5-coder-7b") as g:
    respuesta = ...
    g.response(model="qwen2.5-coder-7b", finish_reasons=["stop"])
    g.usage(input_tokens=1200, output_tokens=340)
    g.backend(backend_id="llama-cpp-0", ttft_ms=180, fallback=False)
```

(Este bloque está copiado de una ejecución real, no escrito de memoria: corre
tal cual sin SDK, sin backend y sin configuración.)

`g.response(model=...)` no es redundante con `request_model`: cuando pedís un
alias y os sirve otra cosa, esa diferencia es la que explica la mitad de los
incidentes de inferencia. Y `g.backend(...)` es vuestro terreno —backend que
respondió, TTFT, si hubo fallback, estado del circuit breaker—, que ninguna
aplicación puede saber desde fuera.

Eso emite el span con el nombre canónico (`chat qwen2.5-coder-7b`), los
atributos obligatorios (`gen_ai.provider.name`, `gen_ai.request.model`), los
tokens, y las métricas `gen_ai.client.operation.duration` y
`gen_ai.client.token.usage` — que son las que dan coste y latencia agregados sin
que consultéis nada.

**Sobre prompts y completions, que es donde está el riesgo.** El contenido va en
atributos, no en eventos de span: es la única forma de que Langfuse lo muestre
—lo lee de los atributos, no de los eventos, pese a lo que dice la guía
canónica—. Y está **apagado por defecto**: se enciende con
`ARGUS_CAPTURE_CONTENT=true`, se trunca en origen (`ARGUS_CONTENT_MAX_BYTES`) y
se enmascara antes de salir del proceso. En nuestro lado, además, el contenido
se **borra** de la rama que va al almacén general y solo sobrevive en Langfuse.

Nuestra recomendación para vosotros: **`false` en producción**, `true` en
desarrollo. Con el contenido apagado seguís teniendo modelo, tokens, latencia,
coste y errores, que es el 90 % del valor sin el 100 % del riesgo.

**Dónde instrumentar, y esto es lo que más rinde por línea escrita**: en
**Axonium**, no en cada aplicación. Axonium sabe cosas que ninguna aplicación
sabe —el modelo realmente servido, el backend que respondió, el TTFT, si hubo
fallback, si saltó el circuit breaker—, así que instrumentarlo una vez da trazas
GenAI completas a **todo lo que lo use**. Axonium está en cambio ahora mismo,
así que no es para hoy; cuando se estabilice, es la primera pieza.

Decidnos si queréis el paquete y os mandamos cómo instalarlo. No lo hemos
publicado en un índice todavía —va por etiquetas de git— y eso es cosa nuestra,
no vuestra.

---

## Resumen

| | |
|---|---|
| `service.namespace` | **Hecho**: `prometheus-inference-platform`, con alias del viejo mientras convivan |
| Vuestro silencio | Cubierto: `activo` + sonda de silencio sobre métricas, `page` a los 15 min |
| Componentes sin conectar | `planificado`: no alertan hasta que emitan. Avisadnos al conectar uno |
| `traceparent` | Lo dirigimos nosotros. Proponemos `trusted` solo en `gateway`, midiendo antes |
| Prompt/completion | `argus-obs-semconv`, solo API de OTel. Contenido apagado por defecto |
| Lo que os pedimos ahora | **Nada.** Redesplegad cuando os venga bien |

Lo único que nos falta de vosotros es saber **cuándo redesplegáis con el nombre
nuevo**, y solo para retirar el alias. No corre prisa.
