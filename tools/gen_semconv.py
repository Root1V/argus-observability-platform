#!/usr/bin/env python3
"""Genera las constantes de convenciones semanticas por lenguaje.

La fuente de verdad es `libs/semconv-model/argus.yaml`. Este script produce
ficheros para Python y Go desde ese modelo.

Con cinco lenguajes en el portafolio, el fallo que rompe las consultas
agregadas no es que un servicio no emita, sino que dos emitan lo mismo con
nombres distintos. Generar en vez de escribir a mano lo hace imposible.

Uso:
    python tools/gen_semconv.py           # escribe los ficheros
    python tools/gen_semconv.py --check   # falla si lo generado difiere (CI)
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("Falta PyYAML. Instala con: uv pip install pyyaml")

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODEL = ROOT / "libs" / "semconv-model" / "argus.yaml"

BANNER_LINES = [
    "GENERADO AUTOMATICAMENTE. NO EDITAR A MANO.",
    "",
    "Fuente: libs/semconv-model/argus.yaml",
    "Regenerar: python tools/gen_semconv.py",
]


def const_name(attr_id: str) -> str:
    """`gen_ai.usage.input_tokens` -> `GEN_AI_USAGE_INPUT_TOKENS`."""
    return re.sub(r"[.\-]", "_", attr_id).upper()


def go_name(attr_id: str) -> str:
    """`gen_ai.usage.input_tokens` -> `GenAIUsageInputTokens`."""
    parts = re.split(r"[._\-]", attr_id)
    out = []
    for p in parts:
        if p.lower() in ("ai", "id", "usd"):
            out.append(p.upper())
        else:
            out.append(p[:1].upper() + p[1:])
    return "".join(out).replace("GenAi", "GenAI")


def load_model() -> dict:
    return yaml.safe_load(MODEL.read_text(encoding="utf-8"))


def render_python(model: dict) -> str:
    lines = ['"""', *BANNER_LINES, '"""', "", "from __future__ import annotations", "", "from typing import Final", ""]
    lines.append(f'SEMCONV_VERSION: Final[str] = "{model["version"]}"')
    lines.append(f'GENAI_SEMCONV_VERSION: Final[str] = "{model["genai_semconv_version"]}"')
    lines.append("")

    for group in model["groups"]:
        lines.append("")
        lines.append(f"# {'-' * 74}")
        lines.append(f"# {group['id']}: {group.get('brief', '')}")
        lines.append(f"# {'-' * 74}")
        for attr in group["attributes"]:
            name = const_name(attr["id"])
            lines.append(f'{name}: Final[str] = "{attr["id"]}"')
            if attr.get("type") == "enum":
                members = attr.get("members", [])
                literal = ", ".join(f'"{m}"' for m in members)
                lines.append(f"{name}_VALUES: Final[tuple[str, ...]] = ({literal},)")

    lines.append("")
    lines.append("")
    lines.append(f"# {'-' * 74}")
    lines.append("# Metricas")
    lines.append(f"# {'-' * 74}")
    for metric in model["metrics"]:
        name = const_name(metric["name"]).replace("GEN_AI_", "M_GEN_AI_").replace("ARGUS_", "M_ARGUS_")
        lines.append(f'{name}: Final[str] = "{metric["name"]}"')
        if metric.get("buckets"):
            bucket_list = ", ".join(str(b) for b in metric["buckets"])
            lines.append(f"{name}_BUCKETS: Final[tuple[float, ...]] = ({bucket_list},)")
        lines.append(f'{name}_UNIT: Final[str] = "{metric["unit"]}"')

    lines.append("")
    return "\n".join(lines) + "\n"


def render_go(model: dict) -> str:
    lines = ["// " + b if b else "//" for b in BANNER_LINES]
    lines.extend(["", "package semconv", ""])
    lines.append("const (")
    lines.append(f'\tSemconvVersion      = "{model["version"]}"')
    lines.append(f'\tGenAISemconvVersion = "{model["genai_semconv_version"]}"')
    lines.append(")")

    for group in model["groups"]:
        lines.append("")
        lines.append(f"// {group['id']}: {group.get('brief', '')}")
        lines.append("const (")
        for attr in group["attributes"]:
            lines.append(f'\t{go_name(attr["id"])} = "{attr["id"]}"')
        lines.append(")")

    lines.append("")
    lines.append("// Metricas")
    lines.append("const (")
    for metric in model["metrics"]:
        lines.append(f'\tMetric{go_name(metric["name"])} = "{metric["name"]}"')
    lines.append(")")
    lines.append("")
    return "\n".join(lines)


TARGETS = {
    ROOT / "libs" / "argus-semconv" / "src" / "argus_semconv" / "attributes.py": render_python,
    ROOT / "libs" / "argus-semconv" / "generated" / "go" / "attributes.go": render_go,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Falla si lo generado difiere de lo commiteado")
    args = parser.parse_args()

    model = load_model()
    drift = []

    for path, renderer in TARGETS.items():
        content = renderer(model)
        if args.check:
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            if existing != content:
                drift.append(path.relative_to(ROOT))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print(f"generado  {path.relative_to(ROOT)}")

    if drift:
        print("DESINCRONIZADO. Estos ficheros no coinciden con el modelo:", file=sys.stderr)
        for p in drift:
            print(f"  - {p}", file=sys.stderr)
        print("\nEjecuta: python tools/gen_semconv.py", file=sys.stderr)
        return 1

    if args.check:
        print("OK - lo generado coincide con el modelo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
