"""B-41: el calificador que entiende paráfrasis, y lo que no se le puede dejar hacer.

Si acierta paráfrasis o no lo dice el corpus con un modelo de verdad
(`python -m diagnose.calificador`). Lo que se prueba aqui es lo que no puede
depender de que el modelo se porte bien: un «sí» sin cita verificable es un
«no», un calificador que falla no aprueba a nadie, y el corpus esta completo.
"""

from __future__ import annotations

import pytest
from diagnose.banco import Criterio, cargar
from diagnose.calificador import (
    POR_PALABRAS,
    ConModelo,
    Corpus,
    VeredictoEtiquetado,
    cargar_corpus,
    cita_verificable,
    medir,
)
from synaptum.testing import FakeGateway, says

CASOS = cargar()
HECHO = Criterio(hecho="la causa es la latencia del destino", alguna_de=["latencia"])


def _cal(*respuestas) -> ConModelo:
    return ConModelo(lambda: FakeGateway(*respuestas), modelo="doble")


def test_un_si_con_cita_del_texto_es_un_si() -> None:
    c = _cal(says('{"afirma": true, "cita": "manager-api tarda mas de lo que el gateway espera"}'))
    assert c.afirma("Probablemente manager-api tarda mas de lo que el gateway espera.", HECHO)


def test_un_si_cuya_cita_no_esta_en_el_texto_es_un_no() -> None:
    """La misma desconfianza que con el juez: lo comprobable se comprueba con codigo."""
    c = _cal(says('{"afirma": true, "cita": "la latencia de manager-api"}'))

    assert not c.afirma("El gateway envia una peticion mal formada.", HECHO)
    assert c.citas_rechazadas == 1


def test_la_cita_se_compara_sin_tildes_comillas_ni_espacios() -> None:
    assert cita_verificable("migración  aplicada", "Una `migracion aplicada` con el proceso vivo")
    assert not cita_verificable("..", "cualquier cosa")


def test_un_calificador_que_falla_no_aprueba() -> None:
    """Si no pudo decidir, no puede decir «sí»: ese es el lado que infla."""
    c = _cal(*[says("no se") for _ in range(5)])

    assert not c.afirma("manager-api es lento", HECHO)
    assert c.fallos == 1


def test_un_texto_vacio_no_se_pregunta() -> None:
    assert not _cal().afirma("   ", HECHO)


# --- El corpus -----------------------------------------------------------------

CORPUS = cargar_corpus()


def test_hay_corpus_real_y_corpus_a_mano() -> None:
    assert {c.origen for c in CORPUS} == {"real", "a-mano"}


@pytest.mark.parametrize("veredicto", [v for c in CORPUS for v in c.veredictos], ids=lambda v: f"{v.juez}:{v.caso}")
def test_cada_veredicto_etiqueta_todos_los_criterios_de_su_caso(veredicto: VeredictoEtiquetado) -> None:
    """Un criterio sin etiqueta es uno en el que el calificador no puede equivocarse."""
    caso = next(c for c in CASOS if c.id == veredicto.caso)
    assert set(veredicto.afirma) == {c.hecho for c in (*caso.causa, *caso.sintoma, *caso.prohibido)}


def test_un_calificador_que_lo_aprueba_todo_infla_y_la_medida_lo_ve() -> None:
    class Complaciente:
        nombre = "complaciente"

        def afirma(self, texto, criterio):
            return True

    real = next(m for m in medir(Complaciente(), CORPUS, CASOS) if m.origen == "real")
    assert real.falsos_si > 0


def test_las_palabras_clave_no_inflan_el_corpus_real() -> None:
    """La linea de base. Si esto cambia, cambio el calificador o cambio el corpus."""
    real = next(m for m in medir(POR_PALABRAS, CORPUS, CASOS) if m.origen == "real")
    assert real.resultado_inflado == 0


def test_una_etiqueta_que_no_cubre_el_caso_se_rechaza() -> None:
    malo = Corpus(fuente="x", etiquetado_por="x", origen="real",
                  veredictos=[VeredictoEtiquetado(caso=CASOS[0].id, juez="x", root_cause="y", afirma={})])
    with pytest.raises(ValueError):
        medir(POR_PALABRAS, [malo], CASOS)
