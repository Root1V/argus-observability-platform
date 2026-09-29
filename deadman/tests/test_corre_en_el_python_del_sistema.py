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

import json
import subprocess
import tempfile
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


# ---------------------------------------------------------------------------
# Un vigilante que detecta algo NO es un vigilante roto.
# ---------------------------------------------------------------------------


def _config_con_un_objetivo_muerto() -> Path:
    """Config minima apuntando a un puerto que no escucha.

    Se prueba EJECUTANDO y no leyendo la fuente: lo que hay que fijar es el
    codigo de salida, y un `grep` de `return 1` pasaria aunque esa rama fuera
    inalcanzable.
    """
    destino = Path(tempfile.mkdtemp()) / "deadman.json"
    destino.write_text(
        json.dumps(
            {
                "objetivos": [{"nombre": "muerto", "url": "http://127.0.0.1:9/"}],
                # La clave se llama asi y no `umbral_fallos`: al escribir esta
                # prueba use el nombre que me sonaba y el vigilante la ignoro
                # en silencio, aplicando su valor por defecto (2). Salio con 0
                # habiendo detectado el objetivo caido, y la prueba parecia
                # decir que el contrato estaba roto cuando lo roto era ella.
                "fallos_para_avisar": 1,
                "estado": str(destino.parent / "estado.json"),
            }
        ),
        encoding="utf-8",
    )
    return destino


@pytest.mark.skipif(not PYTHON_DEL_SISTEMA.exists(), reason="sin /usr/bin/python3")
def test_salir_con_1_es_detectar_no_fallar() -> None:
    """El contrato de codigos de salida, fijado porque se leyo mal.

    `launchctl list` da el codigo de la ultima ejecucion. El vigilante sale con
    1 cuando encuentra un objetivo caido, y `pilot_status` lo interpretaba como
    "el vigilante esta roto": el criterio 7 se ponia en NO justo cuando el
    vigilante acababa de hacer su trabajo (D-101).

    Es el peor sitio posible para ese error. El dead man's switch existe para
    ser lo unico creible cuando lo demas calla, y un tablero que lo marca en
    rojo por funcionar enseña a desconfiar de el.
    """
    r = subprocess.run(
        [
            str(PYTHON_DEL_SISTEMA),
            str(VIGILANTE),
            "--config",
            str(_config_con_un_objetivo_muerto()),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 1, (
        f"con un objetivo caido el vigilante salio con {r.returncode}, no con 1.\n"
        f"{r.stdout}\n{r.stderr}"
    )


def test_pilot_status_acepta_el_1_como_sano() -> None:
    """La otra mitad del contrato, en el lector."""
    lector = (
        Path(__file__).resolve().parents[2] / "scripts" / "pilot_status.py"
    ).read_text(encoding="utf-8")
    assert 'SALIDAS_SANAS = ("0", "1")' in lector, (
        "pilot_status volvio a tratar el 1 como vigilante roto"
    )


@pytest.mark.skipif(not PYTHON_DEL_SISTEMA.exists(), reason="sin /usr/bin/python3")
def test_una_clave_desconocida_se_avisa_en_vez_de_ignorarse() -> None:
    """Una config con una errata que se aplica a medias es peor que una rota.

    Escribí `umbral_fallos` en una config de prueba, el vigilante la ignoró en
    silencio, aplicó su defecto (2), detectó el objetivo caído y salió con 0
    diciendo que todo bien. En el único proceso cuya credibilidad sostiene todo
    lo demás (D-101).
    """
    destino = Path(tempfile.mkdtemp()) / "deadman.json"
    destino.write_text(
        json.dumps(
            {
                "objetivos": [{"nombre": "m", "url": "http://127.0.0.1:9/"}],
                "umbral_fallos": 1,          # el nombre correcto es `fallos_para_avisar`
                "estado": str(destino.parent / "estado.json"),
            }
        ),
        encoding="utf-8",
    )
    r = subprocess.run(
        [str(PYTHON_DEL_SISTEMA), str(VIGILANTE), "--config", str(destino)],
        capture_output=True, text=True, timeout=120,
    )
    assert "umbral_fallos" in r.stderr, f"la clave desconocida no se avisó:\n{r.stderr}"


@pytest.mark.skipif(not PYTHON_DEL_SISTEMA.exists(), reason="sin /usr/bin/python3")
def test_una_clave_desconocida_no_impide_arrancar() -> None:
    """Negarse a correr por una errata convertiría al vigilante en otra cosa
    que se puede caer, y su premisa es correr siempre."""
    destino = _config_con_un_objetivo_muerto()
    datos = json.loads(destino.read_text(encoding="utf-8"))
    datos["algo_que_no_existe"] = True
    destino.write_text(json.dumps(datos), encoding="utf-8")

    r = subprocess.run(
        [str(PYTHON_DEL_SISTEMA), str(VIGILANTE), "--config", str(destino)],
        capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 1, "debería seguir detectando pese a la clave rara"
    assert "algo_que_no_existe" in r.stderr
