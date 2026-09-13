"""Guardarrailes de agentes: detectar y, opcionalmente, PARAR.

Un agente puede entrar en bucle, llamar a la herramienta equivocada o alucinar,
y devolver un 200 con latencia normal. El APM tradicional no ve nada de eso.

Estos guardarrailes viven en el SDK y no en el `alert-bus` por una razon que no
es de comodidad: **en el SDK se puede parar el bucle**. Detectarlo desde el
backend llega tarde, porque para cuando la telemetria ha viajado el agente ya
lleva veinte llamadas mas. Un agente descontrolado cuesta dinero cada segundo.

La division es deliberada:

- **Aqui**: presupuestos ABSOLUTOS (maximo de llamadas, coste, repeticiones
  identicas). No necesitan historico y actuan en el acto.
- **En vmalert**: los ESTADISTICOS (`tool_calls_per_run > P99`, coste sobre la
  mediana historica). Necesitan historico y no pueden vivir en proceso.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Any

from . import attributes as A


class GuardrailBreach(RuntimeError):
    """Se alcanzo un presupuesto del agente.

    Se lanza solo si el presupuesto esta en modo `stop`. En modo `mark` el
    guardarrail se anota en el span y la ejecucion continua.
    """

    def __init__(self, guardrail: str, detalle: str) -> None:
        super().__init__(f"{guardrail}: {detalle}")
        self.guardrail = guardrail
        self.detalle = detalle


@dataclass(frozen=True)
class Budget:
    """Limites de una ejecucion de agente.

    Los valores por defecto son generosos a proposito: un guardarrail que salta
    en ejecuciones legitimas se desactiva, y un guardarrail desactivado no
    protege de nada. Se aprietan cuando se conoce el comportamiento real.
    """

    max_tool_calls: int = 40
    max_cost_usd: float = 0.0            # 0 = sin limite
    max_repeated_calls: int = 3          # misma herramienta, mismos argumentos
    max_tokens: int = 0                  # 0 = sin limite

    # `mark` anota y deja seguir; `stop` lanza GuardrailBreach.
    #
    # Por defecto `mark`, porque parar una ejecucion es una decision del
    # producto, no de la libreria de observabilidad. Un guardarrail que corta
    # produccion sin que nadie lo haya pedido es peor que el bucle.
    mode: str = "mark"

    def stops(self) -> bool:
        return self.mode == "stop"


@dataclass
class AgentRun:
    """Estado de una ejecucion de agente en curso."""

    name: str
    budget: Budget
    tool_calls: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    breaches: list[str] = field(default_factory=list)
    # (herramienta, huella de argumentos) -> veces. Solo llamadas con `args`.
    _calls: dict[tuple[str, str], int] = field(default_factory=dict)
    # Herramientas llamadas sin `args`: cuentan para el volumen, no para bucles.
    _tools_sin_args: set[str] = field(default_factory=set)

    def record_tool_call(self, tool_name: str, args_signature: str | None = None) -> str | None:
        """Registra una llamada. Devuelve el guardarrail incumplido, si lo hay.

        `args_signature` a `None` significa "no se sabe con que argumentos se
        llamo". En ese caso NO se detectan bucles, solo se cuenta el volumen.

        Tratar las llamadas sin argumentos como identicas entre si produciria
        falsos positivos: un agente que llama diez veces a la misma herramienta
        con argumentos distintos que no nos ha dicho esta trabajando, no
        atascado. Y un falso positivo en un guardarrail que puede PARAR
        produccion es mucho peor que un bucle no detectado.
        """
        self.tool_calls += 1

        if self.budget.max_tool_calls and self.tool_calls > self.budget.max_tool_calls:
            return self._breach("tool-call-budget", f"{self.tool_calls} llamadas")

        if args_signature is None:
            self._tools_sin_args.add(tool_name)
            return None

        clave = (tool_name, args_signature)
        self._calls[clave] = self._calls.get(clave, 0) + 1
        repeticiones = self._calls[clave]

        # La misma herramienta con los MISMOS argumentos varias veces es la
        # firma inequivoca de un bucle. Muchas llamadas pueden ser trabajo
        # legitimo; repetir identico no lo es nunca.
        if self.budget.max_repeated_calls and repeticiones >= self.budget.max_repeated_calls:
            return self._breach(
                "tool-call-loop",
                f"«{tool_name}» con los mismos argumentos {repeticiones} veces",
            )
        return None

    def record_usage(self, *, tokens: int = 0, cost_usd: float = 0.0) -> str | None:
        self.tokens += tokens
        self.cost_usd += cost_usd

        if self.budget.max_tokens and self.tokens > self.budget.max_tokens:
            return self._breach("token-budget", f"{self.tokens} tokens")
        if self.budget.max_cost_usd and self.cost_usd > self.budget.max_cost_usd:
            return self._breach("cost-budget", f"{self.cost_usd:.4f} USD")
        return None

    def _breach(self, guardrail: str, detalle: str) -> str:
        if guardrail not in self.breaches:
            self.breaches.append(guardrail)
        self._ultimo_detalle = detalle
        return guardrail

    @property
    def detalle(self) -> str:
        return getattr(self, "_ultimo_detalle", "")

    def attributes(self) -> dict[str, Any]:
        """Lo que se adjunta al span `invoke_agent` al cerrarse."""
        attrs: dict[str, Any] = {
            "argus.agent.tool_calls": self.tool_calls,
            "argus.agent.distinct_tools": len({t for t, _ in self._calls} | self._tools_sin_args),
        }
        if self.tokens:
            attrs["argus.agent.tokens"] = self.tokens
        if self.cost_usd:
            attrs[A.ARGUS_COST_USD] = self.cost_usd
        if self.breaches:
            # `argus.guardrail` es lo que el Collector agente filtra hacia el
            # camino caliente: un guardarrail roto llega en segundos.
            attrs[A.ARGUS_GUARDRAIL] = ",".join(self.breaches)
            attrs[A.ARGUS_HOT] = True
        return attrs


# La ejecucion en curso. Un contextvar y no un global: los agentes corren en
# corrutinas concurrentes y un global los mezclaria.
_current: contextvars.ContextVar[AgentRun | None] = contextvars.ContextVar("argus_agent_run", default=None)


def current_run() -> AgentRun | None:
    return _current.get()


def set_run(run: AgentRun | None) -> contextvars.Token:
    return _current.set(run)


def reset_run(token: contextvars.Token) -> None:
    _current.reset(token)


def signature(args: Any) -> str:
    """Huella estable de los argumentos de una herramienta.

    Solo para comparar llamadas entre si, asi que basta con que sea
    determinista. NO se emite: podria llevar datos sensibles.
    """
    import hashlib
    import json

    try:
        texto = json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        texto = repr(args)
    return hashlib.sha256(texto.encode()).hexdigest()[:12]
