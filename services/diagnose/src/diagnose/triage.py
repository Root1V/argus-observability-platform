"""F3-03: el triage. Que incidentes merecen una investigacion de L4, que cuesta minutos de GPU.

El plan pedia un modelo pequeño. Este triage no lo usa, a proposito: las
razones para NO investigar son hechos que el expediente ya tiene medidos —que
la plataforma entera callaba, que el componente nunca emitio, que las reglas
ya tienen causa con confianza alta, que un hermano ya se investiga—, y
preguntarle eso a un modelo seria pagar para que adivine lo que ya se sabe. Si
algun dia estas reglas dejan pasar ruido que solo un modelo distingue, el
modelo entra DESPUES de ellas.

Devuelve siempre el motivo: un triage que dice «no» sin decir por que no se
puede auditar, y es exactamente lo que habra que auditar cuando un incidente
importante se quede sin investigar.
"""

from __future__ import annotations

from .expediente import Expediente
from .juicio import Veredicto


def merece_investigacion(exp: Expediente, veredicto: Veredicto) -> tuple[bool, str]:
    """(si o no, por que). El orden es el de lo mas barato de comprobar a lo mas fino."""
    if exp.severidad != "page":
        return False, f"severidad {exp.severidad or '?'}: solo los `page` justifican minutos de GPU"
    if exp.plataforma.a_rafagas:
        return False, "la plataforma entera callaba: no hay telemetria del servicio que investigar"
    if exp.emision.consultada and exp.emision.nunca:
        return False, "el componente nunca ha emitido: no hay telemetria que investigar"
    if veredicto.tiene_causa and veredicto.confidence == "alta":
        return False, "las reglas ya tienen causa con confianza alta"
    if exp.hermanos:
        # B-40: N componentes con el mismo fallo son una investigacion, no N.
        # Se queda el primero por nombre: estable, y todos los hermanos llegan
        # a la misma eleccion sin hablar entre ellos.
        primero = min([exp.componente, *(h.componente for h in exp.hermanos)])
        if primero != exp.componente:
            return False, f"es hermano de {primero}, que es el que se investiga"
    if not veredicto.tiene_causa:
        return True, "las reglas no encontraron causa"
    return True, f"las reglas tienen causa con confianza {veredicto.confidence}: se busca confirmarla o precisarla"
