"""B-48: el cuadro de indicadores operacionales. Primero la línea base; los objetivos, después.

**Solo se restan horas del mismo reloj.** `opened_at` es del emisor y en 523 de
1.966 incidentes notificados va DESPUÉS de `notified_at`, que pone el
alert-bus (B-34). Restarlos daría tiempos negativos, o peor, positivos y
falsos. Por eso:

- `notified_at`, `enriched_at`, `acknowledged_at`, `last_seen_at` y
  `resolved_at` son del alert-bus, y con ellos se calcula sobre todo el
  histórico (MTTA, tiempo hasta el diagnóstico, duración visible).
- Lo que necesita la hora del EMISOR —el MTTD— sale de los eventos de B-49, y
  los desfases de reloj se cuentan aparte en vez de mezclarse con la media.

**Cada cifra lleva su `n` y su cobertura.** Un MTTA de 2 minutos con un acuse
de 1.966 avisos no es un MTTA: es una anécdota, y el cuadro lo dice.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from statistics import median
from typing import Any


def _fecha(valor: Any) -> datetime | None:
    if not valor:
        return None
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None


def _segundos(a: Any, b: Any) -> float | None:
    """`b - a` en segundos, o `None` si falta alguna."""
    fa, fb = _fecha(a), _fecha(b)
    return (fb - fa).total_seconds() if fa and fb else None


def _p90(valores: list[float]) -> float:
    ordenados = sorted(valores)
    return ordenados[min(len(ordenados) - 1, round(0.9 * (len(ordenados) - 1)))]


def tiempos(valores: Iterable[float | None], *, de: int) -> dict[str, Any]:
    """Mediana y P90 de unos tiempos, con cuántos hay y de cuántos podía haber."""
    lista = [v for v in valores if v is not None and v >= 0]
    salida: dict[str, Any] = {"n": len(lista), "de": de}
    if lista:
        salida |= {"mediana_s": round(median(lista), 1), "p90_s": round(_p90(lista), 1)}
    return salida


def proporcion(si: int, total: int) -> dict[str, Any]:
    return {"si": si, "de": total, **({"pct": round(100 * si / total, 1)} if total else {})}


# Las alertas que manda vmalert (Alertmanager): su `opened_at` es la hora de
# RECEPCION, porque no traen hora del span. Su deteccion se mide con `startsAt`.
TEMPLADAS = ("burn_rate", "anomaly", "eval_drop")


def es_prueba(d: dict[str, Any]) -> bool:
    """Un aviso de prueba (canal, botones) no es un incidente: fuera de los indicadores.

    Por la firma (`prueba-de-canal-…`, `PruebaBotones…`) o por la etiqueta
    `argus_prueba`, que es la forma explicita de marcarlo desde B-48.
    """
    etiquetas = ((d.get("attributes") or {}).get("labels") or {})
    return str(d.get("signature") or "").lower().startswith("prueba") or str(etiquetas.get("argus_prueba")) == "true"


def _duracion(d: dict[str, Any]) -> float | None:
    """`last_seen_at - notified_at`, con un incidente de UNA senal como 0.

    Con una sola senal `last_seen_at` se pone al crear el incidente y
    `notified_at` milisegundos despues: el negativo es de orden, no de reloj, y
    descartarlo dejaba fuera dos tercios de los `page`.
    """
    t = _segundos(d.get("notified_at"), d.get("last_seen_at"))
    return 0.0 if t is not None and -5 < t < 0 else t


def calcular(docs: list[dict[str, Any]], eventos: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Los indicadores de un conjunto de incidentes (los documentos del histórico) y sus eventos."""
    pruebas = sum(1 for d in docs if es_prueba(d))
    docs = [d for d in docs if not es_prueba(d)]
    notificados = [d for d in docs if d.get("notified_at")]
    pages = [d for d in notificados if d.get("severity") == "page" and not d.get("suppressed_by")]
    con_feedback_util = [d for d in docs if isinstance((d.get("feedback") or {}).get("util"), bool)]
    juzgada = [d for d in docs if (d.get("feedback") or {}).get("causa") in ("correcta", "incorrecta")]

    # Los que tienen eventos (B-49, desde el 01/11): el tiempo hasta notificar y el MTTD.
    #
    # El MTTD se parte por camino, porque cada uno se mide contra otro reloj:
    # - caliente: la hora del span (reloj de la app) contra el alert-bus. Son
    #   relojes distintos, y cuando la app va por delante el negativo es un
    #   desfase, no una deteccion: se cuenta aparte.
    # - templado: `startsAt` de vmalert contra el alert-bus, los dos en la VM de
    #   Docker. Antes de B-48 `startsAt` no se guardaba y estas alertas daban
    #   0 ms: comparaban la recepcion consigo misma.
    hasta_notificar: list[float | None] = []
    caliente: list[float | None] = []
    templado: list[float | None] = []
    desfases = 0
    for d in docs:
        evs = {e["tipo"]: e["cuando"] for e in reversed(eventos.get(d["id"], []))}  # el primero de cada tipo
        if "abierto" not in evs:
            continue
        hasta_notificar.append(_segundos(evs["abierto"], evs.get("notificado")))
        if d.get("kind") in TEMPLADAS:
            inicio = (d.get("attributes") or {}).get("starts_at")
            if inicio:
                templado.append(_segundos(inicio, evs["abierto"]))
            continue
        t = _segundos(d.get("opened_at"), evs["abierto"])
        if t is not None and t < 0:
            desfases += 1
        else:
            caliente.append(t)
    con_eventos = sum(1 for d in docs if eventos.get(d["id"]))

    return {
        "incidentes": len(docs),
        "pruebas_excluidas": pruebas,
        "volumen": {
            "senales": sum(int(d.get("count") or 1) for d in docs),
            "notificados": len(notificados),
            "sintomas_suprimidos": sum(1 for d in docs if d.get("suppressed_by")),
            "page": sum(1 for d in docs if d.get("severity") == "page"),
        },
        # Del alert-bus: todo el histórico.
        "mtta": tiempos((_segundos(d.get("notified_at"), d.get("acknowledged_at")) for d in pages), de=len(pages)),
        "hasta_diagnostico": tiempos((_segundos(d.get("notified_at"), d.get("enriched_at")) for d in notificados),
                                     de=len(notificados)),
        "duracion_visible_page": tiempos((_duracion(d) for d in pages), de=len(pages)),
        "con_causa": proporcion(sum(1 for d in docs if d.get("root_cause")), len(docs)),
        "con_evidencia": proporcion(sum(1 for d in docs if d.get("evidence")), len(docs)),
        # De los eventos de B-49.
        "con_eventos": con_eventos,
        "hasta_notificar": tiempos(hasta_notificar, de=con_eventos),
        "mttd_caliente": {**tiempos(caliente, de=con_eventos), "desfases_de_reloj": desfases},
        "mttd_templado": tiempos(templado, de=con_eventos),
        # Del feedback de B-46.
        "precision_alertas": proporcion(sum(1 for d in con_feedback_util if d["feedback"]["util"]),
                                        len(con_feedback_util)),
        "acierto_causa": proporcion(sum(1 for d in juzgada if d["feedback"]["causa"] == "correcta"), len(juzgada)),
        "feedback_cobertura": proporcion(sum(1 for d in notificados if d.get("feedback")), len(notificados)),
    }


