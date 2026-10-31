"""El bucle: avanza un cursor sobre el fin de los spans, evalua, guarda y cuenta.

Tres decisiones:

1. **Un retraso fijo detras del presente.** Un span tarda en llegar al
   almacen lo que tarda la traza en cerrarse en el muestreo del gateway
   (`decision_wait`, 30 s) mas el lote y la insercion. Evaluar hasta
   `ahora - retraso` y no hasta `ahora` es lo que evita dar por vista una
   ventana a la que todavia le faltan spans. Un span que llegue con mas
   retraso que este —el Collector caido un rato— se pierde, y se acepta.

2. **El cursor solo avanza si la ventana se guardo.** Si ClickHouse falla, la
   misma ventana se reintenta en la vuelta siguiente; no hay huecos
   silenciosos.

3. **Las metricas cuentan el presente.** Al arrancar sin cursor se rellena el
   historico en la tabla, pero los contadores solo suman spans recientes: si
   sumaran el relleno, el primer arranque inyectaria un dia de rechazos en un
   minuto y la alerta de F5-03 saltaria por haber desplegado.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from .almacen import ErrorDeAlmacen
from .evals import Evaluacion, Span, evaluar
from .muestreo import Muestreo, lineas_base

log = logging.getLogger("evaluator")


class AlmacenDeEvaluacion(Protocol):
    def spans_terminados(self, desde: datetime, hasta: datetime) -> list[Span]: ...
    def p99_por_modelo(self, hasta: datetime) -> list[dict[str, Any]]: ...
    def guardar(self, filas: list[tuple[Span, Evaluacion, str]], evaluado_en: datetime) -> None: ...


class Contador(Protocol):
    def add(self, amount: int, attributes: dict[str, str]) -> None: ...


def _fecha(texto: str) -> datetime:
    """`2026-10-27 14:09:01.926187000` (nanosegundos, sin zona) -> datetime UTC."""
    base, _, frac = texto.partition(".")
    return datetime.strptime(f"{base}.{(frac or '0')[:6]:0<6}", "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=UTC)


class Cursor:
    """Hasta donde se ha evaluado, en un fichero. Sin fichero, `None`."""

    def __init__(self, ruta: Path) -> None:
        self.ruta = ruta

    def leer(self) -> datetime | None:
        try:
            return datetime.fromisoformat(json.loads(self.ruta.read_text())["hasta"])
        except (OSError, ValueError, KeyError):
            return None

    def escribir(self, hasta: datetime) -> None:
        tmp = self.ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps({"hasta": hasta.isoformat()}))
        tmp.replace(self.ruta)


class Evaluador:
    def __init__(
        self,
        almacen: AlmacenDeEvaluacion,
        cursor: Cursor,
        *,
        contador: Contador | None = None,
        muestreo: Muestreo | None = None,
        retraso: timedelta = timedelta(minutes=5),
        tramo: timedelta = timedelta(minutes=10),
        relleno: timedelta = timedelta(hours=24),
        presente: timedelta = timedelta(minutes=15),
        refresco_p99: timedelta = timedelta(hours=1),
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.almacen = almacen
        self.cursor = cursor
        self.contador = contador
        self.muestreo = muestreo or Muestreo()
        self.retraso, self.tramo, self.relleno = retraso, tramo, relleno
        self.presente, self.refresco_p99 = presente, refresco_p99
        self.reloj = reloj
        self._p99_en: datetime | None = None
        self.stats: Counter[str] = Counter()

    def _refrescar_p99(self, ahora: datetime) -> None:
        if self._p99_en and ahora - self._p99_en < self.refresco_p99:
            return
        try:
            self.muestreo.p99 = lineas_base(self.almacen.p99_por_modelo(ahora))
            self._p99_en = ahora
        except ErrorDeAlmacen as exc:
            # Sin linea base la muestra sigue: errores y azar. Se reintenta en la vuelta siguiente.
            log.warning("evaluator.p99_sin_linea_base", extra={"error": str(exc)})

    def vuelta(self) -> int:
        """Evalua lo que haya entre el cursor y `ahora - retraso`. Devuelve los spans vistos."""
        ahora = self.reloj()
        hasta_total = ahora - self.retraso
        desde = self.cursor.leer() or (hasta_total - self.relleno)
        self._refrescar_p99(ahora)
        vistos = 0
        while desde < hasta_total:
            hasta = min(desde + self.tramo, hasta_total)
            try:
                vistos += self._tramo(desde, hasta, ahora)
            except ErrorDeAlmacen as exc:
                self.stats["errores_de_almacen"] += 1
                log.error("evaluator.tramo_fallido", extra={"desde": desde.isoformat(), "error": str(exc)})
                break
            self.cursor.escribir(hasta)
            desde = hasta
        return vistos

    def _tramo(self, desde: datetime, hasta: datetime, ahora: datetime) -> int:
        spans = self.almacen.spans_terminados(desde, hasta)
        filas: list[tuple[Span, Evaluacion, str]] = []
        for s in spans:
            muestra = self.muestreo.motivo(s)
            for e in evaluar(s, en_muestra=bool(muestra)):
                filas.append((s, e, muestra))
        self.almacen.guardar(filas, ahora)
        self.stats["spans"] += len(spans)
        self.stats["evaluaciones"] += len(filas)
        for s, e, _ in filas:
            self.stats[str(e.resultado)] += 1
            if self.contador is not None and ahora - _fecha(s.fin) <= self.presente:
                # `argus.eval.app` y no `argus.app`: el SDK pone `argus.app` en
                # el Resource del evaluador, y al convertir a etiquetas el del
                # Resource pisa al del punto. Todas las series decian `argus`.
                self.contador.add(1, {
                    "argus.eval.app": s.app,
                    "argus.eval.service": s.componente,
                    "argus.eval.name": e.eval,
                    "argus.eval.result": str(e.resultado),
                    "gen_ai.request.model": s.modelo_pedido or s.modelo,
                })
        return len(spans)
