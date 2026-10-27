"""F4-03 para L4: el investigador en sombra. Investiga en segundo plano, registra, nunca notifica.

Va en un hilo aparte con una cola corta porque una investigacion tarda minutos
y el bucle de L3 diagnostica cada diez segundos: esperarla seria dejar de
diagnosticar. La cola es corta a proposito —cinco— y lo que no cabe se cuenta y
se tira: si el triage deja pasar mas de lo que la GPU puede investigar, la
respuesta es afinar el triage, no acumular horas de cola.

Lo que sale va a su propio JSONL, al lado del de la sombra de L3, para poder
comparar sobre incidentes reales lo que dijeron las reglas, el juez con modelo
y el investigador.

**Durable (F4-01).** Lo encolado se guarda en `pendientes` (SQLite) con su
`run_id` antes de investigarse, y se marca al terminar. Si el proceso muere a
mitad, al arrancar se retoma con el MISMO `run_id`: el diario de synaptum
reproduce los pasos ya pagados y solo se llama al modelo para lo que faltaba.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .expediente import Expediente
from .investigador import Resultado
from .juicio import Veredicto

log = logging.getLogger("diagnose.sombra_l4")


class SombraL4:
    def __init__(
        self,
        investigar: Callable[[str, str], Resultado],
        *,
        ruta: str = "",
        pendientes: str = "",
        nuevo_run_id: Callable[[str, str], str] | None = None,
        max_cola: int = 5,
        repetir_tras_s: float = 6 * 3600,
    ) -> None:
        # `investigar(huella, run_id)`; `nuevo_run_id(huella, epoca)`.
        self._investigar = investigar
        self._nuevo_run_id = nuevo_run_id or (lambda huella, epoca: f"l4-{huella}-{epoca}")
        self._pendientes = _Pendientes(pendientes) if pendientes else None
        self._ruta = Path(ruta) if ruta else None
        self._cola: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=max_cola)
        self._repetir_tras_s = repetir_tras_s
        # Huella → cuando se encolo. Un incidente que sigue abierto no se
        # vuelve a investigar cada diez segundos; si sigue abierto en seis
        # horas, si.
        self._encoladas: dict[str, datetime] = {}
        self._lock = threading.Lock()
        self.stats = {"encoladas": 0, "descartadas_cola_llena": 0, "repetidas": 0, "investigadas": 0,
                      "parciales": 0, "fallidas": 0}
        self.stats["retomadas"] = 0
        if self._pendientes is not None:
            for trabajo in self._pendientes.sin_terminar()[:max_cola]:
                self._cola.put_nowait(trabajo)
                self._encoladas[trabajo["huella"]] = datetime.fromisoformat(trabajo["encolada"])
                self.stats["retomadas"] += 1
        self._hilo = threading.Thread(target=self._trabajar, name="sombra-l4", daemon=True)
        self._hilo.start()

    def encolar(self, exp: Expediente, veredicto: Veredicto, motivo: str) -> bool:
        ahora = datetime.now(UTC)
        with self._lock:
            previo = self._encoladas.get(exp.huella)
            if previo is not None and (ahora - previo).total_seconds() < self._repetir_tras_s:
                self.stats["repetidas"] += 1
                return False
            trabajo = {
                "huella": exp.huella, "incidente": exp.incidente_id, "app": exp.app, "componente": exp.componente,
                "firma": exp.firma[:120], "motivo": motivo, "reglas": veredicto.model_dump(),
                "encolada": ahora.isoformat(),
            }
            trabajo["run_id"] = self._nuevo_run_id(exp.huella, trabajo["encolada"])
            try:
                self._cola.put_nowait(trabajo)
            except queue.Full:
                self.stats["descartadas_cola_llena"] += 1
                log.warning("sombra_l4.cola_llena", extra={"huella": exp.huella})
                return False
            if self._pendientes is not None:
                self._pendientes.guardar(trabajo)
            self._encoladas[exp.huella] = ahora
            self.stats["encoladas"] += 1
        return True

    def _trabajar(self) -> None:
        while (trabajo := self._cola.get()) is not None:
            try:
                r = self._investigar(trabajo["huella"], trabajo["run_id"])
            except Exception as exc:  # noqa: BLE001 - la sombra nunca tumba nada
                self.stats["fallidas"] += 1
                log.warning("sombra_l4.failed", extra={"huella": trabajo["huella"], "error": str(exc)[:300]})
                if self._pendientes is not None:
                    self._pendientes.terminar(trabajo["run_id"], "fallida")
                continue
            if self._pendientes is not None:
                self._pendientes.terminar(trabajo["run_id"], "hecha")
            self.stats["investigadas"] += 1
            self.stats["parciales"] += int(r.parcial)
            log.info("sombra_l4.done", extra={"huella": r.huella, "final": r.final, "causa": bool(r.causa),
                                              "herramientas": len(r.herramientas_usadas), "segundos": r.segundos})
            self._escribir({**trabajo, "terminada": datetime.now(UTC).isoformat(),
                            "l4": {**asdict(r), "parcial": r.parcial}})

    def _escribir(self, registro: dict[str, Any]) -> None:
        if self._ruta is None:
            return
        try:
            self._ruta.parent.mkdir(parents=True, exist_ok=True)
            with self._ruta.open("a", encoding="utf-8") as f:
                f.write(json.dumps(registro, ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001
            log.warning("sombra_l4.write_failed", extra={"error": str(exc)[:200]})

    def esperar(self, timeout: float | None = None) -> None:
        """Para las pruebas: hasta que la cola se vacie."""
        self._cola.put(None)
        self._hilo.join(timeout)


class _Pendientes:
    """Lo encolado y no terminado, en disco. Es lo que permite retomar tras un reinicio."""

    def __init__(self, ruta: str) -> None:
        import sqlite3

        self._db = sqlite3.connect(ruta, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("""CREATE TABLE IF NOT EXISTS pendientes (
                run_id TEXT PRIMARY KEY, huella TEXT, trabajo TEXT, estado TEXT, actualizada TEXT)""")
            self._db.commit()

    def guardar(self, trabajo: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO pendientes VALUES (?, ?, ?, 'pendiente', ?)",
                             (trabajo["run_id"], trabajo["huella"], json.dumps(trabajo, ensure_ascii=False),
                              datetime.now(UTC).isoformat()))
            self._db.commit()

    def terminar(self, run_id: str, estado: str) -> None:
        with self._lock:
            self._db.execute("UPDATE pendientes SET estado = ?, actualizada = ? WHERE run_id = ?",
                             (estado, datetime.now(UTC).isoformat(), run_id))
            self._db.commit()

    def sin_terminar(self) -> list[dict[str, Any]]:
        with self._lock:
            filas = self._db.execute("SELECT trabajo FROM pendientes WHERE estado = 'pendiente' ORDER BY actualizada")
            return [json.loads(f[0]) for f in filas]
