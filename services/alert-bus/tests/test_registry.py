"""El registro: auto-descubrimiento con aviso, y enrutamiento por regla."""

from __future__ import annotations

import time

from alert_bus.registry import Registry
from argus_schemas import Severity


def test_una_app_conocida_no_se_marca_como_descubierta(registry: Registry) -> None:
    app, nueva = registry.resolve("intelligent-document-platform", "idp-api")
    assert not nueva
    assert app.criticality == "alta"


def test_un_servicio_desconocido_aparece_pero_avisa(registry: Registry) -> None:
    """Las dos mitades van juntas.

    Descubrir sin avisar llena el registro de basura; avisar sin descubrir lo
    deja vacio y la plataforma no ve nada nuevo.
    """
    app, nueva = registry.resolve("app-que-nadie-declaro", "su-api")
    assert nueva is True
    assert app.state == "provisional"
    assert "su-api" in app.components


def test_solo_avisa_la_primera_vez(registry: Registry) -> None:
    """Un servicio nuevo genera UN aviso, no uno por span."""
    registry.resolve("nueva", "api")
    _, segunda = registry.resolve("nueva", "api")
    assert segunda is False


def test_un_componente_nuevo_en_app_conocida_no_genera_friccion(registry: Registry) -> None:
    """Anadir un worker a una app existente es lo mas normal del mundo."""
    app, nueva = registry.resolve("intelligent-document-platform", "idp-nuevo-worker")
    assert nueva is False
    assert "idp-nuevo-worker" in app.components


def test_el_slo_por_defecto_sale_del_rol(registry: Registry) -> None:
    """Una app nueva tiene alertamiento razonable desde el primer span."""
    app = registry.get("intelligent-document-platform")
    assert app.component("idp-worker").slo_ms == 60_000     # rol worker
    assert app.component("idp-api").slo_ms == 8_000         # declarado
    assert app.component("idp-ocr").slo_ms == 30_000


def test_la_criticidad_acota_la_severidad(registry: Registry) -> None:
    assert registry.get("intelligent-document-platform").severity_ceiling is Severity.PAGE
    assert registry.get("llm-benchmark").severity_ceiling is Severity.INFO


def test_los_canales_son_una_regla_no_un_juicio(registry: Registry) -> None:
    """A quien avisar sale del registro. El LLM solo redacta el texto (D-014)."""
    assert registry.channels_for("intelligent-document-platform", Severity.PAGE) == ["memory"]
    assert registry.channels_for("app-inexistente", Severity.PAGE) == ["console"]


def test_el_grafo_de_dependencias_esta_disponible(registry: Registry) -> None:
    """Es lo que alimentara la supresion de alertas sintoma (F2-03)."""
    assert registry.dependencies_of("intelligent-document-platform") == [
        "postgres-main",
        "minio-main",
    ]


def test_un_registro_corrupto_no_tumba_el_alert_bus(tmp_path) -> None:
    """Un error de sintaxis en un YAML no puede dejarte sin deteccion."""
    ruta = tmp_path / "roto.yaml"
    ruta.write_text("esto: [no es: yaml valido", encoding="utf-8")
    registro = Registry(ruta)
    assert registro.apps == {}
    _, nueva = registro.resolve("cualquiera", "componente")
    assert nueva is True


def test_un_registro_ausente_tampoco(tmp_path) -> None:
    registro = Registry(tmp_path / "no-existe.yaml")
    assert registro.apps == {}


# --- Recarga en caliente -----------------------------------------------------
# Lo descubrió el piloto: marcar `edge-ai-inference` como activo no tuvo ningún
# efecto hasta reiniciar, pese a que la documentación prometía lo contrario.


def test_el_registro_se_recarga_cuando_cambia_el_fichero(tmp_path) -> None:
    import time

    import yaml

    ruta = tmp_path / "apps.yaml"
    ruta.write_text(yaml.safe_dump([
        {"id": "mi-app", "estado": "planificado", "criticidad": "media",
         "componentes": [{"id": "api", "rol": "api"}]},
    ]), encoding="utf-8")

    registro = Registry(ruta)
    assert registro.get("mi-app").state == "planificado"
    assert registro.reload_if_changed() is False, "recargó sin que cambiara nada"

    time.sleep(0.01)
    ruta.write_text(yaml.safe_dump([
        {"id": "mi-app", "estado": "activo", "criticidad": "alta",
         "componentes": [{"id": "api", "rol": "api"}, {"id": "worker", "rol": "worker"}]},
    ]), encoding="utf-8")

    assert registro.reload_if_changed() is True
    app = registro.get("mi-app")
    assert app.state == "activo"
    assert app.criticality == "alta"
    assert "worker" in app.components


def test_la_recarga_conserva_los_descubrimientos_provisionales(tmp_path) -> None:
    """Un servicio descubierto no puede desaparecer porque se editara el fichero.

    Si desapareciera, volvería a avisarse de «servicio no registrado» en cada
    edición del registro, que es ruido por construcción.
    """
    import time

    import yaml

    ruta = tmp_path / "apps.yaml"
    ruta.write_text(yaml.safe_dump([{"id": "declarada", "estado": "activo",
                                     "componentes": [{"id": "api"}]}]), encoding="utf-8")
    registro = Registry(ruta)

    _, nueva = registro.resolve("descubierta", "su-api")
    assert nueva is True

    time.sleep(0.01)
    ruta.write_text(yaml.safe_dump([
        {"id": "declarada", "estado": "activo", "componentes": [{"id": "api"}]},
        {"id": "otra", "estado": "activo", "componentes": [{"id": "api"}]},
    ]), encoding="utf-8")
    registro.reload_if_changed()

    assert registro.get("descubierta") is not None
    assert registro.get("otra") is not None


