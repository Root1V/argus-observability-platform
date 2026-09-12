# argus-schemas

Esquemas compartidos entre los servicios de Argus.

Existen como paquete aparte porque los cruzan varios servicios —`alert-bus`,
`notifier`, los agentes— y un esquema divergente entre ellos es un fallo
silencioso: el incidente llega, pero con un campo que el siguiente no entiende.

- `Signal` — un evento crudo que **puede** ser un incidente.
- `Incident` — lo que se notifica y lo que investigan los agentes.
- `Severity` — `info` · `ticket` · `page`.
