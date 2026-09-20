"""Los canales de notificacion.

Google Chat y SMTP se prueban contra servidores LOCALES de verdad, no contra
mocks: lo que hay que verificar es que el payload que sale por el socket es el
que el proveedor espera, y un mock solo confirma que llamamos a nuestro propio
codigo.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest
from alert_bus.sinks import (
    Dispatcher,
    EmailSink,
    FailingSink,
    GoogleChatSink,
    MemorySink,
    WhatsAppSink,
    cuerpo,
    resumen,
    texto_plano,
    titular,
)
from argus_schemas import Incident, Severity, Signal, SignalKind


@pytest.fixture
def incidente() -> Incident:
    senal = Signal(
        kind=SignalKind.ERROR,
        app="intelligent-document-platform",
        component="idp-ocr",
        environment="mac-dev",
        signature="timeout",
        title="timeout en idp-ocr",
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
    )
    inc = Incident.from_signal(senal, Severity.PAGE, "abc123")
    inc.count = 1247
    inc.thread_key = "argus-huella1"
    return inc


@pytest.fixture
def incidente_investigado(incidente: Incident) -> Incident:
    incidente.root_cause = "El despliegue a9f3c21 bajó el timeout de 30s a 5s"
    incidente.confidence = "alta"
    incidente.evidence = ["1.247 spans ocr.extract con status=ERROR desde 14:32"]
    incidente.similar_incidents = ["#47 (12 mar): mismo patrón tras bajar un timeout"]
    return incidente


# --- Redaccion ---------------------------------------------------------------


def test_el_titular_dice_donde_que_y_en_que_estado(incidente: Incident) -> None:
    """Es lo unico que se lee en una notificacion del movil."""
    texto = titular(incidente)
    assert "intelligent-document-platform/idp-ocr" in texto
    assert "timeout" in texto
    assert "investigando" in texto


def test_el_titular_cambia_cuando_hay_causa(incidente_investigado: Incident) -> None:
    assert "causa identificada" in titular(incidente_investigado)


def test_el_resumen_cuantifica(incidente: Incident) -> None:
    """"1.247 señales" es lo que distingue un fallo puntual de una caida."""
    assert "1247 señales" in resumen(incidente)


def test_el_orden_de_las_secciones_no_es_decorativo(incidente_investigado: Incident) -> None:
    """Causa primero, porque es lo que se busca. Lo visto antes despues, porque
    puede ahorrar la investigacion entera."""
    encabezados = [h for h, _ in cuerpo(incidente_investigado)]
    assert encabezados[0].startswith("CAUSA PROBABLE")
    assert encabezados[1] == "VISTO ANTES"
    assert "EVIDENCIA" in encabezados


def test_un_incidente_sin_investigar_no_inventa_secciones(incidente: Incident) -> None:
    encabezados = [h for h, _ in cuerpo(incidente)]
    assert "CAUSA PROBABLE" not in " ".join(encabezados)
    assert encabezados == ["TRAZA"]


def test_el_texto_plano_incluye_la_traza(incidente_investigado: Incident) -> None:
    """Es lo que conecta el aviso con la traza completa del camino frio."""
    assert "4bf92f3577b34da6a3ce929d0e0e4736" in texto_plano(incidente_investigado)


# --- Google Chat -------------------------------------------------------------


class _CapturaHTTP(BaseHTTPRequestHandler):
    # En la clase porque BaseHTTPRequestHandler se instancia por peticion y no
    # admite pasarle estado. Es el patron estandar para capturar en tests.
    recibidas: ClassVar[list[dict]] = []

    def do_POST(self) -> None:
        largo = int(self.headers.get("Content-Length", 0))
        cuerpo_bruto = self.rfile.read(largo)
        type(self).recibidas.append({
            "path": self.path,
            "payload": json.loads(cuerpo_bruto),
            "content_type": self.headers.get("Content-Type", ""),
        })
        respuesta = json.dumps({"name": "spaces/AAA/messages/MSG1"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(respuesta)))
        self.end_headers()
        self.wfile.write(respuesta)

    def log_message(self, *args) -> None:  # silencio
        pass


@pytest.fixture
def servidor_chat():
    _CapturaHTTP.recibidas = []
    servidor = HTTPServer(("127.0.0.1", 0), _CapturaHTTP)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    yield f"http://127.0.0.1:{servidor.server_port}/webhook", _CapturaHTTP
    servidor.shutdown()


def test_gchat_envia_una_tarjeta_v2(servidor_chat, incidente: Incident) -> None:
    """Cards V2 y no el formato legacy, que esta obsoleto."""
    url, captura = servidor_chat
    GoogleChatSink(url).send(incidente, update=False)

    (peticion,) = captura.recibidas
    assert "cardsV2" in peticion["payload"]
    assert peticion["payload"]["cardsV2"][0]["cardId"] == incidente.fingerprint


def test_gchat_incluye_texto_de_respaldo(servidor_chat, incidente: Incident) -> None:
    """Es lo que llega a la notificacion del movil y lo que se ve si el cliente
    no renderiza la tarjeta."""
    url, captura = servidor_chat
    GoogleChatSink(url).send(incidente, update=False)
    assert "timeout" in captura.recibidas[0]["payload"]["text"]


def test_gchat_agrupa_por_thread_key(servidor_chat, incidente: Incident) -> None:
    """Es lo que hace que el informe caiga en el mismo hilo que el aviso."""
    url, captura = servidor_chat
    GoogleChatSink(url).send(incidente, update=False)
    assert f"threadKey={incidente.thread_key}" in captura.recibidas[0]["path"]


def test_gchat_lleva_botones(servidor_chat, incidente: Incident) -> None:
    """Son la razon de que este sea el canal principal: permiten reconocer sin
    salir del chat, y mas adelante aprobar remediaciones."""
    url, captura = servidor_chat
    GoogleChatSink(url).send(incidente, update=False)

    widgets = captura.recibidas[0]["payload"]["cardsV2"][0]["card"]["sections"][0]["widgets"]
    botones = [w for w in widgets if "buttonList" in w]
    assert botones
    etiquetas = [b["text"] for b in botones[0]["buttonList"]["buttons"]]
    assert "Reconocer" in etiquetas


def test_gchat_escapa_el_html(servidor_chat, incidente: Incident) -> None:
    """Las tarjetas interpretan un subconjunto de HTML: un titulo con `<` lo
    romperia."""
    url, captura = servidor_chat
    incidente.title = "fallo en <script>alert(1)</script>"
    GoogleChatSink(url).send(incidente, update=False)

    enviado = json.dumps(captura.recibidas[0]["payload"])
    assert "<script>" not in enviado
    assert "&lt;script&gt;" in enviado


def test_gchat_recuerda_el_mensaje_para_poder_editarlo(servidor_chat, incidente_investigado: Incident) -> None:
    """La divulgacion progresiva necesita editar, no anadir."""
    url, _ = servidor_chat
    sink = GoogleChatSink(url)
    sink.send(incidente_investigado, update=False)
    assert sink._mensajes[incidente_investigado.thread_key] == "spaces/AAA/messages/MSG1"


# --- WhatsApp ----------------------------------------------------------------


def test_whatsapp_ignora_las_actualizaciones(incidente_investigado: Incident) -> None:
    """Seria un segundo mensaje DE PAGO que dice casi lo mismo.

    Desde el 1 de octubre de 2026 ni siquiera las respuestas dentro de la
    ventana de 24 h son gratuitas.
    """
    sink = WhatsAppSink(phone_number_id="1", access_token="t", recipients=["+34600000000"])
    sink.send(incidente_investigado, update=True)   # no debe lanzar ni enviar


def test_whatsapp_solo_para_severidad_page(incidente: Incident) -> None:
    """No es un canal de volumen: cuesta por mensaje."""
    incidente.severity = Severity.TICKET
    sink = WhatsAppSink(phone_number_id="1", access_token="t", recipients=["+34600000000"])
    sink.send(incidente, update=False)   # se omite sin intentar la llamada


def test_whatsapp_sin_destinatarios_no_revienta(incidente: Incident) -> None:
    WhatsAppSink(phone_number_id="1", access_token="t", recipients=[]).send(incidente, update=False)


# --- Correo ------------------------------------------------------------------


def test_email_usa_cabeceras_de_conversacion(incidente: Incident, monkeypatch) -> None:
    """Message-ID e In-Reply-To son lo que hace que el cliente agrupe el aviso
    inicial y el informe del agente en la MISMA conversacion.

    Sin ellas, la divulgacion progresiva produce dos correos sueltos.
    """
    enviados: list = []

    class SMTPFalso:
        def __init__(self, *a, **k): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): ...
        def login(self, *a): ...
        def send_message(self, msg): enviados.append(msg)

    monkeypatch.setattr("alert_bus.sinks.email.smtplib.SMTP", SMTPFalso)

    sink = EmailSink(host="localhost", recipients=["ops@example.com"])
    sink.send(incidente, update=False)
    sink.send(incidente, update=True)

    inicial, actualizacion = enviados
    assert inicial["Message-ID"] == f"<{incidente.fingerprint}@argus>"
    assert actualizacion["In-Reply-To"] == f"<{incidente.fingerprint}@argus>"
    assert actualizacion["Subject"].startswith("Actualización:")


def test_email_manda_texto_y_html(incidente_investigado: Incident, monkeypatch) -> None:
    enviados: list = []

    class SMTPFalso:
        def __init__(self, *a, **k): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): ...
        def login(self, *a): ...
        def send_message(self, msg): enviados.append(msg)

    monkeypatch.setattr("alert_bus.sinks.email.smtplib.SMTP", SMTPFalso)
    EmailSink(host="localhost", recipients=["ops@example.com"]).send(incidente_investigado, update=False)

    tipos = {p.get_content_type() for p in enviados[0].walk()}
    assert "text/plain" in tipos
    assert "text/html" in tipos


def test_email_sin_destinatarios_avisa_pero_no_revienta(incidente: Incident) -> None:
    EmailSink(host="localhost", recipients=[]).send(incidente, update=False)


# --- Despacho asincrono ------------------------------------------------------


def test_el_despacho_no_bloquea_la_ingesta(incidente: Incident) -> None:
    """La razon de tener los canales en proceso en vez de en un servicio (D-029).

    Un canal que tarda no puede retrasar la deteccion del siguiente incidente.
    """
    import time

    class SinkLento:
        name = "lento"

        def send(self, incident, *, update): time.sleep(0.5)

    despachador = Dispatcher(workers=2)
    despachador.start()
    try:
        inicio = time.perf_counter()
        despachador.submit([SinkLento()], incidente, update=False)
        encolar_ms = (time.perf_counter() - inicio) * 1000
        assert encolar_ms < 50, f"encolar tardo {encolar_ms:.0f} ms; deberia ser inmediato"
    finally:
        despachador.stop(timeout_s=2)


def test_un_canal_caido_no_impide_que_los_demas_reciban(incidente: Incident) -> None:
    """Con UN SOLO worker, para que la propiedad se vea de verdad.

    Un canal muerto ahora se reintenta (D-080), y el reintento espera segundos.
    Si esa espera ocurriera dentro del hilo trabajador, con un worker el canal
    sano no recibiria nada hasta que el muerto se rindiera. Por eso el
    reintento se REPROGRAMA en vez de dormir: aqui se comprueba que el sano
    cobra enseguida, sin esperar al muerto.
    """
    memoria = MemorySink()
    despachador = Dispatcher(workers=1)
    despachador.start()
    try:
        despachador.submit([FailingSink(), memoria], incidente, update=False)
        # Deliberadamente corto: menos que el primer reintento del canal roto.
        limite = time.monotonic() + 1.0
        while not memoria.opened and time.monotonic() < limite:
            time.sleep(0.01)
        assert len(memoria.opened) == 1, "el canal sano espero al muerto"

        # Y el fallo acaba contandose cuando se agotan los intentos.
        despachador.drain(timeout_s=15)
    finally:
        despachador.stop(timeout_s=2)

    assert despachador.stats["fallidos"] == 1
    assert despachador.por_canal["failing"]["fallidos"] == 1


def test_la_cola_esta_acotada_y_cuenta_los_descartes(incidente: Incident) -> None:
    """Quedarse sin memoria en el proceso que detecta incidentes es peor que
    perder notificaciones. Pero descartar en silencio es peor que las dos."""
    despachador = Dispatcher(workers=1, max_queue=5)
    # Sin arrancar los hilos: nada consume, la cola se llena.
    for _ in range(20):
        despachador.submit([MemorySink()], incidente, update=False)

    assert despachador.stats["descartados"] == 15


def test_el_despacho_cuenta_por_canal(incidente: Incident) -> None:
    """El total no distingue consola de chat.

    "2 enviados" con tres sinks cargados no dice si el que mira una persona
    fue uno de los dos. Sin el desglose, la prueba de canales daba verde con el
    aviso saliendo solo por consola.
    """
    from alert_bus.sinks import FailingSink, InlineDispatcher, MemorySink

    bueno, malo = MemorySink(), FailingSink()
    despacho = InlineDispatcher()

    despacho.submit([bueno, malo], incidente, update=False)

    assert despacho.por_canal[bueno.name]["enviados"] == 1
    assert despacho.por_canal[malo.name]["fallidos"] == 1
    assert despacho.por_canal[bueno.name]["fallidos"] == 0


def test_gchat_conserva_las_credenciales_al_editar() -> None:
    """`key` y `token` son las credenciales del webhook, y van en la query.

    Perderlas al construir la URL de edicion da un 401, la edicion degrada a
    publicar en el hilo, y la divulgacion progresiva se convierte en un mensaje
    por actualizacion. Como el fallo esta capturado, solo se ve mirando el chat.
    """
    from alert_bus.sinks import GoogleChatSink

    sink = GoogleChatSink(
        "https://chat.googleapis.com/v1/spaces/AAA/messages?key=KKK&token=TTT"
    )
    url = sink._url_de_edicion("spaces/AAA/messages/MMM")

    assert url.startswith("https://chat.googleapis.com/v1/spaces/AAA/messages/MMM?")
    assert "key=KKK" in url
    assert "token=TTT" in url
    assert "updateMask=cardsV2" in url


def test_email_la_actualizacion_lleva_su_propio_message_id(
    incidente_investigado: Incident, monkeypatch
) -> None:
    """Un correo sin `Message-ID` lo rechazan algunos MTA.

    El hilo lo hacen `In-Reply-To` y `References`, no la ausencia de
    identificador propio.
    """
    enviados = []

    class SMTPFalso:
        def __init__(self, *a, **k): ...
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): ...
        def login(self, *a): ...
        def send_message(self, mensaje): enviados.append(mensaje)

    monkeypatch.setattr("smtplib.SMTP", SMTPFalso)
    sink = EmailSink(host="smtp.test", recipients=["a@b.c"])

    sink.send(incidente_investigado, update=False)
    sink.send(incidente_investigado, update=True)

    inicial, actualizacion = enviados
    assert actualizacion["Message-ID"], "toda actualización necesita su propio identificador"
    assert actualizacion["Message-ID"] != inicial["Message-ID"]
    assert actualizacion["In-Reply-To"] == inicial["Message-ID"], "y debe enhebrarse con el inicial"


# --- Telegram ----------------------------------------------------------------


class _CapturaTelegram(BaseHTTPRequestHandler):
    recibidas: ClassVar[list[dict]] = []
    # La API de Telegram devuelve el `message_id`, que es lo que permite editar
    # despues. Se incrementa para que un segundo envio se distinga del primero.
    siguiente_id: ClassVar[int] = 100
    fallar_edicion: ClassVar[bool] = False

    def do_POST(self) -> None:
        largo = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(largo))
        metodo = self.path.rsplit("/", 1)[-1]
        type(self).recibidas.append({"metodo": metodo, "path": self.path, "payload": payload})

        if metodo == "editMessageText" and type(self).fallar_edicion:
            self.send_response(400)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if metodo == "sendMessage":
            type(self).siguiente_id += 1
            resultado = {"message_id": type(self).siguiente_id}
        else:
            resultado = {"message_id": payload.get("message_id")}

        cuerpo_r = json.dumps({"ok": True, "result": resultado}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo_r)))
        self.end_headers()
        self.wfile.write(cuerpo_r)

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def servidor_telegram():
    _CapturaTelegram.recibidas = []
    _CapturaTelegram.siguiente_id = 100
    _CapturaTelegram.fallar_edicion = False
    servidor = HTTPServer(("127.0.0.1", 0), _CapturaTelegram)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    yield f"http://127.0.0.1:{servidor.server_port}", _CapturaTelegram
    servidor.shutdown()


def _telegram(base: str):
    from alert_bus.sinks import TelegramSink
    return TelegramSink("EL-TOKEN", "-100123", api_base=base)


def test_telegram_actualiza_el_mensaje_en_vez_de_mandar_otro(
    servidor_telegram, incidente: Incident, incidente_investigado: Incident
) -> None:
    """Es la razón por la que este canal es mejor que el correo.

    El correo puede enhebrar, pero manda dos mensajes. Telegram EDITA el
    primero, así que el aviso se convierte en el informe y nunca hay dos
    notificaciones del mismo incidente (D-015).
    """
    base, captura = servidor_telegram
    sink = _telegram(base)

    sink.send(incidente, update=False)
    sink.send(incidente_investigado, update=True)

    metodos = [p["metodo"] for p in captura.recibidas]
    assert metodos == ["sendMessage", "editMessageText"]
    # El id editado es el que devolvió el primer envío.
    assert captura.recibidas[1]["payload"]["message_id"] == 101


def test_telegram_si_no_puede_editar_manda_uno_nuevo(
    servidor_telegram, incidente: Incident, incidente_investigado: Incident
) -> None:
    """Perder el informe del agente sería peor que mandar un mensaje de más."""
    base, captura = servidor_telegram
    sink = _telegram(base)

    sink.send(incidente, update=False)
    captura.fallar_edicion = True
    sink.send(incidente_investigado, update=True)

    metodos = [p["metodo"] for p in captura.recibidas]
    assert metodos == ["sendMessage", "editMessageText", "sendMessage"]


def test_telegram_escapa_el_html(servidor_telegram, incidente: Incident) -> None:
    """Un `<` sin escapar devuelve 400 y pierde el aviso entero."""
    base, captura = servidor_telegram
    incidente.title = "fallo en <script>alert(1)</script> & cía"
    _telegram(base).send(incidente, update=False)

    texto = captura.recibidas[0]["payload"]["text"]
    assert "&lt;script&gt;" in texto
    assert "&amp;" in texto
    assert "<script>" not in texto
    # Las etiquetas NUESTRAS sí pasan: son las que dan el formato.
    assert "<b>" in texto


def test_telegram_recorta_lo_que_pasa_del_limite(
    servidor_telegram, incidente_investigado: Incident
) -> None:
    """La API rechaza más de 4096 caracteres.

    Perder el aviso por pasarse de largo sería absurdo, y recortar en silencio
    haría creer que ese era todo el informe.
    """
    base, captura = servidor_telegram
    # La causa raíz entra entera en el cuerpo, así que es por donde se desborda.
    incidente_investigado.root_cause = "causa muy larga. " * 400
    _telegram(base).send(incidente_investigado, update=False)

    texto = captura.recibidas[0]["payload"]["text"]
    assert len(texto) < 4096
    assert "recortado" in texto


def test_telegram_no_pone_el_token_en_el_log(caplog, incidente: Incident) -> None:
    """El token va en la URL, así que un log de la URL es una fuga de credencial."""
    from alert_bus.sinks import TelegramSink

    sink = TelegramSink("TOKEN-SECRETO", "-100", api_base="http://127.0.0.1:1")
    with caplog.at_level("ERROR"), pytest.raises(Exception):
        sink.send(incidente, update=False)

    assert "TOKEN-SECRETO" not in caplog.text


def test_telegram_no_duplica_el_icono_de_severidad(
    servidor_telegram, incidente: Incident
) -> None:
    """`titular()` ya trae el icono; añadirle otro da «🔴 🔴 [app/componente]».

    El formato vive en `render.py` precisamente para que los canales no
    discrepen, y un canal que se lo añade por su cuenta rompe esa propiedad.
    """
    base, captura = servidor_telegram
    _telegram(base).send(incidente, update=False)

    texto = captura.recibidas[0]["payload"]["text"]
    assert texto.count("🔴") == 1
    assert texto.startswith("<b>")
