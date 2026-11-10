import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import kumo_runner as K  # noqa: E402
from exchanges import ErrorExchange  # noqa: E402


class Falso:
    def __init__(self, saldos=None, perps=0.0, error=None):
        self._s, self._p, self._e = saldos or {}, perps, error

    def saldos(self):
        if self._e:
            raise ErrorExchange(self._e)
        return self._s

    def saldo_perps(self):
        return self._p


class SaldoReal(unittest.TestCase):
    def test_spot(self):
        notas = []
        r = K.saldo_real(Falso({"USDC": 50.126, "HYPE": 2}), "hyperliquid", "USDC", None, notas)
        self.assertEqual((r["libre"], r["estables"], r["saldos"]["HYPE"], notas), (50.13, 50.13, 2, []))

    def test_usdc_en_perps(self):
        notas = []
        r = K.saldo_real(Falso({}, perps=30), "hyperliquid", "USDC", None, notas)
        self.assertEqual(r["perps_usdc"], 30)
        self.assertIn("Perps a Spot", notas[0])

    def test_direccion_vacia(self):
        notas = []
        K.saldo_real(Falso({}), "hyperliquid", "USDC", None, notas)
        self.assertIn("cuenta principal", notas[0])

    def test_error_de_claves(self):
        self.assertEqual(K.saldo_real(Falso(error="clave inválida"), "kraken", "USD"), {"error": "clave inválida"})


if __name__ == "__main__":
    unittest.main()
