#!/usr/bin/env python3
"""Dead man's switch: avisa si Argus deja de mirar. (F2-10)

El problema que resuelve es el unico que la plataforma no puede resolverse a si
misma: **si Argus cae, deja de avisar, y su silencio es indistinguible de que
todo va bien.** Toda la deteccion del sistema se apoya en que el sistema
funciona.

Por eso este fichero tiene tres restricciones que parecen excesivas y no lo son:

 1. **Sin dependencias.** Solo la libreria estandar de Python. Si dependiera de
    algo que se instala, compartiria modos de fallo con lo que vigila.

 2. **Fuera del compose.** Corre como tarea del sistema (launchd en macOS, cron
    o systemd en Linux). Un vigilante dentro del contenedor que vigila se cae
    con el.

 3. **Idealmente en OTRA maquina.** En la misma maquina detecta que el servicio
    murio; en otra detecta ademas que la maquina murio, que es el caso en que
    mas falta hace.

Uso:
    python3 deadman/deadman.py --config deadman/deadman.json
    python3 deadman/deadman.py --config ... --test   # fuerza un aviso de prueba
"""

from __future__ import annotations

import argparse
import json
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.request
from email.message import EmailMessage
from pathlib import Path

ESTADO_POR_DEFECTO = Path.home() / ".argus-deadman-state.json"


# --- Comprobaciones ----------------------------------------------------------


def comprobar(url: str, timeout_s: float) -> tuple[bool, str]:
    inicio = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as r:
            codigo = r.status
    except urllib.error.HTTPError as exc:
        codigo = exc.code
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"

    ms = int((time.perf_counter() - inicio) * 1000)
    if codigo >= 500:
        return False, f"HTTP {codigo} en {ms} ms"
    return True, f"HTTP {codigo} en {ms} ms"


# --- Avisos ------------------------------------------------------------------


