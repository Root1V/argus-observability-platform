"""Como se redacta un incidente para una persona.

Vive aparte de los canales a proposito: el CONTENIDO del aviso es el mismo se
mande por Chat, correo o WhatsApp; lo que cambia es el envoltorio. Tenerlo
junto garantizaria que los tres canales acaben diciendo cosas distintas.

La regla que gobierna todo esto: **el contenido nunca es "CPU alta en servicio
X"**. Un aviso que no dice que hacer es ruido con formato.
"""

from __future__ import annotations

from argus_schemas import Incident, Severity

ICONO = {Severity.PAGE: "🔴", Severity.TICKET: "🟡", Severity.INFO: "⚪"}


def titular(incident: Incident) -> str:
    """Una linea. Es lo unico que se lee en una notificacion del movil."""
    estado = "investigando…" if incident.root_cause is None else "causa identificada"
    return (
        f"{ICONO.get(incident.severity, '•')} [{incident.app}/{incident.component}] "
        f"{incident.title} — {estado}"
    )


def resumen(incident: Incident) -> str:
    """Dos lineas de contexto cuantitativo.

    `count` es el numero que convierte "hay un error" en "hay 1.247 errores en
    cuatro segundos", que es lo que distingue un fallo puntual de una caida.
    """
    partes = [f"{incident.count} señal{'es' if incident.count != 1 else ''}"]
    if incident.age_seconds >= 1:
        partes.append(f"{incident.age_seconds:.0f}s")
    if len(incident.components_affected) > 1:
        partes.append(f"{len(incident.components_affected)} componentes")
    if incident.symptoms:
        partes.append(f"afecta a {len(incident.symptoms)} app(s) más")
    return " · ".join(partes)


def cuerpo(incident: Incident) -> list[tuple[str, str]]:
    """Secciones del informe, en el orden en que se leen.

    El orden no es decorativo. Primero la causa, porque es lo que se busca;
    despues si se vio antes, porque puede ahorrar la investigacion entera; luego
    la evidencia, para poder dudar de la causa; y lo descartado al final, porque
    solo importa si no te convence lo anterior.
    """
    secciones: list[tuple[str, str]] = []

    if incident.root_cause:
        confianza = f" (confianza: {incident.confidence})" if incident.confidence else ""
        secciones.append((f"CAUSA PROBABLE{confianza}", incident.root_cause))

    if incident.similar_incidents:
        secciones.append(("VISTO ANTES", "\n".join(f"· {s}" for s in incident.similar_incidents[:2])))

    if incident.evidence:
        secciones.append(("EVIDENCIA", "\n".join(f"· {e}" for e in incident.evidence[:5])))

    if incident.symptoms:
        secciones.append((
            "TAMBIÉN AFECTA A",
            f"{len(incident.symptoms)} incidente(s) aguas abajo, suprimidos por correlación",
        ))

    if incident.trace_ids:
        secciones.append(("TRAZA", incident.trace_ids[0]))

    return secciones


def texto_plano(incident: Incident) -> str:
    """Version en texto para correo y para el log."""
    lineas = [titular(incident), resumen(incident), ""]
    for encabezado, contenido in cuerpo(incident):
        lineas.append(encabezado)
        lineas.append(contenido)
        lineas.append("")
    return "\n".join(lineas).rstrip()
