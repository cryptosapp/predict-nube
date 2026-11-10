#!/usr/bin/env python3
"""Un ciclo de Kumo Bot: lee la config de tu nube, decide, opera y reporta.

Variables de entorno (secretos de tu repositorio de GitHub):
  KUMO_RUNNER_TOKEN, IA_URL, IA_MODELOS, IA_CLAVE (o GROQ_API_KEY en nubes viejas),
  EXCHANGE_ID, EXCHANGE_API_KEY,
  EXCHANGE_SECRET, EXCHANGE_PASSWORD (opcional). La URL de la nube se lee de
  kumo.json (lo escribe el workflow de instalación) o de KUMO_URL.
"""
import json
import os
import sys
import time
import traceback
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))

import estrategia as E  # noqa: E402
import feedback as FB  # noqa: E402
import ia  # noqa: E402
import noticias as N  # noqa: E402
import ordenes as O  # noqa: E402
import predicciones as PR  # noqa: E402
import supervisor as SUP  # noqa: E402
from exchanges import ESTABLES, ErrorExchange, Simulador, crear_exchange, crear_publico  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def url_nube():
    if os.getenv("KUMO_URL"):
        return os.getenv("KUMO_URL").rstrip("/")
    try:
        with open(os.path.join(RAIZ, "kumo.json")) as f:
            return json.load(f)["url"].rstrip("/")
    except (OSError, KeyError, ValueError):
        return ""


