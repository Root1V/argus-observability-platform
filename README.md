# Argus

Plataforma de observabilidad, AIOps agéntico y explicabilidad para un
portafolio de aplicaciones que crece.

> **Estado**: F0 y F2 completas, F1 casi. **A un paso de poder pilotar con una
> aplicación real** — ver [docs/piloto.md](docs/piloto.md) y `make pilot-check`.

## Los documentos

| Documento | Para qué |
|---|---|
| [docs/piloto.md](docs/piloto.md) | **Conectar la primera aplicación real**, paso a paso |
| [roadmap.md](roadmap.md) | Qué está hecho, qué falta, y el backlog. Todo con código estable |
| [docs/decisions.md](docs/decisions.md) | Por qué está hecho así. 35 decisiones con su coste |
| [docs/como-probarlo.md](docs/como-probarlo.md) | Cómo verificarlo tú mismo |
| [docs/runbooks/migrar-plano-central.md](docs/runbooks/migrar-plano-central.md) | Mover el plano central a otra máquina |
| [docs/PLAN.md](docs/PLAN.md) | El plan completo con la investigación que lo respalda |

---

## Arranque rápido

```bash
make setup        # dependencias + secretos locales
make up           # plano central en modo ligero
make agent        # Collector agente de esta máquina
make pilot-check  # ¿listo para conectar una app real? Dice qué falta
```

Para verificarlo todo:

```bash
make check    # rápido, sin Docker (~1 min)
make verify   # completo, contra el stack real
make demo     # la demostración más corta: apalancamiento de librerías
```

`make help` lista todo. La guía detallada está en
[docs/como-probarlo.md](docs/como-probarlo.md).

Con Langfuse (`F1-12`):

```bash
docker compose -f platform/compose.yaml -f platform/compose.genai.yaml --profile genai up -d
```

En **cada máquina** que corra aplicaciones, incluida la del plano central:

```bash
cp platform/.env.agent.example platform/.env.agent && $EDITOR platform/.env.agent
docker compose -f platform/compose.agent.yaml --env-file platform/.env.agent up -d
```

---

## Las tres decisiones que explican todo lo demás

### 1. Dos paquetes, no uno

| Escribes… | Importa | Por qué |
|---|---|---|
| Una **librería** (Axonium, synaptum, un SDK tuyo) | `argus-semconv` | Solo depende de `opentelemetry-api` |
| Una **aplicación** (servicio, worker, CLI) | `argus-sdk` | Es quien configura la telemetría |

Una librería que depende del SDK impone en silencio su versión del SDK y sus
opiniones sobre exportadores a todo el que dependa de ella. Con un portafolio
de quince o más aplicaciones importando Axonium, eso es un infierno de
dependencias garantizado.

La consecuencia práctica: **Axonium instrumentado emite trazas GenAI completas
en toda aplicación que lo use y que haya llamado a `argus.init()`, y no cuesta
absolutamente nada en las que no.** Ninguna aplicación tiene que enrutar su
tráfico por ningún sitio: es apalancamiento sin acoplamiento.

### 2. Las aplicaciones exportan a `localhost`, siempre

Nunca conocen la dirección del plano central. Eso da tres propiedades:

- Mover el plano central de la MacBook al iMac **no toca ni una aplicación**.
- Si el central está suspendido, las apps no se enteran ni se ralentizan: el
  agente local escribe a disco y envía al reconectar.
- Añadir una máquina es desplegar un agente, no reconfigurar apps.

### 3. Tres caminos con latencias distintas

Una tubería convencional acumula latencia en cada salto y detectar tarda más
de un minuto. El conflicto de fondo es que el **tail sampling tiene que
esperar a que la traza cierre** — correcto para almacenar, inaceptable para
detectar.

| Camino | Latencia | Qué lleva | A dónde |
|---|---|---|---|
| **Caliente** | ~2 s | Errores, SLO roto, guardarraíl | Directo al `alert-bus`, **sin tocar la base de datos** |
| **Templado** | 2–15 s | Métricas RED derivadas en vuelo | VictoriaMetrics, vistas materializadas |
| **Frío** | 30–60 s | Traza completa, muestreada | ClickHouse + Langfuse |

---

## Desarrollo

```bash
make test      # 69 pruebas
make semconv   # regenerar constantes desde argus.yaml
make query SQL="SELECT ... FORMAT PrettyCompact"
make logs      # seguir el Collector
make down      # parar (conserva datos) · make clean borra volúmenes
```

---

## Estructura

```
libs/
  semconv-model/argus.yaml    fuente de verdad de las convenciones
  argus-semconv/              para librerías — solo opentelemetry-api
  argus-sdk/                  para aplicaciones — argus.init()
platform/
  compose.yaml                plano central (perfiles lean|genai|agents)
  compose.genai.yaml          overlay: Langfuse + su pipeline, juntos
  compose.agent.yaml          agente, uno por máquina
  collector/agent.yaml        bifurca caliente/frío, cola en disco
  collector/gateway.yaml      redacción, tail sampling, fan-out
  collector/gateway.genai.yaml overlay de la rama Langfuse
  rules/                      SLO burn-rate multi-ventana
tools/gen_semconv.py          generador de constantes por lenguaje
scripts/e2e_smoke.py          verificación contra el stack real
scripts/verify.sh             todo lo anterior en un comando
```
