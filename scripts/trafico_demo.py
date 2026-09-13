#!/usr/bin/env python3
"""Genera tráfico de una aplicación simulada, para ver el dashboard con datos.

No es un test: es una app de mentira que se comporta como una de verdad —tres
componentes, algo de error, algo de latencia, llamadas a un LLM— para poder
mirar el dashboard antes de conectar una aplicación real.

Uso:
    python scripts/trafico_demo.py --minutos 3
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time

RUTAS = ["/documentos", "/documentos/{id}", "/extraer", "/buscar"]
ERRORES = ["timeout", "backend-unavailable", "upstream-error", "validation-failed"]
MODELOS = ["qwen2.5-coder-7b", "llama3.1-8b"]
FUNCIONALIDADES = ["extraccion", "resumen", "busqueda"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutos", type=float, default=2.0)
    parser.add_argument("--app", default="demo-idp")
    args = parser.parse_args()

    os.environ.setdefault("ARGUS_PROTOCOL", "http/protobuf")
    os.environ.setdefault("ARGUS_ENDPOINT", "http://127.0.0.1:4318")
    if token := os.environ.get("ARGUS_GATEWAY_TOKEN"):
        os.environ.setdefault("OTEL_EXPORTER_OTLP_HEADERS", f"Authorization=Bearer {token}")

    import argus

    handle = argus.init(
        "demo-api", namespace=args.app, role="api", version="1.0.0",
        environment="mac-dev", json_logs=False,
    )

    fin = time.time() + args.minutos * 60
    n = 0
    print(f"\n  generando tráfico de «{args.app}» durante {args.minutos:g} min…\n")

    while time.time() < fin:
        ruta = random.choice(RUTAS)

        # Una petición de API que a veces falla y a veces va lenta.
        with argus.step(f"GET {ruta}", slo_ms=800) as peticion:
            peticion.set(**{"http.route": ruta})
            lento = random.random() < 0.12
            time.sleep(random.uniform(0.9, 1.4) if lento else random.uniform(0.02, 0.25))

            if random.random() < 0.07:
                peticion.error(random.choice(ERRORES), retryable=True)
            else:
                # Parte de las peticiones llaman a un modelo.
                if random.random() < 0.4:
                    modelo = random.choice(MODELOS)
                    with argus.genai("chat", provider="ollama", request_model=modelo) as g:
                        time.sleep(random.uniform(0.3, 1.2))
                        entrada = random.randint(400, 3000)
                        salida = random.randint(80, 600)
                        g.usage(input_tokens=entrada, output_tokens=salida)
                        g.response(model=f"{modelo}-q4_K_M", finish_reasons=["stop"])
                        g.attribution(feature=random.choice(FUNCIONALIDADES), use_case="documentos")
                        g.backend(backend_id="llama-0", ttft_ms=random.randint(40, 300),
                                  cost_usd=round((entrada + salida) * 0.0000004, 6))

                # Y algunas encolan trabajo para el worker.
                if random.random() < 0.3:
                    with argus.step("ocr.extract", slo_ms=3000) as ocr:
                        time.sleep(random.uniform(0.1, 0.5))
                        ocr.set(paginas=random.randint(1, 60))

                peticion.outcome("ok")

        n += 1
        if n % 25 == 0:
            print(f"    {n} peticiones")
            handle.force_flush(timeout_millis=3000)
        time.sleep(random.uniform(0.05, 0.3))

    handle.force_flush(timeout_millis=10_000)
    print(f"\n  {n} peticiones generadas. Míralo en http://localhost:3001\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
