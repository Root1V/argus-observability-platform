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
from datetime import timezone
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




def _leer_rfc3339(cruda: str):
    """Lee una marca RFC-3339 tolerando NANOsegundos.

    vmalert esta escrito en Go y emite hasta 9 digitos de fraccion
    (`...48.198507418Z`). El `fromisoformat` de Python 3.9 —el interprete con el
    que launchd ejecuta esto— solo acepta 3 o 6, asi que revienta con 9.

    Y no falla siempre, que es lo peor: Go recorta los ceros del final, asi que
    la misma marca tiene 9 digitos unas veces y 6 o 7 otras. El parser funcionaba
    a ratos, y "a ratos" en un vigilante significa avisos equivocados en momentos
    aleatorios (D-088).

    Devuelve `None` si no se puede leer, y quien llama DECIDE que hacer con eso
    en vez de confundirlo con ausencia.
    """
    from datetime import datetime

    texto = cruda.strip().replace("Z", "+00:00")
    # Recorta la fraccion a 6 digitos, que es lo maximo que entiende datetime.
    if "." in texto:
        cabeza, resto = texto.split(".", 1)
        digitos = ""
        for c in resto:
            if c.isdigit():
                digitos += c
            else:
                break
        cola = resto[len(digitos):]
        texto = f"{cabeza}.{digitos[:6]:<06s}{cola}"
    try:
        return datetime.fromisoformat(texto)
    except ValueError:
        return None


def comprobar_frescura_de_reglas(url: str, timeout_s: float, max_atraso_s: float) -> tuple[bool, str]:
    """Comprueba que vmalert esta evaluando AHORA y no en el pasado.

    Existe por un fallo que ninguna alerta podia cazar, porque la victima era el
    propio evaluador: vmalert se quedo evaluando con un reloj **5,4 dias
    atrasado** (D-087). Seguia diciendo `health: ok`, seguia escribiendo sus
    reglas de grabacion, y cada muestra caia cinco dias en el pasado — donde
    ninguna consulta a `now` la encuentra.

    Consecuencias, todas silenciosas:
      · ninguna regla de grabacion era consultable, asi que las alertas de
        burn-rate de SLO —que leen `argus:error_ratio:*`— no podian disparar;
      · las reglas de ausencia se evaluaban contra datos de hace cinco dias, y
        disparaban por series que en aquel momento aun no existian.

    **Y no se puede detectar desde dentro**: una regla que preguntara "¿estoy
    evaluando a la hora correcta?" la evaluaria el mismo reloj atrasado, y desde
    ahi todo parece consistente. Tiene que mirarlo alguien de fuera, que es
    exactamente para lo que existe este fichero.

    Se compara contra la evaluacion MAS RECIENTE de todas las reglas: basta con
    que una este al dia para saber que el evaluador no se ha quedado atras.
    """
    from datetime import datetime

    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as r:
            datos = json.loads(r.read().decode())
    except Exception as exc:  # noqa: BLE001
        return False, f"no se pudo leer las reglas: {type(exc).__name__}: {exc}"

    marcas = []
    ilegibles = 0
    for grupo in datos.get("data", {}).get("groups", []):
        for regla in grupo.get("rules", []):
            cruda = regla.get("lastEvaluation")
            if not cruda:
                continue
            fecha = _leer_rfc3339(cruda)
            if fecha is None:
                ilegibles += 1
            else:
                marcas.append(fecha)

    if ilegibles and not marcas:
        # NO se calla. La primera version hacia `except ValueError: continue` y
        # eso convertia "no se parsear la fecha" en "no hay fechas", que es un
        # diagnostico completamente distinto y mando un aviso equivocado.
        return False, f"{ilegibles} marcas de tiempo ilegibles: revisa el formato de lastEvaluation"

    if not marcas:
        # Pasa de verdad, y es transitorio: justo tras arrancar vmalert responde
        # a `/api/v1/rules` pero aun no ha evaluado nada. Se cuenta como fallo a
        # proposito —un vmalert que responde y nunca evalua es exactamente el
        # tipo de averia silenciosa que este fichero existe para cazar— y lo que
        # absorbe el arranque es `fallos_para_avisar`, que exige varios fallos
        # consecutivos antes de molestar a nadie.
        return False, "responde pero ninguna regla ha evaluado aun (¿acaba de arrancar?)"

    atraso = (datetime.now(timezone.utc) - max(marcas)).total_seconds()
    if atraso > max_atraso_s:
        horas = atraso / 3600
        return False, (
            f"vmalert evalua con {horas:.1f} h de atraso. Sus reglas de grabacion "
            f"caen en el pasado y las alertas que las leen no pueden disparar. "
            f"Reinicialo: docker compose restart vmalert"
        )
    return True, f"evaluando al dia (atraso {atraso:.0f} s)"



