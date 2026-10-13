"""La version que la documentacion manda instalar tiene que existir.

Lo encontro Veritium al integrarse (`VRT-ARG-001 §3`): `docs/piloto.md` mandaba
instalar `1.0.0a17` cuando las librerias iban por `a21`. Cuatro versiones de
diferencia, y el documento es **el primer sitio** donde mira un equipo nuevo.

El defecto no es el numero, es que un numero copiado a prosa se queda viejo sin
que nada se queje. Es la forma que llevo coleccionando: una verdad en dos
sitios, y el que nadie toca es el que se consulta. Lo mismo que `A-43` con
Prometheus, lo mismo que los tres compromisos que vivian solo en el registro de
decisiones.

Asi que esto no comprueba que el numero sea bonito: comprueba que coincide con
la version que de verdad se publica.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[3]
PILOTO = RAIZ / "docs" / "piloto.md"
PYPROJECT = RAIZ / "libs" / "argus-sdk" / "pyproject.toml"


def _version_publicada() -> str:
    with PYPROJECT.open("rb") as f:
        return tomllib.load(f)["project"]["version"]


@pytest.mark.skipif(not PILOTO.is_file(), reason="sin docs/ en una instalacion del paquete")
def test_la_guia_del_piloto_manda_instalar_la_version_actual() -> None:
    texto = PILOTO.read_text(encoding="utf-8")
    citadas = set(re.findall(r"argus-obs-sdk[^\"'\s]*==(\d+\.\d+\.\d+[a-z]*\d*)", texto))
    assert citadas, "la guia del piloto ya no dice que version instalar"

    actual = _version_publicada()
    viejas = citadas - {actual}
    assert not viejas, (
        f"`docs/piloto.md` manda instalar {sorted(viejas)} y las librerias van por "
        f"{actual}.\n\nEs el primer documento que lee un equipo nuevo: un pin viejo "
        f"ahi no da ningun error, solo les instala otra cosa."
    )
