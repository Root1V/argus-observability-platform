"""Los nombres que mandamos fuera tienen que existir en el modelo.

Este test nace del fallo más caro de todos (D-081): teníamos una fuente de
verdad con generación automática de constantes **precisamente** para que dos
lenguajes no emitieran el mismo atributo con nombres distintos… y luego
escribimos los nombres **a mano en un documento** para otro equipo, sin
contrastarlos con ella.

Ese equipo los implementó fielmente. Durante días emitieron bajo nombres que
nuestro propio paquete no conocía, y lo descubrimos consultando su ventana con
*nuestros* nombres y viendo cero — a punto de escribirles que su arreglo no
había llegado.

**El generador no sirve de nada si el contrato que mandas fuera se escribe en
otro sitio.** Esto cierra ese hueco por el único lado que quedaba abierto.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from argus_semconv import attributes as A

RAIZ = Path(__file__).resolve().parents[3]
DOCS = RAIZ / "docs"

# `argus.algo.otra_cosa` dentro de comillas invertidas.
PATRON = re.compile(r"`(argus\.[a-z0-9_.]+)`")

# Lo que parece un atributo y no lo es. Cada excepción lleva su motivo: una
# lista de permitidos sin razones se convierte en el sitio donde se esconden
# los errores de verdad.
PERMITIDOS: dict[str, str] = {
    "argus.yaml": "el fichero del modelo, no un atributo",
    "argus.usecase": "aparece en PLAN.md como EJEMPLO de nombre mal escrito",
    # Los tres nombres viejos del grupo de inferencia. Siguen citados en el
    # registro de decisiones porque la historia de D-081 no se puede contar sin
    # ellos; lo que no pueden es volver a aparecer en material que mandemos.
    "argus.backend.id": "nombre histórico, citado en decisions.md (D-081)",
    "argus.ttft_ms": "nombre histórico, citado en decisions.md (D-081)",
    "argus.first_token_ms": "nombre histórico, citado en decisions.md (D-081)",
    "argus.backend.circuit_state": "nombre histórico, citado en decisions.md (D-081)",
    "argus.cost_usd": "nombre histórico; hoy es argus.inference.cost_usd (D-081)",
}


def _del_modelo() -> set[str]:
    return {v for v in vars(A).values() if isinstance(v, str) and v.startswith("argus.")}


def _citados() -> dict[str, set[str]]:
    encontrados: dict[str, set[str]] = {}
    for fichero in sorted(DOCS.rglob("*.md")):
        for m in PATRON.finditer(fichero.read_text(encoding="utf-8")):
            encontrados.setdefault(m.group(1).rstrip("."), set()).add(
                str(fichero.relative_to(RAIZ))
            )
    return encontrados


@pytest.mark.skipif(not DOCS.is_dir(), reason="sin docs/ en una instalación del paquete")
def test_ningun_documento_inventa_un_atributo() -> None:
    conocidos = _del_modelo()
    huerfanos = {
        nombre: ficheros
        for nombre, ficheros in _citados().items()
        if nombre not in conocidos and nombre not in PERMITIDOS
    }
    assert not huerfanos, (
        "estos `argus.*` salen en documentación y NO existen en el modelo:\n"
        + "\n".join(f"  {n}  ->  {sorted(f)}" for n, f in sorted(huerfanos.items()))
        + "\n\nO se añaden a libs/semconv-model/argus.yaml, o se corrige el documento. "
        "Mandar fuera un nombre que el paquete no conoce es exactamente D-081."
    )


@pytest.mark.skipif(not DOCS.is_dir(), reason="sin docs/")
def test_la_lista_de_permitidos_no_acumula_basura() -> None:
    """Una excepción que ya no hace falta es una excepción que tapa la siguiente."""
    citados = set(_citados())
    conocidos = _del_modelo()
    sobran = {
        n: motivo
        for n, motivo in PERMITIDOS.items()
        if n not in citados or n in conocidos
    }
    assert not sobran, f"permitidos que ya no aplican, quítalos: {sobran}"


def test_los_tres_nombres_de_inferencia_son_los_acordados() -> None:
    """Lo que Prometheus emite en producción desde el 17/09 (A-25)."""
    assert A.ARGUS_INFERENCE_BACKEND_ID == "argus.inference.backend_id"
    assert A.ARGUS_INFERENCE_TTFT_MS == "argus.inference.ttft_ms"
    assert A.ARGUS_INFERENCE_FIRST_TOKEN_MS == "argus.inference.first_token_ms"
