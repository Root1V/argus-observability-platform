"""F3-08: el banco de incidentes con causa conocida. La unica forma honesta de medir.

Un caso del banco es un incidente REAL, su expediente CONGELADO y la causa que
establecio alguien que no es L3. Las tres condiciones tienen su porque:

- **Real**, porque los errores que importan de este servicio son errores sobre
  como es el mundo, y un incidente inventado los esconde. Las tres
  correcciones de D-131 salieron de incidentes reales con la suite en verde.
- **Congelado**, porque las trazas caducan a los 30 dias (TTL del almacen).
  Un banco que recolecta en vivo deja de existir en un mes y, peor, cambia de
  contenido sin avisar: el juez mejoraria o empeoraria sin haber cambiado.
- **Causa establecida fuera de L3**: por el equipo dueño del servicio, o por
  una medicion independiente de la regla que se evalua. Si la causa la
  escribio L3, el banco mide a L3 contra si mismo.

**Casos de diseño y casos no vistos.** Tres casos se usaron para escribir las
reglas —la traza del 502, el 404 y el `ReadTimeout`—. Puntuar al juez sobre
ellos es puntuarlo sobre sus propios deberes. El informe los separa, y la
cifra que vale es la de los no vistos.

**Como se califica.** Cuatro resultados, y el orden de gravedad no es el
obvio:

| | |
|---|---|
| `acierta`     | afirma la causa |
| `parcial`     | nombra el sintoma, o solo parte de la causa |
| `se_abstiene` | no propone causa (`root_cause` vacio) |
| `falla`       | afirma algo que la causa real desmiente, o una causa que no es ni sintoma ni causa |

`se_abstiene` es MEJOR que `falla`. El aviso sigue diciendo «investigando…» y
nadie va a mirar donde no es. Y lo peor de todo es `falla` con confianza
`alta`, que es lo que este banco existe para que no pase sin verse.

La calificacion es en dos pasos: decidir criterio a criterio si el texto lo
afirma, y componer esas decisiones (`componer`). El primer paso es por palabras
clave salvo que se pase otro calificador, y las palabras clave tienen un limite
que hay que decir: funcionan con el juez de reglas porque sus textos son
plantillas, y fallan con un modelo, que dice lo mismo con otras palabras. El
calificador que entiende paráfrasis, y el corpus contra el que se le mide antes
de creerle, estan en `calificador.py` (B-41).
"""

from __future__ import annotations

import json
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .expediente import Expediente
from .juicio import Juez, Veredicto

DIRECTORIO = Path(__file__).resolve().parents[2] / "banco"

Resultado = Literal["acierta", "parcial", "se_abstiene", "falla"]


def _normalizar(texto: str) -> str:
    """Minusculas y sin acentos: las reglas escriben sin tildes y un modelo no."""
    sin_tildes = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in sin_tildes if not unicodedata.combining(c)).lower()


class Criterio(BaseModel):
    """Un hecho que el veredicto tiene que afirmar (o no puede afirmar).

    `alguna_de` son formas de decirlo: basta una. El `hecho` es la frase que
    lee una persona al revisar por que un caso salio como salio.
    """

    hecho: str
    alguna_de: list[str]

    def se_cumple(self, texto: str) -> bool:
        t = _normalizar(texto)
        return any(_normalizar(k) in t for k in self.alguna_de)


class Caso(BaseModel):
    id: str
    titulo: str

    # --- La verdad, y de donde sale ----------------------------------------
    causa_real: str
    fuente: str
    # `confirmada`: la dijo el equipo dueño del servicio.
    # `medida`: la establece una medicion independiente de la regla evaluada,
    # y queda pendiente de que el dueño la confirme.
    nivel: Literal["confirmada", "medida"]
    usado_para_disenar: bool = False

    sintoma: list[Criterio] = Field(default_factory=list)
    causa: list[Criterio] = Field(default_factory=list)
    prohibido: list[Criterio] = Field(default_factory=list)

    # Lo que deberia hacer un buen juez cuando el expediente no basta. Es
    # parte de la etiqueta, no un comentario: hay casos donde abstenerse ES la
    # respuesta correcta, y el informe tiene que poder decirlo.
    abstenerse_es_correcto: bool = False

    # --- El incidente tal y como estaba -------------------------------------
    capturado_en: datetime
    incidente: dict
    expediente: Expediente
    notas: str = ""


class Calificacion(BaseModel):
    caso: str
    resultado: Resultado
    confianza: str
    origen: str
    diseno: bool
    faltan: list[str] = Field(default_factory=list)
    prohibidos: list[str] = Field(default_factory=list)
    causa_dicha: str | None = None


