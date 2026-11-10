import time
import unittest

import kumo_runner as K
import predicciones as PR

AHORA = int(time.time() * 1000)


def mercado(mid=9017, si=0.40, prob=92.0, horas=6, categoria="cripto", plazo="hoy", sin_ofertas=False):
    """Un mercado como lo arma la nube (GET /runner/predicciones)."""
    return {"mercado": mid, "pregunta": "¿SOL ≥ 120 al vencer?", "regla": "«Sí» gana si SOL está en 120 o más al vencer.", "tipo": "precio",
            "categoria": categoria, "plazo": plazo, "subyacente": "SOL", "nombre_sub": "SOL", "objetivo": 120, "precio_actual": 125, "distancia_pct": 4.2,
            "vence": AHORA + horas * 3600_000, "horas": horas, "cambio_24h_pct": 1.0, "sin_ofertas": sin_ofertas or None,
            "lados": [{"nombre": "Sí", "coin": f"#{mid * 10}", "precio": si, "prob_modelo": prob, "ventaja": round(prob - si * 100, 1)},
                      {"nombre": "No", "coin": f"#{mid * 10 + 1}", "precio": round(1 - si, 3), "prob_modelo": round(100 - prob, 1), "ventaja": round(si * 100 - prob, 1)}]}


def lista(**kw):
    return [mercado(**kw)]


def opinar_fijo(resp):
    llamadas = []

    def f(cands, pos):
        llamadas.append((cands, pos))
        return resp, "modelo-x", None
    f.llamadas = llamadas
    return f


CFG = {"pred_ia": "proponer", "pred_monto": 11, "pred_max_total": 30, "pred_ventaja": 8, "ia_conf_min": 65}


OPS = {"#90170": {"accion": "COMPRAR", "confianza": 80, "prob": 85, "razon": "SOL lejos del objetivo"}}


def libro(ask=0.42, bid=0.38):
    return lambda cod, comprar: ({"precio": ask if comprar else bid, "unidades": 100} if (ask if comprar else bid) else None)


