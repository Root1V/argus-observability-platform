# Dead man's switch

Avisa si Argus deja de mirar.

## El problema que resuelve

Es el único que la plataforma **no puede resolverse a sí misma**: si Argus cae,
deja de avisar, y su silencio es indistinguible de que todo va bien. Toda la
detección del sistema se apoya en que el sistema funciona.

## Tres restricciones que parecen excesivas y no lo son

1. **Sin dependencias.** Solo la biblioteca estándar de Python. Si dependiera
   de algo que se instala, compartiría modos de fallo con lo que vigila.
2. **Fuera del compose.** Corre como tarea del sistema. Un vigilante dentro del
   contenedor que vigila se cae con él.
3. **Idealmente en otra máquina.** En la misma detecta que el servicio murió;
   en otra detecta además que la máquina murió, que es el caso en que más falta
   hace.

## Instalación

```bash
cp deadman/deadman.json.example deadman/deadman.json
$EDITOR deadman/deadman.json     # rellena al menos un canal de aviso
```

**Comprueba que puede avisarte antes de confiar en él.** Un vigilante que no
puede avisar es peor que no tener vigilante, porque da una falsa sensación de
cobertura:

```bash
python3 deadman/deadman.py --config deadman/deadman.json --test
```

En macOS, para que corra solo cada 5 minutos:

```bash
make deadman-setup
```

Genera la tarea de launchd **con las rutas reales ya rellenadas** y te dice los
dos pasos siguientes. Rellenar rutas absolutas a mano en un XML es la forma más
fácil de instalar un vigilante que no vigila nada.

```bash
launchctl list | grep argus     # comprobar que sigue activo
```

En Linux, una línea de cron:

```
*/5 * * * * /usr/bin/python3 /ruta/deadman/deadman.py --config /ruta/deadman/deadman.json
```

## Comportamiento

- **Dos fallos consecutivos** antes de avisar. El peor caso son 10 minutos
  hasta el aviso, razonable para "la plataforma entera está caída".
- **Avisa por todos los canales configurados**, no por el primero que funcione:
  si la plataforma está caída, no se sabe cuál sigue en pie. Duplicar un aviso
  es molesto; no recibirlo es el fallo que este script existe para evitar.
- **Avisa también la recuperación**, o nadie sabe que volvió.
- **WhatsApp es el canal más adecuado aquí**: si la plataforma está caída,
  Google Chat podría seguir funcionando pero nadie lo está mirando a las tres
  de la mañana.
