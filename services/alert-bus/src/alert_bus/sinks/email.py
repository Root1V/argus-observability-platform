"""Correo por SMTP.

Es el canal del informe COMPLETO: sin limite de longitud, sin restricciones de
formato y sin urgencia. Google Chat da el titular; el correo da la evidencia
entera, que es lo que hace falta cuando alguien se sienta a investigar.

Tambien es el canal del digest diario, donde se acumula todo lo que no merecia
interrumpir a nadie.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from argus_schemas import Incident

from .render import cuerpo, resumen, texto_plano, titular

log = logging.getLogger("alert_bus.sinks.email")


class EmailSink:
    """Envia el informe por SMTP."""

    name = "email"

    def __init__(
        self,
        *,
        host: str,
        port: int = 587,
        username: str = "",
        password: str = "",
        sender: str = "argus@localhost",
        recipients: list[str] | None = None,
        use_tls: bool = True,
        timeout_s: float = 20.0,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender
        self._recipients = recipients or []
        self._use_tls = use_tls
        self._timeout = timeout_s

    def send(self, incident: Incident, *, update: bool) -> None:
        if not self._recipients:
            log.warning("email.no_recipients", extra={"incident": incident.id})
            return

        mensaje = EmailMessage()
        prefijo = "Actualización: " if update else ""
        mensaje["Subject"] = f"{prefijo}{titular(incident)}"
        mensaje["From"] = self._sender
        mensaje["To"] = ", ".join(self._recipients)

        # Estas dos cabeceras son lo que hace que el cliente de correo agrupe
        # el aviso inicial y el informe del agente en la MISMA conversacion.
        # Sin ellas, la divulgacion progresiva produce dos correos sueltos.
        hilo = f"<{incident.fingerprint}@argus>"
        if update:
            # Cada correo lleva su PROPIO Message-ID —hay MTA que rechazan los
            # que no lo traen— y apunta al del aviso inicial para enhebrarse.
            marca = incident.updated_at.timestamp()
            mensaje["Message-ID"] = f"<{incident.fingerprint}.{marca}@argus>"
            mensaje["In-Reply-To"] = hilo
            mensaje["References"] = hilo
        else:
            mensaje["Message-ID"] = hilo

        mensaje.set_content(texto_plano(incident))
        mensaje.add_alternative(self._html(incident), subtype="html")

        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as servidor:
            if self._use_tls:
                servidor.starttls()
            if self._username:
                servidor.login(self._username, self._password)
            servidor.send_message(mensaje)

    def _html(self, incident: Incident) -> str:
        secciones = "".join(
            f'<h3 style="margin:16px 0 4px;font-size:13px;letter-spacing:.5px;'
            f'color:#666;text-transform:uppercase">{_esc(encabezado)}</h3>'
            f'<div style="white-space:pre-wrap">{_esc(contenido)}</div>'
            for encabezado, contenido in cuerpo(incident)
        )
        return (
            '<div style="font-family:-apple-system,system-ui,sans-serif;max-width:640px">'
            f'<h2 style="margin:0 0 4px">{_esc(titular(incident))}</h2>'
            f'<p style="color:#666;margin:0">{_esc(resumen(incident))}</p>'
            f"{secciones}"
            "</div>"
        )


def _esc(texto: str) -> str:
    return texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
