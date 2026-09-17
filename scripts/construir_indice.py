#!/usr/bin/env python3
"""Genera un indice de paquetes PEP 503 a partir de las ruedas construidas.

Por que un indice ESTATICO y no devpi, que es lo que decia el plan:

  - El plano central tiene que ser portatil (D-003). Un indice estatico son
    ficheros en un volumen: se mueve con `cp -r` y no tiene estado que migrar.
  - `devpi` aporta subida por `twine`, replica de PyPI y varios indices. Nada de
    eso hace falta para tres paquetes y dos consumidores, y cada pieza que no
    hace falta es una que hay que actualizar.
  - Si algun dia hace falta, `devpi` entra DETRAS de la misma URL y nadie de
    fuera se entera. La decision no se cierra, se aplaza.

Lo que si resuelve, que es lo que los dos equipos pidieron:

  - `pip install argus-obs-sdk` resuelve versiones solo, con rangos normales.
  - Cada enlace lleva `#sha256=`, asi que pip **verifica la integridad** sin que
    nadie compare sumas a mano. Eso era la objecion de fondo de Prometheus:
    aceptar un binario por SHA es una decision de cadena de suministro; que lo
    verifique la herramienta no lo es.
  - Se regenera entero desde las ruedas, asi que no hay estado que se corrompa.

Uso:
    python scripts/construir_indice.py             # desde dist/
    python scripts/construir_indice.py --salida ruta
"""

from __future__ import annotations

import argparse
import hashlib
import html
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def normalizar(nombre: str) -> str:
    """Nombre normalizado segun PEP 503: minusculas y `-` como separador."""
    return re.sub(r"[-_.]+", "-", nombre).lower()


def nombre_del_artefacto(fichero: Path) -> str:
    """El nombre del proyecto que hay dentro de un .whl o un .tar.gz."""
    if fichero.suffix == ".whl":
        return fichero.name.split("-")[0]
    # sdist: nombre-version.tar.gz, y la version empieza por un digito
    tronco = fichero.name.removesuffix(".tar.gz")
    partes = tronco.rsplit("-", 1)
    return partes[0] if len(partes) == 2 else tronco


def sha256(fichero: Path) -> str:
    h = hashlib.sha256()
    with fichero.open("rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origen", type=Path, default=RAIZ / "dist")
    parser.add_argument("--salida", type=Path, default=RAIZ / "platform" / "indice")
    args = parser.parse_args()

    artefactos = sorted(
        [*args.origen.glob("*.whl"), *args.origen.glob("*.tar.gz")]
    )
    if not artefactos:
        print(f"No hay artefactos en {args.origen}. Ejecuta `make wheels` primero.",
              file=sys.stderr)
        return 1

    simple = args.salida / "simple"
    simple.mkdir(parents=True, exist_ok=True)

    por_proyecto: dict[str, list[Path]] = {}
    for a in artefactos:
        por_proyecto.setdefault(normalizar(nombre_del_artefacto(a)), []).append(a)

    for proyecto, ficheros in sorted(por_proyecto.items()):
        carpeta = simple / proyecto
        carpeta.mkdir(exist_ok=True)
        enlaces = []
        for f in sorted(ficheros):
            destino = carpeta / f.name
            destino.write_bytes(f.read_bytes())
            # El fragmento `#sha256=` es lo que hace que pip verifique. Sin el,
            # el indice sirve ficheros que nadie comprueba.
            enlaces.append(
                f'    <a href="{html.escape(f.name)}#sha256={sha256(f)}">'
                f"{html.escape(f.name)}</a><br/>"
            )
        (carpeta / "index.html").write_text(
            "<!DOCTYPE html>\n<html><head>"
            '<meta name="pypi:repository-version" content="1.0">'
            f"<title>{html.escape(proyecto)}</title></head><body>\n"
            + "\n".join(enlaces)
            + "\n</body></html>\n",
            encoding="utf-8",
        )
        print(f"  {proyecto:24} {len(ficheros)} artefacto(s)")

    (simple / "index.html").write_text(
        "<!DOCTYPE html>\n<html><head>"
        '<meta name="pypi:repository-version" content="1.0">'
        "<title>Argus</title></head><body>\n"
        + "\n".join(
            f'    <a href="{p}/">{p}</a><br/>' for p in sorted(por_proyecto)
        )
        + "\n</body></html>\n",
        encoding="utf-8",
    )

    print(f"\n  índice en {args.salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
