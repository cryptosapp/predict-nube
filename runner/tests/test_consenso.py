"""Varias IAs gratuitas: respaldo en cadena, consenso y candado de pérdidas."""
import io
import json
import time
import unittest
import urllib.error
from unittest import mock

import ia
import ordenes as O

LISTA = [{"id": "groq", "url": "https://api.groq.com/openai/v1", "key": "gsk_x", "modelos": ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]},
         {"id": "gemini", "url": "https://generativelanguage.googleapis.com/v1beta/openai", "key": "AIza_x", "modelos": ["gemini-3.1-flash"]},
         {"id": "kilo", "url": "https://api.kilo.ai/api/gateway", "key": "", "modelos": ["nvidia/nemotron-3-super-120b-a12b:free"]}]
PROP = [{"simbolo": "BTC", "accion": "COMPRAR", "precio": 1, "rsi": 28}]


def err(code):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b"{}"))


def resp(sym, accion, conf, razon="r", **extra):
    return json.dumps({"opiniones": [{"simbolo": sym, "accion": accion, "confianza": conf, "razon": razon, **extra}]})


class Base(unittest.TestCase):
    def setUp(self):
        ia.configurar(LISTA, {"enabled": True, "size": 3})
        ia.LOG.clear()

    def tearDown(self):
        ia.configurar(None, None)

    def con(self, respuestas):
        """respuestas = {modelo: texto | excepción}."""
        llamadas = []

        def falso(url, clave, modelo, mensajes, formato_json=True):
            llamadas.append((modelo, clave))
            r = respuestas.get(modelo, err(503))
            if isinstance(r, Exception):
                raise r
            return r
        return mock.patch.object(ia, "_llamar", falso), llamadas


