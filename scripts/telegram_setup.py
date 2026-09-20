#!/usr/bin/env python3
"""Termina de configurar Telegram en cuanto el bot reciba su primer mensaje.

El `chat_id` no se puede saber antes de que alguien hable con el bot: Telegram
impide a proposito que un bot escriba a quien no lo ha iniciado. Asi que esto
espera ese primer mensaje, lo lee, y escribe la configuracion.

Uso:
    python scripts/telegram_setup.py            # espera hasta 5 minutos
    python scripts/telegram_setup.py --espera 60
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

VERDE, ROJO, AMARILLO, GRIS, BOLD, OFF = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"
)

ENV = Path("platform/.env")
API = "https://api.telegram.org"


def api(token: str, metodo: str) -> dict:
    with urllib.request.urlopen(f"{API}/bot{token}/{metodo}", timeout=15) as r:
        return json.loads(r.read())


def leer_token() -> str:
    """Del entorno o del .env. Nunca se imprime."""
    if t := os.getenv("ALERTBUS_TELEGRAM_TOKEN"):
        return t
    if ENV.exists():
        for linea in ENV.read_text(encoding="utf-8").splitlines():
            if linea.startswith("ALERTBUS_TELEGRAM_TOKEN="):
                return linea.split("=", 1)[1].strip()
    return ""


def escribir_env(clave: str, valor: str) -> None:
    """Actualiza o anade una clave, sin tocar el resto del fichero."""
    texto = ENV.read_text(encoding="utf-8") if ENV.exists() else ""
    patron = re.compile(rf"^{re.escape(clave)}=.*$", re.M)
    if patron.search(texto):
        texto = patron.sub(f"{clave}={valor}", texto)
    else:
        texto = texto.rstrip("\n") + f"\n{clave}={valor}\n"
    ENV.write_text(texto, encoding="utf-8")


def chats(token: str) -> dict[int, dict]:
    datos = api(token, "getUpdates")
    if not datos.get("ok"):
        return {}
    encontrados = {}
    for u in datos["result"]:
        m = u.get("message") or u.get("edited_message") or u.get("channel_post") or {}
        if c := m.get("chat"):
            encontrados[c["id"]] = c
    return encontrados


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--espera", type=int, default=300, help="segundos a esperar")
    args = parser.parse_args()

    print(f"\n{BOLD}Configuración de Telegram{OFF}\n")

    token = leer_token()
    if not token:
        print(f"{ROJO}No hay token.{OFF}")
        print(f"{GRIS}  Ponlo en platform/.env como ALERTBUS_TELEGRAM_TOKEN=…{OFF}\n")
        return 1

    try:
        yo = api(token, "getMe")
    except Exception as exc:  # noqa: BLE001
        print(f"{ROJO}No se pudo hablar con Telegram: {exc}{OFF}\n")
        return 1
    if not yo.get("ok"):
        print(f"{ROJO}Token rechazado: {yo.get('description')}{OFF}\n")
        return 1

    usuario = yo["result"]["username"]
    print(f"  {VERDE}OK  {OFF} bot @{usuario}")

    ya = chats(token)
    if not ya:
        print(f"\n{AMARILLO}El bot todavía no tiene conversación.{OFF}")
        print(f"{GRIS}  Telegram impide que un bot escriba a quien no lo ha iniciado.")
        print(f"  Abre Telegram, busca {BOLD}@{usuario}{OFF}{GRIS} y pulsa {BOLD}Iniciar{OFF}{GRIS}.")
        print(f"  Esperando hasta {args.espera}s…{OFF}", flush=True)

    limite = time.time() + args.espera
    while not ya and time.time() < limite:
        time.sleep(3)
        ya = chats(token)

    if not ya:
        print(f"\n{ROJO}Se agotó la espera sin ningún mensaje.{OFF}")
        print(f"{GRIS}  Pulsa Iniciar en @{usuario} y vuelve a ejecutarlo.{OFF}\n")
        return 1

    if len(ya) > 1:
        print(f"\n{AMARILLO}Hay varias conversaciones; se usa la primera.{OFF}")
        for cid, c in ya.items():
            print(f"{GRIS}  {cid}  {c.get('type')}  "
                  f"{c.get('title') or c.get('first_name','')}{OFF}")

    chat_id, chat = next(iter(ya.items()))
    nombre = chat.get("title") or " ".join(
        filter(None, [chat.get("first_name"), chat.get("last_name")])
    )
    print(f"  {VERDE}OK  {OFF} conversación con {nombre} (chat_id {chat_id})")

    escribir_env("ALERTBUS_TELEGRAM_CHAT_ID", str(chat_id))
    # `telegram` primero: es el canal que mira una persona.
    escribir_env("ALERTBUS_SINKS", "console,json,telegram")
    print(f"  {VERDE}OK  {OFF} escrito en platform/.env")

    print(f"\n{BOLD}Ahora:{OFF}")
    print("  make up && make channel-test\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