def avisar_whatsapp(cfg: dict, asunto: str, cuerpo: str) -> str:
    """WhatsApp es el canal correcto para esto: si la plataforma esta caida,
    Google Chat podria seguir funcionando pero nadie lo esta mirando a las tres
    de la manana."""
    datos = json.dumps({
        "messaging_product": "whatsapp",
        "to": cfg["to"],
        "type": "template",
        "template": {
            "name": cfg.get("template", "argus_incidente"),
            "language": {"code": cfg.get("language", "es")},
            "components": [{
                "type": "body",
                "parameters": [
                    {"type": "text", "text": asunto[:180]},
                    {"type": "text", "text": cuerpo[:120]},
                ],
            }],
        },
    }).encode()

    peticion = urllib.request.Request(
        f"https://graph.facebook.com/v21.0/{cfg['phone_id']}/messages",
        data=datos, method="POST",
        headers={"Authorization": f"Bearer {cfg['token']}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(peticion, timeout=15):
        return "whatsapp"


def avisar_email(cfg: dict, asunto: str, cuerpo: str) -> str:
    mensaje = EmailMessage()
    mensaje["Subject"] = asunto
    mensaje["From"] = cfg["from"]
    mensaje["To"] = ", ".join(cfg["to"]) if isinstance(cfg["to"], list) else cfg["to"]
    mensaje.set_content(cuerpo)

    contexto = ssl.create_default_context()
    with smtplib.SMTP(cfg["host"], cfg.get("port", 587), timeout=20) as servidor:
        if cfg.get("tls", True):
            servidor.starttls(context=contexto)
        if cfg.get("user"):
            servidor.login(cfg["user"], cfg["password"])
        servidor.send_message(mensaje)
    return "email"


def avisar_telegram(cfg: dict, asunto: str, cuerpo: str) -> str:
    """El canal del piloto, y el unico que hoy esta configurado de verdad.

    Un vigilante que no sabe hablar por el unico canal que escucha alguien es un
    centinela mudo: se instala, corre, detecta la caida y avisa a nadie.

    Sin dependencias, como el resto del fichero: `urllib` y nada mas. Y sin
    `parse_mode`, a proposito — aqui el texto es lo que sea que haya fallado, y
    un HTML mal formado devolveria 400 justo en el momento en que el aviso
    importa.
    """
    datos = json.dumps({
        "chat_id": cfg["chat_id"],
        "text": f"{asunto}\n\n{cuerpo}",
        "disable_web_page_preview": True,
    }).encode()
    peticion = urllib.request.Request(
        f"https://api.telegram.org/bot{cfg['token']}/sendMessage",
        data=datos, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(peticion, timeout=15):
        return "telegram"


def avisar_gchat(cfg: dict, asunto: str, cuerpo: str) -> str:
    datos = json.dumps({"text": f"*{asunto}*\n{cuerpo}"}).encode()
    peticion = urllib.request.Request(
        cfg["webhook"], data=datos, method="POST",
        headers={"Content-Type": "application/json; charset=UTF-8"},
    )
    with urllib.request.urlopen(peticion, timeout=15):
        return "gchat"


CANALES = {
    "telegram": avisar_telegram,
    "whatsapp": avisar_whatsapp,
    "email": avisar_email,
    "gchat": avisar_gchat,
}


def avisar(cfg: dict, asunto: str, cuerpo: str) -> list[str]:
    """Avisa por TODOS los canales configurados.

    Todos y no el primero que funcione: si la plataforma esta caida, no se sabe
    cual de los canales sigue en pie. Duplicar un aviso es molesto; no recibirlo
    es el fallo que este fichero existe para evitar.
    """
    enviados = []
    for nombre, ajustes in (cfg.get("avisos") or {}).items():
        funcion = CANALES.get(nombre)
        if funcion is None or not ajustes:
            continue
        try:
            enviados.append(funcion(ajustes, asunto, cuerpo))
        except Exception as exc:  # noqa: BLE001
            print(f"  aviso por {nombre} fallo: {exc}", file=sys.stderr)
    return enviados


# --- Estado ------------------------------------------------------------------


def cargar_estado(ruta: Path) -> dict:
    try:
        return json.loads(ruta.read_text())
    except Exception:  # noqa: BLE001
        return {"fallos": 0, "avisado": False, "ultimo_ok": None}


def guardar_estado(ruta: Path, estado: dict) -> None:
    try:
        ruta.write_text(json.dumps(estado))
    except Exception as exc:  # noqa: BLE001
        print(f"  no se pudo guardar el estado: {exc}", file=sys.stderr)


# --- Principal ---------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).parent / "deadman.json")
    parser.add_argument("--test", action="store_true", help="Fuerza un aviso, para comprobar los canales")
    args = parser.parse_args()

    if not args.config.exists():
        print(f"Falta la configuracion: {args.config}", file=sys.stderr)
        print("Copia deadman.json.example y rellenalo.", file=sys.stderr)
        return 2

    cfg = json.loads(args.config.read_text())
    estado_path = Path(cfg.get("estado", ESTADO_POR_DEFECTO)).expanduser()

    if args.test:
        enviados = avisar(cfg, "🟢 Argus · prueba del dead man's switch",
                          "Esto es una prueba. Si lo recibes, el vigilante puede avisarte.")
        print(f"aviso de prueba enviado por: {', '.join(enviados) or 'ningun canal'}")
        return 0 if enviados else 1

    estado = cargar_estado(estado_path)
    umbral = int(cfg.get("fallos_para_avisar", 2))

    fallos = []
    for objetivo in cfg["objetivos"]:
        ok, detalle = comprobar(objetivo["url"], float(cfg.get("timeout_s", 10)))
        marca = "OK " if ok else "MAL"
        print(f"  {marca} {objetivo['nombre']:24} {detalle}")
        if not ok:
            fallos.append(f"{objetivo['nombre']}: {detalle}")

    ahora = time.strftime("%Y-%m-%d %H:%M:%S")

    if not fallos:
        # Recuperacion: se avisa igual que la caida, o nadie sabe que volvio.
        if estado.get("avisado"):
            avisar(cfg, "🟢 Argus ha vuelto",
                   f"La plataforma responde de nuevo.\nComprobado: {ahora}")
            print("  recuperacion avisada")
        guardar_estado(estado_path, {"fallos": 0, "avisado": False, "ultimo_ok": ahora})
        return 0

    estado["fallos"] = estado.get("fallos", 0) + 1
    print(f"  {len(fallos)} objetivo(s) caido(s), fallo consecutivo {estado['fallos']}")

    if estado["fallos"] < umbral:
        guardar_estado(estado_path, estado)
        return 0

    if not estado.get("avisado"):
        detalle = "\n".join(f"· {f}" for f in fallos)
        ultimo = estado.get("ultimo_ok") or "desconocido"
        enviados = avisar(
            cfg,
            "🔴 Argus no responde",
            f"La plataforma de observabilidad no responde.\n\n{detalle}\n\n"
            f"Última vez que respondió: {ultimo}\n"
            f"Comprobado: {ahora}\n\n"
            "Mientras tanto NO hay detección de incidentes: el silencio de las "
            "aplicaciones no significa que estén bien.",
        )
        print(f"  aviso enviado por: {', '.join(enviados) or 'NINGUN CANAL'}")
        estado["avisado"] = True

    guardar_estado(estado_path, estado)
    return 1


if __name__ == "__main__":
    sys.exit(main())
