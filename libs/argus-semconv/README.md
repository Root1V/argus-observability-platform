# argus-semconv

Convenciones semánticas de Argus para **librerías**.

## Cuándo usar este paquete y cuándo no

| Escribes… | Importa | Por qué |
|---|---|---|
| Una **librería** (Axonium, synaptum, un SDK tuyo) | `argus-semconv` | Solo depende de `opentelemetry-api`. No impone SDK ni exportadores a quien te use |
| Una **aplicación** (servicio, worker, CLI) | `argus-sdk` | Es quien configura e inicializa la telemetría |

## La garantía

Si la aplicación que importa tu librería **no** ha inicializado un SDK de
OpenTelemetry, toda la instrumentación de este paquete es **no-op con coste
cero**. Si **sí** lo ha inicializado, se enciende sola usando *su*
configuración, *su* endpoint y *su* muestreo.

Eso significa que puedes instrumentar tus librerías sin imponer nada a nadie.

```python
from argus_semconv import genai

# En Axonium, dentro del cliente de inferencia:
with genai("chat", provider="ollama", request_model=model) as g:
    resp = await self._call_backend(...)
    g.usage(input_tokens=resp.prompt_tokens, output_tokens=resp.completion_tokens)
    g.backend(backend_id=backend.id, ttft_ms=resp.ttft_ms)
```

## Las constantes están generadas

`attributes.py` se genera desde `libs/semconv-model/argus.yaml`. No lo edites a
mano: ejecuta `python tools/gen_semconv.py`. Un test de CI verifica que no hay
deriva entre el modelo y lo commiteado.