class Consenso(Base):
    def test_tres_de_tres(self):
        p, _ = self.con({"openai/gpt-oss-120b": resp("BTC", "COMPRAR", 80, "rebote"), "gemini-3.1-flash": resp("BTC", "COMPRAR", 70),
                         "nvidia/nemotron-3-super-120b-a12b:free": resp("BTC", "COMPRAR", 60)})
        with p:
            out, modelo, error = ia.opinar(PROP, {})
        self.assertEqual(modelo, "consenso:gpt-oss-120b+gemini-3.1-flash+nemotron-3-super-120b-a12b")
        self.assertEqual((out["BTC"]["accion"], out["BTC"]["confianza"]), ("COMPRAR", 70))  # 3/3 conserva la media
        self.assertTrue(out["BTC"]["razon"].startswith("Consenso 3/3 (gpt-oss-120b COMPRAR 80, gemini-3.1-flash COMPRAR 70, nemotron-3-super-120b-a12b COMPRAR 60): rebote"))

    def test_dos_de_tres_baja_17_por_ciento(self):
        p, _ = self.con({"openai/gpt-oss-120b": resp("BTC", "ESPERAR", 70, "lateral"), "gemini-3.1-flash": resp("BTC", "COMPRAR", 60),
                         "nvidia/nemotron-3-super-120b-a12b:free": resp("BTC", "ESPERAR", 65)})
        with p:
            out, _, _ = ia.opinar(PROP, {})
        self.assertEqual(out["BTC"]["accion"], "ESPERAR")
        self.assertEqual(out["BTC"]["confianza"], round(67.5 * (0.5 + 0.5 * 2 / 3)))  # 56 (−17 %)
        self.assertIn("Consenso 2/3", out["BTC"]["razon"])
        self.assertIn(": lateral", out["BTC"]["razon"])  # motivo del que más confianza tuvo

    def test_empate_es_esperar(self):
        p, _ = self.con({"openai/gpt-oss-120b": resp("BTC", "COMPRAR", 70), "gemini-3.1-flash": resp("BTC", "VENDER", 70)})
        with p:
            out, modelo, _ = ia.opinar(PROP, {})
        self.assertEqual(out["BTC"]["accion"], "ESPERAR")
        self.assertEqual(modelo, "consenso:gpt-oss-120b+gemini-3.1-flash")

    def test_una_sola_tal_cual(self):
        p, _ = self.con({"gemini-3.1-flash": resp("BTC", "VENDER", 77, "caro")})
        with p:
            out, modelo, _ = ia.opinar(PROP, {})
        self.assertEqual((modelo, out["BTC"]), ("gemini-3.1-flash", {"accion": "VENDER", "confianza": 77, "razon": "caro"}))

    def test_ninguna_dice_por_que(self):
        p, _ = self.con({"openai/gpt-oss-120b": err(429), "gemini-3.1-flash": err(503)})
        with p:
            out, modelo, error = ia.opinar(PROP, {})
        self.assertEqual((out, modelo), ({}, None))
        self.assertIn("groq gpt-oss-120b: sin cuota (429)", error)

    def test_un_modelo_de_cada_proveedor_primero(self):
        p, llamadas = self.con({m: resp("BTC", "ESPERAR", 60) for m in ("openai/gpt-oss-120b", "gemini-3.1-flash", "nvidia/nemotron-3-super-120b-a12b:free")})
        with p:
            ia.opinar(PROP, {})
        self.assertNotIn("qwen/qwen3.8-27b", [m for m, _ in llamadas])

    def test_si_responde_menos_de_la_mitad_completa_con_las_siguientes(self):
        p, llamadas = self.con({"openai/gpt-oss-120b": err(429), "gemini-3.1-flash": err(503),
                                "nvidia/nemotron-3-super-120b-a12b:free": resp("BTC", "COMPRAR", 70), "qwen/qwen3.8-27b": resp("BTC", "COMPRAR", 60)})
        with p:
            out, modelo, _ = ia.opinar(PROP, {})
        self.assertEqual(modelo, "consenso:nemotron-3-super-120b-a12b+qwen3.8-27b")
        self.assertEqual(out["BTC"]["accion"], "COMPRAR")

    def test_prediccion_prob_mediana_y_empate_mantener(self):
        def r(acc, conf, prob):
            return json.dumps({"opiniones": [{"coin": "#90171", "accion": acc, "confianza": conf, "prob": prob, "razon": "x"}]})
        p, _ = self.con({"openai/gpt-oss-120b": r("COMPRAR MÁS", 70, 60), "gemini-3.1-flash": r("COMPRAR MÁS", 80, 70),
                         "nvidia/nemotron-3-super-120b-a12b:free": r("VENDER", 60, 20)})
        with p:
            out, _, _ = ia.opinar_predicciones([], [{"coin": "#90171"}])
        self.assertEqual((out["#90171"]["accion"], out["#90171"]["prob"]), ("COMPRAR MÁS", 65))  # sin el voto de vender
        p, _ = self.con({"openai/gpt-oss-120b": r("VENDER", 70, 30), "gemini-3.1-flash": r("COMPRAR MÁS", 70, 70)})
        with p:
            out, _, _ = ia.opinar_predicciones([], [{"coin": "#90171"}])
        self.assertEqual(out["#90171"]["accion"], "MANTENER")


