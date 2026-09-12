"""La garantia central: sin SDK, esto es no-op con coste cero.

Es lo que permite instrumentar Axonium y synaptum sin imponer nada a quien los
importe. Si estos tests fallan, el paquete ha roto su contrato.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

# Estos casos corren en un subproceso LIMPIO a proposito: si otro test del
# mismo proceso ya configuro un TracerProvider global, la API deja de devolver
# no-ops y el test perderia todo su valor.

SNIPPET_HEADER = textwrap.dedent(
    """
    import sys
    from opentelemetry import trace
    from argus_semconv import genai, step, tool, agent, retrieval, documents

    # Sin SDK configurado, el proveedor global es el no-op por defecto.
    provider = trace.get_tracer_provider()
    assert type(provider).__name__ in ("NoOpTracerProvider", "ProxyTracerProvider"), type(provider).__name__
    """
)


def _run(body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", SNIPPET_HEADER + textwrap.dedent(body)],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_genai_is_noop_without_sdk() -> None:
    result = _run(
        """
        with genai("chat", provider="ollama", request_model="qwen2.5-7b") as g:
            g.usage(input_tokens=100, output_tokens=50)
            g.response(model="qwen2.5-7b", finish_reasons=["stop"])
            g.backend(backend_id="local-0", ttft_ms=42)
            g.messages(input=[{"role": "user", "content": "hola"}])
            g.attribution(feature="chat", use_case="support")
            assert not g.span.is_recording()
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_step_is_noop_without_sdk() -> None:
    result = _run(
        """
        with step("document.process", pages=42) as s:
            s.set(engine="paddle").outcome("ok")
        assert s.fields["argus.event"] == "document.process"
        assert "argus.duration_ms" in s.fields
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_all_helpers_work_without_sdk() -> None:
    """Ningun helper puede exigir un SDK para no reventar."""
    result = _run(
        """
        with agent("rca-investigator", agent_id="run-1") as a:
            a.set("custom.attr", 1)
        with tool("clickhouse_query", call_id="c-1"):
            pass
        with retrieval("pgvector", top_k=8) as r:
            documents(r, [("doc-a", 0.91), ("doc-b", 0.72)])
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_exception_propagates_but_is_recorded() -> None:
    """La instrumentacion registra el error y RE-LANZA. No se traga nada."""
    result = _run(
        """
        try:
            with step("will.fail"):
                raise ValueError("boom")
        except ValueError as exc:
            assert str(exc) == "boom"
            print("OK")
        else:
            sys.exit("la excepcion no se propago")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_import_does_not_pull_in_sdk() -> None:
    """El paquete no debe arrastrar el SDK ni exportadores.

    Es la comprobacion que impide la regresion mas cara: que alguien añada un
    import del SDK "solo para una cosita" y con ello imponga una version del
    SDK a las quince aplicaciones que importan Axonium.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                """
                import sys
                import argus_semconv  # noqa: F401

                forbidden = [m for m in sys.modules if m.startswith("opentelemetry.sdk")]
                forbidden += [m for m in sys.modules if m.startswith("opentelemetry.exporter")]
                assert not forbidden, f"argus_semconv arrastro: {forbidden}"
                print("OK")
                """
            ),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
