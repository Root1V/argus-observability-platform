"""Todo tracer nuestro declara QUE VERSION del codigo emitio el span.

Es lo unico que lo dice: ningun atributo de recurso lleva la version de
nuestras librerias, y `service.version` es la de la aplicacion.

`argus-semconv` lo hace bien desde `1.0.0a4`, y lo hace **porque se arreglo**:
antes registraba la version del modelo, que vale `1.0.0` desde hace mucho, asi
que un prelanzamiento y una estable declaraban lo mismo (D-082).

Ese arreglo no se aplico al SDK. Sus tres `get_tracer("argus-sdk")` no pasaban
version ninguna — peor que el estado que D-082 corrigio, porque alli habia un
numero equivocado y aqui no habia numero.

**Como se noto**: el dueño de la plataforma pidio la lista de que version corre
cada equipo, y de los cuatro integrados solo se podia contestar de uno —y de
rebote, porque usan `semconv` ademas del SDK—. De los otros tres la respuesta
era lo que nos habian dicho, no lo que mediamos (D-119).

Esta prueba recorre el codigo en vez de comprobar un tracer concreto: el
defecto fue que uno de los sitios se quedo atras, asi que lo que hay que fijar
es que NO HAYA ninguno sin version.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from argus import _scope

FUENTE = Path(__file__).resolve().parents[1] / "src" / "argus"


def _tracers_sin_version(fichero: Path) -> list[str]:
    """Llamadas REALES a `get_tracer` sin version, con el arbol y no con texto.

    La primera version de esto era una expresion regular sobre el fuente, y su
    unico hallazgo fue **el docstring de `_scope.py`**, que cita la linea mala
    para explicarla. Un `grep` no distingue codigo de prosa, y en este
    repositorio la prosa habla mucho del codigo: es el mismo fallo que ya nos
    costo una comprobacion que casaba dentro de una cadena.

    `ast` solo ve llamadas.
    """
    arbol = ast.parse(fichero.read_text(encoding="utf-8"))
    culpables = []
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Call):
            continue
        fn = nodo.func
        nombre = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if nombre not in ("get_tracer", "get_meter"):
            continue
        # Un segundo posicional o un `version=` cuentan como version declarada.
        tiene = len(nodo.args) >= 2 or any(k.arg == "version" for k in nodo.keywords)
        if not tiene:
            culpables.append(f"{fichero.name}:{nodo.lineno}")
    return culpables


def test_ningun_get_tracer_se_queda_sin_version() -> None:
    culpables = [c for f in sorted(FUENTE.rglob("*.py")) for c in _tracers_sin_version(f)]
    assert not culpables, (
        "estos tracers no declaran version, asi que sus spans no dicen que "
        "codigo los emitio:\n  " + "\n  ".join(culpables)
    )


def test_la_prueba_mira_CODIGO_y_no_prosa() -> None:
    """Fija el arreglo de la propia prueba.

    `_scope.py` cita `get_tracer("argus-sdk")` en su docstring a proposito, para
    explicar que era lo que estaba mal. Si esta comprobacion volviera a ser un
    `grep`, ese docstring la haria fallar — y lo mas probable es que alguien
    borrase la cita en vez de la expresion regular.
    """
    scope_py = FUENTE / "_scope.py"
    assert 'get_tracer("argus-sdk")' in scope_py.read_text(encoding="utf-8"), (
        "el docstring de _scope.py ya no cita la linea que explica. Si se quito "
        "para que pasara una prueba, se quito lo que no debia"
    )
    assert _tracers_sin_version(scope_py) == []


def test_la_version_es_la_del_PAQUETE_y_no_la_del_modelo() -> None:
    """La lección exacta de D-082, que es por lo que esto importa.

    La version del modelo de convenciones vale `1.0.0` y no se mueve; la del
    paquete es la que distingue un prelanzamiento de otro. Si el scope llevara
    la del modelo, dos versiones distintas declararian lo mismo.
    """
    from importlib.metadata import version

    assert _scope.version_paquete() == version("argus-obs-sdk")
    assert _scope.version_paquete() != "1.0.0", (
        "el scope declara la version del modelo, no la del paquete"
    )


def test_no_levanta_si_el_paquete_no_esta_instalado(monkeypatch) -> None:
    """Un `init()` que revienta por no saber una version seria lo contrario de
    lo que el contrato del SDK promete."""
    import importlib.metadata

    def explota(_: str) -> str:
        raise importlib.metadata.PackageNotFoundError("argus-obs-sdk")

    monkeypatch.setattr(importlib.metadata, "version", explota)
    assert _scope.version_paquete() == "0.0.0.dev0"


def test_el_scope_se_distingue_del_de_semconv(spans) -> None:
    """Un span puede venir del middleware del SDK o de un decorador de
    semconv, y en el almacen eso se tiene que poder separar sin adivinar."""
    from argus_semconv import _scope as scope_semconv

    assert _scope.atributos() != scope_semconv.atributos()
    assert _scope.atributos()["argus.sdk.package"] == "argus-obs-sdk"


@pytest.mark.parametrize("modulo", ["asgi", "propagate"])
def test_el_tracer_de_cada_modulo_declara_la_version(modulo: str) -> None:
    """Lo anterior comprueba el codigo; esto comprueba el objeto.

    Un `get_tracer` con version podria estar pasandola mal —vacia, o la del
    modelo— y el recorrido del fuente no lo veria.
    """
    import importlib
    from importlib.metadata import version

    fuente = (FUENTE / f"{modulo}.py").read_text(encoding="utf-8")
    assert "_scope.version_paquete()" in fuente, f"{modulo} no usa la version del paquete"
    importlib.import_module(f"argus.{modulo}")
    assert version("argus-obs-sdk") == _scope.version_paquete()
