# canary

Sondas sintéticas. Ejerce cada aplicación cada pocos minutos y **convierte el
silencio en una alerta**.

## Por qué existe

Sin esto, una aplicación caída no produce telemetría — y la ausencia de
telemetría es indistinguible de *"va todo bien y no hay tráfico"*. Ese es el
punto ciego que todos los sistemas de monitorización basados solo en lo que
llega comparten: **no pueden alertar de lo que no ocurre**.

## Por qué importa el doble en macOS

El "nivel 0" del plan —cobertura sin tocar código— se apoyaba en eBPF, que es
tecnología del kernel Linux y **no existe en macOS** (D-003 y §5.3 del plan).
En una Mac, el canario es buena parte de lo que queda: da disponibilidad y
latencia de cualquier aplicación con un endpoint, sin instrumentarla.

## Dos tipos de sonda

| Tipo | Qué comprueba | Para qué sirve |
|---|---|---|
| **Activa** | Un endpoint responde y en cuánto tiempo | Disponibilidad de apps no instrumentadas |
| **Pasiva** | Una app ha emitido telemetría recientemente | Detecta que una app instrumentada **dejó de reportar** |

La pasiva es la que cierra el punto ciego: una aplicación puede estar
respondiendo a su `/health` y haber dejado de procesar trabajo.
