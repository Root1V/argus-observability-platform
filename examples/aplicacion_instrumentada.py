"""Como instrumentar una APLICACION, y la demostracion del apalancamiento.

La misma libreria del ejemplo anterior, ahora usada desde una aplicacion que
SI llamo a `argus.init()`. Sus spans aparecen solos.

Ejecutar (muestra los spans por consola, sin necesidad de plataforma):
    ARGUS_CONSOLE=true ARGUS_DISABLED=true python examples/aplicacion_instrumentada.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import argus
from libreria_instrumentada import ClienteInferenciaLocal, EjecutorHerramientas


def main() -> None:
    # Las DOS lineas que cuesta integrar una aplicacion.
    # Todo lo demas viene del entorno.
    argus.init(
        "demo-api",
        namespace="demo-app",          # la APLICACION
        role="api",                    # el tipo de componente
        version="1.0.0",
    )

    cliente = ClienteInferenciaLocal(["llama-0"])
    ejecutor = EjecutorHerramientas()

    # Un agente: span padre `invoke_agent` con hijos `chat` y `execute_tool`
    # alternados. Esa estructura es la que permite detectar bucles contando
    # tool calls por ejecucion.
    with argus.agent("demo-assistant", conversation_id="conv-42"):
        with argus.step("documento.procesar", paginas=42) as s:
            s.set(motor_ocr="paddle", cache_hit=False)

            # Estos spans los emite la LIBRERIA, no esta aplicacion.
            cliente.chat("qwen2.5-coder-7b", [{"role": "user", "content": "resume"}], feature="resumen")
            ejecutor.ejecutar("buscar_documentos", {"q": "factura"}, call_id="call-01")

            s.set(campos_extraidos=17).outcome("ok")

    print("\nLa aplicacion escribio DOS lineas de instrumentacion (`step` y `agent`).")
    print("Los spans `chat` y `execute_tool` vinieron de la libreria, gratis.")


if __name__ == "__main__":
    main()
