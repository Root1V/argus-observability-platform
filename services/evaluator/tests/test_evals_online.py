"""Las evaluaciones baratas: pasa, falla y, sobre todo, cuando no se puede saber."""

from __future__ import annotations

from dataclasses import replace

from evaluator.evals import Abandono, Rechazo, Resultado, Truncado, Vacia, evaluar, motivos
from evaluator.evals import Span as S

BASE = S(trace_id="t1", span_id="s1", fin="2026-10-27 10:00:00.000000000", app="veritium", componente="veritium-api",
         operacion="chat", modelo="gpt-oss-20b", modelo_pedido="gpt-oss-20b", finish_reasons='["stop"]',
         tokens_salida="120", max_tokens="4096")


def _r(ev, **cambios):
    e = ev.evaluar(replace(BASE, **cambios))
    return None if e is None else e.resultado


def test_una_respuesta_normal_pasa_todas():
    assert [e.resultado for e in evaluar(BASE, en_muestra=False)] == [Resultado.PASA] * 4


def test_motivos_acepta_el_array_serializado_y_un_valor_suelto():
    assert motivos(BASE) == ["stop"]
    assert motivos(replace(BASE, finish_reasons="STOP")) == ["stop"]
    assert motivos(replace(BASE, finish_reasons="")) is None
    assert motivos(replace(BASE, finish_reasons="[]")) is None


def test_rechazo_por_filtro_de_contenido_o_negativa():
    assert _r(Rechazo(), finish_reasons='["content_filter"]') is Resultado.FALLA
    assert _r(Rechazo(), finish_reasons='["refusal"]') is Resultado.FALLA
    assert _r(Rechazo(), finish_reasons='["tool_calls"]') is Resultado.PASA


def test_sin_finish_reasons_no_es_pasa_es_desconocido():
    # Es el caso de Centinela hoy: el span de chat no trae los atributos de respuesta.
    for ev in (Rechazo(), Truncado(), Abandono()):
        e = ev.evaluar(replace(BASE, finish_reasons=""))
        assert e.resultado is Resultado.DESCONOCIDO
        assert "finish_reasons" in e.motivo


def test_una_palabra_fuera_del_vocabulario_no_se_adivina():
    e = Rechazo().evaluar(replace(BASE, finish_reasons='["weird_new_reason"]'))
    assert e.resultado is Resultado.DESCONOCIDO
    assert "weird_new_reason" in e.motivo


def test_el_desenlace_del_gateway_no_es_el_motivo_del_modelo():
    # Verificado el 31/10: con max_tokens=600 el cliente recibio `length` y el
    # span del gateway decia `complete`. Contarlo como fin normal daba `pasa`
    # a una respuesta truncada.
    for ev in (Rechazo(), Truncado()):
        e = ev.evaluar(replace(BASE, finish_reasons='["complete"]'))
        assert e.resultado is Resultado.DESCONOCIDO and "desenlace del gateway" in e.motivo
        assert _r(ev, finish_reasons='["client_disconnected"]') is Resultado.DESCONOCIDO
    # Para abandono si vale: `complete` es que el cliente estaba.
    assert _r(Abandono(), finish_reasons='["complete"]') is Resultado.PASA
    assert _r(Abandono(), finish_reasons='["client_disconnected"]') is Resultado.FALLA


def test_abandono_lee_argus_inference_outcome():
    # Con PRM-282 el desenlace sale de `finish_reasons`: un abandono llegara
    # con `finish_reasons` vacio y el desenlace en su atributo (A-53).
    assert _r(Abandono(), finish_reasons="", desenlace="client_disconnected") is Resultado.FALLA
    assert _r(Abandono(), finish_reasons='["stop"]', desenlace="complete") is Resultado.PASA
    assert _r(Rechazo(), finish_reasons='["length"]', desenlace="complete") is Resultado.PASA
    assert _r(Truncado(), finish_reasons='["length"]', desenlace="complete") is Resultado.FALLA


def test_truncado_por_motivo_o_por_la_cuenta_de_tokens():
    assert _r(Truncado(), finish_reasons='["length"]') is Resultado.FALLA
    e = Truncado().evaluar(replace(BASE, tokens_salida="4096", max_tokens="4096"))
    assert e.resultado is Resultado.FALLA and "4096 de 4096" in e.motivo
    # Sin max_tokens en el span, solo cuenta el motivo.
    assert _r(Truncado(), tokens_salida="9999", max_tokens="") is Resultado.PASA


def test_vacia_cero_tokens_sin_herramienta():
    assert _r(Vacia(), tokens_salida="0") is Resultado.FALLA
    assert _r(Vacia(), tokens_salida="0", finish_reasons='["tool_calls"]') is Resultado.PASA
    assert _r(Vacia(), tokens_salida="") is Resultado.DESCONOCIDO
    # Un abandono no es una respuesta vacia: no hubo respuesta.
    assert _r(Vacia(), tokens_salida="0", finish_reasons='["client_disconnected"]') is None


def test_no_aplican_a_errores_ni_a_operaciones_no_generativas():
    assert evaluar(replace(BASE, error=True), en_muestra=True) == []
    assert evaluar(replace(BASE, operacion="embeddings"), en_muestra=True) == []
    assert evaluar(replace(BASE, operacion="execute_tool"), en_muestra=True) == []


def test_las_caras_solo_con_la_muestra():
    from evaluator.evals import Evaluacion

    class Cara:
        nombre, version, coste = "cara", "1", "caro"

        def evaluar(self, span):
            return Evaluacion("cara", "1", Resultado.PASA)

    fuera = evaluar(BASE, en_muestra=False, caras=(Cara(),))
    dentro = evaluar(BASE, en_muestra=True, caras=(Cara(),))
    assert "cara" not in {e.eval for e in fuera}
    assert "cara" in {e.eval for e in dentro}
