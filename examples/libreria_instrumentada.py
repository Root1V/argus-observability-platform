"""Como instrumentar una LIBRERIA tuya: el caso Axonium.

Esto es lo que iria dentro de `axonium`, `synaptum` o cualquier SDK que
muchas aplicaciones importen.

La regla: la libreria importa `argus_semconv`, que depende SOLO de
`opentelemetry-api`. Nunca `argus` (el SDK), nunca un exportador.

Ejecutar:
    python examples/libreria_instrumentada.py
"""

from __future__ import annotations

import random
import time

# Lo unico que importa una libreria. Si la aplicacion que la usa no configuro
# un SDK, todo esto es no-op con coste cero.
from argus_semconv import genai, tool


class ClienteInferenciaLocal:
    """Fragmento de lo que seria el cliente de inferencia de Axonium.

    Instrumentar AQUI da a toda aplicacion que use Axonium informacion que
    ninguna de ellas puede conocer por si misma: que backend respondio, si
    hubo fallback, si salto el circuit breaker, el tiempo hasta el primer
    token. Y sin que ninguna tenga que escribir una linea de instrumentacion.
    """

    def __init__(self, backends: list[str]) -> None:
        self._backends = backends

    def chat(self, model: str, mensajes: list[dict], *, feature: str | None = None) -> dict:
        with genai("chat", provider="ollama", request_model=model) as g:
            g.attribution(feature=feature, use_case="chat")
            g.request_params(temperature=0.2, max_tokens=1024)
            g.messages(input=mensajes)   # no-op salvo ARGUS_CAPTURE_CONTENT

            backend, hubo_fallback = self._elegir_backend()
            inicio = time.perf_counter()

            try:
                respuesta = self._llamar(backend, model, mensajes)
            except TimeoutError:
                # `retryable` lo lee el agente remediador: reintentar un error
                # no transitorio es hacer dano, no arreglar.
                g.error("timeout", retryable=True, retry_policy="exponential-backoff")
                raise

            ttft_ms = int((time.perf_counter() - inicio) * 1000)

            g.usage(
                input_tokens=respuesta["prompt_tokens"],
                output_tokens=respuesta["completion_tokens"],
            )
            g.response(
                model=respuesta["model"],        # el SERVIDO, que puede diferir del pedido
                response_id=respuesta["id"],
                finish_reasons=[respuesta["finish_reason"]],
            )
            g.backend(
                backend_id=backend,
                circuit_state="closed",
                fallback=hubo_fallback,
                ttft_ms=ttft_ms,
                cost_usd=0.0,                    # local: sin coste monetario
            )
            g.messages(output=respuesta["choices"])

            return respuesta

    def _elegir_backend(self) -> tuple[str, bool]:
        elegido = self._backends[0]
        return elegido, False

    def _llamar(self, backend: str, model: str, mensajes: list[dict]) -> dict:
        time.sleep(random.uniform(0.01, 0.05))
        return {
            "id": f"resp-{random.randint(1000, 9999)}",
            # Alias resuelto: el modelo servido no es el pedido. Esa diferencia
            # explica incidentes y por eso se registra por separado.
            "model": f"{model}-q4_K_M",
            "prompt_tokens": 1240,
            "completion_tokens": 312,
            "finish_reason": "stop",
            "choices": [{"role": "assistant", "content": "..."}],
        }


class EjecutorHerramientas:
    """Fragmento de lo que seria synaptum.

    Los spans `execute_tool` son lo que permite detectar bucles de agente
    contando llamadas por ejecucion: un agente atascado devuelve 200 con
    latencia normal, asi que el APM tradicional no lo ve.
    """

    def ejecutar(self, nombre: str, argumentos: dict, *, call_id: str) -> object:
        with tool(nombre, call_id=call_id) as t:
            t.set("argus.tool.arg_count", len(argumentos))
            return {"ok": True}


if __name__ == "__main__":
    cliente = ClienteInferenciaLocal(["llama-0", "llama-1"])
    ejecutor = EjecutorHerramientas()

    print("Sin argus.init(): la instrumentacion es no-op y no cuesta nada.\n")
    respuesta = cliente.chat("qwen2.5-coder-7b", [{"role": "user", "content": "hola"}], feature="demo")
    ejecutor.ejecutar("buscar_documentos", {"q": "factura"}, call_id="call-01")
    print(f"  respuesta: {respuesta['model']}, {respuesta['completion_tokens']} tokens de salida")
    print("\n  La aplicacion que llame a argus.init() vera estos mismos spans")
    print("  en la plataforma, sin escribir una linea de instrumentacion.")
