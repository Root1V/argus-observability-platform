# Configurar un canal de notificación

Es lo único que bloquea el piloto. La plataforma ya detecta perfectamente;
ahora mismo lo cuenta por consola y por un log JSON, **que nadie mira a las tres
de la mañana**.

Son **dos cosas**, y confundirlas es el error que más cuesta ver:

| | Qué es | Dónde |
|---|---|---|
| **1. Cargar el canal** | Las credenciales del canal | `platform/.env` |
| **2. Enrutar hacia él** | Qué aplicación avisa por dónde | `platform/registry/apps.yaml` |

Con solo la primera, el canal está cargado y **no lo usa nadie**: el aviso sale
por consola, todo parece correcto, y a las tres de la mañana no suena nada. Las
entradas del registro que trae el repositorio ya listan `gchat` y `email`, y los
que no estén configurados se omiten — así que en la práctica basta con el `.env`.

```bash
make channel-test     # comprueba las dos, y qué canal entregó de verdad
```

---

## Google Chat — el más rápido

Cinco minutos, sin aprobación de nadie y sin coste por mensaje. Además es el
único canal con **botones**, así que es donde más adelante aprobarás
remediaciones sin salir del chat.

### 1. Crear el webhook

1. Abre **Google Chat** en el navegador (`mail.google.com/chat`).
2. Entra en el espacio donde quieras los avisos. Si no tienes uno, créalo: *+* →
   **Crear espacio**. Vale un espacio contigo solo para empezar.
3. Pulsa el **nombre del espacio** (arriba) → **Apps e integraciones**.
4. **Webhooks** → **Añadir webhook**.
5. Nombre: `Argus`. Avatar: opcional.
6. **Guardar** y **copia la URL**. Tiene esta pinta:
   ```
   https://chat.googleapis.com/v1/spaces/AAAA.../messages?key=...&token=...
   ```

> Si no ves *Apps e integraciones*, tu administrador de Workspace tiene los
> webhooks desactivados. En ese caso, salta al correo.

### 2. Ponerlo en la configuración

En `platform/.env`:

```bash
ALERTBUS_SINKS=console,json,gchat
ALERTBUS_GCHAT_WEBHOOK=https://chat.googleapis.com/v1/spaces/AAAA.../messages?key=...&token=...
```

### 3. Aplicar y probar

```bash
docker compose -f platform/compose.yaml --profile lean up -d alert-bus
make channel-test
```

Deberías ver llegar **un** mensaje al espacio, que luego **se actualiza** con el
informe. Si llegan dos, la divulgación progresiva no está funcionando y merece
mirarlo.

---

## Correo — para el informe completo

Es el canal del informe largo: sin límite de longitud, sin urgencia, y con la
evidencia entera. Útil cuando alguien se sienta a investigar.

### Con Gmail

Necesitas una **contraseña de aplicación**, no tu contraseña normal:

1. Ten la verificación en dos pasos activada en tu cuenta de Google.
2. Ve a [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
3. Crea una para «Argus» y copia los 16 caracteres.

En `platform/.env`:

```bash
ALERTBUS_SINKS=console,json,gchat,email
ALERTBUS_SMTP_HOST=smtp.gmail.com
ALERTBUS_SMTP_PORT=587
ALERTBUS_SMTP_USER=tu@gmail.com
ALERTBUS_SMTP_PASSWORD=los-16-caracteres-sin-espacios
ALERTBUS_SMTP_FROM=tu@gmail.com
ALERTBUS_SMTP_TO=tu@gmail.com
```

`ALERTBUS_SMTP_TO` admite varios separados por coma.

---

## WhatsApp — solo para lo crítico fuera de horario

**Déjalo para el final**, y solo si de verdad lo quieres. Es el único canal con
coste por mensaje y con fricción de aprobación:

- Un aviso iniciado por el negocio exige una **plantilla preaprobada** por Meta.
  La categoría determina la tarifa: clasifícala como *utility*, no como
  *marketing*, o pagas de más.
- Desde el **1 de octubre de 2026** ni las respuestas dentro de la ventana de
  24 h son gratuitas.

Por eso este canal manda **solo el titular y un enlace**: el informe completo va
por correo y por Chat, que no cuestan por mensaje.

Necesitas una app en [developers.facebook.com](https://developers.facebook.com)
con el producto *WhatsApp*, un número de teléfono verificado, y una plantilla
aprobada con **dos parámetros de cuerpo** (titular y resumen).

```bash
ALERTBUS_SINKS=console,json,gchat,email,whatsapp
ALERTBUS_WHATSAPP_PHONE_ID=el-id-del-numero
ALERTBUS_WHATSAPP_TOKEN=el-token-permanente
ALERTBUS_WHATSAPP_TO=+34600000000
ALERTBUS_WHATSAPP_TEMPLATE=argus_incidente
```

---

## Quién recibe qué

No lo decide el canal, lo decide el **registro de aplicaciones**
(`platform/registry/apps.yaml`). Es una regla, no un juicio de un modelo:

```yaml
- id: intelligent-document-platform
  criticidad: alta                       # acota la severidad máxima
  canales:
    page: [gchat, whatsapp]              # inmediato
    ticket: [gchat]                      # agrupado, sin urgencia
```

Y la `criticidad` pone el techo: una aplicación de criticidad `baja` **nunca**
despierta a nadie, por mucho que falle. Es lo que evita que un laboratorio
genere guardias.

**Listar varios canales es lo normal**, y los que no estén configurados se
omiten con un aviso en el log (`notify.channel_not_loaded`) en vez de romper el
envío. Por eso puedes dejar `page: [gchat, email, console]` y decidir en el
`.env` cuál usas de verdad.

El registro es datos: se recarga solo, **sin reiniciar nada**. Cambiar un canal
tarda unos segundos en surtir efecto (el intervalo del `tick`).

---

## Si algo no llega

```bash
docker compose -f platform/compose.yaml logs alert-bus | grep -i sink
```

| Lo que ves | Qué pasa |
|---|---|
| `sink.not_configured` | Falta una credencial. El canal se omite entero en vez de fallar en cada envío |
| `sink.unknown` | Un nombre mal escrito en `ALERTBUS_SINKS` |
| `notify.no_sink` | El registro no lista ningún canal cargado para esa aplicación y severidad |
| `notify.channel_not_loaded` | El registro enruta a un canal sin credenciales. Los demás sí reciben |
| `dispatch.send_failed` | El canal responde mal. El mensaje lleva el error del proveedor |

Un canal caído **no impide que los demás reciban**: perder un canal es malo,
perder la alerta entera es peor.

---

## Cómo saber que entregó de verdad

`make channel-test` no se conforma con «no hubo fallos»: compara los contadores
**por canal** antes y después, así que distingue *entregó el chat* de *salió por
consola*. Un canal al que nunca se intentó enviar no falla — y esa es
exactamente la forma que tiene un falso positivo de parecer un éxito.

```bash
curl -s localhost:8080/stats | python3 -m json.tool   # despacho_por_canal
```

La última línea de la prueba nombra el canal: `Los canales funcionan: gchat.`
Si dice otra cosa, no estás cubierto.
