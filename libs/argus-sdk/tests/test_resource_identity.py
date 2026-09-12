"""Identidad de dos niveles y construccion del Resource."""

from __future__ import annotations

from argus._config import Config
from argus._resource import build_resource
from argus_semconv import attributes as A


def test_two_level_identity_separates_app_from_component() -> None:
    """`service.namespace` es la APLICACION, `service.name` el COMPONENTE.

    Sin esa separacion acabas con decenas de servicios planos sin forma de
    agruparlos por aplicacion, y el problema empeora con cada app nueva.
    """
    cfg = Config.from_env(
        "idp-worker",
        namespace="intelligent-document-platform",
        role="worker",
        version="0.4.2",
        environment="mac-dev",
    )
    attrs = build_resource(cfg).attributes

    assert attrs[A.SERVICE_NAMESPACE] == "intelligent-document-platform"
    assert attrs[A.SERVICE_NAME] == "idp-worker"
    assert attrs[A.ARGUS_COMPONENT_ROLE] == "worker"
    assert attrs[A.SERVICE_VERSION] == "0.4.2"
    assert attrs[A.DEPLOYMENT_ENVIRONMENT_NAME] == "mac-dev"


def test_namespace_defaults_to_service_for_single_component_apps() -> None:
    cfg = Config.from_env("solo-app")
    assert build_resource(cfg).attributes[A.SERVICE_NAMESPACE] == "solo-app"


def test_langfuse_mirror_attributes_are_present() -> None:
    """Langfuse lee estos dos LITERALMENTE.

    Ponerlos en el Resource es gratis y ahorra un processor de transformacion
    en el Collector.
    """
    cfg = Config.from_env("svc", version="1.2.3", environment="imac")
    attrs = build_resource(cfg).attributes

    assert attrs[A.LANGFUSE_ENVIRONMENT] == "imac"
    assert attrs[A.LANGFUSE_RELEASE] == "1.2.3"


def test_resource_create_honours_otel_resource_attributes(monkeypatch) -> None:
    """La razon por la que se usa `Resource.create()` y no el constructor.

    El constructor directo salta los detectores y esta variable, en silencio.
    Y esta variable es justo el mecanismo por el que se inyectan atributos
    desde el compose sin tocar codigo.
    """
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "custom.team=plataforma,custom.tier=1")
    attrs = build_resource(Config.from_env("svc")).attributes

    assert attrs["custom.team"] == "plataforma"
    assert attrs["custom.tier"] == "1"


def test_sdk_detectors_contribute_host_and_process() -> None:
    attrs = build_resource(Config.from_env("svc")).attributes
    assert "telemetry.sdk.name" in attrs


def test_instance_id_is_generated_when_absent(monkeypatch) -> None:
    monkeypatch.delenv("HOSTNAME", raising=False)
    monkeypatch.delenv("ARGUS_INSTANCE_ID", raising=False)
    cfg = Config.from_env("svc")
    assert cfg.instance_id
    assert Config.from_env("svc").instance_id != cfg.instance_id  # unico por proceso
