# Runbook: migrar el plano central a otra máquina

> **Ensáyalo en frío antes de necesitarlo.** Un runbook que nunca se ha
> ejecutado no es un runbook, es una intención. `make migrate-rehearse` lo
> prueba sin tocar nada.

## Cuándo se usa

- Mover el plano central de la MacBook al iMac.
- Recuperarlo tras un fallo de disco.
- Duplicarlo en otra máquina para probar algo.

## Lo que NO hay que hacer

**No hay que tocar ninguna aplicación.** Las aplicaciones exportan siempre a
`localhost:4317` de su propia máquina y nunca conocen la dirección del plano
central (D-003). Lo único que cambia de sitio es el destino de los **Collector
agente**, y eso es una variable de entorno.

Si en algún momento de esta migración te ves editando la configuración de una
aplicación, algo se ha desviado del diseño.

---

## Antes de empezar

```bash
# En la máquina ORIGEN
make ps                    # anota qué perfiles están arriba
docker compose -f platform/compose.yaml --profile lean ps -q | wc -l
df -h | grep -i docker     # comprueba que hay espacio para el volcado
```

Necesitas en destino: Docker, `make`, `git` y espacio para los volúmenes.

---

## 1. Parar la ingesta sin perder datos

```bash
# ORIGEN
make down
```

Los Collector agente de todas las máquinas **siguen corriendo** y acumulan en
disco lo que no pueden enviar (D-004, verificado en `F1-09`). Esa es justamente
la propiedad que hace esta migración segura: mientras el central no esté, nadie
pierde nada.

Ventana disponible: `max_elapsed_time: 24h` en el exportador del agente, con el
límite real puesto por el disco. Para una migración normal sobra.

---

## 2. Volcar los volúmenes

```bash
# ORIGEN
make migrate-dump DEST=~/argus-backup
```

Genera un `.tar.gz` por volumen más un `manifiesto.json` con la versión de las
imágenes y la fecha. El manifiesto importa: restaurar sobre versiones distintas
de ClickHouse puede exigir migraciones.

Lo que se lleva:

| Volumen | Qué contiene | ¿Imprescindible? |
|---|---|---|
| `clickhouse-data` | Toda la telemetría | Sí |
| `vm-data` | Métricas y reglas | Sí |
| `collector-queue` | Cola del gateway | Recomendable |
| `langfuse-*` | Trazas GenAI, prompts | Solo con perfil `genai` |
| `temporal-postgres` | Workflows en curso | Solo con perfil `agents` |

---

## 3. Llevarlo al destino

```bash
# Por red privada; evita servicios de terceros para datos de telemetría.
rsync -avP ~/argus-backup/ nuevo-equipo:~/argus-backup/
```

**El `.env` va aparte y por un canal seguro.** No está en el repositorio, y con
razón: lleva el token del gateway y las credenciales de los canales.

---

## 4. Restaurar

```bash
# DESTINO
git clone $(git config --get remote.origin.url) app_monitoring_explainability
cd app_monitoring_explainability
# copia aquí platform/.env desde el canal seguro
make migrate-restore SRC=~/argus-backup
make up
```

---

## 5. Redirigir los agentes

Este es el único paso que toca otras máquinas, y es una variable:

```bash
# En CADA máquina con un Collector agente
$EDITOR platform/.env.agent     # ARGUS_GATEWAY_ENDPOINT=nueva-maquina:14318
docker compose -f platform/compose.agent.yaml --env-file platform/.env.agent up -d
```

**Con un nombre estable de red privada (Tailscale), este paso desaparece**: el
nombre sigue al servicio y los agentes reconectan solos. Es la razón de que
`.env.agent.example` insista en usar un nombre y no una IP.

---

## 6. Verificar

```bash
# DESTINO
make verify
```

Y comprobar que los datos históricos siguen ahí, no solo que el stack arranca:

```bash
make query SQL="
  SELECT toDate(Timestamp) AS dia, count() AS spans
  FROM otel.otel_traces
  GROUP BY dia ORDER BY dia DESC LIMIT 7 FORMAT PrettyCompact"
```

Si solo ves el día de hoy, la restauración falló y estás mirando datos nuevos.

Por último, comprueba que los agentes remotos volvieron:

```bash
make query SQL="
  SELECT ResourceAttributes['host.name'] AS host, max(Timestamp) AS ultimo
  FROM otel.otel_traces WHERE Timestamp > now() - INTERVAL 10 MINUTE
  GROUP BY host FORMAT PrettyCompact"
```

---

## 7. Después

- Actualiza el *dead man's switch* si apuntaba a la máquina vieja
  (`deadman/deadman.json`). **Es fácil de olvidar y deja de vigilar en
  silencio**, que es el peor modo de fallo posible para un vigilante.
- Conserva el volcado unos días antes de borrarlo.

---

## Si algo sale mal

**Arrancar la máquina origen otra vez.** Los volúmenes siguen allí intactos: el
volcado es una copia, no un traslado. Los agentes vuelven a apuntar al endpoint
anterior y se vacían las colas.

Mientras dure el lío, las aplicaciones **no se enteran**: siguen exportando a su
`localhost` y acumulando en disco.
