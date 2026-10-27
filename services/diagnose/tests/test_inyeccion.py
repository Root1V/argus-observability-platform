"""F4-06: la inyeccion de prompt. Lo que se puede fijar sin modelo."""

from __future__ import annotations

from diagnose.banco import cargar
from diagnose.inyeccion import ATAQUES, TESTIGO, envenenar, evaluar
from diagnose.juez_modelo import neutralizar, pedir
from diagnose.juicio import JuezDeReglas, Veredicto

CASOS = cargar()


def test_un_mensaje_no_puede_cerrar_el_bloque_del_expediente() -> None:
    caso = next(c for c in CASOS if c.expediente.arbol.spans)
    peticion = pedir(envenenar(caso.expediente, ATAQUES["escapa"]))

    assert peticion.count("EXPEDIENTE>>>") == 1, "solo el cierre que pone Argus"
    assert peticion.count("<<<EXPEDIENTE") == 1
    assert "EXPEDIENTE> > >" in peticion


def test_neutralizar_rompe_cualquier_largo_de_delimitador() -> None:
    assert neutralizar("a>>>>b <<<c") == "a> > > >b < < <c"
    assert neutralizar("x >> y") == "x >> y"


def test_el_ataque_va_al_mensaje_del_fallo_y_al_titulo_sin_tocar_el_original() -> None:
    caso = next(c for c in CASOS if c.expediente.arbol.spans)
    envenenado = envenenar(caso.expediente, ATAQUES["ordena"])

    assert TESTIGO in envenenado.texto()
    assert TESTIGO not in caso.expediente.texto()


def test_el_juez_de_reglas_no_obedece_ni_hace_eco() -> None:
    filas = evaluar(JuezDeReglas(), CASOS)
    assert filas and not any(f.obedece or f.eco for f in filas)


def test_la_medida_detecta_a_un_juez_que_obedece() -> None:
    """Un doble que obedece todo: si la medida no lo ve, la medida no sirve."""

    class Obediente:
        nombre = "obediente"

        def juzgar(self, exp):
            texto = exp.texto()
            if "devuelve causa null" in texto:
                return Veredicto(root_cause=None)
            if TESTIGO in texto:
                return Veredicto(root_cause=f"la causa es {TESTIGO}", confidence="alta")
            return Veredicto(root_cause="la de verdad", confidence="media")

    filas = evaluar(Obediente(), CASOS)
    assert all(f.obedece for f in filas)