class Proponer(unittest.TestCase):
    def test_compra_con_ia_de_acuerdo(self):
        op = opinar_fijo(OPS)
        nuevas, notas, res = PR.proponer(CFG, lista(), [], [], 50, op, libro(0.42))
        self.assertEqual(len(nuevas), 1)
        o = nuevas[0]
        self.assertEqual((o["tipo"], o["coin"], o["accion"], o["unidades"], o["limite"], o["origen"]), ("prediccion", "#90170", "COMPRAR", 27, 0.42, "ia"))
        self.assertEqual((res["candidatos"], res["modelo"], len(res["opiniones"])), (1, "modelo-x", 1))
        self.assertIn("regla", op.llamadas[0][0][0])

    def test_ia_dice_no_o_poca_confianza(self):
        for r in ({"#90170": {"accion": "NO ENTRAR", "confianza": 90, "prob": 40, "razon": ""}},
                  {"#90170": {"accion": "COMPRAR", "confianza": 50, "prob": 85, "razon": ""}},
                  {"#90170": {"accion": "COMPRAR", "confianza": 90, "prob": 41, "razon": ""}}):
            nuevas, _, _ = PR.proponer(CFG, lista(), [], [], 50, opinar_fijo(r))
            self.assertEqual(nuevas, [], r)

    def test_tope_y_saldo(self):
        pos = [{"coin": "+90151", "valor": 25, "precio": 0.5, "cantidad": 50, "pregunta": "¿BTC?", "lado_nombre": "Sí"}]
        # Quedan 5 USDC de tope (30 − 25): compra solo lo que cabe (12 u. × 0.40 = 4.80).
        nuevas, notas, _ = PR.proponer({**CFG, "pred_vender_ia": False}, lista(), pos, [], 50, opinar_fijo(OPS))
        self.assertEqual(nuevas[0]["unidades"], 12)
        # Con 3 USDC libres compra 7 u. × 0.40 = 2.80 (el mínimo en predicciones es ~1 USDC, no 10).
        nuevas, notas, _ = PR.proponer(CFG, lista(), [], [], 3, opinar_fijo(OPS))
        self.assertEqual(nuevas[0]["unidades"], 7)
        # Tope lleno o menos de ~1 USDC libre: nada, y lo explica.
        pos[0]["valor"] = 30
        nuevas, notas, _ = PR.proponer({**CFG, "pred_vender_ia": False}, lista(), pos, [], 50, opinar_fijo(OPS))
        self.assertEqual(nuevas, [])
        self.assertIn("tope", notas[-1])
        nuevas, notas, _ = PR.proponer(CFG, lista(), [], [], 0.6, opinar_fijo(OPS))
        self.assertEqual(nuevas, [])
        self.assertIn("mínimo ~1 USDC", notas[-1])
        self.assertIn("libres", notas[0])

    def test_no_repite_mercado_con_posicion_o_pendiente(self):
        op = opinar_fijo(OPS)
        pend = [{"tipo": "prediccion", "coin": "#90171", "accion": "COMPRAR", "unidades": 10, "limite": 0.5}]
        nuevas, _, res = PR.proponer(CFG, lista(), [], pend, 50, op)
        self.assertEqual((nuevas, res["candidatos"]), ([], 0))
        self.assertEqual(op.llamadas, [])  # sin nada que revisar no se gasta IA

    def test_categorias_horas_y_probabilidad(self):
        for cfg, kw in (({}, {"plazo": "mediano"}), ({}, {"categoria": "bolsa"}), ({}, {"horas": 0.5}), ({}, {"si": 0.03, "prob": 50}),
                        ({"pred_prob_max": 35}, {}), ({}, {"sin_ofertas": True}), ({"pred_ventaja": 60}, {})):
            op = opinar_fijo(OPS)
            nuevas, _, res = PR.proponer({**CFG, **cfg}, lista(**kw), [], [], 50, op)
            self.assertEqual((nuevas, op.llamadas), ([], []), (cfg, kw))
        nuevas, _, _ = PR.proponer({**CFG, "pred_categorias": ["cripto_mediano", "bolsa"]}, lista(plazo="mediano"), [], [], 50, opinar_fijo(OPS))
        self.assertEqual(len(nuevas), 1)

    def test_libro_filtra_antes_de_la_ia(self):
        for ask in (None, 0.90, 0.47):  # sin vendedores · sin ventaja real · demasiado lejos del medio
            op = opinar_fijo(OPS)
            nuevas, notas, _ = PR.proponer(CFG, lista(), [], [], 50, op, libro(ask))
            self.assertEqual((nuevas, op.llamadas), ([], []), ask)
            self.assertIn("libro real", notas[0])

    def test_varias_compras_por_ciclo(self):
        l = [mercado(9017), mercado(9018), mercado(9019)]
        ops = {f"#{m * 10}": OPS["#90170"] for m in (9017, 9018, 9019)}
        nuevas, _, _ = PR.proponer({**CFG, "pred_max_ciclo": 2}, l, [], [], 50, opinar_fijo(ops))
        self.assertEqual(len(nuevas), 2)

    def test_vende_posicion(self):
        pos = [{"coin": "+90171", "valor": 6, "precio": 0.6, "cantidad": 10.0, "pregunta": "¿SOL ≥ 120?", "lado_nombre": "No", "vence": AHORA + 3600_000,
                "costo": 0.5, "min_venta": PR.precio_minimo_venta(0.5)}]
        op = opinar_fijo({"#90171": {"accion": "VENDER", "confianza": 75, "prob": 10, "razon": "SOL muy arriba"}})
        nuevas, _, res = PR.proponer(CFG, lista(), pos, [], 50, op)
        self.assertEqual([(o["coin"], o["accion"], o["unidades"]) for o in nuevas], [("#90171", "VENDER", 10)])
        self.assertEqual(res["posiciones"], 1)
        self.assertLess(op.llamadas[0][1][0]["ventaja_pts"], 0)
        op = opinar_fijo({})
        PR.proponer({**CFG, "pred_vender_ia": False}, lista(), pos, [], 50, op)
        self.assertEqual(op.llamadas, [])  # no revisa tus posiciones si lo apagas

    def test_asegurar_ganancia_sin_ia(self):
        pos = [{"coin": "+90170", "valor": 9.6, "precio": 0.96, "cantidad": 10.0, "pregunta": "¿SOL ≥ 120?", "lado_nombre": "Sí", "vence": AHORA + 3600_000,
                "costo": 0.7, "min_venta": PR.precio_minimo_venta(0.7)}]
        op = opinar_fijo({})
        nuevas, _, _ = PR.proponer({**CFG, "pred_tomar_ganancia": 95, "pred_vender_ia": False}, lista(), pos, [], 50, op)
        self.assertEqual([(o["coin"], o["accion"], o["regla"]) for o in nuevas], [("#90170", "VENDER", True)])
        self.assertIn("Asegurar ganancia", nuevas[0]["razon"])
        self.assertEqual(op.llamadas, [])