def _lo_que_anade(texto: str, caso: Caso) -> str:
    """El veredicto sin las citas del propio incidente.

    Solo puntua lo que el juez AÑADE. La primera pasada del banco dio dos
    aciertos en casos no vistos que eran eco: la regla repetia la firma del
    incidente —que antes de D-118 era el mensaje entero— y el calificador
    encontraba «GenAISpan.set», «previous exception» y «tenant» dentro de la
    cita. Uno de esos dos veredictos terminaba diciendo que la causa era
    `PendingRollbackError`, que es exactamente el sintoma (D-134).

    Y no solo la cita exacta: tambien la que repite casi toda la firma con
    otro prefijo (D-139). Las reglas «acertaban» `aeon` diciendo
    «GenAISpan.set() got an unexpected keyword argument 'argus.run.id'» — la
    firma sin su `TypeError: `. Citar una PARTE de la firma si cuenta, y es a
    proposito: separar la excepcion original del `PendingRollbackError` que la
    envuelve es exactamente el diagnostico que ese caso pide.
    """
    for cita in (caso.incidente.get("signature"), caso.incidente.get("title")):
        if not cita or len(cita) <= 3:
            continue
        cita = str(cita)
        texto = texto.replace(cita, "«…»")
        texto = _sin_casi_toda(texto, cita)
    return texto


def _sin_casi_toda(texto: str, cita: str, *, cobertura: float = 0.8) -> str:
    """Quita el tramo de `texto` que repite al menos el 80 % de `cita` seguido."""
    from difflib import SequenceMatcher

    m = SequenceMatcher(None, texto, cita, autojunk=False).find_longest_match(0, len(texto), 0, len(cita))
    if m.size >= 20 and m.size >= cobertura * len(cita):
        return texto[: m.a] + "«…»" + texto[m.a + m.size :]
    return texto


def componer(caso: Caso, root_cause: str | None, afirma: dict[str, bool]) -> tuple[Resultado, list[str], list[str]]:
    """Las decisiones criterio a criterio → un resultado, lo que falta y lo prohibido.

    Separado de DECIDIR si un texto afirma algo (B-41), porque son dos
    preguntas: la de como se dice algo y la de cuanto vale lo dicho. Esta es
    la segunda, y es la misma para cualquier calificador.

    Media causa es `parcial`, no `falla`. Hasta B-41 `parcial` exigia el
    sintoma entero, y un «el problema es del host o de la ingesta» sin decir
    «todos los servicios a la vez» salia `falla` — por debajo de no decir nada,
    para la mitad correcta de la respuesta (D-139).
    """
    prohibidos = [c.hecho for c in caso.prohibido if afirma.get(c.hecho)]
    if prohibidos:
        return "falla", [], prohibidos
    if not root_cause:
        return "se_abstiene", [], []
    faltan_causa = [c.hecho for c in caso.causa if not afirma.get(c.hecho)]
    if caso.causa and not faltan_causa:
        return "acierta", [], []
    media_causa = len(faltan_causa) < len(caso.causa)
    sintoma_entero = bool(caso.sintoma) and all(afirma.get(c.hecho) for c in caso.sintoma)
    if media_causa or sintoma_entero:
        return "parcial", faltan_causa, []
    faltan_sintoma = [c.hecho for c in caso.sintoma if not afirma.get(c.hecho)]
    return "falla", faltan_causa + faltan_sintoma, []


def calificar(caso: Caso, veredicto: Veredicto, calificador=None) -> Calificacion:
    """Un veredicto contra la etiqueta de un caso.

    `calificador` decide si el texto afirma cada criterio (`calificador.py`).
    Sin el, palabras clave.
    """
    texto = _lo_que_anade(veredicto.root_cause or "", caso)
    afirma = {
        c.hecho: (calificador.afirma(texto, c) if calificador else c.se_cumple(texto)) if veredicto.root_cause else False
        for c in (*caso.causa, *caso.sintoma, *caso.prohibido)
    }
    resultado, faltan, prohibidos = componer(caso, veredicto.root_cause, afirma)
    return Calificacion(
        caso=caso.id,
        resultado=resultado,
        confianza=veredicto.confidence,
        origen=veredicto.origen,
        diseno=caso.usado_para_disenar,
        faltan=faltan,
        prohibidos=prohibidos,
        causa_dicha=veredicto.root_cause,
    )


def cargar(directorio: Path = DIRECTORIO) -> list[Caso]:
    return [
        Caso.model_validate_json(f.read_text(encoding="utf-8"))
        for f in sorted(directorio.glob("*.json"))
    ]


def evaluar(juez: Juez, casos: list[Caso], calificador=None) -> list[Calificacion]:
    return [calificar(c, juez.juzgar(c.expediente), calificador) for c in casos]


