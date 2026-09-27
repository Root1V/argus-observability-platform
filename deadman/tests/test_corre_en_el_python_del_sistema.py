"""El vigilante tiene que correr con el Python que haya, no con el nuestro.

`launchd` lo invoca con `/usr/bin/python3`, que en este Mac es el **3.9.6** de
Xcode. Y el fichero entero existe bajo una premisa: sin dependencias, porque si
dependiera de algo instalado compartiría modos de fallo con lo que vigila.

De ahí este test. `ruff` con `target-version = py311` "modernizó" `timezone.utc`
a `datetime.UTC`, que no existe en 3.9, y el vigilante reventó en **cada**
ejecución con un `ImportError` que solo aparecía en `/tmp/argus-deadman.err`
(D-088). Nada lo notó: `launchctl list` mostraba el trabajo cargado, y cargado no
es lo mismo que funcionando.

Lo que se comprueba aquí no es el comportamiento —eso lo cubre ejecutarlo— sino
la **compatibilidad**: que el fichero compile con el intérprete que lo va a
ejecutar de verdad.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

VIGILANTE = Path(__file__).resolve().parents[1] / "deadman.py"
PYTHON_DEL_SISTEMA = Path("/usr/bin/python3")


@pytest.mark.skipif(
    not PYTHON_DEL_SISTEMA.exists(),
    reason="sin /usr/bin/python3: no es el entorno donde corre launchd",
)
def test_compila_con_el_python_del_sistema() -> None:
    """Compilar basta: un `ImportError` de nivel de módulo salta al compilar."""
    r = subprocess.run(
        [str(PYTHON_DEL_SISTEMA), "-m", "py_compile", str(VIGILANTE)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, (
        f"el vigilante no compila con {PYTHON_DEL_SISTEMA}:\n{r.stderr}\n\n"
        "Es el intérprete que usa launchd. Si necesitas algo más nuevo, el "
        "vigilante deja de cumplir su premisa de correr sin nada instalado."
    )


@pytest.mark.skipif(
    not PYTHON_DEL_SISTEMA.exists(), reason="sin /usr/bin/python3"
)
def test_se_importa_sin_reventar() -> None:
    """Compilar no ejecuta los `import`. Esto sí, que es donde falló."""
    r = subprocess.run(
        [str(PYTHON_DEL_SISTEMA), "-c", f"import sys; sys.path.insert(0, {str(VIGILANTE.parent)!r}); import deadman"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, (
        f"el vigilante no se puede importar con {PYTHON_DEL_SISTEMA}:\n{r.stderr}"
    )


def test_no_depende_de_nada_instalado() -> None:
    """Su primera premisa, y la que lo hace fiable.

    Una dependencia externa aquí significaría que el vigilante se cae por lo
    mismo que puede caer la plataforma.
    """
    fuente = VIGILANTE.read_text(encoding="utf-8")
    importados = {
        linea.split()[1].split(".")[0]
        for linea in fuente.splitlines()
        if linea.startswith(("import ", "from ")) and "import" in linea
    }
    de_terceros = importados - {
        "argparse", "json", "smtplib", "ssl", "sys", "time", "urllib",
        "datetime", "pathlib", "hashlib", "email", "__future__", "os",
    }
    assert not de_terceros, f"el vigilante importa cosas de fuera de la stdlib: {de_terceros}"
