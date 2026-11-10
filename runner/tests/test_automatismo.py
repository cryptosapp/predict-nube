"""Revisión del automatismo: lo que podía hacer perder dinero sin que nadie lo pidiera."""
import os
import unittest
from unittest import mock

import estrategia as E
import kumo_runner as K
import ordenes as O
from exchanges import CcxtExchange, ErrorExchange
from tests.test_ciclo_ordenes import Pub
from tests.test_pagar_con import HL, ORD as ORD_PRED

ENV = {"EXCHANGE_ID": "prueba", "EXCHANGE_API_KEY": ""}
APROBADA = {"id": "u1", "simbolo": "HYPE", "accion": "COMPRAR", "monto": 12, "estado": "aprobada", "origen": "usuario"}


def remoto(ordenes):
    return {"config": {"quote": "USDC", "objetivo": {}, "ia": "off", "monto_min": 11},
            "estado_runner": {"cartera_sim": {"USDC": 100}}, "ordenes": ordenes}


class NuncaDosVeces(unittest.TestCase):
    """Una orden aprobada se reserva en la nube antes de enviarla."""

    def correr(self, tomadas):
        with mock.patch.dict(os.environ, ENV), mock.patch.object(K, "crear_publico", lambda *a: Pub()), \
                mock.patch.object(K, "tomar_ordenes", lambda ids: tomadas):
            return K.ciclo(remoto([APROBADA]))

    def test_solo_ejecuta_las_que_la_nube_le_dio(self):
        r = self.correr(set())  # otro ciclo ya la tomó
        self.assertEqual((r["ordenes_upd"], r["ejecutadas"]), ([], []))
        r = self.correr({"u1"})
        self.assertEqual(r["ordenes_upd"][0]["estado"], "ejecutada")

    def test_sin_respuesta_de_la_nube_no_ejecuta_ninguna(self):
        r = self.correr(None)
        self.assertEqual(r["ejecutadas"], [])
        self.assertTrue(any("no ejecuto ninguna" in e for e in r["errores"]))

    def test_las_que_esperan_su_precio_no_se_reservan(self):
        vistos = []
        o = dict(APROBADA, limite=30.0)  # HYPE vale 40: aún no
        with mock.patch.dict(os.environ, ENV), mock.patch.object(K, "crear_publico", lambda *a: Pub()), \
                mock.patch.object(K, "tomar_ordenes", lambda ids: vistos.extend(ids) or set(ids)):
            r = K.ciclo(remoto([o]))
        self.assertEqual((vistos, r["ordenes_upd"]), ([], []))

    def test_si_el_ciclo_se_cae_el_reporte_lleva_lo_ejecutado(self):
        enviado = {}

        def http(metodo, url, token, cuerpo=None):
            if url.endswith("/runner/config"):
                return remoto([APROBADA])
            enviado.update(cuerpo)
            return {"ciclo": 1}
        with mock.patch.dict(os.environ, {**ENV, "KUMO_URL": "https://nube", "KUMO_RUNNER_TOKEN": "t"}), \
                mock.patch.object(K, "crear_publico", lambda *a: Pub()), mock.patch.object(K, "http", http), \
                mock.patch.object(K, "tomar_ordenes", lambda ids: set(ids)), \
                mock.patch.object(E, "valorar", side_effect=RuntimeError("boom")):
            K.main()
        self.assertFalse(enviado["ok"])
        self.assertEqual(enviado["ordenes_upd"][0]["estado"], "ejecutada")
        self.assertEqual(enviado["ejecutadas"][0]["orden"], "u1")

    def test_el_reporte_se_reintenta(self):
        llamadas = []

        def http(*a):
            llamadas.append(1)
            if len(llamadas) < 3:
                raise OSError("red")
            return {"ciclo": 7}
        with mock.patch.object(K, "http", http):
            self.assertEqual(K.enviar_reporte("u", "t", {}, espera=0)["ciclo"], 7)
        self.assertEqual(len(llamadas), 3)


MERCADOS = [{"mercado": 9017, "horas": 5, "lados": [{"coin": "#90170", "prob_modelo": 60.0}, {"coin": "#90171", "prob_modelo": 40.0}]}]


