import os
import unittest
from unittest import mock

import kumo_runner as K


class ReporteError(unittest.TestCase):
    def test_error_dice_con_que_exchange_fallo(self):
        enviados = []

        def http(metodo, url, token, cuerpo=None):
            if metodo == "GET":
                return {"config": {"modo": "real"}}
            enviados.append(cuerpo)
            return {"ciclo": 20}

        with mock.patch.dict(os.environ, {"KUMO_URL": "https://x", "KUMO_RUNNER_TOKEN": "t", "EXCHANGE_ID": "Hyperliquid"}), \
                mock.patch.object(K, "http", http), mock.patch.object(K, "ciclo", side_effect=RuntimeError("boom")):
            K.main()
        r = enviados[0]
        self.assertFalse(r["ok"])
        self.assertEqual(r["exchange"], "hyperliquid")
        self.assertEqual(r["modo"], "real")
        self.assertIn("boom", r["errores"][0])


if __name__ == "__main__":
    unittest.main()
