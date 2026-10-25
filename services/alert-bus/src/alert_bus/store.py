"""Los incidentes sobreviven a un reinicio del alert-bus.

El motor guarda los incidentes abiertos en un `dict` y eso fue deliberado en la
Fase 2: sin almacen, arrancar era mucho mas simple, y la fuente de verdad de
que algo esta roto es el flujo y no este proceso.

`D-100` hizo que el canario reconcilie, asi que un reinicio ya no pierde el
incidente: el siguiente ciclo lo repuebla. Lo que sigue perdiendose es su
IDENTIDAD — para el proceso recien arrancado es un incidente nuevo, asi que
vuelve a notificar, olvida quien lo habia acusado recibo y tira el informe del
agente de investigacion.

Reconciliar dio la correccion; esto da la educacion.

**Y hay una segunda razon, que es la que decidio el diseno**: un incidente
resuelto no dejaba rastro en ningun sitio. Sin eso no se puede responder "¿ha
abierto alguna vez un incidente el silencio de un servicio?", que es el
criterio 4 del piloto — y un criterio que solo se puede comprobar a mano se
comprueba una vez (D-103).

Decisiones:

- **SQLite de la biblioteca estandar.** El alert-bus es lo que avisa cuando
  algo se rompe; darle una dependencia de red seria compartir modos de fallo
  con lo que vigila. Es el mismo argumento por el que el dead man's switch no
  importa nada.
- **No se escribe en el camino caliente.** Absorber una senal repetida solo
  refresca `last_seen_at`, y eso se puede perder: la siguiente senal lo vuelve
  a poner. Se escribe cuando cambia algo que NO se puede reconstruir — abrir,
  notificar, acusar recibo, enriquecer, resolver.
- **Un fallo del almacen no puede tumbar la deteccion.** Todo va en
  `try/except` con aviso: se prefiere un alert-bus que detecta y no recuerda a
  uno que no detecta.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from argus_schemas import Incident, IncidentState

log = logging.getLogger("alert_bus.store")

# Una fila por INCIDENTE, no por huella.
#
# Hasta D-133 la clave primaria era la huella, y el `INSERT ... ON CONFLICT
# DO UPDATE` hacia que un incidente reabierto PISARA al anterior con la misma
# huella. El almacen se llamaba «historico» y guardaba, de cada problema, solo
# su ultima vez: el `ReadTimeout` del gateway de las 02:50 del 25/10 —el de
# D-129— desaparecio cuando el mismo fallo reabrio a las 08:26, y ese fallo
# tiene trazas TODOS los dias desde hace un mes.
#
# Lo que costo no fue solo el rastro. L3 lee este historico para decir si algo
# es nuevo, y le dijo a una persona «primera vez que esta firma abre
# incidente» sobre un fallo cronico. Y B-32 conto «571 incidentes, 571 huellas
# distintas» como prueba de que la huella identifica la ocurrencia: con la
# huella como clave primaria, esas dos cifras son iguales por construccion.
ESQUEMA = """
CREATE TABLE IF NOT EXISTS incidentes (
    id          TEXT PRIMARY KEY,
    huella      TEXT NOT NULL,
    estado      TEXT NOT NULL,
    abierto_en  TEXT NOT NULL,
    visto_en    TEXT NOT NULL,
    documento   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_huella   ON incidentes(huella);
CREATE INDEX IF NOT EXISTS idx_estado   ON incidentes(estado);
CREATE INDEX IF NOT EXISTS idx_abierto  ON incidentes(abierto_en);
"""


def _migrar_si_hace_falta(con: sqlite3.Connection) -> int:
    """Pasa un almacen con la huella como clave a uno con el id como clave.

    Devuelve cuantas filas se migraron (0 si no hacia falta). Lo perdido antes
    de esto no se recupera —cada huella conservaba solo su ultima ocurrencia—
    pero lo que habia se conserva entero, y desde aqui se acumula.

    En una transaccion: un almacen a medio migrar seria peor que uno viejo,
    porque el arranque fallaria con un esquema que no es ninguno de los dos.
    """
    columnas = con.execute("PRAGMA table_info(incidentes)").fetchall()
    if not columnas:
        return 0
    clave = [c[1] for c in columnas if c[5]]  # (cid, nombre, tipo, notnull, default, pk)
    if clave != ["huella"]:
        return 0
    with con:
        con.execute("ALTER TABLE incidentes RENAME TO incidentes_por_huella")
        # Los indices viejos se renombran con la tabla pero conservan su
        # nombre, y chocarian con los nuevos.
        for indice in ("idx_estado", "idx_abierto"):
            con.execute(f"DROP INDEX IF EXISTS {indice}")
        con.executescript(ESQUEMA)
        n = con.execute(
            "INSERT INTO incidentes (id, huella, estado, abierto_en, visto_en, documento) "
            "SELECT id, huella, estado, abierto_en, visto_en, documento FROM incidentes_por_huella"
        ).rowcount
        con.execute("DROP TABLE incidentes_por_huella")
    return n


class IncidentStore:
    """Persistencia de incidentes en SQLite.

    El documento completo va como JSON en una columna y los campos por los que
    se consulta salen a columnas propias. Es a proposito: el esquema de
    `Incident` cambia —lleva cambiando toda la semana— y no quiero una
    migracion por cada campo nuevo. Lo que no puede cambiar sin darse cuenta es
    aquello por lo que se filtra.
    """

    def __init__(self, ruta: str | Path) -> None:
        self.ruta = Path(ruta).expanduser()
        self._lock = threading.Lock()
        self.disponible = False
        try:
            self.ruta.parent.mkdir(parents=True, exist_ok=True)
            # `check_same_thread=False`: el motor escribe desde el hilo de
            # ingesta y desde el del barrido. La serializacion la hace nuestro
            # propio lock, que es mas explicito que confiar en el de sqlite3.
            self._con = sqlite3.connect(str(self.ruta), check_same_thread=False)
            migradas = _migrar_si_hace_falta(self._con)
            if migradas:
                log.warning("store.migrated", extra={"filas": migradas, "a": "una fila por incidente"})
            self._con.executescript(ESQUEMA)
            self._con.commit()
            self.disponible = True
        except Exception as exc:  # noqa: BLE001
            log.error("store.unavailable", extra={"ruta": str(self.ruta), "error": str(exc)})

    # --- Escritura -----------------------------------------------------------

    def guardar(self, incidente: Incident) -> None:
        """Escribe o actualiza. Nunca levanta."""
        if not self.disponible:
            return
        try:
            with self._lock:
                self._con.execute(
                    "INSERT INTO incidentes (id, huella, estado, abierto_en, visto_en, documento) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "  estado=excluded.estado, "
                    "  visto_en=excluded.visto_en, documento=excluded.documento",
                    (
                        incidente.id,
                        incidente.fingerprint,
                        str(incidente.state),
                        incidente.opened_at.isoformat(),
                        incidente.last_seen_at.isoformat(),
                        incidente.model_dump_json(),
                    ),
                )
                self._con.commit()
        except Exception as exc:  # noqa: BLE001
            log.error("store.write_failed", extra={"huella": incidente.fingerprint, "error": str(exc)})

    # --- Lectura -------------------------------------------------------------

    def abiertos(self) -> Iterator[Incident]:
        """Los que estaban abiertos al apagarse. Lo que se carga al arrancar.

        Un documento que ya no se puede parsear se SALTA con aviso en vez de
        impedir el arranque: el esquema de `Incident` cambia, y una fila vieja
        no puede dejar al alert-bus sin arrancar — seria cambiar "olvido los
        incidentes" por "no detecto nada".
        """
        if not self.disponible:
            return
        try:
            with self._lock:
                filas = self._con.execute(
                    "SELECT documento FROM incidentes WHERE estado != ? ORDER BY abierto_en",
                    (str(IncidentState.RESOLVED),),
                ).fetchall()
        except Exception as exc:  # noqa: BLE001
            log.error("store.read_failed", extra={"error": str(exc)})
            return

        # Uno por huella, el mas reciente. Con una fila por incidente puede
        # haber dos abiertos de la misma huella si un cierre no llego a
        # escribirse —el almacen nunca levanta, asi que puede fallar en
        # silencio— y el motor solo admite uno abierto por huella. El mas
        # reciente es el que el flujo esta alimentando.
        por_huella: dict[str, Incident] = {}
        for (documento,) in filas:
            try:
                incidente = Incident.model_validate_json(documento)
            except Exception as exc:  # noqa: BLE001
                log.warning("store.skipped_row", extra={"error": str(exc)})
                continue
            por_huella[incidente.fingerprint] = incidente   # ORDER BY abierto_en: gana el ultimo
        yield from por_huella.values()

    def historico(self, *, signature: str | None = None, dias: int = 30) -> list[dict]:
        """Incidentes de los ultimos N dias, resueltos incluidos.

        Es lo que hace comprobable el criterio 4 del piloto: su exito es un
        EVENTO pasado —el silencio de un servicio abrio un incidente— y el
        estado actual no lo puede responder, porque lo deseable es que ahora
        mismo no este pasando.
        """
        if not self.disponible:
            return []
        desde = (datetime.now(UTC) - timedelta(days=dias)).isoformat()
        sql = "SELECT documento FROM incidentes WHERE abierto_en >= ?"
        parametros: list[object] = [desde]
        try:
            with self._lock:
                filas = self._con.execute(sql, parametros).fetchall()
        except Exception as exc:  # noqa: BLE001
            log.error("store.read_failed", extra={"error": str(exc)})
            return []

        salida = []
        for (documento,) in filas:
            try:
                d = json.loads(documento)
            except Exception:  # noqa: BLE001
                continue
            if signature is not None and d.get("signature") != signature:
                continue
            salida.append(d)
        salida.sort(key=lambda d: d.get("opened_at") or "")
        return salida

    def cerrar(self) -> None:
        if not self.disponible:
            return
        try:
            with self._lock:
                self._con.close()
            self.disponible = False
        except Exception:  # noqa: BLE001
            pass