class CompraDeLaIaSeRevisa(unittest.TestCase):
    """Una propuesta de la IA aprobada más tarde puede tener un precio viejo."""

    def ejecutar(self, ask, mercados=MERCADOS, origen="ia"):
        ex = HL(usdc=100, ask=ask)
        upd, op = O.ejecutar_prediccion(ex, dict(ORD_PRED, origen=origen), False, {"pred_ventaja": 8}, mercados)
        return upd, op, ex

    def test_con_ventaja_compra(self):
        upd, op, ex = self.ejecutar(0.44)  # tope = 0.60 - 0.04 = 0.56
        self.assertEqual(upd["estado"], "ejecutada")

    def test_sin_ventaja_no_compra(self):
        upd, op, ex = self.ejecutar(0.58)
        self.assertIsNone(op)
        self.assertIn("ya no tiene ventaja", upd["error"])
        self.assertFalse([x for x in ex.log if x[0] == "prediccion"])

    def test_mercado_cerrado_o_sin_datos(self):
        self.assertIn("ya no está abierto", self.ejecutar(0.44, [])[0]["error"])
        self.assertIn("no pude leer", self.ejecutar(0.44, None)[0]["error"])
        casi = [dict(MERCADOS[0], horas=0.5)]
        self.assertIn("vence en 0.5 h", self.ejecutar(0.44, casi)[0]["error"])

    def test_la_tuya_no_se_revisa(self):
        upd, op, ex = self.ejecutar(0.44, None, origen="usuario")
        self.assertEqual(upd["estado"], "ejecutada")


class VentaSpotSinPerdida(unittest.TestCase):
    CFG = {"quote": "USDC", "monto_min": 5, "nunca_vender_con_perdida": True}

    class Ex:
        def __init__(self):
            self.ventas = []

        def vender(self, sym, cant, precio_min=None):
            self.ventas.append((sym, cant, precio_min))
            return {"cantidad": cant, "precio": 40.0, "total": cant * 40}

    def vender(self, costos, precio=40.0, origen="ia"):
        ex = self.Ex()
        o = {"id": "v", "simbolo": "HYPE", "accion": "VENDER", "pct": 100, "origen": origen}
        upd, op = O.ejecutar(ex, o, {"HYPE": 1.0}, {"HYPE": precio}, costos, self.CFG, False)
        return upd, ex

    def test_la_ia_no_vende_con_costo_estimado_o_desconocido(self):
        for c in ({"HYPE": {"costo": 35.0, "estimado": True}}, {}):
            upd, ex = self.vender(c)
            self.assertEqual((upd["estado"], ex.ventas), ("error", []))
            self.assertIn("no sé a cuánto compraste", upd["error"])

    def test_por_debajo_de_costo_mas_comision_espera(self):
        upd, ex = self.vender({"HYPE": {"costo": 39.9}})  # +0.25 %: no cubre la comisión
        self.assertEqual((upd["estado"], ex.ventas), ("aprobada", []))
        self.assertIn("sin pérdida desde", upd["nota"])

    def test_con_ganancia_vende_con_precio_minimo(self):
        upd, ex = self.vender({"HYPE": {"costo": 39.0}})
        self.assertEqual(upd["estado"], "ejecutada")
        self.assertAlmostEqual(ex.ventas[0][2], 39.195)

    def test_hyperliquid_vende_ioc_con_limite_y_mira_el_bid(self):
        ex = CcxtExchange("hyperliquid", "USDC", "0x" + "0" * 39 + "1", "0x" + "11" * 32)
        ex.ex.fetch_ticker = lambda par: {"last": 40.0, "bid": 39.9}
        vistos = []
        ex.ex.create_order = lambda *a: vistos.append(a) or {"filled": 1.0, "average": 39.95, "amount": 1.0}
        ex._ajustar = lambda par, c: c
        ex.vender("HYPE", 1.0, 39.5)
        self.assertEqual(vistos[0][1:5], ("limit", "sell", 1.0, 39.5))
        self.assertEqual(vistos[0][5], {"timeInForce": "Ioc"})
        with self.assertRaises(ErrorExchange) as c:
            ex.vender("HYPE", 1.0, 39.95)  # el mejor comprador paga 39.9
        self.assertIn("sin pérdida es desde", str(c.exception))
        ex.ex.create_order = lambda *a: {"filled": 0.0, "amount": 1.0}
        with self.assertRaises(ErrorExchange):
            ex.vender("HYPE", 1.0, 39.5)

    def test_pagar_con_hype_no_lo_vende_con_perdida(self):
        ex = HL(usdc=2.0, hype=1.0)
        ex.entradas = {"HYPE": 45.0}  # HYPE vale 40
        upd, op = O.ejecutar(ex, ORD_PRED, {}, {}, {}, {"pred_pagar_con": "HYPE"}, False)
        self.assertEqual(upd["estado"], "error")
        self.assertIn("venderla sería con pérdida", upd["error"])
        self.assertEqual(ex.log, [])


