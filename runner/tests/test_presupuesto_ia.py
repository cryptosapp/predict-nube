"""La IA tiene un tope de tiempo por ciclo para que el ciclo de GitHub dure menos de 1 min."""
import time
import unittest
from unittest import mock

import ia


class Presupuesto(unittest.TestCase):
    def tearDown(self):
        ia.LIMITE_TS = None

    def test_sin_tope_usa_el_timeout_normal(self):
        ia.LIMITE_TS = None
        self.assertEqual(ia._timeout(), ia.TIMEOUT)

    def test_con_tope_la_llamada_espera_solo_lo_que_queda(self):
        ia.LIMITE_TS = time.time() + 12
        self.assertLessEqual(ia._timeout(), 12)
        self.assertGreater(ia._timeout(), 10)

    def test_sin_tiempo_no_llama_y_lo_dice(self):
        ia.LIMITE_TS = time.time() + 1
        with self.assertRaises(ia.SinTiempo):
            ia._timeout()
        with mock.patch.object(ia.urllib.request, "urlopen", side_effect=AssertionError("no debía llamar")):
            with self.assertRaises(ia.SinTiempo):
                ia._llamar("https://x", "k", "m", [{"role": "user", "content": "hola"}])

    def test_consenso_corta_y_deja_el_error_visible(self):
        ia.LIMITE_TS = time.time() + 1
        with mock.patch.object(ia, "elegir_votantes", lambda n: [({"nombre": "p"}, "m1"), ({"nombre": "p"}, "m2")]):
            ok, errores = ia.consultar_varias([], 2)
        self.assertEqual(ok, [])
        self.assertTrue(any("sin tiempo en este ciclo" in e["error"] for e in errores), errores)


if __name__ == "__main__":
    unittest.main()
