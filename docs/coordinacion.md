# Coordinación con otros equipos

Las peticiones a otros proyectos y los hilos compartidos **no viven en este
repositorio**. Están en `~/Documents/Victor/<equipo>_argus/`:

| equipo | carpeta |
|---|---|
| Prometheus — plataforma de inferencia local | `prometheus_argus/` |
| Prosodia — pipeline de doblaje con IA | `prosodia_argus/` |

## Por qué fuera

Este repositorio es público. Esos ficheros contienen **información de otros
equipos**: cronologías de sus incidentes, rutas de su código, identificadores de
su backlog y decisiones internas suyas. Publicar eso sin su consentimiento no es
nuestro para decidirlo, por muy útil que sea el registro.

Las **decisiones de Argus** sí se quedan aquí (`decisions.md`), incluidas las que
salieron de esas conversaciones. La diferencia: ahí se documenta **qué
aprendimos y qué cambiamos nosotros**, no el contenido de la conversación ajena.

## El formato del canal, por si sirve

Un fichero, dos escritores. Lo propuso el equipo de Prometheus y resolvió en
cuatro días lo que por cartas habría llevado semanas. Las reglas que lo hacen
funcionar:

- **Solo el dueño de una entrada la cierra.** Quien la abrió decide cuándo está
  satisfecha, así que nadie se marca deberes a sí mismo.
- **No se edita el texto ajeno**, solo se añade debajo y se cambia el estado.
- **Un id por entrada que no se reutiliza**, para poder citar «responde a A-03»
  meses después.
- **Si algo se verificó, decir cómo y dónde.** «Medido en nuestro entorno» y
  «medido en el vuestro» son afirmaciones distintas; escribirlas igual costó
  media hora de perseguir spans que nunca cruzaron.

Ver `D-074` en `decisions.md`.
