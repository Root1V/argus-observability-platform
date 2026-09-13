"""Guardarrailes de agentes.

Un agente en bucle devuelve 200 con latencia normal: el APM tradicional no ve
nada. Estos detectores viven en el SDK y no en el backend porque aqui se puede
PARAR el bucle; detectarlo desde el almacen llega tarde, cuando el agente ya
lleva veinte llamadas mas y el coste ya se gasto.
"""

from __future__ import annotations

import pytest
from argus_semconv import Budget, GuardrailBreach, agent, genai, tool
from argus_semconv import attributes as A

# --- Bucles ------------------------------------------------------------------


def test_repetir_identico_es_un_bucle(spans) -> None:
    """Muchas llamadas pueden ser trabajo legitimo; repetir IDENTICO no lo es
    nunca. Es la firma inequivoca de un agente atascado."""
    with agent("bot", budget=Budget(max_repeated_calls=3)):
        for _ in range(3):
            with tool("buscar", args={"q": "factura"}):
                pass

    padre = next(s for s in spans.get_finished_spans() if s.name == "invoke_agent")
    assert "tool-call-loop" in padre.attributes[A.ARGUS_GUARDRAIL]
    assert padre.attributes[A.ARGUS_HOT] is True


def test_la_misma_herramienta_con_argumentos_distintos_no_es_bucle(spans) -> None:
    """Un agente que pagina resultados llama diez veces a la misma herramienta
    y esta trabajando, no atascado."""
    with agent("bot", budget=Budget(max_repeated_calls=3)):
        for i in range(10):
            with tool("buscar", args={"pagina": i}):
                pass

    padre = next(s for s in spans.get_finished_spans() if s.name == "invoke_agent")
    assert A.ARGUS_GUARDRAIL not in padre.attributes
    assert padre.attributes["argus.agent.tool_calls"] == 10


def test_sin_argumentos_no_se_detectan_bucles_pero_si_el_volumen(spans) -> None:
    """Quien no pasa `args` renuncia a la deteccion de bucles, no al conteo."""
    with agent("bot", budget=Budget(max_tool_calls=5, max_repeated_calls=2)):
        for _ in range(4):
            with tool("buscar"):
                pass

    padre = next(s for s in spans.get_finished_spans() if s.name == "invoke_agent")
    assert A.ARGUS_GUARDRAIL not in padre.attributes


def test_superar_el_maximo_de_llamadas(spans) -> None:
    with agent("bot", budget=Budget(max_tool_calls=3)):
        for i in range(5):
            with tool("herramienta", args={"i": i}):
                pass

    padre = next(s for s in spans.get_finished_spans() if s.name == "invoke_agent")
    assert "tool-call-budget" in padre.attributes[A.ARGUS_GUARDRAIL]


# --- Parar vs marcar ---------------------------------------------------------


def test_por_defecto_marca_pero_no_para(spans) -> None:
    """Parar produccion es una decision del PRODUCTO, no de la libreria de
    observabilidad. Un guardarrail que corta sin que nadie lo pida es peor que
    el bucle."""
    with agent("bot", budget=Budget(max_repeated_calls=2)):
        for _ in range(6):
            with tool("buscar", args={"q": "x"}):
                pass   # no lanza

    padre = next(s for s in spans.get_finished_spans() if s.name == "invoke_agent")
    assert padre.attributes["argus.agent.tool_calls"] == 6
    assert A.ARGUS_GUARDRAIL in padre.attributes


def test_en_modo_stop_lanza_antes_de_ejecutar(spans) -> None:
    """El objetivo es NO hacer la llamada número cuarenta y uno, no registrarla."""
    ejecutadas = 0

    with pytest.raises(GuardrailBreach) as exc, agent("bot", budget=Budget(max_repeated_calls=3, mode="stop")):
        for _ in range(10):
            with tool("buscar", args={"q": "x"}):
                ejecutadas += 1

    assert ejecutadas == 2, "la herramienta se ejecuto pese al guardarrail"
    assert exc.value.guardrail == "tool-call-loop"


def test_las_estadisticas_se_adjuntan_aunque_pare(spans) -> None:
    """Justo cuando la ejecucion muere por un guardarrail es cuando mas falta
    hacen para entender que paso."""
    with pytest.raises(GuardrailBreach), agent("bot", budget=Budget(max_tool_calls=2, mode="stop")):
        for i in range(10):
            with tool("t", args={"i": i}):
                pass

    padre = next(s for s in spans.get_finished_spans() if s.name == "invoke_agent")
    assert padre.attributes["argus.agent.tool_calls"] == 3
    assert A.ARGUS_GUARDRAIL in padre.attributes


