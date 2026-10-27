"""F3-05: memoria por PARECIDO. Lo que F3-06 no ve: el mismo problema con otra clase, o en otra app.

F3-06 (D-142) busca episodios de la MISMA clase en la MISMA app, y no puede
encontrar lo que mas falta hace: el `PendingRollbackError` de `veritium-worker`
y el `NotNullViolationError` de `veritium-api` del 14/10 son la misma migracion
(VRT-57) con dos clases y dos componentes. Esto los encuentra por parecido del
texto del incidente.

Tres decisiones:

- **SQLite y fuerza bruta, no pgvector.** El historico son ~850 incidentes al
  mes. Comparar contra miles de vectores cuesta milisegundos en Python puro, y
  un Postgres nuevo serian un servicio, unas credenciales y unas migraciones mas
  para un problema que no existe a esta escala. Si algun dia existe, la interfaz
  (`indexar`, `parecidos`) no cambia.
- **El embebedor es intercambiable, y el de hoy es LEXICO.** Prometheus no sirve
  ningun modelo de embeddings a Argus —su componente para eso, `tei`, nunca ha
  emitido nada— y un modelo local romperia la regla de que la unica puerta a la
  inferencia es Axonium. El lexico (bolsa de palabras con hashing, separando
  `NotNullViolationError` en sus palabras) es la LINEA BASE: el modelo que llegue
  tiene que ganarle en la misma medida. Cada vector guarda el nombre de su
  embebedor, y cambiarlo reindexa.
- **Se indexa el PROBLEMA, no el diagnostico.** App, componente, tipo, firma y
  titulo. Nunca `root_cause`: buena parte de lo guardado lo escribio L3, y algo
  con el defecto de D-145. Indexar diagnosticos haria que un error se pareciera a
  los diagnosticos equivocados de otro.
"""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import unicodedata
from array import array
from collections import Counter
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any, Protocol

DIMENSIONES_LEXICO = 1024
MAX_TEXTO = 1000


class Embebedor(Protocol):
    nombre: str

    def embeber(self, textos: list[str]) -> list[list[float]]: ...


# --- El texto del problema ----------------------------------------------------

# Cambia cuando cambia la receta del texto. Va en el nombre del indice: un
# vector hecho con otra receta no es comparable, y cambiarla reindexa.
RECETA = "t2"


def texto_del_incidente(d: dict[str, Any], arbol: Any | None = None) -> str:
    """El PROBLEMA, sin donde ocurrio, sin el diagnostico y sin datos de nadie.

    Sin nombres de app ni de componente: con ellos, el `503` del gateway se
    parecia 1,00 a cualquier otro error del gateway —un 409, un 422— y 0,99 a
    todos sus `500`. Lo que se busca es el mismo problema EN OTRO SITIO.

    Con la cadena del fallo de la traza, si la hay: el `503` del 26/10 solo se
    distingue de otro `503` por el `ConnectError` contra `:8087` que lleva
    dentro (D-145).
    """
    from urllib.parse import urlparse

    from .banco import sanear

    nombres = [str(n) for n in (d.get("component"), d.get("app")) if n]
    partes = [str(d.get("kind") or ""), str(d.get("signature") or ""), str(d.get("title") or "")]
    anotaciones = ((d.get("attributes") or {}).get("annotations") or {})
    if isinstance(anotaciones, dict):
        partes += [str(anotaciones.get(k) or "") for k in ("summary", "description")]
    if arbol is not None:
        vistos: set[str] = set()
        for span in list(arbol.cadena_del_fallo)[-3:]:
            destino = ""
            if span.ruta:
                u = urlparse(span.ruta) if "://" in span.ruta else None
                destino = f"{u.port or ''}{u.path}" if u else span.ruta
            linea = " ".join(x for x in (span.clase, span.excepcion, span.mensaje, destino) if x)
            if linea and linea not in vistos:
                vistos.add(linea)
                partes.append(linea)
    texto = " · ".join(p for p in partes if p.strip())
    for n in sorted(nombres, key=len, reverse=True):
        texto = texto.replace(n, "")
    return str(sanear(re.sub(r"\s+", " ", texto)))[:MAX_TEXTO]


