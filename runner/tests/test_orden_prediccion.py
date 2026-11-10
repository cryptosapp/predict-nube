import os
import unittest
from unittest import mock

import kumo_runner as K
import ordenes as O
from exchanges import CcxtExchange, ErrorExchange


class Ex:
    def __init__(self, lleno):
        self.lleno, self.llamadas = lleno, []

    def orden_prediccion(self, cod, comprar, unidades, limite):
        self.llamadas.append((cod, comprar, unidades, limite))
        if self.lleno is None:
            raise ErrorExchange("Hyperliquid: Insufficient balance")
        return {"cantidad": self.lleno, "precio": 0.51, "total": self.lleno * 0.51, "id": 7}


ORD = {"id": "p1", "tipo": "prediccion", "coin": "#90150", "simbolo": "#90150", "etiqueta": "¿BTC ≥ 85 501? · Sí", "accion": "COMPRAR", "unidades": 20, "limite": 0.52}


class OrdenPrediccion(unittest.TestCase):
    def test_compra_llena(self):
        ex = Ex(20)
        upd, op = O.ejecutar(ex, ORD, {}, {}, {}, {"quote": "USDC"}, False)
        self.assertEqual(ex.llamadas, [(90150, True, 20, 0.52)])
        self.assertEqual((upd["estado"], upd["resultado"]["cantidad"], op["simbolo"], op["prediccion"]), ("ejecutada", 20, "¿BTC ≥ 85 501? · Sí", "#90150"))

    def test_parcial_y_sin_llenar(self):
        upd, _ = O.ejecutar(Ex(5), ORD, {}, {}, {}, {"quote": "USDC"}, False)
        self.assertIn("5 de 20", upd["nota"])
        upd, op = O.ejecutar(Ex(0), ORD, {}, {}, {}, {"quote": "USDC"}, False)
        self.assertEqual((upd["estado"], op), ("error", None))
        self.assertIn("No se llenó", upd["error"])

    def test_error_y_simulacion(self):
        upd, _ = O.ejecutar(Ex(None), ORD, {}, {}, {}, {"quote": "USDC"}, False)
        self.assertIn("Insufficient", upd["error"])
        ex = Ex(20)
        upd, _ = O.ejecutar(ex, ORD, {}, {}, {}, {"quote": "USDC"}, True)
        self.assertEqual((upd["estado"], ex.llamadas), ("error", []))

    def test_accion_firmada(self):
        ex = CcxtExchange("hyperliquid", "USDC", "0x" + "0" * 39 + "1", "0x" + "11" * 32)
        vistos = {}
        ex.ex.private_post_exchange = lambda req: vistos.update(req) or {"status": "ok", "response": {"data": {"statuses": [{"filled": {"totalSz": "20", "avgPx": "0.5", "oid": 1}}]}}}
        r = ex.orden_prediccion(90151, False, 20, 0.4)
        o = vistos["action"]["orders"][0]
        self.assertEqual((o["a"], o["b"], o["p"], o["s"], o["t"]), (100090151, False, "0.4", "20", {"limit": {"tif": "Ioc"}}))
        self.assertEqual(list(o), ["a", "b", "p", "s", "r", "t"])  # orden de campos que exige Hyperliquid
        self.assertIn("r", vistos["signature"])
        self.assertEqual((r["cantidad"], r["total"]), (20, 10.0))
        with self.assertRaises(ErrorExchange):
            ex.orden_prediccion(90151, True, 1, 1.2)

    def test_tu_error_no_se_lleno_explica_el_mejor_precio(self):
        import ccxt
        ex = CcxtExchange("hyperliquid", "USDC", "0x" + "0" * 39 + "1", "0x" + "11" * 32)
        msg = 'hyperliquid {"status":"ok","response":{"type":"order","data":{"statuses":[{"error":"Order could not immediately match against any resting orders. asset=100090170"}]}}}'
        def falla(req):
            raise ccxt.ExchangeError(msg)
        ex.ex.private_post_exchange = falla
        ex._info_hl = lambda c: {"levels": [[{"px": "0.81001", "sz": "15.0"}], [{"px": "0.86033", "sz": "40.0"}]]}
        o = dict(ORD, coin="#90170", accion="VENDER", unidades=2, limite=0.827)
        upd, op = O.ejecutar(ex, o, {}, {}, {}, {"quote": "USDC"}, False)
        self.assertEqual((upd["estado"], op), ("error", None))
        self.assertNotIn("hyperliquid {", upd["error"])
        self.assertIn("nadie compraba a 0.827", upd["error"])
        self.assertIn("El mejor comprador pagaba 0.81001 (15 u.)", upd["error"])
        self.assertEqual(upd["mejor"], {"precio": 0.81001, "unidades": 15.0})
        # Comprando, la pista es el vendedor más barato.
        upd, _ = O.ejecutar(ex, dict(o, accion="COMPRAR", limite=0.8), {}, {}, {}, {"quote": "USDC"}, False)
        self.assertIn("El más barato vendía a 0.86033", upd["error"])

    def test_otro_error_de_hyperliquid_legible(self):
        import ccxt
        ex = CcxtExchange("hyperliquid", "USDC", "0x" + "0" * 39 + "1", "0x" + "11" * 32)
        def falla(req):
            raise ccxt.ExchangeError('hyperliquid {"status":"ok","response":{"type":"order","data":{"statuses":[{"error":"Insufficient spot balance asset=100090170"}]}}}')
        ex.ex.private_post_exchange = falla
        with self.assertRaises(ErrorExchange) as c:
            ex.orden_prediccion(90170, False, 2, 0.8)
        self.assertEqual(str(c.exception), "Hyperliquid: Insufficient spot balance asset=100090170")

    def test_ciclo_no_busca_precio_de_predicciones(self):
        from tests.test_ciclo_ordenes import Pub
        remoto = {"config": {"quote": "USDC", "objetivo": {}, "ia": "off"}, "estado_runner": {"cartera_sim": {"USDC": 100}}, "ordenes": [dict(ORD, estado="aprobada")]}
        with mock.patch.dict(os.environ, {"EXCHANGE_ID": "prueba", "EXCHANGE_API_KEY": ""}), mock.patch.object(K, "crear_publico", lambda *a: Pub()):
            r = K.ciclo(remoto)
        self.assertEqual(r["ordenes_upd"][0]["estado"], "error")
        self.assertIn("solo se opera en papel", r["ordenes_upd"][0]["error"])


if __name__ == "__main__":
    unittest.main()


class CostoDeCompra(unittest.TestCase):
    def test_saldos_lee_lo_que_pagaste(self):
        ex = CcxtExchange("hyperliquid", "USDC", "0x" + "0" * 39 + "1", "0x" + "11" * 32)
        ex._cuenta_lista = True
        # Formato real de spotClearinghouseState (entryNtl = USDC pagados en total).
        ex.ex.fetch_balance = lambda *a, **k: {"total": {"USDC": 40.0, "+90171": 13.0, "HYPE": 1.0},
                                              "info": {"balances": [{"coin": "USDC", "total": "40.0", "entryNtl": "0.0"},
                                                                    {"coin": "+90171", "total": "13.0", "hold": "0.0", "entryNtl": "10.14"},
                                                                    {"coin": "HYPE", "total": "1.0", "entryNtl": "88.0"}]}}
        saldos = ex.saldos()
        self.assertEqual((saldos, ex.predicciones), ({"USDC": 40.0, "HYPE": 1.0}, {"+90171": 13.0}))
        self.assertAlmostEqual(ex.entradas["+90171"], 0.78)
        self.assertEqual(ex.entradas["HYPE"], 88.0)  # también el costo real de tus monedas spot
