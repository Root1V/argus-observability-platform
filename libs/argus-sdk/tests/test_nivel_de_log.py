"""El nivel del logger raíz, que se nos quedó en WARNING sin que nadie lo viera.

`configure_logging` solo bajaba el nivel `if root.level == logging.NOTSET`, con
la buena intención de respetar la configuración ajena. Pero **el root de Python
arranca en WARNING, nunca en NOTSET**: esa rama no se ejecutaba jamás y toda
aplicación que nos adoptaba perdía el 100% de sus `logger.info()` en silencio
(D-084).

Lo encontró un equipo consumidor con 21 minutos de pipeline y cero registros
exportados. Nosotros no lo vimos porque nuestros propios servicios fijan el
nivel por su cuenta.

Cada caso va en un proceso limpio: el nivel del root es estado global y un test
que lo toque envenena a los demás.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

TIMEOUT = 60

PREAMBULO = """
import logging, os
os.environ["ARGUS_SERVICE"] = "prueba"
os.environ["ARGUS_DISABLED"] = "1"
"""


def _nivel(cuerpo: str, entorno: dict[str, str] | None = None) -> str:
    import os

    env = {**os.environ, **(entorno or {})}
    r = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(PREAMBULO + cuerpo)],
        capture_output=True, text=True, timeout=TIMEOUT, env=env,
    )
    assert "NIVEL=" in r.stdout, r.stdout + r.stderr
    return r.stdout.split("NIVEL=")[1].split()[0]


COLA = """
import argus
argus.init()
print("NIVEL=" + logging.getLevelName(logging.getLogger().level))
"""


def test_por_defecto_baja_a_info() -> None:
    """Es el caso que estaba roto, y es el 99% de las adopciones.

    Quien instala una plataforma de observabilidad espera que sus `info()`
    lleguen. Perderlos sin un aviso es el peor resultado posible.
    """
    assert _nivel(COLA) == "INFO"


def test_respeta_un_nivel_elegido_a_proposito() -> None:
    """`basicConfig()` deja WARNING **y** un handler.

    Esa huella es lo que distingue un WARNING decidido del de fábrica. Sin
    ella no hay forma de saberlo, y pisar la decisión de la aplicación sería
    el error contrario.
    """
    assert _nivel("logging.basicConfig(level=logging.WARNING)\n" + COLA) == "WARNING"


def test_respeta_un_nivel_mas_bajo() -> None:
    assert _nivel("logging.basicConfig(level=logging.DEBUG)\n" + COLA) == "DEBUG"


def test_el_argumento_explicito_manda() -> None:
    cuerpo = """
import argus
argus.init(logging_level="ERROR")
print("NIVEL=" + logging.getLevelName(logging.getLogger().level))
"""
    assert _nivel(cuerpo) == "ERROR"


def test_la_variable_de_entorno_manda_sobre_el_defecto() -> None:
    assert _nivel(COLA, {"ARGUS_LOG_LEVEL": "DEBUG"}) == "DEBUG"


def test_una_variable_invalida_no_tumba_la_aplicacion() -> None:
    """Regla 1 del contrato. Un nivel mal escrito degrada al defecto y avisa."""
    assert _nivel(COLA, {"ARGUS_LOG_LEVEL": "VERBOSISIMO"}) == "INFO"


def test_un_info_de_la_aplicacion_llega_al_handler() -> None:
    """El nivel es el medio; esto es el fin. Sin esto lo demás da igual."""
    import os

    cuerpo = """
import argus
argus.init()
logging.getLogger("app").info("marca-de-prueba-info")
print("NIVEL=" + logging.getLevelName(logging.getLogger().level))
"""
    r = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(PREAMBULO + cuerpo)],
        capture_output=True, text=True, timeout=TIMEOUT,
        env={**os.environ, "ARGUS_SERVICE": "prueba", "ARGUS_DISABLED": "1"},
    )
    assert "marca-de-prueba-info" in r.stdout, (
        "el INFO de la aplicacion no salio:\n" + r.stdout + r.stderr
    )
