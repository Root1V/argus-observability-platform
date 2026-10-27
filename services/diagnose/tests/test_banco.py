"""F3-08: el banco de incidentes, y las dos invariantes que el juez no puede romper.

No se fija la puntuacion exacta: un banco que crece con casos nuevos y no
vistos TIENE que poder bajar la cifra, y una prueba que lo impidiera seria un
incentivo para no añadir casos dificiles. Lo que se fija es lo que no se puede
negociar:

- **ningun `falla` con confianza `alta`**, que es mandar a alguien a mirar
  donde no es con aire de certeza;
- y que el banco sea lo que dice ser: real, con causa establecida fuera de L3,
  y sin las respuestas de L3 grapadas al incidente.
"""

from __future__ import annotations

import pytest
from diagnose.banco import CAMPOS_DE_L3, Caso, Criterio, calificar, cargar, evaluar, resumen
from diagnose.expediente import Expediente
from diagnose.juicio import JuezDeReglas, Veredicto

CASOS = cargar()


def test_el_banco_tiene_lo_que_pide_f3_08() -> None:
    """Al menos los 5 casos con causa conocida que pedia F3-08. Sin techo: el banco crece con lo que falla."""
    assert len(CASOS) >= 5


@pytest.mark.parametrize("caso", CASOS, ids=lambda c: c.id)
def test_cada_caso_dice_de_donde_sale_su_verdad(caso: Caso) -> None:
    assert caso.causa_real and "POR ESCRIBIR" not in caso.causa_real
    assert caso.fuente and "POR ESCRIBIR" not in caso.fuente
    assert caso.causa, "un caso sin criterio de causa no puede acertar nunca"


@pytest.mark.parametrize("caso", CASOS, ids=lambda c: c.id)
def test_ningun_caso_lleva_las_respuestas_de_l3_grapadas(caso: Caso) -> None:
    """Dos incidentes del banco los diagnostico L3 antes de capturarlos.

    Si su `root_cause` siguiera en el incidente, el caso seria un examen con
    las soluciones dentro.
    """
    for campo in CAMPOS_DE_L3:
        assert not caso.incidente.get(campo), f"{caso.id} trae `{campo}` de L3"


@pytest.mark.parametrize("caso", CASOS, ids=lambda c: c.id)
def test_la_historia_de_cada_caso_no_ve_el_futuro(caso: Caso) -> None:
    """La recurrencia se cuenta hasta el incidente, no hasta el dia de la captura."""
    h = caso.expediente.historia
    if h.primer_dia is not None:
        assert h.primer_dia.isoformat()[:10] <= str(caso.incidente["opened_at"])[:10]


def test_el_juez_de_reglas_nunca_falla_con_confianza_alta() -> None:
    """La invariante. Lo peor que puede hacer un diagnostico."""
    r = resumen(evaluar(JuezDeReglas(), CASOS))

    assert r["no_vistos"]["falla_con_alta"] == 0
    assert r["diseno"]["falla_con_alta"] == 0


def test_hay_casos_no_vistos() -> None:
    """Sin casos no vistos, el banco solo mide lo bien que el juez se sabe sus deberes."""
    assert any(not c.usado_para_disenar for c in CASOS)


# --- El calificador ------------------------------------------------------------


def _caso(**campos) -> Caso:
    base = dict(
        id="x", titulo="t", causa_real="c", fuente="f", nivel="medida",
        capturado_en="2026-10-25T00:00:00+00:00",
        incidente={"signature": "TypeError: GenAISpan.set() got an unexpected keyword", "title": "t"},
        expediente=Expediente(incidente_id="i", huella="h", app="a", componente="c",
                              firma="s", tipo_senal="error", severidad="page"),
        sintoma=[Criterio(hecho="TypeError", alguna_de=["TypeError"])],
        causa=[Criterio(hecho="nombra GenAISpan.set", alguna_de=["GenAISpan.set"])],
    )
    return Caso(**{**base, **campos})