def http(metodo, url, token, cuerpo=None):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(url, data=datos, method=metodo, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "kumo-runner/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "{}")


MARCOS_GRAFICA = ("1h", "4h", "1d")
MAX_GRAFICAS = 8


def _sig(x):
    """Redondea a 6 cifras significativas para que las gráficas pesen poco en la nube."""
    return float(f"{x:.6g}")


def graficas(ex, simbolos, cache):
    """Velas de 1h, 4h y 1d de tus activos para las gráficas de la app.
    {SIM: {marco: {"t": [ms…], "c": [cierres…]}}}. Lo que se lee queda en `cache`
    para que los indicadores no vuelvan a pedir las mismas velas."""
    out = {}
    for s in simbolos[:MAX_GRAFICAS]:
        g = {}
        for m in MARCOS_GRAFICA:
            try:
                v = ex.velas_ts(s, m, 100)
            except (ErrorExchange, AttributeError):
                continue
            cache[(s, m)] = [c for _, c in v]
            if len(v) >= 2:
                g[m] = {"t": [t for t, _ in v], "c": [_sig(c) for _, c in v]}
        if g:
            out[s] = g
    return out


def indicadores(ex, simbolos, marco, cache=None):
    out = {}
    for s in simbolos:
        try:
            cierres = (cache or {}).get((s, marco)) or ex.velas(s, marco, 100)
        except ErrorExchange:
            continue
        if len(cierres) > 20:
            out[s] = {"rsi": E.rsi(cierres), "tendencia": E.tendencia(cierres), "cambio_marco": round((cierres[-1] / cierres[-25] - 1) * 100, 2) if len(cierres) > 25 else None}
    return out


def tomar_ordenes(ids):
    """Reserva en la nube las órdenes aprobadas antes de enviarlas («ejecutando»): si el
    ciclo se cae después de operar, no se repiten. Devuelve los ids tomados, o None si
    la nube no respondió (entonces no se ejecuta ninguna)."""
    if not ids:
        return set()
    url = url_nube()
    if not url:  # sin nube instalada (pruebas locales)
        return set(ids)
    try:
        return set(http("POST", url + "/runner/tomar", os.getenv("KUMO_RUNNER_TOKEN", ""), {"ids": list(ids)}).get("ids") or [])
    except Exception:
        return None


def ciclo(remoto, parcial=None):
    """`parcial` (dict) recibe lo ya operado a medida que avanza: si el ciclo falla a
    mitad, el reporte de error igual lleva las órdenes ejecutadas."""
    parcial = parcial if parcial is not None else {}
    cfg = {**E.CONFIG_DEFECTO, **(remoto.get("config") or {})}
    ia.PREFERIDO = remoto.get("ia_pref") or None
    # Proveedores de IA (con sus claves, ya descifradas) y consenso: los manda la nube.
    ia.configurar(remoto.get("ia_proveedores"), remoto.get("ia_consenso"))
    ia.LOG.clear()
    O.BLOQUEOS.clear()
    estado = remoto.get("estado_runner") or {}
    quote = cfg["quote"].upper()
    exchange_id = os.getenv("EXCHANGE_ID", "kraken").strip().lower() or "kraken"
    clave, secreto, pase = os.getenv("EXCHANGE_API_KEY", ""), os.getenv("EXCHANGE_SECRET", ""), os.getenv("EXCHANGE_PASSWORD", "")
    errores, notas = [], []

    if clave:
        base = crear_exchange(exchange_id, quote, clave, secreto, pase)
    else:
        base = crear_publico(exchange_id, quote)

    simulado = cfg["modo"] != "real"
    if not simulado and not clave:
        errores.append("Modo REAL sin claves del exchange: sigo en simulación")
        simulado = True

    if simulado:
        cartera = {k: float(v) for k, v in (estado.get("cartera_sim") or {}).items()}
        if not cartera:
            cartera = {quote: float(cfg["saldo_simulado"])}
            notas.append(f"Cartera simulada nueva con {cfg['saldo_simulado']:.0f} {quote} virtuales")
        ex = Simulador(base, cartera)
    else:
        ex = base

    saldos = ex.saldos()
    real = saldo_real(base, exchange_id, quote, saldos if not simulado else None, notas) if clave else None
    tenidos = [s for s in saldos if s != quote and s not in ESTABLES]
    pendientes = [o for o in (remoto.get("ordenes") or []) if o.get("estado") in ("propuesta", "aprobada")]
    simbolos = sorted(set(cfg["objetivo"]) | set(tenidos) | {o["simbolo"] for o in pendientes if o.get("tipo") != "prediccion"})
    mercado = ex.precios(simbolos)
    precios = {s: d["precio"] for s, d in mercado.items()}
    sin_par = [s for s in tenidos if s not in precios]
    if sin_par:
        notas.append("Sin par " + quote + " para: " + ", ".join(sin_par[:8]))
    # Gráficas primero: tus activos y luego el reparto objetivo y las órdenes pendientes.
    cache = {}
    orden_g = [s for s in tenidos if s in precios] + [s for s in simbolos if s in precios and s not in tenidos]
    try:
        graf = graficas(ex, orden_g, cache)
    except Exception as e:  # las gráficas nunca deben tumbar el ciclo
        graf = {}
        notas.append(f"Gráficas: {type(e).__name__}")
    ind = indicadores(ex, [s for s in simbolos if s in precios], cfg["marco"], cache)

    costos = E.actualizar_costos(estado.get("costos") or {}, saldos, precios)
    if not simulado:
        costos = E.costos_del_exchange(costos, getattr(base, "entradas", None))

    # Mercados de predicción con el modelo (de la nube), una sola lectura por ciclo.
    cache_pred = {}

    def mercados_pred():
        if "lista" not in cache_pred:
            try:
                d = http("GET", url_nube() + "/runner/predicciones", os.getenv("KUMO_RUNNER_TOKEN", ""))
                cache_pred.update(lista=d.get("mercados") or [], glob=d.get("global"))
            except Exception as e:
                cache_pred.update(lista=None, glob=None, error=type(e).__name__)
        return cache_pred["lista"]

    giros_hechos = {str(k): v for k, v in ((estado.get("giros") or {}).items()) if v > time.time() * 1000 - 14 * 86_400_000}
    # 1) Órdenes que tú aprobaste (o creaste) y las de la IA ya aprobadas.
    ordenes_upd, ejecutadas, giros_vendidos = [], [], []
    parcial.update(ordenes_upd=ordenes_upd, ejecutadas=ejecutadas)
    # Solo se reservan las que se pueden intentar ya (las que esperan su precio siguen en cola).
    aprobadas_ya = [o for o in pendientes if o.get("estado") == "aprobada" and O.lista_para_intentar(o, precios)]
    if aprobadas_ya and not cfg["pausado"]:
        tomadas = tomar_ordenes([o["id"] for o in aprobadas_ya])
        if tomadas is None:
            errores.append("No pude reservar tus órdenes aprobadas en la nube: no ejecuto ninguna en este ciclo (para no repetirlas). Si sigue, actualiza la nube.")
            aprobadas_ya = []
        else:
            aprobadas_ya = [o for o in aprobadas_ya if o["id"] in tomadas]
    if not cfg["pausado"]:
        for o in aprobadas_ya:
            necesita = o.get("tipo") == "prediccion" and o.get("accion") == "COMPRAR" and o.get("origen") == "ia"
            upd, op = O.ejecutar(ex, o, saldos, precios, costos, cfg, simulado, mercados_pred() if necesita else None)
            if upd.get("_op_pago"):
                ejecutadas.append(upd.pop("_op_pago"))
                saldos = ex.saldos()
            ordenes_upd.append(upd)
            if op:
                ejecutadas.append(op)
                saldos = ex.saldos()
                if o.get("giro") is not None and o.get("accion") == "VENDER":
                    giros_vendidos.append((o, op))
            elif upd.get("error"):
                errores.append(f"Orden {o['accion']} {o['simbolo']}: {upd['error']}")
    propuestas, notas_e = E.proponer(cfg, saldos, precios, ind, costos)
    notas += notas_e

    # Mercado global (de la nube, el mismo que ve la app) y titulares de 24 h para la IA.
    glob, titulares = None, []
    if cfg["ia"] != "off":
        mercados_pred()
        glob = cache_pred.get("glob")
        try:
            titulares = N.titulares()
        except Exception as e:  # las noticias nunca tumban el ciclo
            notas.append(f"Noticias: {type(e).__name__}")
    # Con turbulencia o aversión al riesgo se exige más confianza para comprar (+5).
    if glob and (glob.get("turbulencia") or glob.get("tono") == "bajista"):
        cfg = {**cfg, "ia_conf_min": float(cfg.get("ia_conf_min") or 65) + 5,
               "pred_conf_min": float(cfg.get("pred_conf_min") if cfg.get("pred_conf_min") is not None else cfg.get("ia_conf_min") or 65) + 5}
        notas.append(f"Mercado {'turbulento' if glob.get('turbulencia') else 'con aversión al riesgo'}: la IA necesita +5 de confianza para comprar")
    contexto = {"exchange": exchange_id, "quote": quote, "mercado": {s: {**mercado.get(s, {}), **ind.get(s, {})} for s in simbolos if s in precios}}
    if glob and glob.get("resumen"):
        contexto["mercado_global"] = glob["resumen"]
    if titulares:
        contexto["noticias_24h"] = titulares
    # La IA opina de las propuestas y además da su señal de las monedas del reparto
    # y las que tienes, para que la app muestre en cada ciclo qué está pensando.
    vigilar = [s for s in simbolos if s in precios][:8]
    opiniones, modelo, error_ia = ia.opinar(propuestas, contexto, vigilar) if cfg["ia"] != "off" else ({}, None, None)
    for s, o in opiniones.items():
        if s in precios:
            o["precio"] = precios[s]
    if error_ia:
        errores.append(error_ia)
    aprobadas, bloqueadas = E.aplicar_ia(cfg, propuestas, opiniones)

    # 2) Señales fuertes de la IA → órdenes nuevas (propuestas, o ejecutadas en modo auto).
    nuevas = O.de_la_ia(cfg, opiniones, saldos, precios, pendientes, notas)
    parcial["ordenes_nuevas"] = nuevas
    for i, o in enumerate(nuevas):
        o["id"] = f"ia{int(time.time())}{i}"
        o["estado"] = "propuesta"
        if cfg.get("ordenes_ia") == "auto" and not cfg["pausado"]:
            o["estado"] = "aprobada"
            upd, op = O.ejecutar(ex, o, saldos, precios, costos, cfg, simulado)
            o.update({k: v for k, v in upd.items() if k != "id"})
            if op:
                ejecutadas.append(op)
                saldos = ex.saldos()

    fallidas = {}
    if cfg["pausado"]:
        notas.append("Bot en pausa: no se ejecutan operaciones")
    else:
        for p in aprobadas[: int(cfg["max_ops_ciclo"])]:
            sym = p["simbolo"]
            try:
                previa = saldos.get(sym, 0.0)
                if p["accion"] == "COMPRAR":
                    r = ex.comprar(sym, p["monto"])
                    E.registrar_compra(costos, sym, previa, r["cantidad"], r["precio"])
                else:
                    # Nunca por debajo del costo + comisión (salvo tu stop de pérdida).
                    c = costos.get(sym) or {}
                    minimo = O.minimo_spot(c.get("costo")) if cfg["nunca_vender_con_perdida"] and not p.get("stop") else None
                    r = ex.vender(sym, min(p["cantidad"], previa), minimo)
                costo = (costos.get(sym) or {}).get("costo")
                ejecutadas.append({"ts": int(time.time()), "simbolo": sym, "accion": p["accion"], "cantidad": r["cantidad"], "precio": r["precio"], "total": round(r["total"], 2), "motivo": p["motivo"], "simulada": simulado, "pnl": round((r["precio"] / costo - 1) * 100, 2) if p["accion"] == "VENDER" and costo else None, "ia": p.get("ia")})
                saldos = ex.saldos()
            except ErrorExchange as e:
                errores.append(f"{p['accion']} {sym}: {e}")
                fallidas[(sym, p["accion"])] = str(e)[:160]

    # Mercados de predicción de Hyperliquid de tu cuenta real.
    def describir_preds():
        if not (clave and getattr(base, "predicciones", None)):
            return []
        try:
            return PR.describir(base.predicciones, base._info_hl, getattr(base, "entradas", None))
        except Exception as e:  # nunca deben tumbar el ciclo
            notas.append(f"Predicciones: {type(e).__name__}")
            return []
    preds = describir_preds()

    # 2a) Ventas de giro que aprobaste: cuentan como el giro de ese mercado y, con
    #     «girar», el otro lado se compra (o se te propone) solo con lo cobrado.
    for o, op in giros_vendidos:
        giros_hechos[str(o["giro"])] = int(time.time() * 1000)
        if cfg.get("pred_giro") != "girar":
            continue
        try:
            otro = PR.otro_lado(mercados_pred() or [], o["coin"], str(o.get("etiqueta") or "").rsplit(" · ", 1)[0] or None)
            if otro and otro.get("coin"):
                o2, op2 = giro_otro_lado(base, ex, cfg, otro, float(op["total"]), o["coin"], preds, notas, o["giro"], o.get("horas"),
                                         mercados_pred(), cfg.get("pred_ia") != "auto")
                nuevas += o2
                ejecutadas += op2
                if op2:
                    saldos = ex.saldos()
                    preds = describir_preds()
        except Exception as e:  # nunca debe tumbar el ciclo
            notas.append(f"Giro: {type(e).__name__}")

    # 2b) Protección por giro (si la activaste): regla fija, sin IA, antes que la IA opine.
    #     Solo vende sola si tu IA de predicciones está en «ejecuta sola»; si no, te la propone.
    giro_solo = cfg.get("pred_ia") == "auto"
    if (not simulado and exchange_id == "hyperliquid" and hasattr(base, "_info_hl") and preds and not cfg["pausado"]
            and cfg.get("pred_giro") in ("vender", "girar")):
        try:
            planes, notas_g = PR.giros(cfg, mercados_pred() or [], preds, {int(k) for k in giros_hechos}, pendientes)
            notas += notas_g
            if planes:
                ordenes_g, ops_g = ejecutar_giros(base, ex, cfg, planes, preds, notas, mercados_pred(), proponer=not giro_solo)
                nuevas += ordenes_g
                ejecutadas += ops_g
                for g in planes:
                    # Una vez por mercado: también cuando te lo propuso (si la cancelas, no insiste).
                    if any(o.get("giro") == g["mercado"] and o.get("estado") in ("ejecutada", "propuesta") for o in ordenes_g):
                        giros_hechos[str(g["mercado"])] = int(time.time() * 1000)
                if ops_g:
                    saldos = ex.saldos()
                    preds = describir_preds()
        except Exception as e:  # nunca debe tumbar el ciclo
            notas.append(f"Giro: {type(e).__name__}")

    # 3) Beta: la IA revisa los mercados de predicción y tus posiciones en cada ciclo
    #    y propone órdenes (o las ejecuta si activaste «IA ejecuta sola»).
    pred_ia, revision = None, None
    # Feedback en movimiento: resultados de las decisiones anteriores (de ciclos pasados).
    seguir_fb = not simulado and exchange_id == "hyperliquid" and hasattr(base, "_info_hl")
    fb_previo = estado.get("feedback") if seguir_fb else None
    fb_antes = FB.resumir(fb_previo) if seguir_fb and cfg.get("pred_feedback", True) is not False else None
    pred_activo = not simulado and exchange_id == "hyperliquid" and cfg["ia"] != "off" and cfg.get("pred_ia", "proponer") != "off" and hasattr(base, "_info_hl")
    if pred_activo:
        libre_p = saldos.get("USDC", saldos.get(quote, 0.0))
        pagar = str(cfg.get("pred_pagar_con") or "").upper()
        if pagar and pagar != "USDC" and saldos.get(pagar):
            try:
                libre_p += saldos[pagar] * ((ex.precios([pagar]).get(pagar) or {}).get("precio") or 0) * 0.97
            except ErrorExchange:
                pass
        nuevas_p, pred_ia = predicciones_ia(base, ex, cfg, preds, pendientes, libre_p, notas, mercados_pred, cache_pred, titulares, fb_antes,
                                            {int(k) for k in giros_hechos})
        for o in nuevas_p:
            if cfg.get("pred_ia") == "auto" and not cfg["pausado"]:
                # «Solo corto plazo»: lo que vence más lejos queda como propuesta para que tú decidas.
                if not o.get("regla") and not solo_corto_ok(cfg, o):
                    o["nota"] = f"Vence en {o.get('horas') or '?'} h: más allá de tu corto plazo ({cfg.get('pred_corto_horas') or 36} h), necesita tu aprobación"
                    continue
                o["estado"] = "aprobada"
                upd, op = O.ejecutar_prediccion(ex, o, simulado, cfg, mercados_pred())
                if upd.get("_op_pago"):
                    ejecutadas.append(upd.pop("_op_pago"))
                    saldos = ex.saldos()
                o.update({k: v for k, v in upd.items() if k != "id"})
                if op:
                    ejecutadas.append(op)
                    saldos = ex.saldos()
                    preds = describir_preds()
                elif upd.get("error"):
                    errores.append(f"IA predicción {o['accion']} {o['etiqueta']}: {upd['error']}")
        nuevas += nuevas_p
        # Supervisor IA: cada 6 h revisa cómo van y puede ajustar los parámetros (con límites).
        if SUP.toca(cfg, (remoto.get("estado_runner") or {}).get("super_ts")):
            try:
                revision = SUP.revisar(cfg, remoto.get("ordenes_pred") or [], preds, pred_ia, ia.varias_json, FB.para_ia(fb_antes))
            except Exception as e:  # nunca debe tumbar el ciclo
                notas.append(f"Supervisor IA: {type(e).__name__}")

    fb_registro, fb_resumen = None, None
    if seguir_fb:
        try:
            lista_fb = cache_pred.get("lista") or []
            ahora_ms = int(time.time() * 1000)
            # Decisiones de este ciclo primero (aún no están en la nube) y luego las anteriores.
            de_hoy = [{**o, "ts_mod": ahora_ms} for o in nuevas if o.get("tipo") == "prediccion"]
            de_hoy += [{**o, **u, "ts_mod": ahora_ms} for o in pendientes for u in ordenes_upd if u.get("id") == o.get("id")]
            fb_registro = FB.actualizar(fb_previo, preds, de_hoy + (remoto.get("ordenes_pred") or []), ejecutadas,
                                        {m["mercado"]: PR.categoria(m) for m in lista_fb}, lambda sub, ms: precio_al_vencer(base, sub, ms), ahora_ms)
            fb_resumen = {**FB.resumir(fb_registro, ahora_ms), "activo": cfg.get("pred_feedback", True) is not False,
                          "recientes": [{k: c.get(k) for k in ("ts", "tipo", "etiqueta", "categoria", "como", "resultado", "pnl", "confianza", "origen")} for c in fb_registro["cerradas"][:12]]}
        except Exception as e:  # nunca debe tumbar el ciclo
            notas.append(f"Feedback: {type(e).__name__}")

    precios_fin = dict(precios)
    total, activos = E.valorar(saldos, precios_fin, quote)
    valor_preds = round(sum(p["valor"] or 0 for p in preds), 2)
    if not simulado and valor_preds:
        total = round(total + valor_preds, 2)
        for a in activos:
            a["peso"] = round(100 * a["valor"] / total, 2) if total else 0
    for a in activos:
        c = (costos.get(a["simbolo"]) or {}).get("costo")
        a["costo"] = c
        a["pnl"] = round((a["precio"] / c - 1) * 100, 2) if c else None
        a["objetivo"] = cfg["objetivo"].get(a["simbolo"], 0)
        a.update({k: v for k, v in (ind.get(a["simbolo"]) or {}).items()})

    radar = []
    try:
        top = ex.mercados_top(20)
        pedidos = [s.upper() for s in (cfg.get("radar_pedidos") or [])][:5]
        ind_r = indicadores(ex, [f["simbolo"] for f in top[:10]] + pedidos, "1d")
        vistos = set()
        for f in top + [{"simbolo": s, **(ex.precios([s]).get(s) or {})} for s in pedidos]:
            if f["simbolo"] in vistos or not f.get("precio"):
                continue
            vistos.add(f["simbolo"])
            radar.append({**f, **ind_r.get(f["simbolo"], {}), "en_cartera": f["simbolo"] in saldos, "pedido": f["simbolo"] in pedidos})
    except ErrorExchange as e:
        errores.append(f"Radar: {e}")

    return {
        "exchange": exchange_id, "modo": "simulacion" if simulado else "real", "quote": quote,
        "total": total, "libre": round(saldos.get(quote, 0.0), 2), "activos": activos,
        "costos": costos, "cartera_sim": ex.cartera if simulado else None,
        "propuestas": propuestas, "bloqueadas": bloqueadas, "ejecutadas": ejecutadas,
        "notas": notas[:20], "errores": errores[:10], "ia": {"modelo": modelo, "modo": cfg["ia"], "opiniones": opiniones},
        "radar": radar, "real": real,
        "ordenes_upd": ordenes_upd, "ordenes_nuevas": nuevas, "ia_log": ia.LOG[-20:],
        "ordenes": ordenes_ia(propuestas, bloqueadas, ejecutadas, fallidas, cfg["pausado"], opiniones),
        "graficas": graf,
        "predicciones": preds, "valor_predicciones": valor_preds, "pred_ia": pred_ia,
        "supervisor": revision,
        # Registro de decisiones y resultados (la nube lo guarda y lo devuelve en el próximo ciclo).
        "feedback": fb_registro, "feedback_resumen": fb_resumen, "giros": giros_hechos,
        # Ventas que el candado frenó (P/L < +0.5 % o costo desconocido): se ven en la bitácora.
        "bloqueos_perdida": O.BLOQUEOS[-10:],
        "ia_consenso": {**ia.CONSENSO, "proveedores": [p["nombre"] for p in ia.proveedores()]},
    }


def solo_corto_ok(cfg, o):
    """¿Puede la IA ejecutar sola esta orden? Con «solo corto plazo» (por defecto) solo
    si el mercado vence dentro de `pred_corto_horas`."""
    if cfg.get("pred_auto_solo_corto", True) is False:
        return True
    h = o.get("horas")
    return h is not None and h <= float(cfg.get("pred_corto_horas") or 36)


def precio_al_vencer(base, subyacente, ms):
    """Cierre de la vela de 1 minuto que termina en el vencimiento (Hyperliquid), o None."""
    velas = base._info_hl({"type": "candleSnapshot", "req": {"coin": subyacente, "interval": "1m", "startTime": ms - 180_000, "endTime": ms + 60_000}}) or []
    antes = [v for v in velas if int(v.get("t") or 0) < ms]
    return float(antes[-1]["c"]) if antes else None


GIRO_CADUCA_H = 2  # una venta de giro propuesta caduca pronto: el precio cambia


def ejecutar_giros(base, ex, cfg, planes, preds, notas, mercados=None, proponer=False):
    """Vende tu lado al mejor comprador (aunque sea con pérdida: es lo que activaste) y,
    con «girar», compra el otro lado con lo cobrado si el modelo le ve ventaja y cabe
    en tu tope. Con `proponer` (tu IA no está en «ejecuta sola») no mueve nada: deja la
    venta propuesta para que tú la apruebes. Devuelve (órdenes, operaciones)."""
    ordenes, ops = [], []
    for i, g in enumerate(planes):
        ts = int(time.time())
        mejor = base.mejor_precio_prediccion(int(g["coin"][1:]), False)
        if not mejor or not mejor.get("precio"):
            notas.append(f"Giro: quería vender «{g['etiqueta']}» pero no hay compradores ahora")
            continue
        if mejor["precio"] < 0.01:
            notas.append(f"Giro: «{g['etiqueta']}» ya casi no vale nada (pagan {mejor['precio']:g}): vender no recupera nada, no la toco")
            continue
        venta = {"id": f"giro{ts}{i}v", "tipo": "prediccion", "coin": g["coin"], "accion": "VENDER", "unidades": g["unidades"],
                 "limite": float(f"{mejor['precio']:.5g}"), "etiqueta": g["etiqueta"], "razon": g["razon"], "confianza": 100,
                 "origen": "ia", "regla": True, "giro": g["mercado"], "permitir_perdida": True, "horas": g.get("horas")}
        if proponer:
            venta.update(estado="propuesta", caduca=int(time.time() * 1000) + GIRO_CADUCA_H * 3_600_000,
                         razon=g["razon"].replace("Vendo para", "Te propongo vender para") +
                         (" Si la apruebas y se vende, te propondré comprar el otro lado con lo cobrado." if g.get("otro") else ""),
                         nota=f"🔄 Giro: necesita tu aprobación (vende con pérdida). Caduca en {GIRO_CADUCA_H} h.")
            ordenes.append(venta)
            notas.append(f"🔄 Giro: te propuse vender «{g['etiqueta']}» a {venta['limite']:g} (pagaste {g['costo']:g}). Apruébala o cancélala en Órdenes de predicción.")
            continue
        venta["estado"] = "aprobada"
        upd, op = O.ejecutar_prediccion(ex, venta, False, cfg, mercados)
        venta.update({k: v for k, v in upd.items() if k != "id"})
        ordenes.append(venta)
        if not op:
            notas.append(f"Giro: no pude vender «{g['etiqueta']}»: {upd.get('error')}")
            continue
        op["pnl"] = round((op["precio"] / g["costo"] - 1) * 100, 2) if g.get("costo") else None
        ops.append(op)
        notas.append(f"🔄 Giro: vendí {op['cantidad']:g} de «{g['etiqueta']}» a {op['precio']:g} (pagaste {g['costo']:g}): recuperé {op['total']:.2f} USDC")
        if g.get("otro") and g["otro"].get("coin"):
            o2, op2 = giro_otro_lado(base, ex, cfg, g["otro"], float(op["total"]), g["coin"], preds, notas, g["mercado"], g.get("horas"), mercados, False, f"giro{ts}{i}c")
            ordenes += o2
            ops += op2
    return ordenes, ops


def giro_otro_lado(base, ex, cfg, otro, cobrado, vendido, preds, notas, mid, horas, mercados, proponer, oid=None):
    """Después de vender por giro: compra (o propone comprar) el otro lado solo con lo
    cobrado, si el modelo le ve ventaja, está bajo tu máximo y cabe en tu tope."""
    tope = float(cfg.get("pred_max_total") or 30)
    pmax = float(cfg.get("pred_prob_max") or 95) / 100
    mejor = base.mejor_precio_prediccion(int(str(otro["coin"]).lstrip("#")), True)
    ask = mejor and mejor.get("precio")
    pm = otro.get("prob_modelo")
    if not ask:
        notas.append(f"Giro: no compré «{otro['etiqueta']}»: no hay vendedores")
        return [], []
    if ask > pmax:
        notas.append(f"Giro: no compré «{otro['etiqueta']}»: cuesta {ask:g}, más que tu máximo de {pmax * 100:g}%")
        return [], []
    if pm is None or pm / 100 < ask + PR.GIRO_VENTAJA_OTRO:
        notas.append(f"Giro: no compré «{otro['etiqueta']}» a {ask:g}: el modelo le da {pm if pm is not None else '?'}%, sin ventaja suficiente. Solo vendí.")
        return [], []
    try:
        libre = ex.saldos().get("USDC", 0.0)
    except ErrorExchange:
        libre = cobrado
    expuesto = sum(p.get("valor") or 0 for p in preds if p.get("coin") != "+" + str(vendido).lstrip("#+"))
    u = PR.unidades_para(cobrado, ask, min(cobrado, libre, tope - expuesto))  # solo lo cobrado: el giro no pone dinero nuevo
    if u is None:
        notas.append(f"Giro: no compré «{otro['etiqueta']}»: no alcanza para el mínimo o pasaría tu tope de {tope:g} USDC")
        return [], []
    compra = {"id": oid or f"giro{int(time.time())}c", "tipo": "prediccion", "coin": otro["coin"], "accion": "COMPRAR", "unidades": u, "limite": float(f"{ask:.5g}"),
              "etiqueta": otro["etiqueta"], "razon": f"Giro: {'te propongo comprar' if proponer else 'compro'} el otro lado con lo cobrado ({cobrado:.2f} USDC); el modelo le da {pm:g}% y cuesta {ask:g}.",
              "confianza": 100, "origen": "ia", "regla": True, "giro": mid, "horas": horas}
    if proponer:
        compra.update(estado="propuesta", caduca=int(time.time() * 1000) + GIRO_CADUCA_H * 3_600_000, nota="🔄 Giro: necesita tu aprobación.")
        notas.append(f"🔄 Giro: te propuse comprar {u} de «{otro['etiqueta']}» a {ask:g} con lo cobrado")
        return [compra], []
    compra["estado"] = "aprobada"
    upd, op2 = O.ejecutar_prediccion(ex, {**compra, "origen": "regla"}, False, cfg, mercados)
    compra.update({k: v for k, v in upd.items() if k != "id"})
    if op2:
        notas.append(f"🔄 Giro: compré {op2['cantidad']:g} de «{otro['etiqueta']}» a {op2['precio']:g}")
        return [compra], [op2]
    notas.append(f"Giro: no pude comprar «{otro['etiqueta']}»: {upd.get('error')}")
    return [compra], []


def predicciones_ia(base, ex, cfg, preds, pendientes, libre, notas, mercados_pred=None, cache=None, titulares=None, fb=None, excluir=None):
    """Pide a la IA sus órdenes de predicción. Los mercados (con el modelo) vienen de
    la nube; cada orden lleva como límite el mejor precio REAL del libro (no el
    precio medio, que casi nunca se llena). Devuelve (órdenes, resumen)."""
    try:
        if mercados_pred is not None:
            lista = mercados_pred()
            if lista is None:
                raise RuntimeError((cache or {}).get("error") or "sin mercados")
            glob = (cache or {}).get("glob")
        else:
            datos = http("GET", url_nube() + "/runner/predicciones", os.getenv("KUMO_RUNNER_TOKEN", ""))
            lista, glob = datos.get("mercados") or [], datos.get("global")
        nuevas, notas_p, resumen = PR.proponer(cfg, lista, preds, pendientes, libre, lambda c, p: ia.opinar_predicciones(c, p, glob, titulares, FB.para_ia(fb)),
                                               base.mejor_precio_prediccion, glob, fb, excluir)
    except Exception as e:  # nunca debe tumbar el ciclo
        notas.append(f"IA predicciones: {type(e).__name__}")
        return [], None
    notas += notas_p
    listas = []
    for i, o in enumerate(nuevas):
        if o["accion"] == "VENDER":
            mejor = base.mejor_precio_prediccion(int(o["coin"][1:]), False)
            if not mejor or not mejor.get("precio"):
                notas.append(f"Predicciones: quería vender «{o['etiqueta']}» pero no hay compradores ahora")
                continue
            if not o.get("min_venta") or mejor["precio"] < o["min_venta"]:
                desde = f" (sin pérdida vendería desde {o['min_venta']:g})" if o.get("min_venta") else ""
                notas.append(f"Predicciones: quería vender «{o['etiqueta']}» pero el mejor comprador solo paga {mejor['precio']:g}{desde}: no vendo con pérdida")
                continue
            o["limite"] = mejor["precio"]
        o.update({"limite": float(f"{o['limite']:.5g}"), "id": f"iap{int(time.time())}{i}", "estado": "propuesta",
                  "caduca": int(time.time() * 1000) + 2 * 3_600_000})  # el precio cambia rápido: caduca en 2 h
        listas.append(o)
    return listas, resumen


def ordenes_ia(propuestas, bloqueadas, ejecutadas, fallidas, pausado, opiniones):
    """Qué pasó con cada orden propuesta y qué dijo la IA de ella."""
    hechas = {(o["simbolo"], o["accion"]): o for o in ejecutadas}
    frenadas = {(b["simbolo"], b["accion"]) for b in bloqueadas}
    out = []
    for p in propuestas:
        k = (p["simbolo"], p["accion"])
        if k in hechas:
            res, total = "ejecutada", hechas[k]["total"]
        elif k in frenadas:
            res, total = "frenada", None
        elif pausado:
            res, total = "pausa", None
        elif k in fallidas:
            res, total = "error", None
        else:
            res, total = "pendiente", None
        o = opiniones.get(p["simbolo"])
        out.append({"simbolo": p["simbolo"], "accion": p["accion"], "monto": p.get("monto"), "motivo": p.get("motivo"),
                    "resultado": res, "total": total, "error": fallidas.get(k), "ia": o})
    return out


def saldo_real(base, exchange_id, quote, saldos=None, notas=None):
    """Lo que de verdad hay en el exchange (solo lectura), también en simulación,
    para que el usuario compruebe que sus claves funcionan antes de pasar a real."""
    notas = notas if notas is not None else []
    try:
        saldos = saldos if saldos is not None else base.saldos()
    except ErrorExchange as e:
        return {"error": str(e)[:200]}
    if getattr(base, "aviso_cuenta", None):
        notas.append(base.aviso_cuenta)
    estables = sum(v for k, v in saldos.items() if k == quote or k in ESTABLES)
    out = {"libre": round(saldos.get(quote, 0.0), 2), "estables": round(estables, 2),
           "saldos": {k: round(v, 8) for k, v in sorted(saldos.items(), key=lambda kv: -kv[1])[:12]}}
    perps = getattr(base, "saldo_perps", lambda: 0.0)()
    if perps > 0:
        out["perps_usdc"] = round(perps, 2)
        if saldos.get(quote, 0.0) < 1:
            notas.append(f"Tienes {perps:.2f} USDC en Perps de Hyperliquid y Kumo opera en Spot: pásalos en Hyperliquid con Transfer → Perps a Spot.")
    if exchange_id == "hyperliquid" and not saldos and perps <= 0:
        notas.append("Hyperliquid no muestra saldo en esa dirección. Usa la dirección de tu cuenta principal (tu billetera), no la de la API wallet, y espera a que llegue el depósito.")
    return out


def enviar_reporte(url, token, reporte, intentos=4, espera=5):
    """El reporte dice qué órdenes ya se ejecutaron: se reintenta si la red falla."""
    for i in range(intentos):
        try:
            return http("POST", url + "/runner/reporte", token, reporte)
        except Exception:
            if i == intentos - 1:
                raise
            time.sleep(espera * (i + 1))


# La IA tiene hasta 30 s por ciclo (contando desde que empieza): así el trabajo entero
# de GitHub dura menos de 1 minuto, que es lo que cobra como mínimo por cada ciclo.
IA_SEGUNDOS = 30


def main():
    url, token = url_nube(), os.getenv("KUMO_RUNNER_TOKEN", "")
    if not url or not token:
        print("Kumo aún no está instalado (falta kumo.json o KUMO_RUNNER_TOKEN). Nada que hacer.")
        return 0
    remoto = http("GET", url + "/runner/config", token)
    inicio = time.time()
    parcial = {}
    ia.LIMITE_TS = inicio + IA_SEGUNDOS
    try:
        reporte = ciclo(remoto, parcial)
        reporte["ok"] = True
    except Exception as e:  # se reporta para que la app lo muestre
        traceback.print_exc()
        # Con qué exchange falló: si el usuario cambia de exchange, la app no
        # debe mostrar el error viejo como si fuera del nuevo.
        cfg = remoto.get("config") or {}
        reporte = {"ok": False, "exchange": os.getenv("EXCHANGE_ID", "kraken").strip().lower() or "kraken",
                   "modo": "real" if cfg.get("modo") == "real" else "simulacion",
                   "errores": [f"{type(e).__name__}: {str(e)[:300]}"]}
        # Lo que alcanzó a operar antes del fallo también se informa (si no, la nube no
        # sabría que esas órdenes ya se ejecutaron).
        for k in ("ordenes_upd", "ejecutadas", "ordenes_nuevas"):
            if parcial.get(k):
                reporte[k] = parcial[k]
    ia.LIMITE_TS = None
    reporte["duracion"] = round(time.time() - inicio, 1)
    resp = enviar_reporte(url, token, reporte)
    # Sin saldos ni claves en el log: solo un resumen.
    print(f"Ciclo {resp.get('ciclo')} · ok={reporte['ok']} · ops={len(reporte.get('ejecutadas') or [])} · errores={len(reporte.get('errores') or [])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
