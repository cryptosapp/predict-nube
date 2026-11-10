import unittest

import ordenes as O
from exchanges import ErrorExchange

ORD = {"id": "p1", "tipo": "prediccion", "coin": "#90170", "etiqueta": "¿SOL ≥ 120? · Sí", "accion": "COMPRAR", "unidades": 25, "limite": 0.44}


class HL:
    """Hyperliquid de mentira: USDC, HYPE a 40 y una predicción."""
    def __init__(self, usdc=2.0, hype=1.0, ask=0.44, lleno=True, falla_venta=False):
        self.s = {"USDC": usdc, "HYPE": hype}
        self.ask, self.lleno, self.falla_venta, self.log = ask, lleno, falla_venta, []

    def saldos(self):
        return dict(self.s)

    def precios(self, sims):
        return {"HYPE": {"precio": 40.0}}

    def vender(self, sym, cant, precio_min=None):
        if self.falla_venta:
            raise ErrorExchange("Hyperliquid: rechazada")
        self.log.append(("vender", sym, round(cant, 6)))
        self.s[sym] -= cant
        self.s["USDC"] += cant * 40
        return {"cantidad": cant, "precio": 40.0, "total": cant * 40, "id": 1}

    def mejor_precio_prediccion(self, cod, comprar):
        return {"precio": self.ask, "unidades": 100}

    def orden_prediccion(self, cod, comprar, unidades, limite):
        self.log.append(("prediccion", cod, unidades, limite))
        n = unidades if self.lleno else 0
        self.s["USDC"] -= n * limite
        return {"cantidad": n, "precio": limite, "total": n * limite, "id": 2, "mejor": {"precio": 0.5, "unidades": 3}}


CFG = {"pred_pagar_con": "HYPE"}


class PagarConHype(unittest.TestCase):
    def test_vende_solo_lo_que_falta(self):
        ex = HL(usdc=2.0)
        upd, op = O.ejecutar(ex, ORD, {}, {}, {}, CFG, False)
        costo, falta = 25 * 0.44, 25 * 0.44 - 2.0
        self.assertEqual(ex.log[0][:2], ("vender", "HYPE"))
        self.assertAlmostEqual(ex.log[0][2] * 40, max(falta * 1.02 + 0.05, 10.5), places=4)
        self.assertEqual(ex.log[1], ("prediccion", 90170, 25, 0.44))
        self.assertEqual((upd["estado"], upd["_op_pago"]["simbolo"], upd["_op_pago"]["accion"]), ("ejecutada", "HYPE", "VENDER"))
        self.assertIn("Vendí", upd["nota"])
        self.assertEqual(op["prediccion"], "#90170")
        self.assertGreater(ex.s["USDC"], 0)

    def test_con_usdc_suficiente_no_vende(self):
        ex = HL(usdc=50)
        upd, _ = O.ejecutar(ex, ORD, {}, {}, {}, CFG, False)
        self.assertEqual([x[0] for x in ex.log], ["prediccion"])
        self.assertNotIn("_op_pago", upd)

    def test_sin_opcion_no_vende(self):
        ex = HL(usdc=2.0)
        O.ejecutar(ex, ORD, {}, {}, {}, {}, False)
        self.assertEqual([x[0] for x in ex.log], ["prediccion"])

    def test_opcion_por_orden(self):
        ex = HL(usdc=2.0)
        O.ejecutar(ex, {**ORD, "pagar_con": "HYPE"}, {}, {}, {}, {}, False)
        self.assertEqual([x[0] for x in ex.log], ["vender", "prediccion"])

    def test_no_vende_si_no_se_llenaria(self):
        ex = HL(usdc=2.0, ask=0.60)
        upd, _ = O.ejecutar(ex, ORD, {}, {}, {}, CFG, False)
        self.assertEqual((ex.log, upd["estado"]), ([], "error"))
        self.assertIn("No vendí nada", upd["error"])

    def test_hype_insuficiente_o_venta_falla(self):
        ex = HL(usdc=2.0, hype=0.1)
        upd, _ = O.ejecutar(ex, ORD, {}, {}, {}, CFG, False)
        self.assertEqual(ex.log, [])
        self.assertIn("no tienes suficiente HYPE", upd["error"])
        ex = HL(usdc=2.0, falla_venta=True)
        upd, _ = O.ejecutar(ex, ORD, {}, {}, {}, CFG, False)
        self.assertIn("No pude vender HYPE", upd["error"])

    def test_vendio_pero_no_se_lleno(self):
        ex = HL(usdc=2.0, lleno=False)
        upd, _ = O.ejecutar(ex, ORD, {}, {}, {}, CFG, False)
        self.assertEqual(upd["estado"], "error")
        self.assertIn("quedan libres", upd["error"])
        self.assertIn("_op_pago", upd)  # la venta sí ocurrió y se reporta

    def test_vender_prediccion_no_usa_hype(self):
        ex = HL(usdc=0)
        O.ejecutar(ex, {**ORD, "accion": "VENDER"}, {}, {}, {}, CFG, False)
        self.assertEqual([x[0] for x in ex.log], ["prediccion"])


if __name__ == "__main__":
    unittest.main()


class EleccionPorOrden(unittest.TestCase):
    def test_orden_solo_usdc_aunque_config_diga_hype(self):
        ex = HL(usdc=2.0)
        O.ejecutar(ex, {**ORD, "pagar_con": "USDC"}, {}, {}, {}, CFG, False)
        self.assertEqual([x[0] for x in ex.log], ["prediccion"])
