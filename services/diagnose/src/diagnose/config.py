"""Configuracion de L3. Por entorno, como el resto de la plataforma."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DIAGNOSE_", extra="ignore")

    # A quien se le preguntan los incidentes y a quien se le devuelve la causa.
    alertbus_url: str = "http://127.0.0.1:8080"

    # De donde salen las trazas. Es el mismo ClickHouse del plano central, con
    # las mismas credenciales por ahora: no hay un usuario de solo lectura
    # creado todavia, y lo que impide escribir es que este cliente rechaza todo
    # lo que no empiece por SELECT.
    clickhouse_url: str = "http://127.0.0.1:8123"
    clickhouse_user: str = "default"
    clickhouse_password: str = ""
    clickhouse_db: str = "otel"

    # Cada cuanto se busca trabajo. No es el camino caliente: el aviso ya salio
    # —un `page` se notifica en cuanto nace— y esto solo lo mejora despues. Diez
    # segundos es mas rapido que cualquier persona abriendo el movil.
    intervalo_s: int = 10

    # Cuantos incidentes se diagnostican por ciclo. Acotado porque una tormenta
    # de incidentes nuevos no puede convertirse en una tormenta de consultas a
    # ClickHouse: el almacen es el mismo del que depende el resto de la
    # plataforma para mirar.
    max_por_ciclo: int = 3

    # Cuanto se espera antes de volver a diagnosticar el MISMO incidente sin
    # causa. Sin esto, un incidente que las reglas no saben explicar se
    # rediagnosticaria cada diez segundos para siempre.
    reintentar_tras_s: int = 600

    # Que juez se usa. Vacio = reglas, que es el control del experimento y lo
    # unico que funciona sin red ni clave. Cuando haya modelo, su nombre va
    # aqui y el juez de reglas sigue disponible para comparar.
    juez: str = "reglas"

    # --- El juez con modelo, en sombra (D-135, F4-03) ------------------------
    # `modelo` para encenderlo. Juzga cada expediente DESPUES de las reglas y
    # se registra al lado; el aviso sigue llevando el veredicto de las reglas
    # hasta que el banco de F3-08 diga lo contrario. La credencial no va aqui:
    # `AxoniumModel` lee `AXONIUM_CLIENT_ID` / `AXONIUM_CLIENT_SECRET` del
    # entorno, y sin ella el juez en sombra no arranca y lo dice.
    juez_sombra: str = ""
    modelo: str = "qwen3-30b-a3b-q4-k-m-local"
    # `off`, `low`, `medium` o `high`; vacio deja el del modelo. Se traduce a
    # la clave de cada familia en `juez_modelo.opciones_de_plantilla`.
    razonamiento: str = ""
    # Donde se guarda cada pareja reglas/modelo, una linea JSON por juicio.
    # Es lo que permite comparar los dos sobre incidentes REALES y no solo
    # sobre el banco. Vacio = solo al log.
    sombra_path: str = ""

    # --- L4 en sombra (F4-01, F4-03) ------------------------------------------
    # `modelo` para encenderlo: lo que el triage (F3-03) deja pasar se
    # investiga en segundo plano con el mismo `modelo` y `razonamiento`.
    investigador: str = ""

    # --- F3-05: memoria por parecido -------------------------------------------
    # El modelo de embeddings de Axonium (`qwen3-embedding`), `lexico` para la
    # linea base sin modelo, o vacio para apagarla.
    embeddings: str = ""
    memoria_path: str = "/var/lib/argus/memoria.sqlite"
    sombra_l4_path: str = ""
    # El diario de synaptum y las investigaciones pendientes: lo que permite
    # retomar tras un reinicio sin repetir inferencias (F4-01).
    l4_diario_path: str = "/var/lib/argus/l4.sqlite"
    # Investigaciones por hora como mucho (D-150): cada una son ~35.000 tokens
    # de la plataforma que comparten Centinela y Veritium.
    l4_por_hora: int = 2

    def timeout_s(self) -> float:
        return 10.0
