"""Telegram mediante la API de bots.

Es el canal del piloto por descarte razonado, no por preferencia: los webhooks
entrantes de Google Chat y las contrasenas de aplicacion de Gmail son ambos
funciones que la cuenta no tiene (D-044, D-053). Telegram no depende de ningun
proveedor de identidad: un bot, dos minutos, sin aprobacion de nadie.

Y resulta ser el mejor tecnicamente de los tres, por una razon concreta:
`editMessageText` permite **editar un mensaje ya enviado**, asi que la
divulgacion progresiva (D-015) es UN mensaje que se actualiza —el aviso inicial
se convierte en el informe del agente— en vez de dos mensajes en un hilo, que
es lo mejor que puede hacer el correo.

Ademas admite botones, que el correo no, asi que es el sitio natural para la
aprobacion humana de remediaciones (F6-06) el dia que llegue.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Any

from argus_schemas import Incident

from .render import cuerpo, resumen, titular

log = logging.getLogger("alert_bus.sinks.telegram")

class TelegramSink:
    """Envia avisos a un chat de Telegram y los ACTUALIZA en el sitio.

    Sobre el formato: se usa HTML y no Markdown a proposito. El `MarkdownV2` de
    Telegram exige escapar dieciocho caracteres —incluidos `.`, `-` y `!`— y un
    solo descuido devuelve 400 y pierde el aviso entero. HTML necesita escapar
    tres, y son los mismos que ya escapamos en todas partes.
    """

    name = "telegram"

    # La API rechaza mensajes de mas de 4096 caracteres. Un informe con
    # evidencia larga los supera, y perder el aviso por pasarse de largo seria
    # absurdo: se recorta y se dice que se recorto.
    MAX = 4000

    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        timeout_s: float = 10.0,
        api_base: str = "https://api.telegram.org",
    ) -> None:
        self._token = token
        self._chat = chat_id
        self._timeout = timeout_s
        self._base = api_base.rstrip("/")
        # thread_key -> message_id, para poder editarlo despues.
        self._mensajes: dict[str, int] = {}

    # --- Envio ---------------------------------------------------------------

    def send(self, incident: Incident, *, update: bool) -> None:
        clave = incident.thread_key or incident.id
        texto = self._render(incident)

        if update and (message_id := self._mensajes.get(clave)):
            if self._editar(message_id, texto):
                return
            # No se pudo editar —mensaje muy viejo, borrado, o sin cambios—.
            # Mandar uno nuevo es peor que editar y mucho mejor que perder el
            # informe del agente.
            log.warning("telegram.edit_failed", extra={"incident": incident.id})

        if (message_id := self._enviar(texto)) is not None:
            self._mensajes[clave] = message_id

    def _enviar(self, texto: str) -> int | None:
        respuesta = self._llamar("sendMessage", {
            "chat_id": self._chat,
            "text": texto,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })
        if respuesta is None:
            return None
        return respuesta.get("result", {}).get("message_id")

    def _editar(self, message_id: int, texto: str) -> bool:
        respuesta = self._llamar("editMessageText", {
            "chat_id": self._chat,
            "message_id": message_id,
            "text": texto,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, silencioso=True)
        return respuesta is not None

    def _llamar(
        self, metodo: str, payload: dict[str, Any], *, silencioso: bool = False
    ) -> dict[str, Any] | None:
        url = f"{self._base}/bot{self._token}/{metodo}"
        datos = json.dumps(payload).encode("utf-8")
        peticion = urllib.request.Request(
            url, data=datos, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(peticion, timeout=self._timeout) as r:
                return json.loads(r.read())
        except Exception as exc:
            if silencioso:
                return None
            # El token va en la URL: NO puede acabar en un log. Se dice el
            # metodo y el error, nunca la URL.
            log.error("telegram.call_failed", extra={"metodo": metodo, "error": str(exc)})
            raise

    # --- Formato -------------------------------------------------------------

    def _render(self, incident: Incident) -> str:
        # Sin emoji propio: `titular()` ya trae el icono de severidad, y
        # anadirle otro da "🔴 🔴 [app/componente]". El formato vive en
        # `render.py` para que los canales no discrepen entre si.
        lineas = [
            f"<b>{_escapar(titular(incident))}</b>",
            f"<i>{_escapar(resumen(incident))}</i>",
        ]

        for encabezado, contenido in cuerpo(incident):
            lineas.append("")
            lineas.append(f"<b>{_escapar(encabezado)}</b>")
            lineas.append(_escapar(contenido))

        texto = "\n".join(lineas)
        if len(texto) > self.MAX:
            texto = texto[: self.MAX] + "\n\n<i>… recortado</i>"
        return texto


def _escapar(valor: str) -> str:
    """Los tres caracteres que Telegram exige escapar en modo HTML."""
    return (
        valor.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