class Base:
    """Exchange de mentira para predicciones_ia: libro y órdenes."""
    def __init__(self, ask=0.42, bid=0.38):
        self.ask, self.bid, self.ordenes = ask, bid, []

    def mejor_precio_prediccion(self, cod, comprar):
        p = self.ask if comprar else self.bid
        return {"precio": p, "unidades": 100} if p else None


class EnElCiclo(unittest.TestCase):
    def setUp(self):
        from unittest import mock
        self.p = [mock.patch.object(K.ia, "opinar_predicciones", lambda c, p, *_: (OPS, "m", None)),
                  mock.patch.object(K, "http", lambda *a, **k: {"mercados": lista()}),
                  mock.patch.object(K, "url_nube", lambda: "https://nube")]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()

    def test_usa_el_libro(self):
        nuevas, res = K.predicciones_ia(Base(ask=0.44), None, CFG, [], [], 50, [])
        o = nuevas[0]
        self.assertEqual((o["limite"], o["unidades"], o["estado"]), (0.44, 25, "propuesta"))
        self.assertGreater(o["caduca"], AHORA)

    def test_nube_caida_no_tumba_el_ciclo(self):
        from unittest import mock
        notas = []
        with mock.patch.object(K, "http", mock.Mock(side_effect=OSError("sin red"))):
            self.assertEqual(K.predicciones_ia(Base(), None, CFG, [], [], 50, notas), ([], None))
        self.assertIn("OSError", notas[0])


class HLFalso(Base):
    """Cuenta real de Hyperliquid de mentira para un ciclo completo."""
    id, quote, con_claves = "hyperliquid", "USDC", True

    def __init__(self):
        super().__init__(ask=0.44)
        self.usdc, self.predicciones = 50.0, {}

    def _info_hl(self, c):
        return {}

    def saldos(self):
        return {"USDC": self.usdc}

    def precios(self, s):
        return {}

    def velas(self, s, marco="1h", n=100):
        return []

    def mercados_top(self, n=20):
        return []

    def orden_prediccion(self, cod, comprar, unidades, limite):
        self.ordenes.append((cod, comprar, unidades, limite))
        self.usdc -= unidades * limite
        return {"cantidad": unidades, "precio": limite, "total": unidades * limite, "id": 1}


class CicloAuto(unittest.TestCase):
    def correr(self, modo):
        import os
        from unittest import mock
        hl = HLFalso()
        remoto = {"config": {"modo": "real", "quote": "USDC", "objetivo": {}, "ia": "veto", "pred_ia": modo, "ordenes_ia": "off"}, "ordenes": []}
        with mock.patch.dict(os.environ, {"EXCHANGE_ID": "hyperliquid", "EXCHANGE_API_KEY": "0xabc", "EXCHANGE_SECRET": "x", "KUMO_RUNNER_TOKEN": "t"}), \
                mock.patch.object(K, "crear_exchange", lambda *a: hl), \
                mock.patch.object(K, "http", lambda *a, **k: {"mercados": lista()}), \
                mock.patch.object(K, "url_nube", lambda: "https://nube"), \
                mock.patch.object(K.ia, "opinar", lambda *a: ({}, None, None)), \
                mock.patch.object(K.ia, "opinar_predicciones", lambda c, p, *_: (OPS, "m", None)):
            return K.ciclo(remoto), hl

    def test_proponer_no_toca_dinero(self):
        r, hl = self.correr("proponer")
        self.assertEqual(hl.ordenes, [])
        self.assertEqual([(o["coin"], o["estado"]) for o in r["ordenes_nuevas"]], [("#90170", "propuesta")])
        self.assertEqual(r["pred_ia"]["candidatos"], 1)

    def test_auto_ejecuta_al_mejor_precio(self):
        r, hl = self.correr("auto")
        self.assertEqual(hl.ordenes, [(90170, True, 25, 0.44)])
        self.assertEqual(r["ordenes_nuevas"][0]["estado"], "ejecutada")
        self.assertEqual(r["ejecutadas"][0]["prediccion"], "#90170")
        self.assertEqual(r["libre"], 39.0)

    def test_off(self):
        r, hl = self.correr("off")
        self.assertEqual((r["ordenes_nuevas"], r["pred_ia"]), ([], None))


