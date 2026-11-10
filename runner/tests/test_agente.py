"""Ciclo del agente: simulación en papel, resultados oficiales, aprobación en real."""
import os
import time
import unittest
from unittest import mock

import agente as AG
import exchanges
from test_pred_ia import mercado

H = 3_600_000


class HL:
    """Hyperliquid falso (info pública): libro, precios, metadatos y resultados oficiales."""
    def __init__(self, ask=0.42, bid=0.38, liquidado=None):
        self.ask, self.bid, self.liquidado = ask, bid, liquidado or {}

    def __call__(self, c):
        t = c.get("type")
        if t == "l2Book":
            return {"levels": [[{"px": str(self.bid), "sz": "500"}], [{"px": str(self.ask), "sz": "500"}]]}
        if t == "allMids":
            return {"#90170": "0.41", "#90171": "0.59"}
        if t == "outcomeMeta":
            return {"outcomes": [{"outcome": 9017, "name": "template:binaryPrice", "description": "perp:SOL|threshold:120|time:20991231-0000",
                                  "sideSpecs": [{"name": "template:Yes"}, {"name": "template:No"}]}], "questions": []}
        if t == "settledOutcome":
            f = self.liquidado.get(c["outcome"])
            return None if f is None else {"settleFraction": str(f), "details": "template"}
        return {}


OPS = {"#90170": {"accion": "COMPRAR", "confianza": 80, "prob": 85, "razon": "lejos del objetivo"}}


def correr(config=None, estado=None, ordenes=None, hl=None, lista=None, importar=None, opiniones=OPS, env=None):
    hl = hl or HL()
    remoto = {"config": {"modo": "simulacion", "pred_ia": "auto", "ia": "proponer", "pred_auto_solo_corto": False, "pred_monto": 11, "pred_max_total": 30, "pred_ventaja": 8,
                         "pred_categorias": ["cripto_hoy"], **(config or {})},
              "estado_runner": estado or {}, "ordenes": ordenes or [], "importar": importar}
    lista = [mercado()] if lista is None else lista
    with mock.patch.dict(os.environ, env or {}, clear=False), \
            mock.patch.object(AG.K, "http", lambda *a, **k: {"mercados": lista, "global": None}), \
            mock.patch.object(AG.K, "url_nube", lambda: "https://nube"), \
            mock.patch.object(AG.K, "tomar_ordenes", lambda ids: set(ids)), \
            mock.patch.object(AG.ia, "opinar_predicciones", lambda *a, **k: (opiniones, "modelo-x", None)), \
            mock.patch.object(exchanges.CcxtExchange, "orden_prediccion", side_effect=AssertionError("¡orden REAL en simulación!")):
        return AG.ciclo(remoto, info=hl), hl


class Simulacion(unittest.TestCase):
    def test_compra_en_papel_con_precio_real_del_libro_y_anota(self):
        r, _ = correr()
        self.assertEqual(r["modo"], "simulacion")
        (op,) = r["ejecutadas"]
        self.assertTrue(op["simulada"])
        self.assertEqual((op["precio"], op["cantidad"]), (0.42, 27.0))  # ~11 USDC a 0.42 (redondeo de Kumo)
        self.assertEqual(r["papel"]["posiciones"], {"+90170": 27.0})
        self.assertAlmostEqual(r["papel"]["usdc"], 100 - 27 * 0.42, places=4)
        (d,) = r["aprendizaje"]["decisiones"]
        self.assertEqual((d["mercado"], d["lado"], d["accion"], d["precio"], d["y"]), (9017, 0, "comprar", 0.42, None))

    def test_al_vencer_cobra_con_el_resultado_oficial_y_aprende(self):
        r1, _ = correr()
        estado = {"papel": r1["papel"], "aprendizaje": {**r1["aprendizaje"]}}
        estado["aprendizaje"]["decisiones"][0]["vence"] = int(time.time() * 1000) - H  # ya venció
        r2, _ = correr(estado=estado, hl=HL(liquidado={9017: 1.0}), lista=[])  # el mercado ya no está abierto
        liq = [e for e in r2["ejecutadas"] if e["accion"] == "LIQUIDACIÓN"]
        self.assertEqual(len(liq), 1)
        self.assertEqual(r2["papel"]["posiciones"], {})
        self.assertAlmostEqual(r2["papel"]["usdc"], 100 - 27 * 0.42 + 27, places=4)
        d = r2["aprendizaje"]["decisiones"][0]
        self.assertEqual(d["y"], 1.0)
        self.assertEqual(r2["aprendizaje_resumen"]["compras"], 1)
        self.assertTrue(any("resultado oficial" in n for n in r2["notas"]))

    def test_si_no_esta_liquidado_no_cobra_ni_lo_da_por_perdido(self):
        r1, _ = correr()
        r2, _ = correr(estado={"papel": r1["papel"], "aprendizaje": r1["aprendizaje"]}, lista=[])
        self.assertEqual(r2["papel"]["posiciones"], {"+90170": 27.0})

    def test_en_simulacion_sin_piloto_solo_propone(self):
        r, _ = correr(config={"pred_ia": "proponer"})
        self.assertEqual(r["ejecutadas"], [])
        (o,) = r["ordenes_nuevas"]
        self.assertEqual((o["accion"], o.get("estado", "propuesta")), ("COMPRAR", "propuesta"))
        self.assertEqual(r["papel"]["posiciones"], {})

    def test_piloto_solo_corto_plazo_deja_propuesto_lo_lejano(self):
        r, _ = correr(config={"pred_auto_solo_corto": True, "pred_corto_horas": 2})  # el mercado vence en 6 h
        self.assertEqual(r["ejecutadas"], [])
        self.assertIn("corto plazo", r["ordenes_nuevas"][0]["nota"])

    def test_sin_mercados_de_la_nube_no_decide(self):
        with mock.patch.object(AG.K, "http", side_effect=OSError("caída")):
            remoto = {"config": {"modo": "simulacion", "pred_ia": "proponer", "ia": "proponer"}, "estado_runner": {}, "ordenes": []}
            r = AG.ciclo(remoto, info=HL())
        self.assertEqual(r["ejecutadas"], [])
        self.assertTrue(any("no decidí nada" in e for e in r["errores"]), r["errores"])

    def test_en_pausa_no_compra(self):
        r, _ = correr(config={"pausado": True})
        self.assertEqual(r["ejecutadas"], [])

    def test_importa_de_kumo_una_sola_vez(self):
        imp = [{"tipo": "prediccion", "coin": "#88880", "accion": "COMPRAR", "estado": "ejecutada", "limite": 0.5, "ia": {"prob": 70}, "ts": int(time.time() * 1000) - 10 * H, "horas": 5}]
        r, _ = correr(importar=imp, opiniones={})
        self.assertEqual(r["importadas"], 1)
        self.assertTrue(any(d["mercado"] == 8888 and d["origen"] == "kumo" for d in r["aprendizaje"]["decisiones"]))


