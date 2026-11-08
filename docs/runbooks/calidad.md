# Las evaluaciones empeoran a la vez que cambia la entrada

Ha saltado `ArgusEvalCaeConDerivaDeEntrada` (F5-03, D-161). Significa dos cosas
a la vez en la misma app, en las últimas 6 horas:

- **al menos una evaluación suspende mucho más que en su semana**: 10 puntos
  más y el doble, con 10 evaluaciones como mínimo;
- **los tokens de entrada por llamada se alejan un 50 %** de su media semanal,
  con 20 llamadas como mínimo.

Una sola de las dos no avisa, a propósito: que cambie la entrada no dice que
algo vaya mal, y que suspendan más evaluaciones sin que cambie nada puede ser
una muestra pequeña. Avisar por cualquiera de ellas sola es la falsa alarma que
quema la guardia.

---

## 1. ¿Cuál de las evaluaciones, y cuánto?

```promql
argus:eval_caida{argus_eval_app="<app>"}            # tasa de suspensos de 6 h
argus:eval_fallo:ratio7d{argus_eval_app="<app>"}    # la de la semana
argus:deriva_entrada{service_namespace="<app>"}     # cociente 6 h / semana
```

Y los casos concretos, con su motivo, en la tabla del evaluador:

```bash
docker exec argus-evaluator-1 /app/.venv/bin/python -m evaluator informe --horas 6
```

## 2. ¿Qué está entrando distinto?

El cociente de `argus:deriva_entrada` dice hacia dónde: **mayor que 1,5**, los
prompts crecieron (otro tipo de petición, más contexto recuperado, una
plantilla nueva); **menor que 0,67**, encogieron (contexto que ya no llega, una
recuperación que devuelve vacío).

Lo primero que hay que descartar es un **cambio de la propia app**: ¿cambió su
`service.version` en esas horas? Un prompt nuevo que alarga la entrada y además
trunca las respuestas es un despliegue, no una deriva.

## 3. Cómo leer cada evaluación que cae

| Evaluación | Si cae junto con prompts más largos | Si cae junto con prompts más cortos |
|---|---|---|
| `truncado` | Las respuestas no caben en `max_tokens`: más contexto, menos espacio para contestar | Raro: mira si cambió `max_tokens` |
| `vacia` | El modelo se pierde con el contexto largo | Le falta información para contestar |
| `rechazo` | Entra contenido que el filtro bloquea | — |
| `abandono` | Las llamadas tardan más y el cliente corta (Axonium corta a los 600 s) | — |
| `groundedness` (juez de L3) | El expediente creció y el juez cita peor | El expediente llega con lagunas |

## 4. Antes de dar nada por bueno

- **¿Durmió el Mac?** La regla exige que el observador haya estado despierto
  el 80 % de las 6 h, pero tras despertar la VM de Docker va atrasada un rato
  (D-160). Si el aviso llega justo después de abrir la tapa, mira primero
  `argus:observador_despierto:fraccion6h`.
- **¿Pocas llamadas?** Con el mínimo de 20 llamadas y 10 evaluaciones, un solo
  usuario haciendo algo raro puede bastar. El aviso es un `ticket`, no un
  `page`, por eso.
- Marca el aviso como **útil** o **ruido** con el botón (B-46): es lo que dirá
  si estos umbrales sirven.
