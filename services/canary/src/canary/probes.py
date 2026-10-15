"""Sondas: las comprobaciones concretas.

Separadas del planificador para que se puedan probar sin relojes ni esperas.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

import httpx

# La sonda de ingesta se reconoce por estos dos. El `argus.event` tiene una
# politica propia en el muestreo del gateway: sin ella, la sonda seria una
# tirada de dados del 10% y reportaria caido nueve de cada diez veces.
EVENTO_INGESTA: Final = "canary.ingesta"
ATRIBUTO_MARCA: Final = "argus.canary.marca"


class Resultado(StrEnum):
    OK = "ok"
    LENTO = "lento"           # responde, pero por encima de su SLO
    CAIDO = "caido"           # no responde
    SILENCIO = "silencio"     # no ha emitido telemetria en la ventana


@dataclass(frozen=True)
class Medicion:
    objetivo: str             # app/componente
    app: str
    component: str
    resultado: Resultado
    duracion_ms: int | None = None
    detalle: str = ""

    @property
    def es_problema(self) -> bool:
        return self.resultado is not Resultado.OK


class Sonda(Protocol):
    async def ejecutar(self) -> Medicion: ...


class SondaHTTP:
    """Comprueba que un endpoint responde, y en cuanto tiempo.

    Un 5xx cuenta como caido; un 4xx no. Si el servicio devuelve 404 es que
    esta vivo y la sonda apunta mal: eso es un fallo de configuracion de la
    sonda, no una caida de la aplicacion, y confundirlos genera guardias
    inutiles.
    """

    def __init__(
        self,
        *,
        app: str,
        component: str,
        url: str,
        slo_ms: int = 0,
        timeout_s: float = 10.0,
        expected_status: int | None = None,
    ) -> None:
        self.app = app
        self.component = component
        self.url = url
        self.slo_ms = slo_ms
        self.timeout_s = timeout_s
        self.expected_status = expected_status

    async def ejecutar(self) -> Medicion:
        inicio = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as cliente:
                respuesta = await cliente.get(self.url)
            transcurrido = int((time.perf_counter() - inicio) * 1000)
        except Exception as exc:  # noqa: BLE001
            return Medicion(
                objetivo=f"{self.app}/{self.component}",
                app=self.app,
                component=self.component,
                resultado=Resultado.CAIDO,
                duracion_ms=int((time.perf_counter() - inicio) * 1000),
                detalle=f"{type(exc).__name__}: {exc}",
            )

        base = {
            "objetivo": f"{self.app}/{self.component}",
            "app": self.app,
            "component": self.component,
            "duracion_ms": transcurrido,
        }

        if self.expected_status is not None and respuesta.status_code != self.expected_status:
            return Medicion(
                **base,
                resultado=Resultado.CAIDO,
                detalle=f"esperaba {self.expected_status}, devolvio {respuesta.status_code}",
            )

        if respuesta.status_code >= 500:
            return Medicion(**base, resultado=Resultado.CAIDO, detalle=f"HTTP {respuesta.status_code}")

        if self.slo_ms and transcurrido > self.slo_ms:
            return Medicion(
                **base,
                resultado=Resultado.LENTO,
                detalle=f"{transcurrido} ms sobre un objetivo de {self.slo_ms} ms",
            )

        return Medicion(**base, resultado=Resultado.OK)


class SondaSilencio:
    """Comprueba que una aplicacion ha emitido telemetria recientemente.

    Es la que cierra el punto ciego de verdad. Una aplicacion puede estar
    respondiendo a su `/health` perfectamente y haber dejado de procesar
    trabajo; y una aplicacion caida simplemente no emite, que sin esto es
    indistinguible de "va todo bien y no hay trafico".

    Consulta las METRICAS y no las trazas, y la razon importa: las trazas
    pasan por tail sampling, asi que un servicio con poco trafico puede tener
    TODOS sus spans descartados legitimamente y parecer muerto.

    Las metricas se derivan de todas las trazas ANTES de muestrear —es una
    propiedad deliberada del pipeline del gateway—, asi que son la unica fuente
    que responde "¿ha estado activo?" sin sesgo.

    Y mide CRECIMIENTO, no presencia de puntos: las series acumulativas se
    reexportan para siempre aunque el servicio este muerto. Ver `_crecimiento`.
    """

    def __init__(
        self,
        *,
        app: str,
        component: str,
        clickhouse_url: str,
        usuario: str,
        password: str,
        ventana_s: int = 900,
        timeout_s: float = 15.0,
    ) -> None:
        self.app = app
        self.component = component
        self._url = clickhouse_url
        self._usuario = usuario
        self._password = password
        self.ventana_s = ventana_s
        self.timeout_s = timeout_s

    async def ejecutar(self) -> Medicion:
        componente = _escapar(self.component)
        ventana = int(self.ventana_s)
        # Se miran las dos tablas de metricas: un servicio puede emitir solo
        # contadores o solo histogramas segun lo que haga.
        consulta = (
            "SELECT "
            + self._crecimiento("otel.otel_metrics_sum", "Value", componente, ventana)
            + " + "
            + self._crecimiento("otel.otel_metrics_histogram", "Count", componente, ventana)
            + " AS actividad"
        )
        base = {
            "objetivo": f"{self.app}/{self.component}",
            "app": self.app,
            "component": self.component,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as cliente:
                respuesta = await cliente.post(
                    self._url,
                    content=consulta,
                    headers={
                        "X-ClickHouse-User": self._usuario,
                        "X-ClickHouse-Key": self._password,
                    },
                )
            respuesta.raise_for_status()
            actividad = float(respuesta.text.strip() or 0)
        except Exception as exc:  # noqa: BLE001
            # No poder consultar no significa que la app este callada: significa
            # que no lo sabemos. Decir "esta caida" seria inventarse un
            # incidente, que es peor que no detectarlo.
            return Medicion(**base, resultado=Resultado.OK, detalle=f"almacen inaccesible: {exc}")

        if actividad <= 0:
            return Medicion(
                **base,
                resultado=Resultado.SILENCIO,
                detalle=f"sin telemetría en {self.ventana_s // 60} min",
            )
        return Medicion(**base, resultado=Resultado.OK, detalle=f"actividad {actividad:g}")

    @staticmethod
    def _crecimiento(tabla: str, columna: str, componente: str, ventana: int) -> str:
        """Actividad = el contador CRECIO, no «hay puntos».

        Contar puntos no sirve y el piloto lo demostro: `auth-service` llevaba
        tres horas muerto y la sonda veia 1.080 puntos en quince minutos. Eran
        la misma serie del connector `spanmetrics`, que es ACUMULATIVA y
        reexporta su valor en cada intervalo aunque no haya pasado nada. Un
        servicio muerto parecia sano, que es justo el fallo que esta sonda
        existe para impedir.

        Por eso la formula depende de la temporalidad:

        - **Delta** (`AggregationTemporality = 1`): cada punto es lo ocurrido en
          su intervalo, asi que la suma sirve tal cual.
        - **Acumulativa** (`= 2`): el valor solo sube, y estar vivo significa que
          el total de FINAL de ventana supera al de principio. Un reinicio pone
          el contador a cero y tambien cuenta como actividad, que es correcto.

        El `toFloat64` no es decorativo: `Count` es `UInt64` y `Value` es
        `Float64`, y ClickHouse se niega a restar enteros con signo de enteros
        sin signo.

        **El crecimiento se mide POR SERIE, no sobre la suma de todas.** Sumar
        primero y restar despues parece equivalente y no lo es: el exportador
        reescribe a veces el mismo punto dos veces —visto en los datos, la misma
        serie con el mismo valor y el mismo `TimeUnix`—, la suma del instante se
        dobla, y la resta lee esa duplicacion como actividad. Medido sobre una
        serie congelada durante tres horas, la formula vieja daba **204** y la
        nueva **0**. Un servicio muerto habria parecido vivo otra vez.

        Por eso el `max` en la agrupacion mas interna: deduplica puntos
        identicos antes de comparar. Y por eso `sum` solo al final, sobre
        crecimientos por serie que ya son reales.
        """
        return (
            "(SELECT if(any(temp) = 1, sum(total), sum(crecimiento)) FROM ("
            "  SELECT any(temp) AS temp, sum(v) AS total, max(v) - min(v) AS crecimiento FROM ("
            f"    SELECT Attributes, TimeUnix, any(AggregationTemporality) AS temp,"
            f"           max(toFloat64({columna})) AS v"
            f"    FROM {tabla}"
            f"    WHERE ResourceAttributes['service.name'] = '{componente}'"
            f"    AND TimeUnix > now() - INTERVAL {ventana} SECOND"
            "    GROUP BY Attributes, TimeUnix)"
            "  GROUP BY Attributes))"
        )


def _escapar(valor: str) -> str:
    """Escape minimo para literales de ClickHouse.

    Los nombres de servicio salen del registro, que es un fichero que
    controlamos; aun asi, no interpolar sin escapar.
    """
    return valor.replace("\\", "\\\\").replace("'", "\\'")


class SondaDeIngesta:
    """Manda un span y comprueba que se puede consultar en el almacen.

    **Por que existe**, y la frase es de Prometheus: *una sonda de
    disponibilidad que ejercita menos superficie que el servicio declara es una
    sonda que certifica lo que no probo*. Les paso con un modelo de vision que
    llevaba meses sin poder ver mientras su sonda decia sano, porque una sonda
    de disponibilidad manda texto.

    Las cuatro sondas HTTP de este canario tienen esa forma exacta: pegan a
    `/healthz`, `/ping`, `/health` y al `health_check` del Collector. Las cuatro
    dicen que un proceso contesta. **Ninguna dice que la telemetria enviada
    llegue al almacen**, que es la unica funcion distintiva de esta plataforma:

    - ClickHouse puede contestar a `/ping` con la base `otel` borrada.
    - El Collector puede estar sano con una tuberia desactivada por un error de
      configuracion, o con la cola de exportacion llena y descartando.
    - Y el `alert-bus` puede responder a `/healthz` con su receptor OTLP roto.

    En los tres casos el tablero diria verde y no llegaria un solo span.

    **Lo que esta sonda NO cubre, dicho aqui y no en un sitio mas comodo:** el
    salto del Collector AGENTE. Este canario exporta a `ARGUS_ENDPOINT`, que en
    el despliegue del plano central es el gateway, asi que mide
    `gateway -> muestreo -> ClickHouse`. Un agente caido en la maquina de una
    aplicacion no lo detecta esto, lo detecta la sonda de silencio de esa app.
    Nombrarla `extremo a extremo` seria repetir el defecto que viene a arreglar.
    """

    # El muestreo del gateway espera a que la traza cierre antes de decidir
    # (`decision_wait: 30s`), y despues vienen el lote y la insercion. Un plazo
    # mas corto que eso convertiria la sonda en un generador de falsos caidos.
    ESPERA_MINIMA_S: Final = 30

    def __init__(
        self,
        *,
        app: str,
        component: str,
        clickhouse_url: str,
        usuario: str,
        password: str,
        plazo_s: int = 90,
        timeout_s: float = 15.0,
    ) -> None:
        self.app = app
        self.component = component
        self._url = clickhouse_url
        self._usuario = usuario
        self._password = password
        self.plazo_s = max(plazo_s, self.ESPERA_MINIMA_S + 15)
        self.timeout_s = timeout_s

    async def ejecutar(self) -> Medicion:
        base = {
            "objetivo": f"{self.app}/{self.component}",
            "app": self.app,
            "component": self.component,
        }
        # Marca UNICA por ejecucion. Sin ella, una fila de hace una hora haria
        # pasar la sonda mientras la ingesta esta parada — que es la forma de
        # sonda que certifica lo que no probo, otra vez y en esta misma clase.
        marca = uuid.uuid4().hex
        inicio = time.perf_counter()

        try:
            self._emitir(marca)
        except Exception as exc:  # noqa: BLE001
            return Medicion(
                **base,
                resultado=Resultado.CAIDO,
                duracion_ms=int((time.perf_counter() - inicio) * 1000),
                detalle=f"no se pudo emitir: {type(exc).__name__}: {exc}",
            )

        try:
            llego_en = await self._esperar_en_el_almacen(marca)
        except Exception as exc:  # noqa: BLE001
            return Medicion(
                **base,
                resultado=Resultado.CAIDO,
                duracion_ms=int((time.perf_counter() - inicio) * 1000),
                detalle=f"no se pudo consultar el almacen: {type(exc).__name__}: {exc}",
            )

        transcurrido = int((time.perf_counter() - inicio) * 1000)
        if llego_en is None:
            return Medicion(
                **base,
                resultado=Resultado.CAIDO,
                duracion_ms=transcurrido,
                detalle=(
                    f"el span {marca[:8]} no aparecio en {self.plazo_s}s. "
                    f"La telemetria no esta llegando al almacen"
                ),
            )
        return Medicion(**base, resultado=Resultado.OK, duracion_ms=transcurrido)

    def _emitir(self, marca: str) -> None:
        """Un span con la marca, y se fuerza el envio.

        `force_flush` no es opcional: el procesador por lotes del SDK espera
        cientos de milisegundos antes de exportar, y esta sonda mide el camino
        y no la paciencia del lote.
        """
        import argus
        from argus_semconv import attributes as A

        with argus.step(EVENTO_INGESTA) as paso:
            paso.set(**{ATRIBUTO_MARCA: marca, A.ARGUS_COMPONENT_ROLE: "scheduler"})
        argus.handle().force_flush()

    async def _esperar_en_el_almacen(self, marca: str) -> int | None:
        consulta = (
            "SELECT count() FROM otel.otel_traces "
            f"WHERE Timestamp > now() - INTERVAL {self.plazo_s + 60} SECOND "
            f"AND SpanAttributes['{ATRIBUTO_MARCA}'] = '{_escapar(marca)}'"
        )
        limite = time.monotonic() + self.plazo_s
        intento = 0
        while time.monotonic() < limite:
            # La primera consulta no sirve de nada antes del `decision_wait`.
            await asyncio.sleep(5 if intento else self.ESPERA_MINIMA_S)
            intento += 1
            async with httpx.AsyncClient(timeout=self.timeout_s) as cliente:
                r = await cliente.post(
                    self._url,
                    content=consulta,
                    headers={
                        "X-ClickHouse-User": self._usuario,
                        "X-ClickHouse-Key": self._password,
                    },
                )
            r.raise_for_status()
            if int((r.text or "0").strip() or 0) > 0:
                return intento
        return None
