"""L3: el diagnostico de un incidente.

D-013 decidio que esto NO es un agente: es una llamada con salida estructurada
sobre contexto pre-recolectado por consultas fijas. Sin bucle de herramientas,
sin orquestacion. El modulo esta partido en esas dos mitades:

- `recoleccion` + `almacen` + `expediente` → los HECHOS (consultas fijas)
- `juicio` → la OPINION (reglas hoy; un modelo detras de la misma interfaz)

Y `bucle` es lo que los une: busca incidentes sin causa, recolecta, juzga y
devuelve el resultado al alert-bus, que edita el aviso que ya salio.
"""

from .expediente import Expediente
from .juicio import JuezDeReglas, Veredicto

__all__ = ["Expediente", "JuezDeReglas", "Veredicto"]
