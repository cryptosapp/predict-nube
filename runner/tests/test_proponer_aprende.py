"""El agente decide con lo aprendido: corrige probabilidades, exige más donde pierde y anota."""
import unittest

import aprendizaje as A
import predicciones as PR
from test_pred_ia import CFG, OPS, mercado, opinar_fijo

T0 = 1_800_000_000_000


def registro(n_si, n_no, campo_p, p, categoria="cripto_hoy", **kw):
    """Decisiones pasadas resueltas: con `campo_p` = p, salieron n_si «Sí» y n_no «No»."""
    ds = [{"mercado": 1000 + i, "lado": 0, campo_p: p, "categoria": categoria, "vence": T0, **kw} for i in range(n_si + n_no)]
    r = A.anotar({}, ds, T0 - 3_600_000)
    r, _ = A.resolver(r, lambda m: 1.0 if m - 1000 < n_si else 0.0, T0 + 3_600_000, max_consultas=1000)
    return r


class Aprende(unittest.TestCase):
    def test_sin_aprendizaje_compra_como_siempre_y_anota_la_decision(self):
        nuevas, _, res = PR.proponer(CFG, [mercado()], [], [], 50, opinar_fijo(OPS))
        self.assertEqual(len(nuevas), 1)
        (d,) = res["decisiones"]
        self.assertEqual((d["mercado"], d["lado"], d["accion"], d["p_ia"], d["p_modelo"], d["categoria"]), (9017, 0, "comprar", 0.85, 0.92, "cripto_hoy"))

    def test_si_la_ia_exagera_su_probabilidad_corregida_no_alcanza(self):
        # La IA dijo ~85 % en 60 casos y salió 25 %: su 85 % de hoy queda en ~40 %.
        reg = registro(15, 45, "p_ia", 0.85)
        nuevas, notas, res = PR.proponer(CFG, [mercado()], [], [], 50, opinar_fijo(OPS), aprender={"reg": reg, "aj": None})
        self.assertEqual(nuevas, [])
        self.assertTrue(any("corregido con sus resultados reales" in n for n in notas), notas)
        self.assertEqual(res["decisiones"][0]["accion"], "pasar")

    def test_modelo_exagerado_le_quita_ventaja(self):
        # El modelo dijo ~92 % en 60 casos y salió 20 %: corregido (~38 %) ya no ve ventaja contra 0.40.
        reg = registro(12, 48, "p_modelo", 0.92)
        nuevas, _, res = PR.proponer(CFG, [mercado()], [], [], 50, opinar_fijo(OPS), aprender={"reg": reg, "aj": None})
        self.assertEqual((nuevas, res["candidatos"]), ([], 0))

    def test_ventaja_extra_donde_viene_perdiendo(self):
        m = mercado(prob=50.0)  # ventaja 10 puntos (CFG pide 8)
        self.assertEqual(len(PR.proponer(CFG, [m], [], [], 50, opinar_fijo(OPS))[0]), 1)
        aj = {"extra": {"*": 0, "cripto_hoy": 5}}
        self.assertEqual(PR.proponer(CFG, [m], [], [], 50, opinar_fijo(OPS), aprender={"reg": None, "aj": aj})[0], [])
        aj_otra = {"extra": {"*": 0, "bolsa": 20}}
        self.assertEqual(len(PR.proponer(CFG, [m], [], [], 50, opinar_fijo(OPS), aprender={"reg": None, "aj": aj_otra})[0]), 1)

    def test_ventanas_que_no_confirman_frenan_la_compra(self):
        m = mercado()  # «Sí» a 0.40, modelo 92 %
        pv = {"1h": 95, "3h": 60, "6h": 38, "12h": 35, "24h": 30, "48h": 33}
        m["lados"][0].update(prob_ventanas=pv, ventanas_a_favor=2)
        nuevas, notas, _ = PR.proponer(CFG, [m], [], [], 50, opinar_fijo(OPS))
        self.assertEqual(nuevas, [])
        self.assertTrue(any("ventanas" in n for n in notas), notas)
        m["lados"][0]["ventanas_a_favor"] = 5
        vistos = []
        nuevas, _, _ = PR.proponer(CFG, [m], [], [], 50, lambda c, p: (vistos.extend(c), (OPS, "m", None))[1])
        self.assertEqual(len(nuevas), 1)
        self.assertEqual(vistos[0]["prob_por_ventana_pct"]["3h"], 60)

    def test_las_senales_llegan_a_la_ia_y_no_los_objetos_internos(self):
        m = mercado()
        m["senales"] = {"tendencia": "alcista", "macd": 0.4, "rsi": 61}
        vistos = []
        def opinar(c, p):
            vistos.extend(c)
            return OPS, "m", None
        PR.proponer(CFG, [m], [], [], 50, opinar)
        self.assertEqual(vistos[0]["senales"]["tendencia"], "alcista")
        self.assertFalse([k for k in vistos[0] if k.startswith("_")])


if __name__ == "__main__":
    unittest.main()
