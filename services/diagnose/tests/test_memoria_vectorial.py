"""F3-05: la memoria por parecido. Lo que no depende del modelo de embeddings."""

from __future__ import annotations

from diagnose.memoria_vectorial import (
    EmbebedorLexico,
    MemoriaVectorial,
    _espera_del_limite,
    palabras,
    texto_del_incidente,
)


def _inc(ident: str, app: str, comp: str, firma: str, dia: str, titulo: str = "") -> dict:
    return {"id": ident, "app": app, "component": comp, "kind": "error", "signature": firma,
            "title": titulo or firma, "opened_at": f"{dia}T10:00:00+00:00", "root_cause": "lo dijo L3"}


HISTORICO = [
    _inc("a", "veritium", "veritium-api", "NotNullViolationError: null value in column tenant", "2026-10-14"),
    _inc("b", "veritium", "veritium-api", "NotNullViolationError: null value in column tenant", "2026-10-14"),
    _inc("c", "prometheus-inference-platform", "gateway", "ReadTimeout", "2026-10-13"),
    _inc("d", "veritium", "veritium-worker", "PendingRollbackError", "2026-10-15"),        # despues: no
]


def test_separa_las_clases_en_palabras_y_quita_tildes() -> None:
    assert palabras("NotNullViolationError: migración") == ["not", "null", "violation", "migracion"]


def test_lo_indexado_es_el_problema_sin_nombres_ni_diagnostico_ni_uuid() -> None:
    d = {**_inc("x", "veritium", "veritium-api", "CaseRunNotFound case=0017eddd-a4d0-47af-9835-52f90eff499d",
                "2026-10-14"), "title": "fallo en veritium-api"}
    t = texto_del_incidente(d)

    assert "veritium-api" not in t and "veritium" not in t
    assert "lo dijo L3" not in t
    assert "0017eddd" not in t and "<uuid>" in t


def test_encuentra_la_misma_causa_con_otra_clase_y_agrupa_por_episodio(tmp_path) -> None:
    """El PendingRollback de veritium-worker y el NOT NULL de veritium-api: la misma migracion."""
    m = MemoriaVectorial(tmp_path / "m.sqlite", EmbebedorLexico())
    assert m.indexar(HISTORICO) == 4
    assert m.indexar(HISTORICO) == 0                                   # se llena una vez

    consulta = _inc("q", "veritium", "veritium-worker",
                    "PendingRollbackError: Original exception was: NotNullViolationError: null value in column tenant",
                    "2026-10-14T12")
    top = m.parecidos({**consulta, "opened_at": "2026-10-14T12:00:00+00:00"}, minimo=0.0)

    assert top[0].clase == "NotNullViolationError" and top[0].incidentes == 2
    assert all(p.dia <= "2026-10-14" for p in top)                     # nada del futuro


def test_lo_de_la_misma_clase_y_app_se_deja_a_f3_06(tmp_path) -> None:
    m = MemoriaVectorial(tmp_path / "m.sqlite", EmbebedorLexico())
    m.indexar(HISTORICO)
    consulta = {**_inc("q", "veritium", "veritium-api", "NotNullViolationError", "2026-10-15")}

    assert all(p.clase != "NotNullViolationError" for p in m.parecidos(consulta, minimo=0.0))


def test_el_tope_por_ciclo_indexa_primero_lo_mas_reciente(tmp_path) -> None:
    m = MemoriaVectorial(tmp_path / "m.sqlite", EmbebedorLexico())
    assert m.indexar(HISTORICO, maximo=1) == 1
    assert m._todos()[0][0] == "d"


def test_el_limite_de_ritmo_dice_cuanto_esperar_y_lo_demas_no_se_reintenta() -> None:
    assert _espera_del_limite("exceeded the token rate limit of 40000 TPM. Reset in 16 seconds.") == 17.0
    assert _espera_del_limite("Model is not an embedding model") is None
