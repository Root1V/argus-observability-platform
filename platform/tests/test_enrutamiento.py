"""Toda aplicacion vigilada tiene que avisar a alguien.

`channels_for` cae a `["console"]` cuando una aplicacion no declara canales, y
la consola es el log del propio alert-bus: nadie la mira. Una alerta que se
detecta, se normaliza, se correlaciona y acaba en un fichero de log es una
alerta que no existe.

No es hipotetico. Al revisar la integracion de Prosodia (S-06) salio que su
`page` y su `ticket` iban solo a consola, y con ella seis servicios de
infraestructura marcados `activo` — incluida ClickHouse, declarada `alta` y con
sonda HTTP: su caida deja a la plataforma sin almacen y no avisaba a nadie
(D-094).

Es el modo de fallo de D-089 con otra causa. Alli las etiquetas impedian
resolver la identidad; aqui la identidad resuelve bien y no hay destino. En los
dos casos la deteccion funcionaba y la entrega no, y en los dos se vio mirando
a donde llegaba el aviso, no si se generaba.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REGISTRO = Path(__file__).resolve().parents[1] / "registry" / "apps.yaml"

# Lo que `channels_for` devuelve cuando no hay nada declarado. No es un canal
# humano: es el log del proceso.
SOLO_CONSOLA = ["console"]

# Criticidad -> severidad maxima, igual que `CRITICALITY_CEILING` en el
# registro del alert-bus. Duplicado a proposito: si alguien cambia una de las
# dos tablas, esta prueba lo dice en vez de que las dos discrepen en silencio.
TECHO = {"alta": "page", "media": "ticket", "baja": "info"}


def _apps() -> list[dict]:
    return yaml.safe_load(REGISTRO.read_text(encoding="utf-8"))


def _vigiladas() -> list[dict]:
    """Solo las `activo`: son las unicas que generan guardias.

    Una `planificado` que no avise a nadie es correcto — todavia no emite.
    """
    return [a for a in _apps() if a.get("estado") == "activo"]


def test_hay_aplicaciones_activas() -> None:
    """Si el filtro deja de encontrar nada, esta prueba pasaria vacia."""
    assert len(_vigiladas()) >= 5


@pytest.mark.parametrize("app", _vigiladas(), ids=lambda a: a["id"])
def test_cada_activa_avisa_a_alguien(app: dict) -> None:
    canales = app.get("canales") or {}
    nivel = TECHO.get(app.get("criticidad", "media"), "ticket")
    if nivel == "info":
        pytest.skip("criticidad baja: solo digest, no hay entrega que comprobar")

    destinos = canales.get(nivel) or SOLO_CONSOLA
    humanos = [c for c in destinos if c != "console"]
    assert humanos, (
        f"`{app['id']}` es `activo` con criticidad `{app.get('criticidad','media')}`, "
        f"asi que su aviso maximo es `{nivel}` — y va a {destinos}. "
        f"La consola es el log del alert-bus: no la mira nadie."
    )


@pytest.mark.parametrize("app", _vigiladas(), ids=lambda a: a["id"])
def test_el_techo_de_criticidad_tiene_canal_declarado(app: dict) -> None:
    """Declarar `page` en una app `media` no sobra: evita que subir la
    criticidad mas adelante dependa de que alguien recuerde anadir el canal."""
    canales = app.get("canales") or {}
    nivel = TECHO.get(app.get("criticidad", "media"), "ticket")
    if nivel == "info":
        pytest.skip("criticidad baja")
    assert nivel in canales, f"`{app['id']}` no declara canales para `{nivel}`"


# ---------------------------------------------------------------------------
# El latido es un eje distinto del estado, y confundirlos rompe una de las dos
# cosas: o la aplicacion no avisa a nadie, o el canario grita por algo sabido.
# ---------------------------------------------------------------------------

ROLES_SIN_LATIDO = {"cli", "library", "frontend", "infra"}


def _componentes_vigilados() -> list[tuple[str, dict]]:
    salida = []
    for app in _vigiladas():
        for c in app.get("componentes", []):
            c = {"id": c} if isinstance(c, str) else c
            if c.get("rol", "api") in ROLES_SIN_LATIDO:
                continue
            if c.get("estado", "activo") != "activo":
                continue
            salida.append((app["id"], c))
    return salida


@pytest.mark.parametrize(
    "app_id,comp", _componentes_vigilados(), ids=lambda v: v if isinstance(v, str) else v["id"]
)
def test_quien_no_late_lo_dice(app_id: str, comp: dict) -> None:
    """`latido` solo admite booleano.

    Un `latido: "false"` en YAML es la cadena `"false"`, que es verdadera, y la
    sonda se encenderia igual sin que nadie lo note. El fallo silencioso de un
    guardarrail es peor que no tenerlo.
    """
    if "latido" in comp:
        assert isinstance(comp["latido"], bool), (
            f"`{app_id}/{comp['id']}` tiene `latido: {comp['latido']!r}`, que no es booleano"
        )


def test_prosodia_no_tiene_sonda_de_silencio() -> None:
    """Se fija explicitamente porque es la razon de que `latido` exista.

    Prosodia es `activo` para que sus avisos lleguen a una persona, y esta
    callada la mayor parte del dia porque es un pipeline que se lanza. Si
    alguien le quita el `latido: false` sin anadir la sonda de doblaje colgado
    (F2-16), la guardia empieza a recibir falsas alarmas.
    """
    prosodia = next(a for a in _apps() if a["id"] == "prosodia")
    for c in prosodia["componentes"]:
        assert c.get("latido") is False, f"{c['id']} volveria a tener sonda de silencio"