def comprobar_latido_en_el_almacen(url: str, timeout_s: float, max_atraso_s: float) -> tuple[bool, str]:
    """Comprueba que el latido de vmalert SE PUEDE CONSULTAR en el almacen.

    Existe porque `comprobar_frescura_de_reglas` resulto insuficiente el mismo
    dia que se escribio. Esa mira lo que vmalert DICE de si mismo
    (`lastEvaluation`), y vmalert puede decir la verdad —"evaluando, atraso de
    1 s"— mientras cada muestra que escribe aterriza **102 minutos en el
    pasado**, donde ninguna consulta a `now` la encuentra (D-091).

    Medido: relojes del host y de los contenedores identicos, iteraciones de
    24 ms, cero errores de escritura, `lastSamples = 1` en cada evaluacion... y
    la serie ausente al consultarla. Un componente puede informar
    correctamente de su salud y producir basura.

    Asi que esta mira el OTRO extremo: pregunta al almacen por
    `argus:observador_despierto`, que vale 1 siempre y no depende de que haya
    trafico. Si no esta a `now`, la cadena esta rota en algun punto y da igual
    cual: las reglas que dependen de series grabadas —el burn-rate de SLO, la
    puerta de las alertas de ausencia— no pueden funcionar.

    Prueba la CADENA, no un eslabon. Es la diferencia entre preguntarle a
    alguien si esta trabajando y mirar si el trabajo esta hecho.
    """
    consulta = f"{url.rstrip('/')}/api/v1/query?query=argus%3Aobservador_despierto"
    try:
        with urllib.request.urlopen(consulta, timeout=timeout_s) as r:
            datos = json.loads(r.read().decode())
    except Exception as exc:  # noqa: BLE001
        return False, f"no se pudo consultar el almacen: {type(exc).__name__}: {exc}"

    resultado = (datos.get("data") or {}).get("result") or []
    if not resultado:
        return False, (
            "el latido de vmalert NO esta en el almacen a esta hora. Las reglas de "
            "grabacion no llegan, asi que el burn-rate de SLO y la puerta de las "
            "alertas de ausencia estan inertes. Reinicia vmalert."
        )

    try:
        marca = float(resultado[0]["value"][0])
    except (KeyError, IndexError, TypeError, ValueError):
        return False, "el latido llego con una forma que no se sabe leer"

    atraso = time.time() - marca
    if atraso > max_atraso_s:
        return False, f"el latido esta {atraso / 60:.0f} min atrasado en el almacen"
    return True, f"latido al dia en el almacen (atraso {atraso:.0f} s)"


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


# Las claves que el vigilante entiende. Existe esta lista porque escribi
# `umbral_fallos` en una configuracion de prueba, el vigilante la IGNORO en
# silencio y aplico su valor por defecto — detecto el objetivo caido y salio
# diciendo que todo bien.
#
# Es el mismo modo de fallo que llevo toda la semana arreglando en otros
# sitios, y aqui duele mas: este proceso existe para ser lo unico creible
# cuando lo demas calla. Una config con una errata que se aplica a medias es
# peor que una que no arranca (D-101).
CLAVES_CONOCIDAS = frozenset({
    "objetivos", "reglas", "latido", "avisos",
    "estado", "timeout_s", "fallos_para_avisar",
})


def revisar_config(cfg):
    """Avisa de claves que no se entienden. NO impide arrancar.

    Negarse a correr por una errata convertiria el vigilante en otra cosa que
    se puede caer, y su premisa es correr siempre. Asi que avisa fuerte y
    sigue con los valores por defecto, que es lo que hacia antes en silencio.
    """
    desconocidas = sorted(set(cfg) - CLAVES_CONOCIDAS)
    if desconocidas:
        print(
            "  AVISO  claves de configuracion que no entiendo y se IGNORAN: "
            + ", ".join(desconocidas)
            + "\n         (conocidas: " + ", ".join(sorted(CLAVES_CONOCIDAS)) + ")",
            file=sys.stderr,
        )
    return desconocidas


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
    revisar_config(cfg)
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

    # Que vmalert RESPONDA no basta: tiene que estar evaluando a la hora que es.
    reglas = cfg.get("reglas")
    if reglas:
        ok, detalle = comprobar_frescura_de_reglas(
            reglas["url"],
            float(cfg.get("timeout_s", 10)),
            float(reglas.get("max_atraso_s", 300)),
        )
        marca = "OK " if ok else "MAL"
        print(f"  {marca} {'frescura de vmalert':24} {detalle}")
        if not ok:
            fallos.append(f"frescura de vmalert: {detalle}")

    # Y que lo escrito SE PUEDA CONSULTAR, que no es lo mismo (D-091).
    almacen = cfg.get("latido")
    if almacen:
        ok, detalle = comprobar_latido_en_el_almacen(
            almacen["url"],
            float(cfg.get("timeout_s", 10)),
            float(almacen.get("max_atraso_s", 300)),
        )
        marca = "OK " if ok else "MAL"
        print(f"  {marca} {'latido en el almacen':24} {detalle}")
        if not ok:
            fallos.append(f"latido en el almacen: {detalle}")

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
