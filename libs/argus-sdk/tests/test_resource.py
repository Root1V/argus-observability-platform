"""El Resource: identidad de dos niveles y respeto a la variable estandar."""

from __future__ import annotations

from argus._config import Config
from argus._resource import build_resource



def test_un_relleno_nuestro_no_pisa_la_variable_estandar(monkeypatch) -> None:
    """Es el mismo fallo que pedimos arreglar a otro equipo, en nuestra casa.

    `Resource.create()` fusiona y lo que le pasamos gana. Correcto para lo que
    alguien eligió; un fallo para lo que rellenamos nosotros: `namespace` cae a
    `service` y `role` a `api` cuando no se dicen, y esos rellenos pisaban
    `OTEL_RESOURCE_ATTRIBUTES` en silencio.

    Allí era el constructor directo; aquí, un valor por defecto disfrazado de
    elección. El síntoma es idéntico: la variable estándar no hace nada.
    """
    monkeypatch.setenv("OTEL_SERVICE_NAME", "svc")
    monkeypatch.setenv(
        "OTEL_RESOURCE_ATTRIBUTES",
        "service.namespace=desde-el-entorno,argus.component.role=worker",
    )
    monkeypatch.delenv("ARGUS_NAMESPACE", raising=False)
    monkeypatch.delenv("ARGUS_ROLE", raising=False)

    recurso = build_resource(Config.from_env())

    assert recurso.attributes["service.namespace"] == "desde-el-entorno"
    assert recurso.attributes["argus.component.role"] == "worker"


def test_lo_elegido_sigue_ganando_al_entorno(monkeypatch) -> None:
    """Ceder no puede convertirse en ignorar: si alguien lo dice, manda."""
    monkeypatch.setenv("OTEL_SERVICE_NAME", "svc")
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "service.namespace=desde-el-entorno")
    monkeypatch.setenv("ARGUS_NAMESPACE", "elegido")

    recurso = build_resource(Config.from_env())
    assert recurso.attributes["service.namespace"] == "elegido"
