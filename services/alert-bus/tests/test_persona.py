"""La persona en el ciclo del incidente: acuse, feedback, escalado y linea de tiempo (B-46, B-47, B-49)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from alert_bus.engine import Engine
from alert_bus.sinks import MemorySink
from alert_bus.sinks.render import cuerpo
from alert_bus.sinks.telegram import botones
from alert_bus.store import IncidentStore
from alert_bus.telegram_entrada import TelegramEntrada
from argus_schemas import IncidentState, Signal, SignalKind


def _senal(componente: str = "idp-api", signature: str = "503") -> Signal:
    return Signal(kind=SignalKind.ERROR, app="intelligent-document-platform", component=componente,
                  environment="mac-dev", signature=signature, title="falla")


def _motor(registry, tmp_path, sink=None, **kw) -> Engine:
    return Engine(registry, [sink or MemorySink()], store=IncidentStore(tmp_path / "i.sqlite"), **kw)


def _tipos(engine: Engine, incidente_id: str) -> list[str]:
    return [e["tipo"] for e in engine.timeline(incidente_id)]


# --- B-49: la linea de tiempo ---------------------------------------------------


def test_la_linea_de_tiempo_cuenta_el_ciclo_entero(registry, tmp_path) -> None:
    e = _motor(registry, tmp_path, resolve_after_s=1)
    e.ingest([_senal()])
    inc = e.open_incidents()[0]
    e.enrich(inc.fingerprint, root_cause="el destino cayo", confidence="alta")
    e.acknowledge(inc.id, por="telegram:emeric")
    e.feedback(inc.id, causa="correcta")
    inc.last_seen_at = datetime.now(UTC) - timedelta(seconds=5)
    e.sweep()

    assert _tipos(e, inc.id) == ["abierto", "notificado", "diagnosticado", "reconocido", "feedback", "resuelto"]
    cuandos = [ev["cuando"] for ev in e.timeline(inc.id)]
    assert cuandos == sorted(cuandos), "todas las horas son del mismo reloj y van en orden"


def test_sin_almacen_no_hay_linea_de_tiempo_pero_todo_funciona(registry) -> None:
    e = Engine(registry, [MemorySink()])
    e.ingest([_senal()])
    inc = e.open_incidents()[0]
    assert e.acknowledge(inc.id) is not None
    assert e.timeline(inc.id) == []


# --- B-47: el acuse -------------------------------------------------------------


def test_el_acuse_guarda_la_hora_de_la_primera_vez(registry, tmp_path) -> None:
    e = _motor(registry, tmp_path)
    e.ingest([_senal()])
    inc = e.open_incidents()[0]
    primero = e.acknowledge(inc.id).acknowledged_at
    segundo = e.acknowledge(inc.id).acknowledged_at

    assert primero is not None and primero == segundo, "pulsar otra vez no puede mover el MTTA"
    assert _tipos(e, inc.id).count("reconocido") == 1
    assert inc.state is IncidentState.ACKNOWLEDGED


def test_el_acuse_sobrevive_a_un_reinicio(registry, tmp_path) -> None:
    e = _motor(registry, tmp_path)
    e.ingest([_senal()])
    inc = e.open_incidents()[0]
    e.acknowledge(inc.id)
    despues = _motor(registry, tmp_path)
    assert despues.open_incidents()[0].acknowledged_at == inc.acknowledged_at


# --- B-47: el escalado ----------------------------------------------------------


def test_el_escalado_viene_apagado(registry, tmp_path) -> None:
    sink = MemorySink()
    e = _motor(registry, tmp_path, sink=sink)
    e.ingest([_senal()])
    inc = e.open_incidents()[0]
    inc.notified_at = datetime.now(UTC) - timedelta(hours=5)
    assert e.escalate() == []


def test_escala_un_page_sin_acuse_con_su_plazo_y_su_tope(registry, tmp_path) -> None:
    sink = MemorySink()
    e = _motor(registry, tmp_path, sink=sink, escalar_tras_s=900, escalados_max=2)
    e.ingest([_senal()])
    inc = e.open_incidents()[0]
    notificado = datetime.now(UTC) - timedelta(minutes=16)
    inc.notified_at = notificado

    assert e.escalate() == [inc]
    assert e.escalate() == [], "el segundo espera otros 15 minutos"
    inc.notified_at = datetime.now(UTC) - timedelta(minutes=31)
    assert e.escalate() == [inc]
    inc.notified_at = datetime.now(UTC) - timedelta(hours=3)
    assert e.escalate() == [], "tope de dos escalados"

    assert inc.escalations == 2
    assert len(sink.opened) == 3, "cada escalado es un aviso NUEVO: Telegram no suena al editar"
    assert _tipos(e, inc.id).count("notificado") == 1, "escalar no cambia la hora de la notificacion"


def test_no_se_escala_lo_reconocido_ni_un_sintoma(registry, tmp_path) -> None:
    e = _motor(registry, tmp_path, escalar_tras_s=60)
    e.ingest([_senal()])
    inc = e.open_incidents()[0]
    e.acknowledge(inc.id)
    inc.notified_at = datetime.now(UTC) - timedelta(hours=1)
    assert e.escalate() == []


# --- B-46: el feedback ----------------------------------------------------------


def test_el_feedback_llega_tambien_despues_del_cierre(registry, tmp_path) -> None:
    e = _motor(registry, tmp_path, resolve_after_s=1)
    e.ingest([_senal()])
    inc = e.open_incidents()[0]
    inc.last_seen_at = datetime.now(UTC) - timedelta(seconds=5)
    e.sweep()
    assert e.open_incidents() == []

    hecho = e.feedback(inc.id, por="telegram:emeric", causa_real="  el certificado   caduco ")
    assert hecho.feedback["causa"] == "incorrecta"
    assert hecho.feedback["causa_real"] == "el certificado caduco"
    historico = IncidentStore(tmp_path / "i.sqlite").obtener(inc.id)
    assert historico.feedback["causa_real"] == "el certificado caduco", "se guarda: es lo que leera la memoria"


def test_el_feedback_tiene_vocabulario_cerrado(registry, tmp_path) -> None:
    e = _motor(registry, tmp_path)
    e.ingest([_senal()])
    inc = e.open_incidents()[0]
    with pytest.raises(ValueError):
        e.feedback(inc.id, causa="mas o menos")
    assert e.feedback(inc.id) is None, "un feedback vacio no se anota"
    assert e.feedback("no-existe", causa="correcta") is None


# --- El aviso -------------------------------------------------------------------


def test_el_aviso_dice_lo_que_hizo_la_persona(registry, tmp_path) -> None:
    e = _motor(registry, tmp_path)
    e.ingest([_senal()])
    inc = e.open_incidents()[0]
    assert "ESTADO" not in dict(cuerpo(inc))
    inc.escalations = 1
    assert "Sin acuse" in dict(cuerpo(inc))["ESTADO"]
    e.acknowledge(inc.id)
    e.feedback(inc.id, causa_real="el certificado caduco", util=True)
    estado = dict(cuerpo(inc))["ESTADO"]
    assert "Reconocido" in estado and "el certificado caduco" in estado and "útil" in estado


def test_los_botones_desaparecen_cuando_se_usan(registry, tmp_path) -> None:
    e = _motor(registry, tmp_path)
    e.ingest([_senal()])
    inc = e.open_incidents()[0]

    def datos() -> list[str]:
        teclado = botones(inc) or {"inline_keyboard": []}
        return [b["callback_data"].split(":")[0] for fila in teclado["inline_keyboard"] for b in fila]

    assert datos() == ["ack", "util", "ruido"], "sin causa no se puede juzgar la causa"
    e.enrich(inc.fingerprint, root_cause="el destino cayo", confidence="media")
    assert datos() == ["ack", "ok", "no", "util", "ruido"]
    e.acknowledge(inc.id)
    e.feedback(inc.id, causa="correcta", util=False)
    assert botones(inc) is None
    assert all(len(b["callback_data"].encode()) <= 64 for fila in
               (botones(e.open_incidents()[0]) or {"inline_keyboard": []})["inline_keyboard"] for b in fila)


# --- La entrada de Telegram -----------------------------------------------------


class SinkFalso:
    name = "telegram"

    def __init__(self) -> None:
        self.actualizados: list[int] = []
        self.preguntas: list[str] = []
        self.dichos: list[str] = []

    def actualizar(self, message_id, incident) -> bool:
        self.actualizados.append(message_id)
        return True

    def preguntar(self, texto, *, responder_a=None):
        self.preguntas.append(texto)
        return 900

    def decir(self, texto, *, responder_a=None) -> None:
        self.dichos.append(texto)


def _entrada(engine) -> tuple[TelegramEntrada, SinkFalso, list[tuple[str, dict]]]:
    sink, llamadas = SinkFalso(), []
    entrada = TelegramEntrada("t", "42", engine, sink)
    entrada._llamar = lambda metodo, datos, **kw: llamadas.append((metodo, datos)) or {"ok": True}
    return entrada, sink, llamadas


def _pulsar(data: str, chat: int = 42) -> dict:
    return {"update_id": 1, "callback_query": {"id": "cq", "data": data, "from": {"username": "emeric"},
                                               "message": {"message_id": 77, "chat": {"id": chat}}}}


def test_un_boton_de_otro_chat_no_hace_nada(registry, tmp_path) -> None:
    e = _motor(registry, tmp_path)
    e.ingest([_senal()])
    inc = e.open_incidents()[0]
    entrada, sink, llamadas = _entrada(e)
    entrada.procesar(_pulsar(f"ack:{inc.id}", chat=666))

    assert inc.acknowledged_at is None and entrada.stats["ajenas"] == 1
    assert sink.actualizados == [] and llamadas[0][0] == "answerCallbackQuery"


def test_lo_veo_acusa_recibo_y_rehace_el_mensaje(registry, tmp_path) -> None:
    e = _motor(registry, tmp_path)
    e.ingest([_senal()])
    inc = e.open_incidents()[0]
    entrada, sink, llamadas = _entrada(e)
    entrada.procesar(_pulsar(f"ack:{inc.id}"))

    assert inc.acknowledged_at is not None
    assert sink.actualizados == [77]
    assert e.timeline(inc.id)[-1]["detalle"] == "telegram:emeric"
    assert llamadas == [("answerCallbackQuery", {"callback_query_id": "cq", "text": "Reconocido."})]


def test_incorrecta_pregunta_la_causa_real_y_la_respuesta_se_anota(registry, tmp_path) -> None:
    e = _motor(registry, tmp_path)
    e.ingest([_senal()])
    inc = e.open_incidents()[0]
    e.enrich(inc.fingerprint, root_cause="el destino cayo", confidence="media")
    entrada, sink, _ = _entrada(e)
    entrada.procesar(_pulsar(f"no:{inc.id}"))
    assert inc.feedback["causa"] == "incorrecta" and len(sink.preguntas) == 1

    # Un mensaje cualquiera en el chat no es asunto del alert-bus.
    entrada.procesar({"update_id": 2, "message": {"message_id": 5, "chat": {"id": 42}, "text": "hola"}})
    assert "causa_real" not in inc.feedback
    # La respuesta a la pregunta, si.
    entrada.procesar({"update_id": 3, "message": {
        "message_id": 6, "chat": {"id": 42}, "text": "el certificado caduco",
        "reply_to_message": {"message_id": 900}, "from": {"username": "emeric"}}})
    assert inc.feedback["causa_real"] == "el certificado caduco"
    assert entrada.stats["respuestas"] == 1


def test_un_boton_desconocido_no_hace_nada(registry, tmp_path) -> None:
    e = _motor(registry, tmp_path)
    entrada, _, _ = _entrada(e)
    entrada.procesar(_pulsar("borra:todo"))
    assert entrada.stats["desconocidas"] == 1


# --- La API ---------------------------------------------------------------------


def test_api_de_linea_de_tiempo_y_feedback(registry, tmp_path) -> None:
    from alert_bus.app import create_app
    from alert_bus.config import Settings
    from fastapi.testclient import TestClient

    e = _motor(registry, tmp_path)
    e.ingest([_senal()])
    inc = e.open_incidents()[0]
    cliente = TestClient(create_app(Settings(token="s3creto"), engine=e))

    assert cliente.post(f"/incidents/{inc.id}/feedback", json={"causa": "correcta"}).status_code == 401
    r = cliente.post(f"/incidents/{inc.id}/feedback", json={"causa": "correcta"},
                     headers={"Authorization": "Bearer s3creto"})
    assert r.status_code == 200 and r.json()["feedback"]["causa"] == "correcta"
    assert cliente.post(f"/incidents/{inc.id}/feedback", json={"causa": "quiza"},
                        headers={"Authorization": "Bearer s3creto"}).status_code == 422
    tipos = [ev["tipo"] for ev in cliente.get(f"/incidents/{inc.id}/timeline").json()]
    assert tipos == ["abierto", "notificado", "feedback"]
