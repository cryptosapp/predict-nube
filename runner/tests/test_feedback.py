"""Feedback en movimiento: registro de decisiones y resultados, resumen y ajustes."""
import unittest

import feedback as FB
import predicciones as PR
from test_pred_ia import CFG, OPS, libro, mercado

T0 = 1_800_000_000_000
H = 3_600_000


def pos(cod=90170, cant=10, costo=0.40, precio=0.45, vence=T0 + 6 * H, lado="Sí", sub="SOL", objetivo=120):
    return {"coin": f"+{cod}", "pregunta": "¿SOL ≥ 120 al vencer?", "lado_nombre": lado, "cantidad": cant, "costo": costo, "precio": precio,
            "vence": vence, "subyacente": sub, "objetivo": objetivo}


def orden(cod=90170, conf=80, votos=None, categoria="cripto_hoy"):
    return {"tipo": "prediccion", "accion": "COMPRAR", "estado": "ejecutada", "coin": f"#{cod}", "origen": "ia", "confianza": conf, "categoria": categoria,
            "ts": T0, "ia": {"votos": votos}}


def cerrada(resultado, pnl, horas=1, cat="cripto_hoy", conf=80, votos=None, tipo="prediccion"):
    return {"ts": T0 - horas * H, "tipo": tipo, "etiqueta": "x", "categoria": cat, "origen": "ia", "confianza": conf, "votos": votos, "como": "vencida",
            "resultado": resultado, "pnl": pnl}