class Pregunta(unittest.TestCase):
    def test_plantillas_nuevas(self):
        P = lambda name, desc, q=None: PR._pregunta({"outcome": 1, "name": name, "description": desc}, {1: q} if q else {})
        self.assertEqual(P("template:binaryPrice", "perp:xyz:SP500|priceDescription:x|threshold:7590|time:20261007-2000"), "¿S&P 500 ≥ 7 590 al vencer?")
        self.assertEqual(P("template:priceTouch", "perp:BTC|priceDescription:x|target:92500|time:20261101-0000"), "¿BTC toca 92 500 antes de vencer?")
        self.assertEqual(P("template:policyRateDecrease", "", {"description": "decisionLabel:October 2026 FOMC|institution:Federal Reserve"}), "¿La Fed baja la tasa de interés (October 2026 FOMC)?")
        self.assertEqual(P("Recurring Named Outcome", "index:1", {"description": "class:priceBucket|underlying:BTC|priceThresholds:82638,86011"}), "¿BTC entre 82 638 y 86 011 al vencer?")


if __name__ == "__main__":
    unittest.main()


class SoloCortoPlazo(unittest.TestCase):
    def correr(self, horas, **cfg):
        import os
        from unittest import mock
        hl = HLFalso()
        remoto = {"config": {"modo": "real", "quote": "USDC", "objetivo": {}, "ia": "veto", "pred_ia": "auto", "ordenes_ia": "off",
                             "pred_categorias": ["cripto_hoy", "cripto_mediano"], **cfg}, "ordenes": []}
        plazo = "hoy" if horas <= 36 else "mediano"
        with mock.patch.dict(os.environ, {"EXCHANGE_ID": "hyperliquid", "EXCHANGE_API_KEY": "0xabc", "EXCHANGE_SECRET": "x"}), \
                mock.patch.object(K, "crear_exchange", lambda *a: hl), \
                mock.patch.object(K, "http", lambda *a, **k: {"mercados": lista(horas=horas, plazo=plazo)}), \
                mock.patch.object(K, "url_nube", lambda: "https://nube"), \
                mock.patch.object(K.ia, "opinar", lambda *a: ({}, None, None)), \
                mock.patch.object(K.ia, "opinar_predicciones", lambda c, p, *_: (OPS, "m", None)), \
                mock.patch.object(K.ia, "chat_json", lambda m: (None, None, "x")):
            return K.ciclo(remoto), hl

    def test_corto_plazo_ejecuta_solo(self):
        r, hl = self.correr(6)
        self.assertEqual((len(hl.ordenes), r["ordenes_nuevas"][0]["estado"]), (1, "ejecutada"))

    def test_mediano_plazo_solo_propone(self):
        r, hl = self.correr(200)
        o = r["ordenes_nuevas"][0]
        self.assertEqual((hl.ordenes, o["estado"]), ([], "propuesta"))
        self.assertIn("necesita tu aprobación", o["nota"])

    def test_tu_limite_de_corto_plazo_y_apagarlo(self):
        r, hl = self.correr(60, pred_corto_horas=72)
        self.assertEqual(r["ordenes_nuevas"][0]["estado"], "ejecutada")
        r, hl = self.correr(200, pred_auto_solo_corto=False)
        self.assertEqual(r["ordenes_nuevas"][0]["estado"], "ejecutada")


