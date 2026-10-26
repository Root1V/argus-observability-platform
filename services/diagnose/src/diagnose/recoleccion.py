"""Monta el expediente de un incidente. Seis consultas y ninguna opinion.

La division que gobierna este servicio: **recolectar y juzgar son dos cosas**.
Aqui se recolecta. Lo que sale es un `Expediente`, que son hechos consultables;
lo que opina es `juicio.py`, y se puede cambiar de opinion sin volver a
consultar nada.

Importa porque es lo que hace medible a L3. Un fallo de diagnostico es o un
expediente incompleto —faltaba el dato— o un juicio equivocado —estaba el dato
y lo lei mal—, y son dos arreglos distintos. Si fuera una sola funcion que
consulta y concluye, todos los fallos tendrian la misma pinta.

**Ninguna laguna levanta.** Un bloque que no se puede recolectar se anota en
`expediente.lagunas` con el motivo y el expediente sigue. La razon es la misma
que la del almacen del alert-bus: se prefiere un diagnostico con un bloque
menos a ningun diagnostico. Y se anota en vez de callarse porque «no hay
trazas» y «no pude consultar las trazas» son cosas distintas, y quien juzgue
tiene que poder distinguirlas (D-103).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from .almacen import Almacen, ErrorDeAlmacen
from .expediente import (
    VENTANA_HERMANOS_S,
    Alcance,
    Arbol,
    Cambios,
    Concurrente,
    Contraparte,
    Emision,
    Expediente,
    Hermano,
    Historia,
    Instancia,
    Plataforma,
    arbol_desde_filas,
)

log = logging.getLogger("diagnose.recoleccion")

# Ventana de contexto hacia atras desde que se abrio el incidente. Un minuto
# antes y no mas: lo que importa es el estado del sistema CUANDO empezo, y una
# ventana ancha mete ruido de otro problema.
MARGEN_PREVIO = timedelta(minutes=1)
VENTANA_MINIMA = timedelta(minutes=5)


def _ahora() -> datetime:
    return datetime.now(UTC)


def _fecha(valor: Any) -> datetime | None:
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=UTC)
    try:
        d = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def recolectar(
    incidente: dict[str, Any],
    *,
    almacen: Almacen | None = None,
    registro: list[dict[str, Any]] | None = None,
    historico: list[dict[str, Any]] | None = None,
    ahora: datetime | None = None,
) -> Expediente:
    """El expediente de un incidente, con las lagunas anotadas.

    `registro` e `historico` llegan ya consultados —son HTTP contra el
    alert-bus y los trae el bucle— mientras las trazas se consultan aqui. No es
    incoherencia: lo que se le pasa es lo que el bucle puede reutilizar entre
    varios incidentes del mismo ciclo, y lo que se consulta aqui es lo que
    depende de ESTE incidente.
    """
    ahora = ahora or _ahora()
    abierto = _fecha(incidente.get("opened_at")) or ahora
    visto = _fecha(incidente.get("last_seen_at")) or ahora

    exp = Expediente(
        incidente_id=str(incidente.get("id", "")),
        huella=str(incidente.get("fingerprint", "")),
        app=str(incidente.get("app", "")),
        componente=str(incidente.get("component", "")),
        firma=str(incidente.get("signature", "")),
        tipo_senal=str(incidente.get("kind", "")),
        severidad=str(incidente.get("severity", "")),
        titulo=str(incidente.get("title", "")),
        senales=int(incidente.get("count", 1) or 1),
        entorno=incidente.get("environment"),
    )
    # La edad se calcula aqui y NO se lee de `age_seconds`: 4 de 562 incidentes
    # tienen `notified_at` anterior a `opened_at` porque las dos marcas vienen
    # de relojes distintos, y en esos la edad sale negativa (B-34). Un
    # expediente que dice «-818 s» es un expediente que se lee como roto.
    exp.edad_s = max(0.0, (ahora - abierto).total_seconds())

    desde = abierto - MARGEN_PREVIO
    hasta = max(visto, desde + VENTANA_MINIMA)
    ventana_min = max(1, int((hasta - desde).total_seconds() // 60))

    # --- 1. Topologia, del registro -----------------------------------------
    if registro is None:
        exp.lagunas.append("topologia: no pude leer el registro de aplicaciones")
    else:
        exp.aguas_arriba, exp.aguas_abajo = _topologia(exp.app, registro)

    # --- 2. Historia, del almacen de incidentes ------------------------------
    if historico is None:
        exp.lagunas.append("historia: no pude leer el historico de incidentes")
    else:
        exp.historia = _historia(exp, historico)
        exp.hermanos = _hermanos(exp, historico, abierto)

    if almacen is None:
        exp.lagunas.append("trazas: sin almacen de telemetria configurado")
        return exp

    # --- 3. El arbol del ejemplar -------------------------------------------
    trazas = list(incidente.get("trace_ids") or [])
    if not trazas:
        exp.lagunas.append("arbol: el incidente no trae ninguna traza de ejemplo")
    else:
        try:
            exp.arbol = arbol_desde_filas(almacen.spans_de_traza(trazas[0]))
            if not exp.arbol.spans:
                exp.lagunas.append(
                    f"arbol: la traza {trazas[0]} no esta en el almacen "
                    "(muestreo, o todavia no ha aterrizado)"
                )
        except ErrorDeAlmacen as exc:
            exp.lagunas.append(f"arbol: {exc}")

    # --- 4. El puente al otro lado de la llamada que fallo ------------------
    exp.contraparte = _contraparte(exp.arbol, almacen, exp.lagunas)

    # --- 5. Alcance ---------------------------------------------------------
    try:
        exp.alcance = _alcance(
            almacen.alcance(app=exp.app, componente=exp.componente, desde=desde, hasta=hasta),
            ventana_min=ventana_min,
        )
        if not exp.alcance.totales:
            exp.lagunas.append(
                f"alcance: ningun span `Server` de {exp.app}/{exp.componente} en la ventana; "
                "el incidente puede venir de un span interno o de una llamada saliente"
            )
    except ErrorDeAlmacen as exc:
        exp.lagunas.append(f"alcance: {exc}")

    # --- 5b. Recurrencia en trazas -------------------------------------------
    # Solo tiene sentido si la firma es una clase de error (`ReadTimeout`,
    # `404`); para `canary-silencio` la consulta devolveria cero y ese cero no
    # significaria «nunca» sino «no aplica». Asi que cero no se escribe: no se
    # afirma nada que la consulta no pueda sostener.
    if exp.tipo_senal == "error":
        clase = _clase_para_contar(exp)
        try:
            filas = almacen.recurrencia(app=exp.app, componente=exp.componente, clase=clase, hasta=hasta)
        except ErrorDeAlmacen as exc:
            exp.lagunas.append(f"recurrencia: {exc}")
            filas = None
        if filas:
            exp.historia.dias_mirados = 30
            exp.historia.dias_con_errores = len(filas)
            exp.historia.errores_en_trazas = sum(int(f.get("errores") or 0) for f in filas)
            exp.historia.primer_dia = _fecha(f"{filas[0]['dia']}T00:00:00+00:00")
        elif filas is not None:
            # Cero dias para un incidente de tipo `error` es una contradiccion:
            # el propio incidente es un error dentro de la ventana. Asi que cero
            # no significa «nunca» sino «esta clase no aparece como
            # `error.type`», y de ahi no se puede afirmar nada. El primer banco
            # lo escribio como «ninguno de los 30 dias anteriores» para dos
            # incidentes de antes de D-118, cuya firma es el mensaje entero.
            exp.lagunas.append(
                f"recurrencia: la clase `{clase[:60]}` no aparece como `error.type`; no se puede contar"
            )

    # --- 5d. Procesos: reinicios y despliegues (B-38) ----------------------
    if exp.tipo_senal == "error":
        try:
            exp.cambios = _cambios(almacen.instancias(app=exp.app, componente=exp.componente, hasta=hasta))
        except ErrorDeAlmacen as exc:
            exp.lagunas.append(f"procesos: {exc}")

    # --- 5c. Emision anterior, solo para silencios (B-39) -------------------
    if _es_un_silencio(exp):
        try:
            exp.emision = _emision(almacen.emision(app=exp.app, componente=exp.componente, hasta=hasta))
        except ErrorDeAlmacen as exc:
            exp.lagunas.append(f"emision: {exc}")
        # Hasta la APERTURA, no hasta el final de la ventana: la plataforma
        # vuelve a rafagas justo despues, y medir eso seria ver el futuro.
        try:
            exp.plataforma = Plataforma(consultada=True, minutos_con_datos=almacen.minutos_con_datos(hasta=abierto))
        except ErrorDeAlmacen as exc:
            exp.lagunas.append(f"plataforma: {exc}")

    # --- 6. Concurrentes ----------------------------------------------------
    try:
        exp.concurrentes = _concurrentes(
            almacen.concurrentes(componente=exp.componente, desde=desde, hasta=hasta),
            aguas_arriba=exp.aguas_arriba,
            registro=registro or [],
        )
    except ErrorDeAlmacen as exc:
        exp.lagunas.append(f"concurrentes: {exc}")

    return exp


# --- Los seis bloques, uno por funcion ---------------------------------------


def _es_un_silencio(exp: Expediente) -> bool:
    """El canario abre los silencios como `burn_rate` con firma `canary-silencio`."""
    return exp.tipo_senal == "silence" or "silencio" in exp.firma


def _emision(filas: list[dict[str, Any]]) -> Emision:
    from .expediente import _como_fecha

    e = Emision(consultada=True)
    for fila in filas:
        n = int(fila.get("n") or 0)
        e.por_fuente[str(fila.get("fuente"))] = n
        if not n:
            continue
        # `min`/`max` de una tabla vacia salen como 1970: solo cuentan con datos.
        for campo, mejor in (("primera", min), ("ultima", max)):
            fecha = _como_fecha(fila.get(campo))
            if fecha is not None:
                actual = getattr(e, campo)
                setattr(e, campo, fecha if actual is None else mejor(actual, fecha))
    return e


def _cambios(filas: list[dict[str, Any]]) -> Cambios:
    from .expediente import _como_fecha

    c = Cambios(consultada=True)
    for fila in filas:
        inst = str(fila.get("instancia") or "")
        if not inst:
            c.sin_identidad = True
            continue
        errores = int(fila.get("errores") or 0)
        c.instancias.append(Instancia(
            instancia=inst,
            version=str(fila.get("version") or ""),
            desde=_como_fecha(fila.get("desde")),
            hasta=_como_fecha(fila.get("hasta")),
            errores=errores,
            # `minIf` sin coincidencias devuelve 1970: solo vale si hubo errores.
            primer_error=_como_fecha(fila.get("primer_error")) if errores else None,
        ))
    if c.instancias:
        c.sin_identidad = False
    return c


def _clase_para_contar(exp: Expediente) -> str:
    """La clase de error por la que se cuenta la recurrencia.

    La firma, si parece una clase. Si parece un MENSAJE —las firmas de antes
    de D-118 llevaban el texto entero, con su `DETAIL:` y sus UUID— la clase
    del span del ejemplar que la tenga: es lo que el SDK escribio en
    `error.type` y por lo tanto lo que se puede buscar.
    """
    if " " not in exp.firma and "\n" not in exp.firma:
        return exp.firma
    for span in exp.arbol.cadena_del_fallo:
        if span.clase and exp.firma.startswith(span.clase.rsplit(".", 1)[-1]):
            return span.clase
    for span in exp.arbol.cadena_del_fallo:
        if span.clase:
            return span.clase
    return exp.firma


def _topologia(app: str, registro: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """De que depende y quien depende de ella, segun `apps.yaml`.

    Aguas abajo se calcula al reves —quien declara a esta en su `depende_de`—
    porque el registro solo guarda una direccion. Tenerlo importa para redactar:
    un fallo con tres aplicaciones aguas abajo se cuenta distinto que uno sin
    nadie detras.
    """
    arriba: list[str] = []
    abajo: list[str] = []
    for entrada in registro:
        if entrada.get("id") == app:
            arriba = [str(d) for d in (entrada.get("depende_de") or [])]
        elif app in (entrada.get("depende_de") or []):
            abajo.append(str(entrada.get("id")))
    return arriba, sorted(abajo)


def _historia(exp: Expediente, historico: list[dict[str, Any]]) -> Historia:
    """Cuantas veces esta MISMA firma abrio incidente, y si alguien anoto causa.

    Se compara por `(app, componente, firma)` y no por huella: la huella
    incluye el tipo de senal, y el mismo fallo llegando por el camino caliente
    y por una regla de vmalert tiene dos huellas. Por firma se ve que es lo
    mismo.

    Busqueda EXACTA, no por parecido. El «top-N de similares» que pide F3-06
    no se puede hacer todavia: 571 incidentes guardados tienen 571 huellas
    distintas y 468 de ellos son el mismo error bajo 468 firmas, de cuando la
    firma llevaba el mensaje entero. Un top-N ahi devolveria 468 copias de un
    error como 468 precedentes (B-32).
    """
    propios = [
        d for d in historico
        if d.get("signature") == exp.firma
        and d.get("app") == exp.app
        and d.get("component") == exp.componente
    ]
    h = Historia(veces=len(propios))
    fechas = sorted(f for d in propios if (f := _fecha(d.get("opened_at"))))
    if fechas:
        h.primera_vez = fechas[0]
        h.ultima_vez = fechas[-1]
    # Causas de ocurrencias anteriores, las mas recientes primero y sin
    # repetir: tres incidentes con la misma causa anotada son una pista, no
    # tres.
    vistas: set[str] = set()
    for d in sorted(propios, key=lambda d: str(d.get("opened_at") or ""), reverse=True):
        causa = d.get("root_cause")
        if causa and causa not in vistas and d.get("id") != exp.incidente_id:
            vistas.add(str(causa))
            h.causas_anteriores.append(str(causa))
    return h


# Clases que no dicen nada por si solas: tres componentes con `error` a la vez
# no comparten una causa, comparten un SDK que no supo nombrar la excepcion.
CLASES_SIN_NOMBRE = {"", "error", "exception"}


def _hermanos(exp: Expediente, historico: list[dict[str, Any]], abierto: datetime) -> list[Hermano]:
    """Otros componentes de esta app que abrieron la misma clase a la vez (B-40).

    Misma clase por el ultimo segmento: `redis.exceptions.TimeoutError` y
    `TimeoutError` son la misma (B-33). Uno por componente, el mas cercano.
    """
    from .juicio import _clase_de, _ultimo_segmento

    clase = _ultimo_segmento(_clase_de(exp.firma))
    if clase.lower() in CLASES_SIN_NOMBRE:
        return []
    cercanos: dict[str, Hermano] = {}
    for d in historico:
        componente = str(d.get("component") or "")
        if d.get("app") != exp.app or not componente or componente == exp.componente:
            continue
        if _ultimo_segmento(_clase_de(str(d.get("signature") or ""))) != clase:
            continue
        cuando = _fecha(d.get("opened_at"))
        if cuando is None:
            continue
        segundos = (cuando - abierto).total_seconds()
        if abs(segundos) > VENTANA_HERMANOS_S:
            continue
        previo = cercanos.get(componente)
        if previo is None or abs(segundos) < abs(previo.segundos):
            cercanos[componente] = Hermano(componente=componente, firma=str(d.get("signature") or ""), segundos=segundos)
    return sorted(cercanos.values(), key=lambda h: (abs(h.segundos), h.componente))


def _alcance(filas: list[dict[str, Any]], *, ventana_min: int) -> Alcance:
    a = Alcance(ventana_min=ventana_min)
    for fila in filas:
        fallidas = int(fila.get("fallidas") or 0)
        a.fallidas += fallidas
        a.totales += int(fila.get("totales") or 0)
        if fallidas:
            a.rutas[str(fila.get("ruta") or "(sin ruta)")] = fallidas
    return a


def _concurrentes(
    filas: list[dict[str, Any]],
    *,
    aguas_arriba: list[str],
    registro: list[dict[str, Any]],
) -> list[Concurrente]:
    """Otros servicios con errores a la vez, marcando los que son dependencia.

    La marca es lo unico que distingue una pista de una coincidencia. El
    registro declara dependencias entre APLICACIONES y aqui llegan
    COMPONENTES, asi que hay que traducir: `veritium-api` es un componente de
    `veritium`, y `veritium` puede ser aguas arriba. Sin esa traduccion la
    marca no se activaria nunca y el bloque entero seria ruido con formato.
    """
    de_componente_a_app: dict[str, str] = {}
    for entrada in registro:
        for componente in entrada.get("componentes") or []:
            de_componente_a_app[str(componente)] = str(entrada.get("id"))

    salida = []
    for fila in filas:
        servicio = str(fila.get("servicio") or "")
        app = de_componente_a_app.get(servicio, servicio)
        salida.append(
            Concurrente(
                servicio=servicio,
                clase=str(fila.get("clase") or ""),
                errores=int(fila.get("errores") or 0),
                aguas_arriba=app in aguas_arriba,
            )
        )
    # Las dependencias primero: si algo de lo que dependemos esta fallando a la
    # vez, es lo primero que hay que leer.
    salida.sort(key=lambda c: (not c.aguas_arriba, -c.errores))
    return salida


def _contraparte(arbol: Arbol, almacen: Almacen, lagunas: list[str]) -> Contraparte | None:
    """El otro lado de la llamada saliente que fallo, si hay una.

    Se busca el span de error mas profundo de tipo `Client`: es la llamada que
    el servicio hizo y le salio mal. Si el fallo no sale del servicio, no hay
    contraparte y este bloque no existe — que es distinto de existir vacio.
    """
    salientes = [s for s in arbol.cadena_del_fallo if s.tipo == "Client" and s.ruta]
    if not salientes:
        return None
    llamada = max(salientes, key=lambda s: s.profundidad)

    destino = llamada.ruta or ""
    ruta = urlparse(destino).path if "://" in destino else destino
    c = Contraparte(
        llamada=f"{llamada.nombre} ({llamada.clase or 'error'})",
        destino=destino,
    )
    if not ruta or not llamada.momento:
        lagunas.append("contraparte: la llamada saliente no trae ruta u hora utilizables")
        return c

    try:
        filas = almacen.contraparte(ruta=ruta, momento=llamada.momento)
    except ErrorDeAlmacen as exc:
        lagunas.append(f"contraparte: {exc}")
        return c

    # El candidato que SOLAPA en el tiempo con la llamada, no el mas cercano:
    # un servicio que atiende la misma ruta cada pocos segundos tiene siempre
    # uno cerca, y coger el mas cercano empalmaria la llamada que fallo con una
    # peticion distinta que salio bien. Eso convertiria «nadie atendio esto» en
    # «lo atendieron y fue bien», que es el error de diagnostico mas caro que
    # este bloque puede cometer.
    inicio = llamada.momento
    fin = inicio + timedelta(milliseconds=llamada.ms)
    for fila in filas:
        from .expediente import _como_fecha

        empieza = _como_fecha(fila.get("Timestamp"))
        if empieza is None:
            continue
        acaba = empieza + timedelta(milliseconds=float(fila.get("Duration") or 0) / 1e6)
        if acaba < inicio or empieza > fin:
            continue
        c.encontrada = True
        c.servicio = str(fila.get("servicio") or "")
        c.codigo = str(fila.get("codigo") or "") or None
        c.estado = str(fila.get("StatusCode") or "") or None
        c.ms = float(fila.get("Duration") or 0) / 1e6
        c.discrepa = llamada.error and c.estado != "Error"
        break
    return c