def resumen(calificaciones: list[Calificacion]) -> dict[str, dict[str, int]]:
    """Cuenta por resultado, separando diseño de no vistos."""
    salida: dict[str, dict[str, int]] = {}
    for grupo, filtro in (("no_vistos", False), ("diseno", True)):
        filas = [c for c in calificaciones if c.diseno is filtro]
        cuenta = {r: 0 for r in ("acierta", "parcial", "se_abstiene", "falla")}
        for c in filas:
            cuenta[c.resultado] += 1
        cuenta["total"] = len(filas)
        cuenta["falla_con_alta"] = sum(1 for c in filas if c.resultado == "falla" and c.confianza == "alta")
        salida[grupo] = cuenta
    return salida


def informe(calificaciones: list[Calificacion], casos: list[Caso]) -> str:
    por_id = {c.id: c for c in casos}
    lineas = []
    for titulo, filtro in (("CASOS NO VISTOS (la cifra que vale)", False), ("CASOS DE DISEÑO", True)):
        lineas.append(titulo)
        for c in calificaciones:
            if c.diseno is not filtro:
                continue
            caso = por_id[c.caso]
            marca = {"acierta": "✓", "parcial": "~", "se_abstiene": "·", "falla": "✗"}[c.resultado]
            extra = " (abstenerse era lo correcto)" if c.resultado == "se_abstiene" and caso.abstenerse_es_correcto else ""
            lineas.append(f"  {marca} {c.resultado:<12} [{c.confianza:<5}] {caso.id}{extra}")
            for f in c.faltan:
                lineas.append(f"        falta: {f}")
            for p in c.prohibidos:
                lineas.append(f"        AFIRMA LO PROHIBIDO: {p}")
        lineas.append("")
    r = resumen(calificaciones)
    for grupo in ("no_vistos", "diseno"):
        g = r[grupo]
        lineas.append(
            f"{grupo:<10} {g['total']} casos · acierta {g['acierta']} · parcial {g['parcial']} · "
            f"se abstiene {g['se_abstiene']} · falla {g['falla']} (con confianza alta: {g['falla_con_alta']})"
        )
    return "\n".join(lineas)


# --- Captura desde el stack real ----------------------------------------------


def sanear(valor):
    """Quita el volcado de filas y sentencias de cualquier texto, a cualquier profundidad.

    El banco vive en el repositorio, y el repositorio tiene remoto. La primera
    captura metio en dos casos el `DETAIL:` de Postgres y el `[parameters:]` de
    SQLAlchemy de Veritium: UUID de sus filas y un codigo de empleado,
    veinticuatro veces. Pasaron la redaccion del colector porque no tienen
    forma de identificador conocido, y no son datos nuestros para publicar.

    Se corta en el mismo sitio que en `juicio._sin_volcado`: lo que explica el
    fallo esta antes del volcado, y el banco no pierde nada que un juez
    necesite.
    """
    from .juicio import _sin_volcado

    if isinstance(valor, str):
        return _sin_volcado(valor) if any(m in valor for m in ("DETAIL:", "[SQL:", "[parameters:")) else valor
    if isinstance(valor, list):
        return [sanear(v) for v in valor]
    if isinstance(valor, dict):
        return {k: sanear(v) for k, v in valor.items()}
    return valor


# Lo que el incidente lleva de vuelta de L3. Se quita al capturar: un caso cuyo
# incidente ya trae la respuesta de L3 es un examen con las soluciones grapadas.
CAMPOS_DE_L3 = ("root_cause", "confidence", "evidence", "similar_incidents", "enriched_at")


