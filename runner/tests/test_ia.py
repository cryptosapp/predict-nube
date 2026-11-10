import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ia  # noqa: E402

LIMPIO = {"IA_URL": "", "IA_MODELOS": "", "IA_CLAVE": "", "GROQ_API_KEY": "", "GROQ_MODEL": ""}
PROPUESTA = [{"simbolo": "BTC", "accion": "COMPRAR", "precio": 1, "rsi": 28}]


def http_error(code):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b"{}"))


class Proveedor(unittest.TestCase):
    def test_sin_ia(self):
        with mock.patch.dict(os.environ, LIMPIO):
            self.assertIsNone(ia.proveedor())
            self.assertEqual(ia.opinar(PROPUESTA, {}), ({}, None, "Sin IA configurada"))

    def test_kilo_sin_clave(self):
        with mock.patch.dict(os.environ, {**LIMPIO, "IA_URL": "https://api.kilo.ai/api/gateway/", "IA_MODELOS": "a:free, b"}):
            self.assertEqual(ia.proveedor(), ("https://api.kilo.ai/api/gateway", "", ["a:free", "b"], "kilo.ai"))

    def test_nube_vieja_con_groq(self):
        with mock.patch.dict(os.environ, {**LIMPIO, "GROQ_API_KEY": "gsk_x"}):
            url, clave, modelos, _ = ia.proveedor()
            self.assertEqual((url, clave, modelos[0]), (ia.GROQ_URL, "gsk_x", ia.GROQ_MODELOS[0]))

    def test_ia_url_manda_sobre_groq(self):
        with mock.patch.dict(os.environ, {**LIMPIO, "GROQ_API_KEY": "gsk_x", "IA_URL": "https://x.ai/v1", "IA_MODELOS": "m"}):
            self.assertEqual(ia.proveedor()[0], "https://x.ai/v1")


class Respuestas(unittest.TestCase):
    def test_extraer_json_envuelto(self):
        self.assertEqual(ia.extraer_json('<think>hmm {no}</think>\n```json\n{"a": 1}\n```'), {"a": 1})

    def test_opinar_y_respaldo(self):
        ok = json.dumps({"opiniones": [{"simbolo": "btc", "accion": "esperar", "confianza": "150", "razon": "r"}]})
        llamadas = []

        def falso(url, clave, modelo, mensajes, formato_json=True):
            llamadas.append((modelo, formato_json))
            if modelo == "caido":
                raise http_error(503)
            if formato_json:
                raise http_error(400)  # no acepta response_format
            return ok

        with mock.patch.dict(os.environ, {**LIMPIO, "IA_URL": "https://x.ai/v1", "IA_MODELOS": "caido,bueno"}), mock.patch.object(ia, "_llamar", falso):
            out, modelo, error = ia.opinar(PROPUESTA, {})
        self.assertEqual((modelo, error), ("bueno", None))
        self.assertEqual(out["BTC"], {"accion": "ESPERAR", "confianza": 100, "razon": "r"})
        self.assertEqual(llamadas, [("caido", True), ("bueno", True), ("bueno", False)])

    def test_clave_rechazada_no_insiste(self):
        def falso(*a, **k):
            raise http_error(401)

        with mock.patch.dict(os.environ, {**LIMPIO, "IA_URL": "https://x.ai/v1", "IA_MODELOS": "a,b", "IA_CLAVE": "k"}), mock.patch.object(ia, "_llamar", falso) as m:
            out, modelo, error = ia.opinar(PROPUESTA, {})
        self.assertEqual((out, modelo, error), ({}, None, "x.ai HTTP 401 con a"))


if __name__ == "__main__":
    unittest.main()
