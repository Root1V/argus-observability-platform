"""F4-03: el informe de las sombras lee lo registrado y señala donde mirar."""

from __future__ import annotations

from diagnose.informe_sombra import informe


def test_cuenta_y_señala_donde_una_sombra_tiene_causa_y_las_reglas_no() -> None:
    l3 = [
        {"app": "a", "componente": "c", "reglas": {"root_cause": "r"}, "modelo": {"root_cause": "m", "origen": "x"}},
        {"app": "a", "componente": "d", "reglas": {"root_cause": None}, "modelo": {"root_cause": "solo el modelo",
                                                                                "origen": "x"}},
    ]
    l4 = [{"app": "a", "componente": "d", "encolada": "2026-10-27T10:00", "reglas": {"root_cause": None},
           "l4": {"final": "informe", "causa": "dijo L4", "confianza": "media", "tokens": 30000, "segundos": 8.0,
                  "citas_inventadas": ["[traza] x"], "pasos_reproducidos": 2}}]
    texto = informe(l3, l4)

    assert "reglas 1 · modelo 2 · solo el modelo 1" in texto
    assert "solo el modelo" in texto
    assert "solo L4 (las reglas sin causa) 1" in texto
    assert "citas inventadas 1" in texto and "pasos retomados del diario 2" in texto
    assert "30,000 tokens" in texto


def test_sin_registros_no_revienta() -> None:
    assert "0 juicios" in informe([], []) and "0 investigaciones" in informe([], [])