def clase_de(firma: str) -> str:
    from .juicio import _clase_de, _ultimo_segmento

    return _ultimo_segmento(_clase_de(firma or ""))


# --- El embebedor lexico -------------------------------------------------------

_PALABRA = re.compile(r"[a-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
# Palabras que estan en casi todos los incidentes y no distinguen ninguno.
_VACIAS = {"de", "la", "el", "en", "y", "a", "los", "las", "del", "error", "the", "of", "in", "to", "is"}


def palabras(texto: str) -> list[str]:
    """`NotNullViolationError: null value` → not null violation error null value. Sin tildes."""
    texto = _CAMEL.sub(" ", texto)
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c)).lower()
    return [p for p in _PALABRA.findall(texto) if len(p) > 1 and p not in _VACIAS and not p.isdigit()]


def _cubeta(token: str, dimensiones: int) -> int:
    # md5 y no `hash()`: el de Python cambia entre procesos, y el indice vive en disco.
    return int.from_bytes(hashlib.md5(token.encode()).digest()[:4], "big") % dimensiones


class EmbebedorLexico:
    """Bolsa de palabras y de pares de palabras, con hashing. Determinista y sin modelo."""

    def __init__(self, dimensiones: int = DIMENSIONES_LEXICO) -> None:
        self.dimensiones = dimensiones
        self.nombre = f"lexico-{dimensiones}"

    def embeber(self, textos: list[str]) -> list[list[float]]:
        return [self._uno(t) for t in textos]

    def _uno(self, texto: str) -> list[float]:
        ps = palabras(texto)
        tokens = ps + [f"{a}_{b}" for a, b in pairwise(ps)]
        v = [0.0] * self.dimensiones
        for token, n in Counter(tokens).items():
            v[_cubeta(token, self.dimensiones)] += 1.0 + math.log(n)
        return _unitario(v)


class EmbebedorAxonium:
    """Un modelo de embeddings servido por Prometheus. Hoy no hay ninguno para Argus."""

    def __init__(self, modelo: str, *, reintentos: int = 6) -> None:
        self.modelo = modelo
        self.nombre = f"axonium:{modelo}"
        # Prometheus limita los embeddings de Argus a 40.000 tokens por minuto
        # y dice cuando se libera («Reset in 16 seconds»). Indexar el historico
        # entero son ~130.000: se espera lo que dice y se sigue.
        self.reintentos = reintentos

    def embeber(self, textos: list[str]) -> list[list[float]]:
        import asyncio

        from axonium import AsyncAxonium

        async def pedir() -> list[list[float]]:
            cliente = AsyncAxonium()
            try:
                for intento in range(self.reintentos + 1):
                    try:
                        r = await cliente.embeddings.create(model=self.modelo, input=textos)
                        return [_unitario(list(d.embedding)) for d in r.data]
                    except Exception as exc:
                        espera = _espera_del_limite(str(exc))
                        if espera is None or intento == self.reintentos:
                            raise
                        await asyncio.sleep(espera)
                raise RuntimeError("inalcanzable")
            finally:
                await cliente.aclose()

        return asyncio.run(pedir())


def _espera_del_limite(mensaje: str) -> float | None:
    """Los segundos que pide el limite de ritmo de Prometheus, o `None` si el error es otro."""
    if "rate limit" not in mensaje.lower():
        return None
    m = re.search(r"reset in (\d+(?:\.\d+)?) seconds?", mensaje, re.IGNORECASE)
    return (float(m.group(1)) if m else 30.0) + 1.0


def _unitario(v: list[float]) -> list[float]:
    norma = math.sqrt(sum(x * x for x in v))
    return [x / norma for x in v] if norma else v


# --- El almacen ----------------------------------------------------------------


@dataclass
class Parecido:
    """Un EPISODIO parecido: componente, clase y dia, como en F3-06, con su similitud."""

    app: str
    componente: str
    clase: str
    dia: str
    incidentes: int
    similitud: float
    ejemplo: str

    def resumen(self) -> str:
        return (f"{self.dia} · {self.app}/{self.componente} · {self.incidentes} incidente(s) de {self.clase}"
                f" · parecido {self.similitud:.2f}: {self.ejemplo[:140]}")