class EstrategiaSinPerdida(unittest.TestCase):
    CFG = {**E.CONFIG_DEFECTO, "quote": "USDC", "objetivo": {"HYPE": 10}, "ganancia_min": 0}
    IND = {"HYPE": {"rsi": 80}}

    def test_costo_estimado_no_vende_solo(self):
        p, notas = E.proponer(self.CFG, {"USDC": 10, "HYPE": 2}, {"HYPE": 40.0}, self.IND, {"HYPE": {"costo": 30.0, "estimado": True}})
        self.assertFalse([x for x in p if x["accion"] == "VENDER"])
        self.assertTrue(any("costo estimado" in n for n in notas))

    def test_ganancia_minima_cubre_comision(self):
        p, _ = E.proponer(self.CFG, {"USDC": 10, "HYPE": 2}, {"HYPE": 40.0}, self.IND, {"HYPE": {"costo": 39.9}})
        self.assertFalse([x for x in p if x["accion"] == "VENDER"])
        p, _ = E.proponer(self.CFG, {"USDC": 10, "HYPE": 2}, {"HYPE": 40.0}, self.IND, {"HYPE": {"costo": 39.0}})
        self.assertTrue([x for x in p if x["accion"] == "VENDER"])

    def test_costo_real_de_hyperliquid(self):
        c = E.costos_del_exchange({"HYPE": {"costo": 30.0, "estimado": True}}, {"HYPE": 41.5, "+90171": 0.7})
        self.assertEqual(c, {"HYPE": {"costo": 41.5, "estimado": False, "fuente": "exchange"}})

    def test_promedio_con_parte_estimada_sigue_estimado(self):
        c = {"HYPE": {"costo": 30.0, "estimado": True}}
        E.registrar_compra(c, "HYPE", 1.0, 1.0, 40.0)
        self.assertTrue(c["HYPE"]["estimado"])


class IaAutoNoCompraSinFin(unittest.TestCase):
    def test_respeta_el_reparto(self):
        cfg = {"quote": "USDC", "ia": "veto", "ordenes_ia": "auto", "ia_conf_min": 65, "monto_min": 5, "monto_max": 25,
               "objetivo": {"BTC": 20}, "banda": 3}
        op = {"BTC": {"accion": "COMPRAR", "confianza": 90}, "SOL": {"accion": "COMPRAR", "confianza": 90}}
        notas = []
        # BTC ya está en 22 % de 20 %: comprar más pasaría su objetivo; SOL no está en tu reparto.
        n = O.de_la_ia(cfg, op, {"USDC": 78, "BTC": 22 / 60000}, {"BTC": 60000.0, "SOL": 100.0}, [], notas)
        self.assertEqual(n, [])
        self.assertEqual(len(notas), 2)
        # En «proponer» sí las propone: tú decides.
        n = O.de_la_ia({**cfg, "ordenes_ia": "proponer"}, op, {"USDC": 78, "BTC": 22 / 60000}, {"BTC": 60000.0, "SOL": 100.0}, [])
        self.assertEqual(len(n), 2)


if __name__ == "__main__":
    unittest.main()
