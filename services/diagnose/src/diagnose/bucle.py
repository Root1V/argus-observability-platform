"""El bucle: busca incidentes sin causa, los diagnostica y devuelve el resultado.

**Por que un bucle que pregunta y no una cola.** El almacen de incidentes ya es
la cola: un incidente abierto con `root_cause` vacio ES trabajo pendiente, y
sobrevive a un reinicio porque el alert-bus lo persiste (D-103). Montar una
cola aparte seria tener dos sitios donde mirar que puede decir cosas distintas.

De ahi sale, gratis, la propiedad que D-012 queria de Temporal: **esto es
reanudable**. Si el proceso muere a mitad de un diagnostico, el incidente sigue
sin causa y el siguiente ciclo lo vuelve a coger. No hace falta durabilidad de
workflow para algo cuyo estado vive en otro sitio — y D-013 ya dijo que L3 no
es un workflow. Temporal hara falta para L4, que es el que espera aprobacion
humana.

**Idempotencia.** `enrich` sobreescribe campos, no acumula, asi que diagnosticar
dos veces el mismo incidente da el mismo resultado. Lo que se evita con el
periodo de espera no es la correccion, es el gasto.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from .almacen import Almacen
from .config import Settings
from .expediente import Expediente
from .juicio import Juez, JuezDeReglas, Veredicto
from .recoleccion import recolectar

log = logging.getLogger("diagnose.bucle")


class Diagnosticador:
    """Un ciclo = preguntar, elegir, recolectar, juzgar, devolver."""

    def __init__(
        self,
        settings: Settings,
        *,
        almacen: Almacen | None = None,
        juez: Juez | None = None,
        cliente: httpx.Client | None = None,
        sombra: Juez | None = None,
        investigacion: Any | None = None,
    ) -> None:
        self.settings = settings
        self.juez: Juez = juez or JuezDeReglas()
        # El juez en sombra: juzga, se registra, y no toca el aviso.
        self.sombra = sombra
        # L4 en sombra (`SombraL4`): lo que el triage deja pasar se investiga
        # en segundo plano. Tampoco toca el aviso.
        self.investigacion = investigacion
        self._almacen = almacen
        self._http = cliente or httpx.Client(timeout=settings.timeout_s())
        # Huella -> cuando se juzgo por ultima vez y con cuantas senales. Las
        # dos cosas: el tiempo evita rediagnosticar en bucle, y el contador
        # permite volver antes cuando el incidente ha CRECIDO —mas senales es
        # mas evidencia, y puede que ahora si se explique.
        self._juzgados: dict[str, tuple[datetime, int]] = {}
        self.stats = {
            "ciclos": 0,
            "diagnosticados": 0,
            "con_causa": 0,
            "sin_causa": 0,
            "fallidos": 0,
            "saltados_por_espera": 0,
            "sombra_juzgados": 0,
            "sombra_fallidos": 0,
            "triage_investigar": 0,
            "triage_no": 0,
        }

    # --- Un ciclo ------------------------------------------------------------

    def ciclo(self) -> list[tuple[Expediente, Veredicto]]:
        self.stats["ciclos"] += 1
        incidentes = self._incidentes()
        if not incidentes:
            return []

        pendientes = [i for i in incidentes if self._toca(i)]
        if not pendientes:
            return []

        # El registro y el historico se piden UNA vez por ciclo, no por
        # incidente: son los mismos para todos y una tormenta de incidentes no
        # tiene por que ser una tormenta de peticiones.
        registro = self._registro()
        historico = self._historico()

        salida: list[tuple[Expediente, Veredicto]] = []
        for incidente in pendientes[: self.settings.max_por_ciclo]:
            try:
                resultado = self._diagnosticar(incidente, registro, historico)
            except Exception as exc:  # noqa: BLE001 - un diagnostico que falla no para el bucle
                self.stats["fallidos"] += 1
                log.error(
                    "diagnose.failed",
                    extra={"incidente": incidente.get("id"), "error": str(exc)},
                )
                continue
            salida.append(resultado)
        return salida

    def _diagnosticar(
        self,
        incidente: dict[str, Any],
        registro: list[dict[str, Any]] | None,
        historico: list[dict[str, Any]] | None,
    ) -> tuple[Expediente, Veredicto]:
        expediente = recolectar(
            incidente,
            almacen=self._almacen,
            registro=registro,
            historico=historico,
        )
        veredicto = self.juez.juzgar(expediente)
        # La memoria va con el veredicto la diga quien la diga: es un hecho del
        # historico, no una opinion del juez (F3-06).
        veredicto.similar_incidents = [p.resumen() for p in expediente.memoria]

        self.stats["diagnosticados"] += 1
        self.stats["con_causa" if veredicto.tiene_causa else "sin_causa"] += 1
        self._juzgados[expediente.huella] = (datetime.now(UTC), expediente.senales)

        self._devolver(expediente.huella, veredicto)
        self._en_sombra(expediente, veredicto)
        self._triage(expediente, veredicto)
        log.info(
            "diagnose.done",
            extra={
                "incidente": expediente.incidente_id,
                "huella": expediente.huella,
                "causa": bool(veredicto.tiene_causa),
                "confianza": veredicto.confidence,
                "origen": veredicto.origen,
                "lagunas": len(expediente.lagunas),
            },
        )
        return expediente, veredicto

    def _en_sombra(self, expediente: Expediente, de_las_reglas: Veredicto) -> None:
        """El juez con modelo, despues de que el aviso ya tenga su veredicto.

        Despues y no antes por dos razones: el veredicto de las reglas no
        espera a una inferencia de segundos, y un fallo aqui —red, credencial,
        un modelo que no valida— no puede costarle nada al diagnostico que si
        llega a una persona. Por eso todo va dentro de un `try`.
        """
        if self.sombra is None:
            return
        try:
            del_modelo = self.sombra.juzgar(expediente)
        except Exception as exc:  # noqa: BLE001 - la sombra nunca tumba el diagnostico
            self.stats["sombra_fallidos"] += 1
            log.warning("sombra.failed", extra={"huella": expediente.huella, "error": str(exc)[:200]})
            return
        self.stats["sombra_juzgados"] += 1
        registro = {
            "cuando": datetime.now(UTC).isoformat(),
            "huella": expediente.huella,
            "incidente": expediente.incidente_id,
            "app": expediente.app,
            "componente": expediente.componente,
            "firma": expediente.firma[:120],
            "reglas": de_las_reglas.model_dump(),
            "modelo": del_modelo.model_dump(),
            # Si coinciden en tener causa o no. La comparacion de fondo la hace
            # el banco; esto es para ver de un vistazo cuanto discrepan.
            "coinciden_en_tener_causa": de_las_reglas.tiene_causa == del_modelo.tiene_causa,
        }
        log.info(
            "sombra.done",
            extra={"huella": expediente.huella, "origen": del_modelo.origen,
                   "causa": del_modelo.tiene_causa, "confianza": del_modelo.confidence},
        )
        if self.settings.sombra_path:
            try:
                import json
                from pathlib import Path

                ruta = Path(self.settings.sombra_path)
                ruta.parent.mkdir(parents=True, exist_ok=True)
                with ruta.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(registro, ensure_ascii=False) + "\n")
            except Exception as exc:  # noqa: BLE001
                log.warning("sombra.write_failed", extra={"error": str(exc)[:200]})

    def _triage(self, expediente: Expediente, veredicto: Veredicto) -> None:
        """F3-03: si L4 esta encendido, decide si este incidente se investiga, y lo dice."""
        if self.investigacion is None:
            return
        from .triage import merece_investigacion

        si, motivo = merece_investigacion(expediente, veredicto)
        self.stats["triage_investigar" if si else "triage_no"] += 1
        log.info("triage.done", extra={"huella": expediente.huella, "investigar": si, "motivo": motivo})
        if si:
            self.investigacion.encolar(expediente, veredicto, motivo)

    # --- A quien se le pregunta y a quien se le contesta ---------------------

    def _toca(self, incidente: dict[str, Any]) -> bool:
        """Si este incidente merece un diagnostico ahora.

        Se salta lo que ya tiene causa, lo resuelto y los SINTOMAS. Los
        sintomas se saltan porque su causa ya tiene su propio incidente y es
        ahi donde el diagnostico sirve: diagnosticar los dos es pagar dos veces
        por una respuesta, que es la misma razon por la que no se notifican.
        """
        if incidente.get("root_cause"):
            return False
        if incidente.get("state") == "resolved":
            return False
        if incidente.get("suppressed_by"):
            return False

        huella = str(incidente.get("fingerprint", ""))
        previo = self._juzgados.get(huella)
        if previo is None:
            return True

        cuando, senales = previo
        if (datetime.now(UTC) - cuando).total_seconds() >= self.settings.reintentar_tras_s:
            return True
        # Ha crecido al doble: hay evidencia nueva, vale la pena volver.
        if int(incidente.get("count", 1) or 1) >= senales * 2:
            return True
        self.stats["saltados_por_espera"] += 1
        return False

    def _incidentes(self) -> list[dict[str, Any]]:
        return self._get("/incidents") or []

    def _registro(self) -> list[dict[str, Any]] | None:
        return self._get("/registry")

    def _historico(self) -> list[dict[str, Any]] | None:
        return self._get("/incidents/history?dias=30")

    def _get(self, ruta: str) -> list[dict[str, Any]] | None:
        """`None` significa «no pude preguntar», y eso acaba en una laguna.

        Distinto de `[]`, que significa «no hay». La diferencia la lee quien
        juzga: un diagnostico hecho sin historico no vale lo mismo que uno
        hecho sabiendo que no hay historico.
        """
        try:
            r = self._http.get(f"{self.settings.alertbus_url}{ruta}")
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001 - preguntar y no poder es un dato, no un fallo
            log.warning("alertbus.unreachable", extra={"ruta": ruta, "error": str(exc)})
            return None

    def _devolver(self, huella: str, veredicto: Veredicto) -> None:
        """POST a `enrich`, que EDITA el aviso que ya salio (D-015).

        Que esto falle no invalida el diagnostico —el expediente y el veredicto
        existen y estan en el log— pero si lo deja sin llegar a nadie, y por eso
        se cuenta aparte.
        """
        try:
            r = self._http.post(
                f"{self.settings.alertbus_url}/incidents/{huella}/enrich",
                json=veredicto.como_enriquecimiento(),
            )
            r.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            self.stats["fallidos"] += 1
            log.error("enrich.failed", extra={"huella": huella, "error": str(exc)})

    # --- Para siempre --------------------------------------------------------

    def correr(self, *, ciclos: int | None = None) -> None:
        """Bucle principal. `ciclos` acotado existe para las pruebas."""
        hechos = 0
        while ciclos is None or hechos < ciclos:
            try:
                self.ciclo()
            except Exception as exc:  # noqa: BLE001 - el bucle no se cae
                log.error("ciclo.failed", extra={"error": str(exc)})
            hechos += 1
            if ciclos is None or hechos < ciclos:
                time.sleep(self.settings.intervalo_s)

    def cerrar(self) -> None:
        self._http.close()
        if self._almacen is not None:
            self._almacen.cerrar()