class MercadoGlobal(unittest.TestCase):
    """Volatilidad 1 h / 24 h / 48 h y mercado global en la decisión."""

    def test_turbulencia_global_exige_mas_ventaja(self):
        # ventaja 11 (modelo 51 % vs precio 0.40): pasa con 8, no con 8 + 4 de turbulencia
        glob = {"turbulencia": True, "resumen": "Turbulencia en BTC."}
        nuevas, _, res = PR.proponer(CFG, lista(prob=51.0), [], [], 50, opinar_fijo(OPS), None, glob)
        self.assertEqual((nuevas, res["candidatos"], res["turbulencia"]), ([], 0, True))
        nuevas, notas, _ = PR.proponer(CFG, lista(prob=51.0), [], [], 50, opinar_fijo(OPS))
        self.assertEqual(len(nuevas), 1)
        _, notas, _ = PR.proponer(CFG, lista(prob=51.0), [], [], 50, opinar_fijo(OPS), None, glob)
        self.assertTrue(any("turbulencia" in n for n in notas))

    def test_turbulencia_del_subyacente(self):
        m = mercado(prob=51.0)
        m["regimen"] = "turbulento"
        nuevas, _, _ = PR.proponer(CFG, [m], [], [], 50, opinar_fijo(OPS))
        self.assertEqual(nuevas, [])
        m["lados"][0]["ventaja"] = 13.0
        nuevas, _, _ = PR.proponer(CFG, [m], [], [], 50, opinar_fijo(OPS))
        self.assertEqual(len(nuevas), 1)

    def test_la_ia_recibe_volatilidad_y_global(self):
        from unittest import mock
        m = mercado()
        m.update({"mov": {"h1": 0.4, "h24": 2.1, "h48": 3.0}, "regimen": "normal", "cambio_1h_pct": 0.2})
        visto = {}

        def opinar(c, p, glob=None, noticias=None, feedback=None):
            visto.update(cands=c, glob=glob)
            return OPS, "m", None
        glob = {"resumen": "24 h: cripto +1%, bolsa −0.4%.", "turbulencia": False}
        with mock.patch.object(K.ia, "opinar_predicciones", opinar), \
                mock.patch.object(K, "http", lambda *a, **k: {"mercados": [m], "global": glob}), \
                mock.patch.object(K, "url_nube", lambda: "https://nube"):
            nuevas, res = K.predicciones_ia(Base(ask=0.42), None, CFG, [], [], 50, [])
        self.assertEqual(visto["glob"], glob)
        self.assertEqual(visto["cands"][0]["mov_tipico_1h_24h_48h_pct"], [0.4, 2.1, 3.0])
        self.assertEqual(res["global"], glob["resumen"])
        self.assertEqual(len(nuevas), 1)

    def test_prompt_lleva_el_global(self):
        from unittest import mock
        import ia
        pedidos = []
        with mock.patch.object(ia, "chat_json", lambda msgs: (pedidos.append(msgs[1]["content"]) or ({"opiniones": []}, "m", None))):
            ia.opinar_predicciones([{"coin": "#1"}], [], {"resumen": "Aversión al riesgo."})
        self.assertIn("Aversión al riesgo.", pedidos[0])
        self.assertIn("1 h, 24 h", pedidos[0])


