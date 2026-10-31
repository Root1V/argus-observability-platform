"""`python -m evaluator` arranca el bucle; `python -m evaluator informe [--horas N]` resume la tabla."""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from datetime import timedelta

from .almacen import TABLA, Almacen
from .bucle import Cursor, Evaluador
from .config import Settings
from .muestreo import Muestreo

log = logging.getLogger("evaluator")

INFORME = f"""
SELECT app, componente, eval, resultado, count() AS n, any(motivo) AS ejemplo
FROM {TABLA} FINAL
WHERE span_fin > now() - toIntervalHour({{horas:UInt32}})
  -- Solo la version vigente de cada evaluacion: al cambiar su criterio, las
  -- filas viejas se quedan con su version y no deben sumarse a las nuevas.
  AND (eval, version) IN (SELECT eval, max(version) FROM {TABLA} GROUP BY eval)
GROUP BY app, componente, eval, resultado
ORDER BY app, componente, eval, resultado
"""


def _almacen(s: Settings) -> Almacen:
    return Almacen(s.clickhouse_url, s.clickhouse_user, s.clickhouse_password)


def _contador():
    """El contador de resultados, o `None` si el SDK no esta: el bucle no depende de la metrica."""
    try:
        import argus
        from opentelemetry import metrics

        argus.init("evaluator", namespace="argus", role="worker", version="0.1.0")
        return metrics.get_meter("evaluator", "0.1.0").create_counter(
            "argus.eval.results", unit="{evaluacion}",
            description="Resultados de las evaluaciones online, por app, componente, evaluacion y modelo",
        )
    except Exception:  # noqa: BLE001
        log.warning("evaluator.sin_metricas")
        return None


def bucle(s: Settings) -> None:
    almacen = _almacen(s)
    almacen.preparar()
    ev = Evaluador(
        almacen, Cursor(s.cursor_path), contador=_contador(), muestreo=Muestreo(s.muestra_pct),
        retraso=timedelta(seconds=s.retraso_s), relleno=timedelta(hours=s.relleno_h),
    )
    del_juez = _evaluador_del_juez(s, almacen)
    parar = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: parar.set())
    log.info("evaluator.arranca", extra={"cursor": str(ev.cursor.leer())})
    while not parar.is_set():
        try:
            vistos = ev.vuelta()
            log.info(f"evaluator.vuelta spans={vistos} {dict(ev.stats)}")
            if del_juez is not None:
                filas = del_juez.vuelta()
                if filas:
                    log.info(f"evaluator.juez evaluaciones={len(filas)} {del_juez.stats}")
                    _contar(ev.contador, filas)
        except Exception:
            # Un fallo inesperado no tumba el servicio: se reintenta en la vuelta siguiente.
            log.exception("evaluator.vuelta_fallida")
        parar.wait(s.intervalo_s)


def _evaluador_del_juez(s: Settings, almacen: Almacen):
    """F5-02, si hay diario. Sin modelo (o sin credencial) solo se evalua el formato."""
    import os

    if s.diario_juez_path is None:
        return None
    from .juez import EstadoDiario, EvaluadorDelJuez

    juez = None
    if s.modelo and os.environ.get("AXONIUM_CLIENT_ID"):
        from .modelo_juez import JuezConModelo

        juez = JuezConModelo(s.modelo, razonamiento=s.razonamiento or None)
    elif s.modelo:
        log.warning("evaluator.juez.sin_credencial")
    log.info("evaluator.juez.arranca", extra={"diario": str(s.diario_juez_path), "modelo": s.modelo if juez else ""})
    return EvaluadorDelJuez(s.diario_juez_path, EstadoDiario.cargar(s.diario_estado_path), juez,
                            almacen.guardar, caras_por_hora=s.caras_por_hora)


def _contar(contador, filas) -> None:
    """El diario se lee en cuanto se escribe: todo lo que sale de aqui es presente."""
    if contador is None:
        return
    for span, e, _ in filas:
        contador.add(1, {"argus.eval.app": span.app, "argus.eval.service": span.componente,
                         "argus.eval.name": e.eval, "argus.eval.result": str(e.resultado),
                         "gen_ai.request.model": span.modelo})


def informe(s: Settings, horas: int) -> str:
    filas = _almacen(s).consulta(INFORME, horas=horas)
    lineas = [f"EVALUACIONES de las ultimas {horas} h"]
    actual = None
    for f in filas:
        if (f["app"], f["componente"]) != actual:
            actual = (f["app"], f["componente"])
            lineas.append(f"\n{f['app'] or '—'}/{f['componente']}")
        ejemplo = f" · {f['ejemplo'][:90]}" if f["resultado"] != "pasa" and f["ejemplo"] else ""
        lineas.append(f"  {f['eval']:<9} {f['resultado']:<11} {int(f['n']):>6}{ejemplo}")
    return "\n".join(lineas)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # Una linea por consulta, 144 en el relleno del primer arranque: ruido.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(prog="python -m evaluator")
    sub = parser.add_subparsers(dest="orden")
    inf = sub.add_parser("informe")
    inf.add_argument("--horas", type=int, default=24)
    args = parser.parse_args()
    s = Settings()
    if args.orden == "informe":
        print(informe(s, args.horas))
    else:
        bucle(s)


if __name__ == "__main__":
    main()
