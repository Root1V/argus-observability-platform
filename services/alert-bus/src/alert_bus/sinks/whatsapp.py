"""WhatsApp mediante la Cloud API de Meta.

**Solo para severidad critica fuera de horario.** No es un canal de volumen, y
hay dos razones de coste que lo hacen explicito:

1. Un aviso iniciado por el negocio exige una **plantilla preaprobada**, y la
   categoria de la plantilla determina la tarifa. Clasificarla como *utility* y
   no como *marketing* evita pagar de mas.
2. Desde el **1 de octubre de 2026** las respuestas dentro de la ventana de 24
   horas dejan de ser gratuitas y pasan a cobrarse por mensaje.

Por eso este canal manda solo el titular y un enlace: el informe completo vive
en el correo y en Google Chat, que no cuestan por mensaje.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

from argus_schemas import Incident, Severity

from .render import resumen, titular

log = logging.getLogger("alert_bus.sinks.whatsapp")

API = "https://graph.facebook.com/v21.0"


class WhatsAppSink:
    """Envia el titular por plantilla de WhatsApp."""

    name = "whatsapp"

    def __init__(
        self,
        *,
        phone_number_id: str,
        access_token: str,
        recipients: list[str] | None = None,
        template: str = "argus_incidente",
        language: str = "es",
        timeout_s: float = 15.0,
    ) -> None:
        self._phone_id = phone_number_id
        self._token = access_token
        self._recipients = recipients or []
        self._template = template
        self._language = language
        self._timeout = timeout_s

    def send(self, incident: Incident, *, update: bool) -> None:
        # Una actualizacion por WhatsApp seria un segundo mensaje de pago que
        # dice casi lo mismo. El informe ya va por correo y por Chat.
        if update:
            return
        if incident.severity is not Severity.PAGE:
            log.debug("whatsapp.skipped_non_page", extra={"incident": incident.id})
            return
        if not self._recipients:
            log.warning("whatsapp.no_recipients", extra={"incident": incident.id})
            return

        for destinatario in self._recipients:
            self._send_one(destinatario, incident)

    def _send_one(self, destinatario: str, incident: Incident) -> None:
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": destinatario,
            "type": "template",
            "template": {
                "name": self._template,
                "language": {"code": self._language},
                "components": [{
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": _acortar(titular(incident), 180)},
                        {"type": "text", "text": _acortar(resumen(incident), 120)},
                    ],
                }],
            },
        }

        peticion = urllib.request.Request(
            f"{API}/{self._phone_id}/messages",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(peticion, timeout=self._timeout):
            pass


def _acortar(texto: str, limite: int) -> str:
    """Los parametros de plantilla tienen limite y no admiten saltos de linea."""
    limpio = texto.replace("\n", " ").strip()
    return limpio if len(limpio) <= limite else limpio[: limite - 1] + "…"
