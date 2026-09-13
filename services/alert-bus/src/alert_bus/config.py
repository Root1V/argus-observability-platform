"""Configuracion, por entorno como todo lo demas."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ALERTBUS_", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8080

    # El registro de aplicaciones. Sin el, todo servicio entra como provisional
    # y la correlacion por topologia no tiene grafo sobre el que trabajar.
    registry_path: Path = Path("platform/registry/apps.yaml")

    # Ventana de agrupacion para severidades por debajo de `page`. Los `page`
    # no esperan: avisar tarde es no avisar.
    group_window_s: int = 60

    # Un incidente sin senales nuevas durante este tiempo se da por resuelto.
    resolve_after_s: int = 900

    # Cada cuanto se revisan ventanas y cierres.
    tick_s: int = 5

    # Token que exige a los Collector agente. El mismo del gateway.
    token: str = ""

    sinks: str = "console,json"

    # Cuantos envios en paralelo. Dos bastan: el volumen de notificaciones es
    # bajo por definicion y mas hilos solo anaden contencion.
    dispatch_workers: int = 2
    dispatch_queue_size: int = 1_000

    # --- Google Chat ---------------------------------------------------------
    # El canal principal: un POST con JSON, sin aprobacion de proveedor, sin
    # coste por mensaje, y el unico con botones.
    gchat_webhook: str = ""

    # --- Telegram ------------------------------------------------------------
    # El canal del piloto. No depende de ningun proveedor de identidad, y es el
    # unico de los tres que permite EDITAR un mensaje ya enviado, asi que la
    # divulgacion progresiva es un mensaje que se actualiza (D-053).
    telegram_token: str = ""
    telegram_chat_id: str = ""
    # Solo para pruebas: apunta el sink a un servidor que imita la API. En
    # produccion no se toca.
    telegram_api_base: str = "https://api.telegram.org"

    # --- Correo --------------------------------------------------------------
    # El canal del informe COMPLETO, sin limite de longitud ni urgencia.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "argus@localhost"
    smtp_to: str = ""
    smtp_tls: bool = True

    # --- WhatsApp ------------------------------------------------------------
    # SOLO critico fuera de horario: cuesta por mensaje y exige plantilla
    # preaprobada, clasificada como `utility` y no como `marketing`.
    whatsapp_phone_id: str = ""
    whatsapp_token: str = ""
    whatsapp_to: str = ""
    whatsapp_template: str = "argus_incidente"

    def sink_names(self) -> list[str]:
        return [s.strip() for s in self.sinks.split(",") if s.strip()]

    def smtp_recipients(self) -> list[str]:
        return [r.strip() for r in self.smtp_to.split(",") if r.strip()]

    def whatsapp_recipients(self) -> list[str]:
        return [r.strip() for r in self.whatsapp_to.split(",") if r.strip()]
