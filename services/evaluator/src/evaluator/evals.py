"""Las evaluaciones. Cada una mira UN span GenAI y dice pasa, falla o «no lo puedo saber».

**Tres resultados y no dos, y el tercero es el que importa.** Un span de chat
que no trae `gen_ai.response.finish_reasons` no ha pasado la evaluacion de
rechazo: no se ha podido hacer. Contarlo como «pasa» es lo que convierte una
laguna de instrumentacion en una tasa de rechazos del 0 % que nadie mira —el
mismo error que D-091, donde «no puedo parsear» se leia como «no hay
evaluaciones»—. `desconocido` se guarda y se cuenta aparte, y es lo que dice
que apps no se pueden evaluar todavia y por que.

**Un vocabulario cerrado.** `finish_reasons` lo rellena cada proveedor con sus
palabras. Las conocidas se traducen; una que no esta en ninguna lista da
`desconocido` con la palabra en el motivo, en vez de adivinar. Asi una palabra
nueva aparece en el informe en lugar de esconderse dentro de «pasa».

**Baratas y caras.** Las de aqui son deterministas y no cuestan nada, asi que
se aplican a TODOS los spans: un pico de rechazos en una app con 200
llamadas al dia no se ve en un 5 %. La muestra (`muestreo.py`) existe para las
que cuestan un modelo —groundedness, consistencia—, que llegan con F5-02.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol

# Operaciones que producen una respuesta de modelo. `embeddings` no termina con
# un motivo ni tiene salida que leer, y `execute_tool` es la herramienta, no el
# modelo.
GENERATIVAS = frozenset({"chat", "text_completion", "generate_content"})

# Fin normal de una generacion: las palabras de OpenAI, Anthropic y Gemini.
FIN_NORMAL = frozenset({"stop", "end_turn", "stop_sequence", "tool_calls", "tool_use", "function_call"})
# El DESENLACE del gateway de Prometheus, que hasta PRM-282 iba dentro de
# `finish_reasons`. No dice por que paro el modelo: un `length` sale como
# `complete` (verificado el 31/10 con max_tokens=600). Para rechazo y truncado
# es `desconocido`; para abandono, `complete` es que el cliente si estaba.
DESENLACE_GATEWAY = frozenset({"complete", "upstream_error"})
TRUNCADO = frozenset({"length", "max_tokens"})
RECHAZO = frozenset({"content_filter", "refusal", "safety", "recitation", "prohibited_content", "blocklist",
                     "spii"})
# El cliente se fue antes de la respuesta (el gateway de Prometheus lo anota
# asi). No es un fallo del modelo, pero si es una respuesta que nadie recibio.
ABANDONO = frozenset({"client_disconnected", "cancelled"})
LLAMA_HERRAMIENTA = frozenset({"tool_calls", "tool_use", "function_call"})
CONOCIDOS = FIN_NORMAL | TRUNCADO | RECHAZO | ABANDONO | DESENLACE_GATEWAY | {"error"}


class Resultado(StrEnum):
    PASA = "pasa"
    FALLA = "falla"
    DESCONOCIDO = "desconocido"


@dataclass(frozen=True)
class Span:
    """Lo que una evaluacion puede leer de un span. Sin contenido: no esta en el almacen (D-008)."""

    trace_id: str
    span_id: str
    fin: str
    app: str
    componente: str
    operacion: str
    # El que se sirvio (`response.model`) y el que se pidio (`request.model`).
    # Centinela solo emite el pedido; el informe usa el servido si existe.
    modelo: str
    modelo_pedido: str = ""
    finish_reasons: str = ""
    tokens_salida: str = ""
    max_tokens: str = ""
    # `argus.inference.outcome`: como termino la peticion en el gateway (A-53).
    desenlace: str = ""
    error: bool = False
    duracion_ns: int = 0


@dataclass(frozen=True)
class Evaluacion:
    eval: str
    version: str
    resultado: Resultado
    motivo: str = ""


class Eval(Protocol):
    nombre: str
    version: str
    coste: Literal["barato", "caro"]

    def evaluar(self, span: Span) -> Evaluacion | None:
        """`None` es «no aplica»: no se guarda ni se cuenta."""
        ...


def motivos(span: Span) -> list[str] | None:
    """`finish_reasons` en minusculas, o `None` si el span no lo trae.

    En el almacen es el array JSON serializado (`["stop"]`); se acepta tambien
    un valor suelto por si alguna app lo emite como cadena.
    """
    crudo = span.finish_reasons.strip()
    if not crudo:
        return None
    try:
        valor = json.loads(crudo)
    except ValueError:
        valor = crudo
    lista = valor if isinstance(valor, list) else [valor]
    return [str(m).strip().lower() for m in lista if str(m).strip()] or None


def _entero(texto: str) -> int | None:
    try:
        return int(float(texto))
    except ValueError:
        return None


def _aplica(span: Span) -> bool:
    # Un span en error no tiene respuesta que evaluar: eso ya lo cuentan las
    # metricas RED, y contarlo aqui otra vez solo duplicaria la alerta.
    return span.operacion in GENERATIVAS and not span.error


class _PorMotivo:
    """Una evaluacion que se decide con `finish_reasons`."""

    nombre = ""
    version = "1"
    coste: Literal["barato", "caro"] = "barato"
    falla_con: frozenset[str] = frozenset()
    # Si la evaluacion pregunta por que paro EL MODELO. Abandono no: pregunta
    # por el cliente, y para eso el desenlace del gateway si vale.
    lee_el_modelo = True

    def evaluar(self, span: Span) -> Evaluacion | None:
        if not _aplica(span):
            return None
        lista = motivos(span)
        if lista is None:
            return self._desconocido("el span no trae gen_ai.response.finish_reasons")
        raros = sorted(set(lista) - CONOCIDOS)
        malos = sorted(set(lista) & self.falla_con)
        if malos:
            return Evaluacion(self.nombre, self.version, Resultado.FALLA, ", ".join(malos))
        if raros:
            return self._desconocido(f"finish_reasons fuera del vocabulario: {', '.join(raros)}")
        if self.lee_el_modelo and set(lista) <= DESENLACE_GATEWAY | ABANDONO:
            return self._desconocido(f"finish_reasons trae el desenlace del gateway ({', '.join(lista)}), "
                                     "no el motivo del modelo")
        return self._pasa(span, lista)

    def _pasa(self, span: Span, lista: list[str]) -> Evaluacion:
        return Evaluacion(self.nombre, self.version, Resultado.PASA)

    def _desconocido(self, motivo: str) -> Evaluacion:
        return Evaluacion(self.nombre, self.version, Resultado.DESCONOCIDO, motivo)


class Rechazo(_PorMotivo):
    """El modelo se nego a responder o el filtro de contenido corto la respuesta."""

    nombre = "rechazo"
    version = "2"
    falla_con = RECHAZO


class Truncado(_PorMotivo):
    """La respuesta se corto por longitud: quien la lee recibe media frase o un JSON roto."""

    nombre = "truncado"
    version = "2"
    falla_con = TRUNCADO

    def _pasa(self, span: Span, lista: list[str]) -> Evaluacion:
        # Hay proveedores que dicen `stop` aunque hayan agotado el limite. Si
        # el span trae los dos numeros, la cuenta manda.
        salida, tope = _entero(span.tokens_salida), _entero(span.max_tokens)
        if salida is not None and tope and salida >= tope:
            return Evaluacion(self.nombre, self.version, Resultado.FALLA,
                              f"agoto max_tokens ({salida} de {tope}) aunque dice {', '.join(lista)}")
        return Evaluacion(self.nombre, self.version, Resultado.PASA)


class Abandono(_PorMotivo):
    """El cliente se fue antes de recibir la respuesta. Casi siempre es su timeout: la espera fue demasiado larga."""

    nombre = "abandono"
    version = "2"
    falla_con = ABANDONO
    lee_el_modelo = False

    def evaluar(self, span: Span) -> Evaluacion | None:
        # Con PRM-282 el desenlace sale de `finish_reasons` y pasa aqui; un
        # abandono llegara con `finish_reasons` vacio.
        if _aplica(span) and span.desenlace:
            if span.desenlace in ABANDONO:
                return Evaluacion(self.nombre, self.version, Resultado.FALLA, span.desenlace)
            return Evaluacion(self.nombre, self.version, Resultado.PASA)
        return super().evaluar(span)


class Vacia(_PorMotivo):
    """Termino bien y no dijo nada: cero tokens de salida sin llamar a una herramienta."""

    nombre = "vacia"

    def evaluar(self, span: Span) -> Evaluacion | None:
        if not _aplica(span):
            return None
        lista = motivos(span) or []
        # Un abandono no es una respuesta vacia: no hubo respuesta. Ya lo cuenta `abandono`.
        if set(lista) & ABANDONO:
            return None
        salida = _entero(span.tokens_salida)
        if salida is None:
            return self._desconocido("el span no trae gen_ai.usage.output_tokens")
        if salida == 0 and not set(lista) & LLAMA_HERRAMIENTA:
            return Evaluacion(self.nombre, self.version, Resultado.FALLA, "0 tokens de salida")
        return Evaluacion(self.nombre, self.version, Resultado.PASA)


BARATAS: tuple[Eval, ...] = (Rechazo(), Truncado(), Abandono(), Vacia())
# Las que cuestan un modelo y solo se aplican a la muestra. Vacia hasta F5-02:
# sin el contenido de los mensajes no hay nada que darles (D-008, D-154).
CARAS: tuple[Eval, ...] = ()


def evaluar(span: Span, *, en_muestra: bool, baratas: tuple[Eval, ...] = BARATAS,
            caras: tuple[Eval, ...] = CARAS) -> list[Evaluacion]:
    elegidas = baratas + (caras if en_muestra else ())
    return [e for ev in elegidas if (e := ev.evaluar(span)) is not None]
