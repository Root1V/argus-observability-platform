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
    """Un valor del vocabulario no se marca como inventado.

    La asercion se estrecho al anadir `argus.denied_by`: antes decia "ningun
    aviso que mencione `argus.outcome`" y empezo a fallar con `denied`, porque
    una denegacion sin atribuir avisa a proposito y su texto cita el campo.

    Fallar estuvo BIEN —es un cambio de comportamiento y lo encontro una prueba
    que no habia tocado— pero lo que esta prueba afirma es otra cosa: que un
    valor legitimo no se tache de ilegitimo. El par `denied`/`denied_by` se
    comprueba abajo, en su propio bloque.
    """
    with warnings.catch_warnings(record=True) as avisos:
        warnings.simplefilter("always")
        with step("x") as s:
            s.outcome(valor)
    tachados = [a for a in avisos if "argus.outcome" in str(a.message) and "no esta en el vocabulario" in str(a.message)]
    assert not tachados


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


# --- `argus.denied_by` ------------------------------------------------------
#
# Lo pidio Aeon y lo prometimos hace dos dias. El campo existe porque nuestra
# propia recomendacion —no pongais `argus.guardrail` en una denegacion de
# politica— dejo la denegacion sin ningun portador de QUIEN denego.
#
# Lo que se fija aqui no es el vocabulario, que es lo facil: es el PAR. Las dos
# mitades sueltas se escriben sin error y las dos rompen el `GROUP BY` que es
# la unica razon por la que el campo existe.


def _avisos_de(fn) -> list[str]:
    with warnings.catch_warnings(record=True) as capturados:
        warnings.simplefilter("always")
        fn()
    return [str(a.message) for a in capturados]


@pytest.mark.parametrize("valor", A.ARGUS_DENIED_BY_VALUES)
def test_los_tres_valores_son_los_que_pidio_aeon(valor: str) -> None:
    """`policy` una regla, `human` una persona, `budget` un limite agotado.
    Son los tres que nos dieron, y nos cubren exactamente."""
    assert set(A.ARGUS_DENIED_BY_VALUES) == {"policy", "human", "budget"}

    def emitir() -> None:
        with step("decision") as s:
            s.denied(by=valor)

    assert not [a for a in _avisos_de(emitir) if "denied_by" in a]


def test_denied_pone_las_dos_mitades() -> None:
    with step("decision") as s:
        s.denied(by="policy")
    assert s.fields[A.ARGUS_OUTCOME] == "denied"
    assert s.fields[A.ARGUS_DENIED_BY] == "policy"


def test_una_denegacion_sin_atribuir_avisa() -> None:
    """La mitad que importa.

    Es el hueco que Aeon describio: colapsar sus cinco desenlaces a `denied`
    fue correcto y perdia quien denego. Y el hueco no se ve al consultar —un
    `GROUP BY` sobre un campo ausente devuelve una fila, no una queja—, asi que
    el sitio donde tiene que doler es la emision.
    """
    def emitir() -> None:
        with step("decision") as s:
            s.outcome("denied")

    avisos = [a for a in _avisos_de(emitir) if "sin decir quien" in a]
    assert avisos, "una denegacion sin atribucion paso en silencio"
    assert "decision" in avisos[0], "el aviso no dice de que paso habla"


def test_la_atribucion_en_dos_llamadas_no_avisa() -> None:
    """El aviso mira el estado RESUELTO, no el intermedio.

    Esta prueba existe por `a16`: alli un aviso leia un campo antes de que
    estuviera resuelto y mando dos falsos positivos a un equipo. Poner el par
    en dos llamadas es correcto y no puede avisar.
    """
    def emitir() -> None:
        with step("decision") as s:
            s.outcome("denied")
            s.set(**{A.ARGUS_DENIED_BY: "human"})

    assert not [a for a in _avisos_de(emitir) if "denied" in a]


def test_la_otra_mitad_suelta_tambien_avisa() -> None:
    """`denied_by` sin `denied` cuelga la dimension de un paso que siguio
    adelante, asi que cualquier recuento de denegaciones lo cuenta."""
    def emitir() -> None:
        with step("decision") as s:
            s.set(**{A.ARGUS_DENIED_BY: "policy"})
            s.outcome("ok")

    assert [a for a in _avisos_de(emitir) if "no `denied`" in a]


def test_un_valor_inventado_avisa_aunque_se_escriba_con_set() -> None:
    """Cubre a quien emite el atributo a mano, que es como lo hacen los cuatro
    servicios Go de Aeon: leen `modelo.json` y ponen el atributo ellos."""
    def emitir() -> None:
        with step("decision") as s:
            s.outcome("denied")
            s.set(**{A.ARGUS_DENIED_BY: "cedar"})

    assert [a for a in _avisos_de(emitir) if "no esta en el vocabulario" in a]


def test_una_denegacion_no_enciende_el_camino_caliente() -> None:
    """La diferencia con `error()`, y el motivo por el que el campo existe.

    Recomendamos a Aeon no marcar `argus.guardrail` en una denegacion porque
    ese campo pagina en ~2 s, y una denegacion es el sistema haciendo lo
    correcto. Si `denied()` marcase `argus.hot` estariamos reintroduciendo por
    la puerta de al lado la fatiga de alertas que ese consejo evitaba.
    """
    with step("decision") as s:
        s.denied(by="policy")
    assert A.ARGUS_HOT not in s.fields
    assert A.ARGUS_GUARDRAIL not in s.fields


def test_lo_empaquetado_trae_los_valores_de_denied_by() -> None:
    """Aeon valida contra `modelo.json` desde Go. Si el JSON y el `.py` se
    desincronizan, ellos comprueban contra una lista y nosotros contra otra."""
    assert tuple(_miembros("argus.denied_by")) == A.ARGUS_DENIED_BY_VALUES
