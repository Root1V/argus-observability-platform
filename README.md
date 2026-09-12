# Argus

Plataforma de observabilidad, AIOps agéntico y explicabilidad para un
portafolio de aplicaciones que crece.

> **Estado**: Fase 0 (plano central) y arranque de Fase 1 (librerías).
> El plan completo está en `docs/PLAN.md`.

---

## Qué hay construido

| Pieza | Estado |
|---|---|
| `libs/semconv-model/argus.yaml` — convenciones, fuente de verdad | ✅ |
| `tools/gen_semconv.py` — generador de constantes por lenguaje | ✅ Python y Go |
| `libs/argus-semconv` — para **librerías** (solo `opentelemetry-api`) | ✅ |
| `libs/argus-sdk` — para **aplicaciones** (`argus.init()`) | ✅ |
| `platform/` — Collector agente + gateway, ClickHouse, VictoriaMetrics | ✅ perfil ligero |
| `platform/` — Langfuse (perfil genai), Temporal (perfil agents) | ⏳ definido, sin probar |
| `services/alert-bus`, `notifier`, `canary` | ⏳ Fase 2 |
| Flota de agentes | ⏳ Fase 3+ |

---

## Arranque rápido

```bash
# 1. Secretos
cp platform/.env.example platform/.env && $EDITOR platform/.env

# 2. Plano central, modo ligero (4 contenedores)
docker compose -f platform/compose.yaml --profile lean up -d

# 3. Verificación de extremo a extremo contra el stack real
set -a && . platform/.env && set +a
uv run --with 'opentelemetry-exporter-otlp-proto-http' python scripts/e2e_smoke.py
```

Con Langfuse (Fase 1):

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
uv sync                                   # workspace
uv run pytest -q                          # 69 pruebas
uv run python tools/gen_semconv.py        # regenerar constantes
uv run python tools/gen_semconv.py --check  # detectar deriva (CI)
uv run ruff check .
```

### Validar las configs del Collector

Contra el binario real, que es lo único que prueba que funcionan:

```bash
cd platform && ./scripts/validate-collector.sh
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
```
