"""El almacen de diagnostico: de solo lectura, y parametrizado.

Las dos pruebas de aqui no comprueban que las consultas devuelvan lo correcto
—eso lo hacen las de recoleccion con filas reales— sino dos propiedades del
cliente que, si se rompen, no se notan hasta que es tarde.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from diagnose.almacen import Almacen, ErrorDeAlmacen, _como_parametro


def test_rechaza_cualquier_cosa_que_no_sea_una_lectura() -> None:
    """La unica forma de que esto escriba en el almacen de telemetria.

    No es defensa contra un atacante —el SQL lo escribo yo en ese modulo— sino
    contra mi mismo manana: si alguien anade una sentencia que no sea de
    lectura, falla aqui y no en produccion.
    """
    almacen = Almacen("http://no-se-usa", "u", "p")

    for sentencia in ("INSERT INTO otel.otel_traces VALUES", "ALTER TABLE x DROP COLUMN y",
                      "DROP TABLE otel.otel_traces", "TRUNCATE TABLE otel.otel_logs"):
        with pytest.raises(ErrorDeAlmacen, match="solo lectura"):
            almacen.consulta(sentencia)


def test_las_fechas_van_en_utc_y_sin_zona() -> None:
    """La columna es `DateTime64(9)` sin zona: un `+00:00` la hace fallar al parsear.

    Y convertir aqui, en un sitio, es lo que evita que una consulta pregunte
    por otra hora — el error que ya costo una lectura equivocada de un desfase
    de relojes (D-129).
    """
    from zoneinfo import ZoneInfo

    # Julio y no octubre: el 25 de octubre de 2026 es justo el dia del cambio
    # de hora en la UE, y una fecha asi hace que la prueba hable de horario de
    # verano en vez de hablar de la conversion. El primer intento de este test
    # fallo por eso — por mi expectativa, no por el codigo.
    en_madrid = datetime(2026, 7, 15, 10, 2, 47, 784727, tzinfo=ZoneInfo("Europe/Madrid"))
    en_utc = datetime(2026, 7, 15, 8, 2, 47, 784727, tzinfo=UTC)
    ingenua = datetime(2026, 7, 15, 8, 2, 47, 784727)

    assert _como_parametro(en_madrid) == "2026-07-15 08:02:47.784727"
    assert _como_parametro(en_utc) == "2026-07-15 08:02:47.784727"
    # Una fecha sin zona se manda tal cual: ya se asume UTC, que es lo que es
    # en todo el proyecto. Lo que no se hace es interpretarla como local.
    assert _como_parametro(ingenua) == "2026-07-15 08:02:47.784727"


def test_una_firma_con_comillas_no_puede_escapar_del_parametro() -> None:
    """La firma viene de un atributo de span, o sea de codigo ajeno.

    Interpolarla en el SQL seria dejar que cualquier aplicacion instrumentada
    ejecute consultas en mi ClickHouse mandando un `error.type` con comillas.
    Esto comprueba que el valor viaja como parametro y no como texto cosido al
    SQL: las comillas viajan tal cual, sin escapar, porque el valor nunca
    entra en la sentencia. Lo unico que se escapa es lo que el formato TSV de
    los parametros necesita —saltos de linea, tabuladores, barras—, y eso no
    tiene nada que ver con el SQL.
    """
    hostil = "502' UNION ALL SELECT * FROM system.users --"

    assert _como_parametro(hostil) == hostil


def test_un_salto_de_linea_en_la_firma_se_escapa() -> None:
    """ClickHouse lee los parametros HTTP como TSV escapado.

    Un salto de linea crudo corta el valor: «only 24 of 34 bytes was parsed».
    Las firmas de Veritium de antes de D-118 llevan la linea `DETAIL:` de
    Postgres dentro, y la consulta de recurrencia fallaba con 500 en todas.
    """
    firma = 'NotNullViolationError: x\nDETAIL:\tfila\\1'

    assert _como_parametro(firma) == 'NotNullViolationError: x\\nDETAIL:\\tfila\\\\1'
