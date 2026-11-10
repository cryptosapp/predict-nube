import os
import unittest
from unittest import mock

import ia
import kumo_runner as K


class Pub:
    id, quote = "prueba", "USDC"
    P = {"HYPE": 40.123456789, "BTC": 60000.0}

    def __init__(self):
        self.pedidas = []

    def existe(self, s):
        return s in self.P

    def precios(self, s):
        return {k: {"precio": self.P[k], "cambio_24h": 0, "volumen": 1e6} for k in s if k in self.P}

    def velas_ts(self, s, marco="1h", n=100):
        self.pedidas.append((s, marco))
        return [(1_700_000_000_000 + i * 3_600_000, self.P[s] * (1 + i / 1000)) for i in range(60)]

    def velas(self, s, marco="1h", n=100):
        return [c for _, c in self.velas_ts(s, marco, n)]

    def mercados_top(self, n=20):
        return []


class Graficas(unittest.TestCase):
    def test_reporte_trae_velas_por_marco_sin_pedirlas_dos_veces(self):
        pub = Pub()
        remoto = {"config": {"quote": "USDC", "objetivo": {"BTC": 50}, "ia": "off"},
                  "estado_runner": {"cartera_sim": {"USDC": 100, "HYPE": 1}}}
        with mock.patch.dict(os.environ, {"EXCHANGE_ID": "prueba", "EXCHANGE_API_KEY": ""}), \
                mock.patch.object(K, "crear_publico", lambda *a: pub):
            r = K.ciclo(remoto)
        g = r["graficas"]
        self.assertEqual(list(g), ["HYPE", "BTC"])  # primero lo que tienes
        self.assertEqual(sorted(g["HYPE"]), ["1d", "1h", "4h"])
        self.assertEqual(len(g["HYPE"]["1h"]["t"]), 60)
        self.assertEqual(g["HYPE"]["1h"]["c"][0], 40.1235)  # 6 cifras significativas
        # Los indicadores (marco 1h) reutilizan las velas de la gráfica.
        self.assertEqual(pub.pedidas.count(("HYPE", "1h")), 1)

    def test_exchange_sin_velas_ts_no_rompe(self):
        class Viejo:
            def velas(self, s, marco="1h", n=100):
                return [1.0] * 30
        self.assertEqual(K.graficas(Viejo(), ["BTC"], {}), {})


class Preferido(unittest.TestCase):
    def tearDown(self):
        ia.PREFERIDO = None

    def test_modelo_preferido_va_primero(self):
        ia.PREFERIDO = "b"
        with mock.patch.dict(os.environ, {"IA_URL": "https://x.ai/v1", "IA_MODELOS": "a,b,c"}):
            self.assertEqual(ia.proveedor()[2], ["b", "a", "c"])
        ia.PREFERIDO = "zz"
        with mock.patch.dict(os.environ, {"IA_URL": "https://x.ai/v1", "IA_MODELOS": "a,b"}):
            self.assertEqual(ia.proveedor()[2], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
