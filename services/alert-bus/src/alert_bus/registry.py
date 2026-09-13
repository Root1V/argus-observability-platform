"""Registro de aplicaciones.

Es el prerequisito silencioso de toda la fase: sin el, ni la correlacion por
topologia ni el enrutamiento de notificaciones funcionan. Una alerta sin dueno
es una alerta que nadie atiende.

Dos comportamientos que van juntos y que por separado fallan:

- **Auto-descubrimiento**: un servicio que empieza a emitir con un namespace
  desconocido aparece solo, como `provisional`, con SLO por defecto segun su
  rol. Sin esto el registro se queda vacio y la plataforma no ve nada nuevo.
- **Pero no en silencio**: genera un aviso de severidad baja. Sin esto el
  registro se llena de basura y deja de ser fuente de verdad.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from argus_schemas import Severity

log = logging.getLogger("alert_bus.registry")

# SLO por defecto segun la forma del componente, para que una aplicacion nueva
# tenga alertamiento razonable desde el primer span sin configurar nada.
DEFAULT_SLO_MS: dict[str, int] = {
    "api": 3_000,
    "worker": 60_000,
    "scheduler": 300_000,
    "cli": 0,            # sin umbral: un CLI puede durar lo que quiera
    "model-server": 30_000,
    "frontend": 5_000,
    "library": 0,
}

# Criticidad -> severidad maxima. Una aplicacion de criticidad baja nunca
# despierta a nadie, por mucho que falle.
CRITICALITY_CEILING: dict[str, Severity] = {
    "alta": Severity.PAGE,
    "media": Severity.TICKET,
    "baja": Severity.INFO,
}


@dataclass
class Component:
    id: str
    role: str = "api"
    slo_ms: int = 0
    # `planificado` = declarado pero aun sin conectar. No se le vigila el
    # silencio; si empieza a emitir, se ve igual.
    state: str = "activo"

    def __post_init__(self) -> None:
        if not self.slo_ms:
            self.slo_ms = DEFAULT_SLO_MS.get(self.role, 0)


@dataclass
class Application:
    id: str
    display_name: str = ""
    state: str = "activo"                 # activo | provisional | retirado
    criticality: str = "media"
    owner: str = ""
    components: dict[str, Component] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    channels: dict[str, list[str]] = field(default_factory=dict)
    runbook: str = ""

    # Namespaces antiguos que siguen llegando. Renombrar una aplicacion es
    # cambiar una variable de entorno en un repositorio que no controlamos, asi
    # que hay una ventana —de minutos o de semanas— en la que conviven los dos
    # valores. Sin alias, el viejo entra como `provisional`, pierde su
    # criticidad, sus canales y su runbook, y el aviso de "servicio no
    # registrado" convierte un renombrado planificado en una alerta.
    aliases: list[str] = field(default_factory=list)

    @property
    def severity_ceiling(self) -> Severity:
        return CRITICALITY_CEILING.get(self.criticality, Severity.TICKET)

    def component(self, component_id: str) -> Component:
        """Devuelve el componente, creandolo como provisional si no existe.

        Un componente nuevo dentro de una aplicacion conocida es lo mas normal
        del mundo (un worker que se anade). No merece friccion.
        """
        if component_id not in self.components:
            self.components[component_id] = Component(id=component_id)
        return self.components[component_id]


class Registry:
    """Catalogo de aplicaciones, recargable en caliente.

    Es datos, no codigo: anadir una aplicacion no reinicia nada.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._apps: dict[str, Application] = {}
        self._lock = threading.Lock()
        self._discovered: set[str] = set()
        self._alias: dict[str, str] = {}
        if path:
            self.load()

    # --- Carga ---------------------------------------------------------------

    def reload_if_changed(self) -> bool:
        """Recarga si el fichero cambio. Devuelve si hubo recarga.

        El registro es datos, no codigo: anadir una aplicacion o marcar una como
        activa no debe exigir reiniciar el servicio que detecta incidentes. Se
        compara el mtime en vez de releer, para no pagar el fichero en cada
        consulta.

        Lo descubrio el piloto: marcar `edge-ai-inference` como activo no tuvo
        ningun efecto hasta reiniciar, pese a que la documentacion prometia lo
        contrario.
        """
        if not self._path:
            return False
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            return False
        if mtime <= self._mtime:
            return False
        self.load()
        return True

    def load(self) -> None:
        if not self._path or not self._path.exists():
            log.warning("registry.missing", extra={"path": str(self._path)})
            return
        try:
            raw = yaml.safe_load(self._path.read_text(encoding="utf-8")) or []
        except yaml.YAMLError as exc:
            # Un registro mal formado no puede tumbar el alert-bus: dejaria de
            # detectar incidentes por un error de sintaxis en un YAML.
            log.error("registry.invalid", extra={"error": str(exc)})
            return

        apps: dict[str, Application] = {}
        for entry in raw:
            app = Application(
                id=entry["id"],
                display_name=entry.get("nombre_visible", entry["id"]),
                state=entry.get("estado", "activo"),
                criticality=entry.get("criticidad", "media"),
                owner=entry.get("dueño", entry.get("dueno", "")),
                depends_on=list(entry.get("depende_de", [])),
                channels=dict(entry.get("canales", {})),
                runbook=entry.get("runbook", ""),
                aliases=list(entry.get("alias", [])),
            )
            for comp in entry.get("componentes", []):
                slo = comp.get("slo", {}) or {}
                app.components[comp["id"]] = Component(
                    id=comp["id"],
                    role=comp.get("rol", "api"),
                    slo_ms=int(slo.get("p95_ms", 0)),
                    state=comp.get("estado", "activo"),
                )
            apps[app.id] = app

        # Un alias que choca con el id de otra aplicacion la dejaria en la
        # sombra sin decir nada. Se avisa y se ignora el alias: perder el
        # renombrado es mucho menos malo que perder una aplicacion entera.
        alias_a_id: dict[str, str] = {}
        for app in apps.values():
            for alias in app.aliases:
                if alias in apps:
                    log.error(
                        "registry.alias_colisiona",
                        extra={"alias": alias, "aplicacion": app.id},
                    )
                    continue
                if alias in alias_a_id:
                    log.error(
                        "registry.alias_duplicado",
                        extra={"alias": alias, "aplicaciones": [alias_a_id[alias], app.id]},
                    )
                    continue
                alias_a_id[alias] = app.id

        with self._lock:
            # Los descubrimientos provisionales sobreviven a la recarga: si no,
            # cada edicion del fichero volveria a avisar de "servicio no
            # registrado", que es ruido por construccion.
            for app_id, provisional in self._apps.items():
                if app_id not in apps and provisional.state == "provisional":
                    apps[app_id] = provisional
            self._apps = apps
            self._alias = alias_a_id
        try:
            self._mtime = self._path.stat().st_mtime
        except OSError:
            pass
        log.info("registry.loaded", extra={"apps": len(apps)})

    # --- Consulta ------------------------------------------------------------

    def get(self, app_id: str) -> Application | None:
        with self._lock:
            app = self._apps.get(app_id)
            if app is None and (real := self._alias.get(app_id)):
                app = self._apps.get(real)
            return app

    def resolve(self, app_id: str, component_id: str) -> tuple[Application, bool]:
        """Devuelve la aplicacion y si acaba de descubrirse.

        El booleano es lo que dispara el aviso de "servicio no registrado".
        Descubrir sin avisar llena el registro de basura; avisar sin descubrir
        lo deja vacio. Hacemos las dos cosas.
        """
        with self._lock:
            app = self._apps.get(app_id)
            if app is None and (real := self._alias.get(app_id)):
                # Llega por un namespace antiguo durante un renombrado. Se
                # atiende con la identidad nueva y no se descubre nada: no es
                # un servicio desconocido, es el mismo con otro nombre.
                app = self._apps.get(real)
                if app is not None:
                    log.info(
                        "registry.alias_en_uso",
                        extra={
                            "alias": app_id,
                            "aplicacion": app.id,
                            "componente": component_id,
                        },
                    )
            if app is not None:
                app.component(component_id)
                return app, False

            app = Application(
                id=app_id,
                display_name=app_id,
                state="provisional",
                criticality="media",
            )
            app.component(component_id)
            self._apps[app_id] = app
            newly = app_id not in self._discovered
            self._discovered.add(app_id)

        if newly:
            log.warning(
                "registry.unregistered_service",
                extra={"app": app_id, "component": component_id},
            )
        return app, newly

    def dependencies_of(self, app_id: str) -> list[str]:
        app = self.get(app_id)
        return list(app.depends_on) if app else []

    def upstream_of(self, app_id: str, *, max_depth: int = 3) -> list[str]:
        """Cierre transitivo de dependencias, de mas cercana a mas lejana.

        Transitivo porque las cadenas reales lo son: la IDP depende de MinIO, y
        si MinIO depende del almacenamiento, un fallo de disco explica los dos.

        Con profundidad ACOTADA: mas alla de tres saltos, "A depende de B" deja
        de ser una explicacion util y se convierte en "todo depende de todo".

        El orden importa: al correlacionar se prefiere la causa mas CERCANA,
        porque es la mas accionable. Si Postgres esta caido, el aviso util es
        el de Postgres, no el del disco que lo aloja.
        """
        visto: set[str] = {app_id}
        resultado: list[str] = []
        frontera = [app_id]

        for _ in range(max_depth):
            siguiente: list[str] = []
            for actual in frontera:
                for dep in self.dependencies_of(actual):
                    if dep in visto:
                        continue
                    visto.add(dep)
                    resultado.append(dep)
                    siguiente.append(dep)
            if not siguiente:
                break
            frontera = siguiente

        return resultado

    def channels_for(self, app_id: str, severity: Severity) -> list[str]:
        """A donde va el aviso. Es una REGLA, no un juicio (D-014)."""
        app = self.get(app_id)
        if app is None:
            return ["console"]
        key = "page" if severity is Severity.PAGE else "ticket"
        return app.channels.get(key) or ["console"]

    @property
    def apps(self) -> dict[str, Application]:
        with self._lock:
            return dict(self._apps)

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "id": a.id,
                "estado": a.state,
                "criticidad": a.criticality,
                "componentes": sorted(a.components),
                "depende_de": a.depends_on,
                # El enrutamiento es parte del estado consultable: sin el no se
                # puede comprobar desde fuera a donde iria un aviso.
                "canales": dict(a.channels),
            }
            for a in self.apps.values()
        ]
