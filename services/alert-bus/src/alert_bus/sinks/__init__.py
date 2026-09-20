"""Canales de notificacion.

Son una interfaz a proposito: enchufar Keep, PagerDuty o lo que venga mas
adelante es escribir un adaptador, no rehacer la capa (D-025).

Todo sink recibe `update`: `False` cuando el incidente nace, `True` cuando el
agente lo enriquece. Un canal que soporte editar mensajes (Google Chat) debe
ACTUALIZAR el existente; uno que no (WhatsApp) puede ignorar las
actualizaciones.
"""

from __future__ import annotations

from .base import ConsoleSink, FailingSink, JSONSink, MemorySink, Sink
from .dispatch import Dispatcher, InlineDispatcher
from .email import EmailSink
from .googlechat import GoogleChatSink
from .render import cuerpo, resumen, texto_plano, titular
from .telegram import TelegramSink
from .whatsapp import WhatsAppSink

__all__ = [
    "Sink",
    "ConsoleSink",
    "JSONSink",
    "MemorySink",
    "FailingSink",
    "GoogleChatSink",
    "TelegramSink",
    "EmailSink",
    "WhatsAppSink",
    "Dispatcher",
    "InlineDispatcher",
    "titular",
    "resumen",
    "cuerpo",
    "texto_plano",
]
