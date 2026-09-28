"""La seudonimizacion tiene que cubrir TODOS los ambitos que llevan atributos.

Esta prueba nace de un agujero medido: `transform/pseudonymize` cubria
`context: span` y `context: log`, y un `user.id` colgado de un EVENTO de span
aterrizaba en crudo en ClickHouse. No era un caso teorico —el registro de
auditoria de Prometheus vive exactamente ahi— y no lo cazaba nada, porque la
configuracion del Collector no la leia ninguna prueba (D-092).

El error de fondo se repite: una regla escrita para la telemetria que uno
imagina, no para la que llega. Igual que cubrir `user.id` y no `user_id`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

GATEWAY = Path(__file__).resolve().parents[1] / "collector" / "gateway.yaml"

# Los ambitos OTTL que pueden llevar atributos de identidad. `resource` queda
# fuera a proposito: un recurso describe al emisor, no a un usuario.
AMBITOS_CON_ATRIBUTOS = {"span", "spanevent", "log"}

# Las cuatro grafias con las que un identificador de persona nos llega de
# verdad. Las dos primeras son las del estandar; las otras dos, las que manda
# el trafico real.
GRAFIAS_DE_PERSONA = {"user.id", "user_id", "enduser.id", "jwt.subject"}


@pytest.fixture(scope="module")
def seudonimizador() -> dict:
    cfg = yaml.safe_load(GATEWAY.read_text())
    return cfg["processors"]["transform/pseudonymize"]


def _sentencias_por_ambito(proc: dict) -> dict[str, list[str]]:
    porambito: dict[str, list[str]] = {}
    for clave, bloques in proc.items():
        if not clave.endswith("_statements"):
            continue
        for bloque in bloques:
            porambito.setdefault(bloque["context"], []).extend(bloque["statements"])
    return porambito


def test_estan_los_tres_ambitos(seudonimizador: dict) -> None:
    """Si un ambito nuevo aparece en la config, tiene que tener sus reglas."""
    presentes = set(_sentencias_por_ambito(seudonimizador))
    faltan = AMBITOS_CON_ATRIBUTOS - presentes
    assert not faltan, (
        f"ambitos sin seudonimizar: {sorted(faltan)}. Un atributo de identidad "
        f"en uno de ellos llega EN CRUDO al almacen."
    )


@pytest.mark.parametrize("ambito", sorted(AMBITOS_CON_ATRIBUTOS))
def test_cada_ambito_borra_el_identificador_en_crudo(seudonimizador: dict, ambito: str) -> None:
    """Hashear sin borrar deja el original al lado del seudonimo."""
    sentencias = _sentencias_por_ambito(seudonimizador)[ambito]
    texto = "\n".join(sentencias)

    # El ambito de logs solo recibe las dos grafias que emitimos nosotros; span
    # y spanevent reciben tambien las de terceros.
    esperadas = {"user.id", "user_id"} if ambito == "log" else GRAFIAS_DE_PERSONA

    for grafia in esperadas:
        hasheada = f'{ambito}.attributes["{grafia}"]' in texto and "enduser.pseudo.id" in texto
        borrada = f'delete_key({ambito}.attributes, "{grafia}")' in texto
        assert hasheada and borrada, (
            f"en `{ambito}`, `{grafia}` no se convierte en `enduser.pseudo.id` "
            f"y se borra (hasheada={hasheada}, borrada={borrada})"
        )


@pytest.mark.parametrize("ambito", sorted(AMBITOS_CON_ATRIBUTOS))
def test_el_correo_no_viaja(seudonimizador: dict, ambito: str) -> None:
    texto = "\n".join(_sentencias_por_ambito(seudonimizador)[ambito])
    for grafia in ("user.email", "user_email"):
        assert f'delete_key({ambito}.attributes, "{grafia}")' in texto, (
            f"en `{ambito}`, `{grafia}` no se borra. El `redaction` lo enmascara "
            f"a `****`, pero la clave sobrevive y sugiere un correo que ya no esta."
        )


@pytest.mark.parametrize("ambito", ["span", "spanevent"])
def test_un_objeto_que_es_persona_tambien_se_seudonimiza(seudonimizador: dict, ambito: str) -> None:
    """`argus.target.id` es opaco salvo cuando el objeto administrado es alguien.

    `/admin/users/{user_id}/disable` mete un identificador de persona en un
    atributo que las reglas del actor no miran. `argus.target.type` es el unico
    campo que sabe si el id de al lado es una cosa o alguien.
    """
    texto = "\n".join(_sentencias_por_ambito(seudonimizador)[ambito])
    assert f'{ambito}.attributes["argus.target.type"] == "user"' in texto, (
        f"en `{ambito}` no hay regla condicionada por `argus.target.type`"
    )
    assert f'set({ambito}.attributes["argus.target.id"], SHA256(' in texto


def test_la_sal_nunca_esta_en_el_repositorio(seudonimizador: dict) -> None:
    """Sin sal, el hash de un UUID conocido es comprobable por fuerza bruta."""
    for sentencias in _sentencias_por_ambito(seudonimizador).values():
        for s in sentencias:
            if "SHA256" not in s:
                continue
            assert "${env:ARGUS_PSEUDONYM_SALT}" in s, f"hash sin sal del entorno: {s}"


def test_las_rutas_ottl_llevan_su_prefijo(seudonimizador: dict) -> None:
    """Un `attributes[...]` a secas no es solo sintaxis vieja: filtra la sal.

    OTTL acepta la forma sin prefijo y la reescribe sola, pero al hacerlo
    **registra la sentencia reescrita en el log del Collector** — con
    `${env:ARGUS_PSEUDONYM_SALT}` ya expandido. La sal estuvo en claro en los
    logs del contenedor desde que existe la seudonimizacion, once veces.

    No llego a ClickHouse porque el agente no tiene receptor `filelog`, pero el
    plan contempla anadirlo, y ese dia sal y hashes acabarian en el mismo
    almacen — que es exactamente lo que la sal existe para impedir (D-092).
    """
    for ambito, sentencias in _sentencias_por_ambito(seudonimizador).items():
        for s in sentencias:
            sin_prefijo = re.search(r"(?<![\w.])attributes[\[,]", s)
            assert not sin_prefijo, (
                f"en `{ambito}`, ruta sin prefijo `{ambito}.`: {s}\n"
                f"OTTL la reescribira y dejara la sal en el log del Collector."
            )