class Mayoria(Base):
    """beta21: cada modelo es un voto, se completan los votos pedidos y «todas» = hasta 5."""
    G, Q, GE, K = "openai/gpt-oss-120b", "qwen/qwen3.8-27b", "gemini-3.1-flash", "nvidia/nemotron-3-super-120b-a12b:free"

    def test_gana_la_mayoria_aunque_la_minoria_tenga_mas_confianza(self):
        p, _ = self.con({self.G: resp("BTC", "ESPERAR", 95), self.GE: resp("BTC", "COMPRAR", 40, "rebote"), self.K: resp("BTC", "COMPRAR", 45)})
        with p:
            out, _, _ = ia.opinar(PROP, {})
        self.assertEqual(out["BTC"]["accion"], "COMPRAR")
        self.assertTrue(out["BTC"]["razon"].startswith("Consenso 2/3"))

    def test_si_una_falla_entra_la_siguiente_hasta_juntar_los_votos(self):
        p, llamadas = self.con({self.G: resp("BTC", "COMPRAR", 70), self.GE: err(429), self.K: resp("BTC", "COMPRAR", 60), self.Q: resp("BTC", "ESPERAR", 50)})
        with p:
            out, modelo, _ = ia.opinar(PROP, {})
        self.assertEqual([m for m, _ in llamadas][-1], self.Q)
        self.assertEqual(modelo, "consenso:gpt-oss-120b+nemotron-3-super-120b-a12b+qwen3.8-27b")
        self.assertTrue(out["BTC"]["razon"].startswith("Consenso 2/3"))

    def test_todas_usa_todos_los_modelos_activos(self):
        ia.configurar(LISTA, {"enabled": True, "size": 0})
        self.assertEqual(ia.tamano_consenso(), 4)
        p, llamadas = self.con({self.G: resp("BTC", "COMPRAR", 70), self.GE: resp("BTC", "COMPRAR", 60), self.K: resp("BTC", "ESPERAR", 60), self.Q: resp("BTC", "COMPRAR", 50)})
        with p:
            out, _, _ = ia.opinar(PROP, {})
        self.assertEqual(len(llamadas), 4)
        self.assertTrue(out["BTC"]["razon"].startswith("Consenso 3/4"))

    def test_todas_tiene_tope_de_cinco(self):
        ia.configurar([{"id": "groq", "url": "https://api.groq.com/openai/v1", "key": "gsk_x", "modelos": ["m%d" % i for i in range(8)]}], {"enabled": True, "size": 0})
        self.assertEqual(ia.tamano_consenso(), 5)
        p, llamadas = self.con({"m%d" % i: resp("BTC", "COMPRAR", 60) for i in range(8)})
        with p:
            out, _, _ = ia.opinar(PROP, {})
        self.assertEqual(len(llamadas), 5)
        self.assertTrue(out["BTC"]["razon"].startswith("Consenso 5/5"))

    def test_solo_votan_los_modelos_marcados(self):
        """Con modelos marcados, los de reserva no se llaman aunque pidas «todas»."""
        ia.configurar([{"id": "groq", "url": "https://api.groq.com/openai/v1", "key": "gsk_x", "modelos": ["a", "b"], "reserva": ["lento"]},
                       {"id": "kilo", "url": "https://api.kilo.ai/api/gateway", "key": "", "modelos": ["c"], "reserva": ["otro"]}], {"enabled": True, "size": 0})
        self.assertEqual(ia.tamano_consenso(), 3)
        p, llamadas = self.con({"a": resp("BTC", "COMPRAR", 70), "b": resp("BTC", "COMPRAR", 60), "c": resp("BTC", "ESPERAR", 60)})
        with p:
            out, _, _ = ia.opinar(PROP, {})
        self.assertEqual(sorted(m for m, _ in llamadas), ["a", "b", "c"])
        self.assertTrue(out["BTC"]["razon"].startswith("Consenso 2/3"))

    def test_la_reserva_entra_si_un_marcado_falla(self):
        ia.configurar([{"id": "groq", "url": "https://api.groq.com/openai/v1", "key": "gsk_x", "modelos": ["a", "b"], "reserva": ["lento"]},
                       {"id": "kilo", "url": "https://api.kilo.ai/api/gateway", "key": "", "modelos": [], "reserva": ["otro"]}], {"enabled": True, "size": 3})
        self.assertEqual(ia.tamano_consenso(), 2)
        p, llamadas = self.con({"a": resp("BTC", "COMPRAR", 70), "b": err(429), "lento": resp("BTC", "COMPRAR", 60)})
        with p:
            out, _, _ = ia.opinar(PROP, {})
        self.assertEqual([m for m, _ in llamadas], ["a", "b", "lento"])
        self.assertTrue(out["BTC"]["razon"].startswith("Consenso 2/2"))

    def test_un_solo_marcado_responde_solo_y_la_reserva_lo_cubre(self):
        ia.configurar([{"id": "groq", "url": "https://api.groq.com/openai/v1", "key": "gsk_x", "modelos": ["a"], "reserva": ["b"]}], {"enabled": True, "size": 3})
        p, llamadas = self.con({"a": err(429), "b": resp("BTC", "COMPRAR", 60)})
        with p:
            out, modelo, _ = ia.opinar(PROP, {})
        self.assertEqual([m for m, _ in llamadas], ["a", "b"])
        self.assertEqual(modelo, "b")

    def test_pedir_mas_votos_que_modelos_usa_los_que_hay(self):
        ia.configurar(LISTA[:2], {"enabled": True, "size": 5})
        self.assertEqual(ia.tamano_consenso(), 3)


