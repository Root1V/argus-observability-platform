# Argus

[![publicar](https://github.com/Root1V/argus-observability-platform/actions/workflows/publicar.yml/badge.svg)](https://github.com/Root1V/argus-observability-platform/actions/workflows/publicar.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Plataforma de observabilidad, AIOps agéntico y explicabilidad para un
portafolio de aplicaciones que crece.

Las aplicaciones importan una librería ligera y exportan **siempre a
`localhost`**; el plano central puede moverse de máquina sin que ninguna se
entere. Un camino caliente detecta incidentes **sin tocar la base de datos**,
y los agentes investigan sobre el frío.

## Estado: piloto en curso

No es una maqueta. Corre con una aplicación real desde el 13/09/2026 —
**Prometheus**, una plataforma de inferencia local— y estos números están
medidos, no estimados:

| | |
|---|---|
| Spans reales del piloto | **68.709**, tres servicios |
| Deduplicación bajo tormenta | **10.446 señales → 45 notificaciones** (99,6 %) |
| Trazas GenAI contrastadas | **689 de 689**, contra el contador del otro equipo |
| Detección hasta el aviso | **~2 s**, medido con reloj |
| Pruebas | 229, en 3.11 · 3.12 · 3.13 |

**El piloto no está cerrado, y `make pilot-status` dice por qué**: quedan tres
criterios sin verificar y un reloj de catorce días sin fallos nuevos de la
plataforma que se reinicia cada vez que encontramos uno. Van **14 fallos
nuestros** encontrados ejecutando, no en la suite — están marcados uno a uno en
[decisions.md](docs/decisions.md).

Esa cuenta es deliberada. Una plataforma de observabilidad a medio construir es
peor que no tenerla, porque genera confianza infundada.

## Los documentos

| Documento | Para qué |
|---|---|
| [docs/piloto.md](docs/piloto.md) | **Conectar una aplicación**, paso a paso, y los 8 criterios para cerrar el piloto |
| [docs/coordinacion.md](docs/coordinacion.md) | Cómo se coordina un cambio con el equipo de otra aplicación |
| [roadmap.md](roadmap.md) | Qué está hecho, qué falta, y el backlog. Todo con código estable |
| [docs/decisions.md](docs/decisions.md) | Por qué está hecho así. **75 decisiones** con su coste y lo que se descartó |
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
