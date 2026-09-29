"""Un id de contenedor no es una maquina.

El criterio 6 del piloto cuenta hosts distintos. Antes de D-104 todo el
almacen llevaba el mismo `host.name` —el id del contenedor del agente— asi que
la cuenta habria sido 1 por un motivo equivocado; y un despliegue viejo que
siga sellando ids inflaria la cuenta y daria el criterio por cumplido con una
sola maquina mal identificada.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pilot_status import clasificar_hosts


@pytest.mark.parametrize(
    "nombre",
    ["2a3bc2822dfb", "eb3f7dd3361d", "000000000000", "abcdef123456"],
)
def test_los_ids_de_contenedor_no_cuentan(nombre: str) -> None:
    reales, sospechosos = clasificar_hosts([nombre])
    assert reales == []
    assert sospechosos == [nombre]


@pytest.mark.parametrize(
    "nombre",
    [
        "MacBook-Pro-de-Emeric--Caren",
        "imac-estudio",
        "servidor-1",
        # Doce caracteres pero no hexadecimales: es un nombre.
        "maquina-uno",
        # Hexadecimales pero no doce: tampoco es un id corto de Docker.
        "abcdef",
        "abcdef1234567",
    ],
)
def test_los_nombres_de_maquina_si_cuentan(nombre: str) -> None:
    reales, sospechosos = clasificar_hosts([nombre])
    assert reales == [nombre]
    assert sospechosos == []


def test_los_vacios_se_descartan() -> None:
    """El gateway emite sus propias metricas sin `host.name`. Contarlo como
    host daria un segundo host que no es una maquina."""
    reales, sospechosos = clasificar_hosts(["", "imac-estudio", ""])
    assert reales == ["imac-estudio"]
    assert sospechosos == []


def test_una_mezcla_se_separa_bien() -> None:
    reales, sospechosos = clasificar_hosts(
        ["2a3bc2822dfb", "macbook-emeric", "", "imac-estudio", "eb3f7dd3361d"]
    )
    assert reales == ["macbook-emeric", "imac-estudio"]
    assert len(sospechosos) == 2