def _t(m: dict[str, Any]) -> str:
    if not m.get("n"):
        return f"sin datos (0 de {m['de']})"
    return f"mediana {_dur(m['mediana_s'])} · P90 {_dur(m['p90_s'])} · n={m['n']} de {m['de']}"


def _dur(s: float) -> str:
    if s < 1:
        return f"{s * 1000:.0f} ms"
    if s < 120:
        return f"{s:.1f} s"
    if s < 7200:
        return f"{s / 60:.1f} min"
    return f"{s / 3600:.1f} h"


def _p(m: dict[str, Any]) -> str:
    return f"{m['pct']} % ({m['si']} de {m['de']})" if m.get("de") else "sin datos"


def informe(ind: dict[str, Any], dias: int) -> str:
    v = ind["volumen"]
    senales_por = v["senales"] / ind["incidentes"] if ind["incidentes"] else 0
    lineas = [
        f"INDICADORES de los últimos {dias} días · {ind['incidentes']} incidentes"
        + (f" ({ind['pruebas_excluidas']} de prueba, fuera)" if ind.get("pruebas_excluidas") else ""),
        "",
        "VOLUMEN",
        f"  {v['senales']:,} señales → {ind['incidentes']:,} incidentes ({senales_por:.1f} señales por incidente)",
        f"  {v['page']:,} page · {v['sintomas_suprimidos']:,} síntomas suprimidos por correlación",
        "",
        "TIEMPOS (reloj del alert-bus)",
        f"  MTTA                       {_t(ind['mtta'])}",
        f"  hasta el diagnóstico       {_t(ind['hasta_diagnostico'])}",
        f"  duración visible (page)    {_t(ind['duracion_visible_page'])}",
        "",
        f"TIEMPOS (eventos de B-49, {ind['con_eventos']} incidentes)",
        f"  hasta notificar            {_t(ind['hasta_notificar'])}",
        f"  MTTD camino caliente       {_t(ind['mttd_caliente'])}"
        + (f" · {ind['mttd_caliente']['desfases_de_reloj']} con la app adelantada, fuera"
           if ind["mttd_caliente"]["desfases_de_reloj"] else ""),
        f"  MTTD camino templado       {_t(ind['mttd_templado'])}",
        "",
        "DIAGNÓSTICO",
        f"  con causa                  {_p(ind['con_causa'])}",
        f"  con evidencia              {_p(ind['con_evidencia'])}",
        "",
        "LO QUE DICE LA PERSONA (B-46)",
        f"  precisión de las alertas   {_p(ind['precision_alertas'])}",
        f"  acierto de la causa        {_p(ind['acierto_causa'])}",
        f"  avisos con feedback        {_p(ind['feedback_cobertura'])}",
    ]
    return "\n".join(lineas)


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import urllib.request

    parser = argparse.ArgumentParser(prog="python -m alert_bus.indicadores")
    parser.add_argument("--dias", type=int, default=30)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    ind = json.load(urllib.request.urlopen(f"{args.url.rstrip('/')}/indicadores?dias={args.dias}", timeout=30))
    print(json.dumps(ind, indent=2, ensure_ascii=False) if args.json else informe(ind, args.dias))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