class FormatosRaros(Base):
    def test_diccionario_por_activo_cuenta_como_voto(self):
        glm = json.dumps({"BTC": {"senal": "ESPERAR", "confianza": 70, "razon": "neutral"}})
        p, _ = self.con({"openai/gpt-oss-120b": resp("BTC", "HOLD", 60, "lateral"), "gemini-3.1-flash": glm, "nvidia/nemotron-3-super-120b-a12b:free": resp("BTC", "COMPRAR", 50)})
        with p:
            out, modelo, _ = ia.opinar(PROP, {})
        self.assertEqual(modelo, "consenso:gpt-oss-120b+gemini-3.1-flash+nemotron-3-super-120b-a12b")
        self.assertEqual(out["BTC"]["accion"], "ESPERAR")
        self.assertIn("Consenso 2/3", out["BTC"]["razon"])

    def test_sin_votos_validos_no_cuenta_y_entra_la_siguiente(self):
        p, _ = self.con({"openai/gpt-oss-120b": json.dumps({"hola": "mundo"}), "gemini-3.1-flash": err(429),
                         "nvidia/nemotron-3-super-120b-a12b:free": resp("BTC", "COMPRAR", 70), "qwen/qwen3.8-27b": resp("BTC", "COMPRAR", 60)})
        with p:
            out, modelo, _ = ia.opinar(PROP, {})
        self.assertEqual(modelo, "consenso:nemotron-3-super-120b-a12b+qwen3.8-27b")
        self.assertTrue(any(l["error"] == "formato inválido (no se contó su voto)" for l in ia.LOG))


class Cadena(Base):
    def setUp(self):
        ia.configurar(LISTA, {"enabled": False})

    def test_429_pasa_al_siguiente_y_queda_registrado(self):
        p, llamadas = self.con({"openai/gpt-oss-120b": err(429), "qwen/qwen3.8-27b": "no es json", "gemini-3.1-flash": resp("BTC", "ESPERAR", 60)})
        with p:
            out, modelo, error = ia.opinar(PROP, {})
        self.assertEqual((modelo, error, out["BTC"]["accion"]), ("gemini-3.1-flash", None, "ESPERAR"))
        self.assertEqual([x["error"] for x in ia.ULTIMO_RESPALDO], ["sin cuota (429)", "JSON inválido"])

    def test_clave_rechazada_salta_sus_otros_modelos(self):
        p, llamadas = self.con({"openai/gpt-oss-120b": err(401), "gemini-3.1-flash": resp("BTC", "ESPERAR", 60)})
        with p:
            ia.chat_json([{"role": "user", "content": "x"}])
        self.assertEqual([m for m, _ in llamadas], ["openai/gpt-oss-120b", "gemini-3.1-flash"])

    def test_sin_clave_no_manda_authorization(self):
        vistos = {}

        class R:
            def __enter__(s):
                return s

            def __exit__(s, *a):
                pass

            def read(s):
                return json.dumps({"choices": [{"message": {"content": '{"ok":true}'}}]}).encode()

        def abrir(req, timeout=None):
            vistos[req.full_url] = dict(req.header_items())
            vistos[req.full_url + "#body"] = json.loads(req.data)
            return R()
        with mock.patch.object(ia.urllib.request, "urlopen", abrir):
            ia.preguntar({"url": "https://api.kilo.ai/api/gateway", "key": "", "nombre": "kilo"}, "m:free", [])
            ia.preguntar({"url": ia.GROQ_URL, "key": "gsk_x", "nombre": "groq"}, "openai/gpt-oss-120b", [])
            ia.preguntar({"url": ia.GROQ_URL, "key": "gsk_x", "nombre": "groq"}, "llama-3.3-70b-versatile", [])
        kilo = vistos["https://api.kilo.ai/api/gateway/chat/completions"]
        self.assertNotIn("Authorization", kilo)
        self.assertEqual(vistos["https://api.kilo.ai/api/gateway/chat/completions#body"]["max_tokens"], 4096)
        self.assertEqual(vistos[ia.GROQ_URL + "/chat/completions"]["User-agent"], "curl/8.14.1")

    def test_cortada_por_tokens_lo_dice(self):
        """Un modelo que razona y se queda sin tokens no es «respuesta vacía»: se cortó."""
        def con(cuerpo):
            class R:
                def __enter__(s):
                    return s

                def __exit__(s, *a):
                    pass

                def read(s):
                    return json.dumps(cuerpo).encode()
            return mock.patch.object(ia.urllib.request, "urlopen", lambda req, timeout=None: R())
        kilo = {"url": "https://api.kilo.ai/api/gateway", "key": "", "nombre": "kilo"}
        for contenido in (None, '{"opiniones": [{"simbolo"'):
            with con({"choices": [{"finish_reason": "length", "message": {"content": contenido}}]}):
                self.assertEqual(ia.preguntar(kilo, "m:free", [])[1][1], "se cortó (límite de tokens)")
        # Si el JSON llegó entero antes del corte, sirve.
        with con({"choices": [{"finish_reason": "length", "message": {"content": '{"ok": true} y luego más'}}]}):
            self.assertEqual(ia.preguntar(kilo, "m:free", [])[0], {"ok": True})

    def test_groq_json_schema_salvo_llama(self):
        c = ia._cuerpo(ia.GROQ_URL, "openai/gpt-oss-120b", [], True)
        self.assertEqual((c["response_format"]["type"], c["reasoning_effort"]), ("json_schema", "low"))
        c = ia._cuerpo(ia.GROQ_URL, "llama-3.3-70b-versatile", [], True)
        self.assertEqual(c["response_format"], {"type": "json_object"})
        self.assertNotIn("reasoning_effort", c)
        self.assertEqual(ia._cuerpo("https://api.mistral.ai/v1", "m", [], True)["response_format"], {"type": "json_object"})
        self.assertNotIn("response_format", ia._cuerpo("https://openrouter.ai/api/v1", "m", [], True))
        self.assertEqual(ia._cuerpo("https://generativelanguage.googleapis.com/v1beta/openai", "g", [], True)["max_tokens"], 2048)


