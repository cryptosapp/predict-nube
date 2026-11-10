"""Aprendizaje: anotar decisiones, resolverlas con el resultado oficial y corregir."""
import unittest

import aprendizaje as A

H = A.HORA
T0 = 1_800_000_000_000


def dec(mercado=100, lado=0, p_ia=0.8, p_modelo=0.7, precio=0.6, accion="pasar", categoria="cripto_hoy", vence=T0 + H, **kw):
    return {"mercado": mercado, "lado": lado, "p_ia": p_ia, "p_modelo": p_modelo, "p_mercado": precio, "precio": precio,
            "accion": accion, "categoria": categoria, "vence": vence, **kw}


class Anotar(unittest.TestCase):
    def test_un_mercado_se_anota_una_sola_vez(self):
        r = A.anotar({}, [dec()], T0)
        r = A.anotar(r, [dec(p_ia=0.9)], T0 + 600_000)
        self.assertEqual(len(r["decisiones"]), 1)
        self.assertEqual(r["decisiones"][0]["p_ia"], 0.8)  # la primera evaluación es la que cuenta

    def test_si_despues_compra_pasa_a_comprar_con_su_precio(self):
        r = A.anotar({}, [dec(precio=0.6)], T0)
        r = A.anotar(r, [dec(accion="comprar", precio=0.55, p_ia=0.85)], T0 + 600_000)
        d = r["decisiones"][0]
        self.assertEqual((d["accion"], d["precio"], d["p_ia"]), ("comprar", 0.55, 0.85))

    def test_ignora_lo_que_no_trae_mercado_o_lado(self):
        self.assertEqual(A.anotar({}, [{"mercado": None, "lado": 0}, {"mercado": 1, "lado": 3}], T0)["decisiones"], [])


class Resolver(unittest.TestCase):
    def test_usa_el_resultado_oficial_y_respeta_el_lado(self):
        r = A.anotar({}, [dec(mercado=1, lado=0), dec(mercado=2, lado=1)], T0)
        r, n = A.resolver(r, lambda m: 1.0 if m == 1 else 1.0, T0 + 2 * H)
        ys = {d["mercado"]: d["y"] for d in r["decisiones"]}
        self.assertEqual((n, ys), (2, {1: 1.0, 2: 0.0}))  # el «No» del mercado 2 perdió

    def test_si_no_esta_liquidado_espera_y_no_lo_da_por_perdido(self):
        r = A.anotar({}, [dec()], T0)
        r, n = A.resolver(r, lambda m: None, T0 + 2 * H)
        self.assertEqual((n, r["decisiones"][0]["y"]), (0, None))
        self.assertFalse(r["decisiones"][0].get("sin_resultado"))
        r, _ = A.resolver(r, lambda m: None, T0 + 4 * A.DIA)
        self.assertTrue(r["decisiones"][0]["sin_resultado"])

    def test_no_pregunta_antes_de_vencer_ni_falla_sin_red(self):
        llamadas = []
        r = A.anotar({}, [dec(vence=T0 + 10 * H)], T0)
        A.resolver(r, lambda m: llamadas.append(m), T0 + H)
        self.assertEqual(llamadas, [])
        def cae(m):
            raise OSError("sin red")
        r2 = A.anotar({}, [dec()], T0)
        r2, n = A.resolver(r2, cae, T0 + 2 * H)
        self.assertEqual(n, 0)

    def test_limita_las_consultas_por_ciclo(self):
        r = A.anotar({}, [dec(mercado=i) for i in range(50)], T0)
        llamadas = []
        A.resolver(r, lambda m: llamadas.append(m) or 1.0, T0 + 2 * H, max_consultas=7)
        self.assertEqual(len(llamadas), 7)


class Tiempo(unittest.TestCase):
    def test_con_el_tope_vencido_no_empieza_consultas(self):
        import time as _t
        r = A.anotar({}, [dec(mercado=i) for i in range(5)], T0)
        llamadas = []
        A.resolver(r, lambda m: llamadas.append(m) or 1.0, T0 + 2 * H, hasta=_t.time() - 1)
        self.assertEqual(llamadas, [])

    def test_primero_los_recientes_y_los_importados_al_final(self):
        ds = [dec(mercado=1, vence=T0 - 5 * H), dec(mercado=2, vence=T0 - H), dec(mercado=3, vence=T0, vence_estimado=True)]
        r = A.anotar({}, ds, T0 - 6 * H)
        orden = []
        A.resolver(r, lambda m: orden.append(m) or 1.0, T0 + 2 * H)
        self.assertEqual(orden, [2, 1, 3])


def resuelto(n_si, n_no, **kw):
    ds = [dec(mercado=i, **kw) for i in range(n_si + n_no)]
    r = A.anotar({}, ds, T0)
    r, _ = A.resolver(r, lambda m: 1.0 if m < n_si else 0.0, T0 + 2 * H, max_consultas=1000)
    return r


