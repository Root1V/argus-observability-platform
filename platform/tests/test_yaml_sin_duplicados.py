"""Ningún YAML de la plataforma puede tener una clave repetida.

`yaml.safe_load` acepta claves duplicadas y se queda con la última, sin error.
Un segundo bloque `volumes:` o `spanevent:` desactiva al anterior en silencio,
el fichero sigue siendo válido, y el servicio arranca sin una queja haciendo
menos de lo que el texto dice que hace.

Nació acotada a `gateway.yaml` el mismo día que me pasó allí. **Una hora
después me volvió a pasar en `compose.yaml`**, añadiendo un `volumes:` al
alert-bus que ya tenía uno: el volumen de persistencia se perdió y lo vi
imprimiendo el YAML parseado, no el texto (D-103).

Dos veces en una sesión es la señal de que el guardarraíl estaba mal acotado,
no de que haya que tener más cuidado. Así que ahora cubre todos.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

PLATAFORMA = Path(__file__).resolve().parents[1]

# `compose.agent.yaml` y los perfiles incluidos: se descubren solos para que un
# fichero nuevo entre sin que nadie se acuerde de añadirlo aquí.
FICHEROS = sorted(
    p for p in PLATAFORMA.rglob("*.yaml")
    if "tests" not in p.parts and p.name != "apps.yaml.example"
)


class _SinDuplicados(yaml.SafeLoader):
    """Loader que se niega a quedarse con la última."""


def _mapa(loader: _SinDuplicados, nodo: yaml.MappingNode, deep: bool = False) -> dict:
    # `<<: *ancla` es una clave de FUSION, no una clave normal. `SafeLoader`
    # la resuelve dentro de `construct_mapping`, y al escribir un constructor
    # propio hay que llamarlo antes o el `<<` llega aqui como clave literal
    # cuyo valor es un mapa — inhashable — y revienta.
    #
    # No es un detalle: `compose.yaml` usa `<<: *restart` en cada servicio, asi
    # que sin esto la prueba se SALTABA justo el fichero donde me habia pasado
    # el fallo. Un guardarrail que se salta el caso que lo motivo es un fichero
    # de texto (D-088, otra vez).
    loader.flatten_mapping(nodo)
    vistas: set = set()
    for clave_nodo, _ in nodo.value:
        clave = loader.construct_object(clave_nodo, deep=deep)
        if clave in vistas:
            raise AssertionError(f"clave duplicada: {clave!r} (línea {clave_nodo.start_mark.line + 1})")
        vistas.add(clave)
    return yaml.SafeLoader.construct_mapping(loader, nodo, deep)


_SinDuplicados.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapa)


def test_hay_ficheros_que_revisar() -> None:
    """Si el descubrimiento dejara de encontrar nada, todo lo de abajo pasaría
    vacío y nadie se enteraría."""
    assert len(FICHEROS) >= 4, [p.name for p in FICHEROS]


@pytest.mark.parametrize("ruta", FICHEROS, ids=lambda p: str(p.relative_to(PLATAFORMA)))
def test_sin_claves_duplicadas(ruta: Path) -> None:
    texto = ruta.read_text(encoding="utf-8")
    # Los `${VAR:?mensaje}` de compose no son YAML válido en todos los casos,
    # pero sí lo son como cadenas; no hace falta expandir nada para detectar
    # una clave repetida.
    try:
        yaml.load(texto, Loader=_SinDuplicados)
    except AssertionError:
        raise
    except yaml.YAMLError as exc:
        raise AssertionError(
            f"{ruta.name} no se pudo cargar: {type(exc).__name__}: {exc}\n"
            f"Saltarselo dejaria el fichero sin cubrir sin que nadie lo note."
        ) from exc
