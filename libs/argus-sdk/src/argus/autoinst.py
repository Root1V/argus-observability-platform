"""Deteccion y activacion de auto-instrumentaciones.

La estrategia que recomienda la comunidad OTel: empezar por auto-instrumentacion
para tener linea base, y anadir manual solo para las operaciones de negocio que
la automatica no puede capturar.

Aqui se detecta por introspeccion que instrumentaciones estan instaladas y se
activan las que aplican. Nada es obligatorio: si un paquete no esta, se salta
en silencio. Una app que no habla con Kafka no deberia tener que pensar en ello.
"""

from __future__ import annotations

import importlib
import warnings
from collections.abc import Callable

# (modulo de instrumentacion, clase, nombre legible)
_CANDIDATES: tuple[tuple[str, str, str], ...] = (
    ("opentelemetry.instrumentation.httpx", "HTTPXClientInstrumentor", "httpx"),
    ("opentelemetry.instrumentation.requests", "RequestsInstrumentor", "requests"),
    ("opentelemetry.instrumentation.sqlalchemy", "SQLAlchemyInstrumentor", "sqlalchemy"),
    ("opentelemetry.instrumentation.asyncpg", "AsyncPGInstrumentor", "asyncpg"),
    ("opentelemetry.instrumentation.redis", "RedisInstrumentor", "redis"),
    ("opentelemetry.instrumentation.celery", "CeleryInstrumentor", "celery"),
    ("opentelemetry.instrumentation.aiokafka", "AIOKafkaInstrumentor", "aiokafka"),
)


def activate(skip: frozenset[str] = frozenset()) -> list[str]:
    """Activa todas las auto-instrumentaciones disponibles.

    Devuelve los nombres de las que se activaron. Idempotente: las
    instrumentaciones de OTel ignoran un segundo `instrument()`.
    """
    activated: list[str] = []

    for module_path, class_name, label in _CANDIDATES:
        if label in skip:
            continue
        try:
            module = importlib.import_module(module_path)
            instrumentor = getattr(module, class_name)()
            if getattr(instrumentor, "is_instrumented_by_opentelemetry", False):
                continue
            instrumentor.instrument()
            activated.append(label)
        except ImportError:
            continue  # el paquete no esta instalado: es lo normal
        except Exception as exc:  # noqa: BLE001
            warnings.warn(
                f"Argus: no se pudo activar la instrumentacion de {label}: {exc}",
                RuntimeWarning,
                stacklevel=3,
            )

    return activated


def activate_genai(skip: frozenset[str] = frozenset()) -> list[str]:
    """Activa la instrumentacion GenAI disponible.

    OpenLIT cubre 25+ proveedores (Ollama, vLLM, OpenAI, Anthropic, Bedrock) y
    20+ frameworks (LangChain, LangGraph, CrewAI, LlamaIndex), que es
    practicamente todo el portafolio de una vez. Antes de escribir
    instrumentacion a mano conviene usar la que ya existe: gestiona la
    propagacion de contexto y la creacion de spans, que es donde mas se
    equivoca la instrumentacion manual.
    """
    if "openlit" in skip:
        return []
    try:
        import openlit

        # No inicializa su propio pipeline: se engancha al TracerProvider que
        # ya configuramos, para que todo acabe en el mismo sitio.
        openlit.init(disable_metrics=True)
        return ["openlit"]
    except ImportError:
        return []
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"Argus: no se pudo activar OpenLIT: {exc}", RuntimeWarning, stacklevel=3)
        return []


def instrument_fastapi(app: object) -> bool:
    """Instrumenta una app FastAPI, si la instrumentacion esta disponible."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)  # type: ignore[arg-type]
        return True
    except Exception:  # noqa: BLE001
        return False


def instrument_sqlalchemy_engine(engine: object) -> bool:
    """Instrumenta un engine de SQLAlchemy ya creado."""
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument(engine=engine)
        return True
    except Exception:  # noqa: BLE001
        return False


_EXTRA_HOOKS: list[Callable[[], None]] = []


def register_hook(hook: Callable[[], None]) -> None:
    """Permite a una aplicacion anadir su propia instrumentacion al arranque."""
    _EXTRA_HOOKS.append(hook)


def run_hooks() -> None:
    for hook in _EXTRA_HOOKS:
        try:
            hook()
        except Exception as exc:  # noqa: BLE001
            warnings.warn(f"Argus: hook de instrumentacion fallo: {exc}", RuntimeWarning, stacklevel=3)