class SinPerdidas(unittest.TestCase):
    """Nunca vender una predicción con pérdida, por mínima que sea."""

    def pos(self, precio, costo):
        return [{"coin": "+90171", "valor": precio * 10, "precio": precio, "cantidad": 10.0, "pregunta": "¿S&P 500 ≥ 7 800?", "lado_nombre": "No",
                 "vence": AHORA + 3600_000, "costo": costo, "min_venta": PR.precio_minimo_venta(costo)}]

    VENDE = {"#90171": {"accion": "VENDER", "confianza": 90, "prob": 20, "razon": "cambió el panorama"}}

    def test_precio_minimo(self):
        self.assertEqual(PR.precio_minimo_venta(0.7253), 0.729)  # +0.5 % (candado: P/L mínimo), redondeado hacia arriba
        self.assertEqual(PR.precio_minimo_venta(0.2), 0.201)  # al menos +0.001
        self.assertIsNone(PR.precio_minimo_venta(None))

    def test_ia_no_vende_por_debajo_de_lo_que_pagaste(self):
        # Los dos casos reales: compró a 0.7253 y vendió a 0.69; compró a 0.78 y vendió a 0.7192.
        for precio, costo in ((0.69, 0.7253), (0.7192, 0.78), (0.7253, 0.7253)):
            nuevas, notas, _ = PR.proponer({**CFG, "pred_categorias": []}, lista(), self.pos(precio, costo), [], 50, opinar_fijo(self.VENDE))
            self.assertEqual(nuevas, [], (precio, costo))
            self.assertTrue(any("no vendo con pérdida" in n for n in notas))

    def test_con_ganancia_si_vende_y_nunca_bajo_el_minimo(self):
        nuevas, _, _ = PR.proponer({**CFG, "pred_categorias": []}, lista(), self.pos(0.80, 0.7253), [], 50, opinar_fijo(self.VENDE))
        self.assertEqual(len(nuevas), 1)
        self.assertGreaterEqual(nuevas[0]["limite"], PR.precio_minimo_venta(0.7253))

    def test_sin_costo_conocido_no_vende(self):
        pos = self.pos(0.9, None)
        nuevas, notas, _ = PR.proponer({**CFG, "pred_categorias": []}, lista(), pos, [], 50, opinar_fijo(self.VENDE))
        self.assertEqual(nuevas, [])
        self.assertTrue(any("no sé a cuánto" in n for n in notas))

    def test_asegurar_ganancia_respeta_el_costo(self):
        cfg = {**CFG, "pred_tomar_ganancia": 60, "pred_vender_ia": False, "pred_categorias": []}
        nuevas, _, _ = PR.proponer(cfg, lista(), self.pos(0.65, 0.78), [], 50, opinar_fijo({}))
        self.assertEqual(nuevas, [])

    def test_la_ia_recibe_lo_que_pagaste(self):
        op = opinar_fijo({})
        PR.proponer({**CFG, "pred_categorias": []}, lista(), self.pos(0.69, 0.7253), [], 50, op)
        r = op.llamadas[0][1][0]
        self.assertEqual((r["pagaste"], r["vender_solo_desde"], r["puede_vender_sin_perdida"]), (0.7253, 0.729, False))

    def test_en_el_ciclo_el_mejor_comprador_por_debajo_no_vende(self):
        from unittest import mock
        pos = self.pos(0.80, 0.7253)
        with mock.patch.object(K.ia, "opinar_predicciones", lambda c, p, *_: (self.VENDE, "m", None)), \
                mock.patch.object(K, "http", lambda *a, **k: {"mercados": lista()}), mock.patch.object(K, "url_nube", lambda: "https://nube"):
            notas = []
            nuevas, _ = K.predicciones_ia(Base(bid=0.72), None, {**CFG, "pred_categorias": []}, pos, [], 50, notas)
            self.assertEqual(nuevas, [])
            self.assertTrue(any("no vendo con pérdida" in n for n in notas), notas)
            nuevas, _ = K.predicciones_ia(Base(bid=0.79), None, {**CFG, "pred_categorias": []}, pos, [], 50, [])
            self.assertEqual((nuevas[0]["accion"], nuevas[0]["limite"]), ("VENDER", 0.79))

    def test_ultimo_candado_al_ejecutar(self):
        import ordenes as O

        class Ex:
            entradas = {"+90171": 0.78}

            def __init__(self):
                self.ordenes = []

            def orden_prediccion(self, cod, comprar, unidades, limite):
                self.ordenes.append((cod, comprar, unidades, limite))
                return {"cantidad": unidades, "precio": limite, "total": unidades * limite}
        o = {"id": "x", "tipo": "prediccion", "coin": "#90171", "accion": "VENDER", "unidades": 13, "limite": 0.7192, "origen": "ia"}
        ex = Ex()
        upd, op = O.ejecutar_prediccion(ex, o, False)
        self.assertEqual((upd["estado"], op, ex.ordenes), ("error", None, []))
        self.assertIn("con pérdida", upd["error"])
        # Tú puedes vender con pérdida si lo marcas a propósito.
        upd, op = O.ejecutar_prediccion(ex, {**o, "origen": "usuario", "permitir_perdida": True}, False)
        self.assertEqual(upd["estado"], "ejecutada")
        # Con ganancia, sí.
        upd, _ = O.ejecutar_prediccion(ex, {**o, "limite": 0.80}, False)
        self.assertEqual(upd["estado"], "ejecutada")
        # Sin costo conocido: la IA no vende; tú sí.
        ex.entradas = {}
        self.assertEqual(O.ejecutar_prediccion(ex, o, False)[0]["estado"], "error")
        self.assertEqual(O.ejecutar_prediccion(ex, {**o, "origen": "usuario"}, False)[0]["estado"], "ejecutada")

    def test_describir_trae_el_costo(self):
        info = lambda c: {"outcomes": [], "questions": []} if c["type"] == "outcomeMeta" else {"#90171": "0.69"}
        p = PR.describir({"+90171": 10}, info, {"+90171": 0.7253})[0]
        self.assertEqual((p["costo"], p["min_venta"], p["pnl_pct"]), (0.7253, 0.7297, -4.87))  # mercado desconocido: se supone comisión ×2