def test_citar_la_firma_del_incidente_no_puntua() -> None:
    """La primera pasada dio dos aciertos que eran eco de la firma (D-134)."""
    eco = Veredicto(
        root_cause="El aviso dice `TypeError: GenAISpan.set() got an unexpected keyword`. La causa es TypeError.",
        confidence="media",
    )
    assert calificar(_caso(), eco).resultado == "parcial"


def test_afirmar_lo_prohibido_es_fallar_aunque_tambien_diga_la_causa() -> None:
    caso = _caso(prohibido=[Criterio(hecho="culpa a la red", alguna_de=["transporte"])])
    v = Veredicto(root_cause="GenAISpan.set fallo por el transporte", confidence="alta")

    c = calificar(caso, v)
    assert c.resultado == "falla"
    assert c.prohibidos == ["culpa a la red"]


def test_no_proponer_causa_es_abstenerse_y_no_fallar() -> None:
    assert calificar(_caso(), Veredicto(root_cause=None)).resultado == "se_abstiene"


def test_las_tildes_no_cambian_la_nota() -> None:
    """Las reglas escriben sin tildes y un modelo no."""
    caso = _caso(causa=[Criterio(hecho="migracion", alguna_de=["migracion"])])
    v = Veredicto(root_cause="Una migración dejó un proceso viejo", confidence="media")

    assert calificar(caso, v).resultado == "acierta"


@pytest.mark.parametrize("caso", CASOS, ids=lambda c: c.id)
def test_ningun_caso_lleva_el_volcado_de_una_fila(caso: Caso) -> None:
    """El banco vive en un repositorio con remoto, y los datos son de otros equipos.

    La primera captura metio el `DETAIL:` de Postgres de Veritium: UUID de sus
    filas y un codigo de empleado, veinticuatro veces.
    """
    texto = caso.model_dump_json()
    for marca in ("Failing row contains", "[parameters:", "[SQL:"):
        assert marca not in texto, f"{caso.id} lleva `{marca}`"
    # Ningun UUID: ni de filas (Veritium) ni de procesos (`service.instance.id`).
    import re

    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", texto), f"{caso.id} lleva un UUID"


def test_media_causa_es_parcial_y_no_falla() -> None:
    """«host o ingesta» sin «todos a la vez» salia `falla`, por debajo de no decir nada (D-139)."""
    caso = _caso(
        causa=[Criterio(hecho="todos a la vez", alguna_de=["a la vez"]), Criterio(hecho="host", alguna_de=["host"])],
        sintoma=[Criterio(hecho="el canario calla", alguna_de=["canario"])],
    )
    assert calificar(caso, Veredicto(root_cause="host o ingesta", confidence="media")).resultado == "parcial"


def test_repetir_casi_toda_la_firma_con_otro_prefijo_tambien_es_eco() -> None:
    """Las reglas «acertaban» aeon citando la firma sin su `TypeError: ` (D-139)."""
    caso = _caso(
        incidente={"signature": "TypeError: GenAISpan.set() got an unexpected keyword argument 'argus.run.id'", "title": "t"},
        causa=[Criterio(hecho="por nombre", alguna_de=["keyword"])],
    )
    v = Veredicto(root_cause="`TypeError` en aeon-worker: GenAISpan.set() got an unexpected keyword argument 'argus.run.id'")
    assert calificar(caso, v).resultado != "acierta"


def test_citar_una_parte_de_la_firma_si_cuenta() -> None:
    """Sacar la excepcion original de dentro del PendingRollbackError ES el diagnostico."""
    firma = ("PendingRollbackError: This Session's transaction has been rolled back due to a previous exception "
             "during flush. To begin a new transaction with this Session, first issue Session.rollback(). "
             "Original exception was: null value in column \"tenant\" violates not-null constraint")
    caso = _caso(incidente={"signature": firma, "title": "t"},
                 causa=[Criterio(hecho="tenant", alguna_de=["tenant"])])
    v = Veredicto(root_cause="La causa es la excepcion original: null value in column \"tenant\" violates not-null constraint")
    assert calificar(caso, v).resultado == "acierta"
