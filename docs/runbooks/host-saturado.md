# El host está saturado

Saltó `ArgusHostCPUSostenida` o `ArgusHostMemoriaAlta`.

> **Aviso antes de nada: en macOS estas alertas miden la VM de Docker, no el
> Mac.** El `hostmetrics` corre dentro del contenedor del agente y la VM es una
> frontera real. Si el Mac va lento y estas alertas están en silencio, eso **no
> significa que el Mac esté bien** — significa que no lo estamos mirando.
> Mira el apartado 2 a mano.

El plano central, las aplicaciones y la inferencia local **comparten máquina**.
Eso es una decisión consciente y tiene esta consecuencia: cuando algo satura el
host, la plataforma que debería avisarte compite por los mismos recursos.

---

## 1. Qué está consumiendo

```bash
ps -eo pid,etime,%cpu,%mem,command | sort -k3 -rn | head -15
```

Mira **las dos** columnas, y sobre todo `etime`:

- **%CPU alto y `etime` corto** — una compilación, una indexación, un modelo
  cargando. Normal, se va solo.
- **%CPU alto y `etime` de días** — casi siempre un proceso girando en vacío.
  Esto es lo que la alerta busca: la ventana de 2 horas existe para no
  confundir una cosa con la otra.

## 2. El caso que originó esta alerta

Un `python3 -` lanzado desde un heredoc se quedó **62 horas al 98,7 % de un
núcleo**. El trabajo que iba a hacer ya estaba hecho y commiteado; el proceso
sobrevivió a su propósito.

Los sospechosos habituales de esa forma:

- Un intérprete leyendo de `stdin` que nunca recibió su fin de entrada.
- Un `tail -f`, un `watch` o un bucle de reintento de una sesión ya cerrada.
- Un servidor de pruebas que quedó levantado.

```bash
# Procesos de más de un día ordenados por CPU
ps -eo pid,etime,%cpu,command | awk '$2 ~ /-/' | sort -k3 -rn | head
```

**Antes de matar nada, comprueba si su trabajo ya está hecho.** En aquel caso el
fichero existía en git desde tres días antes.

## 3. Si es la inferencia local

No es un fallo: es la máquina haciendo su trabajo. Lo que sí conviene mirar es
si la plataforma se está quedando sin sitio.

```bash
docker stats --no-stream
```

Si ClickHouse o el Collector están arriba del límite, el orden de recorte es:
bajar la retención en caliente, luego el perfil (`genai` → `lean`), y solo
después mover el plano central a otra máquina.

## 4. Si es la memoria

No se recupera sola. `memory_limiter` protege al Collector de sí mismo, pero no
del resto del host. Si el sistema empieza a paginar, la telemetría es lo primero
que se degrada — y de forma invisible, porque los SDK descartan en silencio.

---

## Por qué esta alerta es de severidad `ticket` y no `page`

Un host saturado casi nunca es una emergencia de madrugada: o es trabajo
legítimo, o es desperdicio que lleva horas y aguanta hasta mañana. Ponerla en
`page` la convertiría en la alerta que se silencia, y entonces no serviría para
nada la próxima vez.