class CreadoresExternos(unittest.TestCase):
    """Mercados de out / skew / trade.xyz: se muestran con su creador y, si cobran
    comisión de creador (deployerFeeScale), el mínimo sin pérdida sube."""

    META = {"outcomes": [
        {"outcome": 7581, "name": "template:binaryPrice", "description": "perp:BTC|priceDescription:the Pyth BTC/USD price|seconds:90|threshold:85090|time:20261009-0800",
         "sideSpecs": [{"name": "template:Yes"}, {"name": "template:No"}], "venue": "skew", "deployerFeeScale": "1.0"},
        {"outcome": 7352, "name": "template:binaryPrice", "description": "perp:HYPE|priceDescription:1-minute candle|seconds:300|threshold:98|time:20261030-2000",
         "sideSpecs": [{"name": "template:Yes"}, {"name": "template:No"}], "venue": "txyz"}], "questions": []}

    def info(self, c):
        return self.META if c["type"] == "outcomeMeta" else {"#75811": "0.9", "#73520": "0.5"}

    def test_minimo_con_comision_doble(self):
        self.assertEqual(PR.precio_minimo_venta(0.7253), 0.729)
        self.assertEqual(PR.precio_minimo_venta(0.7253, 1.0), 0.7297)

    def test_describir_muestra_creador(self):
        r = PR.describir({"+75811": 10.0, "+73520": 5.0}, self.info, {"+75811": 0.7253, "+73520": 0.4})
        d = {p["coin"]: p for p in r}
        self.assertEqual((d["+75811"]["creador"], d["+75811"]["comision_doble"], d["+75811"]["min_venta"]), ("skew", True, 0.7297))
        self.assertEqual((d["+73520"]["creador"], d["+73520"]["comision_doble"]), ("trade.xyz", None))

    def test_bloqueo_final_usa_comision_del_creador(self):
        import ordenes as O
        PR._META.update(datos=None, ts=0)

        class Ex:
            entradas = {"+75811": 0.7253}
            _info_hl = staticmethod(self.info)
            def orden_prediccion(s, *a):
                raise AssertionError("no debía vender")
        o = {"id": "x", "tipo": "prediccion", "coin": "#75811", "accion": "VENDER", "unidades": 10, "limite": 0.728, "origen": "ia"}
        upd, op = O.ejecutar_prediccion(Ex(), o, False)
        self.assertIsNone(op)
        self.assertIn("sin pérdida desde 0.7297", upd["error"])

    def test_sin_meta_supone_comision_alta(self):
        PR._META.update(datos=None, ts=0)
        def falla(c):
            raise OSError("sin red")
        self.assertEqual(PR.escala_comision(falla, 7581), 1.0)