class Parser(unittest.TestCase):
    def test_bloques_y_texto_alrededor(self):
        self.assertEqual(ia.extraer_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(ia.extraer_json('<think>uh {x}</think>Aquí va:\n```json\n{"a": 2}\n```\nListo.'), {"a": 2})
        self.assertEqual(ia.extraer_json('Respuesta: {"a": 3} fin'), {"a": 3})


class CandadoDePerdidas(unittest.TestCase):
    """Ninguna venta automática con P/L < +0.5 % o costo desconocido."""
    CFG = {"quote": "USDC", "monto_min": 5, "nunca_vender_con_perdida": True}

    class Ex:
        def __init__(self):
            self.ventas = []

        def vender(self, sym, cant, precio_min=None):
            self.ventas.append(precio_min)
            return {"cantidad": cant, "precio": 10.0, "total": cant * 10}

    def vender(self, costo, precio, **orden):
        O.BLOQUEOS.clear()
        ex = self.Ex()
        o = {"id": "v", "simbolo": "HYPE", "accion": "VENDER", "pct": 100, "origen": "ia", **orden}
        costos = {"HYPE": {"costo": costo}} if costo else {}
        upd, op = O.ejecutar(ex, o, {"HYPE": 1.0}, {"HYPE": precio}, costos, self.CFG, False)
        return upd, ex, list(O.BLOQUEOS)

    def test_menos_6_75_bloquea(self):
        upd, ex, bloq = self.vender(10.0, 9.325)
        self.assertEqual((upd["estado"], ex.ventas), ("aprobada", []))
        self.assertIn("-6.75 %", bloq[0]["motivo"])

    def test_mas_8_7_pasa(self):
        upd, ex, bloq = self.vender(10.0, 10.87)
        self.assertEqual((upd["estado"], bloq), ("ejecutada", []))
        self.assertAlmostEqual(ex.ventas[0], 10.05)  # y nunca por debajo de +0.5 %

    def test_costo_desconocido_bloquea(self):
        upd, ex, bloq = self.vender(None, 10.87)
        self.assertEqual((upd["estado"], ex.ventas), ("error", []))
        self.assertEqual(bloq[0]["motivo"], "costo desconocido")

    def test_orden_manual_pasa(self):
        # Tuya y marcada «vender aunque sea con pérdida»: pasa aunque pierda.
        upd, ex, bloq = self.vender(10.0, 9.325, origen="usuario", permitir_perdida=True)
        self.assertEqual((upd["estado"], bloq, ex.ventas), ("ejecutada", [], [None]))


if __name__ == "__main__":
    unittest.main()


class PrediccionesEnElCiclo(unittest.TestCase):
    """Ciclo completo con consenso activo en la nube: las decisiones de predicciones y el
    supervisor las toman varias IAs (aquí respuestas simuladas por modelo)."""

    def test_predicciones_y_supervisor_votan(self):
        import os
        import re
        import kumo_runner as K
        from tests.test_pred_ia import HLFalso, lista

        votos = {"openai/gpt-oss-120b": ("COMPRAR", 80, 85, 10), "gemini-3.1-flash": ("COMPRAR", 70, 75, 12),
                 "nvidia/nemotron-3-super-120b-a12b:free": ("NO ENTRAR", 60, 40, None)}
        pedidos = []

        def falso(url, clave, modelo, mensajes, formato_json=True):
            texto = mensajes[-1]["content"]
            acc, conf, prob, ventaja = votos[modelo]
            if "supervisor" in mensajes[0]["content"]:
                pedidos.append(("super", modelo))
                return json.dumps({"cambios": {"pred_ventaja": ventaja} if ventaja else {}, "razon": "r"})
            pedidos.append(("pred", modelo))
            coins = sorted(set(re.findall(r'"coin": "(#\d+)"', texto)))
            return json.dumps({"opiniones": [{"coin": c, "accion": acc, "confianza": conf, "prob": prob, "razon": modelo} for c in coins]})

        hl = HLFalso()
        remoto = {"config": {"modo": "real", "quote": "USDC", "objetivo": {}, "ia": "veto", "pred_ia": "proponer", "ordenes_ia": "off",
                             "pred_ventaja": 8, "pred_conf_min": 50},
                  "ordenes": [], "estado_runner": {}, "ia_proveedores": LISTA, "ia_consenso": {"enabled": True, "size": 3}}
        try:
            with mock.patch.dict(os.environ, {"EXCHANGE_ID": "hyperliquid", "EXCHANGE_API_KEY": "0xabc", "EXCHANGE_SECRET": "x"}), \
                    mock.patch.object(K, "crear_exchange", lambda *a: hl), \
                    mock.patch.object(K, "http", lambda *a, **k: {"mercados": lista()}), \
                    mock.patch.object(K, "url_nube", lambda: "https://nube"), \
                    mock.patch.object(K.ia, "opinar", lambda *a: ({}, None, None)), \
                    mock.patch.object(ia, "_llamar", falso):
                rep = K.ciclo(remoto)
        finally:
            ia.configurar(None, None)
        pia = rep["pred_ia"]
        self.assertTrue(pia["modelo"].startswith("consenso:"), pia["modelo"])
        self.assertEqual(len({m for q, m in pedidos if q == "pred"}), 3)
        op = pia["opiniones"][0]
        self.assertEqual(op["accion"], "COMPRAR")
        self.assertIn("Consenso 2/3", op["razon"])
        # Supervisor: 2 de 3 piden subir la ventaja → mediana de 10 y 12 = 11.
        self.assertTrue(rep["supervisor"]["modelo"].startswith("consenso:"))
        self.assertEqual(rep["supervisor"]["cambios"], {"pred_ventaja": 11.0})
        self.assertEqual(len({m for q, m in pedidos if q == "super"}), 3)

    def test_varias_json_sin_consenso_es_la_cadena(self):
        ia.configurar(LISTA, {"enabled": False})
        try:
            with mock.patch.object(ia, "_llamar", lambda *a, **k: json.dumps({"cambios": {}})):
                ok, error = ia.varias_json([{"role": "user", "content": "x"}], lambda d: "cambios" in d)
            self.assertEqual((len(ok), error), (1, None))
            with mock.patch.object(ia, "_llamar", lambda *a, **k: json.dumps({"otra": 1})):
                ok, error = ia.varias_json([{"role": "user", "content": "x"}], lambda d: "cambios" in d)
            self.assertEqual(ok, [])
            self.assertIn("formato inválido", error)
        finally:
            ia.configurar(None, None)
