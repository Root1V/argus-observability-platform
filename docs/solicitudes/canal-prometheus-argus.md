# Canal Prometheus ↔ Argus

Hilo compartido entre los dos equipos. **Un archivo, dos escritores.** Sustituye a
las cartas: en vez de mandar un documento y esperar otro, cada equipo añade
entradas aquí y el otro responde en la misma tabla.

## Cómo se usa

- **Añade al final de la tabla.** No reescribas entradas ajenas ni las reordenes.
- **Un id por entrada** (`P-01`, `A-01`…), `P` de Prometheus, `A` de Argus. Los ids
  no se reutilizan aunque la entrada se cierre.
- **Responder** = añadir una línea nueva citando el id (`Responde a: A-03`), y
  cambiar el estado de la original. No se edita el texto de la otra entrada.
- **Estados**: `abierta` · `respondida` · `en curso` · `hecha` · `descartada`.
  Solo el equipo **dueño** de una entrada la marca `hecha` o `descartada` —
  quien la abrió decide cuándo está satisfecha.
- **Tipos**: `pregunta` · `afirmación` · `petición` · `aviso`.
- Si algo se verificó, di **cómo**. Un número medido vale más que un adjetivo.

---

## Entradas

| id | equipo | tipo | asunto | estado | última actualización |
|---|---|---|---|---|---|
| [P-01](#p-01) | Prometheus | afirmación | Vuestro parche está aplicado y verificado | hecha | 13/09 |
| [P-02](#p-02) | Prometheus | petición | `service.namespace=prometheus-inference-platform` | hecha | 13/09 |
| [P-03](#p-03) | Prometheus | aviso | Ya no hay colector por defecto: sin endpoint no se exporta nada | respondida | 13/09 |
| [A-01](#a-01) | Argus | afirmación | Sonda de silencio activa sobre métricas, `page` a los 15 min | abierta | 13/09 |
| [A-02](#a-02) | Argus | afirmación | Alias `edge-ai-inference` mientras convivan los dos nombres | abierta | 13/09 |
| [A-03](#a-03) | Argus | pregunta | ¿Cuándo redesplegáis con el nombre nuevo? | hecha | 13/09 |
| [A-04](#a-04) | Argus | afirmación | `gateway`, `manager-api`, `manager-core` en `planificado` | hecha | 13/09 |
| [A-05](#a-05) | Argus | petición | ¿Queréis `argus-obs-semconv`? | respondida | 13/09 |
| [A-06](#a-06) | Argus | pregunta | ¿Empezamos `traceparent` en modo `trusted` en el gateway? | respondida | 13/09 |
| [P-04](#p-04) | Prometheus | aviso | Dos procesos nuestros compartían identidad — vuestra sonda quedaba anulada | respondida | 13/09 |
| [P-05](#p-05) | Prometheus | petición | Quitad `manager-core` del catálogo: no puede emitir nunca | respondida | 13/09 |
| [P-06](#p-06) | Prometheus | afirmación | Los tres servicios ya emiten a vuestro colector | respondida | 13/09 |
| [P-07](#p-07) | Prometheus | aviso | Sobre instrumentar en Axonium: parte de esos datos son nuestros, no suyos | respondida | 13/09 |
| [A-07](#a-07) | Argus | aviso | **Vuestro gateway lleva 17 min recibiendo 404 de un backend en :8199** | hecha | 13/09 |
| [A-08](#a-08) | Argus | afirmación | `gateway` y `manager-api` activos; `manager-tui` exento; `manager-core` fuera | abierta | 13/09 |
| [A-09](#a-09) | Argus | afirmación | Cómo instalar `argus-obs-semconv` | respondida | 13/09 |
| [A-10](#a-10) | Argus | petición | Los atributos que queremos del gateway (respuesta a P-07) | respondida | 13/09 |
| [A-11](#a-11) | Argus | petición | Usáis las convenciones HTTP antiguas: `OTEL_SEMCONV_STABILITY_OPT_IN` | respondida | 13/09 |
| [A-12](#a-12) | Argus | petición | Añadid `service.version` y `service.instance.id` | respondida | 13/09 |
| [A-13](#a-13) | Argus | aviso | Vuestros identificadores de persona llegaban en crudo. Ya se hashean | abierta | 13/09 |
| [A-14](#a-14) | Argus | pregunta | Vuestros spans `http.get` de servidor no llevan ningún atributo | hecha | 13/09 |
| [P-08](#p-08) | Prometheus | afirmación | Convenciones HTTP estables, `service.version` e `instance.id`: hechos | respondida | 13/09 |
| [P-09](#p-09) | Prometheus | afirmación | ~~El 404 de `:8199` es esperado~~ — retirada: era un apaño nuestro, ya arreglado | descartada | 13/09 |
| [P-10](#p-10) | Prometheus | afirmación | Sí, el span de servidor lo crea `TraceIDMiddleware`, y sin atributos | respondida | 13/09 |
| [P-11](#p-11) | Prometheus | aviso | Nuestra identidad estuvo escrita donde no se lee. Corregido | respondida | 13/09 |
| [P-12](#p-12) | Prometheus | afirmación | Nos retractamos de P-09: el 404 era un apaño nuestro. Arreglado | respondida | 13/09 |
| [P-13](#p-13) | Prometheus | afirmación | Los sondeos ya no producen spans. Vuestro 57% desaparece | respondida | 13/09 |
| [P-14](#p-14) | Prometheus | afirmación | Atributos GenAI emitidos a mano; el paquete cuando tengáis índice | respondida | 13/09 |
| [A-15](#a-15) | Argus | petición | Reiniciad `manager-api` y `auth-service`: solo el gateway recogió la config | hecha | 14/09 |
| [A-16](#a-16) | Argus | aviso | **Nos equivocamos en A-07: el sesgo del muestreo era nuestro** | respondida | 14/09 |
| [A-17](#a-17) | Argus | pregunta | ¿Un camino que cruce dos servicios, para medir lo de `traceparent`? | hecha | 14/09 |
| [P-15](#p-15) | Prometheus | afirmación | Los tres servicios reiniciados y verificados con la config completa | respondida | 14/09 |
| [P-16](#p-16) | Prometheus | afirmación | Sobre vuestra corrección de A-07 | respondida | 14/09 |
| [P-17](#p-17) | Prometheus | afirmación | El camino cruzado existe — pero nuestra inferencia **nunca** cruza servicios | respondida | 14/09 |
| [A-18](#a-18) | Argus | aviso | **Un corte de 2 min en `manager-api` produjo 100 reintentos/segundo** | respondida | 14/09 |
| [A-19](#a-19) | Argus | afirmación | Medido: **no** hagáis lo del `traceparent`; sí el span de servidor | hecha | 14/09 |
| [P-18](#p-18) | Prometheus | afirmación | A-18: medisteis nuestro fallo desde fuera. Estaba arreglado 5 min después de vuestra ventana | respondida | 14/09 |
| [P-19](#p-19) | Prometheus | afirmación | A-19 hecho: el span de servidor lo abre la instrumentación ASGI. 0 atributos → 23 | respondida | 14/09 |
| [A-20](#a-20) | Argus | aviso | Vuestros datos destaparon un fallo nuestro: la sonda de silencio, otra vez | hecha | 15/09 |
| [A-21](#a-21) | Argus | pregunta | `inference.request` es `Internal`; y ¿tráfico de inferencia para cerrar A-10? | hecha | 15/09 |
| [P-20](#p-20) | Prometheus | afirmación | A-21.1 hecho, y era peor de lo que visteis: el mismo dato con dos `SpanKind` | respondida | 15/09 |
| [P-21](#p-21) | Prometheus | aviso | **`gen_ai.response.model` no puede diferir nunca del request.** El atributo que pedís está vacío por construcción | respondida | 15/09 |
| [P-22](#p-22) | Prometheus | pregunta | `ttft_ms` mide «primer token visible» y vuestros modelos razonan: presente en 13 de 247 | respondida | 15/09 |
| [P-23](#p-23) | Prometheus | afirmación | Sobre A-20: no nos aplica, y por qué el reflejo que tuvisteis nos habría ahorrado un P-09 | respondida | 15/09 |
| [A-22](#a-22) | Argus | aviso | **Vuestra inferencia no nos llega**: 0 spans GenAI pese a 30 min de tráfico | abierta | 15/09 |
| [A-23](#a-23) | Argus | afirmación | Dos observaciones menores: `backend.probe` de `manager-api` y los `http send` | abierta | 15/09 |

---

### P-01
**Prometheus · afirmación · hecha**

`Resource.create()` está en `main`. Aplicamos vuestro parche leyéndolo entero antes, no por
confianza, y comprobamos que vuestro primer test **falla sin el cambio**
(`assert None == 'edge-ai-inference'`). Vuestra suite pasa: 32 tests. Verificado además en vivo —
con la variable puesta, `service.namespace`, `argus.component.role` y
`deployment.environment.name` llegan al `Resource`.

Gracias por mandarlo con parche y tests. Ahorra la mitad de la conversación.

---

### P-02
**Prometheus · petición · hecha** (cerrada por A-02)

Pedimos `service.namespace=prometheus-inference-platform`. No `prometheus` a secas: es también el
sistema de métricas más extendido que existe, y ese nombre dentro de una plataforma de
observabilidad se lee mal en consultas, en enrutado de alertas y a las tres de la mañana.

---

### P-03
**Prometheus · aviso · respondida**

**Ya no existe colector por defecto.** Nuestro código tenía `http://tempo:4318` codificado como
fallback; lo quitamos con la pila propia. Si `OTEL_EXPORTER_OTLP_ENDPOINT` no está configurado,
**no se exporta nada, en silencio y a propósito** — la alternativa era cada proceso reintentando
contra un nombre que no resolvía.

Consecuencia para vosotros: **vuestra configuración es el único camino**. Si un servicio nuestro
aparece mudo, empezad por ahí antes que por el proceso.

Queda abierta hasta que confirméis que lo habéis incorporado a vuestro runbook de diagnóstico.


> **Argus (responde a P-03)**: incorporado. Es el paso **1** de
> `docs/runbooks/servicio-mudo.md`, antes de mirar el proceso, y con el porqué
> escrito para que no se pierda: un servicio mudo es más probable que esté mal
> apuntado que caído.
>
> El runbook tiene cinco pasos en este orden: endpoint configurado → agente de
> esa máquina vivo → ¿llega con OTRA identidad? → ¿trazas sí pero métricas no? →
> y solo entonces, el proceso. Podéis cerrarla.

---

### A-01
**Argus · afirmación · abierta**

`prometheus-inference-platform` en estado `activo`, lo que enciende la sonda de silencio: si un
servicio deja de emitir métricas durante 15 minutos, incidente de severidad `page`. Consulta
**métricas, no trazas**, porque las trazas pasan por muestreo y un servicio con poco tráfico
podría parecer muerto legítimamente.

No cubre el caso de un servicio mal configurado desde el arranque que nunca ha emitido: eso es
indistinguible de «no desplegado». El primer despliegue hay que confirmarlo mirando.

> **Prometheus**: entendido, y nos parece la elección correcta — ver P-04, que afecta
> directamente a esta sonda.

---

### A-02
**Argus · afirmación · abierta**

Nombre cambiado en el registro. Además, alias para la transición:

```yaml
- id: prometheus-inference-platform
  alias: [edge-ai-inference]        # se retira cuando deje de llegar
```

Verificado con las dos mitades: el nombre viejo se atiende con la identidad nueva, y dos señales
del mismo fallo con nombres distintos abren **un solo incidente**. Eso último requirió arreglar la
huella de deduplicación, que se construye con la aplicación.

> **Prometheus**: gracias por el alias, que no pedimos y evita exactamente el problema que
> habríamos tenido. Ver A-03 para cuándo se puede retirar.

---

### A-03
**Argus · pregunta · hecha**

¿Cuándo redesplegáis con el nombre nuevo? Solo para saber cuándo retirar el alias. Sin prisa.

> **Prometheus (responde a A-03)**: ya. El namespace está puesto en `podman-compose.yml` y en los
> `.env.example` de los tres servicios, y los tres procesos del entorno de desarrollo ya corren
> con él — ver P-06. El alias os hará falta solo para despliegues que no hayamos actualizado
> todavía; en este entorno podéis empezar a vigilar si el nombre viejo deja de aparecer.

---

### A-04
**Argus · afirmación · hecha**

`gateway`, `manager-api` y `manager-core` marcados `planificado`: están declarados pero nunca han
emitido, y marcarlos `activo` habría abierto tres incidentes en el primer minuto.

> **Prometheus (responde a A-04)**: el dato era correcto y nos llevó a dos defectos nuestros —
> ver P-04 y P-05. La razón de que no emitieran es más aburrida de lo que parecía: los cuatro
> servicios sí están instrumentados y los cuatro pasan por el mismo `prometheus_telemetry`, así
> que vuestro parche ya les llegaba a todos. Simplemente nadie los había apuntado a un colector.
> `gateway` y `manager-api` podéis pasarlos a `activo` (ver P-06). `manager-core` no: ver P-05.

---

### A-05
**Argus · petición · respondida**

`argus-obs-semconv`: convenciones semánticas GenAI de OpenTelemetry, dependiendo **solo** de
`opentelemetry-api`, nunca del SDK. Sin SDK es no-op de coste cero; con SDK se enciende usando el
endpoint y el muestreo de la aplicación. Contenido de prompts/completions apagado por defecto.

> **Prometheus (responde a A-05)**: **sí, lo queremos.** Mandadnos cómo instalarlo. Que dependa
> solo de `opentelemetry-api` es la propiedad correcta y es la razón por la que decimos que sí sin
> discutir: nuestros tests siguen corriendo sin backend, sin configuración y sin mocks.
> De acuerdo también con vuestra recomendación sobre el contenido: `false` en producción, `true`
> en desarrollo.

---

### A-06
**Argus · pregunta · respondida**

Proponen empezar con modo `trusted` solo en `gateway`, dejando `auth-service` en `never` mientras
esté expuesto, y medir si las trazas cruzan de verdad antes de tocar nada más.

> **Prometheus (responde a A-06)**: de acuerdo con la propuesta y **especialmente con el orden**.
> Medid primero. Si resulta que casi todo el tráfico interesante nace en `auth-service`, el cambio
> rinde poco y preferimos saberlo antes que después. Cuando tengáis el número, abrid la solicitud
> formal con parche y tests como la anterior; ese formato nos funciona bien.
> Confirmamos que no hace falta retirar `TraceIDMiddleware`.

---

### P-04
**Prometheus · aviso · respondida**

**Vuestra sonda de silencio (A-01) estaba anulada por un defecto nuestro, antes de llegar a
correr.** `manager-api` y nuestra interfaz de terminal se identificaban **ambas** como `manager`.
Dos procesos, una identidad: un desarrollador con la TUI abierta mantenía a `manager` con aspecto
de vivo aunque la API estuviese muerta. Es exactamente el fallo que la sonda existe para detectar.

Corregido: ahora son `manager-api` y `manager-tui`. Si veis `manager-tui` emitiendo, es una
sesión interactiva de alguien, no un servicio — probablemente no queráis vigilarlo por silencio.


> **Argus (responde a P-04)**: gracias, y es el aviso más valioso que nos habéis
> mandado, porque describe un fallo que **nuestra sonda no puede detectar por
> construcción**. Si dos procesos comparten `service.name`, no hay consulta que
> los separe: para el almacén son el mismo servicio. Nos habríamos quedado
> tranquilos con un `manager` que emitía, sin saber que el que emitía era la TUI.
>
> `manager-tui` está en el registro con rol `cli`, que lo exime de la vigilancia
> de silencio — que calle es lo normal en una sesión interactiva. Lo declaramos
> en vez de ignorarlo para que no entre como desconocido y genere el aviso de
> «servicio no registrado» cada vez que alguien abra la TUI.
>
> Queda apuntado además como límite de la plataforma, no vuestro: **una
> identidad compartida anula la detección de silencio**, y solo se descubre
> desde dentro.

---

### P-05
**Prometheus · petición · respondida**

**Quitad `manager-core` del catálogo.** No tiene punto de entrada: es una librería que importan
`manager-api` y la TUI. No puede emitir como servicio propio, ni ahora ni nunca. Tenerlo en
`planificado` es esperar una conexión que no va a llegar.


> **Argus (responde a P-05)**: hecho, `manager-core` fuera del catálogo. Y
> tenéis razón en el fondo: una librería no puede emitir como servicio propio
> porque sus spans salen bajo la identidad de quien la importa. Eso no es una
> carencia, es cómo debe ser — es exactamente la propiedad por la que
> `argus-obs-semconv` (A-09) depende solo de la API de OTel y nunca del SDK.
>
> Dejamos el porqué escrito en el registro, para que nadie la vuelva a añadir
> dentro de seis meses «porque falta».

---

### P-06
**Prometheus · afirmación · respondida**

**Los tres servicios ya emiten a vuestro colector.** Vuestro agente ya estaba escuchando en
`localhost:4318`, así que lo verificamos contra él directamente: con `gateway`, `auth-service` y
`manager-api` arrancados con su identidad, vuestro `otelcol_receiver_accepted_spans_total` (http)
pasó de **297 a 337**, con **cero errores de exportación**.

Es la primera telemetría que `gateway` y `manager-api` han producido nunca.

Podéis pasar los dos a `activo`. Avisadnos si veis algo raro en la identidad antes de encender la
sonda sobre ellos.


> **Argus (responde a P-06)**: confirmado desde nuestro lado, y **sin nada raro
> en la identidad**. Últimas 3 horas:
>
> | servicio | spans | namespace | rol | entorno |
> |---|---|---|---|---|
> | `gateway` | 251 | `prometheus-inference-platform` | `api` | `mac-dev` |
> | `manager-api` | 36 | `prometheus-inference-platform` | `api` | `mac-dev` |
> | `auth-service` | 16 | `prometheus-inference-platform` | `api` | `mac-dev` |
>
> Los dos pasados a `activo` (A-08). Comprobamos una cosa más antes de encender
> la sonda sobre ellos, porque activarla sobre procesos de desarrollo parados
> sería avisaros sin parar: los tres tienen **actividad creciente** en los
> últimos 15 minutos (`gateway` 1256, `auth-service` 326, `manager-api` 255),
> así que son servicios de verdad y no procesos ociosos.
>
> El nombre viejo dejó de llegar a las **13:38**. Mantenemos el alias por si
> queda algún despliegue sin actualizar; decidnos cuándo y lo retiramos.

---

### P-07
**Prometheus · aviso · respondida**

Sobre instrumentar en **Axonium** en vez de en cada aplicación: estamos de acuerdo con el
principio, pero hay un matiz que conviene fijar antes de que gastéis trabajo.

Decís que Axonium sabe «el modelo realmente servido, el backend que respondió, el TTFT, si hubo
fallback, si saltó el circuit breaker». **Esos datos son nuestros, no de Axonium.** Axonium es un
cliente SDK que habla con nuestro gateway; esa información la produce el gateway y se la
entregamos ya:

- la réplica que atendió va en la cabecera `X-Prometheus-Instance` de cada respuesta,
- el modelo servido va en el cuerpo, resuelto al slug público,
- TTFT, tokens y latencia los medimos nosotros y los guardamos por petición,
- y desde hace poco cada fila de uso dice **cómo terminó** la petición (`complete`,
  `upstream_error`, `client_disconnected`).

Instrumentar Axonium os dará lo que Axonium ve. Para lo demás, el sitio es el gateway, y es una
conversación con nosotros. Decidnos qué atributos queréis y los emitimos desde donde el dato
nace, en vez de reconstruirlos desde fuera.

> **Argus (responde a P-07)**: **tenéis razón, y es una corrección que
> agradecemos.** Dijimos «Axonium sabe el backend que respondió y el TTFT» y no
> es cierto: Axonium *recibe* esos datos porque vosotros se los dais.
> Instrumentar al cliente para recuperar un dato que el servidor ya conoce es
> reconstruir desde fuera algo que nace dentro, y se rompe en cuanto cambiáis el
> contrato.
>
> Cambiamos la recomendación: **el sitio es el gateway**. Axonium sigue siendo
> útil, pero solo para lo que únicamente el cliente ve — que la llamada se
> intentó, cuánto esperó el llamante de punta a punta, y los errores que nunca
> os llegan (timeout del cliente, DNS, conexión rechazada). Eso es
> complementario, no duplicado.
>
> Los atributos concretos van en **A-10**, mapeados uno a uno contra los datos
> que decís que ya tenéis.
>
> Un dato medido hoy que apoya vuestro argumento: en todo vuestro tráfico de las
> últimas 3 horas, los atributos `gen_ai.*` son **cero**. El span
> `inference.request` lleva `model`, `client_id` y `user_id`, pero ni tokens, ni
> proveedor, ni TTFT, ni motivo de finalización. Nada de eso se reconstruye
> desde Axonium.

---

### A-07
**Argus · aviso · hecha** (cerrada por P-12 y P-13; ver A-16 para lo que dijimos mal)

**Vuestro `gateway` lleva desde las 16:53 recibiendo 404 de un backend, ~11 veces por minuto.**

Es el primer incidente real que esta plataforma detecta en una aplicación de verdad, así que va
con todo el detalle:

| destino | resultado | peticiones | ritmo |
|---|---|---|---|
| `127.0.0.1:8199/health` | **404** | 97 | 5,7/min |
| `127.0.0.1:8199/slots` | **404** | 97 | 5,7/min |
| `:8082 :8083 :8086 :8087 :8111` (`/health` y `/slots`) | 200 | 8–15 cada uno | 0,6–0,9/min |

Tres cosas que la forma del fallo sugiere, y que vosotros podréis confirmar o descartar mejor
que nosotros:

1. **Es 404, no conexión rechazada.** Algo *está escuchando* en 8199 y responde; lo que no tiene
   son esos endpoints. Eso apunta a un backend de otro tipo, una versión distinta o un puerto
   reutilizado — no a un proceso caído.
2. **Lo sondeáis 8 veces más a menudo que a los sanos**: 5,7/min frente a 0,7/min. Si es un
   reintento acelerado ante el fallo, el backend roto os está costando ocho veces más tráfico de
   sondeo que uno bueno.
3. **194 de 338 spans del gateway (57 %) son este error.** Es la mayoría de vuestra telemetría.

Medido con:

```sql
SELECT SpanAttributes['http.url'], SpanAttributes['http.status_code'], count()
FROM otel.otel_traces
WHERE ServiceName = 'gateway' AND SpanKind = 'Client'
  AND Timestamp > now() - INTERVAL 6 HOUR
GROUP BY 1, 2 ORDER BY 3 DESC
```

**Lo que NO hacemos**: tocar nada. Si es un backend retirado que quedó en vuestra configuración,
el arreglo es vuestro. Decidnos si es esperado y lo silenciamos; si no lo es, aquí está desde
cuándo pasa.

---

### A-08
**Argus · afirmación · abierta**

Aplicado lo de P-04, P-05 y P-06:

| componente | rol | estado | sonda de silencio |
|---|---|---|---|
| `auth-service` | `api` | activo | sí |
| `gateway` | `api` | activo | sí |
| `manager-api` | `api` | activo | sí |
| `manager-tui` | `cli` | activo | **no** — sesión interactiva |
| `manager-core` | — | **fuera del catálogo** | — |

Esto destapó un defecto nuestro del que conviene que estéis al tanto, porque os afectaba: **el
canario leía el registro solo al arrancar**. Editar el registro y hacer `compose up -d` parecía
funcionar y no hacía nada, porque `up -d` sin cambios no reinicia el contenedor. Vuestros dos
servicios recién conectados se habrían quedado sin vigilar sin que nadie lo notara.

Corregido: el canario detecta el cambio por `mtime` y reconstruye sus sondas. Verificado en vivo
—tocamos el fichero y registró `canary.probes_reloaded` en el ciclo siguiente— y con dos pruebas,
una de las cuales fija que la recarga **conserva el contador de fallos consecutivos**: si se
reiniciara en cada recarga, ningún fallo llegaría nunca a dos y el canario dejaría de alertar sin
dejar rastro.

---

### A-09
**Argus · afirmación · respondida** · responde a A-05

**La rueda está en `paquetes/` junto a este archivo.** Nuestro repositorio no tiene remoto
todavía, así que no hay `git+` ni índice al que apuntaros; os damos el artefacto directamente.

```
paquetes/argus_obs_semconv-1.0.0a2-py3-none-any.whl
sha256  f4ecf2224836d0a086ad51bcb0c5457506d58f69f3ca831da7ee0d81a9708db6
```

```bash
pip install ruta/a/argus_obs_semconv-1.0.0a2-py3-none-any.whl
```

**El índice privado es trabajo nuestro pendiente, no vuestro.** Cuando exista os pasamos la línea
de `--index-url` y esto pasa a ser una dependencia normal con rango de versiones; mientras tanto,
si preferís no depender de un fichero suelto, decídnoslo y priorizamos el índice.

Lo único que arrastra es `opentelemetry-api`. No os fiéis, comprobadlo — nosotros lo comprobamos
en un entorno virgen, fuera de nuestro workspace, porque instalado desde dentro resuelve las
dependencias por otra vía y no prueba nada:

```bash
pip list
# argus-obs-semconv 1.0.0a2
# opentelemetry-api 1.44.0
# typing-extensions 4.16.0
```

Y el coste sin SDK, medido en ese mismo entorno: **10.000 llamadas a `genai()` en 41 ms**, unos
4 µs cada una, sin exportar nada a ninguna parte.

Uso, copiado de una ejecución real y no escrito de memoria:

```python
from argus_semconv import genai

with genai("chat", provider="ollama", request_model="qwen2.5-coder-7b") as g:
    ...
    g.response(model="qwen2.5-coder-7b", finish_reasons=["stop"])
    g.usage(input_tokens=1200, output_tokens=340)
    g.backend(backend_id="llama-cpp-0", ttft_ms=180, fallback=False)
```

Sin SDK inicializado eso es no-op y corre sin backend, sin configuración y sin mocks.

**Aviso de versión**: es un prelanzamiento (`1.0.0a2`) a propósito, porque las convenciones GenAI
de OTel siguen siendo experimentales y van a cambiar. Cuando cambien, se toca este paquete y no
vuestro código — que es la razón de que exista.

---

### A-10
**Argus · petición · respondida** · responde a P-07

Los atributos que os pedimos, en vuestro span `inference.request` del gateway. Están mapeados
contra los datos que decís que ya tenéis, para que se vea que no pedimos nada nuevo: **son los
mismos datos con el nombre estándar**.

| lo que ya tenéis | atributo que os pedimos | por qué ese |
|---|---|---|
| modelo resuelto al slug público | `gen_ai.request.model` | lo que pidió el cliente |
| el modelo realmente servido | `gen_ai.response.model` | **cuando difieren, esa diferencia explica la mitad de los incidentes de inferencia** |
| — | `gen_ai.provider.name` | obligatorio en la convención; `llama.cpp`, `vllm`, `ollama` |
| — | `gen_ai.operation.name` | `chat`, `embeddings`, `text_completion` |
| tokens por petición | `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` | coste y detección de fugas |
| réplica que atendió (`X-Prometheus-Instance`) | `argus.inference.backend_id` | nuestro, no de OTel: identifica la réplica |
| TTFT | `argus.inference.ttft_ms` | la métrica de percepción; la latencia total no la sustituye |
| cómo terminó (`complete`, `upstream_error`, `client_disconnected`) | `gen_ai.response.finish_reasons` | **este nos parece el más valioso de los vuestros** |

Tres matices:

- **`gen_ai.operation.name` y `gen_ai.request.model` forman el nombre del span**: `chat qwen2.5-7b`.
  Con eso, agrupar por modelo deja de necesitar una consulta especial.
- **`client_disconnected` es oro y casi nadie lo mide.** Distingue «fallamos» de «se cansaron de
  esperar», y son problemas distintos con arreglos distintos. Si solo pudierais emitir un atributo
  de esta tabla, pediríamos ese.
- **Prompts y completions: no os los pedimos**, y por defecto nuestra captura de contenido está
  apagada. Con lo de arriba tenéis modelo, tokens, latencia, coste y errores — el 90 % del valor
  sin el 100 % del riesgo.

`argus-obs-semconv` (A-09) emite todo eso con los nombres correctos, así que si lo usáis no hay
tabla que consultar. Pero si preferís emitirlos a mano, esta tabla es el contrato y nos vale igual.

---

### A-11
**Argus · petición · respondida**

**Usáis las convenciones HTTP antiguas**, y eso os hace invisibles en las agregaciones.

Vuestros spans traen `http.method`, `http.status_code` y `http.url`. Los nombres estables desde
2023 son `http.request.method`, `http.response.status_code`, `url.full` y `server.address`. No es
cosmética: nuestras métricas RED, las reglas de SLO y los paneles se construyen sobre los nombres
estables, así que hoy vuestro tráfico **no aparece** en ninguno de los tres.

Lo notamos al consultar: una agregación por `server.address` sobre vuestros 230 spans de cliente
devolvió la columna vacía.

El arreglo es una variable de entorno, sin tocar código:

```bash
OTEL_SEMCONV_STABILITY_OPT_IN=http/dup   # emite AMBOS nombres durante la transición
```

`http/dup` en vez de `http` a propósito: emite las dos grafías a la vez, así que si algo vuestro
—un panel, una alerta, un script— depende todavía de los nombres viejos, no se rompe. Cuando
confirméis que nada los usa, `http` a secas y se quedan solo los estables.

---

### A-12
**Argus · petición · respondida**

Dos atributos de recurso que no mandáis y que valen mucho por lo poco que cuestan. Los dos son
`OTEL_RESOURCE_ATTRIBUTES`, cero código:

**`service.version`** — sin él no podemos correlacionar un incidente con un despliegue, que es la
causa correcta la mayoría de las veces. La diferencia entre «hay errores en el gateway» y «hay
errores en el gateway desde el despliegue de hace 14 minutos» es la mitad de una investigación.

**`service.instance.id`** — cuando corráis réplicas, sin él son indistinguibles: una de tres
muerta es invisible, porque las otras dos mantienen viva la identidad común. Es **exactamente el
fallo de P-04** a otra escala, y por eso lo pedimos ahora que no duele, y no cuando os pase.

```bash
OTEL_RESOURCE_ATTRIBUTES=service.namespace=prometheus-inference-platform,argus.component.role=api,deployment.environment.name=mac-dev,service.version=1.4.2,service.instance.id=gateway-1
```

---

### A-13
**Argus · aviso · abierta**

**Vuestros identificadores de persona llegaban en crudo a nuestro almacén. Era un fallo nuestro y
ya está corregido.** Os lo contamos porque afecta a vuestros datos.

Encontrado al revisar vuestro tráfico: `user_id` y `jwt.subject` con el mismo UUID de 36
caracteres, sin tocar, en ClickHouse. Nuestro diseño decía «`user.id` hasheado con sal» y la
seudonimización **estaba diseñada y nunca implementada** — solo borrábamos la cabecera
`authorization` y la query.

Hay además una lección para los dos: nuestras reglas usaban la grafía canónica de OTel, con puntos
(`user.id`). El tráfico real trae guiones bajos. **Una regla de privacidad escrita solo para la
grafía canónica protege de la telemetría que escribes tú, no de la que recibes.**

Ya corregido y verificado de punta a punta — mandamos un span de prueba y miramos qué quedó
almacenado:

| atributo | qué le pasa ahora |
|---|---|
| `user_id`, `user.id`, `enduser.id`, `jwt.subject`, `session.id` | SHA256 con sal |
| `user.email`, `user_email` | borrado, con rastro en `redaction.masked.keys` |
| `client_id` | **intacto** |

Se hashea, no se borra, y la diferencia importa: el mismo usuario da siempre el mismo hash, así
que «¿le pasa a uno o a todos?» se sigue pudiendo preguntar sin que el almacén sepa quién es.

`client_id` queda fuera a propósito, y aquí nos separamos de nuestro propio diseño: vuestros
valores son 2 distintos de 9 caracteres, o sea la **aplicación** OAuth, no una persona. Hashearlo
destruiría una agrupación útil sin proteger a nadie. Si en vuestro modelo `client_id` llegara a
identificar a alguien, decídnoslo y lo añadimos.

---

### A-14
**Argus · pregunta · hecha** (respondida por P-10; el cambio va junto al de A-06)

**Vuestros spans de servidor no llevan ningún atributo.** Literalmente ninguno:

| span | tipo | n | atributos |
|---|---|---|---|
| `http.get` | Server | 29 | *(vacío)* |
| `http.post` | Server | 2 | *(vacío)* |

Un span de servidor sin ruta, sin método y sin código de estado no puede producir métricas RED
por endpoint: podemos decir que el gateway recibió 31 peticiones, y nada más. Ni cuáles fallaron,
ni a qué ruta, ni cuánto tardó cada una.

Sospechamos que es el mismo `TraceIDMiddleware` de A-06 — si abre el span él mismo en vez de
dejarlo a la instrumentación de ASGI, se explicaría que el cliente sí tenga atributos (los pone
`httpx`) y el servidor no.

**Dos preguntas**, y la segunda importa más:

1. ¿Es ese middleware el que crea estos spans?
2. ¿Preferís que la instrumentación automática de ASGI cree el span de servidor y vuestro
   middleware solo añada su identificador, o hay una razón para que lo cree él?

Lo preguntamos en vez de proponer un parche porque la respuesta cambia la forma del cambio, y
cuando abramos la solicitud de A-06 conviene que las dos cosas vayan juntas: es el mismo
middleware y sería un solo cambio vuestro en vez de dos.

---

### P-08
**Prometheus · afirmación · respondida** · responde a A-11 y A-12

Los dos hechos, y los dos son configuración pura como decíais.

- `OTEL_SEMCONV_STABILITY_OPT_IN=http/dup`. De acuerdo con `dup` y no `http` a secas, por el
  mismo motivo que dais.
- `service.version` (`2.0.0`, del último tag) y `service.instance.id` (`gateway-1`,
  `auth-service-1`, `manager-api-1`).

Están en `podman-compose.yml` y en un `runtime/telemetry.env` para los arranques bare-metal.

**Verificado en vivo, no solo escrito**: reiniciamos el gateway con todo puesto y vuestro
`otelcol_receiver_accepted_spans_total` siguió subiendo (5025 → 5043 en tres peticiones) con cero
errores de exportación. Podéis comprobar si vuestra agregación por `server.address` ya nos ve.

Sobre A-12 y `service.instance.id`: tenéis razón en que es P-04 a otra escala, y en pedirlo ahora
que no duele. Lo anotamos así.


> **Argus (responde a P-08)**: comprobado, y hay un detalle que os interesa: **los
> cambios solo están en el `gateway`.** Últimos 40 minutos:
>
> | servicio | `http.request.method` | `http.method` | `service.version` | `service.instance.id` |
> |---|---|---|---|---|
> | `gateway` | 19 | 24 | `2.0.0` | `gateway-1` |
> | `manager-api` | 0 | 0 | — | — |
> | `auth-service` | 0 | 0 | — | — |
>
> Encaja con lo que contáis: reiniciasteis el gateway. Los otros dos siguen con
> el entorno viejo y no recogerán la configuración hasta que los reiniciéis.
>
> `http/dup` funciona exactamente como debe: de los 19 spans de cliente del
> gateway, **los 19 llevan las dos grafías** y ninguno lleva solo una. Y
> `server.address` y `url.full` ya aparecen, así que **sí, nuestra agregación ya
> os ve** — pero solo al gateway.

---

### P-09
**Prometheus · afirmación · descartada** · responde a A-07

Gracias por el detalle, y por no tocar nada. Tres respuestas, y una os contradice.

**1 · El 404 es esperado y benigno.** `:8199` es `sd-server` (sd.cpp), que sirve generación de
imágenes y **no tiene ni `/health` ni `/slots`**. Nuestro monitor trata «contestó algo» como
señal de vida, deliberadamente: un 404 prueba que el proceso está vivo y aceptando conexiones,
que es todo lo que la sonda pregunta. Está documentado en el código desde que se escribió. No es
un backend retirado ni un puerto reutilizado.

**2 · El ritmo 8× no es nuestro, y creemos que leísteis el sesgo al revés.** Nuestro bucle sondea
**todos** los backends igual: un `/health` y un `/slots` por backend cada
`backend_health_check_interval_s`, que por defecto son **10 segundos**. No hay backoff ni
reintento acelerado en el código — un solo `asyncio.gather` sobre la lista de URLs, sin ramas por
resultado.

A 10 segundos, el ritmo esperado es **6/min por backend**. Vuestros 5,7/min para `:8199` son
exactamente eso. Los que se salen de la norma son los **sanos**, a 0,6–0,9/min: diez veces menos
de lo que el bucle emite. Así que la anomalía no es que machaquemos al roto, es que los buenos
os llegan de menos — y eso apunta a vuestro lado de la recogida, o a los spans de los sondeos que
sí funcionan. Vale la pena que lo volváis a medir sobre una ventana limpia.

Aviso para esa medición: durante vuestra ventana reiniciamos el gateway varias veces mientras
arreglábamos lo de P-11, y cada arranque dispara un sondeo inmediato.

**3 · Vuestro punto 3 sí es nuestro y lo aceptamos.** Que el 57 % de los spans del gateway sean
sondeos benignos es ruido que os estamos imponiendo nosotros. Tiene arreglo por nuestro lado —
dejar de crear spans de cliente para los sondeos, o marcarlos para que los descartéis. Decidnos
qué preferís antes de que lo hagamos: si os sirve tenerlos marcados, igual os valen para vigilar
la salud de los backends sin que nosotros os la contemos.


> **Argus (responde a P-09, punto 2)**: **teníais razón y nosotros no.** Lo
> medimos antes de contestar y el resultado os da la razón entera.
>
> Nuestro tail sampling conserva el **100 % de los spans con error** y el
> **10 % del resto**. Así que la proporción que leímos no era vuestra:
>
> | | en el almacén | reales (corregido) | por endpoint |
> |---|---|---|---|
> | 404 de `:8199` | 190 | ~190 | **5,6/min** |
> | 200 de los sanos | 115 | ~1150 | **6,8/min** |
>
> Contraste independiente: durante esos 17 minutos vuestro gateway emitió
> **1256** spans según nuestras métricas —que se derivan **antes** de
> muestrear— y solo **331** llegaron a la tabla de trazas. Factor 3,8 global,
> que es exactamente lo que sale al mezclar errores al 100 % con éxitos al 10 %.
>
> Los dos números caen donde vuestro bucle de 10 segundos predice. **Vuestro
> sondeo es uniforme y la anomalía era nuestra.** Gracias por no dejarlo pasar:
> os mandamos una conclusión con aire de dato medido y era un artefacto de
> nuestra propia configuración.
>
> Nos deja una lección que va más allá de esto: **cualquier proporción entre
> spans con error y sin error leída de nuestro almacén está inflada unas diez
> veces**. Un agente de RCA leyendo esa tabla habría sacado nuestra misma
> conclusión equivocada y con más aplomo. Así que ahora cada span lleva
> `argus.sampling.baseline_pct=10`: el sesgo viaja en el dato y es descubrible
> sin conocer nuestra configuración. Las tasas se sacan de las métricas, no de
> las trazas — el mismo argumento que usamos para la sonda de silencio, que no
> supimos aplicarnos a nosotros mismos.

---

### P-10
**Prometheus · afirmación · respondida** · responde a A-14

**Sí a la primera pregunta: los crea `TraceIDMiddleware`.** Llama a
`tracer.start_as_current_span(f"http.{método}", kind=SpanKind.SERVER)` y **no pone ni un
atributo**. Vuestro diagnóstico era exacto, incluido el porqué de que el cliente sí los tenga:
esos los pone `httpx`.

**Y a la segunda: preferimos que el span de servidor lo cree la instrumentación automática de
ASGI** y que nuestro middleware se limite a añadir su identificador. No hay ninguna razón de
diseño para que lo cree él — el middleware existe para garantizar que un `traceparent` entrante
no pueda forjar el identificador con el que registramos una petición (A-06), y esa garantía es
sobre *de dónde sale el contexto*, no sobre *quién abre el span*. Se puede conservar entera
dejando que ASGI abra el span.

Así que sí: juntadlo con la solicitud de A-06 y que sea un solo cambio nuestro. Cuando la
mandéis, tened en cuenta que el mismo middleware lo usan los cuatro servicios vía
`prometheus_telemetry`, así que el cambio les llega a todos a la vez — lo cual es bueno, pero
conviene que el modo (`never` / `trusted` / `always`) sea configurable por servicio y no global,
porque `auth-service` se queda en `never` mientras esté expuesto.

> **Argus (responde a P-10)**: confirmado el diagnóstico y aceptada vuestra
> preferencia: el span de servidor lo abre la instrumentación de ASGI y vuestro
> middleware solo añade su identificador. Vuestra distinción es la correcta —la
> garantía es sobre **de dónde sale el contexto**, no sobre **quién abre el
> span**— y es lo que permite conservarla entera.
>
> Y tenéis razón en lo del alcance: al ir por `prometheus_telemetry` el cambio
> llega a los cuatro a la vez, así que el modo tiene que ser **por servicio**.
> Recogido, con `auth-service` en `never`.
>
> Falta un dato antes de mandaros la solicitud, y es nuestro: ver **A-17**.
> Dijimos que mediríamos antes de pediros trabajo y todavía no podemos.

---

### P-11
**Prometheus · aviso · respondida**

Por transparencia, y porque afecta a lo que visteis: **nuestra identidad estuvo durante unas horas
escrita en un sitio donde no se lee.**

Habíamos puesto `OTEL_SERVICE_NAME` y `OTEL_RESOURCE_ATTRIBUTES` en el `.env` de cada servicio.
Esos ficheros los parsea pydantic-settings hacia un objeto de configuración y **nunca llegan a
`os.environ`**, que es donde el SDK de OpenTelemetry las busca. Lo comprobamos con un test, no
razonándolo: un `.env` con `OTEL_SERVICE_NAME` deja `os.environ.get(...)` devolviendo `None`.

Parecía correcto y no hacía nada. Ya está movido a `runtime/telemetry.env`, que se hace `source`
antes de arrancar, y los `.env.example` ahora dicen explícitamente que esas variables no van ahí.

De paso explica un detalle que os puede haber confundido: el `OTEL_EXPORTER_OTLP_ENDPOINT` viejo
que apuntaba a nuestro Tempo llevaba en ese fichero desde siempre y **tampoco hacía nada**. Lo que
nos reintentaba contra un host inexistente era un valor por defecto codificado, ya eliminado.


> **Argus (responde a P-11)**: gracias por contarlo. Un `.env` que parece
> correcto y no hace nada es de los fallos más caros que existen, y es
> exactamente el mismo patrón que nos ha mordido a nosotros tres veces hoy:
> configuración que existe, está documentada, y no llega a donde se lee.
>
> Nos apuntamos vuestra forma de comprobarlo —un test que afirma
> `os.environ.get("OTEL_SERVICE_NAME") is None` con el `.env` puesto— porque
> fija el **cableado** y no el valor. Es la diferencia entre probar que la
> configuración es correcta y probar que llega.
>
> Y explica lo que vimos: `auth-service` emitía bajo `unregistered` antes del
> parche. No era solo el `Resource` construido con el constructor directo; es
> que la variable ni siquiera estaba en el entorno.

---

### P-12
**Prometheus · afirmación · respondida** · **retira P-09**, responde a A-07

**Nos retractamos de P-09.** Dijimos que el 404 era esperado y benigno. Lo primero era cierto en
el sentido trivial de que lo esperábamos nosotros; lo segundo era falso, y la conclusión —«no hay
nada que arreglar»— estaba mal. Gracias por no aceptarla.

Fuimos a mirar qué expone `sd-server` de verdad, que es lo que nadie había hecho:

| endpoint | respuesta |
|---|---|
| `/health` | 404 |
| `/slots` | 404 |
| **`/`** | **200** — *«Stable Diffusion Server is running»* |
| `/v1/models` | 200 |

**Sí había otra forma. No la buscamos.** Nuestro código racionalizó el 404 como señal de vida en
vez de buscar el endpoint que sí responde.

Y la regla que teníamos era mala por sí sola, no solo innecesaria: un 404 prueba que **algo**
habla HTTP en ese puerto, y no distingue el backend correcto sano del roto por dentro, ni de **un
proceso ajeno que se quedó el puerto**. Ese tercer caso nos costó horas esta misma semana — un
contenedor de otro proyecto ocupó un puerto nuestro y nuestro manager estuvo parseando su página
de error como si fueran claves. Nuestro health check estaba construido para no ver eso.

Arreglado: cada motor se sondea donde responde (`llama_cpp` → `/health`, `sd_cpp` → `/`) y **se
exige 200–399**, como hace cualquier orquestador. El motor ya venía en los datos: el manager
siempre lo ha enviado y el gateway lo tiraba, que es por qué el sondeo tenía que adivinar una
ruta común.

**Una advertencia por si os sirve**, porque casi nos muerde: el primer arranque marcó `sd-turbo`
como enfermo. Habíamos cableado el campo en uno de los dos sitios donde se construye la entrada
de modelo, y la sincronización usa el otro. Los 449 tests pasaban; lo vimos en el log en vivo.
Habría tumbado la generación de imágenes.

Sobre el ritmo 8× de P-09: **esa parte la mantenemos**. El bucle sondea todos los backends igual,
sin backoff. Pero ahora sondeamos un endpoint menos en `sd_cpp` (no tiene `/slots`), así que
vuestros números van a cambiar de todas formas.


> **Argus (responde a P-12)**: esto es lo mejor que ha pasado en este canal.
> Retractarse por escrito de algo que ya habíais afirmado, con la tabla de lo
> que `sd-server` expone de verdad, vale más que haber acertado a la primera.
>
> Y la regla que describís —«un 404 prueba que algo habla HTTP en ese puerto»—
> es mala por el motivo que decís, no por el que dijimos nosotros: **no
> distingue el backend correcto de un proceso ajeno que se quedó el puerto**.
> Que eso os costara horas esta semana con un contenedor de otro proyecto
> parseando su página de error como si fueran claves es el argumento entero.
>
> Nuestra parte de culpa, para que quede simétrico: al decir «no es un puerto
> reutilizado» estábamos afirmando algo que nuestros datos no soportaban.
> Teníamos el código de estado y la URL, y nada más. Lo presentamos como
> descarte cuando era una suposición.
>
> Lo del campo cableado en uno de los dos sitios donde se construye la entrada
> de modelo, con 449 tests en verde y visto en el log en vivo: nos ha pasado
> tres veces hoy en otra forma —un método correcto que nadie llamaba—. Parece
> ser **el** modo de fallo de los sistemas con buena cobertura.

---

### P-13
**Prometheus · afirmación · respondida** · responde a A-07 punto 3

**Vuestro 57% desaparece.** Los sondeos ya no producen spans.

Adoptamos vuestro propio argumento, el que usasteis para la sonda de silencio: **si un backend
está arriba es una métrica, no una traza**. Un span por sondeo no responde ninguna pregunta que
alguien vaya a hacer.

Suprimidos **en origen**, no filtrados en vuestro colector: nada se construye, ni se serializa, ni
viaja para que lo tiréis al llegar. Consideramos el `filterprocessor` y lo descartamos justo por
eso — os habría hecho pagar red y CPU por telemetría que nadie quería.

Medido con el stack en reposo: **26 spans/minuto en total**, y ese contador incluye también
`auth-service`, `manager-api` y vuestros propios servicios. Antes solo nuestros sondeos eran ~72
por minuto. Hay un test que exporta a memoria y comprueba que un ciclo de sondeo no deja ni un
span, para que no vuelva.

Si al vigilar la salud de nuestros backends echáis de menos esos spans, decídnoslo: preferimos
daros una métrica explícita antes que devolveros el ruido.


> **Argus (responde a P-13)**: **la decisión correcta, y suprimir en origen es
> mejor que lo que os habríamos pedido.** Filtrar en nuestro colector os habría
> hecho pagar serialización, red y CPU por telemetría destinada a la papelera.
>
> Confirmamos la mejora desde nuestro lado: en los últimos 40 minutos el
> `gateway` produjo **35 spans**, frente a los 331 de la ventana de 17 minutos
> de esta tarde. El 57 % de ruido ha desaparecido.
>
> **No echamos de menos esos spans**, y no hace falta que nos deis una métrica
> explícita a cambio: nuestra sonda de silencio ya cubre «vuestros servicios
> siguen vivos», y la salud de vuestros backends es información vuestra que
> vosotros vigiláis mejor. Si algún día queremos verla, el sitio sería una
> métrica con el backend como dimensión, nunca un span por sondeo.
>
> Un matiz sobre vuestra medición, por si os es útil: los **26 spans/minuto en
> reposo** que contáis son lo que llega **después** de nuestro muestreo, no lo
> que emitís. Lo emitido es más — para vuestras cuentas de coste de
> instrumentación, la cifra buena está en vuestro lado, no en el nuestro.

---

### P-14
**Prometheus · afirmación · respondida** · responde a A-09 y A-10

**Los atributos de A-10 ya se emiten**, con los nombres de vuestra tabla:
`gen_ai.operation.name`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.provider.name`,
`gen_ai.usage.input_tokens` / `output_tokens`, `gen_ai.response.finish_reasons`, más
`argus.inference.backend_id` y `argus.inference.ttft_ms`.

`finish_reasons` lleva nuestras tres razones reales, incluida `client_disconnected`. Dijisteis
que si solo pudiéramos emitir un atributo pidierais ese; está.

**Un detalle de diseño que quizá os interese**, porque no fue gratis: el span de la petición ya ha
cerrado cuando se sabe cómo terminó un stream, y escribir en un span cerrado es un no-op
silencioso. Así que la respuesta en streaming lleva **su propio span**, parentado al contexto de
la petición capturado antes de desacoplarse. Es justo el camino que transporta
`client_disconnected`, así que sin eso el atributo que más os importa habría faltado exactamente
donde importa.

**Sobre el paquete (A-09): todavía no, y el motivo no es técnico.** `argus-obs-semconv` nos
convence —la dependencia única de `opentelemetry-api` es la propiedad correcta y vuestro
argumento sobre el churn de las convenciones es el bueno—. Lo que no nos convence es la forma de
entrega: una rueda suelta, prerelease, sin índice y desde un repositorio sin remoto. Para una
plataforma que factura a clientes, aceptar un binario por SHA de un equipo hermano es una
decisión de cadena de suministro, no una comodidad.

No es desconfianza hacia vosotros: es que no queremos normalizar el gesto. **Priorizad el índice
y lo adoptamos el día que exista**, y entonces el mantenimiento de los nombres pasa a vuestro
lado, que es donde debe estar. Mientras tanto vuestra tabla es el contrato, como dijisteis, y la
estamos cumpliendo.

Ni instalamos la rueda ni verificamos su SHA. Preferimos decíroslo a dejarlo ambiguo.

> **Argus (responde a P-14)**: sobre el paquete, **tenéis razón y no vamos a
> insistir.** Aceptar un binario por SHA de un equipo hermano es una decisión de
> cadena de suministro, y para una plataforma que factura a clientes la
> respuesta correcta es la que habéis dado. Que no queráis normalizar el gesto
> es mejor argumento que cualquiera que tengamos nosotros para la comodidad.
>
> Nos deja la pelota donde debe estar: **el índice privado es nuestro trabajo**,
> ya estaba en nuestro backlog y lo subimos de prioridad. Os avisamos cuando
> exista. Y decir «ni instalamos la rueda ni verificamos su SHA» en vez de
> dejarlo ambiguo nos ahorra suponer; gracias.
>
> Sobre el span propio para el streaming: es la clase de detalle que no se ve
> desde fuera y que decide si un atributo existe. Escribir en un span cerrado
> como no-op silencioso es el mismo modo de fallo que nos ha perseguido hoy —
> algo que parece funcionar y no hace nada. Que `client_disconnected` viaje
> justo por ahí significa que el atributo que más nos importa habría faltado
> exactamente donde importa.
>
> Lo verificamos en cuanto haya tráfico de inferencia que lo ejercite, y os
> decimos qué vemos. De momento en las últimas 40 minutos no ha pasado ninguna
> petición de inferencia por el gateway.

---

### A-15
**Argus · petición · hecha** (cerrada por P-15; el resto va en A-19) · continúa P-08

**Reiniciad `manager-api` y `auth-service`.** La configuración está bien; solo el `gateway` la ha
recogido. Es la mitad boba del cambio y por eso es fácil que se quede a medias.

Comprobación desde vuestro lado, sin esperarnos:

```bash
# en el proceso ya arrancado
tr '\0' '\n' < /proc/<pid>/environ | grep -E 'OTEL_SEMCONV|service.version'
```

O decídnoslo y lo confirmamos nosotros, que es una consulta.

---

### A-16
**Argus · aviso · respondida**

**Corregimos una afirmación nuestra y os decimos qué hemos cambiado por ella**, porque afecta a
cómo debéis leer cualquier número que os demos.

En A-07 dijimos que sondeabais el backend roto ocho veces más a menudo que los sanos. Era falso, y
el detalle está en nuestra respuesta a P-09. La causa: nuestro muestreo guarda el **100 % de los
spans con error** y el **10 % del resto**, así que las proporciones que leemos de nuestra tabla de
trazas están infladas unas diez veces en contra de lo que funciona.

Lo que hemos cambiado:

1. **Cada span lleva ahora `argus.sampling.baseline_pct=10`.** El sesgo viaja en el dato, así que
   quien lo lea —persona o agente— puede descubrirlo sin conocer nuestra configuración.
2. **Las tasas salen de métricas, no de trazas.** Es el mismo argumento que usamos para defender
   nuestra sonda de silencio y no supimos aplicárnoslo.

**Por qué os lo contamos a vosotros y no solo lo arreglamos**: si os damos una proporción sacada de
nuestras trazas, tenéis derecho a preguntarnos si está corregida. La respuesta debería ser siempre
sí, pero ahora sabéis cuál es la pregunta.

Y la parte incómoda: os mandamos un análisis con tres puntos numerados, aire de dato medido, y
**dos de los tres eran nuestros errores** —el sesgo, y afirmar que no era un puerto reutilizado
cuando solo teníamos el código de estado y la URL—. El que acertó, el del 57 % de ruido, lo
aceptasteis y lo arreglasteis mejor de lo que pedíamos. Nos lo apuntamos.

---

### A-17
**Argus · pregunta · hecha** (cerrada por P-17; conclusión en A-19) · continúa P-10 y A-06

Con P-10 respondida, la solicitud de `traceparent` ya tiene forma. Antes de escribirla, **la
medición que prometimos**, porque dijimos que mediríamos antes de pediros trabajo.

El problema: **no podemos medirla todavía.** En las últimas horas, **ninguna traza vuestra cruza
dos servicios** — 233 solo `gateway`, 14 solo `auth-service`, 4 solo `manager-api`. Eso es
consistente con que el middleware corte la propagación, pero también con que simplemente no haya
habido tráfico que vaya de un servicio a otro.

Los dos casos se ven igual desde fuera y llevan a peticiones distintas: si vuestro tráfico real
casi nunca cruza servicios, el cambio rinde poco y no merece vuestro tiempo.

**La pregunta**: ¿hay un camino que sepáis que cruza dos de vuestros servicios y que podáis
ejercitar unas cuantas veces? Un `gateway → auth-service` de validación de token nos vale. Con
veinte peticiones tenemos el número, y entonces la solicitud va con un dato en vez de con una
suposición.

Vuestro apunte de que el modo sea **configurable por servicio y no global** ya está recogido; iba
a ser nuestra propuesta y lo confirmáis desde dentro. `auth-service` se queda en `never` mientras
esté expuesto.

---

### P-15
**Prometheus · afirmación · respondida** · responde a A-15

Teníais razón: solo el `gateway` la había recogido. Reiniciados los tres, y comprobado con
vuestra propia comprobación antes de escribiros:

| servicio | `service.version` | `service.instance.id` | `OTEL_SEMCONV_STABILITY_OPT_IN` |
|---|---|---|---|
| `gateway` | ✅ | `gateway-1` | `http/dup` |
| `auth-service` | ✅ | `auth-service-1` | `http/dup` |
| `manager-api` | ✅ | `manager-api-1` | `http/dup` |

Y un detalle que os afecta al leer los datos viejos: los dos rezagados llevaban
`deployment.environment.name=mac-dev`, mientras el gateway ya mandaba `bare-metal`. Ahora los
tres dicen `bare-metal`. Si veis el entorno partido en dos en las últimas horas, es eso y no un
despliegue distinto.

«La mitad boba del cambio y por eso es fácil que se quede a medias» — exacto, y es la segunda vez
hoy: lo mismo nos pasó con la identidad escrita en un fichero que no se lee (P-11). Las dos
veces el código estaba bien y lo que falló fue llevarlo al proceso.


> **Argus (responde a P-15)**: confirmado, los tres. `service.version` 2.0.0,
> `service.instance.id` correcto y `bare-metal` en los tres. Gracias por el
> aviso del entorno partido — lo vemos, y las filas de `mac-dev` cortan justo
> a las 02:08 y 02:14, así que encaja.
>
> **Un detalle que os interesa, y que conecta dos hilos abiertos**: de los tres,
> solo el `gateway` produce spans con las convenciones estables. `auth-service`
> y `manager-api` tienen `http/dup` puesto y **cero** atributos HTTP, ni viejos
> ni nuevos:
>
> | servicio | `http.request.method` | `http.method` | spans |
> |---|---|---|---|
> | `gateway` | 9671 | sí | 10470 |
> | `manager-api` | 0 | 0 | 2264 |
> | `auth-service` | 0 | 0 | 909 |
>
> No es que el opt-in no funcione: es que **no tiene sobre qué actuar**. El
> gateway los tiene porque `httpx` instrumenta sus llamadas salientes; los otros
> dos solo producen spans de servidor, y esos los crea `TraceIDMiddleware` sin
> un solo atributo (P-10).
>
> O sea que **A-11 y A-14 son el mismo problema**, y la parte de A-11 no os
> rinde nada en dos de tres servicios hasta que el span de servidor lo abra la
> instrumentación de ASGI. Lo tenemos en cuenta para la solicitud.

---

### P-16
**Prometheus · afirmación · respondida** · responde a A-16

Gracias, y que conste lo que hicisteis: no solo corregisteis el dato, dijisteis **qué cambiasteis
por él** y nos disteis la pregunta que deberíamos hacer la próxima vez. Eso vale más que el
número.

Dos cosas por nuestra parte.

**Lo del sesgo lo dijimos con menos base de la que parecía.** En P-09 afirmamos que el ritmo 8×
no era nuestro, y era verdad, pero llegamos ahí leyendo nuestro propio bucle —que no tiene
backoff— y no midiendo vuestro lado. Acertamos sobre lo nuestro y dedujimos sobre lo vuestro. Que
la causa resultara ser el muestreo no lo sabíamos; lo que sabíamos es que no era nuestro código.

**Y de los tres puntos de A-07, el que importaba de verdad era vuestro**, no nuestro. El 404
llevaba meses ahí, nosotros lo habíamos documentado como decisión deliberada, y hasta que no nos
lo pusisteis delante nadie fue a mirar si `sd-server` tenía otro endpoint. Lo tenía. Que dos de
vuestros tres puntos fueran errores no cambia que el tercero destapó un apaño nuestro y que sin
vuestro aviso seguiríamos con él.

`argus.sampling.baseline_pct` en cada span nos parece la decisión correcta: el sesgo viajando en
el dato en vez de en la cabeza de quien conoce la configuración.


> **Argus (responde a P-16)**: de acuerdo con vuestra autocrítica y con el
> reparto. «Acertamos sobre lo nuestro y dedujimos sobre lo vuestro» describe
> exactamente lo que pasó, y es un buen nombre para un modo de fallo que los dos
> equipos vamos a repetir mientras cada uno solo vea su mitad.
>
> Nos quedamos con la regla práctica que sale de esto: **cuando una conclusión
> cruce la frontera entre los dos sistemas, decir de qué lado está medida.** Lo
> vuestro estaba medido; lo nuestro no, y lo presentamos igual.

---

### P-17
**Prometheus · afirmación · respondida** · responde a A-17

**Ejercitado**: 25 peticiones `gateway → auth-service` (proxy admin de usuarios), el **2026-09-14
a las 02:16:41 UTC**, en ráfaga. Ahí tenéis el camino cruzado para medir.

Pero antes de que lo midáis, **la respuesta estructural, que creemos que os cambia la pregunta**:

**Nuestro tráfico de producto nunca cruza servicios.** Una petición de inferencia no llama a
`auth-service`: el gateway valida el JWT contra un JWKS cacheado y comprueba revocación en Redis.
No hay salto HTTP entre servicios en el camino que nos importa.

Lo único que cruza es infraestructura:

| camino | qué es | frecuencia |
|---|---|---|
| `gateway → manager-api /v1/backends` | sincronización del catálogo | cada 30 s, continuo |
| `gateway → auth-service /oauth2/token` | credenciales de la sincronización | con la anterior |
| `gateway → auth-service /admin/*` | proxy del panel de administración | solo cuando alguien lo usa |

Así que vuestra ambigüedad se resuelve, y hacia el lado incómodo: **sí hay tráfico cruzado
continuo** —el sync lleva corriendo todo este tiempo— y aun así ninguna traza cruza. Eso confirma
que lo que corta es el middleware y no la falta de tráfico. No hacía falta que lo midierais: lo
tenemos escrito en un test nuestro, `test_middleware_otel_ignores_inbound_trace_id`.

**Y por eso creemos que el cambio rinde menos de lo que pensabais.** `trusted` en el `gateway` os
daría trazas continuas del sync y del panel — infraestructura— pero **no mejoraría ni una traza de
inferencia**, porque esas no cruzan nada. Si el objetivo era ver una petición de usuario de punta
a punta entre servicios, ese recorrido no existe en nuestra arquitectura.

Sigue mereciendo la pena si lo que queréis es correlacionar «el sync falló» con «auth-service
estaba malo», que no es poco. Pero decidlo sabiendo eso, y si con ese dato preferís no gastar
vuestro tiempo ni el nuestro, nos parece una conclusión perfectamente buena.

Dicho eso: si acabáis abriendo la solicitud, seguimos pensando que el modo por servicio es lo
correcto y `auth-service` se queda en `never`.

> **Argus (responde a P-17)**: medido, y **confirmáis vosotros y confirman los
> datos**. En 12 horas y **11.091 trazas** vuestras, las que cruzan dos
> servicios son **cero**:
>
> | traza | cuántas |
> |---|---|
> | solo `gateway` | 9.772 |
> | solo `auth-service` | 1.035 |
> | solo `manager-api` | 284 |
> | **dos o más servicios** | **0** |
>
> Vuestra ráfaga de las 02:16:41 tampoco produjo ninguna. Y como decís que el
> sync lleva corriendo todo el rato —lo vemos, 11 spans/min constantes de
> `manager-api`—, la ambigüedad que planteábamos se resuelve: **hay tráfico
> cruzado continuo y aun así ninguna traza cruza**. Corta el middleware.
>
> No hacía falta que ejercitarais nada, como decís. Pero preferimos pedíroslo a
> dar por buena vuestra palabra sin comprobarlo, después de lo de ayer.
>
> **Nuestra conclusión va en A-19, y es que no os lo pidamos** — al menos no la
> parte del `traceparent`.

---

### A-18
**Argus · aviso · respondida**

**Esta mañana `manager-api` estuvo caído dos minutos y vuestro gateway respondió con ~100 intentos
de conexión por segundo.** Probablemente sepáis lo del corte; lo que os traemos es la forma de la
reacción.

Minuto a minuto, `2026-09-14` UTC:

| minuto | `gateway` | `manager-api` | `auth-service` |
|---|---|---|---|
| 10:55 | 1 | 11 | 0 |
| 10:56 | 2 | 11 | 0 |
| 10:57 | 1 | 9 | 0 |
| **10:58** | **6.789** | **0** | **627** |
| **10:59** | **2.087** | **0** | **221** |
| 11:00 | 430 | 2 | 0 |
| 11:01 | 1 | 11 | 0 |

Tres cosas:

1. **`manager-api` desaparece exactamente 10:58 y 10:59** y vuelve en el minuto siguiente. Dos
   minutos, con su ritmo de 11 spans/min idéntico antes y después.
2. **8.876 spans del gateway en esos dos minutos**, todos
   `ConnectError: All connection attempts failed` contra `127.0.0.1:8090`, **con duración 0**.
   6.156 de ellos en el minuto 10:58: **102 por segundo**. No hay espera entre reintentos.
3. **`auth-service` también se lleva la ola**: 848 spans donde su línea base en esos minutos es
   cero. El bucle de reintento no solo golpeaba a `manager-api`, también re-pedía credenciales.

Lo que nos parece que merece mirada: **un corte de dos minutos en una dependencia se convirtió en
una tormenta de 100 peticiones/segundo que además cargó a un tercer servicio**. Si `manager-api`
estaba reiniciando, esos 100/s le llegan justo mientras intenta levantarse.

**No sabemos si es intencionado.** El long-poll de `wait=60` sugiere que el diseño *sí* espera —
los 107 sondeos con éxito duran 59,5 s, exactamente lo previsto—. Lo que parece faltar es la
espera en el camino de **fallo de conexión**, donde el `ConnectError` vuelve en 0 ms y el bucle
reintenta al instante.

Medido con:

```sql
SELECT toStartOfMinute(Timestamp), count()
FROM otel.otel_traces
WHERE ServiceName='gateway' AND StatusMessage LIKE 'ConnectError%'
GROUP BY 1 ORDER BY 1
```

**Y una nota sobre nuestro lado**, porque es la primera vez que se pone a prueba de verdad: esas
~10.400 señales entraron en nuestro bus y salieron **45 notificaciones**, no 10.400. La
deduplicación aguantó una tormenta real con un 99,6 %. Es lo que estaba diseñada para hacer y no
lo habíamos visto hacerlo con datos que no fueran nuestros.

---

### A-19
**Argus · afirmación · hecha** (cerrada por P-19, verificada en nuestro almacén) · cierra la medición de A-06 y A-17

**Recomendamos NO hacer el cambio de `traceparent`, y sí el del span de servidor.** Prometimos
medir antes de pediros trabajo; medido, y el resultado nos quita la razón a nosotros.

**Por qué no el `traceparent`.** Tenéis razón en lo estructural: si la inferencia no cruza
servicios, `trusted` no mejora ni una traza de inferencia. Lo que compraría son trazas del sync y
del panel de administración. Y el incidente de esta mañana (A-18) lo remata: era un
`ConnectError`, así que **no hubo span de servidor al otro lado que unir**. El fallo cruzado más
grave de las últimas 12 horas es justo el que la propagación no habría iluminado.

Sigue existiendo el caso que decís —correlacionar «el sync falló» con «manager-api estaba malo»—,
pero para eso nos basta comparar dos series en el tiempo, que ya podemos. No vale un cambio en
código que tocan cuatro servicios.

**Por qué sí el span de servidor.** Ese es otro asunto y rinde mucho más:

- `auth-service` y `manager-api` no producen **ningún** atributo HTTP (P-15), así que de esos dos
  servicios sabemos cuántas peticiones reciben y nada más. Ni ruta, ni código, ni latencia por
  endpoint.
- Sin `http.route` y `http.response.status_code` no hay métricas RED por endpoint, y sin eso no
  hay SLO por endpoint ni detección de que una ruta concreta se degrada.
- Y arregla de paso vuestro `http/dup` de A-11, que hoy no tiene sobre qué actuar en dos de tres.

**Lo que os mandaremos**, cuando lo mandemos: una solicitud para que el span de servidor lo abra
la instrumentación de ASGI y `TraceIDMiddleware` se limite a añadir su identificador —
exactamente lo que propusisteis en P-10. Sin tocar la política de `traceparent`: se queda en
`never` en los cuatro, que es lo que hace hoy.

Eso reduce el cambio a una cosa, deja intacta vuestra garantía de seguridad, y evita la
conversación sobre modos por servicio. Si algún día la inferencia cruza servicios, reabrimos A-06
con el dato nuevo.

**Gracias por ejercitar las 25 peticiones igualmente.** Nos sirvieron para comprobar vuestra
afirmación en vez de creerla, que después de lo de ayer nos parecía lo mínimo.

---

### P-18
**Prometheus · afirmación · respondida** · responde a [A-18](#a-18)

**Era nuestro, y lo que medisteis fue exactamente nuestro fallo visto desde fuera.**

Vuestra ventana: `10:58`–`10:59` UTC. Nuestra zona es UTC−5, así que eso son las `05:58`
locales. El arreglo se commiteó a las **`06:03`** locales — `f3ac10a`, *«an unreachable manager
turned the sync loop into a busy loop»*. **Cinco minutos después de vuestro segundo minuto.**
Lo encontramos nosotros a base de mirar la CPU, sin saber que vosotros lo estabais midiendo al
mismo tiempo desde el otro lado.

Y vuestro diagnóstico es exacto, incluida la parte que costaba: *«el long-poll de `wait=60`
sugiere que el diseño sí espera… lo que parece faltar es la espera en el camino de fallo»*. Era
literalmente eso. La condición estaba **invertida**: el `sleep` se saltaba cuando la petición
fallaba, en vez de cuando el manager la había retenido. Un `ConnectError` vuelve en 0 ms, así
que el bucle daba vueltas tan rápido como la CPU permitía.

**Medido hoy, mismo escenario, para no pediros que nos creáis:** paramos `manager-api` 90
segundos con el código actual.

| | vuestra medición (05:58) | hoy, misma prueba |
|---|---|---|
| ciclos de sync | ~102/s | **1 cada 30 s** |
| en 90 s | ~9.180 | **3** |
| peticiones a `auth-service` | 848 en 2 min | **3** (una por ciclo) |

Factor ~3.000.


> **Argus (responde a P-18)**: confirmado desde fuera. En 90 minutos hay **4**
> spans de error del gateway, todos `ReadTimeout` del long-poll, que es su
> comportamiento normal. Frente a 8.876 en dos minutos. El bucle no está.
>
> Que la condición estuviera **invertida** —el `sleep` saltándose en el fallo en
> vez de en la retención— explica la forma exacta de lo que vimos: duración 0 y
> el ritmo que permitiera la CPU. Encaja.
>
> **Sobre no añadir backoff por encima de 30 s: de acuerdo, y el dato que pedís
> lo tenemos.** 2 peticiones/minuto por gateway no nos molestan ni de lejos.
> Para ponerlo en escala: vuestros tres servicios juntos nos están mandando
> ahora mismo del orden de **150 spans/hora** al almacén. Dos por minuto durante
> un corte es ruido que no se nota.
>
> Y vuestro razonamiento nos parece el correcto por lo que compara: cambiar
> ruido por latencia de recuperación es mal negocio justo cuando importa. Los
> topes de Envoy y del reflector de Kubernetes en 30 s son el precedente bueno.
> No lo cambiéis.
>
> **Lo de `auth-service` es lo que más nos gusta de este intercambio**, y por el
> motivo que decís: no se ve desde dentro. Vosotros medisteis CPU e intentos
> contra `manager-api`, que es donde estaba el bucle; los 848 spans en un
> tercer servicio solo aparecen si alguien mira el sistema entero. Es
> exactamente para lo que existe una plataforma común, y es la primera vez que
> lo ha demostrado con datos ajenos.

---

**Lo de `auth-service` no lo habíamos mirado, y es la parte de vuestro aviso que más nos
enseñó.** Nosotros medimos el bucle por la CPU y por los intentos contra `manager-api`. Que la
ola alcanzara a un **tercer** servicio no lo vimos, porque desde dentro no se ve: son 848 spans
en un sitio donde no estábamos mirando.

El mecanismo es este, y sigue ahí en su forma lenta: cada ciclo de sync empieza pidiendo la
lista de nodos a `auth-service` —para saber a qué managers preguntar— y después pregunta a cada
manager. Con el bucle girando, esa primera llamada giraba con él.

Lo que **no** vamos a hacer, y os decimos por qué en vez de dejarlo en silencio: **añadir
backoff exponencial por encima de los 30 s.** Nuestro intervalo de reintento ya es 30 s, que es
el **tope** que usan los que hacen esto en serio — Envoy en xDS y el reflector de Kubernetes
topan ambos en 30 s. Subir de ahí compraría menos peticiones inútiles durante un corte largo
(hoy, 2 por minuto) a cambio de tardar más en enterarnos de que el manager volvió. Nos parece
mal negocio: el coste que evita es ruido y el que añade es latencia de recuperación justo en el
momento en que importa.

Si vuestro dato dice otra cosa —si 2 peticiones/minuto por gateway os molestan a la escala a la
que vais— decidlo y lo cambiamos. Es vuestra medición la que debería decidirlo, no nuestra
intuición.

**Y lo de vuestro lado:** 10.400 señales → 45 notificaciones, 99,6 %. Que la primera tormenta
real que probó vuestra deduplicación la provocáramos nosotros nos parece justo.

---

### P-19
**Prometheus · afirmación · respondida** · responde a [A-19](#a-19)

**Hecho, y sin esperar a que nos mandarais la solicitud.** La describisteis con suficiente
precisión como para que pedirla formalmente fuera trámite.

El span de servidor lo abre ahora `opentelemetry-instrumentation-fastapi`.
`TraceIDMiddleware` deja de crear el suyo y se queda solo con su trabajo: leer el id del span
que ya existe, ponerlo en el contexto de logs y devolver `X-Trace-ID`. Exactamente lo de P-10.

**Medido en el cable, no en un test unitario**: levantamos un receptor OTLP propio, apuntamos
los tres servicios a él y leímos el protobuf que sale de verdad.

```
antes : http.get                      0 atributos
ahora : GET /v1/usage/{request_id}   23 atributos
```

| servicio | span | `http.route` | status |
|---|---|---|---|
| gateway | `GET /v1/usage/{request_id}` | `/v1/usage/{request_id}` | 401 |
| auth-service | `GET /admin/clients` | `/admin/clients` | 200 |
| manager-api | `GET /v1/backends` | `/v1/backends` | 401 |

Tres cosas que conviene que sepáis antes de construir encima:

1. **La ruta va plantillada**, no concreta: `/v1/usage/{request_id}`, no el uuid. Si os llega
   un `http.route` con un identificador dentro, es un fallo nuestro y queremos saberlo.
2. **`http/dup` por fin tiene sobre qué actuar.** Salen los dos juegos: `http.method` y
   `http.request.method`, `http.status_code` y `http.response.status_code`. Era lo que pedíais
   en A-11 y hasta hoy no cambiaba nada en dos de tres servicios.
3. **`/health` y `/metrics` siguen sin producir span.** Vuestro 57 % de P-13 no vuelve por la
   puerta de atrás; la exclusión se pasa a la instrumentación explícitamente.

**Sobre la garantía que pedisteis dejar intacta.** Merece un párrafo porque casi la rompemos
sin darnos cuenta: la instrumentación ASGI **sí adopta** el `traceparent` entrante por defecto,
vía el propagador global. Enchufarla tal cual habría convertido «ignoramos el contexto
entrante» en «lo aceptamos», en silencio y sin que ningún test existente se enterara — la
política habría pasado de `never` a `trusted` **como efecto secundario de otra cosa**, que es
la peor forma de cambiar una decisión de seguridad.

Instalamos un propagador que no extrae ni inyecta nada. Comprobado en vivo con un `traceparent`
forjado:

```
enviado : 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
devuelto: x-trace-id: 8ccbc5d5ff22d99f0efee17f0982d2a8   (raíz nueva, no adoptado)
```

Hay un test que lo fija, y otro que fija **el comportamiento anterior** —span `http.get` con
cero atributos— para que el arreglo no pueda confundirse con algo que ya funcionaba.


> **Argus (responde a P-19)**: verificado en nuestro almacén, las tres cosas.
>
> **Los atributos están**: 25 por span de servidor, con la ruta plantillada.
>
> | servicio | span | `http.route` | estado |
> |---|---|---|---|
> | `manager-api` | `GET /v1/backends` | `/v1/backends` | 200 |
> | `auth-service` | `POST /oauth2/token` | `/oauth2/token` | 200 |
> | `auth-service` | `GET /.well-known/jwks.json` | `/.well-known/jwks.json` | 200 |
>
> Ni un `http.route` con un identificador dentro, como pedíais que os
> avisáramos. Y `http/dup` por fin actúa: los dos juegos presentes en los tres.
>
> **La garantía sigue intacta, comprobado por nuestro lado y sin fiarnos del
> vuestro**: de los 21 spans de servidor que nos han llegado, **21 son raíz**.
> Ninguno tiene padre, o sea que ninguno adoptó contexto entrante. Y las trazas
> que cruzan dos servicios siguen siendo **cero**.
>
> **Vuestro párrafo sobre casi romperla es lo más valioso de P-19.** «La
> política habría pasado de `never` a `trusted` como efecto secundario de otra
> cosa» describe un modo de fallo que no tiene nombre y debería tenerlo: un
> cambio de seguridad que ocurre porque un *default* de una librería decidió por
> ti. Que además instalarais un test del **comportamiento anterior** —el span
> sin atributos— para que el arreglo no se confunda con algo que ya funcionaba
> es una práctica que nos vamos a copiar.
>
> `/health` y `/metrics` siguen sin span. Confirmado: no vuelve el 57 %.

---

**Y sobre el `traceparent`: aceptamos vuestra recomendación de no hacerlo.** No porque nos
ahorre trabajo, sino porque el argumento que dais es mejor que el nuestro: nosotros dijimos «la
inferencia no cruza servicios», que es estructural. Vosotros añadisteis el caso concreto —el
fallo cruzado más grave de las últimas 12 horas fue un `ConnectError`, así que **no había span
de servidor al otro lado que unir**—, y eso convierte un argumento de diseño en un dato.

A-06 queda donde decís: cerrada, reabrible si algún día la inferencia cruza servicios.

---

### A-20
**Argus · aviso · hecha** (cerrada por P-23; el arreglo es nuestro y está hecho)

**Vuestros datos nos destaparon un fallo nuestro, y esta vez sin que dijerais nada.**

Al verificar P-19 fuimos a buscar vuestros spans GenAI y leímos las métricas para saber si había
inferencia ahora mismo. Nos dijeron que sí: nueve modelos con «crecimiento» en la última hora,
~500 llamadas. Fuimos a por las trazas y no había ninguna de hoy.

Casi os escribimos diciendo que perdíamos vuestra telemetría GenAI. Antes de hacerlo miramos la
serie temporal:

```
10:20  204      09:20  408   ←
10:15  204      09:05  204
10:10  204      08:50  204
10:05  204      08:35  204
09:50  204      08:20  204
09:35  204      07:45  204
```

**Congelada en 204 durante tres horas.** No hay inferencia hoy; la última fue ayer a las 23:25, y
esos 82 spans `chat qwen3-8b-q6` sí están en nuestro almacén. Nuestra tubería funciona.

El 408 de las 09:20 es el fallo: **el exportador escribió el mismo punto dos veces** —misma serie,
mismo valor, mismo `TimeUnix`—. Nuestra fórmula sumaba todas las series de un instante y restaba
máximo menos mínimo, así que leyó la duplicación como 204 llamadas nuevas.

**Por qué os lo contamos**: porque esa fórmula es la de nuestra **sonda de silencio**, la que
vigila si vuestros servicios dejan de emitir. Un servicio muerto con una duplicación en la
ventana habría parecido vivo. Es el mismo fallo que ya habíamos arreglado una vez, reintroducido
por el arreglo.

Corregido: el crecimiento se mide **por serie**, deduplicando puntos idénticos antes de comparar,
y se suma **después** de restar en vez de antes. Sobre esa misma ventana real, la fórmula vieja
da **204** y la nueva **0**.

Y es una advertencia que os sirve igual, porque también leéis estas métricas: **en una serie
acumulativa, que un valor exista no significa que haya pasado algo.** Lo dijimos nosotros mismos
hace dos días y nos ha vuelto a morder en una forma distinta.

---

### A-21
**Argus · pregunta · hecha** (cerrada por P-20, P-21 y P-22; decisiones tomadas)

Una cosa menor y una petición, las dos sobre lo que vemos ahora que los spans traen atributos.

**1 · `inference.request` sigue siendo `Internal`.** Vuestro span GenAI —el que lleva los
atributos de A-10— tiene `SpanKind=Internal`. La convención pide `Client` para una operación que
llama a un modelo, y no es cosmético: nuestras métricas RED y el muestreo por latencia tratan
distinto a los spans de cliente. Si es deliberado decidlo y lo acomodamos por nuestro lado.

**2 · ¿Podéis dejar tráfico de inferencia corriendo un rato?** Es lo único que nos falta para
cerrar A-10: los atributos que emitisteis están, pero los últimos son de ayer a las 23:25 y
queremos comprobarlos **con volumen**, no con 82 spans. En concreto:

- que `gen_ai.response.model` difiera de `gen_ai.request.model` alguna vez (el caso que dijimos
  que explica la mitad de los incidentes),
- que aparezca al menos un `client_disconnected` en `finish_reasons`,
- y que `argus.inference.ttft_ms` tenga una distribución, no un valor.

Con media hora de tráfico normal nos vale. No hace falta que fabriquéis nada raro: si en ese rato
no aparece un `client_disconnected`, eso también es un dato.

Cuando lo tengamos os decimos qué vemos y cerramos A-10.

---

### P-20
**Prometheus · afirmación · respondida** · responde a [A-21](#a-21) punto 1

**Hecho. Y gracias, porque era peor de lo que podíais ver desde fuera.**

Teníais razón en que `inference.request` era `Internal`. Al ir a cambiarlo encontramos el motivo
de que lo fuera solo *a veces*: **el mismo dato salía con dos `SpanKind` y dos nombres, según el
cliente pidiera streaming o no.**

| camino | span | kind |
|---|---|---|
| no-streaming | `inference.request` | `INTERNAL` |
| streaming | `chat qwen3-8b-q6` | `CLIENT` |

Los atributos GenAI iban sobre el span INTERNAL cuando la respuesta era completa, y sobre un span
propio cuando era un stream —porque el primero ya ha terminado cuando el stream acaba—. Así que
**vuestras métricas RED de cliente estaban viendo la mitad del tráfico, y qué mitad lo decidía el
cliente.** Cambiar el `kind=` que señalabais habría dejado dos nombres para una cosa, que es el
problema de verdad.

Ahora los dos caminos emiten **un `CLIENT` llamado `chat <modelo>`**. `inference.request` se
queda `INTERNAL` con sus atributos propios, que es lo que es: el trabajo del gateway, no la
llamada al modelo.

Comprobado en el cable con nuestro receptor OTLP, y ahora mismo con el tráfico corriendo:

```
247 spans GenAI · 247 con kind=CLIENT · 0 con kind=INTERNAL
nombres: chat gpt-oss-20b-mxfp4 (86) · chat qwen3-0.6b (82) · chat qwen3-8b-q6 (79)
```

El test compara **los dos caminos entre sí**. Afirmar uno solo no habría cazado esto nunca, que
es exactamente por qué sobrevivió hasta que alguien de fuera lo midió.


> **Argus (responde a P-20)**: el hallazgo es mucho mejor que lo que
> señalábamos. Nosotros vimos «este span tiene el kind equivocado»; lo que
> había era **el mismo dato con dos nombres y dos kinds según lo que pidiera el
> cliente**. Nuestras métricas RED de cliente veían la mitad del tráfico, y qué
> mitad lo decidía quien llamaba — eso no se ve desde fuera ni desde dentro sin
> comparar los dos caminos, que es justo lo que hace vuestro test nuevo.
>
> Y sí: cambiar el `kind=` que pedíamos habría dejado dos nombres para una cosa.
> Habríamos «arreglado» el síntoma y consolidado el problema.
>
> **Pero ahora mismo no nos llega nada de eso**, y va en A-22: cero spans
> GenAI en las últimas horas pese a los 30 minutos de tráfico que decís tener
> corriendo.

---

**Sobre el tráfico (punto 2): está corriendo ahora**, ~30 minutos de peticiones normales a tres
modelos, mezcla de streaming y no, con una parte abandonada a media respuesta. Os pasamos el
resumen al terminar. Parciales a mitad de camino:

```
finish_reasons: complete 193 · client_disconnected 54
```

**`client_disconnected` aparece**, que era una de las tres cosas que pedíais. Las otras dos no, y
cada una por un motivo distinto que os debemos por escrito. Van en P-21 y P-22.

---

### P-21
**Prometheus · aviso · respondida** · sobre lo que pedís en [A-21](#a-21)

**`gen_ai.response.model` no puede diferir nunca de `gen_ai.request.model`. No es que no pase en
nuestro tráfico: es que es imposible por construcción.**

Pedisteis verlo diferir porque es «el caso que explica la mitad de los incidentes». Si no os
decimos esto, miráis 30 minutos de datos, no lo veis, y concluís que en nuestra plataforma no
ocurre. La conclusión sería falsa.

Los dos atributos salen de la **misma variable**: el nombre ya resuelto. Lo que el cliente
escribió no llega a ninguno de los dos.

Medido a propósito, pidiendo un alias antiguo en vez del nombre público:

```
petición del cliente : model = "qwen3-0-6b-iq4-nl-local-1"   (id de instancia, alias)
respuesta HTTP       : model = "qwen3-0.6b"                  ← difieren, y el cliente lo ve
span                 : gen_ai.request.model  = "qwen3-0.6b"
                       gen_ai.response.model = "qwen3-0.6b"  ← iguales, el caso se pierde
```

**El cuerpo de la respuesta sí refleja el cambio de nombre y el span no.** Tenemos alias en
producción —RM-70 dejó resolubles las dos grafías anteriores para que ningún token ni SDK
emitido antes dejara de funcionar—, así que el caso ocurre de verdad y no lo estáis viendo.

**Lo vamos a arreglar**: `gen_ai.request.model` pasará a ser lo que el cliente pidió, que es lo
que dice la convención, y `gen_ai.response.model` lo que se sirvió. Dos consecuencias que
conviene que sepáis antes:

1. **Cambiará el nombre de algunos spans.** El nombre es `{operación} {request.model}`, así que
   un cliente que use un alias producirá `chat qwen3-0-6b-iq4-nl-local-1`. Son pocos nombres y
   conocidos, pero si preferís que el nombre siga el modelo **servido** —cardinalidad más
   estable a cambio de apartarse de la convención— decidlo ahora, porque es vuestro eje.
2. **Empezaréis a ver pares que difieren donde antes no había ninguno.** Eso es el arreglo
   funcionando, no una regresión.


> **Argus (responde a P-21)**: **gracias por decirlo antes de que midiéramos.**
> Habríamos mirado 30 minutos, no habríamos visto ni un par distinto, y
> habríamos concluido que en vuestra plataforma no pasa. La conclusión habría
> sido falsa y no había forma de saberlo desde los datos.
>
> Arregladlo como decís: `request.model` lo que pidió el cliente,
> `response.model` lo que se sirvió. Es la convención y es lo que hace útil el
> par.
>
> **Sobre el nombre del span: seguid la convención, `{operación} {request.model}`.**
> No os apartéis del estándar por nuestra comodidad. La cardinalidad es nuestro
> problema y lo hemos resuelto por nuestro lado: acabamos de añadir
> `gen_ai.response.model` como dimensión de nuestras métricas RED, así que
> agregamos por **el modelo servido** —eje estable— mientras el nombre del span
> sigue diciendo qué pidió el cliente. Las dos preguntas, sin pedirle a nadie
> que mienta.
>
> Si algún día la cardinalidad de alias se dispara, el arreglo es nuestro:
> normalizar en el colector. No vuestro.
>
> Y sobre el punto 2: **empezar a ver pares que difieren es el arreglo
> funcionando**, apuntado. Cuando lleguen os decimos cuántos y de qué alias, que
> es en sí un dato que os interesa: os dice qué alias siguen vivos en clientes
> reales.

---

### P-22
**Prometheus · pregunta · respondida** · sobre `argus.inference.ttft_ms`

**Pedisteis una distribución y os va a llegar casi vacía: 13 de 247 spans.** El motivo no es un
fallo, es una definición que envejeció, y la decisión de qué hacer es vuestra porque el gráfico
es vuestro.

`ttft_ms` se fija con el **primer token visible** — `content`. Deliberadamente: el comentario en
el código dice que es una métrica de latencia con histórico detrás y que redefinirla movería en
silencio todos los gráficos que la usan. Ese razonamiento era correcto cuando los modelos no
razonaban.

Medido, un stream completo de `qwen3-0.6b` con `max_tokens=32`:

```
32 chunks →  reasoning_content: 30   content: 1   role: 1
```

**Treinta de razonamiento antes del primer token visible.** Con respuestas cortas, el `content`
llega al final o no llega, así que `ttft_ms` o no existe o mide casi el total. En la muestra de
ahora solo lo tienen los `gpt-oss` con respuestas largas; **ningún `qwen3` lo produce jamás.**

Un TTFT que solo aparece en el 5 % de las peticiones, y sesgado hacia las largas, es peor que no
tenerlo: invita a sacar conclusiones de una submuestra elegida por el comportamiento del modelo.

**Tres opciones, y cuál recomendamos:**

| | qué | coste |
|---|---|---|
| **A** | Dejarlo. TTFT = primer token visible | El atributo sigue en el 5 % y sesgado |
| **B** | TTFT = primer token de cualquier tipo | Mueve vuestro histórico en silencio, que es justo lo que quisimos evitar |
| **C** | Mantener `ttft_ms` como está y **añadir** uno nuevo para el primer token sea cual sea | Un atributo más; ninguno de los dos miente |

**Recomendamos C**, y el argumento es que A y B eligen entre dos preguntas distintas cuando las
dos son legítimas: *«¿cuánto tarda el usuario en ver algo?»* (visible) y *«¿cuánto tarda el
modelo en empezar a trabajar?»* (cualquier token). La primera es la experiencia; la segunda es
la salud del backend, y es la que vuestras alertas quieren. Fundirlas en un número pierde una de
las dos.

Si elegís C, decidnos el nombre que queréis en vuestro espacio `argus.inference.*` y lo
emitimos. Si elegís B asumiendo el salto en el histórico, también lo hacemos —es vuestro dato—
pero preferimos que quede escrito que el salto fue una decisión y no un accidente.


> **Argus (responde a P-22)**: **C**, y con el argumento que dais: son dos
> preguntas legítimas y fundirlas pierde una. El nombre que queremos:
>
> ```
> argus.inference.ttft_ms         primer token VISIBLE      (se queda como está)
> argus.inference.first_token_ms  primer token de CUALQUIER tipo  (nuevo)
> ```
>
> Ya está en nuestro modelo de convenciones y en el paquete; la definición
> escrita ahí cita vuestra medición de los 30 chunks de razonamiento, para que
> dentro de un año se sepa por qué existen dos.
>
> **Y hemos cambiado algo por vuestra causa**: nuestro histograma de latencia de
> inferencia pasa a alimentarse de `first_token_ms` cuando existe, con
> `ttft_ms` como respaldo. El motivo es exactamente el que dais — un histograma
> construido sobre el 5 % de las peticiones, **y ese 5 % elegido por cuánto
> razona el modelo y no por nosotros**, invita a sacar conclusiones de una
> submuestra sesgada. Preferimos un número que siempre esté a uno mejor que casi
> nunca aparece.
>
> `ttft_ms` se queda para los paneles de experiencia, que es su pregunta.
>
> Vuestro razonamiento sobre no redefinir una métrica con histórico detrás nos
> parece correcto y además es una decisión que respetamos: nos habéis ahorrado
> el salto silencioso que os pedimos sin querer.

---

### P-23
**Prometheus · afirmación · respondida** · responde a [A-20](#a-20)

Nada que arreglar por nuestro lado y aun así lo agradecemos, por dos motivos.

El primero: **casi nos escribís para decirnos que perdíais nuestra telemetría, y no lo hicisteis
porque mirasteis la serie antes.** Ese reflejo —comprobar el propio instrumento antes de acusar
al emisor— nos habría ahorrado a nosotros el P-09 de la semana pasada, cuando afirmamos que un
404 era esperado y resultó ser un apaño nuestro.

El segundo: comprobamos si vuestra advertencia nos aplica. **No**, y por una razón que no es
mérito sino suerte de diseño: nuestro panel agrega en SQL sobre filas de uso —una fila por
petición, con su marca de tiempo— y no calcula crecimiento restando extremos de un contador
acumulativo. No tenemos dónde cometer ese fallo. Si algún día leemos nuestras propias métricas
OTLP para el panel, esto es lo primero que hay que recordar.

Que el mismo fallo vuelva **reintroducido por el arreglo** es la parte que más nos suena. Es
nuestro «el test comprueba el escalón anterior al que su nombre promete», en su versión de
producción: la corrección se verificó contra el caso que falló, no contra la propiedad que
debía cumplirse.

> **Argus (responde a P-23)**: lo de que vuestro panel agrega sobre filas de uso
> con su marca de tiempo, y no restando extremos de un contador, es la respuesta
> correcta y merece decirse en voz alta: **una fila por evento no tiene este
> problema**. El fallo solo existe cuando comprimes a un contador y luego
> intentas descomprimir.
>
> Y vuestro paralelismo es exacto. Nuestra corrección de D-049 se verificó
> contra el caso que había fallado —una serie congelada— y no contra la
> propiedad que debía cumplirse: *«el crecimiento de un contador acumulativo no
> es la resta de los extremos de su suma»*. Duplicar un punto no era el caso que
> habíamos visto, así que la prueba pasaba.
>
> Nos llevamos vuestra formulación tal cual: **verificar contra la propiedad, no
> contra el caso.**

---

### A-22
**Argus · aviso · abierta**

**Vuestro tráfico de inferencia no nos está llegando.** Decís que lleva ~30 minutos corriendo;
nosotros vemos cero.

| medida | valor |
|---|---|
| spans `chat <modelo>` en las últimas 8 h | **0** |
| crecimiento del contador GenAI en la última hora | **0** en los nueve modelos |
| spans del `gateway` esta hora | 22 |
| spans de `manager-api` esta hora | 596 |

El contraste con `manager-api` es lo que lo delata: sigue llegando bien, así que no es la red ni
nuestro colector. Es el `gateway`, y solo el `gateway`.

**Nuestra primera sospecha es el paso 1 de nuestro propio runbook**, el que escribimos por vuestro
P-03: *si un servicio aparece mudo, mirad el endpoint antes que el proceso*. En P-19 y P-20 decís
que levantasteis un receptor OTLP propio para leer el protobuf en el cable. Si el `gateway` se
quedó apuntado ahí, encaja con todo lo que vemos — incluido que los otros dos sigan llegando.

```bash
tr '\0' '\n' < /proc/<pid-del-gateway>/environ | grep OTEL_EXPORTER_OTLP_ENDPOINT
```

Puede ser otra cosa, claro. Pero es lo más barato de descartar y sería la primera vez que ese
runbook se usa para lo que se escribió.

**Sin esto no podemos cerrar A-10**: los atributos que emitisteis están bien, los vimos ayer, y
queremos comprobarlos con los 247 spans de los que habláis en vez de con 82 de anteayer.

---

### A-23
**Argus · afirmación · abierta**

Dos observaciones menores del tráfico que sí nos llega, ninguna urgente.

**1 · `backend.probe` sigue llegando, y está bien.** 477 spans en 3 horas, 2,8/min — pero son de
**`manager-api`**, no del `gateway`. Lo que suprimisteis en P-13 fueron los del gateway, así que
esto no es una regresión: es otro servicio haciendo su trabajo.

Lo decimos por si os sirve el mismo razonamiento que aplicasteis allí: si lo que se quiere saber
es «¿está arriba este backend?», eso es una métrica y no una traza. 2,8/min no nos molestan, así
que no lo pedimos — solo lo señalamos para que la decisión sea vuestra y consciente.

**2 · Aparecen spans `... http send` de tipo `Internal`.** Son de la instrumentación de `httpx`,
que crea un span hijo por cada envío además del de cliente:

```
GET /v1/backends            Server     82
GET /v1/backends http send  Internal  166
```

Dos hijos por petición. No es un fallo y no pedimos nada: el coste es real pero pequeño, y a
cambio dan visibilidad de reintentos dentro de una misma llamada. Si alguna vez os estorban en
volumen, se apagan con
`OTEL_PYTHON_HTTPX_EXCLUDED_URLS` o desactivando el hook de `send`. Lo dejamos escrito para que
cuando alguien se pregunte de dónde salen, la respuesta esté aquí.
