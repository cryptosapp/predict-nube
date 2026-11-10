"""Predict — un ciclo del agente de predicciones de Hyperliquid (solo predicciones).

Cada ciclo:
1. Lee los mercados con el modelo, las señales y el mercado global (de tu nube).
2. Resuelve con el resultado OFICIAL de Hyperliquid las decisiones que ya vencieron, y
   con eso calibra probabilidades, mide aciertos y endurece donde viene perdiendo.
3. Ejecuta las órdenes que aprobaste.
4. La IA (rotando modelos gratis, con consenso) revisa los candidatos y tus posiciones.
5. Anota cada decisión para medirla cuando venza.

Dinero: en «simulación» opera una cartera en papel con precios reales (sin firma: es
imposible que salga una orden real). En «real», con «propone» deja órdenes para que tú
las apruebes y solo con «ejecuta sola» compra por su cuenta, dentro de tus topes.
"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aprendizaje as A  # noqa: E402
import estrategia as E  # noqa: E402
import feedback as FB  # noqa: E402
import ia  # noqa: E402
import kumo_runner as K  # noqa: E402
import ordenes as O  # noqa: E402
import predicciones as PR  # noqa: E402
from exchanges import crear_exchange  # noqa: E402
from simulacion import SimPredicciones, resultado_oficial  # noqa: E402

IA_SEGUNDOS = 30
RESULTADOS_SEGUNDOS = 8  # tope para preguntar resultados oficiales antes de que decida la IA


def info_publica(timeout=20):
    """POST público a la API de Hyperliquid (sin claves)."""
    def info(cuerpo):
        req = urllib.request.Request("https://api.hyperliquid.xyz/info", data=json.dumps(cuerpo).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "predict/1.0"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    return info


def ciclo(remoto, parcial=None, info=None):
    parcial = parcial if parcial is not None else {}
    prueba = info is not None
    cfg = {**E.CONFIG_DEFECTO, **(remoto.get("config") or {})}
    ia.PREFERIDO = remoto.get("ia_pref") or None
    ia.configurar(remoto.get("ia_proveedores"), remoto.get("ia_consenso"))
    ia.LOG.clear()
    O.BLOQUEOS.clear()
    estado = remoto.get("estado_runner") or {}
    clave, secreto = os.getenv("EXCHANGE_API_KEY", ""), os.getenv("EXCHANGE_SECRET", "")
    errores, notas, ejecutadas, nuevas, ordenes_upd = [], [], [], [], []
    parcial.update(ordenes_upd=ordenes_upd, ejecutadas=ejecutadas, ordenes_nuevas=nuevas)

    real = cfg.get("modo") == "real"
    if real and not (clave and secreto):
        errores.append("Modo REAL sin la API de Hyperliquid: sigo en simulación")
        real = False
    if real:
        ex = crear_exchange("hyperliquid", "USDC", clave, secreto)
        info = ex._info_hl
    else:
        info = info or info_publica()
        ex = SimPredicciones(info, estado.get("papel"), float(cfg.get("pred_saldo_papel") or 100))
        if ex.nuevo:
            notas.append(f"Cartera en papel nueva con {ex.usdc:.0f} USDC virtuales (precios y libro reales, sin dinero)")
    simulado = not real
    # Resultados oficiales con respuesta rápida (6 s) y un tope total: la IA decide primero.
    hasta = time.time() + RESULTADOS_SEGUNDOS
    consultar = resultado_oficial(info if prueba else info_publica(6))

    # Mercados con el modelo, las señales y el global (los arma tu nube; una lectura por ciclo).
    cache = {}

    def mercados():
        if "lista" not in cache:
            try:
                d = K.http("GET", K.url_nube() + "/runner/predicciones", os.getenv("KUMO_RUNNER_TOKEN", ""))
                cache.update(lista=d.get("mercados") or [], glob=d.get("global"))
            except Exception as e:
                cache.update(lista=None, glob=None, error=type(e).__name__)
        return cache["lista"]

    # 1) Aprendizaje: lo importado de Kumo y los resultados oficiales de lo que ya venció.
    apr = estado.get("aprendizaje") or {}
    importadas = None
    if remoto.get("importar"):
        apr, importadas = A.importar(apr, A.desde_kumo(remoto["importar"]))
        notas.append(f"Aprendizaje: sumé {importadas} decisiones importadas de Kumo")
    apr, resueltas = A.resolver(apr, consultar, max_consultas=10, hasta=hasta)
    if resueltas:
        notas.append(f"Aprendizaje: {resueltas} decisión{'es' if resueltas != 1 else ''} resuelta{'s' if resueltas != 1 else ''} con el resultado oficial de Hyperliquid")
    aj = A.ajustes(apr)
    notas += [f"Aprendizaje: {r}" for r in aj["razones"]]

    # 2) Cartera: en papel, cobra lo que ya liquidó Hyperliquid.
    if simulado:
        abiertos = {m["mercado"] for m in mercados() or []}
        if mercados() is not None:
            ejecutadas += ex.liquidar(consultar, abiertos, hasta=hasta + 4)
    saldos = ex.saldos()
    preds = PR.describir(ex.predicciones, ex._info_hl, ex.entradas) if ex.predicciones else []

    giros = {str(k): v for k, v in (estado.get("giros") or {}).items() if v > time.time() * 1000 - 14 * 86_400_000}
    giros_vendidos = []
    # 3) Órdenes que aprobaste (reservadas en la nube para no repetirlas).
    pendientes = [o for o in (remoto.get("ordenes") or []) if o.get("estado") in ("propuesta", "aprobada") and o.get("tipo") == "prediccion"]
    aprobadas = [o for o in pendientes if o.get("estado") == "aprobada"]
    if aprobadas and not cfg["pausado"]:
        tomadas = K.tomar_ordenes([o["id"] for o in aprobadas])
        if tomadas is None:
            errores.append("No pude reservar tus órdenes aprobadas en la nube: no ejecuto ninguna en este ciclo (para no repetirlas)")
            aprobadas = []
        for o in [o for o in aprobadas if o["id"] in (tomadas or set())]:
            upd, op = O.ejecutar_prediccion(ex, o, simulado, cfg, mercados() if o.get("accion") == "COMPRAR" and o.get("origen") == "ia" else None)
            ordenes_upd.append(upd)
            if op:
                ejecutadas.append(op)
                if o.get("giro") is not None and o.get("accion") == "VENDER":
                    giros_vendidos.append((o, op))
            elif upd.get("error"):
                errores.append(f"Orden {o['accion']} {o.get('etiqueta') or o['coin']}: {upd['error']}")
        if ejecutadas:
            saldos = ex.saldos()
            preds = PR.describir(ex.predicciones, ex._info_hl, ex.entradas) if ex.predicciones else []

    # 3b) Venta de giro que aprobaste: cuenta como el giro de ese mercado y, con «girar»,
    #     el otro lado se propone (o se compra, en papel o con «ejecuta sola») solo con lo cobrado.
    for o, op in giros_vendidos:
        giros[str(o["giro"])] = int(time.time() * 1000)
        if cfg.get("pred_giro") != "girar":
            continue
        try:
            otro = PR.otro_lado(mercados() or [], o["coin"], str(o.get("etiqueta") or "").rsplit(" · ", 1)[0] or None)
            if otro and otro.get("coin"):
                o2, op2 = K.giro_otro_lado(ex, ex, cfg, otro, float(op["total"]), o["coin"], preds, notas, o["giro"], o.get("horas"),
                                           mercados(), cfg.get("pred_ia") != "auto")
                nuevas += o2
                ejecutadas += op2
        except Exception as e:  # nunca debe tumbar el ciclo
            notas.append(f"Giro: {type(e).__name__}")
    if giros_vendidos:
        saldos = ex.saldos()
        preds = PR.describir(ex.predicciones, ex._info_hl, ex.entradas) if ex.predicciones else []

    # 4) Protección por giro (si la activaste), igual que en Kumo.
    if preds and not cfg["pausado"] and cfg.get("pred_giro") in ("vender", "girar"):
        try:
            planes, notas_g = PR.giros(cfg, mercados() or [], preds, {int(k) for k in giros}, pendientes)
            notas += notas_g
            if planes:
                # Solo el piloto automático vende solo; si no, te deja la venta propuesta (también en papel).
                proponer = cfg.get("pred_ia") != "auto"
                o_g, ops_g = K.ejecutar_giros(ex, ex, cfg, planes, preds, notas, mercados(), proponer=proponer)
                nuevas += o_g
                ejecutadas += ops_g
                for g in planes:
                    if any(o.get("giro") == g["mercado"] and o.get("estado") in ("ejecutada", "propuesta") for o in o_g):
                        giros[str(g["mercado"])] = int(time.time() * 1000)
                if ops_g:
                    saldos = ex.saldos()
                    preds = PR.describir(ex.predicciones, ex._info_hl, ex.entradas) if ex.predicciones else []
        except Exception as e:  # nunca debe tumbar el ciclo
            notas.append(f"Giro: {type(e).__name__}")

    # 5) La IA decide con lo aprendido.
    resumen = None
    if cfg.get("pred_ia", "proponer") != "off" and cfg.get("ia") != "off":
        lista = mercados()
        if lista is None:
            errores.append(f"Predicciones: no pude leer los mercados de tu nube ({cache.get('error')}): no decidí nada")
        else:
            glob = cache.get("glob")
            lecc = A.lecciones(apr)
            historial = " ".join(x for x in (FB.para_ia(FB.resumir(estado.get("feedback"))) if estado.get("feedback") else None,
                                             ("Lo que aprendiste de tus resultados oficiales: " + " ".join(lecc)) if lecc else None) if x) or None
            nuevas_p, notas_p, resumen = PR.proponer(
                cfg, lista, preds, pendientes, saldos.get("USDC", 0.0),
                lambda c, p: ia.opinar_predicciones(c, p, glob, None, historial),
                libro=ex.mejor_precio_prediccion, glob=glob, excluir={int(k) for k in giros}, aprender={"reg": apr, "aj": aj})
            notas += notas_p
            for o in nuevas_p:
                o["id"] = o.get("id") or f"ag{int(time.time() * 1000)}{len(nuevas)}"
                # Piloto automático: ejecuta solo (en papel o con dinero real). Si no, queda propuesta.
                if cfg.get("pred_ia") == "auto" and not cfg["pausado"]:
                    if not o.get("regla") and not K.solo_corto_ok(cfg, o):
                        o["nota"] = f"Vence en {o.get('horas') or '?'} h: más allá de tu corto plazo, necesita tu aprobación"
                        nuevas.append(o)
                        continue
                    o["estado"] = "aprobada"
                    upd, op = O.ejecutar_prediccion(ex, o, simulado, cfg, lista)
                    o.update({k: v for k, v in upd.items() if k != "id"})
                    if op:
                        ejecutadas.append(op)
                    elif upd.get("error"):
                        errores.append(f"Predicción {o['accion']} {o['etiqueta']}: {upd['error']}")
                nuevas.append(o)
            # Lo comprado de verdad queda como «comprar» con el precio que se pagó.
            pagado = {o["coin"]: (o.get("resultado") or {}).get("precio") for o in nuevas_p if o.get("estado") == "ejecutada"}
            decisiones = []
            for d in resumen.get("decisiones") or []:
                if d["accion"] == "comprar" and d["coin"] not in pagado:
                    d = {**d, "accion": "propuesta"}  # propuesta sin ejecutar: cuenta como opinión, no como compra
                elif d["coin"] in pagado and pagado[d["coin"]]:
                    d = {**d, "precio": pagado[d["coin"]]}
                decisiones.append(d)
            apr = A.anotar(apr, decisiones)
            if ejecutadas:
                saldos = ex.saldos()
                preds = PR.describir(ex.predicciones, ex._info_hl, ex.entradas) if ex.predicciones else []

    precios = {"#" + p["coin"].lstrip("+"): p["precio"] for p in preds if p.get("precio") is not None}
    valor_preds = round(sum(p.get("valor") or 0 for p in preds), 2)
    total = ex.valor(precios) if simulado else round(saldos.get("USDC", 0.0) + valor_preds, 2)
    puntos = A.puntaje(apr)
    return {
        "exchange": "hyperliquid", "modo": "simulacion" if simulado else "real", "quote": "USDC",
        "total": total, "libre": round(saldos.get("USDC", 0.0), 2), "activos": [],
        "predicciones": preds, "valor_predicciones": valor_preds, "pred_ia": resumen,
        "ordenes_upd": ordenes_upd, "ordenes_nuevas": nuevas, "ejecutadas": ejecutadas,
        "notas": notas[:25], "errores": errores[:10], "giros": giros,
        "aprendizaje": apr, "importadas": importadas,
        "aprendizaje_resumen": {**puntos, "ajustes": aj, "lecciones": A.lecciones(apr),
                                "calibracion_ia": A.tabla_calibracion(apr, campo="p_ia"),
                                "calibracion_modelo": A.tabla_calibracion(apr, campo="p_modelo"),
                                "recientes": [{k: d.get(k) for k in ("ts", "etiqueta", "categoria", "accion", "precio", "p_ia", "p_modelo", "y", "senales")}
                                              for d in (apr.get("decisiones") or [])[:30]]},
        "papel": ex.estado() if simulado else None,
        "bloqueos_perdida": O.BLOQUEOS[-10:], "ia_log": ia.LOG[-20:],
        "ia_consenso": {**ia.CONSENSO, "proveedores": [p["nombre"] for p in ia.proveedores()]},
    }


def main():
    url, token = K.url_nube(), os.getenv("KUMO_RUNNER_TOKEN", "")
    if not url or not token:
        print("Predict aún no está instalado (falta kumo.json o KUMO_RUNNER_TOKEN). Nada que hacer.")
        return 0
    remoto = K.http("GET", url + "/runner/config", token)
    inicio = time.time()
    parcial = {}
    ia.LIMITE_TS = inicio + IA_SEGUNDOS
    try:
        reporte = ciclo(remoto, parcial)
        reporte["ok"] = True
    except Exception as e:  # se reporta para que la app lo muestre
        # El registro de GitHub puede ser público: solo el tipo de error, sin detalles
        # (direcciones o montos). El detalle completo va al reporte de tu nube.
        print(f"Error en el ciclo: {type(e).__name__}")
        reporte = {"ok": False, "exchange": "hyperliquid", "modo": "real" if (remoto.get("config") or {}).get("modo") == "real" else "simulacion",
                   "errores": [f"{type(e).__name__}: {str(e)[:300]}"]}
        for k in ("ordenes_upd", "ejecutadas", "ordenes_nuevas"):
            if parcial.get(k):
                reporte[k] = parcial[k]
    ia.LIMITE_TS = None
    reporte["duracion"] = round(time.time() - inicio, 1)
    resp = K.enviar_reporte(url, token, reporte)
    # Sin direcciones ni saldos en el registro público de GitHub: solo el resultado.
    print(f"Ciclo {resp.get('ciclo') if isinstance(resp, dict) else '?'} · ok={reporte.get('ok')} · {reporte.get('duracion')} s")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # registro público: sin detalles
        print(f"El ciclo no pudo hablar con tu nube: {type(e).__name__}")
        sys.exit(1)
