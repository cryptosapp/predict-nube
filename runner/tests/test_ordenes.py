import unittest

import ordenes as O
from exchanges import Simulador

CFG = {"quote": "USDC", "monto_min": 11, "monto_max": 25, "ia": "veto", "ia_conf_min": 65,
       "ordenes_ia": "proponer", "nunca_vender_con_perdida": True}


class Pub:
    id, quote = "prueba", "USDC"

    def __init__(self, precios):
        self.p = precios

    def existe(self, s):
        return s in self.p

    def precios(self, s):
        return {k: {"precio": self.p[k]} for k in s if k in self.p}


def sim(cartera, precios):
    return Simulador(Pub(precios), dict(cartera))


class Ordenes(unittest.TestCase):
    def test_compra_aprobada_se_ejecuta(self):
        ex = sim({"USDC": 50}, {"HYPE": 40.0})
        upd, op = O.ejecutar(ex, {"id": "u1", "simbolo": "HYPE", "accion": "COMPRAR", "monto": 12, "origen": "usuario"},
                             ex.saldos(), {"HYPE": 40.0}, {}, CFG, True)
        self.assertEqual(upd["estado"], "ejecutada")
        self.assertEqual(op["orden"], "u1")
        self.assertLess(ex.saldos()["USDC"], 50)

    def test_limite_no_alcanzado_espera(self):
        ex = sim({"USDC": 50}, {"HYPE": 40.0})
        upd, op = O.ejecutar(ex, {"id": "u2", "simbolo": "HYPE", "accion": "COMPRAR", "monto": 12, "limite": 38},
                             ex.saldos(), {"HYPE": 40.0}, {}, CFG, True)
        self.assertEqual((upd["estado"], op), ("aprobada", None))
        self.assertIn("Esperando precio", upd["nota"])

    def test_sin_saldo_espera(self):
        ex = sim({"USDC": 10.53}, {"HYPE": 40.0})
        upd, _ = O.ejecutar(ex, {"id": "u3", "simbolo": "HYPE", "accion": "COMPRAR", "monto": 12}, ex.saldos(), {"HYPE": 40.0}, {}, CFG, True)
        self.assertEqual(upd["estado"], "aprobada")
        self.assertIn("insuficiente", upd["nota"])

    def test_venta_con_perdida_espera_salvo_permiso(self):
        ex = sim({"USDC": 0, "HYPE": 1}, {"HYPE": 30.0})
        o = {"id": "u4", "simbolo": "HYPE", "accion": "VENDER", "pct": 100}
        costos = {"HYPE": {"costo": 40.0}}
        upd, op = O.ejecutar(ex, o, ex.saldos(), {"HYPE": 30.0}, costos, CFG, True)
        self.assertEqual((upd["estado"], op), ("aprobada", None))
        upd, op = O.ejecutar(ex, {**o, "permitir_perdida": True}, ex.saldos(), {"HYPE": 30.0}, costos, CFG, True)
        self.assertEqual(upd["estado"], "ejecutada")

    def test_ia_propone_sin_repetir(self):
        op = {"HYPE": {"accion": "COMPRAR", "confianza": 80, "razon": "x"}, "BTC": {"accion": "ESPERAR", "confianza": 90},
              "SOL": {"accion": "COMPRAR", "confianza": 50}, "ETH": {"accion": "VENDER", "confianza": 90}}
        nuevas = O.de_la_ia(CFG, op, {"USDC": 40, "ETH": 0.01}, {"HYPE": 40, "BTC": 1, "SOL": 1, "ETH": 2000},
                            [{"simbolo": "ETH", "accion": "VENDER"}])
        self.assertEqual([(o["simbolo"], o["accion"]) for o in nuevas], [("HYPE", "COMPRAR")])
        self.assertTrue(11 <= nuevas[0]["monto"] <= 25)

    def test_ia_apagada_no_propone(self):
        op = {"HYPE": {"accion": "COMPRAR", "confianza": 99}}
        self.assertEqual(O.de_la_ia({**CFG, "ordenes_ia": "off"}, op, {"USDC": 40}, {"HYPE": 40}, []), [])


if __name__ == "__main__":
    unittest.main()
