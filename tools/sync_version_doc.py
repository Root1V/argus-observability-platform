"""Sube la version de la guia del piloto. Lo llama release.sh."""
import pathlib
import re
import sys

version = sys.argv[1]
p = pathlib.Path("docs/piloto.md")
if p.is_file():
    t = p.read_text(encoding="utf-8")
    nuevo = re.sub(r"(argus-obs-sdk\[[^\]]*\]==)\d+\.\d+\.\d+[a-z]*\d*", rf"\g<1>{version}", t)
    if nuevo != t:
        p.write_text(nuevo, encoding="utf-8")
        print(f"  {'docs/piloto.md':<24} {version}")
