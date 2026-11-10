import os
import unittest
from unittest import mock

import kumo_runner as K
import predicciones as P

META = {"outcomes": [
    {"outcome": 9015, "name": "Recurring", "description": "class:priceBinary|underlying:BTC|expiry:20261007-0600|targetPrice:85501|period:1d",
     "sideSpecs": [{"name": "Yes"}, {"name": "No"}], "quoteToken": "USDC"},
    {"outcome": 1473, "name": "template:sportsTournamentParticipant", "description": "participant:Arsenal",
     "sideSpecs": [{"name": "Yes"}, {"name": "No"}], "quoteToken": "USDC"}],
    "questions": [{"question": 198, "description": "competition:English Premier League|resolutionDeadline:20270605-1200",
                   "fallbackOutcome": 1472, "namedOutcomes": [1473]}]}
MIDS = {"#90151": "0.5", "#14730": "0.25", "BTC": "60000"}


def info(c):
    return {"outcomeMeta": META, "allMids": MIDS}[c["type"]]


class Predicciones(unittest.TestCase):
    def test_separar(self):
        self.assertEqual(P.separar({"USDC": 5.8, "HYPE": 1, "+90151": 5}), ({"USDC": 5.8, "HYPE": 1}, {"+90151": 5}))

    def test_describir(self):
        btc, ars = sorted(P.describir({"+90151": 5, "+14730": 4}, info), key=lambda x: x["mercado"], reverse=True)
        self.assertEqual((btc["pregunta"], btc["lado_nombre"], btc["valor"], btc["pago_si_acierta"]), ("¿BTC ≥ 85 501 al vencer?", "No", 2.5, 5))
        self.assertEqual(btc["vence"], 1791352800000)
        self.assertEqual((ars["pregunta"], ars["lado_nombre"], ars["valor"]), ("¿Arsenal gana English Premier League?", "Sí", 1.0))
        self.assertIsNotNone(ars["vence"])

    def test_sin_red_no_rompe(self):
        def caida(c):
            raise OSError("sin red")
        r = P.describir({"+90151": 5}, caida)
        self.assertEqual((r[0]["pregunta"], r[0]["valor"]), ("Mercado 9015", None))


class Base:
    """Exchange real falso: Hyperliquid con USDC, HYPE y una predicción."""
    id, quote, con_claves, aviso_cuenta = "hyperliquid", "USDC", True, None

    def __init__(self):
        self.predicciones = {}

    def saldos(self):
        s, self.predicciones = P.separar({"USDC": 5.81, "HYPE": 0.1, "+90151": 5})
        return s

    def saldo_perps(self):
        return 0.0

    def existe(self, s):
        return s == "HYPE"

    def precios(self, s):
        return {"HYPE": {"precio": 40.0, "cambio_24h": 0, "volumen": 1}} if "HYPE" in s else {}

    def velas(self, s, marco="1h", n=100):
        return [40.0] * 60

    def mercados_top(self, n=20):
        return []

    def _info_hl(self, c):
        return info(c)


class CicloConPredicciones(unittest.TestCase):
    def test_real_suma_predicciones_y_no_las_opera(self):
        remoto = {"config": {"quote": "USDC", "objetivo": {"HYPE": 50}, "modo": "real", "ia": "off"}, "estado_runner": {}}
        with mock.patch.dict(os.environ, {"EXCHANGE_ID": "hyperliquid", "EXCHANGE_API_KEY": "0xabc", "EXCHANGE_SECRET": "k"}), \
                mock.patch.object(K, "crear_exchange", lambda *a: Base()):
            r = K.ciclo(remoto)
        self.assertEqual([p["coin"] for p in r["predicciones"]], ["+90151"])
        self.assertEqual(r["valor_predicciones"], 2.5)
        self.assertEqual(r["total"], round(5.81 + 4.0 + 2.5, 2))
        self.assertNotIn("+90151", r["real"]["saldos"])
        self.assertFalse(any("+90151" in n for n in r["notas"]))
        self.assertFalse(any(p["simbolo"].startswith("+") for p in r["propuestas"]))
        self.assertAlmostEqual(sum(a["peso"] for a in r["activos"]) + 100 * 2.5 / r["total"], 100, delta=0.1)


if __name__ == "__main__":
    unittest.main()
