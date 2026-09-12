"""Propagacion fuera de HTTP: donde las trazas se parten de verdad.

En HTTP la propagacion W3C funciona sola. En Celery, Kafka y colas artesanales
no, y el fallo no da ningun error: el consumidor arranca una traza nueva en vez
de unirse a la del productor, y acabas con dos trazas donde deberia haber una.
"""

from __future__ import annotations

from argus import propagate
from argus_semconv import attributes as A
from opentelemetry import trace

# El TracerProvider, el exportador y el propagador compuesto vienen del
# conftest raiz: solo puede haber un proveedor global por proceso.


def test_message_headers_carry_the_trace(spans) -> None:
    """El caso Kafka/Redpanda: el contexto va en las cabeceras del mensaje."""
    tracer = trace.get_tracer("test")

    with tracer.start_as_current_span("orders.publish") as producer:
        expected_trace = producer.context.trace_id
        headers = propagate.inject_headers()

    assert "traceparent" in headers

    with propagate.extract_headers(headers, name="orders.consume") as consumer:
        assert consumer.context.trace_id == expected_trace

    # UNA sola traza, no dos.
    assert len({s.context.trace_id for s in spans.get_finished_spans()}) == 1


def test_payload_carries_the_trace_when_there_are_no_headers(spans) -> None:
    """El caso de las colas artesanales sobre Redis o Postgres."""
    tracer = trace.get_tracer("test")

    with tracer.start_as_current_span("job.enqueue") as producer:
        expected_trace = producer.context.trace_id
        payload = propagate.inject_payload({"task": "ocr", "doc_id": 42})

    assert propagate.PAYLOAD_KEY in payload
    assert payload["doc_id"] == 42  # no se destruye el payload original

    with propagate.extract_payload(payload, name="job.run") as consumer:
        assert consumer.context.trace_id == expected_trace


def test_missing_context_starts_a_new_trace_without_crashing(spans) -> None:
    """Un mensaje de antes de la migracion no debe romper al consumidor."""
    with propagate.extract_headers({}, name="orders.consume") as span:
        assert span.context.trace_id != 0

    with propagate.extract_payload(None, name="job.run") as span:
        assert span.context.trace_id != 0


def test_baggage_crosses_the_boundary(spans) -> None:
    """El baggage lleva atribucion entre procesos.

    Regla dura: pequeno, acotado y SIN PII. Viaja en cabeceras y cruza
    maquinas; todo lo que metas acaba en sitios que no controlas.
    """
    from opentelemetry import context as otel_context

    token = propagate.set_baggage(**{A.ARGUS_APP: "idp", A.ARGUS_RUN_ID: "run-123"})
    try:
        headers = propagate.inject_headers()
        assert "baggage" in headers
        assert "run-123" in headers["baggage"]
    finally:
        otel_context.detach(token)  # type: ignore[arg-type]


def test_run_opens_a_root_trace_for_cli_jobs(spans) -> None:
    """Los CLI y trabajos por lotes necesitan una traza raiz explicita."""
    with propagate.run("nightly.reindex", run_id="run-abc", app="idp") as span:
        assert span.attributes[A.ARGUS_RUN_ID] == "run-abc"
        assert span.attributes[A.ARGUS_APP] == "idp"

    finished = spans.get_finished_spans()[0]
    assert finished.name == "nightly.reindex"
    assert finished.parent is None


def test_end_to_end_api_to_queue_to_worker(spans) -> None:
    """La prueba que define la Fase 1 del plan.

    Una unidad de trabajo que cruza API -> cola -> worker tiene que producir
    UNA sola traza con todos los spans. Si salen dos, la propagacion fuera de
    HTTP no funciona y nada de lo que viene despues sirve.
    """
    tracer = trace.get_tracer("test")

    # 1. La API recibe la peticion y encola trabajo.
    with tracer.start_as_current_span("POST /documents") as api_span:
        root_trace = api_span.context.trace_id
        message = propagate.inject_payload({"doc_id": 7})

    # 2. El worker toma el mensaje en otro proceso y hace su trabajo.
    with propagate.extract_payload(message, name="document.process"):
        with tracer.start_as_current_span("ocr.extract"):
            pass

    finished = spans.get_finished_spans()
    trace_ids = {s.context.trace_id for s in finished}

    assert len(trace_ids) == 1, f"la traza se partio en {len(trace_ids)} trozos"
    assert trace_ids.pop() == root_trace
    assert {s.name for s in finished} == {"POST /documents", "document.process", "ocr.extract"}