class Registro(unittest.TestCase):
    def test_abre_con_la_decision_que_la_compro(self):
        votos = [{"modelo": "a", "accion": "COMPRAR", "confianza": 80}, {"modelo": "b", "accion": "NO ENTRAR", "confianza": 60}]
        r = FB.actualizar(None, [pos()], [orden(votos=votos)], ahora=T0)
        a = r["abiertas"]["+90170"]
        self.assertEqual((a["origen"], a["confianza"], a["categoria"], a["invertido"], a["cantidad"]), ("ia", 80, "cripto_hoy", 4.0, 10))
        self.assertEqual(a["votos"], [{"modelo": "a", "accion": "COMPRAR"}, {"modelo": "b", "accion": "NO ENTRAR"}])

    def test_compra_manual_toma_la_categoria_del_mercado(self):
        r = FB.actualizar(None, [pos()], [], categorias={9017: "cripto_mediano"}, ahora=T0)
        self.assertEqual((r["abiertas"]["+90170"]["origen"], r["abiertas"]["+90170"]["categoria"]), ("tu", "cripto_mediano"))

    def test_vencio_ganando_por_el_precio_del_subyacente(self):
        r = FB.actualizar(None, [pos()], [orden()], ahora=T0)
        r = FB.actualizar(r, [], precio_final=lambda s, ms: 125.0, ahora=T0 + 7 * H)
        c = r["cerradas"][0]
        self.assertEqual((c["como"], c["resultado"], c["pnl"], c["pnl_pct"], c["confianza"]), ("vencida", "ganada", 6.0, 150.0, 80))
        self.assertEqual(r["abiertas"], {})

    def test_vencio_perdiendo_y_el_lado_no_gana_al_reves(self):
        r = FB.actualizar(None, [pos()], ahora=T0)
        r = FB.actualizar(r, [], precio_final=lambda s, ms: 110.0, ahora=T0 + 7 * H)
        self.assertEqual((r["cerradas"][0]["resultado"], r["cerradas"][0]["pnl"]), ("perdida", -4.0))
        r = FB.actualizar(None, [pos(cod=90171, lado="No", costo=0.6)], ahora=T0)
        r = FB.actualizar(r, [], precio_final=lambda s, ms: 110.0, ahora=T0 + 7 * H)
        self.assertEqual((r["cerradas"][0]["resultado"], r["cerradas"][0]["pnl"]), ("ganada", 4.0))

    def test_muy_justo_o_pistas_que_se_contradicen_no_se_cuenta(self):
        r = FB.actualizar(None, [pos()], ahora=T0)
        r = FB.actualizar(r, [], precio_final=lambda s, ms: 120.05, ahora=T0 + 7 * H)
        self.assertEqual((r["cerradas"][0]["resultado"], r["cerradas"][0]["pnl"]), ("sin_confirmar", None))
        # Último precio 0,95 justo antes de vencer, pero la vela dice que perdió.
        r = FB.actualizar(None, [pos(precio=0.95)], ahora=T0 + 6 * H - 10 * 60_000)
        r = FB.actualizar(r, [], precio_final=lambda s, ms: 110.0, ahora=T0 + 7 * H)
        self.assertEqual(r["cerradas"][0]["resultado"], "sin_confirmar")

    def test_sin_subyacente_decide_el_ultimo_precio_visto(self):
        r = FB.actualizar(None, [pos(precio=0.97, sub=None, objetivo=None)], ahora=T0 + 6 * H - 20 * 60_000)
        r = FB.actualizar(r, [], ahora=T0 + 7 * H)
        self.assertEqual((r["cerradas"][0]["resultado"], r["cerradas"][0]["pnl"]), ("ganada", 6.0))
        r = FB.actualizar(None, [pos(precio=0.5, sub=None, objetivo=None)], ahora=T0 + 6 * H - 20 * 60_000)
        r = FB.actualizar(r, [], ahora=T0 + 7 * H)
        self.assertEqual(r["cerradas"][0]["resultado"], "sin_confirmar")

    def test_venta_de_kumo_cuenta_lo_cobrado(self):
        r = FB.actualizar(None, [pos()], [orden()], ahora=T0)
        op = {"accion": "VENDER", "prediccion": "#90170", "cantidad": 10, "total": 5.5, "simulada": False}
        r = FB.actualizar(r, [], ops=[op], ahora=T0 + H)
        c = r["cerradas"][0]
        self.assertEqual((c["como"], c["resultado"], c["pnl"]), ("vendida", "ganada", 1.5))

    def test_venta_parcial_y_el_resto_vence(self):
        r = FB.actualizar(None, [pos()], ahora=T0)
        r = FB.actualizar(r, [pos(cant=4)], ops=[{"accion": "VENDER", "prediccion": "#90170", "cantidad": 6, "total": 3.0}], ahora=T0 + H)
        self.assertEqual((r["abiertas"]["+90170"]["cantidad"], r["abiertas"]["+90170"]["cobrado"]), (4, 3.0))
        r = FB.actualizar(r, [], precio_final=lambda s, ms: 125.0, ahora=T0 + 7 * H)
        self.assertEqual((r["cerradas"][0]["como"], r["cerradas"][0]["pnl"]), ("vencida", 3.0))  # 3 cobrados + 4 al vencer − 4 invertidos

    def test_comprar_mas_suma_lo_invertido(self):
        r = FB.actualizar(None, [pos()], ahora=T0)
        r = FB.actualizar(r, [pos(cant=20, costo=0.45)], ahora=T0 + H)
        self.assertEqual(r["abiertas"]["+90170"]["invertido"], 9.0)

    def test_desaparece_un_ciclo_sin_vencer_se_espera(self):
        r = FB.actualizar(None, [pos()], ahora=T0)
        r = FB.actualizar(r, [], ahora=T0 + H // 2)
        self.assertIn("+90170", r["abiertas"])
        r = FB.actualizar(r, [], ahora=T0 + 3 * H)
        self.assertEqual((r["cerradas"][0]["como"], r["cerradas"][0]["resultado"]), ("cerrada", "sin_confirmar"))

    def test_venta_spot_y_limites_del_registro(self):
        r = FB.actualizar({"cerradas": [cerrada("ganada", 1, horas=24 * 40)] + [cerrada("ganada", 1)] * 70}, [],
                          ops=[{"accion": "VENDER", "simbolo": "BTC", "total": 102, "pnl": 2.0, "orden": "x"}, {"accion": "VENDER", "simbolo": "ETH", "total": 50, "pnl": 3, "simulada": True}], ahora=T0)
        self.assertEqual((r["cerradas"][0]["tipo"], r["cerradas"][0]["etiqueta"], r["cerradas"][0]["pnl"], r["cerradas"][0]["resultado"]), ("spot", "BTC", 2.0, "ganada"))
        self.assertEqual(len(r["cerradas"]), FB.MAX_CERRADAS)
        self.assertTrue(all(T0 - c["ts"] < FB.DIAS * FB.DIA for c in r["cerradas"]))


class Resumen(unittest.TestCase):
    def test_cuentas_de_24_h_y_7_dias(self):
        reg = {"cerradas": [cerrada("ganada", 2, 1), cerrada("perdida", -3, 30), cerrada("ganada", 1, 24 * 8), cerrada("sin_confirmar", None, 2),
                            cerrada("ganada", 5, 1, tipo="spot", cat="spot")], "abiertas": {"+1": {}}}
        s = FB.resumir(reg, T0)
        self.assertEqual(s["hoy"], {"n": 1, "ganadas": 1, "perdidas": 0, "pnl": 2})
        self.assertEqual(s["semana"], {"n": 2, "ganadas": 1, "perdidas": 1, "pnl": -1})
        self.assertEqual((s["spot_semana"]["n"], s["sin_confirmar"], s["abiertas"]), (1, 1, 1))
        self.assertEqual(s["ajustes"], {"conf_extra": {}, "conf_extra_todo": 0, "max_ciclo": None})

    def test_categoria_que_pierde_pide_mas_confianza(self):
        reg = {"cerradas": [cerrada("ganada", 1, 50), cerrada("perdida", -2, 60), cerrada("perdida", -2, 70), cerrada("perdida", -2, 80),
                            cerrada("ganada", 3, 50, cat="bolsa")]}
        s = FB.resumir(reg, T0)
        self.assertEqual(s["ajustes"]["conf_extra"], {"cripto_hoy": 10})
        self.assertEqual((FB.conf_extra(s, "cripto_hoy"), FB.conf_extra(s, "bolsa"), FB.conf_extra(None, "bolsa")), (10, 0, 0))
        self.assertTrue(any("cripto_hoy: 1 de 4 ganadas" in t for t in s["lecciones"]))

    def test_pocas_muestras_no_ajustan(self):
        s = FB.resumir({"cerradas": [cerrada("perdida", -2, 50), cerrada("perdida", -2, 60), cerrada("perdida", -2, 70)]}, T0)
        self.assertEqual(s["ajustes"]["conf_extra"], {})

    def test_racha_de_perdidas_en_24_h(self):
        s = FB.resumir({"cerradas": [cerrada("perdida", -1, 1), cerrada("perdida", -1, 2), cerrada("perdida", -1, 3), cerrada("ganada", 1, 4)]}, T0)
        self.assertEqual((s["ajustes"]["conf_extra_todo"], s["ajustes"]["max_ciclo"]), (10, 1))
        s = FB.resumir({"cerradas": [cerrada("ganada", 1, 1), cerrada("perdida", -1, 2), cerrada("perdida", -1, 3), cerrada("perdida", -1, 4)]}, T0)
        self.assertEqual(s["ajustes"]["conf_extra_todo"], 0)  # la última fue ganada: se cortó la racha

    def test_aciertos_por_modelo(self):
        v = [{"modelo": "a", "accion": "COMPRAR"}, {"modelo": "b", "accion": "NO ENTRAR"}]
        s = FB.resumir({"cerradas": [cerrada("ganada", 1, 1, votos=v), cerrada("perdida", -1, 2, votos=v), cerrada("perdida", -1, 3, votos=v)]}, T0)
        self.assertEqual(s["modelos"], {"a": {"votos": 3, "aciertos": 1}, "b": {"votos": 3, "aciertos": 2}})

    def test_texto_para_la_ia(self):
        self.assertIsNone(FB.para_ia(FB.resumir({}, T0)))
        self.assertIsNone(FB.para_ia(None))
        t = FB.para_ia(FB.resumir({"cerradas": [cerrada("ganada", 2, 1, conf=90), cerrada("perdida", -3, 30, conf=66)]}, T0))
        self.assertIn("últimas 24 h 1 ganadas y 0 perdidas (+2.00 USDC)", t)
        self.assertIn("7 días 1 ganadas y 1 perdidas (-1.00 USDC)", t)
        self.assertIn("cripto_hoy 1/2", t)
        self.assertIn("85+: 1/1", t)


class Ejecutor(unittest.TestCase):
    def proponer(self, fb, conf=80, cfg=None):
        ops = {"#90170": {**OPS["#90170"], "confianza": conf}}
        return PR.proponer({**CFG, **(cfg or {})}, [mercado()], [], [], 50, lambda c, p: (ops, "m", None), libro(), fb=fb)

    def test_sin_feedback_compra_como_siempre_y_guarda_categoria_y_votos(self):
        ops = {"#90170": {**OPS["#90170"], "votos": [{"modelo": "a", "accion": "COMPRAR", "confianza": 80}]}}
        nuevas, _, _ = PR.proponer(CFG, [mercado()], [], [], 50, lambda c, p: (ops, "m", None), libro())
        self.assertEqual((nuevas[0]["categoria"], nuevas[0]["ia"]["votos"][0]["modelo"]), ("cripto_hoy", "a"))

    def test_categoria_que_pierde_exige_mas_y_lo_dice(self):
        fb = {"ajustes": {"conf_extra": {"cripto_hoy": 10}, "conf_extra_todo": 0, "max_ciclo": None}}
        nuevas, notas, _ = self.proponer(fb, conf=70)
        self.assertEqual(nuevas, [])
        self.assertTrue(any("Feedback" in n and "se exige 75" in n for n in notas))
        nuevas, _, _ = self.proponer(fb, conf=76)
        self.assertEqual(len(nuevas), 1)

    def test_otra_categoria_no_se_frena(self):
        fb = {"ajustes": {"conf_extra": {"bolsa": 10}, "conf_extra_todo": 0, "max_ciclo": None}}
        self.assertEqual(len(self.proponer(fb, conf=70)[0]), 1)

    def test_racha_baja_las_compras_por_ciclo_pero_nunca_las_sube(self):
        fb = {"ajustes": {"conf_extra": {}, "conf_extra_todo": 0, "max_ciclo": 1}}
        ops = {"#90170": OPS["#90170"], "#90180": {**OPS["#90170"]}}
        ms = [mercado(), mercado(mid=9018)]
        n, _, _ = PR.proponer({**CFG, "pred_max_ciclo": 3}, ms, [], [], 50, lambda c, p: (ops, "m", None), libro(), fb=fb)
        self.assertEqual(len(n), 1)
        n, _, _ = PR.proponer({**CFG, "pred_max_ciclo": 3}, ms, [], [], 50, lambda c, p: (ops, "m", None), libro())
        self.assertEqual(len(n), 2)


class EnElCiclo(unittest.TestCase):
    """Ciclo completo: el feedback llega al pedido de las IAs y al supervisor, frena la
    compra si la categoría viene perdiendo, y el registro vuelve en el reporte."""

    def ciclo(self, activo):
        import json
        import os
        import re
        import time
        from unittest import mock

        import ia
        import kumo_runner as K
        from test_consenso import LISTA
        from test_pred_ia import HLFalso, lista

        ahora = int(time.time() * 1000)
        mal = [{"ts": ahora - k * H, "tipo": "prediccion", "etiqueta": "x", "categoria": "cripto_hoy", "origen": "ia", "confianza": 70, "como": "vencida",
                "resultado": "perdida", "pnl": -2.0} for k in (30, 40, 50, 60)]
        textos = []

        def falso(url, clave, modelo, mensajes, formato_json=True):
            textos.append((("super" if "supervisor" in mensajes[0]["content"] else "pred"), mensajes[-1]["content"]))
            if textos[-1][0] == "super":
                return json.dumps({"cambios": {}, "razon": "r"})
            coins = sorted(set(re.findall(r'"coin": "(#\d+)"', mensajes[-1]["content"])))
            return json.dumps({"opiniones": [{"coin": c, "accion": "COMPRAR", "confianza": 62, "prob": 85, "razon": "r"} for c in coins]})

        remoto = {"config": {"modo": "real", "quote": "USDC", "objetivo": {}, "ia": "veto", "pred_ia": "proponer", "ordenes_ia": "off",
                             "pred_ventaja": 8, "pred_conf_min": 55, "pred_feedback": activo},
                  "ordenes": [], "estado_runner": {"feedback": {"abiertas": {}, "cerradas": mal}}, "ia_proveedores": LISTA, "ia_consenso": {"enabled": False}}
        try:
            with mock.patch.dict(os.environ, {"EXCHANGE_ID": "hyperliquid", "EXCHANGE_API_KEY": "0xabc", "EXCHANGE_SECRET": "x"}), \
                    mock.patch.object(K, "crear_exchange", lambda *a: HLFalso()), \
                    mock.patch.object(K, "http", lambda *a, **k: {"mercados": lista()}), \
                    mock.patch.object(K, "url_nube", lambda: "https://nube"), \
                    mock.patch.object(K.ia, "opinar", lambda *a: ({}, None, None)), \
                    mock.patch.object(ia, "_llamar", falso):
                rep = K.ciclo(remoto)
        finally:
            ia.configurar(None, None)
        return rep, textos

    def test_activo_avisa_a_las_ias_y_frena_la_compra(self):
        rep, textos = self.ciclo(True)
        pred = [t for q, t in textos if q == "pred"][0]
        self.assertIn("Resultados reales de este bot en predicciones", pred)
        self.assertIn("cripto_hoy 0/4", pred)
        self.assertIn("Resultados reales de este bot", [t for q, t in textos if q == "super"][0])
        self.assertEqual([o for o in rep["ordenes_nuevas"] if o.get("tipo") == "prediccion"], [])
        self.assertTrue(any(n.startswith("Feedback: la IA quería comprar") and "se exige 65" in n for n in rep["notas"]), rep["notas"])
        r = rep["feedback_resumen"]
        self.assertEqual((r["activo"], r["semana"]["perdidas"], r["ajustes"]["conf_extra"], len(r["recientes"])), (True, 4, {"cripto_hoy": 10}, 4))
        self.assertEqual(len(rep["feedback"]["cerradas"]), 4)

    def test_apagado_no_avisa_ni_frena_pero_sigue_anotando(self):
        rep, textos = self.ciclo(False)
        self.assertNotIn("Resultados reales", [t for q, t in textos if q == "pred"][0])
        self.assertEqual(len([o for o in rep["ordenes_nuevas"] if o.get("tipo") == "prediccion"]), 1)
        self.assertEqual((rep["feedback_resumen"]["activo"], len(rep["feedback"]["cerradas"])), (False, 4))


if __name__ == "__main__":
    unittest.main()
