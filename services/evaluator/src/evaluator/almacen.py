"""El almacen: lee spans de `otel.otel_traces` y escribe en `argus.evaluaciones`. Nada mas.

Lee con consultas fijas y parametrizadas, como `diagnose.almacen` (D-131): los
valores vienen de atributos que emite codigo ajeno. Escribe en UNA tabla, con
UNA sentencia fija; no hay un «ejecuta este SQL» que alguien pueda reutilizar
manana para escribir en la telemetria.

**La tabla es idempotente.** `ReplacingMergeTree` con la clave
(span, evaluacion, version): volver a evaluar una ventana —un reinicio, un
relleno a mano— reemplaza las filas en vez de duplicarlas.

**Por el FIN del span, no por el inicio.** Un span se inserta cuando termina,
y una generacion del 27B tarda nueve minutos: con un cursor sobre el inicio,
la ventana ya habria pasado cuando el span llega, y las llamadas lentas —las
que mas interesan— serian justo las que nunca se evaluan.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from .evals import Evaluacion, Span

log = logging.getLogger("evaluator.almacen")

# Un span que empezo hace mas de esto y termina ahora no se encuentra: es la
# poda por la clave primaria (Timestamp), sin la que cada consulta lee la tabla
# entera. El presupuesto mas largo de la plataforma es el de L4, 15 minutos.
DURACION_MAXIMA_H = 2
LIMITE_SPANS = 20_000

TABLA = "argus.evaluaciones"
DDL = (
    "CREATE DATABASE IF NOT EXISTS argus",
    f"""CREATE TABLE IF NOT EXISTS {TABLA} (
    evaluado_en DateTime64(3),
    span_fin DateTime64(9),
    trace_id String,
    span_id String,
    app LowCardinality(String),
    componente LowCardinality(String),
    operacion LowCardinality(String),
    modelo LowCardinality(String),
    eval LowCardinality(String),
    version LowCardinality(String),
    resultado LowCardinality(String),
    motivo String,
    muestra LowCardinality(String)
) ENGINE = ReplacingMergeTree(evaluado_en)
PARTITION BY toYYYYMM(span_fin)
ORDER BY (app, componente, eval, trace_id, span_id, version)
TTL toDateTime(span_fin) + INTERVAL 90 DAY""",
)

SPANS_TERMINADOS = f"""
SELECT
    toString(Timestamp + toIntervalNanosecond(Duration)) AS fin,
    TraceId AS trace_id,
    SpanId AS span_id,
    ResourceAttributes['service.namespace'] AS app,
    ServiceName AS componente,
    SpanAttributes['gen_ai.operation.name'] AS operacion,
    SpanAttributes['gen_ai.response.model'] AS modelo,
    SpanAttributes['gen_ai.request.model'] AS modelo_pedido,
    SpanAttributes['gen_ai.response.finish_reasons'] AS finish_reasons,
    SpanAttributes['gen_ai.usage.output_tokens'] AS tokens_salida,
    SpanAttributes['gen_ai.request.max_tokens'] AS max_tokens,
    StatusCode = 'Error' AS error,
    Duration AS duracion_ns
FROM otel.otel_traces
WHERE SpanAttributes['gen_ai.operation.name'] != ''
  AND Timestamp >= {{desde:DateTime64(9)}} - INTERVAL {DURACION_MAXIMA_H} HOUR
  AND Timestamp <= {{hasta:DateTime64(9)}}
  AND Timestamp + toIntervalNanosecond(Duration) > {{desde:DateTime64(9)}}
  AND Timestamp + toIntervalNanosecond(Duration) <= {{hasta:DateTime64(9)}}
ORDER BY fin
LIMIT {LIMITE_SPANS}
"""

P99_POR_MODELO = """
SELECT
    ServiceName AS componente,
    SpanAttributes['gen_ai.operation.name'] AS operacion,
    SpanAttributes['gen_ai.request.model'] AS modelo,
    quantile(0.99)(Duration) AS p99,
    count() AS n