class Real(unittest.TestCase):
    ENV = {"EXCHANGE_API_KEY": "0xabc", "EXCHANGE_SECRET": "0xdef"}

    def test_real_sin_api_sigue_en_simulacion(self):
        with mock.patch.dict(os.environ, {"EXCHANGE_API_KEY": "", "EXCHANGE_SECRET": ""}):
            r, _ = correr(config={"modo": "real"})
        self.assertEqual(r["modo"], "simulacion")
        self.assertTrue(any("sigo en simulación" in e for e in r["errores"]))

    def test_real_con_propone_solo_deja_propuestas(self):
        hl = HL()
        falso = mock.MagicMock()
        falso._info_hl = hl
        falso.predicciones, falso.entradas = {}, {}
        falso.saldos.return_value = {"USDC": 50.0}
        falso.mejor_precio_prediccion.side_effect = lambda cod, comprar: {"precio": 0.42 if comprar else 0.38, "unidades": 500}
        with mock.patch.object(AG, "crear_exchange", lambda *a: falso):
            r, _ = correr(config={"modo": "real", "pred_ia": "proponer"}, env=self.ENV, hl=hl)
        self.assertEqual(r["modo"], "real")
        falso.orden_prediccion.assert_not_called()
        (o,) = r["ordenes_nuevas"]
        self.assertEqual((o["accion"], o.get("estado", "propuesta")), ("COMPRAR", "propuesta"))
        self.assertEqual(r["aprendizaje"]["decisiones"][0]["accion"], "propuesta")  # una propuesta no cuenta como compra


class GiroAprobado(unittest.TestCase):
    def test_venta_de_giro_aprobada_vende_y_propone_el_otro_lado(self):
        hl = HL()
        falso = mock.MagicMock()
        falso._info_hl = hl
        falso.predicciones, falso.entradas = {}, {}
        falso.saldos.return_value = {"USDC": 50.0}
        falso.mejor_precio_prediccion.side_effect = lambda cod, comprar: {"precio": 0.42 if comprar else 0.28, "unidades": 500}
        falso.orden_prediccion.return_value = {"cantidad": 20.0, "precio": 0.28, "total": 5.6, "id": 1}
        venta = {"id": "g1", "tipo": "prediccion", "coin": "#90171", "accion": "VENDER", "unidades": 20, "limite": 0.28, "permitir_perdida": True,
                 "giro": 9017, "estado": "aprobada", "origen": "ia", "etiqueta": "¿SOL ≥ 120 al vencer? · No"}
        with mock.patch.object(AG, "crear_exchange", lambda *a: falso):
            r, _ = correr(config={"modo": "real", "pred_giro": "girar", "pred_ia": "off"}, env=Real.ENV, hl=hl, ordenes=[venta])
        falso.orden_prediccion.assert_called_once_with(90171, False, 20, 0.28)  # solo lo que aprobaste
        self.assertIn("9017", r["giros"])
        (c,) = [o for o in r["ordenes_nuevas"] if o["accion"] == "COMPRAR"]
        self.assertEqual((c["coin"], c["estado"], c["giro"]), ("#90170", "propuesta", 9017))


if __name__ == "__main__":
    unittest.main()
