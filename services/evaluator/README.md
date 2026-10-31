# evaluator — evaluaciones online (F5-01)

Lee de `otel.otel_traces` las llamadas a modelos de todo el portafolio cuando
ya han terminado, les pasa evaluaciones y deja el resultado en dos sitios:

- **`argus.evaluaciones`** (ClickHouse): una fila por span y evaluación, con
  `pasa`, `falla` o `desconocido` y el motivo. Es idempotente: volver a evaluar
  una ventana reemplaza las filas.
- **`argus_eval_results_total`** (VictoriaMetrics): el contador por app (`argus_eval_app`),
  componente, evaluación, resultado y modelo pedido. Es lo que leerá la
  alerta de condición conjunta de F5-03.

```bash
docker compose -f platform/compose.yaml --profile agents up -d evaluator
docker exec argus-evaluator-1 /app/.venv/bin/python -m evaluator informe --horas 24
```

## Qué evalúa hoy

| Evaluación | Falla cuando | Lee |
|---|---|---|
| `rechazo` | el modelo se niega o el filtro de contenido corta | `finish_reasons` |
| `truncado` | la respuesta se corta por longitud, o agota `max_tokens` | `finish_reasons`, tokens |
| `abandono` | el cliente se va antes de la respuesta | `finish_reasons` |
| `vacia` | termina bien con 0 tokens y sin llamar a una herramienta | tokens |

Todas son deterministas y se aplican a **todos** los spans GenAI generativos
(`chat`, `text_completion`, `generate_content`) que no acabaron en error. Un
span en error ya lo cuentan las métricas RED.

**`desconocido` no es `pasa`.** Si un span no trae el atributo que la
evaluación necesita, o trae una palabra fuera del vocabulario, se guarda como
`desconocido` con el motivo. Así una laguna de instrumentación se ve en el
informe en vez de esconderse como una tasa de fallos del 0 %.

## La muestra

Cada span lleva en `muestra` por qué entraría en las evaluaciones caras:
`error` (todos), `p99` (los más lentos contra la semana de su componente y
modelo) o `azar` (5 % por traza, determinista). Hoy no hay evaluaciones caras:
groundedness y consistencia necesitan el contenido de los mensajes, que no se
guarda en ClickHouse (D-008) y no tiene otro sitio mientras Langfuse esté
apagado (D-154). Llegan con F5-02.