def capturar(incidente: dict, *, almacen, registro: list[dict], historico: list[dict]) -> Expediente:
    """El expediente de un incidente tal y como se podia recolectar EN SU MOMENTO.

    Dos fugas que evitar, y las dos son de las que no dan error:

    - El historico se corta en el incidente: solo cuenta lo abierto ANTES. Si
      no, el juez veria causas anotadas despues — incluidas las que escribio
      el propio L3.
    - Los campos que L3 ya rellenó se quitan del incidente.

    La recurrencia en trazas ya se cuenta hasta el incidente y no hasta hoy:
    lo hace `recolectar` con su `hasta`.
    """
    from .recoleccion import _fecha, recolectar

    limpio = {k: v for k, v in incidente.items() if k not in CAMPOS_DE_L3}
    abierto = str(incidente.get("opened_at") or "")
    anterior = [d for d in historico if str(d.get("opened_at") or "") < abierto]
    # «Ahora», para un incidente capturado, es su ultima señal. Con el reloj de
    # hoy, el primer borrador del caso de aeon decia «16 señales en 2.181.680 s»
    # —25 dias— para un fallo que duro minutos.
    momento = _fecha(incidente.get("last_seen_at")) or _fecha(incidente.get("opened_at"))
    return recolectar(limpio, almacen=almacen, registro=registro, historico=anterior, ahora=momento)


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import os

    import httpx

    from .almacen import Almacen
    from .juicio import JuezDeReglas

    parser = argparse.ArgumentParser(prog="python -m diagnose.banco")
    sub = parser.add_subparsers(dest="orden", required=True)
    cap = sub.add_parser("capturar", help="congela el expediente de un incidente real")
    cap.add_argument("huella")
    cap.add_argument("--caso", required=True, help="id del caso, p. ej. veritium-tenant")
    cap.add_argument("--bus", default="http://127.0.0.1:8080")
    ev = sub.add_parser("evaluar", help="pasa un juez sobre el banco")
    ev.add_argument("--juez", default="reglas", choices=("reglas", "modelo"))
    ev.add_argument("--modelo", default=os.environ.get("DIAGNOSE_MODELO", "gpt-oss-20b-mxfp4"))
    ev.add_argument("--razonamiento", default=None, choices=("low", "medium", "high"))
    ev.add_argument("--calificador", default="palabras", choices=("palabras", "modelo"),
                    help="quien decide si el veredicto afirma cada criterio (B-41)")
    rec = sub.add_parser("recapturar", help="rehace el expediente de un caso sin tocar su etiqueta")
    rec.add_argument("caso")
    rec.add_argument("--bus", default="http://127.0.0.1:8080")
    args = parser.parse_args(argv)

    if args.orden == "evaluar":
        casos = cargar()
        if "modelo" in (args.juez, args.calificador) and not os.environ.get("AXONIUM_CLIENT_ID"):
            print("el modelo necesita AXONIUM_CLIENT_ID y AXONIUM_CLIENT_SECRET en el entorno")
            return 2
        if args.juez == "modelo":
            from .juez_modelo import JuezConModelo, gateway_axonium

            juez = JuezConModelo(gateway_axonium, modelo=args.modelo, razonamiento=args.razonamiento)
        else:
            juez = JuezDeReglas()
        calificador = None
        if args.calificador == "modelo":
            from .calificador import ConModelo
            from .juez_modelo import gateway_axonium

            calificador = ConModelo(gateway_axonium, modelo=args.modelo)
        print(informe(evaluar(juez, casos, calificador), casos))
        return 0

    historico = httpx.get(f"{args.bus}/incidents/history?dias=30", timeout=30).json()
    registro = httpx.get(f"{args.bus}/registry", timeout=10).json()
    almacen = Almacen(
        os.environ.get("DIAGNOSE_CLICKHOUSE_URL", "http://127.0.0.1:8123"),
        os.environ["DIAGNOSE_CLICKHOUSE_USER"],
        os.environ["DIAGNOSE_CLICKHOUSE_PASSWORD"],
    )

    if args.orden == "recapturar":
        # Cuando el expediente gana un bloque, los casos congelados no lo
        # tienen. Esto lo recalcula A FECHA DEL INCIDENTE —el historico se
        # corta igual y las consultas llevan su `hasta`— y deja la etiqueta
        # exactamente como estaba: la verdad de un caso no cambia porque el
        # expediente sepa mas.
        destino = DIRECTORIO / f"{args.caso}.json"
        caso = json.loads(destino.read_text(encoding="utf-8"))
        incidente = caso["incidente"]
        expediente = capturar(incidente, almacen=almacen, registro=registro, historico=historico)
        caso["expediente"] = sanear(json.loads(expediente.model_dump_json()))
        destino.write_text(json.dumps(caso, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"→ {destino}  ({len(expediente.lagunas)} lagunas)")
        return 0

    candidatos = [d for d in historico if d.get("fingerprint") == args.huella]
    if not candidatos:
        print(f"no hay ningun incidente con huella {args.huella} en el historico")
        return 1
    incidente = max(candidatos, key=lambda d: str(d.get("opened_at")))
    expediente = capturar(incidente, almacen=almacen, registro=registro, historico=historico)
    destino = DIRECTORIO / f"{args.caso}.json"
    borrador = {
        "id": args.caso,
        "titulo": "POR ESCRIBIR",
        "causa_real": "POR ESCRIBIR",
        "fuente": "POR ESCRIBIR",
        "nivel": "medida",
        "capturado_en": datetime.now().astimezone().isoformat(),
        "incidente": sanear({k: v for k, v in incidente.items() if k not in CAMPOS_DE_L3}),
        "expediente": sanear(json.loads(expediente.model_dump_json())),
    }
    destino.write_text(json.dumps(borrador, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(expediente.texto())
    print(f"\n→ {destino}  ({len(expediente.lagunas)} lagunas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
