"""Captura de contenido: apagada por defecto, enmascarada y truncada."""

from __future__ import annotations

import json

import pytest
from argus_semconv import attributes as A
from argus_semconv import genai, mask
from argus_semconv._content import serialize


def test_content_is_off_by_default(spans, monkeypatch) -> None:
    """Sin activarlo explicitamente, el contenido NO sale de la aplicacion."""
    monkeypatch.delenv("ARGUS_CAPTURE_CONTENT", raising=False)
    monkeypatch.delenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", raising=False)

    with genai("chat", provider="ollama", request_model="m") as g:
        g.messages(input=[{"role": "user", "content": "secreto"}])

    assert A.GEN_AI_INPUT_MESSAGES not in spans.get_finished_spans()[0].attributes


def test_content_goes_to_attributes_not_events(spans, monkeypatch) -> None:
    """Langfuse lee el contenido de los ATRIBUTOS, no de los eventos de span.

    La guia canonica de las semconv dice eventos. Si la seguimos al pie de la
    letra, Langfuse ingiere el span y lo muestra sin input ni output, que es
    justo lo que lo hace util. Por eso emitimos en atributos.
    """
    monkeypatch.setenv("ARGUS_CAPTURE_CONTENT", "true")

    with genai("chat", provider="ollama", request_model="m") as g:
        g.messages(input=[{"role": "user", "content": "hola"}], output=[{"role": "assistant", "content": "que tal"}])

    finished = spans.get_finished_spans()[0]
    assert A.GEN_AI_INPUT_MESSAGES in finished.attributes
    assert A.GEN_AI_OUTPUT_MESSAGES in finished.attributes
    assert json.loads(finished.attributes[A.GEN_AI_INPUT_MESSAGES])[0]["content"] == "hola"
    assert finished.events == ()


@pytest.mark.parametrize(
    ("raw", "marker"),
    [
        ("escribe a juan.perez@empresa.com", "[EMAIL]"),
        ("la tarjeta es 4111 1111 1111 1111", "[CARD]"),
        ("mi dni es 12345678Z", "[DNI]"),
        ("clave sk-abcdefghijklmnopqrstuvwxyz", "[KEY]"),
        ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz", "[TOKEN]"),
        ("iban ES9121000418450200051332", "[IBAN]"),
    ],
)
def test_masking_redacts_known_pii(raw: str, marker: str) -> None:
    masked = mask(raw)
    assert marker in masked


def test_masking_applies_before_the_attribute_is_set(spans, monkeypatch) -> None:
    """Enmascarar en origen es la primera capa; el Collector es la segunda."""
    monkeypatch.setenv("ARGUS_CAPTURE_CONTENT", "true")

    with genai("chat", provider="ollama", request_model="m") as g:
        g.messages(input=[{"role": "user", "content": "mi correo es ana@test.com"}])

    payload = spans.get_finished_spans()[0].attributes[A.GEN_AI_INPUT_MESSAGES]
    assert "ana@test.com" not in payload
    assert "[EMAIL]" in payload


def test_oversized_content_is_truncated_and_flagged(spans, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_CAPTURE_CONTENT", "true")
    monkeypatch.setenv("ARGUS_CONTENT_MAX_BYTES", "64")

    with genai("chat", provider="ollama", request_model="m") as g:
        g.messages(input=[{"role": "user", "content": "x" * 5000}])

    attrs = spans.get_finished_spans()[0].attributes
    assert len(attrs[A.GEN_AI_INPUT_MESSAGES].encode("utf-8")) <= 64
    assert attrs[A.GEN_AI_CONTENT_TRUNCATED] is True


def test_serialize_never_raises_on_unserializable_payload() -> None:
    """Perder telemetria es aceptable; tumbar la app del usuario no lo es."""

    class Opaque:
        def __repr__(self) -> str:
            return "<opaque>"

    text, truncated = serialize({"obj": Opaque()})
    assert "opaque" in text
    assert truncated is False


def test_truncation_does_not_produce_invalid_utf8() -> None:
    """Cortar por bytes puede partir un caracter multibyte a la mitad."""
    text, truncated = serialize({"v": "ñ" * 200}, limit=31)
    assert truncated is True
    text.encode("utf-8").decode("utf-8")  # no lanza


@pytest.mark.parametrize(
    "valor",
    [
        "smoke-1789238954",   # identificador con una tirada de 10 digitos
        "v20260912",          # version con fecha
        "run-1234567890",     # id de ejecucion
        "idp-worker-3",       # nombre de componente
        "0.4.2",              # version semantica
    ],
)
def test_identifiers_are_not_mistaken_for_phone_numbers(valor: str) -> None:
    """Regresion encontrada con la prueba de humo end-to-end.

    El patron de telefono original capturaba cualquier tirada de diez digitos,
    asi que un `service.namespace` terminado en numero se convertia en
    `smoke-****`. Eso rompe agrupar por aplicacion y enrutar la notificacion a
    su dueno: destruye datos utiles sin proteger nada.

    Un telefono de verdad lleva separadores o prefijo internacional.
    """
    assert mask(valor) == valor


@pytest.mark.parametrize(
    "valor",
    ["+34 612 345 678", "555-123-4567", "+1-555-123-4567", "555.123.4567"],
)
def test_real_phone_numbers_are_still_redacted(valor: str) -> None:
    assert "[PHONE]" in mask(valor)
