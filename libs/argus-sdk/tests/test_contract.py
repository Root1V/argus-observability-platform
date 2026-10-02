"""Las cinco reglas del contrato de diseno, como tests.

Si estas fallan, el SDK ha dejado de ser seguro de adoptar. Con quince o mas
aplicaciones dependiendo de el, un fallo aqui las afecta a todas.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

from argus._config import Config

TIMEOUT = 120


def _run(body: str, env_prefix: str = "") -> subprocess.CompletedProcess[str]:
    """Ejecuta en un proceso limpio.

    Es obligatorio: OpenTelemetry solo deja fijar el proveedor global una vez
    por proceso, asi que probar la inicializacion dentro del proceso de pytest
    contaminaria el resto de los tests.
    """
    return subprocess.run(
        [sys.executable, "-c", env_prefix + textwrap.dedent(body)],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )


# --- Regla 1: nunca tumba la aplicacion -------------------------------------


def test_init_with_unreachable_collector_does_not_raise() -> None:
    """El caso mas comun en el dia a dia: el plano central esta suspendido.

    La aplicacion tiene que arrancar y funcionar igual. Perder telemetria es
    aceptable; no arrancar, no.
    """
    result = _run(
        """
        import warnings
        warnings.simplefilter("ignore")
        import argus

        # Puerto sin nadie escuchando.
        h = argus.init("test-svc", endpoint="http://127.0.0.1:1", namespace="test-app")
        with argus.step("work") as s:
            s.set(n=1)
        h.force_flush(timeout_millis=200)
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_init_with_garbage_config_degrades_to_noop() -> None:
    result = _run(
        """
        import os, warnings
        warnings.simplefilter("ignore")
        os.environ["ARGUS_MAX_QUEUE_SIZE"] = "no-es-un-numero"
        os.environ["ARGUS_PROTOCOL"] = "carrier-pigeon"
        os.environ["ARGUS_PROPAGATE"] = "sometimes"
        import argus

        h = argus.init("test-svc")
        # Valores invalidos caen a los defaults en vez de reventar.
        assert h.config.protocol == "grpc"
        assert h.config.propagate == "never"
        assert h.config.max_queue_size == 2048
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_queue_is_bounded_so_memory_cannot_grow_unbounded() -> None:
    """Con el Collector caido, la cola descarta en vez de crecer.

    Sin esto, una aplicacion con el backend caido acaba agotando la memoria
    del host, que es un fallo mucho peor que el que intentabas observar.
    """
    result = _run(
        """
        import warnings
        warnings.simplefilter("ignore")
        import argus

        h = argus.init("test-svc", endpoint="http://127.0.0.1:1")
        for i in range(20_000):
            with argus.step("spam") as s:
                s.set(i=i)
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# --- Regla 2: idempotente ----------------------------------------------------


def test_double_init_warns_and_is_noop() -> None:
    result = _run(
        """
        import warnings
        import argus

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            first = argus.init("svc-a", endpoint="http://127.0.0.1:1")
            second = argus.init("svc-b", endpoint="http://127.0.0.1:1")

        assert first is second, "la segunda llamada devolvio un handle distinto"
        assert second.config.service == "svc-a", "la segunda llamada piso la configuracion"
        assert any("no-op" in str(w.message) for w in caught), "no avisa de la doble llamada"
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_double_init_does_not_duplicate_log_handlers() -> None:
    result = _run(
        """
        import logging, warnings
        warnings.simplefilter("ignore")
        import argus

        argus.init("svc", endpoint="http://127.0.0.1:1")
        before = len(logging.getLogger().handlers)
        argus.init("svc", endpoint="http://127.0.0.1:1")
        assert len(logging.getLogger().handlers) == before
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# --- Regla 3: no-op si no esta configurada -----------------------------------


def test_helpers_work_without_init() -> None:
    """Los decoradores funcionan sin `init()`, con coste cero.

    Esto es lo que permite instrumentar librerias compartidas tuyas sin
    imponer telemetria a quien las importe.
    """
    result = _run(
        """
        import argus  # sin init()

        with argus.step("work") as s:
            s.set(n=1).outcome("ok")
        with argus.genai("chat", provider="ollama", request_model="m") as g:
            g.usage(input_tokens=1, output_tokens=2)
        assert not g.span.is_recording()
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_disabled_flag_turns_everything_off() -> None:
    result = _run(
        """
        import os, warnings
        warnings.simplefilter("ignore")
        os.environ["ARGUS_DISABLED"] = "true"
        import argus

        h = argus.init("svc")
        assert h.config.disabled is True
        with argus.step("work"):
            pass
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# --- Regla 4: cero configuracion en el caso normal ---------------------------


def test_zero_arg_init_reads_everything_from_env() -> None:
    """El contrato es de VARIABLES DE ENTORNO, no de una API de Python.

    Es lo que permite que un componente Go y uno Python se configuren copiando
    el mismo bloque, y que anadir un componente nuevo no exija aprender nada.
    """
    result = _run(
        """
        import os, warnings
        warnings.simplefilter("ignore")
        os.environ.update({
            "ARGUS_SERVICE": "idp-worker",
            "ARGUS_NAMESPACE": "intelligent-document-platform",
            "ARGUS_ROLE": "worker",
            "ARGUS_VERSION": "0.4.2",
            "ARGUS_ENVIRONMENT": "mac-dev",
            "ARGUS_ENDPOINT": "http://127.0.0.1:1",
        })
        import argus

        h = argus.init()
        cfg = h.config
        assert cfg.service == "idp-worker"
        assert cfg.namespace == "intelligent-document-platform"
        assert cfg.role == "worker"
        assert cfg.version == "0.4.2"
        assert cfg.environment == "mac-dev"
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_standard_otel_env_vars_are_honoured() -> None:
    """Una app que ya tiene OTel migra cambiando variables, sin tocar codigo."""
    result = _run(
        """
        import os, warnings
        warnings.simplefilter("ignore")
        os.environ.update({
            "OTEL_SERVICE_NAME": "legacy-api",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:1",
        })
        import argus

        cfg = argus.init().config
        assert cfg.service == "legacy-api"
        assert cfg.endpoint == "http://127.0.0.1:1"
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# --- Regla 5: superficie publica minima y fijada -----------------------------


def test_public_surface_is_pinned() -> None:
    """Si la API cabe en una pantalla, se adopta. Este test la mantiene ahi."""
    import argus

    expected = {
        "init", "handle", "Argus", "Config",
        "ASGIMiddleware", "middleware",
        "genai", "retrieval", "tool", "agent", "documents",
        "step", "instrument", "GenAISpan", "Step",
        "attributes", "capture_enabled", "mask",
        "propagate", "autoinst",
        "emit_wide_event", "GenAIMetrics", "__version__",
    }
    assert set(argus.__all__) == expected, (
        "La superficie publica cambio. Si es intencionado, actualiza este test "
        "y piensa si de verdad hace falta."
    )


# --- Detalles del transporte que el SDK debe absorber ------------------------


def test_grpc_headers_are_lowercased() -> None:
    """Regresion encontrada al conectar la tuberia real.

    gRPC rechaza las claves de metadatos en mayuscula con "Illegal header key",
    asi que `OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer xyz` reventaba el
    exportador. Quien escribe esa variable no tiene por que conocer ese detalle
    del protocolo: lo absorbe el SDK.
    """
    result = _run(
        """
        import os, warnings
        warnings.simplefilter("ignore")
        os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = "Authorization=Bearer secreto,X-Custom=valor"
        import argus
        from argus._config import Config
        from argus import _tracing

        cfg = Config.from_env("svc", endpoint="http://127.0.0.1:1")
        # La config conserva las claves tal cual las escribio el usuario...
        assert "Authorization" in cfg.headers

        # ...y el exportador gRPC las pasa en minuscula.
        exportador = _tracing._build_exporter(cfg)
        enviadas = dict(getattr(exportador, "_headers", ()) or ())
        assert all(k == k.lower() for k in enviadas), enviadas
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_sin_endpoint_SI_se_exporta() -> None:
    """La otra lectura de «no-op sin configurar», que es la cara.

    El punto 3 del contrato dice «no-op si NO llamas a `init()`», y eso lo
    verifica `test_helpers_work_without_init`. Pero la frase se leia tambien
    como «sin endpoint no se exporta», y esa lectura es FALSA: tras `init()`
    el endpoint cae a localhost y se manda de verdad.

    Prosodia la leyo asi —lo escribieron en su propio `.env.example`— y su
    suite de tests acabo metiendo 1.797 registros en el almacen compartido,
    899 de ellos mientras verificaban que no metian registros raros (D-111).

    Esta prueba fija la verdad incomoda para que la documentacion no pueda
    volver a sugerir la comoda.
    """
    cfg = Config.from_env("sin-endpoint")
    assert cfg.endpoint, "sin endpoint configurado deberia caer a uno por defecto"
    assert "localhost" in cfg.endpoint or "127.0.0.1" in cfg.endpoint
    assert not cfg.disabled, (
        "no configurar un endpoint NO desactiva la telemetria: "
        "la unica forma es ARGUS_DISABLED"
    )


def test_la_unica_forma_de_no_exportar_es_disabled(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_DISABLED", "1")
    assert Config.from_env("apagado").disabled is True


def test_el_contrato_no_dice_que_sin_endpoint_no_se_exporta() -> None:
    """El README es lo que la gente lee, y es donde estaba la ambiguedad.

    Se comprueba el texto porque el defecto no estaba en el codigo: el codigo
    siempre hizo esto. Estaba en una frase que invitaba a la lectura contraria
    y que ademas se presentaba como «verificada por tests».
    """
    from pathlib import Path

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert "No-op si NO llamas a `init()`" in readme, (
        "el contrato volvio a la redaccion ambigua"
    )
    assert "ARGUS_DISABLED=1" in readme, "falta decir cual es la unica forma de apagar"