FROM otel.otel_traces
WHERE SpanAttributes['gen_ai.operation.name'] != ''
  AND Timestamp > {hasta:DateTime64(9)} - INTERVAL 7 DAY
  AND Timestamp <= {hasta:DateTime64(9)}
GROUP BY componente, operacion, modelo
"""


class ErrorDeAlmacen(RuntimeError):
    pass


class Almacen:
    def __init__(self, url: str, usuario: str, contrasena: str, *, timeout_s: float = 30.0) -> None:
        self.url = url.rstrip("/")
        self._cliente = httpx.Client(timeout=timeout_s, auth=(usuario, contrasena) if usuario else None)

    def cerrar(self) -> None:
        self._cliente.close()

    def _post(self, sql: str, params: dict[str, str] | None = None, cuerpo: bytes | None = None) -> str:
        consulta = dict(params or {})
        if cuerpo is None:
            cuerpo = sql.encode("utf-8")
        else:
            consulta["query"] = sql
        try:
            r = self._cliente.post(self.url, params=consulta, content=cuerpo)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            detalle = getattr(getattr(exc, "response", None), "text", "") or str(exc)
            raise ErrorDeAlmacen(" ".join(detalle.split())[:300]) from exc
        return r.text

    def consulta(self, sql: str, **parametros: Any) -> list[dict[str, Any]]:
        if not sql.lstrip().upper().startswith(("SELECT", "WITH")):
            raise ErrorDeAlmacen("consulta() solo lee")
        params = {"default_format": "JSONEachRow"}
        params |= {f"param_{k}": _como_parametro(v) for k, v in parametros.items()}
        return [json.loads(linea) for linea in self._post(sql, params).splitlines() if linea.strip()]

    # --- Lectura -------------------------------------------------------------

    def spans_terminados(self, desde: datetime, hasta: datetime) -> list[Span]:
        filas = self.consulta(SPANS_TERMINADOS, desde=desde, hasta=hasta)
        if len(filas) >= LIMITE_SPANS:
            log.warning("evaluator.ventana_truncada", extra={"desde": str(desde), "hasta": str(hasta)})
        return [
            Span(
                trace_id=f["trace_id"], span_id=f["span_id"], fin=f["fin"], app=f["app"],
                componente=f["componente"], operacion=f["operacion"], modelo=f["modelo"],
                modelo_pedido=f["modelo_pedido"], finish_reasons=f["finish_reasons"],
                tokens_salida=f["tokens_salida"], max_tokens=f["max_tokens"],
                error=bool(int(f["error"])), duracion_ns=int(f["duracion_ns"]),
            )
            for f in filas
        ]

    def p99_por_modelo(self, hasta: datetime) -> list[dict[str, Any]]:
        return self.consulta(P99_POR_MODELO, hasta=hasta)

    # --- Escritura: solo esto ------------------------------------------------

    def preparar(self) -> None:
        for sentencia in DDL:
            self._post(sentencia)

    def guardar(self, filas: list[tuple[Span, Evaluacion, str]], evaluado_en: datetime) -> None:
        if not filas:
            return
        lineas = [
            json.dumps({
                "evaluado_en": _como_parametro(evaluado_en)[:23],
                "span_fin": s.fin, "trace_id": s.trace_id, "span_id": s.span_id, "app": s.app,
                "componente": s.componente, "operacion": s.operacion, "modelo": s.modelo or s.modelo_pedido,
                "eval": e.eval, "version": e.version, "resultado": str(e.resultado), "motivo": e.motivo,
                "muestra": muestra,
            }, ensure_ascii=False)
            for s, e, muestra in filas
        ]
        self._post(f"INSERT INTO {TABLA} FORMAT JSONEachRow", cuerpo="\n".join(lineas).encode("utf-8"))


def _como_parametro(valor: Any) -> str:
    """Fechas en UTC y sin zona: la columna es `DateTime64(9)` sin zona (D-129)."""
    if isinstance(valor, datetime):
        if valor.tzinfo is not None:
            valor = valor.astimezone(UTC).replace(tzinfo=None)
        return valor.strftime("%Y-%m-%d %H:%M:%S.%f")
    return str(valor).replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")
