"""F5-02: las evaluaciones con contenido, sobre el juez de L3 en sombra.

**Por que el juez y no las apps.** Groundedness, consistencia y formato
necesitan el texto del prompt y de la respuesta, y el de las apps no esta en
ningun sitio: no lo capturan, ClickHouse lo borra (D-008) y Langfuse esta
apagado. El juez es una app LLM nuestra y su diario de sombra guarda la entrada
y el dictamen crudo. Cuando se encienda Langfuse (B-45), esto se convierte en
una fuente mas.

**Formato** (determinista, Pydantic): el dictamen CRUDO contra el contrato. No
el veredicto: `a_veredicto` arregla en silencio una confianza inventada o una
causa nula con confianza alta, y eso es justo lo que hay que contar. Un fallo
del proveedor no es un fallo de formato: no aplica.

**Groundedness** (con modelo): cada evidencia que AÑADIO el modelo —las de las
reglas salen del expediente por construccion— tiene que tener un fragmento
literal del expediente que la respalde. El modelo propone el fragmento; el
codigo comprueba que esta en el texto. Las cifras se comprueban antes y sin
modelo. La causa no se mide aqui: es una hipotesis, y lo que la sostiene es
justo la evidencia.

**Consistencia** (con modelo solo si hace falta): dos juicios del mismo
episodio no pueden dar causas incompatibles. Abstenerse no es contradecirse.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ValidationError, model_validator

from .evals import Evaluacion, Resultado, Span

log = logging.getLogger("evaluator.juez")

COMPONENTE = "diagnose-juez"
# El contrato que `diagnose.juez_modelo.a_veredicto` hace cumplir a la fuerza.
MAX_EVIDENCIA = 5
MAX_TEXTO = 600
VOLCADOS = ("DETAIL:", "[SQL:", "[parameters:", "(Background on this error")
# Los errores de synaptum que dicen «el modelo no cumplio el esquema». Los
# demas (ProviderError, NetworkError, RequestTimeoutError, LimitExceeded) son
# de disponibilidad y no dicen nada del formato.
ERRORES_DE_FORMATO = ("NoObjectGeneratedError",)
# Un fragmento de menos de esto encaja en cualquier sitio ("503", "gateway").
FRAGMENTO_MINIMO = 12
CIFRA_MINIMA = 31


# --- El registro ---------------------------------------------------------------


@dataclass(frozen=True)
class Registro:
    """Una linea de `sombra.jsonl` con su intercambio con el modelo."""

    cuando: str
    huella: str
    incidente: str
    app: str
    componente: str
    firma: str
    reglas: dict[str, Any]
    modelo: dict[str, Any]
    llm: dict[str, Any]

    @property
    def episodio(self) -> str:
        return f"{self.app}|{self.componente}|{self.firma}"

    @property
    def salida(self) -> dict[str, Any] | None:
        return self.llm.get("salida")

    def como_span(self) -> Span:
        """Para guardarlo en la misma tabla que los spans: la app evaluada es Argus."""
        fin = datetime.fromisoformat(self.cuando).astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f000")
        return Span(trace_id=self.huella, span_id=self.incidente, fin=fin, app="argus", componente=COMPONENTE,
                    operacion="juicio", modelo=str(self.llm.get("modelo") or ""))


def leer(linea: str) -> Registro | None:
    try:
        d = json.loads(linea)
    except ValueError:
        return None
    if not isinstance(d, dict) or not d.get("llm"):
        return None
    return Registro(cuando=d["cuando"], huella=d.get("huella", ""), incidente=d.get("incidente", ""),
                    app=d.get("app", ""), componente=d.get("componente", ""), firma=d.get("firma", ""),
                    reglas=d.get("reglas") or {}, modelo=d.get("modelo") or {}, llm=d["llm"])


# --- Formato ---------------------------------------------------------------------


class Contrato(BaseModel):
    """Lo que el juez tiene que devolver, con las reglas que el codigo de L3 arregla si no se cumplen."""

    causa: str | None
    confianza: Literal["alta", "media", "baja"]
    evidencia: list[str]

    @model_validator(mode="after")
    def _reglas(self) -> Contrato:
        causa = (self.causa or "").strip()
        if not causa and self.confianza != "baja":
            raise ValueError(f"sin causa y con confianza {self.confianza}")
        if causa and not self.evidencia:
            raise ValueError("causa sin evidencia")
        if len(self.evidencia) > MAX_EVIDENCIA:
            raise ValueError(f"{len(self.evidencia)} evidencias, el maximo es {MAX_EVIDENCIA}")
        for texto in [causa, *self.evidencia]:
            if len(texto) > MAX_TEXTO:
                raise ValueError(f"un texto de {len(texto)} caracteres, el maximo es {MAX_TEXTO}")
            if any(v in texto for v in VOLCADOS):
                raise ValueError("vuelca la fila o la sentencia SQL")
        return self


def _motivo(exc: ValidationError) -> str:
    return "; ".join(str(e.get("msg", "")).removeprefix("Value error, ") for e in exc.errors())[:300]


def formato(r: Registro) -> Evaluacion | None:
    if r.salida is None:
        error = str(r.llm.get("error") or "")
        if error.startswith(ERRORES_DE_FORMATO):
            return Evaluacion("formato", "1", Resultado.FALLA, error[:300])
        return None
    try:
        Contrato.model_validate(r.salida)
    except ValidationError as exc:
        return Evaluacion("formato", "1", Resultado.FALLA, _motivo(exc))
    return Evaluacion("formato", "1", Resultado.PASA)


# --- Groundedness ------------------------------------------------------------------


def _normal(texto: str) -> str:
    return " ".join(texto.lower().split())


def _numeros(texto: str) -> list[str]:
    sin_fechas = re.sub(r"\d{4}-\d{2}-\d{2}[T ]?\d{0,2}:?\d{0,2}:?\d{0,2}(\.\d+)?|\d{1,2}:\d{2}(:\d{2})?", " ", texto)
    return re.findall(r"\d+(?:[.,]\d+)?", sin_fechas)


def cifras_sin_respaldo(afirmacion: str, entrada: str) -> list[str]:
    """Cifras de la afirmacion que no aparecen en la entrada. Las pequeñas no: «3 de 5» sale en cualquier sitio.

    Se comparan como NUMEROS redondeados a los decimales de la afirmacion, no
    como texto: «el 100 %» esta respaldado por un «100.0%» del expediente, y
    «26.7» por un «26.67». Compararlas como texto suspendio el unico caso del
    banco que fallo con el expediente correcto.
    """
    en_la_entrada = [float(n.replace(",", ".")) for n in _numeros(entrada)]
    faltan = []
    for c in _numeros(afirmacion):
        normal = c.replace(",", ".")
        valor = float(normal)
        decimales = len(normal.split(".")[1]) if "." in normal else 0
        if not ("." in normal or valor > CIFRA_MINIMA):
            continue
        if not any(round(x, decimales) == valor for x in en_la_entrada):
            faltan.append(c)
    return faltan


def anadidas_por_el_modelo(r: Registro) -> list[str]:
    """Las evidencias del modelo que no venian ya de las reglas."""
    de_las_reglas = {_normal(e) for e in r.reglas.get("evidence") or []}
    salida = r.salida or {}
    return [e for e in salida.get("evidencia") or [] if str(e).strip() and _normal(str(e)) not in de_las_reglas]


def fragmento_valido(fragmento: str | None, entrada: str) -> bool:
    if not fragmento or len(fragmento.strip()) < FRAGMENTO_MINIMO:
        return False
    return _normal(fragmento) in _normal(entrada)


class Juez(Protocol):
    """El modelo que respalda o no. Dos preguntas cerradas; el codigo decide."""

    def respaldo(self, entrada: str, afirmaciones: list[str]) -> list[str | None]:
        """Para cada afirmacion, el fragmento literal de la entrada que la respalda, o `None`."""
        ...

    def misma_causa(self, a: str, b: str) -> bool:
        ...


def groundedness(r: Registro, juez: Juez) -> Evaluacion | None:
    afirmaciones = anadidas_por_el_modelo(r)
    if not afirmaciones:
        return None
    entrada = str(r.llm.get("entrada") or "")
    sin_respaldo: list[str] = []
    a_preguntar: list[str] = []
    for a in afirmaciones:
        faltan = cifras_sin_respaldo(a, entrada)
        if faltan:
            sin_respaldo.append(f"«{a[:80]}» (cifras {', '.join(faltan)} que no estan en el expediente)")
        else:
            a_preguntar.append(a)
    if a_preguntar:
        fragmentos = juez.respaldo(entrada, a_preguntar)
        for a, frag in zip(a_preguntar, fragmentos, strict=True):
            if not fragmento_valido(frag, entrada):
                sin_respaldo.append(f"«{a[:80]}»")
    if sin_respaldo:
        return Evaluacion("groundedness", "1", Resultado.FALLA,
                          f"{len(sin_respaldo)} de {len(afirmaciones)} sin respaldo: " + "; ".join(sin_respaldo)[:400])
    return Evaluacion("groundedness", "1", Resultado.PASA, f"{len(afirmaciones)} de {len(afirmaciones)} respaldadas")


# --- Consistencia ------------------------------------------------------------------


PARECIDO_SUFICIENTE = 0.6


def consistencia(r: Registro, anterior: str | None, juez: Juez) -> Evaluacion | None:
    """`anterior` es la causa del juicio previo del mismo episodio (`""` = se abstuvo, `None` = no hubo)."""
    if anterior is None or r.salida is None:
        return None
    actual = str((r.salida or {}).get("causa") or "").strip()
    if not actual or not anterior:
        return Evaluacion("consistencia", "1", Resultado.PASA, "uno de los dos se abstuvo: no es contradecirse")
    if difflib.SequenceMatcher(None, _normal(actual), _normal(anterior)).ratio() >= PARECIDO_SUFICIENTE:
        return Evaluacion("consistencia", "1", Resultado.PASA)
    if juez.misma_causa(anterior, actual):
        return Evaluacion("consistencia", "1", Resultado.PASA, "dicho de otra forma")
    return Evaluacion("consistencia", "1", Resultado.FALLA, f"antes «{anterior[:120]}», ahora «{actual[:120]}»")


# --- La fuente ---------------------------------------------------------------------


@dataclass
class EstadoDiario:
    """Hasta que byte se leyo y la ultima causa de cada episodio. En un fichero."""

    ruta: Path
    posicion: int = 0
    causas: dict[str, tuple[str, str]] = field(default_factory=dict)  # episodio -> (cuando, causa)

    @classmethod
    def cargar(cls, ruta: Path) -> EstadoDiario:
        try:
            d = json.loads(ruta.read_text())
            return cls(ruta, int(d["posicion"]), {k: tuple(v) for k, v in d.get("causas", {}).items()})
        except (OSError, ValueError, KeyError):
            return cls(ruta)

    def guardar(self) -> None:
        tmp = self.ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps({"posicion": self.posicion, "causas": self.causas}))
        tmp.replace(self.ruta)


class EvaluadorDelJuez:
    """Lee lo nuevo del diario de la sombra, evalua y lo entrega a `guardar`.

    Las caras (groundedness, consistencia) tienen tope por hora: son inferencia
    de la plataforma compartida. Lo que pasa del tope se queda sin evaluar y se
    cuenta; formato se evalua siempre.
    """

    def __init__(self, diario: Path, estado: EstadoDiario, juez: Juez | None,
                 guardar: Callable[[list[tuple[Span, Evaluacion, str]], datetime], None], *,
                 caras_por_hora: int = 6, reloj: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.diario, self.estado, self.juez, self.guardar = diario, estado, juez, guardar
        self.caras_por_hora, self.reloj = caras_por_hora, reloj
        self._caras: list[datetime] = []
        self.stats: dict[str, int] = {"registros": 0, "caras": 0, "caras_por_ritmo": 0, "caras_fallidas": 0}

    def _puede_caras(self, ahora: datetime) -> bool:
        self._caras = [t for t in self._caras if (ahora - t).total_seconds() < 3600]
        if self.juez is None or len(self._caras) >= self.caras_por_hora:
            return False
        self._caras.append(ahora)
        return True

    def vuelta(self) -> list[tuple[Span, Evaluacion, str]]:
        if not self.diario.exists():
            return []
        tam = self.diario.stat().st_size
        if tam < self.estado.posicion:  # el diario se trunco o roto: se empieza de nuevo
            self.estado.posicion = 0
        filas: list[tuple[Span, Evaluacion, str]] = []
        with self.diario.open("rb") as f:
            f.seek(self.estado.posicion)
            for cruda in f:
                if not cruda.endswith(b"\n"):  # linea a medio escribir: la proxima vuelta
                    break
                self.estado.posicion += len(cruda)
                r = leer(cruda.decode("utf-8", errors="replace"))
                if r is None:
                    continue
                self.stats["registros"] += 1
                filas.extend((r.como_span(), e, "diario") for e in self._evaluar(r))
        ahora = self.reloj()
        self.guardar(filas, ahora)
        self.estado.guardar()
        return filas

    def _evaluar(self, r: Registro) -> list[Evaluacion]:
        hechas = [e for e in (formato(r),) if e is not None]
        anterior = self.estado.causas.get(r.episodio)
        if r.salida is not None:
            self.estado.causas[r.episodio] = (r.cuando, str(r.salida.get("causa") or "").strip())
        if r.salida is None:
            return hechas
        if not self._puede_caras(self.reloj()):
            self.stats["caras_por_ritmo"] += 1
            return hechas
        self.stats["caras"] += 1
        juez = self.juez
        assert juez is not None  # `_puede_caras` ya lo comprobo
        try:
            for e in (groundedness(r, juez), consistencia(r, anterior[1] if anterior else None, juez)):
                if e is not None:
                    hechas.append(e)
        except Exception as exc:  # noqa: BLE001 - un modelo caido no para el resto
            self.stats["caras_fallidas"] += 1
            log.warning("evaluator.juez.cara_fallida", extra={"error": f"{type(exc).__name__}: {exc}"[:200]})
        return hechas
