"""El modelo YAML es la fuente de verdad; lo generado no puede desviarse.

Con cinco lenguajes, el fallo que rompe las consultas agregadas no es que un
servicio no emita, sino que dos emitan lo mismo con nombres distintos. Este
test es lo que lo hace imposible.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]


def test_generated_constants_match_the_model() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "gen_semconv.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=120,
    )
    assert result.returncode == 0, (
        "Las constantes generadas no coinciden con libs/semconv-model/argus.yaml.\n"
        "Ejecuta: python tools/gen_semconv.py\n" + result.stdout + result.stderr
    )


def test_python_and_go_expose_the_same_attribute_values() -> None:
    """Python y Go deben emitir literalmente las mismas cadenas."""
    import re

    from argus_semconv import attributes as A

    go_source = (ROOT / "libs" / "argus-semconv" / "generated" / "go" / "attributes.go").read_text()
    go_values = set(re.findall(r'=\s*"([^"]+)"', go_source))

    python_values = {
        value
        for name, value in vars(A).items()
        if not name.startswith("_") and isinstance(value, str) and not name.endswith("VERSION") and not name.endswith("_UNIT")
    }

    missing = python_values - go_values
    assert not missing, f"Atributos en Python que no existen en Go: {sorted(missing)}"
