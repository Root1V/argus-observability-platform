"""Google Chat mediante webhook entrante.

Es el canal principal por una razon practica: es el de menor friccion de los
tres. Un POST con JSON, sin aprobacion de proveedor, sin plantillas, sin coste
por mensaje.

Y es el unico que soporta **botones**, lo que lo convierte en el sitio natural
para la aprobacion humana de acciones de remediacion (L5, F6-06) y para
reconocer un incidente sin salir del chat.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from argus_schemas import Incident, Severity

from .render import cuerpo, resumen, titular

log = logging.getLogger("alert_bus.sinks.gchat")

# Cards V2. El formato legacy (`cards`) esta obsoleto.
COLOR = {
    Severity.PAGE: {"red": 0.85, "green": 0.15, "blue": 0.15, "alpha": 1},
    Severity.TICKET: {"red": 0.95, "green": 0.7, "blue": 0.1, "alpha": 1},
    Severity.INFO: {"red": 0.5, "green": 0.5, "blue": 0.5, "alpha": 1},
}


class GoogleChatSink:
    """Envia tarjetas a un espacio de Google Chat.

    Sobre la actualizacion de mensajes: un webhook entrante devuelve el nombre
    del mensaje creado, y ese nombre permite EDITARLO despues con
    `?messageReplyOption` y una peticion PUT. Eso es lo que hace posible la
    divulgacion progresiva (D-015): el informe del agente sustituye al aviso
    inicial en vez de anadir un mensaje nuevo.

    Si la edicion falla —permisos, mensaje caducado— se cae a publicar en el
    HILO del mensaje original. Peor que editar, mucho mejor que un mensaje
    suelto sin contexto.
    """

    name = "gchat"

    def __init__(self, webhook_url: str, *, timeout_s: float = 10.0) -> None:
        self._url = webhook_url
        self._timeout = timeout_s
        # thread_key -> nombre del mensaje en Google Chat, para poder editarlo.
        self._mensajes: dict[str, str] = {}

    def send(self, incident: Incident, *, update: bool) -> None:
        payload = self._build_card(incident)
        clave = incident.thread_key or incident.id

        if update and (nombre := self._mensajes.get(clave)):
            if self._update(nombre, payload):
                return
            # No se pudo editar: al menos que caiga en el mismo hilo.
            log.warning("gchat.update_failed", extra={"incident": incident.id})

        nombre = self._post(payload, thread_key=clave)
        if nombre:
            self._mensajes[clave] = nombre

    # --- HTTP ----------------------------------------------------------------

    def _post(self, payload: dict[str, Any], *, thread_key: str) -> str | None:
        # `threadKey` agrupa en un hilo; con REPLY_MESSAGE_OR_FAIL el segundo
        # mensaje con la misma clave responde al hilo en vez de crear uno nuevo.
        query = urllib.parse.urlencode({
            "threadKey": thread_key,
            "messageReplyOption": "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD",
        })
        separador = "&" if "?" in self._url else "?"
        url = f"{self._url}{separador}{query}"

        try:
            respuesta = self._request(url, payload, method="POST")
            return respuesta.get("name") if respuesta else None
        except Exception as exc:
            log.error("gchat.post_failed", extra={"error": str(exc)})
            raise

    def _update(self, nombre_mensaje: str, payload: dict[str, Any]) -> bool:
        try:
            base = self._url.split("/spaces/")[0]
            url = f"{base}/v1/{nombre_mensaje}?updateMask=cardsV2"
            self._request(url, payload, method="PUT")
            return True
        except Exception:  # noqa: BLE001
            return False

    def _request(self, url: str, payload: dict[str, Any], *, method: str) -> dict[str, Any] | None:
        datos = json.dumps(payload).encode("utf-8")
        peticion = urllib.request.Request(
            url, data=datos, method=method,
            headers={"Content-Type": "application/json; charset=UTF-8"},
        )
        with urllib.request.urlopen(peticion, timeout=self._timeout) as r:
            cuerpo_respuesta = r.read()
        try:
            return json.loads(cuerpo_respuesta) if cuerpo_respuesta else None
        except ValueError:
            return None

    # --- Tarjeta -------------------------------------------------------------

    def _build_card(self, incident: Incident) -> dict[str, Any]:
        widgets: list[dict[str, Any]] = [
            {"decoratedText": {"text": f"<b>{_escape(resumen(incident))}</b>"}}
        ]

        for encabezado, contenido in cuerpo(incident):
            widgets.append({
                "decoratedText": {
                    "topLabel": encabezado,
                    "text": _escape(contenido).replace("\n", "<br>"),
                    "wrapText": True,
                }
            })

        # Los botones son la razon de que este sea el canal principal: permiten
        # reconocer sin salir del chat, y mas adelante aprobar remediaciones.
        botones = [
            {
                "text": "Reconocer",
                "onClick": {"action": {"function": "acknowledge",
                                       "parameters": [{"key": "incident_id", "value": incident.id}]}},
            },
            {
                "text": "Silenciar 1h",
                "onClick": {"action": {"function": "silence",
                                       "parameters": [{"key": "fingerprint", "value": incident.fingerprint}]}},
            },
        ]
        widgets.append({"buttonList": {"buttons": botones}})

        return {
            "cardsV2": [{
                "cardId": incident.fingerprint,
                "card": {
                    "header": {
                        "title": _escape(titular(incident)),
                        "subtitle": f"{incident.app} · {incident.environment or 'sin entorno'}",
                    },
                    "sections": [{"widgets": widgets}],
                },
            }],
            # Texto de respaldo: es lo que llega a la notificacion del movil y
            # lo que se ve si el cliente no renderiza la tarjeta.
            "text": _escape(titular(incident)),
        }


def _escape(texto: str) -> str:
    """Google Chat interpreta un subconjunto de HTML en las tarjetas."""
    return texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
