"""Un reinicio ya no convierte un incidente abierto en uno nuevo.

`D-100` hizo que el canario reconcilie, asi que tras reiniciar el alert-bus el
tablero se repuebla solo. Lo que seguia perdiendose era la IDENTIDAD del
incidente: para el proceso recien arrancado era nuevo, asi que volvia a
notificar, olvidaba el acuse de recibo y tiraba el informe del agente.

Reconciliar dio la correccion; esto da la educacion (D-103).

Y hay una segunda razon que decidio el diseno: un incidente resuelto no dejaba
rastro, y sin rastro no se puede responder "¿ha abierto alguna vez un incidente
el silencio de un servicio?" — el criterio 4 del piloto, cuyo exito es un
EVENTO pasado y no un estado presente.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from alert_bus.engine import Engine
from alert_bus.sinks import MemorySink
from alert_bus.store import IncidentStore
from argus_schemas import IncidentState, Signal, SignalKind


def _senal(componente: str = "idp-ocr", signature: str = "canary-silencio") -> Signal:
    return Signal(
        kind=SignalKind.SILENCE,
        app="intelligent-document-platform",
        component=componente,
        environment="mac-dev",
        signature=signature,
        title=f"{componente} dejó de emitir telemetría",
    )


@pytest.fixture
def ruta_almacen(tmp_path):
    return tmp_path / "incidentes.sqlite"


def _motor(registry, ruta, sink=None):
    """Un alert-bus 'recien arrancado' sobre el mismo almacen."""
    return Engine(
        registry, [sink or MemorySink()],
        group_window_s=60, resolve_after_s=900,
        store=IncidentStore(ruta),
    )


# --- Lo que motivo todo -----------------------------------------------------


def test_tras_reiniciar_el_incidente_es_el_MISMO(registry, ruta_almacen) -> None:
    antes = _motor(registry, ruta_almacen)
    antes.ingest([_senal()])
    original = antes.open_incidents()[0]

    despues = _motor(registry, ruta_almacen)
    recuperado = despues.open_incidents()[0]

    assert recuperado.id == original.id, "el incidente cambio de identidad"
    assert recuperado.fingerprint == original.fingerprint
    assert recuperado.opened_at == original.opened_at, (
        "perder `opened_at` hace que un problema de horas parezca recien nacido"
    )


def test_tras_reiniciar_no_se_vuelve_a_notificar(registry, ruta_almacen) -> None:
    """El ruido que `D-100` dejo abierto explicitamente.

    `notified_at` es lo que impide avisar dos veces de lo mismo. Sin
    persistirlo, un reinicio lo pone a `None` y el primer ciclo de
    reconciliacion vuelve a avisar de algo ya avisado.
    """
    sink_antes = MemorySink()
    antes = _motor(registry, ruta_almacen, sink_antes)
    antes.ingest([_senal()])
    antes.flush_grouped()
    assert len(sink_antes.opened) == 1

    sink_despues = MemorySink()
    despues = _motor(registry, ruta_almacen, sink_despues)
    # El canario reconcilia: manda su estado otra vez.
    despues.ingest([_senal()])
    despues.flush_grouped()

    assert sink_despues.opened == [], "volvio a notificar un incidente ya avisado"


def test_el_acuse_de_recibo_sobrevive(registry, ruta_almacen) -> None:
    """Es de lo que mas importa conservar: significa que alguien ya lo mira, y
    volver a avisarle es la forma mas rapida de que deje de mirar."""
    antes = _motor(registry, ruta_almacen)
    antes.ingest([_senal()])
    antes.acknowledge(antes.open_incidents()[0].id)

    despues = _motor(registry, ruta_almacen)
    assert despues.open_incidents()[0].state is IncidentState.ACKNOWLEDGED


def test_el_informe_del_agente_sobrevive(registry, ruta_almacen) -> None:
    """Cuesta tokens y minutos; perderlo significa volver a investigar lo mismo."""
    antes = _motor(registry, ruta_almacen)
    antes.ingest([_senal()])
    huella = antes.open_incidents()[0].fingerprint
    antes.enrich(huella, root_cause="el despliegue a9f3c21 bajo el timeout")

    despues = _motor(registry, ruta_almacen)
    assert despues.open_incidents()[0].root_cause == "el despliegue a9f3c21 bajo el timeout"


# --- El camino caliente no se toca ------------------------------------------


def test_absorber_una_senal_repetida_no_escribe(registry, ruta_almacen) -> None:
    """Es el camino caliente, y lo unico que cambia se reconstruye con la
    siguiente senal. Escribir aqui seria pagar un viaje a disco por senal."""
    motor = _motor(registry, ruta_almacen)
    motor.ingest([_senal()])

    escrituras = []
    motor._store.guardar = lambda inc: escrituras.append(inc)  # type: ignore[union-attr]
    for _ in range(10):
        motor.ingest([_senal()])

    assert escrituras == [], "el camino caliente escribio en disco"


# --- El rastro que hace comprobable el criterio 4 ---------------------------


def test_un_incidente_resuelto_deja_rastro(registry, ruta_almacen) -> None:
    motor = _motor(registry, ruta_almacen)
    motor.ingest([_senal()])
    motor.open_incidents()[0].last_seen_at -= timedelta(hours=2)
    assert motor.sweep() == 1
    assert motor.open_incidents() == []

    historico = motor._store.historico(signature="canary-silencio")  # type: ignore[union-attr]
    assert len(historico) == 1
    assert historico[0]["state"] == "resolved"
    assert historico[0]["component"] == "idp-ocr"


def test_el_historico_filtra_por_firma(registry, ruta_almacen) -> None:
    motor = _motor(registry, ruta_almacen)
    motor.ingest([_senal(signature="canary-silencio")])
    motor.ingest([_senal(componente="idp-api", signature="canary-caido")])

    assert len(motor._store.historico(signature="canary-silencio")) == 1  # type: ignore[union-attr]
    assert len(motor._store.historico()) == 2  # type: ignore[union-attr]


# --- El almacen nunca puede tumbar la deteccion -----------------------------


def test_un_almacen_inservible_no_impide_detectar(registry, tmp_path) -> None:
    """Se prefiere un alert-bus que detecta y no recuerda a uno que no detecta.

    La ruta apunta a un directorio, asi que `sqlite3.connect` falla.
    """
    inservible = tmp_path / "soy-un-directorio"
    inservible.mkdir()
    almacen = IncidentStore(inservible)
    assert not almacen.disponible

    motor = Engine(registry, [MemorySink()], store=almacen)
    motor.ingest([_senal()])
    assert len(motor.open_incidents()) == 1


def test_una_fila_ilegible_no_impide_arrancar(registry, ruta_almacen) -> None:
    """El esquema de `Incident` cambia. Una fila vieja no puede dejar al
    alert-bus sin arrancar: seria cambiar «olvido» por «no detecto nada»."""
    motor = _motor(registry, ruta_almacen)
    motor.ingest([_senal()])

    almacen = IncidentStore(ruta_almacen)
    with almacen._lock:
        almacen._con.execute(
            "INSERT INTO incidentes (huella, id, estado, abierto_en, visto_en, documento) "
            "VALUES ('rota', 'x', 'open', '2026-01-01T00:00:00+00:00', "
            "        '2026-01-01T00:00:00+00:00', '{esto no es un incidente}')"
        )
        almacen._con.commit()
    almacen.cerrar()

    despues = _motor(registry, ruta_almacen)
    assert len(despues.open_incidents()) == 1, "la fila rota se llevo por delante a la buena"
