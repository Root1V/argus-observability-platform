"""Lo que la persona contesta al aviso: los botones y la causa real (B-46, B-47).

Hasta aqui el alert-bus solo HABLABA por Telegram. Esto le deja escuchar, y
eso abre una puerta, asi que tiene tres cerrojos:

1. **Solo el chat configurado.** Una pulsacion o un mensaje de cualquier otro
   chat se ignora y se cuenta. El bot es de Argus (`argus_avisos_bot`), pero
   cualquiera que lo encuentre puede escribirle.
2. **Solo las acciones conocidas**, sobre ids de incidente que existan. No hay
   un «ejecuta esto»: un boton acusa recibo o anota una opinion, nada mas.
3. **Solo pulsaciones y respuestas** (`allowed_updates`). Lo demas que le
   llegue al bot no se pide, asi que no se consume.

**Por long-polling y no por webhook**: un webhook necesita una URL publica con
TLS, y el alert-bus vive en un portatil detras de un router. `getUpdates` sale
de dentro hacia fuera, como los envios.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.request
from collections import OrderedDict
from typing import Any

log = logging.getLogger("alert_bus.telegram_entrada")

ACCIONES = ("ack", "ok", "no", "util", "ruido")
PREGUNTA_CAUSA = "¿Cuál fue la causa real del incidente {id}? Contesta a este mensaje."


class TelegramEntrada:
    def __init__(self, token: str, chat_id: str, engine: Any, sink: Any, *,
                 api_base: str = "https://api.telegram.org", espera_s: int = 25) -> None:
        self._token, self._chat = token, str(chat_id)
        self._engine, self._sink = engine, sink
        self._base = api_base.rstrip("/")
        self._espera = espera_s
        self._offset: int | None = None
        # message_id de la pregunta «¿cual fue la causa real?» -> id del
        # incidente. En memoria: tras un reinicio una respuesta pendiente se
        # pierde y se dice, que es mejor que adivinar a que incidente iba.
        self._preguntas: OrderedDict[int, str] = OrderedDict()
        self._parar = threading.Event()
        self._hilo: threading.Thread | None = None
        self.stats = {"pulsaciones": 0, "respuestas": 0, "ajenas": 0, "desconocidas": 0, "fallos": 0}

    # --- Ciclo -----------------------------------------------------------------

    def start(self) -> None:
        self._hilo = threading.Thread(target=self._correr, name="telegram-entrada", daemon=True)
        self._hilo.start()

    def stop(self) -> None:
        self._parar.set()

    def _correr(self) -> None:
        while not self._parar.is_set():
            try:
                for update in self._recibir():
                    self._offset = update["update_id"] + 1
                    self.procesar(update)
            except Exception as exc:  # noqa: BLE001 - la entrada nunca tumba el alert-bus
                self.stats["fallos"] += 1
                log.warning("telegram_entrada.failed", extra={"error": str(exc)[:200]})
                self._parar.wait(10)

    def _recibir(self) -> list[dict[str, Any]]:
        datos: dict[str, Any] = {"timeout": self._espera, "allowed_updates": ["callback_query", "message"]}
        if self._offset is not None:
            datos["offset"] = self._offset
        respuesta = self._llamar("getUpdates", datos, timeout=self._espera + 10)
        return respuesta.get("result", []) if respuesta else []

    def _llamar(self, metodo: str, datos: dict[str, Any], *, timeout: float = 10) -> dict[str, Any] | None:
        peticion = urllib.request.Request(
            f"{self._base}/bot{self._token}/{metodo}", data=json.dumps(datos).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"},
        )
        # El token va en la URL: nunca se registra la URL, solo el metodo.
        with urllib.request.urlopen(peticion, timeout=timeout) as r:
            return json.loads(r.read())

    # --- Lo que llega ----------------------------------------------------------

    def procesar(self, update: dict[str, Any]) -> None:
        if (cq := update.get("callback_query")) is not None:
            self._pulsacion(cq)
        elif (msg := update.get("message")) is not None:
            self._mensaje(msg)

    def _de_este_chat(self, mensaje: dict[str, Any] | None) -> bool:
        return str(((mensaje or {}).get("chat") or {}).get("id")) == self._chat

    def _pulsacion(self, cq: dict[str, Any]) -> None:
        mensaje = cq.get("message") or {}
        if not self._de_este_chat(mensaje):
            self.stats["ajenas"] += 1
            self._contestar(cq, "Este bot no atiende este chat.")
            return
        accion, _, incidente = str(cq.get("data") or "").partition(":")
        if accion not in ACCIONES or not incidente:
            self.stats["desconocidas"] += 1
            self._contestar(cq, "Botón desconocido.")
            return
        self.stats["pulsaciones"] += 1
        quien = f"telegram:{(cq.get('from') or {}).get('username') or (cq.get('from') or {}).get('id')}"

        if accion == "ack":
            hecho = self._engine.acknowledge(incidente, por=quien)
            texto = "Reconocido." if hecho else "Ese incidente ya no está abierto."
        elif accion == "ok":
            hecho = self._engine.feedback(incidente, por=quien, causa="correcta")
            texto = "Anotado: causa correcta."
        elif accion == "no":
            hecho = self._engine.feedback(incidente, por=quien, causa="incorrecta")
            texto = "Anotado: causa incorrecta."
            if hecho is not None:
                pregunta = self._sink.preguntar(PREGUNTA_CAUSA.format(id=incidente),
                                                responder_a=mensaje.get("message_id"))
                if pregunta is not None:
                    self._recordar_pregunta(pregunta, incidente)
        else:
            hecho = self._engine.feedback(incidente, por=quien, util=accion == "util")
            texto = "Anotado: útil." if accion == "util" else "Anotado: ruido."
        if hecho is None:
            texto = "No encuentro ese incidente."
        self._contestar(cq, texto)
        if hecho is not None and mensaje.get("message_id"):
            self._sink.actualizar(mensaje["message_id"], hecho)

    def _mensaje(self, msg: dict[str, Any]) -> None:
        if not self._de_este_chat(msg):
            self.stats["ajenas"] += 1
            return
        respondido = (msg.get("reply_to_message") or {}).get("message_id")
        incidente = self._preguntas.pop(respondido, None) if respondido else None
        if incidente is None:
            return  # una conversacion normal en el chat no es asunto del alert-bus
        texto = str(msg.get("text") or "").strip()
        if not texto:
            return
        quien = f"telegram:{(msg.get('from') or {}).get('username') or (msg.get('from') or {}).get('id')}"
        hecho = self._engine.feedback(incidente, por=quien, causa_real=texto)
        self.stats["respuestas"] += 1
        self._sink.decir("Anotada la causa real. La memoria de incidentes la usará la próxima vez."
                         if hecho else "No encuentro ese incidente.", responder_a=msg.get("message_id"))

    def _recordar_pregunta(self, message_id: int, incidente: str) -> None:
        self._preguntas[message_id] = incidente
        while len(self._preguntas) > 100:
            self._preguntas.popitem(last=False)

    def _contestar(self, cq: dict[str, Any], texto: str) -> None:
        """`answerCallbackQuery`: sin esto Telegram deja el boton «cargando» varios segundos."""
        try:
            self._llamar("answerCallbackQuery", {"callback_query_id": cq.get("id"), "text": texto})
        except Exception as exc:  # noqa: BLE001
            log.warning("telegram_entrada.answer_failed", extra={"error": str(exc)[:200]})
