"""Las consultas FIJAS sobre el almacen de trazas. Solo lectura.

D-013: «contexto pre-recolectado por **consultas fijas**». Fijas no es una
limitacion tecnica, es la decision: un agente que escribe su propio SQL contra
el almacen de telemetria es un agente al que hay que auditar cada consulta, y
lo que se gana es flexibilidad para un trabajo que no la necesita. Las seis
preguntas de un diagnostico se saben de antemano.

**Los parametros van parametrizados, y eso aqui no es higiene: es el modelo de
amenaza.** La firma de un incidente viene de un atributo de span, o sea de
codigo ajeno que emite telemetria hacia mi. Interpolarla en el SQL seria dejar
que cualquier aplicacion instrumentada ejecute consultas en mi ClickHouse
mandando un `error.type` con comillas. ClickHouse tiene parametros por HTTP
(`param_x` + `{x:String}`) y es lo que se usa en todas.

**Solo lectura, y con un usuario que podria ser de solo lectura.** Hoy usa las
mismas credenciales que el resto porque no hay un segundo usuario creado; lo
que no hace es ninguna sentencia que no empiece por SELECT, y eso esta
comprobado en `test_solo_lectura`.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

import httpx

log = logging.getLogger("diagnose.almacen")

# Tope de filas por consulta. Un incidente grande no puede traer un arbol de
# diez mil spans: el expediente tiene que caber en una ventana de contexto y en
# la cabeza de quien lo lea.
LIMITE_SPANS = 200
LIMITE_FILAS = 50


class ErrorDeAlmacen(RuntimeError):
    """No se pudo consultar. Se convierte en una laguna del expediente, no en un fallo."""


class Almacen:
    """Cliente de lectura sobre `otel_traces`.

    No se traza a si mismo con el SDK a proposito: L3 lee la telemetria de la
    plataforma, y si sus propias consultas generaran spans en la misma tabla,
    un diagnostico sobre «errores en los ultimos 15 minutos» acabaria
    midiendose a si mismo. El bucle si se traza; las consultas no.
    """

    def __init__(
        self,
        url: str,
        usuario: str,
        contrasena: str,
        *,
        base_de_datos: str = "otel",
        timeout_s: float = 10.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.base = base_de_datos
        self._cliente = httpx.Client(
            timeout=timeout_s,
            auth=(usuario, contrasena) if usuario else None,
        )

    def cerrar(self) -> None:
        self._cliente.close()

    # --- Motor ---------------------------------------------------------------

    def consulta(self, sql: str, **parametros: Any) -> list[dict[str, Any]]:
        """Ejecuta un SELECT parametrizado y devuelve filas como diccionarios.

        Rechaza cualquier cosa que no sea un SELECT. No es defensa contra un
        atacante —el SQL lo escribo yo en este modulo— sino contra mi mismo
        manana: la unica forma de que este cliente escriba en el almacen de
        telemetria es que alguien anada aqui una sentencia que no sea de
        lectura, y entonces falla en la primera prueba.
        """
        if not sql.lstrip().upper().startswith(("SELECT", "WITH")):
            raise ErrorDeAlmacen("el almacen de diagnostico es de solo lectura")

        # El SQL va en el CUERPO y los parametros en la URL. Mandarlos juntos
        # como formulario —`data={"query": ..., "param_x": ...}`— es lo que
        # hacia esta funcion al escribirla, y ClickHouse recibe entonces
        # `query=SELECT...&param_x=...` como la sentencia entera: falla con un
        # 400 en la primera consulta y en todas.
        #
        # No lo cazo ninguna prueba porque las pruebas de este servicio usan un
        # almacen falso, que es lo correcto —no quiero una suite que necesite
        # ClickHouse— y justo por eso el ensayo contra el stack real no es
        # opcional. Las seis consultas devolvian 400, las seis lagunas se
        # anotaron, y el expediente salio educadamente vacio: la degradacion
        # funciono tan bien que sin mirar el ensayo habria parecido que
        # funcionaba todo (D-131).
        consulta = {"default_format": "JSONEachRow"}
        for clave, valor in parametros.items():
            consulta[f"param_{clave}"] = _como_parametro(valor)

        try:
            respuesta = self._cliente.post(self.url, params=consulta, content=sql.encode("utf-8"))
            respuesta.raise_for_status()
        except httpx.HTTPError as exc:
            raise ErrorDeAlmacen(_una_linea(str(exc))) from exc

        filas = []
        for linea in respuesta.text.splitlines():
            if linea.strip():
                filas.append(json.loads(linea))
        return filas

    # --- Las consultas fijas -------------------------------------------------

    def spans_de_traza(self, trace_id: str) -> list[dict[str, Any]]:
        """Todos los spans de una traza, para montar el arbol del ejemplar.

        Se filtra ademas por la tabla de indice `otel_traces_trace_id_ts`, que
        es la que el exportador mantiene para esto: sin ella, buscar por
        `TraceId` es un recorrido de la particion entera.
        """
        return self.consulta(
            f"""
            SELECT TraceId, SpanId, ParentSpanId, SpanName, SpanKind, ServiceName,
                   Duration, StatusCode, StatusMessage, toString(Timestamp) AS Timestamp,
                   SpanAttributes,
                   `Events.Name` AS `Events.Name`, `Events.Attributes` AS `Events.Attributes`
            FROM {self.base}.otel_traces
            WHERE TraceId = {{traza:String}}
            ORDER BY Timestamp
            LIMIT {LIMITE_SPANS}
            """,
            traza=trace_id,
        )

    def alcance(
        self,
        *,
        app: str,
        componente: str,
        desde: datetime,
        hasta: datetime,
    ) -> list[dict[str, Any]]:
        """Peticiones atendidas y fallidas por ruta, en la ventana.

        Cuenta solo spans `Server`: son las peticiones que el servicio ATIENDE.
        Contar tambien los `Client` mezclaria «me han pedido 400 cosas» con «yo
        he pedido 1.200», y la fraccion dejaria de significar nada.
        """
        return self.consulta(
            f"""
            SELECT
                SpanAttributes['http.route'] AS ruta,
                countIf(StatusCode = 'Error') AS fallidas,
                count() AS totales
            FROM {self.base}.otel_traces
            WHERE Timestamp BETWEEN {{desde:DateTime64(9)}} AND {{hasta:DateTime64(9)}}
              AND ResourceAttributes['service.namespace'] = {{app:String}}
              AND ServiceName = {{componente:String}}
              AND SpanKind = 'Server'
            GROUP BY ruta
            ORDER BY fallidas DESC, totales DESC
            LIMIT {LIMITE_FILAS}
            """,
            desde=desde,
            hasta=hasta,
            app=app,
            componente=componente,
        )

    def concurrentes(
        self,
        *,
        componente: str,
        desde: datetime,
        hasta: datetime,
    ) -> list[dict[str, Any]]:
        """Otros servicios con errores en la misma ventana.

        Correlacion TEMPORAL, y es distinta de la del registro: coge lo que no
        esta declarado como dependencia. El motor del alert-bus ya suprime
        sintomas por topologia, pero solo entre apps que alguien escribio en
        `apps.yaml`; esto ve lo que pasa a la vez sin pedir permiso al grafo.

        Que aparezca algo aqui NO lo convierte en causa. Es coincidencia hasta
        que la topologia o el puente digan otra cosa, y el expediente lo
        etiqueta asi.
        """
        return self.consulta(
            f"""
            SELECT
                ServiceName AS servicio,
                SpanAttributes['error.type'] AS clase,
                count() AS errores
            FROM {self.base}.otel_traces
            WHERE Timestamp BETWEEN {{desde:DateTime64(9)}} AND {{hasta:DateTime64(9)}}
              AND StatusCode = 'Error'
              AND ServiceName != {{componente:String}}
            GROUP BY servicio, clase
            ORDER BY errores DESC
            LIMIT {LIMITE_FILAS}
            """,
            desde=desde,
            hasta=hasta,
            componente=componente,
        )

    def contraparte(
        self,
        *,
        ruta: str,
        momento: datetime,
        margen_s: float = 3.0,
    ) -> list[dict[str, Any]]:
        """Quien ATENDIO la llamada saliente que fallo. El puente.

        Hace a mano el salto que la traza deberia dar sola. Y tiene que hacerlo
        a mano porque **en este stack ninguna traza cruza un servicio**: medido
        sobre 132.000 trazas de dos dias, solo 3 tienen spans de mas de un
        servicio, y los 2.369 spans `Server` de `auth-service` son todos raiz —
        o sea, nunca adoptan el `traceparent` entrante. El `ASGIMiddleware`
        llega con `propagate_mode="never"` por defecto, que es el correcto para
        lo expuesto a internet y el equivocado para dos servicios que se hablan
        por loopback.

        Mientras eso siga asi, el arbol del ejemplar se corta en el borde del
        servicio y esta consulta es la unica forma de ver el otro lado: la RUTA
        de la llamada, ventana de +-3 s, lado servidor.

        Se busca por ruta y no por nombre de span porque los dos lados la
        nombran distinto: el cliente del gateway emite un span llamado `POST` a
        secas y guarda el destino en `url.full`, mientras el servidor de
        `auth-service` lo llama `POST /oauth2/token`. Casar por nombre no
        encontraria nunca nada — y lo que es peor, no encontrar nada es una
        conclusion con significado en este bloque, asi que un empalme mal hecho
        no se veria como un fallo sino como un diagnostico.

        Su limite esta en las rutas con parametro: el servidor las nombra con
        la PLANTILLA (`/users/{id}`) y el cliente con el valor resuelto, asi que
        ahi el empalme falla. Es el mismo borde que D-117.

        **Que no encuentre nada tambien es un hallazgo**, y de los buenos: si
        nadie registro haber atendido la llamada que fallo, el fallo ocurrio
        antes de llegar a la aplicacion —proxy, validacion de transporte,
        conexion rechazada— y eso descarta medio arbol de hipotesis.
        """
        return self.consulta(
            f"""
            SELECT Timestamp, ServiceName AS servicio, SpanName AS nombre, Duration,
                   StatusCode, StatusMessage,
                   SpanAttributes['http.response.status_code'] AS codigo,
                   SpanAttributes['error.type'] AS clase,
                   TraceId
            FROM {self.base}.otel_traces
            WHERE Timestamp BETWEEN {{desde:DateTime64(9)}} AND {{hasta:DateTime64(9)}}
              AND SpanKind = 'Server'
              AND position(SpanName, {{ruta:String}}) > 0
            ORDER BY Timestamp
            LIMIT {LIMITE_FILAS}
            """,
            desde=momento - timedelta(seconds=margen_s),
            hasta=momento + timedelta(seconds=margen_s),
            ruta=ruta,
        )


def _una_linea(texto: str) -> str:
    """El motivo de una laguna acaba en una notificacion, asi que cabe en una linea.

    `httpx` describe un 400 en tres lineas con un enlace a MDN. Eso en un
    mensaje de Telegram, multiplicado por seis consultas, entierra el resto del
    aviso — y lo vi en el primer ensayo contra el stack real.
    """
    return " ".join(texto.split())[:200]


def _como_parametro(valor: Any) -> str:
    """Serializa un parametro para la API HTTP de ClickHouse.

    Las fechas van sin zona y en UTC: la columna es `DateTime64(9)` sin zona, y
    mandarle un `+00:00` la hace fallar al parsear. Convertir aqui y no en cada
    llamada es lo que evita que una consulta pregunte por otra hora — que es el
    error que ya costo una lectura equivocada de un desfase de relojes (D-129).
    """
    if isinstance(valor, datetime):
        from datetime import UTC

        if valor.tzinfo is not None:
            valor = valor.astimezone(UTC).replace(tzinfo=None)
        return valor.strftime("%Y-%m-%d %H:%M:%S.%f")
    return str(valor)
