"""`deployment.environment.name` tiene vocabulario cerrado, y es del estandar.

No lo cerramos nosotros: OTel lo fija en production/staging/test/development.
Lo que faltaba era comprobarlo, que es la diferencia entre tener un vocabulario
y tenerlo de verdad.

Derivo a cuatro valores entre cuatro emisores —`mac-dev`, `local`, `local` y
`bare-metal`— y ninguno era del estandar. El motivo no fue descuido de nadie:
el campo se estaba usando para responder "¿en que MAQUINA corre?", que es
`host.name`. Y el primer valor inventado lo escribimos nosotros en el plan,
mezclando maquina y nivel en la misma columna (D-106).
"""

from __future__ import annotations

import warnings

import pytest
from argus._config import Config
from argus_semconv import attributes as A


def _avisos(monkeypatch, valor: str) -> list[str]:
    monkeypatch.setenv("ARGUS_ENVIRONMENT", valor)
    monkeypatch.delenv("DEPLOYMENT_ENVIRONMENT", raising=False)
    with warnings.catch_warnings(record=True) as capturados:
        warnings.simplefilter("always")
        Config.from_env("prueba")
    return [str(a.message) for a in capturados if "environment" in str(a.message)]


def test_el_vocabulario_es_el_del_estandar() -> None:
    """Si alguien anade un valor propio, esta prueba lo dice.

    Inventar un nombre para algo que el estandar ya nombra es D-081; ampliar
    su vocabulario cerrado es la misma idea un paso mas alla.
    """
    assert A.DEPLOYMENT_ENVIRONMENT_NAME_VALUES == (
        "production", "staging", "test", "development",
    )


@pytest.mark.parametrize("valor", ["production", "staging", "test", "development"])
def test_los_del_estandar_no_avisan(monkeypatch, valor: str) -> None:
    assert _avisos(monkeypatch, valor) == []


@pytest.mark.parametrize("valor", ["mac-dev", "local", "bare-metal", "imac", "server-1", "ci"])
def test_los_cuatro_que_derivaron_avisan(monkeypatch, valor: str) -> None:
    """Los seis son valores REALES: cuatro que estaban emitiendose y dos que
    nuestro propio plan proponia."""
    assert _avisos(monkeypatch, valor), f"{valor} paso sin aviso"


@pytest.mark.parametrize("valor", ["local", "mac-dev", "bare-metal"])
def test_al_que_confunde_maquina_con_nivel_se_le_dice(monkeypatch, valor: str) -> None:
    """El aviso generico no basta para el error que de verdad se comete.

    Quien escribe `local` o `bare-metal` no esta eligiendo mal entre cuatro
    niveles: esta respondiendo a otra pregunta. Decirle solo "no esta en la
    lista" le haria elegir uno al azar.
    """
    mensaje = _avisos(monkeypatch, valor)[0]
    assert "host.name" in mensaje


def test_sin_entorno_SI_se_avisa(monkeypatch) -> None:
    """Esta prueba decia lo contrario, y su razonamiento habilito el fallo.

    Afirmaba: «es opcional, avisar de que falta seria ruido en cualquier
    script». Suena razonable y es falso, porque el SDK **no dejaba el campo
    vacio**: lo rellenaba con `local`, que no esta en el vocabulario del
    estandar.

    Asi que el silencio que esta prueba protegia era el silencio con el que se
    sellaba un valor invalido — y el aviso de D-106 no podia verlo porque
    miraba la variable de entorno y no el valor resuelto (D-109).

    Ahora el entorno ausente queda ausente y avisa. Ausente es honesto;
    adivinado era el fallo.
    """
    assert _avisos(monkeypatch, "") != []


def test_un_entorno_invalido_no_impide_arrancar(monkeypatch) -> None:
    """Un entorno mal escrito es un dato peor, no un motivo para no arrancar."""
    monkeypatch.setenv("ARGUS_ENVIRONMENT", "bare-metal")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cfg = Config.from_env("prueba")
    assert cfg.environment == "bare-metal"
