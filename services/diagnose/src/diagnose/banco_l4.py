"""El banco de L4: los mismos casos que F3-08, con las HERRAMIENTAS congeladas.

L3 se mide con un expediente congelado porque las trazas caducan a los 30 dias
y cambian cada minuto. L4 tiene el mismo problema multiplicado: no lee un
expediente, PREGUNTA, y lo que devuelve una pregunta hoy no es lo que habria
devuelto a la hora del incidente. Un banco que investigara contra el stack vivo
mediria al agente y al dia en que se paso, mezclados.

Asi que se graba, para cada caso y A LA HORA DEL INCIDENTE, lo que contestaria
cada consulta de `mcp-obs` con las preguntas que un investigador razonable
haria —la traza del ejemplo, el alcance, la recurrencia de su clase, la linea
base de la contraparte, los concurrentes, el registro—, y en la evaluacion se
sirve con **el mismo servidor `mcp-obs`** y un almacen congelado debajo. El
agente no puede distinguirlo del real, que es lo que hace que la medida valga.

Lo que el agente pregunte y no este grabado se contesta vacio y SE CUENTA:
`sin_datos` alto significa que el banco no cubre lo que el agente quiso mirar,
y entonces la cifra dice menos de lo que parece.

El expediente que sirve es el congelado del caso, el mismo que lee el juez de
L3: asi L4 y L3 se miden sobre la misma informacion de partida, y lo que L4
añade es solo lo que fue a buscar.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .banco import DIRECTORIO, Caso, cargar, sanear

log = logging.getLogger("diagnose.banco_l4")

DIRECTORIO_L4 = DIRECTORIO / "l4"

# Que argumentos identifican una consulta. Los de tiempo (`desde`, `hasta`,
# `dias`, `minutos`) no: en el banco el tiempo esta fijo en el incidente.
CLAVES = {
    "spans_de_traza": ("trace_id",),
    "alcance": ("app", "componente"),
    "recurrencia": ("app", "componente", "clase"),
    "linea_base": ("servicio", "ruta"),
    "concurrentes": ("componente",),
}

# Lo unico que el arbol lee de un span (`arbol_desde_filas`). El resto de
# atributos no se graba: el banco vive en un repositorio con remoto.
ATRIBUTOS_DEL_ARBOL = ("error.type", "url.full", "http.url", "http.route")
EVENTOS_DEL_ARBOL = ("exception.type", "exception.message")

VENTANA = timedelta(minutes=15)


def clave(metodo: str, **argumentos: Any) -> str:
    partes = []
    for k in CLAVES[metodo]:
        v = str(argumentos.get(k) or "").strip()
        if k == "clase":
            v = v.split(":", 1)[0].rsplit(".", 1)[-1]   # `httpx.ConnectError` y `ConnectError`, la misma
        if k == "ruta" and "://" in v:
            v = urlparse(v).path
        partes.append(f"{k}={v.lower()}")
    return metodo + "|" + "|".join(partes)


class AlmacenCongelado:
    """Contesta las consultas de `mcp-obs` con lo grabado. Lo que no esta, vacio y anotado."""

    def __init__(self, grabado: dict[str, list[dict[str, Any]]], fallos: Path | None = None) -> None:
        self._grabado = grabado
        self._fallos = fallos

    def _servir(self, metodo: str, **kw: Any) -> list[dict[str, Any]]:
        k = clave(metodo, **kw)
        if k in self._grabado:
            return self._grabado[k]
        if self._fallos is not None:
            with self._fallos.open("a", encoding="utf-8") as f:
                f.write(k + "\n")
        return []

    def spans_de_traza(self, trace_id: str) -> list[dict[str, Any]]:
        return self._servir("spans_de_traza", trace_id=trace_id)

    def alcance(self, **kw: Any) -> list[dict[str, Any]]:
        return self._servir("alcance", **kw)

    def recurrencia(self, **kw: Any) -> list[dict[str, Any]]:
        return self._servir("recurrencia", **kw)

    def linea_base(self, **kw: Any) -> list[dict[str, Any]]:
        return self._servir("linea_base", **kw)

    def concurrentes(self, **kw: Any) -> list[dict[str, Any]]:
        return self._servir("concurrentes", **kw)

    def cerrar(self) -> None: ...


# --- Grabar ----------------------------------------------------------------


def _span_minimo(fila: dict[str, Any]) -> dict[str, Any]:
    atributos = dict(fila.get("SpanAttributes") or {})
    eventos = []
    for attr in list(fila.get("Events.Attributes") or []):
        eventos.append({k: v for k, v in dict(attr or {}).items() if k in EVENTOS_DEL_ARBOL})
    return sanear({
        **{k: fila.get(k) for k in ("TraceId", "SpanId", "ParentSpanId", "SpanName", "SpanKind", "ServiceName",
                                     "Duration", "StatusCode", "StatusMessage", "Timestamp")},
        "SpanAttributes": {k: v for k, v in atributos.items() if k in ATRIBUTOS_DEL_ARBOL},
        "Events.Name": list(fila.get("Events.Name") or []),
        "Events.Attributes": eventos,
    })


def grabar(caso: Caso, almacen: Any, registro: list[dict[str, Any]]) -> dict[str, Any]:
    """Lo que contestaria cada consulta razonable, a la hora del incidente."""
    from .juicio import _clase_de
    from .recoleccion import _clase_para_contar, _fecha

    inc, exp = caso.incidente, caso.expediente
    abierto = _fecha(inc.get("opened_at"))
    hasta = _fecha(inc.get("last_seen_at")) or abierto
    app, componente = exp.app, exp.componente
    g: dict[str, list[dict[str, Any]]] = {}

    def guardar(metodo: str, filas: list[dict[str, Any]], **kw: Any) -> None:
        g[clave(metodo, **kw)] = sanear(filas)

    for traza in list(inc.get("trace_ids") or [])[:3]:
        g[clave("spans_de_traza", trace_id=traza)] = [_span_minimo(f) for f in almacen.spans_de_traza(traza)]
    guardar("alcance", almacen.alcance(app=app, componente=componente, desde=hasta - VENTANA, hasta=hasta),
            app=app, componente=componente)
    guardar("concurrentes", almacen.concurrentes(componente=componente, desde=hasta - VENTANA, hasta=hasta),
            componente=componente)
    clases = {_clase_para_contar(exp), _clase_de(exp.firma)}
    if (eslabon := exp.arbol.eslabon_mas_profundo) is not None and eslabon.clase:
        clases.add(eslabon.clase)
    for c in sorted(x for x in clases if x and " " not in x):
        guardar("recurrencia", almacen.recurrencia(app=app, componente=componente, clase=c, hasta=abierto, dias=30),
                app=app, componente=componente, clase=c)
    cp = exp.contraparte
    if cp is not None and cp.destino and eslabon is not None:
        servicio = cp.servicio if cp.encontrada and cp.servicio else None
        ruta = urlparse(cp.destino).path if "://" in cp.destino else cp.destino
        if servicio and ruta:
            guardar("linea_base", almacen.linea_base(servicio=servicio, ruta=ruta, hasta=abierto,
                                                     plazo_ms=max(eslabon.ms, 1.0)),
                    servicio=servicio, ruta=ruta)
    return {
        "caso": caso.id,
        "hasta": hasta.isoformat() if hasta else None,
        "almacen": g,
        "registro": sanear(registro),
    }


def ruta_de(caso_id: str) -> Path:
    return DIRECTORIO_L4 / f"{caso_id}.json"


def cargar_grabado(caso_id: str) -> dict[str, Any]:
    return json.loads(ruta_de(caso_id).read_text(encoding="utf-8"))


def servidor_congelado(caso: Caso, grabado: dict[str, Any], fallos: Path | None = None) -> Any:
    from .mcp_obs import crear_servidor

    incidente = {**caso.incidente}
    rutas = {
        "/incidents": [incidente],
        "/incidents/history?dias=30": [incidente],
        "/registry": grabado.get("registro") or [],
    }
    return crear_servidor(
        AlmacenCongelado(grabado["almacen"], fallos),
        lambda ruta: rutas.get(ruta),
        expediente_de=lambda _d: caso.expediente.texto(),
    )


# --- Evaluar ---------------------------------------------------------------


async def investigar_caso(caso: Caso, *, modelo: str, razonamiento: str | None, fallos: Path) -> Any:
    import sys

    from synaptum.mcp import MCPTools

    from .investigador import Investigador, gateway_con_herramientas

    fallos.unlink(missing_ok=True)
    args = ("-m", "diagnose.banco_l4", "servir", caso.id, "--fallos", str(fallos))
    async with MCPTools.stdio(sys.executable, *args) as obs:
        inv = Investigador(gateway_con_herramientas, obs.tools, modelo=modelo, razonamiento=razonamiento)
        return await inv.investigar_async(str(caso.incidente.get("fingerprint") or caso.expediente.huella))


def evaluar(casos: list[Caso], *, modelo: str, razonamiento: str | None, salida: Path | None = None) -> str:
    import asyncio
    import tempfile
    from dataclasses import asdict

    from .banco import calificar, informe
    from .juicio import Veredicto

    filas, notas = [], []
    tmp = Path(tempfile.mkdtemp(prefix="banco-l4-"))
    for caso in casos:
        fallos = tmp / f"{caso.id}.fallos"
        r = asyncio.run(investigar_caso(caso, modelo=modelo, razonamiento=razonamiento, fallos=fallos))
        v = Veredicto(root_cause=r.causa, confidence=r.confianza, evidence=r.evidencia, origen=r.origen)
        n = calificar(caso, v)
        notas.append(n)
        sin_datos = fallos.read_text().splitlines() if fallos.exists() else []
        filas.append({"caso": caso.id, "resultado": n.resultado, **asdict(r), "sin_datos": sin_datos})
        print(f"{n.resultado:<12} [{r.confianza:<5}] {r.segundos:6.1f}s  herr {len(r.herramientas_usadas):>2}"
              f"  sin datos {len(sin_datos)}  citas inventadas {len(r.citas_inventadas)}  {r.final:<17} {caso.id}",
              flush=True)
    if salida is not None:
        salida.write_text(json.dumps(filas, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return informe(notas, casos)


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import os

    parser = argparse.ArgumentParser(prog="python -m diagnose.banco_l4")
    sub = parser.add_subparsers(dest="orden", required=True)
    gr = sub.add_parser("grabar", help="graba las respuestas de las herramientas a la hora de cada incidente")
    gr.add_argument("casos", nargs="*", help="por defecto, todos")
    gr.add_argument("--bus", default="http://127.0.0.1:8080")
    sv = sub.add_parser("servir", help="servidor MCP congelado de un caso (lo arranca `evaluar`)")
    sv.add_argument("caso")
    sv.add_argument("--fallos", default="")
    ev = sub.add_parser("evaluar", help="pasa el investigador por el banco")
    ev.add_argument("casos", nargs="*")
    ev.add_argument("--modelo", default=os.environ.get("DIAGNOSE_MODELO", "qwen3-30b-a3b-q4-k-m-local"))
    ev.add_argument("--razonamiento", default=None, choices=("off", "low", "medium", "high"))
    ev.add_argument("--salida", default="")
    args = parser.parse_args(argv)

    todos = cargar()
    elegidos = [c for c in todos if not getattr(args, "casos", None) or c.id in args.casos]

    if args.orden == "servir":
        logging.basicConfig(level=logging.WARNING)
        caso = next(c for c in todos if c.id == args.caso)
        servidor_congelado(caso, cargar_grabado(caso.id), Path(args.fallos) if args.fallos else None).run("stdio")
        return 0

    if args.orden == "grabar":
        import httpx

        from .almacen import Almacen

        almacen = Almacen(os.environ.get("DIAGNOSE_CLICKHOUSE_URL", "http://127.0.0.1:8123"),
                          os.environ["DIAGNOSE_CLICKHOUSE_USER"], os.environ["DIAGNOSE_CLICKHOUSE_PASSWORD"])
        registro = httpx.get(f"{args.bus}/registry", timeout=10).json()
        DIRECTORIO_L4.mkdir(parents=True, exist_ok=True)
        for caso in elegidos:
            g = grabar(caso, almacen, registro)
            ruta_de(caso.id).write_text(json.dumps(g, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            vacias = [k for k, v in g["almacen"].items() if not v]
            print(f"→ {caso.id}: {len(g['almacen'])} consultas grabadas, {len(vacias)} vacias")
        return 0

    print(evaluar(elegidos, modelo=args.modelo, razonamiento=args.razonamiento,
                  salida=Path(args.salida) if args.salida else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
