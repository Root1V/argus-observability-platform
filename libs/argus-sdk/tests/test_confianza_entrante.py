"""`ARGUS_PROPAGATE` fue un mal nombre, y arreglarlo no puede romper a nadie.

Gobierna UNA cosa: si adoptar un `traceparent` que llega de fuera. No tiene
nada que ver con `argus.propagate`, el modulo que lleva el contexto entre
hilos y procesos — y los dos se llamaban igual.

Lo reporto Prosodia: `init()` registraba `"propagate": "never"` justo despues
de que adoptaran `argus.propagate.Executor`, y tardaron un rato en separarlos.
No era confusion suya (D-107).

El nombre nuevo dice lo que hace. El viejo sigue valiendo porque ya esta en
tres repositorios que no controlamos: romperlo para arreglar una palabra seria
cobrarles a ellos nuestro error.
"""

from __future__ import annotations

import warnings

import pytest
from argus._config import Config


@pytest.fixture(autouse=True)
def _limpio(monkeypatch):
    for v in ("ARGUS_PROPAGATE", "ARGUS_TRUST_INBOUND"):
        monkeypatch.delenv(v, raising=False)


def _config(monkeypatch, **env) -> tuple[Config, list[str]]:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with warnings.catch_warnings(record=True) as capturados:
        warnings.simplefilter("always")
        cfg = Config.from_env("prueba")
    return cfg, [str(a.message) for a in capturados]


def test_el_nombre_viejo_sigue_funcionando(monkeypatch) -> None:
    """Es la mitad que protege a los tres repositorios que ya lo usan."""
    cfg, avisos = _config(monkeypatch, ARGUS_PROPAGATE="trusted")
    assert cfg.propagate == "trusted"
    assert cfg.trust_inbound == "trusted"
    assert avisos == []


def test_el_nombre_nuevo_funciona(monkeypatch) -> None:
    cfg, avisos = _config(monkeypatch, ARGUS_TRUST_INBOUND="always")
    assert cfg.trust_inbound == "always"
    assert avisos == []


def test_el_nuevo_gana_y_se_avisa_del_desacuerdo(monkeypatch) -> None:
    """Un desacuerdo silencioso aqui decide si se adopta un `traceparent`
    ajeno, que es una decision de seguridad (OWASP A03). Elegir callando seria
    el peor sitio para hacerlo."""
    cfg, avisos = _config(
        monkeypatch, ARGUS_TRUST_INBOUND="always", ARGUS_PROPAGATE="never"
    )
    assert cfg.trust_inbound == "always"
    assert any("no coinciden" in a for a in avisos), avisos


def test_los_dos_de_acuerdo_no_avisan(monkeypatch) -> None:
    """Durante una migracion es normal tenerlos los dos puestos."""
    cfg, avisos = _config(
        monkeypatch, ARGUS_TRUST_INBOUND="trusted", ARGUS_PROPAGATE="trusted"
    )
    assert cfg.trust_inbound == "trusted"
    assert not [a for a in avisos if "no coinciden" in a]


def test_un_modo_invalido_avisa_en_vez_de_callar(monkeypatch) -> None:
    """Antes se coercionaba a `never` en silencio. Caer al modo mas seguro es
    correcto; no decirlo convierte una errata en una decision invisible."""
    cfg, avisos = _config(monkeypatch, ARGUS_TRUST_INBOUND="siempre")
    assert cfg.trust_inbound == "never"
    assert any("desconocido" in a for a in avisos), avisos


def test_sin_nada_el_defecto_es_el_seguro(monkeypatch) -> None:
    cfg, avisos = _config(monkeypatch)
    assert cfg.trust_inbound == "never"
    assert avisos == []


def test_las_dos_formas_de_leerlo_dan_lo_mismo(monkeypatch) -> None:
    """`propagate` lo leen el middleware ASGI y los tests; `trust_inbound` es
    el nombre que decimos hacia fuera. Si divergieran, el middleware aplicaria
    una politica distinta de la que se documenta."""
    for modo in ("never", "trusted", "always"):
        cfg, _ = _config(monkeypatch, ARGUS_TRUST_INBOUND=modo)
        assert cfg.propagate == cfg.trust_inbound == modo


def test_el_log_de_init_no_dice_propagate() -> None:
    """La clave vieja se leia pegada a `argus.propagate` y son cosas distintas.

    Se comprueba sobre la fuente porque lo que se arregla es lo que una
    persona LEE en el log, y eso no se puede afirmar desde el valor.
    """
    from pathlib import Path

    fuente = (Path(__file__).resolve().parents[1] / "src/argus/__init__.py").read_text(
        encoding="utf-8"
    )
    assert '"trust_inbound": cfg.trust_inbound' in fuente
    assert '"propagate": cfg.propagate' not in fuente
