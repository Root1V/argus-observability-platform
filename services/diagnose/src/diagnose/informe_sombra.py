"""F4-03: lo que dijeron las sombras, al lado de lo que dijeron las reglas, sobre incidentes REALES.

La sombra registra para poder comparar, y registrar no sirve si nadie lo lee.
Esto lee los dos diarios —`sombra.jsonl` (juez con modelo, L3) y
`sombra-l4.jsonl` (investigador, L4)— y dice cuantas veces tuvo cada uno
causa donde las reglas no, cuanto costaron y en que acabaron.

No dice quien acierta: sin la causa real no se puede saber, y para eso esta el
banco. Lo que si dice es DONDE mirar: los incidentes en los que una sombra
propone causa y las reglas no son los candidatos a caso de banco.

`python -m diagnose.informe_sombra [--l3 ruta] [--l4 ruta]`
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _leer(ruta: Path) -> list[dict[str, Any]]:
    if not ruta.exists():
        return []
    filas = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        try:
            filas.append(json.loads(linea))
        except ValueError:
            continue
    return filas


def _corto(texto: str | None, n: int = 110) -> str:
    return (texto or "—").replace("\n", " ")[:n]


def informe(l3: list[dict[str, Any]], l4: list[dict[str, Any]]) -> str:
    lineas: list[str] = []

    lineas.append(f"JUEZ CON MODELO EN SOMBRA (L3) · {len(l3)} juicios")
    # En modo «completa», si el modelo falla queda el veredicto de las reglas
    # con `origen = reglas`. Eso no es un juicio del modelo: se cuenta aparte.
    sin_modelo = [f for f in l3 if (f.get("modelo") or {}).get("origen") == "reglas"]
    l3 = [f for f in l3 if f not in sin_modelo]
    if sin_modelo:
        lineas.append(f"  {len(sin_modelo)} sin modelo disponible (quedo el veredicto de las reglas): no cuentan")
    if l3:
        reglas = sum(1 for f in l3 if (f.get("reglas") or {}).get("root_cause"))
        modelo = sum(1 for f in l3 if (f.get("modelo") or {}).get("root_cause"))
        solo_modelo = [f for f in l3 if (f.get("modelo") or {}).get("root_cause")
                       and not (f.get("reglas") or {}).get("root_cause")]
        lineas.append(f"  con causa: reglas {reglas} · modelo {modelo} · solo el modelo {len(solo_modelo)}")
        origenes = sorted({(f.get("modelo") or {}).get("origen", "?") for f in l3})
        lineas.append(f"  modelos: {', '.join(origenes)}")
        for f in solo_modelo[-5:]:
            lineas.append(f"    {f.get('cuando', '')[:16]} {f.get('app')}/{f.get('componente')}: "
                          f"{_corto((f.get('modelo') or {}).get('root_cause'))}")

    lineas.append("")
    lineas.append(f"INVESTIGADOR EN SOMBRA (L4) · {len(l4)} investigaciones")
    if l4:
        res = [f.get("l4") or {} for f in l4]
        finales: dict[str, int] = {}
        for r in res:
            finales[r.get("final", "?")] = finales.get(r.get("final", "?"), 0) + 1
        con_causa = sum(1 for r in res if r.get("causa"))
        solo_l4 = [f for f in l4 if (f.get("l4") or {}).get("causa") and not (f.get("reglas") or {}).get("root_cause")]
        tokens = [int(r.get("tokens") or 0) for r in res]
        segundos = [float(r.get("segundos") or 0) for r in res]
        inventadas = sum(len(r.get("citas_inventadas") or []) for r in res)
        reproducidos = sum(int(r.get("pasos_reproducidos") or 0) for r in res)
        lineas.append(f"  final: {', '.join(f'{k} {v}' for k, v in sorted(finales.items()))}")
        lineas.append(f"  con causa {con_causa} · solo L4 (las reglas sin causa) {len(solo_l4)} · "
                      f"citas inventadas {inventadas} · pasos retomados del diario {reproducidos}")
        lineas.append(f"  coste: {sum(tokens):,} tokens ({sum(tokens) // max(len(tokens), 1):,} por investigacion) · "
                      f"{sum(segundos) / 60:.1f} min ({sum(segundos) / max(len(segundos), 1):.0f} s por investigacion)")
        for f in l4[-8:]:
            r = f.get("l4") or {}
            lineas.append(f"    {f.get('encolada', '')[:16]} {f.get('app')}/{f.get('componente')} [{r.get('final')}]")
            lineas.append(f"        reglas: {_corto((f.get('reglas') or {}).get('root_cause'))}")
            lineas.append(f"        L4 [{r.get('confianza')}]: {_corto(r.get('causa'))}")
    return "\n".join(lineas)


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m diagnose.informe_sombra")
    parser.add_argument("--l3", default="/var/lib/argus/sombra.jsonl")
    parser.add_argument("--l4", default="/var/lib/argus/sombra-l4.jsonl")
    args = parser.parse_args(argv)
    print(informe(_leer(Path(args.l3)), _leer(Path(args.l4))))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
