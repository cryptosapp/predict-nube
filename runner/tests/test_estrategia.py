import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import estrategia as E  # noqa: E402


def cfg(**k):
    return {**E.CONFIG_DEFECTO, "objetivo": {"BTC": 50}, "quote": "USDT", **k}


class Pruebas(unittest.TestCase):
    def test_rsi_extremos(self):
        self.assertEqual(E.rsi([float(i) for i in range(1, 40)]), 100.0)
        self.assertLess(E.rsi([float(i) for i in range(40, 1, -1)]), 5)
        self.assertIsNone(E.rsi([1, 2, 3]))

    def test_compra_bajo_objetivo_y_rsi_bajo(self):
        p, _ = E.proponer(cfg(), {"USDT": 100}, {"BTC": 50000}, {"BTC": {"rsi": 30}}, {})
        self.assertEqual(p[0]["accion"], "COMPRAR")
        self.assertEqual(p[0]["monto"], 25.0)  # tope monto_max

    def test_no_compra_con_rsi_alto(self):
        p, _ = E.proponer(cfg(), {"USDT": 100}, {"BTC": 50000}, {"BTC": {"rsi": 55}}, {})
        self.assertEqual(p, [])

    def test_nunca_vende_con_perdida(self):
        saldos = {"USDT": 10, "BTC": 0.002}  # BTC = 90% de la cartera
        costos = {"BTC": {"costo": 50000}}
        p, notas = E.proponer(cfg(), saldos, {"BTC": 45000}, {"BTC": {"rsi": 80}}, costos)
        self.assertEqual(p, [])
        self.assertTrue(any("ganancia" in n for n in notas))

    def test_stop_respeta_nunca_vender_con_perdida(self):
        saldos = {"USDT": 10, "BTC": 0.002}
        costos = {"BTC": {"costo": 50000}}
        p, _ = E.proponer(cfg(stop_perdida=5), saldos, {"BTC": 45000}, {"BTC": {"rsi": 50}}, costos)
        self.assertEqual(p, [])
        p, _ = E.proponer(cfg(stop_perdida=5, nunca_vender_con_perdida=False), saldos, {"BTC": 45000}, {"BTC": {"rsi": 50}}, costos)
        self.assertTrue(p[0].get("stop"))

    def test_vende_sobrepeso_con_ganancia(self):
        saldos = {"USDT": 10, "BTC": 0.002}
        p, _ = E.proponer(cfg(), saldos, {"BTC": 60000}, {"BTC": {"rsi": 50}}, {"BTC": {"costo": 50000}})
        self.assertEqual(p[0]["accion"], "VENDER")
        self.assertLessEqual(p[0]["monto"], 25.0)

    def test_ia_veto_y_confirmar(self):
        prop = [{"simbolo": "BTC", "accion": "COMPRAR"}]
        ok, no = E.aplicar_ia(cfg(), prop, {"BTC": {"accion": "VENDER", "confianza": 80}})
        self.assertEqual((len(ok), len(no)), (0, 1))
        ok, no = E.aplicar_ia(cfg(), prop, {"BTC": {"accion": "VENDER", "confianza": 40}})
        self.assertEqual(len(ok), 1)
        ok, no = E.aplicar_ia(cfg(ia="confirmar"), prop, {})
        self.assertEqual(len(ok), 0)

    def test_costo_promedio(self):
        c = {"BTC": {"costo": 100}}
        E.registrar_compra(c, "BTC", 1, 1, 200)
        self.assertEqual(c["BTC"]["costo"], 150)


if __name__ == "__main__":
    unittest.main()
