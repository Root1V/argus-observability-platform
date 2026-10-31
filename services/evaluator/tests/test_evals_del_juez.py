"""F5-02 sobre el juez de L3: formato, groundedness y consistencia, sin modelo."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from evaluator.evals import Resultado
from evaluator.juez import (
    EstadoDiario,
    EvaluadorDelJuez,
    Registro,
    anadidas_por_el_modelo,
    cifras_sin_respaldo,
    consistencia,
    formato,
    fragmento_valido,
    groundedness,
    leer,
)

ENTRADA = ("EXPEDIENTE\n<<<EXPEDIENTE\nARBOL: el span Client a /oauth2/token devolvio 400 Bad Request\n"
           "ALCANCE: 44 de 44 peticiones fallaron en 5 min (100.0%)\nEXPEDIENTE>>>")


def _r(salida=None, error=None, reglas=None, **campos) -> Registro:
    base = dict(cuando="2026-10-31T16:00:00+00:00", huella="h1", incidente="i1", app="veritium",
                componente="api", firma="400", reglas=reglas or {"evidence": ["ALCANCE: 44 de 44"]},
                modelo={}, llm={"modelo": "m", "entrada": ENTRADA, "salida": salida, "error": error})
    return Registro(**{**base, **campos})


VALIDA = {"causa": "el token de OAuth se rechaza", "confianza": "media",
          "evidencia": ["el span Client a /oauth2/token devolvio 400"]}


class JuezFalso:
    def __init__(self, fragmentos=None, misma=True):
        self.fragmentos, self.misma, self.preguntas = fragmentos, misma, 0

    def respaldo(self, entrada, afirmaciones):
        self.preguntas += 1
        return self.fragmentos if self.fragmentos is not None else [None] * len(afirmaciones)

    def misma_causa(self, a, b):
        self.preguntas += 1
        return self.misma


# --- Formato ---------------------------------------------------------------------


def test_formato_pasa_un_dictamen_que_cumple():
    assert formato(_r(VALIDA)).resultado is Resultado.PASA


def test_formato_cuenta_lo_que_a_veredicto_arregla_en_silencio():
    casos = {
        "confianza": {**VALIDA, "confianza": "segurisimo"},
        "sin causa y con confianza alta": {"causa": None, "confianza": "alta", "evidencia": []},
        "causa sin evidencia": {**VALIDA, "evidencia": []},
        "6 evidencias": {**VALIDA, "evidencia": ["x"] * 6},
        "vuelca la fila": {**VALIDA, "evidencia": ["insert fallo DETAIL: Failing row contains (1, 2)"]},
    }
    for esperado, salida in casos.items():
        e = formato(_r(salida))
        assert e.resultado is Resultado.FALLA, esperado
        if esperado != "confianza":
            assert esperado in e.motivo, (esperado, e.motivo)


def test_formato_un_fallo_de_esquema_es_falla_y_uno_del_proveedor_no_aplica():
    assert formato(_r(error="NoObjectGeneratedError: ninguna valido")).resultado is Resultado.FALLA
    assert formato(_r(error="ProviderError: backend caido")) is None


# --- Groundedness ------------------------------------------------------------------


def test_solo_se_miden_las_evidencias_que_anadio_el_modelo():
    r = _r({**VALIDA, "evidencia": ["ALCANCE: 44 de 44", "algo nuevo del modelo"]})
    assert anadidas_por_el_modelo(r) == ["algo nuevo del modelo"]


def test_el_fragmento_tiene_que_estar_literal_en_la_entrada():
    assert fragmento_valido("el span Client a /oauth2/token devolvio 400", ENTRADA)
    assert fragmento_valido("EL SPAN client a  /oauth2/token devolvio 400", ENTRADA)  # mayusculas y espacios
    assert not fragmento_valido("el span Client a /oauth2/token devolvio 500", ENTRADA)
    assert not fragmento_valido("400", ENTRADA)  # demasiado corto: encaja en cualquier sitio
    assert not fragmento_valido(None, ENTRADA)


def test_groundedness_pasa_con_fragmentos_verificables():
    juez = JuezFalso(["el span Client a /oauth2/token devolvio 400"])
    assert groundedness(_r(VALIDA), juez).resultado is Resultado.PASA


def test_groundedness_falla_si_el_fragmento_es_inventado():
    juez = JuezFalso(["el span Client a /oauth2/token devolvio 503 Service Unavailable"])
    e = groundedness(_r(VALIDA), juez)
    assert e.resultado is Resultado.FALLA and "1 de 1" in e.motivo


def test_una_cifra_que_no_esta_en_el_expediente_falla_sin_preguntar_al_modelo():
    juez = JuezFalso()
    r = _r({**VALIDA, "evidencia": ["fallaron 873 peticiones"]})
    e = groundedness(r, juez)
    assert e.resultado is Resultado.FALLA and "873" in e.motivo
    assert juez.preguntas == 0
    # Las pequeñas no cuentan, y las fechas tampoco.
    assert cifras_sin_respaldo("3 de 5 a las 16:02 del 2026-10-31", ENTRADA) == []
    assert cifras_sin_respaldo("el 100.0% de 44", ENTRADA) == []
    # Como numeros, no como texto: el unico falso ✗ del banco era «100%» frente a «100.0%».
    assert cifras_sin_respaldo("el 100% de las peticiones", ENTRADA) == []
    assert cifras_sin_respaldo("el 26.7% fallo", "fallaron 26.67%") == []
    assert cifras_sin_respaldo("el 26.8% fallo", "fallaron 26.67%") == ["26.8"]


def test_sin_evidencias_nuevas_no_aplica():
    assert groundedness(_r({**VALIDA, "evidencia": ["ALCANCE: 44 de 44"]}), JuezFalso()) is None


# --- Consistencia ------------------------------------------------------------------


def test_consistencia():
    r = _r(VALIDA)
    assert consistencia(r, None, JuezFalso()) is None  # no hubo juicio anterior
    assert consistencia(r, "", JuezFalso()).resultado is Resultado.PASA  # se abstuvo
    juez = JuezFalso()
    assert consistencia(r, "el token de OAuth se rechaza.", juez).resultado is Resultado.PASA
    assert juez.preguntas == 0  # casi igual: no hace falta el modelo
    assert consistencia(r, "el backend esta caido", JuezFalso(misma=False)).resultado is Resultado.FALLA
    assert consistencia(r, "credenciales caducadas en el IdP", JuezFalso(misma=True)).resultado is Resultado.PASA


# --- La fuente ---------------------------------------------------------------------


def _linea(salida, huella="h1", cuando="2026-10-31T16:00:00+00:00", causa_reglas=None):
    return json.dumps({"cuando": cuando, "huella": huella, "incidente": "i", "app": "a", "componente": "c",
                       "firma": "400", "reglas": {"root_cause": causa_reglas, "evidence": []}, "modelo": {},
                       "llm": {"modelo": "m", "entrada": ENTRADA, "salida": salida, "error": None}}) + "\n"


def test_el_diario_se_lee_una_vez_y_respeta_el_tope_de_caras(tmp_path):
    diario = tmp_path / "sombra.jsonl"
    vieja = json.dumps({"cuando": "2026-10-30T00:00:00+00:00", "huella": "h0", "reglas": {}, "modelo": {}}) + "\n"
    diario.write_text(vieja + _linea(VALIDA, "h1") + _linea(VALIDA, "h2") + _linea(VALIDA, "h3"))
    guardadas = []
    juez = JuezFalso(["el span Client a /oauth2/token devolvio 400"])
    ev = EvaluadorDelJuez(diario, EstadoDiario.cargar(tmp_path / "estado.json"), juez,
                          lambda filas, _: guardadas.extend(filas), caras_por_hora=2,
                          reloj=lambda: datetime(2026, 10, 31, 16, 5, tzinfo=UTC))
    ev.vuelta()

    evals = [e.eval for _, e, _ in guardadas]
    assert evals.count("formato") == 3  # la linea sin `llm` no cuenta
    assert evals.count("groundedness") == 2 and ev.stats["caras_por_ritmo"] == 1
    assert evals.count("consistencia") == 1  # el segundo del mismo episodio, comparado con el primero
    assert guardadas[0][0].app == "argus" and guardadas[0][0].componente == "diagnose-juez"

    # Una segunda vuelta no relee; una linea nueva si se lee, y el estado sobrevive a un reinicio.
    guardadas.clear()
    ev.vuelta()
    assert guardadas == []
    with diario.open("a") as f:
        f.write(_linea(VALIDA, "h4"))
    ev2 = EvaluadorDelJuez(diario, EstadoDiario.cargar(tmp_path / "estado.json"), None,
                           lambda filas, _: guardadas.extend(filas))
    ev2.vuelta()
    assert [e.eval for _, e, _ in guardadas] == ["formato"]  # sin modelo, solo formato


def test_una_linea_a_medio_escribir_espera_a_la_siguiente_vuelta(tmp_path):
    diario = tmp_path / "sombra.jsonl"
    entera = _linea(VALIDA)
    diario.write_text(entera + entera[:40])
    guardadas = []
    ev = EvaluadorDelJuez(diario, EstadoDiario.cargar(tmp_path / "e.json"), None,
                          lambda filas, _: guardadas.extend(filas))
    ev.vuelta()
    assert len(guardadas) == 1 and ev.estado.posicion == len(entera.encode())


def test_leer_ignora_lo_que_no_es_un_registro_con_intercambio():
    assert leer("no es json") is None
    assert leer(json.dumps({"cuando": "x"})) is None