class MemoriaVectorial:
    def __init__(self, ruta: str | Path, embebedor: Embebedor) -> None:
        self.embebedor = embebedor
        self._db = sqlite3.connect(str(ruta), check_same_thread=False)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS vectores (
                id TEXT NOT NULL, modelo TEXT NOT NULL, app TEXT, componente TEXT, clase TEXT,
                abierto TEXT, texto TEXT, vector BLOB NOT NULL, PRIMARY KEY (id, modelo))"""
        )
        self._db.commit()
        self._cache: list[tuple[str, str, str, str, str, str, array]] | None = None

    @property
    def indice(self) -> str:
        return f"{self.embebedor.nombre}|{RECETA}"

    def indexar(
        self,
        incidentes: list[dict[str, Any]],
        *,
        arbol_de: Any | None = None,
        lote: int = 32,
        maximo: int | None = None,
    ) -> int:
        """Indexa los que faltan para este embebedor y esta receta. Arranca vacio y se llena solo.

        `arbol_de(incidente)` da el arbol de su traza de ejemplo, o `None`.
        """
        modelo = self.indice
        ya = {fila[0] for fila in self._db.execute("SELECT id FROM vectores WHERE modelo = ?", (modelo,))}
        nuevos = [d for d in incidentes if d.get("id") and str(d["id"]) not in ya]
        # Los mas recientes primero: si hay tope, lo que antes sirve es lo nuevo.
        nuevos.sort(key=lambda d: str(d.get("opened_at") or ""), reverse=True)
        nuevos = nuevos[:maximo] if maximo is not None else nuevos
        for i in range(0, len(nuevos), lote):
            parte = nuevos[i : i + lote]
            textos = [texto_del_incidente(d, arbol_de(d) if arbol_de else None) for d in parte]
            vectores = self.embebedor.embeber(textos)
            self._db.executemany(
                "INSERT OR REPLACE INTO vectores VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (str(d["id"]), modelo, str(d.get("app") or ""), str(d.get("component") or ""),
                     clase_de(str(d.get("signature") or "")), str(d.get("opened_at") or ""), t,
                     array("f", v).tobytes())
                    for d, t, v in zip(parte, textos, vectores, strict=True)
                ],
            )
        if nuevos:
            self._db.commit()
            self._cache = None
        return len(nuevos)

    def _todos(self) -> list[tuple[str, str, str, str, str, str, array]]:
        if self._cache is None:
            filas = self._db.execute(
                "SELECT id, app, componente, clase, abierto, texto, vector FROM vectores WHERE modelo = ?",
                (self.indice,),
            )
            self._cache = [(i, a, c, k, ab, t, _vector(v)) for i, a, c, k, ab, t, v in filas]
        return self._cache

    def parecidos(
        self,
        incidente: dict[str, Any],
        *,
        arbol: Any | None = None,
        k: int = 3,
        minimo: float = 0.25,
    ) -> list[Parecido]:
        """Los k EPISODIOS mas parecidos, abiertos ANTES que este, de OTRA clase o de otra app.

        Lo de la misma clase en la misma app ya lo da F3-06; repetirlo aqui
        llenaria la lista con lo que ya se sabe. `minimo` corta el ruido: por
        debajo, «parecido» solo significa que los dos son incidentes.
        """
        consulta = self.embebedor.embeber([texto_del_incidente(incidente, arbol)])[0]
        propia = (str(incidente.get("app") or ""), clase_de(str(incidente.get("signature") or "")))
        abierto = str(incidente.get("opened_at") or "")
        episodios: dict[tuple[str, str, str, str], Parecido] = {}
        for ident, app, comp, clase, ab, texto, vector in self._todos():
            if ident == str(incidente.get("id")) or (abierto and ab >= abierto) or (app, clase) == propia:
                continue
            s = sum(a * b for a, b in zip(consulta, vector, strict=False))
            if s < minimo:
                continue
            clave = (app, comp, clase, ab[:10])
            previo = episodios.get(clave)
            if previo is None:
                episodios[clave] = Parecido(app, comp, clase, ab[:10], 1, s, texto)
            else:
                previo.incidentes += 1
                if s > previo.similitud:
                    previo.similitud, previo.ejemplo = s, texto
        return sorted(episodios.values(), key=lambda p: -p.similitud)[:k]

    def cerrar(self) -> None:
        self._db.close()


def _vector(blob: bytes) -> array:
    v = array("f")
    v.frombytes(blob)
    return v


# --- La medida -------------------------------------------------------------------


def _casa(p: Parecido, patron: dict[str, str]) -> bool:
    return all(getattr(p, k) == v for k, v in patron.items())


def medir(memoria: MemoriaVectorial, consultas: list[dict[str, Any]], casos: dict[str, Any],
          arbol_de: Any | None = None, k: int = 3) -> list[dict[str, Any]]:
    """Para cada consulta: ¿esta lo esperado en el top-k? ¿se colo un negativo?"""
    filas = []
    for q in consultas:
        caso = casos[q["caso"]]
        arbol = caso.expediente.arbol if caso.expediente.arbol.spans else (arbol_de(caso.incidente) if arbol_de else None)
        top = memoria.parecidos(caso.incidente, arbol=arbol, k=k, minimo=0.0)
        filas.append({
            "caso": q["caso"],
            "acierta": any(_casa(p, e) for p in top for e in q["esperado"]),
            "negativos": [p.resumen()[:120] for p in top for n in q.get("negativo", []) if _casa(p, n)],
            "top": [f"{p.similitud:.2f} {p.dia} {p.app}/{p.componente} {p.clase}" for p in top],
        })
    return filas


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import os

    import httpx

    from .almacen import Almacen
    from .banco import DIRECTORIO, cargar
    from .expediente import arbol_desde_filas

    parser = argparse.ArgumentParser(prog="python -m diagnose.memoria_vectorial")
    parser.add_argument("orden", choices=("medir",))
    parser.add_argument("--embebedor", default="lexico", help="`lexico` o el id de un modelo de embeddings de Axonium")
    parser.add_argument("--indice", default="memoria.sqlite")
    parser.add_argument("--bus", default="http://127.0.0.1:8080")
    args = parser.parse_args(argv)

    embebedor: Embebedor = EmbebedorLexico() if args.embebedor == "lexico" else EmbebedorAxonium(args.embebedor)
    almacen = Almacen(os.environ.get("DIAGNOSE_CLICKHOUSE_URL", "http://127.0.0.1:8123"),
                      os.environ["DIAGNOSE_CLICKHOUSE_USER"], os.environ["DIAGNOSE_CLICKHOUSE_PASSWORD"])

    def arbol_de(d: dict[str, Any]) -> Any | None:
        trazas = list(d.get("trace_ids") or [])
        if not trazas:
            return None
        try:
            arbol = arbol_desde_filas(almacen.spans_de_traza(trazas[0]))
        except Exception:  # noqa: BLE001 - una traza que no esta no impide indexar el incidente
            return None
        return arbol if arbol.spans else None

    memoria = MemoriaVectorial(args.indice, embebedor)
    historico = httpx.get(f"{args.bus}/incidents/history?dias=30", timeout=30).json()
    print(f"indexados {memoria.indexar(historico, arbol_de=arbol_de)} nuevos con {memoria.indice}")
    pares = json.loads((DIRECTORIO / "memoria" / "pares.json").read_text(encoding="utf-8"))
    casos = {c.id: c for c in cargar()}
    filas = medir(memoria, pares["consultas"], casos, arbol_de)
    for f in filas:
        marca = "✓" if f["acierta"] else "✗"
        print(f"{marca} {f['caso']}" + (f"  NEGATIVO EN EL TOP: {len(f['negativos'])}" if f["negativos"] else ""))
        for t in f["top"]:
            print(f"     {t}")
    print(f"\n{sum(f['acierta'] for f in filas)}/{len(filas)} con lo esperado en el top-3 · "
          f"{sum(1 for f in filas if f['negativos'])} con un negativo en el top-3")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