# --- Coste y tokens ----------------------------------------------------------


def test_el_coste_se_acumula_por_EJECUCION(spans) -> None:
    """"Esta peticion gasto X" no sirve: el coste se fuga por ejecucion, que es
    donde lo agentico multiplica las llamadas."""
    with agent("bot", budget=Budget(max_cost_usd=0.10)):
        for _ in range(3):
            with genai("chat", provider="openai", request_model="m") as g:
                g.backend(cost_usd=0.02)

    padre = next(s for s in spans.get_finished_spans() if s.name == "invoke_agent")
    assert padre.attributes[A.ARGUS_COST_USD] == pytest.approx(0.06)
    assert A.ARGUS_GUARDRAIL not in padre.attributes


def test_superar_el_presupuesto_de_coste(spans) -> None:
    with agent("bot", budget=Budget(max_cost_usd=0.05)):
        for _ in range(4):
            with genai("chat", provider="openai", request_model="m") as g:
                g.backend(cost_usd=0.02)

    padre = next(s for s in spans.get_finished_spans() if s.name == "invoke_agent")
    assert "cost-budget" in padre.attributes[A.ARGUS_GUARDRAIL]


def test_superar_el_presupuesto_de_tokens(spans) -> None:
    with agent("bot", budget=Budget(max_tokens=1000)):
        for _ in range(3):
            with genai("chat", provider="ollama", request_model="m") as g:
                g.usage(input_tokens=400, output_tokens=100)

    padre = next(s for s in spans.get_finished_spans() if s.name == "invoke_agent")
    assert "token-budget" in padre.attributes[A.ARGUS_GUARDRAIL]


# --- Aislamiento -------------------------------------------------------------


def test_una_herramienta_fuera_de_un_agente_no_revienta(spans) -> None:
    """Las herramientas se usan tambien sin agente."""
    with tool("suelta", args={"x": 1}):
        pass


def test_los_agentes_anidados_no_se_mezclan(spans) -> None:
    """El interior consume su propio presupuesto y al salir se restaura el de
    fuera. Con un global en vez de un contextvar, esto se mezclaria."""
    with agent("externo", budget=Budget(max_tool_calls=100)):
        with tool("a", args={"i": 1}):
            pass
        with agent("interno", budget=Budget(max_tool_calls=2)):
            for i in range(3):
                with tool("b", args={"i": i}):
                    pass
        with tool("c", args={"i": 2}):
            pass

    por_nombre = {
        s.attributes.get(A.GEN_AI_AGENT_NAME): s
        for s in spans.get_finished_spans() if s.name == "invoke_agent"
    }
    assert por_nombre["externo"].attributes["argus.agent.tool_calls"] == 2
    assert por_nombre["interno"].attributes["argus.agent.tool_calls"] == 3
    assert A.ARGUS_GUARDRAIL in por_nombre["interno"].attributes
    assert A.ARGUS_GUARDRAIL not in por_nombre["externo"].attributes


async def test_dos_agentes_concurrentes_no_se_mezclan(spans) -> None:
    """Los agentes corren en corrutinas concurrentes. Un global los mezclaria y
    el conteo de uno contaminaria el presupuesto del otro."""
    import asyncio

    async def ejecutar(nombre: str, llamadas: int) -> None:
        with agent(nombre, budget=Budget(max_tool_calls=100)):
            for i in range(llamadas):
                with tool("t", args={"i": i}):
                    await asyncio.sleep(0)

    await asyncio.gather(ejecutar("uno", 3), ejecutar("dos", 7))

    por_nombre = {
        s.attributes.get(A.GEN_AI_AGENT_NAME): s
        for s in spans.get_finished_spans() if s.name == "invoke_agent"
    }
    assert por_nombre["uno"].attributes["argus.agent.tool_calls"] == 3
    assert por_nombre["dos"].attributes["argus.agent.tool_calls"] == 7


def test_sin_presupuesto_sigue_contando(spans) -> None:
    """El conteo es util aunque no haya limite: alimenta el detector
    estadistico de vmalert, que si necesita historico."""
    with agent("bot"):
        for i in range(5):
            with tool("t", args={"i": i}):
                pass

    padre = next(s for s in spans.get_finished_spans() if s.name == "invoke_agent")
    assert padre.attributes["argus.agent.tool_calls"] == 5
    assert padre.attributes["argus.agent.distinct_tools"] == 1
