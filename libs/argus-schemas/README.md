# argus-obs-schemas

Esquemas compartidos entre los servicios de Argus.

```bash
pip install argus-obs-schemas
```
```python
import argus_schemas
```

> **El nombre de instalación y el de importación no coinciden, y conviene
> decirlo antes que nada.** Se instala `argus-obs-schemas` y se importa `argus_schemas`.
>
> El prefijo `-obs-` es deliberado: elegimos nombres que **no existían** en
> PyPI para que un fallo del índice no acabara instalando el paquete de un
> desconocido con el nombre que esperábamos. El módulo conservó el nombre
> corto porque es lo que se escribe cien veces.
>
> Va arriba porque es lo primero con lo que tropieza quien lo adopta.

Existen como paquete aparte porque los cruzan varios servicios —`alert-bus`,
`notifier`, los agentes— y un esquema divergente entre ellos es un fallo
silencioso: el incidente llega, pero con un campo que el siguiente no entiende.

- `Signal` — un evento crudo que **puede** ser un incidente.
- `Incident` — lo que se notifica y lo que investigan los agentes.
- `Severity` — `info` · `ticket` · `page`.
