import os
import unittest
from unittest import mock

import ia
import kumo_runner as K


class Pub:
    id, quote = "prueba", "USDC"
    P = {"HYPE": 40.0, "BTC": 60000.0}

    def existe(self, s):
        return s in self.P

    def precios(self, s):
        return {k: {"precio": self.P[k], "cambio_24h": 0, "volumen": 1e6} for k in s if k in self.P}

    def velas(self, s, marco="1h", n=100):
        return [self.P[s]] * 60

    def mercados_top(self, n=20):
        return []


class CicloOrdenes(unittest.TestCase):
    def test_ciclo_ejecuta_aprobada_y_la_ia_propone(self):
        remoto = {"config": {"quote": "USDC", "objetivo": {"BTC": 0}, "monto_min": 11, "monto_max": 20, "ia": "veto", "ordenes_ia": "proponer"},
                  "estado_runner": {"cartera_sim": {"USDC": 100}},
                  "ordenes": [{"id": "u1", "simbolo": "HYPE", "accion": "COMPRAR", "monto": 12, "estado": "aprobada", "origen": "usuario"},
                              {"id": "ia0", "simbolo": "HYPE", "accion": "VENDER", "pct": 50, "estado": "propuesta", "origen": "ia"}]}
        op = {"BTC": {"accion": "COMPRAR", "confianza": 90, "razon": "rebote"}}
        ia.LOG.clear()
        with mock.patch.dict(os.environ, {"EXCHANGE_ID": "prueba", "EXCHANGE_API_KEY": ""}), \
                mock.patch.object(K, "crear_publico", lambda *a: Pub()), \
                mock.patch.object(ia, "opinar", lambda *a: (op, "m1", None)):
            r = K.ciclo(remoto)
        self.assertEqual(r["ordenes_upd"], [{"id": "u1", "estado": "ejecutada", "resultado": r["ordenes_upd"][0]["resultado"]}])
        self.assertEqual(r["ejecutadas"][0]["orden"], "u1")
        self.assertEqual([(o["simbolo"], o["accion"], o["estado"]) for o in r["ordenes_nuevas"]], [("BTC", "COMPRAR", "propuesta")])
        self.assertTrue(20 >= r["ordenes_nuevas"][0]["monto"] >= 11)
        self.assertLess(r["cartera_sim"]["USDC"], 100)

    def test_pausa_no_ejecuta(self):
        remoto = {"config": {"quote": "USDC", "objetivo": {}, "pausado": True, "ia": "off"},
                  "estado_runner": {"cartera_sim": {"USDC": 100}},
                  "ordenes": [{"id": "u1", "simbolo": "HYPE", "accion": "COMPRAR", "monto": 12, "estado": "aprobada"}]}
        with mock.patch.dict(os.environ, {"EXCHANGE_ID": "prueba", "EXCHANGE_API_KEY": ""}), \
                mock.patch.object(K, "crear_publico", lambda *a: Pub()):
            r = K.ciclo(remoto)
        self.assertEqual(r["ordenes_upd"], [])
        self.assertEqual(r["cartera_sim"]["USDC"], 100)


if __name__ == "__main__":
    unittest.main()
