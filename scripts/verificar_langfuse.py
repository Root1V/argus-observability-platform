#!/usr/bin/env python3
"""Verifica el perfil GenAI: las trazas de prompts llegan a Langfuse. (F1-12)

Lo que comprueba, y por qué cada cosa:

 1. Langfuse responde y el proyecto está provisionado. Sin esto, el resto no
    puede funcionar.
 2. Un span GenAI aparece en Langfuse. Es el camino frío, rama de Langfuse.
 3. **Con el MISMO `trace_id` que en ClickHouse.** Es lo que hace útil el
    fan-out (D-006): ClickHouse tiene la traza completa, Langfuse el subárbol
    GenAI, y saltar de una a otra es seguir un identificador.
 4. El contenido del prompt aparece en Langfuse. Va en ATRIBUTOS y no en
    eventos, contra la guía canónica, porque Langfuse lee los atributos (D-008).
 5. Y **no** aparece en ClickHouse, donde sería peso muerto.

Uso:
    python scripts/verificar_langfuse.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

VERDE, ROJO, GRIS, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[0m"

LANGFUSE = os.getenv("LANGFUSE_URL", "http://127.0.0.1:3000")
PK = os.environ["LANGFUSE_PUBLIC_KEY"]
SK = os.environ["LANGFUSE_SECRET_KEY"]
CH = os.getenv("ARGUS_CH_URL", "http://127.0.0.1:8123")
CH_USER = os.getenv("CLICKHOUSE_USER", "argus")
CH_PASS = os.environ["CLICKHOUSE_PASSWORD"]

MARCA = f"langfuse-{int(time.time())}"
_fallos: list[str] = []


def check(etiqueta: str, ok: bool, detalle: str = "") -> None:
    print(f"{VERDE}OK  {OFF} {etiqueta}" if ok else f"{ROJO}FALLO{OFF} {etiqueta}")
    if detalle and not ok:
        print(f"       {detalle}")
    if not ok:
        _fallos.append(etiqueta)


def langfuse_api(ruta: str) -> dict | None:
    import base64

    auth = base64.b64encode(f"{PK}:{SK}".encode()).decode()
    peticion = urllib.request.Request(f"{LANGFUSE}{ruta}")
    peticion.add_header("Authorization", f"Basic {auth}")
    try:
        with urllib.request.urlopen(peticion, timeout=20) as r:
            return json.load(r)
    except Exception:  # noqa: BLE001
        return None


def ch(sql: str) -> str:
    peticion = urllib.request.Request(f"{CH}/", data=sql.encode(), method="POST")
    peticion.add_header("X-ClickHouse-User", CH_USER)
    peticion.add_header("X-ClickHouse-Key", CH_PASS)
    with urllib.request.urlopen(peticion, timeout=20) as r:
        return r.read().decode().strip()


def emitir() -> str:
    """Emite un span GenAI con contenido y devuelve su trace_id."""
    import argus

    handle = argus.init(
        "langfuse-probe", namespace=MARCA, role="cli", environment="ci",
        endpoint="http://127.0.0.1:4318", json_logs=False,
    )
    with argus.agent("probe-agent", conversation_id=f"conv-{MARCA}"):
        with argus.genai("chat", provider="ollama", request_model="qwen2.5-coder-7b") as g:
            g.usage(input_tokens=1234, output_tokens=567)
            g.response(model="qwen2.5-coder-7b-q4", finish_reasons=["stop"])
            g.attribution(feature="verificacion", use_case="prueba")
            g.messages(
                input=[{"role": "user", "content": f"marca {MARCA}: resume este documento"}],
                output=[{"role": "assistant", "content": "Resumen del documento."}],
            )
            trace_id = format(g.span.get_span_context().trace_id, "032x")

        with argus.tool("buscar_documentos", args={"q": MARCA}):
            pass

    handle.force_flush(timeout_millis=15_000)
    return trace_id


def main() -> int:
    os.environ["ARGUS_PROTOCOL"] = "http/protobuf"
    os.environ["ARGUS_CAPTURE_CONTENT"] = "true"
    os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization=Bearer {os.environ['ARGUS_GATEWAY_TOKEN']}"

    print(f"\n--- Perfil GenAI: Langfuse (F1-12)  (marca: {MARCA}) ---\n")

    # 1. Langfuse en pie y proyecto provisionado.
    salud = None
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"{LANGFUSE}/api/public/health", timeout=5) as r:
                salud = r.status
            break
        except Exception:  # noqa: BLE001
            time.sleep(5)
    check("Langfuse responde", salud == 200, f"estado: {salud}")
    if salud != 200:
        print(f"\n{GRIS}Arráncalo: docker compose -f platform/compose.yaml "
              f"-f platform/compose.genai.yaml --profile genai up -d{OFF}\n")
        return 1

    proyectos = langfuse_api("/api/public/projects")
    check(
        "El proyecto está provisionado (sin pasar por la UI)",
        bool(proyectos and proyectos.get("data")),
        "las claves del .env no autentican; ¿se provisionó el proyecto?",
    )
    if proyectos and proyectos.get("data"):
        print(f"{GRIS}       proyecto: {proyectos['data'][0].get('name')}{OFF}")

    # 2-5. Emitir y comprobar dónde acaba cada cosa.
    print(f"{GRIS}       emitiendo un span GenAI con contenido…{OFF}")
    trace_id = emitir()
    print(f"{GRIS}       trace_id: {trace_id}{OFF}")
    print(f"{GRIS}       esperando al tail sampling y al worker de Langfuse…{OFF}")

    traza = None
    for _ in range(36):
        time.sleep(5)
        traza = langfuse_api(f"/api/public/traces/{trace_id}")
        if traza:
            break

    check("El span GenAI llega a Langfuse", traza is not None,
          "no apareció en 3 min; mira los logs de langfuse-worker")

    if traza:
        # El mismo trace_id en los dos lados es lo que hace útil el fan-out:
        # ClickHouse tiene la traza completa, Langfuse el subárbol GenAI.
        en_clickhouse = int(ch(
            f"SELECT count() FROM otel.otel_traces WHERE TraceId = '{trace_id}'"
        ) or 0)
        check(
            "El MISMO trace_id está en ClickHouse y en Langfuse",
            en_clickhouse > 0,
            f"spans en ClickHouse con ese trace_id: {en_clickhouse}",
        )
        print(f"{GRIS}       ClickHouse: {en_clickhouse} spans · Langfuse: la rama GenAI{OFF}")

        observaciones = traza.get("observations", [])
        nombres = {o.get("name") for o in observaciones}
        check(
            "Langfuse renderiza las observaciones GenAI",
            bool(observaciones),
            f"observaciones: {sorted(nombres)}",
        )

        # El contenido: en Langfuse sí, en ClickHouse no.
        texto = json.dumps(traza)
        check(
            "El contenido del prompt SÍ está en Langfuse",
            MARCA in texto,
            "Langfuse lee el contenido de los ATRIBUTOS; si va en eventos, no lo ve (D-008)",
        )

        contenido_en_ch = int(ch(
            f"SELECT count() FROM otel.otel_traces WHERE TraceId = '{trace_id}' "
            f"AND mapContains(SpanAttributes, 'gen_ai.input.messages')"
        ) or 0)
        check(
            "Y NO está en ClickHouse, donde sería peso muerto",
            contenido_en_ch == 0,
            f"{contenido_en_ch} spans conservan el contenido en ClickHouse",
        )

    print()
    if _fallos:
        print(f"{ROJO}{len(_fallos)} comprobación(es) fallaron:{OFF}")
        for f in _fallos:
            print(f"  - {f}")
        return 1
    print(f"{VERDE}El perfil GenAI funciona: prompts en Langfuse, estructura en ClickHouse.{OFF}")
    print(f"{GRIS}  Míralo en {LANGFUSE}/project/argus-portafolio/traces/{trace_id}{OFF}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
