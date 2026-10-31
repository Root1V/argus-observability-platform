"""La muestra para las evaluaciones caras: todos los fallos, todo el P99 y un 5 % del resto (PLAN §7.4c).

**Por traza y determinista.** El azar sale de un hash del `trace_id`, no de
`random`: los spans de una misma traza entran o salen juntos —una evaluacion de
consistencia necesita la conversacion entera, no un turno suelto—, y volver a
evaluar la misma ventana tras un reinicio elige exactamente la misma muestra.

**El P99 se mide contra la semana, no contra la ventana.** En diez minutos hay
tres llamadas; el P99 de tres llamadas es la mas lenta de ellas, que es decir
«un tercio de todo». La linea base es la de los ultimos 7 dias por componente,
operacion y modelo pedido, y con menos de `MINIMO_PARA_P99` llamadas no hay P99:
esas llamadas solo entran por error o por azar.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .evals import Span

MINIMO_PARA_P99 = 50


def _clave(componente: str, operacion: str, modelo: str) -> tuple[str, str, str]:
    return componente, operacion, modelo


@dataclass
class Muestreo:
    porcentaje: float = 5.0
    # (componente, operacion, modelo pedido) -> P99 de la duracion en ns.
    p99: dict[tuple[str, str, str], int] = field(default_factory=dict)

    def motivo(self, span: Span) -> str:
        """Por que entra en la muestra (`error`, `p99`, `azar`) o `""` si no entra."""
        if span.error:
            return "error"
        umbral = self.p99.get(_clave(span.componente, span.operacion, span.modelo_pedido))
        if umbral and span.duracion_ns >= umbral:
            return "p99"
        if _cubeta(span.trace_id) < self.porcentaje * 100:
            return "azar"
        return ""


def _cubeta(trace_id: str) -> int:
    """0..9999, estable para un mismo `trace_id`."""
    return int(hashlib.sha256(trace_id.encode()).hexdigest()[:8], 16) % 10_000


def lineas_base(filas: list[dict]) -> dict[tuple[str, str, str], int]:
    """De las filas de `Almacen.p99_por_modelo` al diccionario que usa `Muestreo`."""
    return {
        _clave(f["componente"], f["operacion"], f["modelo"]): int(float(f["p99"]))
        for f in filas
        if int(f["n"]) >= MINIMO_PARA_P99
    }
