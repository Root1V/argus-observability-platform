"""Sin identidad, la telemetria llega y no se puede usar.

Es el mas grave de los avisos de arranque y el ultimo que pusimos.

Lo reporto Prosodia: arrancaron su stack sin `OTEL_SERVICE_NAME`, los dos
procesos salieron como `unknown-service`, la telemetria se exporto igual y no
hubo ningun error. La unica pista era la linea de `argus.init`. Cuatrocientos
cincuenta registros que ni ellos mismos podian atribuirse, porque el campo que
lo diria es el que faltaba (D-109).

Y el namespace vacio es el mismo fallo con peor consecuencia: `unregistered` en
el registro, criticidad `media` sin canales, e incidentes que se escriben en un
log y en ningun sitio mas. Le paso a Aeon durante dias (D-094).
"""

from __future__ import annotations

import warnings

import pytest
from argus._config import SERVICIO_DESCONOCIDO, Config


@pytest.fixture(autouse=True)
def _sin_identidad(monkeypatch):
    for v in ("ARGUS_SERVICE", "OTEL_SERVICE_NAME", "ARGUS_NAMESPACE"):
        monkeypatch.delenv(v, raising=False)


def _avisos(monkeypatch, **env) -> list[str]:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with warnings.catch_warnings(record=True) as capturados:
        warnings.simplefilter("always")
        Config.from_env()
    return [str(a.message) for a in capturados if "identidad" in str(a.message)]


# --- Lo que reportaron ------------------------------------------------------


def test_sin_nada_avisa_de_las_dos_cosas(monkeypatch) -> None:
    avisos = _avisos(monkeypatch)
    assert len(avisos) == 1, "dos avisos por el mismo arranque se leen como ruido"
    assert "ARGUS_SERVICE" in avisos[0]
    assert "ARGUS_NAMESPACE" in avisos[0]


def test_el_caso_de_prosodia(monkeypatch) -> None:
    """Tenian namespace y les faltaba el nombre de servicio."""
    avisos = _avisos(monkeypatch, ARGUS_NAMESPACE="prosodia")
    assert len(avisos) == 1
    assert "ARGUS_SERVICE" in avisos[0]
    assert "ARGUS_NAMESPACE" not in avisos[0], "avisa de algo que si estaba puesto"


def test_el_caso_de_aeon(monkeypatch) -> None:
    """Tenian nombre de servicio y les faltaba el namespace: `unregistered`."""
    avisos = _avisos(monkeypatch, ARGUS_SERVICE="toolgw")
    assert len(avisos) == 1
    assert "ARGUS_NAMESPACE" in avisos[0]
    assert "NO llegan a ningun canal" in avisos[0], (
        "el aviso no dice la consecuencia, que es la parte que hace actuar"
    )


def test_con_identidad_completa_no_avisa(monkeypatch) -> None:
    assert _avisos(monkeypatch, ARGUS_SERVICE="idp-api", ARGUS_NAMESPACE="idp") == []


def test_la_variable_estandar_tambien_cuenta(monkeypatch) -> None:
    """`OTEL_SERVICE_NAME` es la del estandar y una app que ya tiene OTel la
    trae puesta. Avisar ahi seria ruido en toda migracion."""
    avisos = _avisos(monkeypatch, OTEL_SERVICE_NAME="idp-api", ARGUS_NAMESPACE="idp")
    assert avisos == []


def test_el_servicio_pasado_por_argumento_cuenta(monkeypatch) -> None:
    """`init("mi-servicio")` es la forma mas corta y no puede avisar."""
    monkeypatch.setenv("ARGUS_NAMESPACE", "idp")
    with warnings.catch_warnings(record=True) as capturados:
        warnings.simplefilter("always")
        Config.from_env("idp-api")
    assert [a for a in capturados if "identidad" in str(a.message)] == []


# --- Lo que el aviso tiene que decir ----------------------------------------


def test_dice_que_la_telemetria_llegara_igual(monkeypatch) -> None:
    """Es la frase que separa este aviso de un error de configuracion normal.

    Quien lo lee tiene que entender que NO va a ver un fallo despues: los datos
    llegan y no sirven, y eso es lo que lo hace caro.
    """
    avisos = _avisos(monkeypatch)
    assert "se exportara igual" in avisos[0]
    assert "sin ningun error" in avisos[0]


def test_el_centinela_es_el_del_estandar(monkeypatch) -> None:
    """`unknown-service` no es nuestro: es lo que OTel usa cuando falta el
    nombre. Cambiarlo rompería la continuidad con cualquier otro backend."""
    assert SERVICIO_DESCONOCIDO == "unknown-service"
    cfg = Config.from_env()
    assert cfg.service == SERVICIO_DESCONOCIDO


def test_el_aviso_no_impide_arrancar(monkeypatch) -> None:
    """Una app sin nombre tiene que seguir funcionando: el contrato es no
    tumbar nunca, y un script suelto legitimamente no tiene identidad."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cfg = Config.from_env()
    assert cfg.service == SERVICIO_DESCONOCIDO
    # El namespace NO queda vacio: cae al nombre del servicio. Es un defecto
    # documentado, y la consecuencia es que la identidad de dos niveles
    # desaparece — por eso el aviso lo dice asi y no "queda vacio".
    assert cfg.namespace == SERVICIO_DESCONOCIDO



# --- El entorno no se inventa -----------------------------------------------


def test_el_entorno_no_se_inventa(monkeypatch) -> None:
    """Antes caia a `local`, que no esta en el vocabulario del estandar.

    O sea que este SDK sellaba en silencio un valor invalido, y el aviso de
    D-106 no podia verlo porque miraba la variable y no el valor resuelto.

    Peor: en D-106 contamos que tres equipos mandaban `local` y lo presentamos
    como que lo habian inventado ellos. Dos de esos `local` los ponia este
    codigo, y Prosodia llego a aceptar la culpa por un valor que nunca
    escribieron (D-109).
    """
    monkeypatch.delenv("ARGUS_ENVIRONMENT", raising=False)
    monkeypatch.delenv("DEPLOYMENT_ENVIRONMENT", raising=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cfg = Config.from_env("idp-api")
    assert cfg.environment == "", "volvio a inventarse un entorno"
    assert cfg.environment != "local"


def test_la_ausencia_de_entorno_avisa(monkeypatch) -> None:
    """Ausente es honesto y sigue siendo un problema: sin el no se puede
    separar produccion de desarrollo."""
    monkeypatch.delenv("ARGUS_ENVIRONMENT", raising=False)
    monkeypatch.delenv("DEPLOYMENT_ENVIRONMENT", raising=False)
    with warnings.catch_warnings(record=True) as capturados:
        warnings.simplefilter("always")
        Config.from_env("idp-api")
    assert [a for a in capturados if "deployment.environment.name" in str(a.message)]


def test_el_aviso_del_entorno_mira_el_valor_RESUELTO(monkeypatch) -> None:
    """Es el arreglo de fondo: comprobar la variable dejaba fuera los defectos.

    Un valor invalido puesto a mano ya avisaba desde a13; uno puesto por el
    propio SDK, no. El unico sitio donde se ven los dos es el valor final.
    """
    monkeypatch.setenv("ARGUS_ENVIRONMENT", "bare-metal")
    with warnings.catch_warnings(record=True) as capturados:
        warnings.simplefilter("always")
        cfg = Config.from_env("idp-api")
    assert cfg.environment == "bare-metal"
    assert [a for a in capturados if "no esta en el vocabulario" in str(a.message)]