class Calibrar(unittest.TestCase):
    def test_con_pocos_casos_casi_no_corrige(self):
        r = resuelto(1, 2, p_ia=0.8)  # dijo 80 %, salió 33 % en 3 casos
        p = A.calibrar(0.8, r)
        self.assertLess(p, 0.8)
        self.assertGreater(p, 0.7)

    def test_con_muchos_casos_se_acerca_a_lo_que_paso(self):
        r = resuelto(30, 30, p_ia=0.8)  # dijo 80 %, salió 50 %
        self.assertAlmostEqual(A.calibrar(0.8, r), 0.8 + 60 / 80 * (0.5 - 0.8), places=3)

    def test_sin_datos_no_toca(self):
        self.assertEqual(A.calibrar(0.42, {}), 0.42)


class PuntajeYAjustes(unittest.TestCase):
    def test_brier_y_ventaja_real(self):
        r = resuelto(6, 6, p_ia=0.8, precio=0.6, accion="comprar")
        p = A.puntaje(r, T0 + 3 * H)
        self.assertEqual((p["compras"], p["ganadas"]), (12, 6))
        self.assertEqual(p["ventaja_real"], -10.0)  # pagó 0.60 por algo que salió 50 %
        self.assertEqual(p["brier_ia"], round((6 * 0.04 + 6 * 0.64) / 12, 4))

    def test_categoria_que_pierde_pide_mas_ventaja_y_nunca_menos(self):
        r = resuelto(6, 6, p_ia=0.8, precio=0.6, accion="comprar")
        aj = A.ajustes(r, T0 + 3 * H)
        self.assertEqual(A.extra_para(aj, "cripto_hoy"), 5)
        self.assertEqual(A.extra_para(aj, "bolsa"), 0)
        bien = resuelto(10, 2, p_ia=0.8, precio=0.6, accion="comprar")
        self.assertEqual(A.extra_para(A.ajustes(bien, T0 + 3 * H), "cripto_hoy"), 0)

    def test_pocas_compras_no_ajustan(self):
        r = resuelto(1, 5, p_ia=0.8, precio=0.6, accion="comprar")
        self.assertEqual(A.ajustes(r, T0 + 3 * H)["extra"], {"*": 0})

    def test_ia_peor_que_el_mercado_endurece_todo(self):
        r = resuelto(15, 15, p_ia=0.9, precio=0.5, p_modelo=0.5)  # el mercado dijo 50 % y acertó; la IA 90 %
        aj = A.ajustes(r, T0 + 3 * H)
        self.assertEqual(aj["extra"]["*"], 5)
        self.assertTrue(any("peor que el precio del mercado" in x for x in aj["razones"]))

    def test_lecciones_solo_con_muestra_y_desvio(self):
        self.assertEqual(A.lecciones(resuelto(2, 2, p_ia=0.8)), [])
        ls = A.lecciones(resuelto(10, 20, p_ia=0.8))
        self.assertTrue(any("dijiste ~80 % salió 33 %" in x and "escéptico" in x for x in ls), ls)


class Kumo(unittest.TestCase):
    def test_importa_ordenes_de_kumo_y_las_resuelve(self):
        ordenes = [{"tipo": "prediccion", "coin": "#31520", "accion": "COMPRAR", "estado": "ejecutada", "limite": 0.48, "resultado": {"precio": 0.4825},
                    "etiqueta": "¿BTC ≥ 82 139? · Sí", "categoria": "cripto_hoy", "confianza": 70, "ia": {"prob": 62}, "ts": T0, "horas": 14},
                   {"tipo": "prediccion", "coin": "#3151", "accion": "VENDER", "estado": "ejecutada"},
                   {"tipo": "spot", "accion": "COMPRAR"}]
        ds = A.desde_kumo(ordenes)
        self.assertEqual(len(ds), 1)
        d = ds[0]
        self.assertEqual((d["mercado"], d["lado"], d["precio"], d["p_ia"], d["accion"], d["origen"]), (3152, 0, 0.4825, 0.62, "comprar", "kumo"))
        self.assertEqual(d["vence"], T0 + 14 * H)
        r, n = A.importar({}, ds, T0 + 20 * H)
        r, n2 = A.importar(r, ds, T0 + 20 * H)
        self.assertEqual((n, n2), (1, 0))  # no se duplica al importar dos veces

    def test_lado_valido(self):
        # #3152 → mercado 315, lado 2: no es un lado binario válido, anotar lo ignora
        self.assertEqual(A.importar({}, [{"mercado": 315, "lado": 2}])[1], 0)


if __name__ == "__main__":
    unittest.main()
