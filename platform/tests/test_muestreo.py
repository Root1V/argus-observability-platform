"""Lo que no se puede muestrear.

Un registro de auditoria al 10% no es un registro de auditoria.

Se descubrio midiendo: Prometheus adopto `argus.target.*` y ejecuto dos
acciones admin reales para que las verificaramos contra el almacen. Ninguna
llego. Ninguna politica aplicaba a un PATCH admin —no falla, dura
milisegundos, no toca cola, no es GenAI— asi que caian al `baseline` del 10%
(D-098).

El coste de equivocarse no es simetrico: una traza normal perdida es una
muestra menos; una accion administrativa perdida es un hueco en el registro de
quien hizo que, y el Anexo III del AI Act obliga a conservarlo seis meses.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

GATEWAY = Path(__file__).resolve().parents[1] / "collector" / "gateway.yaml"


@pytest.fixture(scope="module")
def politicas() -> list[dict]:
    cfg = yaml.safe_load(GATEWAY.read_text(encoding="utf-8"))
    return cfg["processors"]["tail_sampling"]["policies"]


def test_la_auditoria_tiene_politica_propia(politicas: list[dict]) -> None:
    nombres = [p["name"] for p in politicas]
    assert "auditoria" in nombres, f"sin politica de auditoria: {nombres}"


def test_la_politica_mira_el_ambito_del_evento(politicas: list[dict]) -> None:
    """El evento de auditoria vive en `spanevent`, y las politicas de atributo
    solo miran el span. Es el mismo ambito que se escapo en D-092."""
    aud = next(p for p in politicas if p["name"] == "auditoria")
    assert aud["type"] == "ottl_condition", (
        "una politica `string_attribute` no alcanza los atributos del evento"
    )
    condiciones = aud["ottl_condition"]
    assert condiciones.get("spanevent"), "la politica no mira el ambito spanevent"


def test_la_auditoria_se_decide_antes_del_baseline(politicas: list[dict]) -> None:
    """El orden no cambia el resultado —basta con que UNA politica diga que si—
    pero leerlo despues del muestreo probabilistico invita a pensar lo
    contrario, y quien edite esto despues no tiene por que saberlo."""
    nombres = [p["name"] for p in politicas]
    assert nombres.index("auditoria") < nombres.index("baseline")


def test_el_baseline_sigue_siendo_lo_ultimo(politicas: list[dict]) -> None:
    """Una politica anadida despues del baseline seguiria funcionando, pero la
    lista dejaria de leerse como "estas se quedan, el resto al 10%"."""
    assert politicas[-1]["name"] == "baseline"
    assert politicas[-1]["type"] == "probabilistic"


def test_la_auditoria_cubre_los_desenlaces_de_gobierno(politicas: list[dict]) -> None:
    """Una denegación y una espera son registros, no muestras.

    Le recomendamos a Aeon emitir `argus.outcome=denied` sin `argus.guardrail`
    —para no paginar en cada denegación de política— y medir la tasa. Con solo
    eso no aplicaba ninguna política: **0 de 8 denegaciones** sobrevivieron al
    `baseline` en la prueba. El consejo cambiaba una notificación de más por un
    registro incompleto al 90%, que es peor porque el ruido se nota y un
    registro con huecos no (D-099).
    """
    aud = next(p for p in politicas if p["name"] == "auditoria")
    for ambito in ("span", "spanevent"):
        condiciones = " ".join(aud["ottl_condition"][ambito])
        for desenlace in ("denied", "suspended"):
            assert f'"{desenlace}"' in condiciones, (
                f"`{ambito}` no conserva `argus.outcome={desenlace}`"
            )


def test_la_politica_de_auditoria_no_perdio_sus_condiciones_originales(politicas: list[dict]) -> None:
    """Un `spanevent:` duplicado en YAML pisa al anterior SIN error.

    Pasó al añadir los desenlaces de gobierno: escribí un segundo bloque
    `spanevent` en vez de ampliar el que había, y el fichero siguió siendo YAML
    válido y el Collector lo habría aceptado — con la condición del evento de
    auditoría desaparecida. Se vio al imprimir el YAML *parseado*, no el texto.
    """
    aud = next(p for p in politicas if p["name"] == "auditoria")
    spanevent = aud["ottl_condition"]["spanevent"]
    assert any("audit.admin_action" in c for c in spanevent), (
        "la condición del evento de auditoría ya no está"
    )
    assert any("argus.target.type" in c for c in spanevent)
    assert len(spanevent) >= 4


def test_el_fichero_no_tiene_claves_duplicadas() -> None:
    """Generaliza lo de arriba a todo el gateway.

    `yaml.safe_load` acepta claves repetidas y se queda con la última, así que
    un bloque duplicado desactiva al anterior en silencio en cualquier parte
    del fichero, no solo en esta política.
    """
    import yaml

    class SinDuplicados(yaml.SafeLoader):
        pass

    def _mapa(loader, nodo, deep=False):
        vistas = set()
        for clave_nodo, _ in nodo.value:
            clave = loader.construct_object(clave_nodo, deep=deep)
            if clave in vistas:
                raise AssertionError(f"clave duplicada en gateway.yaml: {clave!r}")
            vistas.add(clave)
        return yaml.SafeLoader.construct_mapping(loader, nodo, deep)

    SinDuplicados.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapa)
    yaml.load(GATEWAY.read_text(encoding="utf-8"), Loader=SinDuplicados)


# ---------------------------------------------------------------------------
# El factor de correccion del sesgo (D-114).
#
# `argus.sampling.retained_pct` es un numero DERIVADO de otras dos cosas de
# este mismo despliegue: el `sampling_percentage` de la politica `baseline`, y
# que la puerta `recordpolicy` del Collector este encendida. Un numero derivado
# sin prueba es un numero que se queda atras sin avisar — y este se usa para
# multiplicar recuentos, asi que quedarse atras significa publicar cifras
# equivocadas, no perder un dato.
# ---------------------------------------------------------------------------

COMPOSE = Path(__file__).resolve().parents[1] / "compose.yaml"
GENAI = Path(__file__).resolve().parents[1] / "collector" / "gateway.genai.yaml"
PUERTA = "processor.tailsamplingprocessor.recordpolicy"


@pytest.fixture(scope="module")
def gateway() -> dict:
    return yaml.safe_load(GATEWAY.read_text(encoding="utf-8"))


def _sentencias_de_retencion(cfg: dict) -> list[str]:
    proc = cfg["processors"]["transform/sampling_retention"]
    return [s for bloque in proc["trace_statements"] for s in bloque["statements"]]


def test_el_porcentaje_escrito_coincide_con_la_politica_baseline(
    gateway: dict, politicas: list[dict]
) -> None:
    """La prueba que hace fiable el numero.

    El `10` del transform y el `sampling_percentage: 10` de la politica son dos
    sitios distintos del mismo fichero. Si alguien baja el baseline al 5% y no
    toca el transform, el almacen dice que se conservo el 10% de esas trazas y
    cualquier correccion sale a la mitad. Nada falla: el numero sigue saliendo.
    """
    baseline = next(p for p in politicas if p["name"] == "baseline")
    real = baseline["probabilistic"]["sampling_percentage"]

    escritas = [s for s in _sentencias_de_retencion(gateway) if '== "baseline"' in s]
    assert escritas, "no se escribe retained_pct para el baseline"
    assert f'"argus.sampling.retained_pct"], {real})' in escritas[0], (
        f'la politica baseline esta al {real}% y el transform escribe otra cosa:\n'
        f'  {escritas[0]}'
    )


def test_lo_conservado_entero_vale_cien(gateway: dict) -> None:
    """El factor de correccion de un error o una denegacion es 1, no 10.

    Es el defecto que motivo todo esto: con el 10 fijo, corregir multiplicaba
    por diez justo lo que ya estaba completo.
    """
    sentencias = _sentencias_de_retencion(gateway)
    cien = [s for s in sentencias if 'retained_pct"], 100)' in s]
    assert cien, "nada escribe 100 para las politicas que conservan entero"
    assert '!= "baseline"' in cien[0]


def test_no_se_escribe_nada_si_no_se_sabe_que_conservo_la_traza(gateway: dict) -> None:
    """Sin la puerta, `tailsampling.policy` llega nulo. Entonces NO se rellena.

    Un factor inventado rompe el calculo en silencio; su ausencia manda a quien
    consulta a las metricas, que es la regla de D-054. Es la misma decision que
    no hacer `setdefault("unknown")` en `argus.denied_by` (D-112).
    """
    for s in _sentencias_de_retencion(gateway):
        if "retained_pct" in s:
            assert "!= nil" in s or '== "baseline"' in s, (
                f"esta sentencia escribe el factor sin comprobar que se conoce "
                f"la politica:\n  {s}"
            )


def test_el_transform_va_despues_del_muestreo(gateway: dict) -> None:
    """El orden ES la correccion.

    Antes de `tail_sampling` el atributo `tailsampling.policy` no existe, asi
    que no habria nada que leer y no se escribiria ningun factor. El processor
    viejo iba justo ahi, antes, y no importaba porque escribia una constante.
    """
    for nombre, tuberia in gateway["service"]["pipelines"].items():
        procs = tuberia.get("processors", [])
        if "transform/sampling_retention" not in procs:
            continue
        assert "tail_sampling" in procs, f"{nombre}: retencion sin muestreo"
        assert procs.index("transform/sampling_retention") > procs.index("tail_sampling"), (
            f"{nombre}: el transform de retencion va ANTES del muestreo, "
            f"asi que lee un atributo que todavia no existe"
        )


def test_toda_tuberia_que_muestrea_limpia_los_internos_del_procesador() -> None:
    """La puerta pone `tailsampling.*` en TODAS las ramas que comparten la
    instancia de `tail_sampling`, incluida la de Langfuse. Son detalles de
    implementacion del procesador y no deberian llegar a ningun almacen.

    Se comprueba sobre los dos ficheros porque la rama de Langfuse vive en el
    overlay, y el hueco de D-092 fue exactamente eso: unos ambitos cubiertos y
    otros no.
    """
    for fichero in (GATEWAY, GENAI):
        cfg = yaml.safe_load(fichero.read_text(encoding="utf-8"))
        for nombre, tuberia in cfg.get("service", {}).get("pipelines", {}).items():
            procs = tuberia.get("processors", [])
            if "tail_sampling" not in procs:
                continue
            assert "transform/sampling_retention" in procs, (
                f"{fichero.name}:{nombre} muestrea y no limpia los "
                f"`tailsampling.*` que pone la puerta `recordpolicy`"
            )


def test_la_puerta_esta_encendida_en_el_despliegue() -> None:
    """Sin la puerta, el arreglo es PEOR que el defecto que arregla.

    `tailsampling.policy` llega nulo, ninguna sentencia aplica, y el atributo
    desaparece de todos los spans: se pasa de un factor equivocado a ningun
    factor. Editar el `gateway.yaml` no es desplegar — la tercera vez que esa
    distincion cuesta algo en este proyecto.
    """
    cfg = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    command = cfg["services"]["collector"]["command"]
    texto = command if isinstance(command, str) else " ".join(command)
    assert PUERTA in texto, f"la puerta `{PUERTA}` no esta en el command del collector"


def test_la_puerta_no_vive_dentro_de_la_variable_que_el_overlay_sobreescribe() -> None:
    """El overlay GenAI sustituye `ARGUS_COLLECTOR_CONFIGS` ENTERA.

    Si la puerta estuviera dentro de ese valor por defecto, desplegar con
    Langfuse la perderia y `retained_pct` dejaria de escribirse sin un solo
    error. Tiene que ir fuera, y eso es lo que se fija aqui.
    """
    cfg = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    command = cfg["services"]["collector"]["command"]
    texto = command if isinstance(command, str) else " ".join(command)

    _, _, tras_variable = texto.partition("${ARGUS_COLLECTOR_CONFIGS")
    assert PUERTA not in tras_variable, (
        "la puerta esta dentro de ${ARGUS_COLLECTOR_CONFIGS...}, asi que el "
        "overlay GenAI la perderia en silencio"
    )
    assert PUERTA in texto.partition("${ARGUS_COLLECTOR_CONFIGS")[0]