def test_un_fichero_que_desaparece_no_borra_el_registro(tmp_path) -> None:
    import yaml

    ruta = tmp_path / "apps.yaml"
    ruta.write_text(yaml.safe_dump([{"id": "a", "estado": "activo",
                                     "componentes": [{"id": "api"}]}]), encoding="utf-8")
    registro = Registry(ruta)
    ruta.unlink()

    registro.reload_if_changed()
    assert registro.get("a") is not None, "quedarse sin registro por un fichero borrado sería peor"


async def test_el_ticker_recarga_el_registro(tmp_path) -> None:
    """El metodo de recarga existia pero nadie lo llamaba.

    Cambiar un canal en el fichero no tenia efecto hasta reiniciar, que es
    justo lo que la recarga en caliente promete evitar. Esta prueba fija el
    CABLEADO, no el metodo: un metodo correcto que nadie invoca es un bug.
    """
    import asyncio

    import yaml

    from alert_bus.app import _ticker
    from alert_bus.engine import Engine

    ruta = tmp_path / "apps.yaml"
    ruta.write_text(yaml.safe_dump([
        {"id": "a", "estado": "activo", "componentes": [{"id": "api"}],
         "canales": {"page": ["console"]}},
    ]), encoding="utf-8")

    registro = Registry(ruta)
    motor = Engine(registro, [])
    assert registro.channels_for("a", Severity.PAGE) == ["console"]

    tarea = asyncio.create_task(_ticker(motor, 0))
    try:
        time.sleep(0.01)
        ruta.write_text(yaml.safe_dump([
            {"id": "a", "estado": "activo", "componentes": [{"id": "api"}],
             "canales": {"page": ["gchat"]}},
        ]), encoding="utf-8")
        for _ in range(50):
            await asyncio.sleep(0.02)
            if registro.channels_for("a", Severity.PAGE) == ["gchat"]:
                break
    finally:
        tarea.cancel()

    assert registro.channels_for("a", Severity.PAGE) == ["gchat"], \
        "el registro es datos: cambiar un canal no debe exigir reiniciar"


def _escribir(ruta, entradas) -> None:
    import yaml
    ruta.write_text(yaml.safe_dump(entradas, allow_unicode=True), encoding="utf-8")


def test_un_namespace_antiguo_se_atiende_con_la_identidad_nueva(tmp_path) -> None:
    """Renombrar una aplicacion no es un corte seco.

    El `service.namespace` lo pone el despliegue de OTRO repositorio, asi que
    entre que se acuerda el nombre y se redespliega conviven los dos valores.
    Sin alias, el viejo entra como `provisional` y pierde criticidad, canales y
    runbook: un renombrado planificado se convierte en una degradacion.
    """
    ruta = tmp_path / "apps.yaml"
    _escribir(ruta, [{
        "id": "prometheus-inference-platform",
        "alias": ["edge-ai-inference"],
        "estado": "activo",
        "criticidad": "alta",
        "canales": {"page": ["email"]},
        "componentes": [{"id": "auth-service", "rol": "api"}],
    }])
    registro = Registry(ruta)

    app, nueva = registro.resolve("edge-ai-inference", "auth-service")

    assert nueva is False, "el nombre viejo no es un servicio desconocido"
    assert app.id == "prometheus-inference-platform"
    assert app.criticality == "alta"
    assert registro.channels_for("edge-ai-inference", Severity.PAGE) == ["email"]


def test_un_alias_no_puede_tapar_a_otra_aplicacion(tmp_path) -> None:
    """Un alias que choca con un id real dejaria esa aplicacion en la sombra.

    Perder el renombrado es mucho menos malo que perder una aplicacion entera,
    asi que gana el id y el alias se descarta.
    """
    ruta = tmp_path / "apps.yaml"
    _escribir(ruta, [
        {"id": "a", "estado": "activo", "criticidad": "alta",
         "componentes": [{"id": "api"}]},
        {"id": "b", "alias": ["a"], "estado": "activo", "criticidad": "baja",
         "componentes": [{"id": "api"}]},
    ])
    registro = Registry(ruta)

    app, nueva = registro.resolve("a", "api")
    assert app.id == "a" and app.criticality == "alta"
    assert nueva is False


def test_el_alias_desaparece_al_retirarlo(tmp_path) -> None:
    """El alias es temporal por definicion: se retira cuando deja de llegar."""
    ruta = tmp_path / "apps.yaml"
    _escribir(ruta, [{"id": "nueva", "alias": ["vieja"], "estado": "activo",
                      "componentes": [{"id": "api"}]}])
    registro = Registry(ruta)
    assert registro.get("vieja") is not None

    time.sleep(0.01)
    _escribir(ruta, [{"id": "nueva", "estado": "activo",
                      "componentes": [{"id": "api"}]}])
    registro.reload_if_changed()

    _, nueva = registro.resolve("vieja", "api")
    assert nueva is True, "sin alias, el nombre viejo vuelve a ser un desconocido"
