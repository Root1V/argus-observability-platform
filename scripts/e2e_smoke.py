#!/usr/bin/env python3
"""Prueba de humo de extremo a extremo contra el plano central real.

Comprueba lo que define la Fase 0 y el arranque de la Fase 1 del plan:

 1. El endpoint OTLP RECHAZA telemetria sin token.
 2. Una unidad de trabajo que cruza API -> cola -> worker produce UNA sola
    traza. Si salen dos, la propagacion fuera de HTTP no funciona y nada de lo
    que viene despues sirve.
 3. Los spans GenAI llegan con tokens y modelo poblados.
 4. Un error marca `argus.hot`, que es lo que enruta al camino caliente.
 5. La redaccion funciona: un email en un prompt no llega en claro al almacen.
 6. La identidad de dos niveles permite agrupar por aplicacion y desglosar por
    componente.

No usa mocks: escribe en la ClickHouse de verdad y consulta lo que llego.
"""

from __future__ import annotations

import os
import time
import urllib.parse
import urllib.request

CLICKHOUSE = os.getenv("ARGUS_CH_URL", "http://127.0.0.1:8123")
CH_USER = os.getenv("CLICKHOUSE_USER", "argus")
CH_PASSWORD = os.environ["CLICKHOUSE_PASSWORD"]
RUN_TAG = f"smoke-{int(time.time())}"

PASS, FAIL = "\033[32mOK  \033[0m", "\033[31mFALLO\033[0m"

# Toda consulta se acota a ESTE run. Buscar por prefijo de servicio encontraba
# filas de ejecuciones anteriores y daba comprobaciones en verde con datos
# viejos, que es peor que fallar.
NS = f"ResourceAttributes['service.namespace'] = '{RUN_TAG}'"
NS_FILTER_COUNT = f"SELECT count() FROM otel.otel_traces WHERE {NS}"
_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"{PASS if condition else FAIL} {label}")
    if detail and not condition:
        print(f"       {detail}")
    if not condition:
        _failures.append(label)


