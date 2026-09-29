"""La junta entre el canario y el alert-bus, que es donde se perdian incidentes.

El fallo de D-100: el canario reportaba un silencio UNA vez y lo apuntaba; el
alert-bus lo guardaba en un `dict` en memoria. Al reiniciar el alert-bus el
tablero se vaciaba, el canario seguia viendo el problema y no reenviaba nada.
El problema abierto desaparecia sin haberse resuelto, y las dos mitades creian
estar en lo correcto.

Ninguna de las dos decisiones era tonta por separado. Lo que fallaba es que el
dedup del EMISOR asumia que el receptor no olvida, y el receptor olvida en cada
despliegue.

Estas pruebas son del lado del receptor: que reenviar no hace ruido, que
refresca la vida del incidente, y que un receptor recien arrancado se repuebla.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from alert_bus.engine import Engine
from argus_schemas import IncidentState, Signal, SignalKind


def _senal(componente: str = "idp-ocr") -> Signal:
    """Una aplicacion del registro de pruebas y de criticidad `alta`.

    Importa que sea `alta`: una auto-descubierta entra con criticidad media,
    cuyo techo es `ticket`, y entonces `needs_notification` es falso por el
    motivo equivocado y la prueba pasaria sin comprobar nada.
    """
    return Signal(
        kind=SignalKind.SILENCE,
        app="intelligent-document-platform",
        component=componente,
        environment="mac-dev",
        signature="canary-silencio",
        title=f"{componente} dejó de emitir telemetría",
    )


def test_reenviar_no_vuelve_a_notificar(engine: Engine, sink) -> None:
    """Es la premisa de la que depende todo lo demas.

    Si reenviar cada ciclo notificara cada ciclo, la reconciliacion seria
    exactamente la fatiga de alertas que el dedup del canario evitaba, y el
    arreglo seria peor que el fallo.
    """
    for _ in range(5):
        engine.ingest([_senal()])
    engine.flush_grouped()

    assert len(sink.opened) == 1, "reenviar produjo notificaciones de mas"


def test_reenviar_mantiene_vivo_el_incidente(engine: Engine) -> None:
    """`sweep()` cierra por `last_seen_at`, asi que el reenvio es lo unico que
    impide que un problema que SIGUE ahi se declare resuelto solo."""
    engine.ingest([_senal()])
    incidente = engine.open_incidents()[0]

    # Lo envejecemos mas alla del plazo de cierre.
    incidente.last_seen_at = incidente.last_seen_at - timedelta(hours=1)

    # Un ciclo de reconciliacion lo refresca...
    engine.ingest([_senal()])
    assert engine.sweep() == 0
    assert len(engine.open_incidents()) == 1

    # ...y sin reconciliacion se cierra, que es lo correcto.
    engine.open_incidents()[0].last_seen_at -= timedelta(hours=1)
    assert engine.sweep() == 1
    assert engine.open_incidents() == []


def test_un_receptor_recien_arrancado_se_repuebla(engine_nuevo, sink) -> None:
    """El caso que motivo todo: el alert-bus se reinicia con un problema abierto.

    Antes el canario no reenviaba y el tablero se quedaba vacio para siempre.
    Ahora el siguiente ciclo lo repuebla, sin que nadie tenga que acordarse de
    reiniciar tambien el canario — que era la mitigacion escrita en el runbook.
    """
    # Un alert-bus recien creado: su memoria esta vacia, como tras un reinicio.
    assert engine_nuevo.open_incidents() == []

    engine_nuevo.ingest([_senal()])

    abiertos = engine_nuevo.open_incidents()
    assert len(abiertos) == 1
    assert abiertos[0].state is IncidentState.OPEN
    assert abiertos[0].component == "idp-ocr"


def test_al_repoblarse_se_avisa_otra_vez(engine_nuevo, sink_nuevo) -> None:
    """Y esto es deliberado, aunque moleste.

    Tras un reinicio el incidente es NUEVO para el receptor, asi que vuelve a
    notificar. Es ruido, y es preferible al silencio: la alternativa es que
    nadie sepa que el problema sigue abierto.

    Quitarlo del todo necesita persistir los incidentes (`F2-15`) para que el
    receptor reconozca el que ya tenia. Reconciliar da la correccion;
    persistir dara la educacion.
    """
    engine_nuevo.ingest([_senal()])
    engine_nuevo.flush_grouped()
    assert len(sink_nuevo.opened) == 1


@pytest.mark.parametrize("componentes", [1, 3])
def test_se_reconcilian_varios_a_la_vez(engine: Engine, componentes: int) -> None:
    """El canario manda su estado COMPLETO, no un objetivo por peticion."""
    senales = [_senal(f"comp-{i}") for i in range(componentes)]
    for _ in range(3):
        engine.ingest(senales)
    assert len(engine.open_incidents()) == componentes
