import unittest
from unittest import mock

import ia
import kumo_runner as K


class Senales(unittest.TestCase):
    def test_sin_propuestas_la_ia_igual_da_senales(self):
        visto = {}

        def chat(m):
            visto["texto"] = m[1]["content"]
            return {"opiniones": [{"simbolo": "BTC", "accion": "esperar", "confianza": 61, "razon": "lateral"}]}, "m1", None

        with mock.patch.object(ia, "chat_json", chat):
            op, modelo, err = ia.opinar([], {"mercado": {}}, ["BTC", "HYPE"])
        self.assertIn("BTC, HYPE", visto["texto"])
        self.assertEqual(op["BTC"]["accion"], "ESPERAR")
        self.assertEqual(modelo, "m1")

    def test_sin_nada_no_llama(self):
        with mock.patch.object(ia, "chat_json", side_effect=AssertionError):
            self.assertEqual(ia.opinar([], {}, []), ({}, None, None))

    def test_resultado_de_cada_orden(self):
        props = [{"simbolo": "SOL", "accion": "COMPRAR", "monto": 12, "motivo": "a"},
                 {"simbolo": "ETH", "accion": "VENDER", "monto": 15, "motivo": "b"},
                 {"simbolo": "HYPE", "accion": "COMPRAR", "monto": 11, "motivo": "c"}]
        op = {"SOL": {"accion": "COMPRAR", "confianza": 70, "razon": "x"}, "ETH": {"accion": "ESPERAR", "confianza": 80, "razon": "y"}}
        out = K.ordenes_ia(props, [props[1]], [{"simbolo": "SOL", "accion": "COMPRAR", "total": 12.0}],
                           {("HYPE", "COMPRAR"): "fondos insuficientes"}, False, op)
        self.assertEqual([o["resultado"] for o in out], ["ejecutada", "frenada", "error"])
        self.assertEqual(out[0]["ia"]["confianza"], 70)
        self.assertEqual(out[2]["error"], "fondos insuficientes")


if __name__ == "__main__":
    unittest.main()
