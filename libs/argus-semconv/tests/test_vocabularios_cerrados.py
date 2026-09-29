"""Un vocabulario declarado cerrado tiene que estarlo tambien en el codigo.

`argus.outcome` llevaba desde el principio declarado `enum` en el modelo, y
`Step.outcome()` aceptaba cualquier cadena. El artefacto afirmaba una cosa y el
codigo hacia otra — exactamente el patron que Aeon nos describio el mismo dia
que preguntaba si este campo tenia vocabulario (D-096).

El coste de no comprobarlo no se ve: un valor inventado se escribe igual de
bien que uno bueno, y el campo sigue ahi. Lo que desaparece es la capacidad de
agregarlo, que es la unica razon por la que un campo se declara cerrado.
"""

from __future__ import annotations

import warnings

import pytest
from argus_semconv import attributes as A
from argus_semconv import modelo
from argus_semconv.guardrails import AgentRun, Budget
from argus_semconv.steps import step


def _miembros(nombre: str) -> list[str]:
    for grupo in modelo()["groups"]:
        for attr in grupo["attributes"]:
            if attr["id"] == nombre:
                return attr.get("members", [])
    raise AssertionError(f"{nombre} no esta en el modelo")


# --- El modelo viaja con el paquete ----------------------------------------


def test_el_modelo_se_publica_con_la_libreria() -> None:
    """Lo pidio Aeon: cuatro de sus seis servicios son Go y hoy copian nuestros
    nombres a mano. Un fichero en la rama principal describe lo que habra; lo
    que hay que poder comprobar es la version instalada."""
    d = modelo()
    assert d["groups"], "el modelo empaquetado esta vacio"
    assert _miembros("argus.outcome"), "los enum llegan sin sus valores"


def test_lo_empaquetado_coincide_con_las_constantes() -> None:
    """Si el JSON y el `.py` salen del mismo generador pero uno no se
    regenero, un consumidor en Go validaria contra una lista y nosotros
    emitiriamos otra."""
    assert tuple(_miembros("argus.outcome")) == A.ARGUS_OUTCOME_VALUES
    assert tuple(_miembros("argus.guardrail")) == A.ARGUS_GUARDRAIL_VALUES


# --- `argus.outcome` --------------------------------------------------------


@pytest.mark.parametrize("valor", A.ARGUS_OUTCOME_VALUES)
def test_los_desenlaces_del_vocabulario_no_avisan(valor: str) -> None:
    with warnings.catch_warnings(record=True) as avisos:
        warnings.simplefilter("always")
        with step("x") as s:
            s.outcome(valor)
    assert not [a for a in avisos if "argus.outcome" in str(a.message)]


@pytest.mark.parametrize("valor", ["denied_by_policy", "approval_granted", "result", "OK"])
def test_un_desenlace_inventado_avisa(valor: str) -> None:
    """Los tres primeros son los que Aeon propuso; el cuarto es el despiste
    clasico de mayusculas, que crea un cubo aparte silenciosamente."""
    with warnings.catch_warnings(record=True) as avisos:
        warnings.simplefilter("always")
        with step("x") as s:
            s.outcome(valor)
    assert [a for a in avisos if "argus.outcome" in str(a.message)], f"{valor} paso sin aviso"


def test_un_desenlace_inventado_se_escribe_igual() -> None:
    """Avisar no es descartar: perder el dato seria peor que tenerlo mal
    clasificado, y el contrato es no romper nunca la aplicacion."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with step("x") as s:
            s.outcome("inventado")
            assert s.fields[A.ARGUS_OUTCOME] == "inventado"


def test_denied_existe_porque_una_denegacion_no_es_un_error() -> None:
    """Lo trajo Aeon. Sin `denied`, un rechazo de politica se contaba como
    `error` y ensuciaba cualquier tasa de error con decisiones correctas."""
    assert "denied" in A.ARGUS_OUTCOME_VALUES


# --- `argus.guardrail` ------------------------------------------------------


def test_los_tipos_que_emitimos_estan_en_el_vocabulario() -> None:
    """El modelo daba `cost-per-run` como ejemplo y el codigo emite
    `cost-budget`. Un nombre que no existe, en el sitio donde un equipo va a
    buscarlo: Aeon acerto por leer `guardrails.py` y no el modelo."""
    corredor = AgentRun(name="a", budget=Budget(max_tool_calls=1, max_tokens=1, max_cost_usd=0.01))
    for _ in range(5):
        corredor.record_tool_call("t", "misma-firma")
    corredor.record_usage(tokens=99, cost_usd=1.0)

    emitidos = set(corredor.breaches)
    assert emitidos, "la prueba no provoco ningun incumplimiento"
    fuera = emitidos - set(A.ARGUS_GUARDRAIL_VALUES)
    assert not fuera, f"estos tipos se emiten y no estan en el modelo: {sorted(fuera)}"


def test_un_guardarrail_marca_el_camino_caliente() -> None:
    """Se fija porque decide el significado del campo: el Collector agente
    enruta a `traces/hot` cualquier span con `argus.guardrail`, asi que ponerlo
    equivale a pedir una notificacion en segundos. Por eso una espera de
    aprobacion —operacion normal de L5— NO puede ir aqui."""
    corredor = AgentRun(name="a", budget=Budget(max_tool_calls=1))
    corredor.record_tool_call("t", "firma")
    corredor.record_tool_call("t", "firma")
    attrs = corredor.attributes()
    assert A.ARGUS_GUARDRAIL in attrs
    assert attrs[A.ARGUS_HOT] is True


# --- Un paso que no acaba ---------------------------------------------------


def test_no_poner_desenlace_lo_pone_en_ok() -> None:
    """La trampa que hay que dejar fijada, no solo documentada.

    Aeon planteo dejar `argus.outcome` sin poner mientras un paso espera a una
    persona, razonando que la ausencia se leeria como "todavia no". No hay
    ausencia: `_finalize` hace `setdefault("ok")` y cada espera se habria
    registrado como un exito (D-097).
    """
    with step("x") as s:
        pass
    assert s.fields[A.ARGUS_OUTCOME] == "ok"


def test_suspended_existe_para_un_paso_que_espera() -> None:
    """Es el argumento de Prometheus sobre `unknown` en P-31, aqui: "todavia no
    ha acabado" es un hecho, y tiene que distinguirse de "acabo bien"."""
    assert "suspended" in A.ARGUS_OUTCOME_VALUES
    with warnings.catch_warnings(record=True) as avisos:
        warnings.simplefilter("always")
        with step("espera") as s:
            s.outcome("suspended")
    assert not [a for a in avisos if "argus.outcome" in str(a.message)]
    assert s.fields[A.ARGUS_OUTCOME] == "suspended"


def test_un_paso_suspendido_no_es_un_guardarrail() -> None:
    """Esperar a una persona es operacion normal de L5. `argus.guardrail`
    enciende el camino caliente, asi que marcarlo asi paginaria en cada
    aprobacion — la fatiga de alertas construida desde dentro."""
    assert "approval-required" not in A.ARGUS_GUARDRAIL_VALUES
    with step("espera") as s:
        s.outcome("suspended")
    assert A.ARGUS_GUARDRAIL not in s.fields
    assert A.ARGUS_HOT not in s.fields