def wait_for_collector(timeout_s: int = 60) -> bool:
    """Espera a que el Collector acepte trafico.

    Sin esto, la prueba mide el arranque del Collector en vez de la tuberia, y
    falla de forma desconcertante justo despues de un `docker compose up`.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:13133/", timeout=3):
                return True
        except Exception:  # noqa: BLE001
            time.sleep(2)
    return False


def ch_query(sql: str) -> str:
    url = f"{CLICKHOUSE}/?" + urllib.parse.urlencode({"query": sql})
    request = urllib.request.Request(url)
    auth = urllib.parse.quote(CH_USER), CH_PASSWORD
    request.add_header("X-ClickHouse-User", auth[0])
    request.add_header("X-ClickHouse-Key", auth[1])
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode().strip()


# --- 1. El endpoint exige token ---------------------------------------------

def test_auth_required() -> None:
    request = urllib.request.Request(
        "http://127.0.0.1:4318/v1/traces",
        data=b'{"resourceSpans":[]}',
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            check("El endpoint OTLP rechaza peticiones sin token", False, f"acepto con {response.status}")
    except urllib.error.HTTPError as exc:
        check("El endpoint OTLP rechaza peticiones sin token", exc.code in (401, 403), f"codigo {exc.code}")
    except Exception as exc:  # noqa: BLE001
        check("El endpoint OTLP rechaza peticiones sin token", False, str(exc))


# --- 2-6. Emitir telemetria real --------------------------------------------

def emit() -> None:
    import argus
    from argus import propagate

    handle = argus.init(
        "smoke-api",
        namespace=RUN_TAG,
        role="api",
        version="1.0.0",
        environment="ci",
        endpoint="http://localhost:4318",
        propagate_mode="always",
    )

    from opentelemetry import trace

    tracer = trace.get_tracer("smoke")

    # (2) API -> cola -> worker, tres procesos logicos, una sola traza.
    with tracer.start_as_current_span("POST /documents"):
        message = propagate.inject_payload({"doc_id": 7})

    with propagate.extract_payload(message, name="document.process"):
        # (3) Span GenAI con tokens y modelo.
        with argus.genai("chat", provider="ollama", request_model="qwen2.5-coder-7b") as g:
            g.usage(input_tokens=1200, output_tokens=340)
            g.response(model="qwen2.5-coder-7b-q4", finish_reasons=["stop"])
            g.backend(backend_id="llama-0", ttft_ms=87)
            # (5) PII en el prompt: no debe llegar en claro.
            g.messages(input=[{"role": "user", "content": "escribe a ana.perez@empresa.com"}])

        # (4) Un error marca argus.hot para el camino caliente.
        with argus.step("ocr.extract") as s:
            s.error("timeout", retryable=True)

    handle.force_flush(timeout_millis=10_000)


def main() -> int:
    os.environ["ARGUS_CAPTURE_CONTENT"] = "true"
    os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization=Bearer {os.environ['ARGUS_GATEWAY_TOKEN']}"
    os.environ["ARGUS_PROTOCOL"] = "http/protobuf"

    print(f"\n--- Prueba de humo end-to-end  (namespace: {RUN_TAG}) ---\n")

    if not wait_for_collector():
        print(f"{FAIL} El Collector no responde en 13133. Arranca el plano central primero.")
        return 1

    test_auth_required()
    emit()

    # El gateway hace tail sampling con decision_wait de 30 s: hay que esperar
    # a que la traza cierre y se decida. Es exactamente el motivo por el que la
    # DETECCION no pasa por aqui, sino por el camino caliente.
    print("       esperando al tail sampling del gateway (decision_wait 30s)...")
    rows = "0"
    for _ in range(30):
        time.sleep(3)
        try:
            rows = ch_query(NS_FILTER_COUNT)
            if int(rows) >= 4:
                break
        except Exception:  # noqa: BLE001
            continue

    check("Las trazas llegan a ClickHouse", int(rows) >= 4, f"filas encontradas: {rows}")

    trace_ids = ch_query(
        f"SELECT uniqExact(TraceId) FROM otel.otel_traces "
        f"WHERE ResourceAttributes['service.namespace'] = '{RUN_TAG}'"
    )
    check(
        "API -> cola -> worker produce UNA sola traza",
        trace_ids == "1",
        f"se encontraron {trace_ids} trazas distintas; la propagacion fuera de HTTP esta rota",
    )

    genai = ch_query(
        f"SELECT SpanName, SpanAttributes['gen_ai.usage.input_tokens'], SpanAttributes['gen_ai.response.model'] "
        f"FROM otel.otel_traces WHERE ResourceAttributes['service.namespace'] = '{RUN_TAG}' "
        f"AND SpanAttributes['gen_ai.operation.name'] = 'chat'"
    )
    check(
        "El span GenAI llega con nombre, tokens y modelo servido",
        "chat qwen2.5-coder-7b" in genai and "1200" in genai and "q4" in genai,
        genai or "(vacio)",
    )

    hot = ch_query(
        f"SELECT count() FROM otel.otel_traces "
        f"WHERE ResourceAttributes['service.namespace'] = '{RUN_TAG}' AND SpanAttributes['argus.hot'] = 'true'"
    )
    check("Un error marca argus.hot para el camino caliente", int(hot or 0) >= 1, f"spans marcados: {hot}")

    leaked = ch_query(
        f"SELECT count() FROM otel.otel_traces "
        f"WHERE ResourceAttributes['service.namespace'] = '{RUN_TAG}' "
        f"AND arrayExists(v -> position(v, 'ana.perez@empresa.com') > 0, mapValues(SpanAttributes))"
    )
    check("La PII del prompt no llega en claro al almacen", leaked == "0", f"spans con el email en claro: {leaked}")

    grouped = ch_query(
        f"SELECT count(DISTINCT ServiceName) FROM otel.otel_traces "
        f"WHERE ResourceAttributes['service.namespace'] = '{RUN_TAG}'"
    )
    check(
        "La identidad de dos niveles permite agrupar por aplicacion",
        int(grouped or 0) >= 1,
        f"componentes bajo el namespace: {grouped}",
    )

    print()
    if _failures:
        print(f"\033[31m{len(_failures)} comprobacion(es) fallaron:\033[0m")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("\033[32mTodas las comprobaciones pasaron.\033[0m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
