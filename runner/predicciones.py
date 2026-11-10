"""Mercados de predicción de Hyperliquid (HIP-4, «outcome markets»).

En la cuenta aparecen como saldos «+N», donde N = 10 × id_del_mercado + lado
(0 = primer lado, normalmente «Sí»; 1 = el segundo, normalmente «No»). Valen entre
0 y 1 USDC y al vencer pagan 1 por unidad si aciertas y 0 si no.

La estrategia de monedas nunca los toca: aquí se separan de tus monedas, se les
pone nombre legible y se valoran con el precio medio del libro (allMids usa la
forma «#N»). En la beta, además, cada ciclo la IA revisa los mercados de precio
(cripto de hoy y de mediano plazo, bolsa) y tus posiciones, y propone (o ejecuta,
si lo activas) órdenes: ver `proponer`.
"""
import math
import re
import time

import aprendizaje as A
from datetime import datetime, timezone

import feedback as FB

PATRON = re.compile(r"^\+(\d+)$")
TRADUCE = {"Yes": "Sí", "No": "No", "template:Yes": "Sí", "template:No": "No"}


# Vender nunca con pérdida: el precio mínimo cubre lo que pagaste más la comisión de
# Hyperliquid al vender (~0.15 %) y un poco de redondeo.
COMISION_VENTA = 0.003
MARGEN_MIN = 0.005  # ninguna venta automática con P/L menor a +0.5 %

# Mínimo por orden en predicciones: ~1 USDC (los 10 USDC son de spot). Hyperliquid no
# lo publica; en el libro hay cientos de órdenes de 1.00-1.05 USDC y muchas de menos de 10.
MIN_ORDEN = 1.0


def unidades_para(monto, precio, maximo):
    """Unidades a comprar por ~`monto` USDC sin pasar de `maximo` (lo libre / lo que queda
    del tope). Con poco USDC compra lo que alcance. None si no llega al mínimo de ~1 USDC."""
    u = math.ceil(min(monto, maximo) / precio - 1e-9)
    if u * precio > maximo + 1e-9:
        u = math.floor(maximo / precio + 1e-9)
    if u < 1 or u * precio < MIN_ORDEN - 1e-9:
        return None
    return u

# Quién crea cada mercado («venue» en outcomeMeta). Sin venue = Hyperliquid. Los
# creadores externos ponen en garantía HYPE, crean y resuelven sus mercados, y con
# deployerFeeScale > 0 la comisión sube: 1.0 = el doble (la mitad es para el creador).
CREADORES = {"": "Hyperliquid", "out": "out", "skew": "skew", "txyz": "trade.xyz"}


def creador(o):
    v = str((o or {}).get("venue") or "")
    return CREADORES.get(v, v)


def escala_de(o):
    """deployerFeeScale del mercado (0 si no tiene)."""
    return max(0.0, _num((o or {}).get("deployerFeeScale")) or 0.0)


def precio_minimo_venta(costo, escala=0.0):
    """Precio por unidad desde el que vender ya no pierde dinero (None si no se sabe el costo).
    `escala` = deployerFeeScale del mercado: con 1.0 la comisión es el doble."""
    if not costo or costo <= 0:
        return None
    # Al menos +0.5 % de P/L (regla del candado), o la comisión si es mayor.
    com = max(MARGEN_MIN, COMISION_VENTA * (1 + max(0.0, escala or 0.0)))
    return min(0.999, math.ceil(round(max(costo * (1 + com), costo + 0.001) * 10000, 6)) / 10000)


_META = {"ts": 0, "datos": None}


def escala_comision(info, oid):
    """deployerFeeScale del mercado `oid` (outcomeMeta guardado 5 min). Si no se puede
    leer, 1.0: ante la duda se supone la comisión alta para no vender con pérdida."""
    try:
        if not _META["datos"] or time.time() - _META["ts"] > 300:
            _META.update(datos=info({"type": "outcomeMeta"}) or {}, ts=time.time())
        for o in _META["datos"].get("outcomes") or []:
            if o.get("outcome") == oid:
                return escala_de(o)
    except Exception:
        pass
    return 1.0


def es_prediccion(coin):
    return bool(PATRON.match(str(coin)))


def separar(saldos):
    """({moneda: cant} sin predicciones, {"+N": cant})."""
    normales, preds = {}, {}
    for k, v in saldos.items():
        (preds if es_prediccion(k) else normales)[k] = v
    return normales, preds


def _campos(desc):
    out = {}
    for parte in str(desc or "").split("|"):
        if ":" in parte:
            k, v = parte.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _vence_ms(texto):
    try:
        return int(datetime.strptime(texto, "%Y%m%d-%H%M").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _cifra(n):
    return f"{n:,.0f}".replace(",", " ") if n >= 1000 else f"{n:.6g}"


def _pregunta(o, preguntas):
    """Texto legible del mercado (igual que en la nube)."""
    desc = str(o.get("description") or "")
    c, nombre = _campos(desc), str(o.get("name") or "")
    q = preguntas.get(o.get("outcome"))
    cq = _campos(q.get("description")) if q else {}
    perp = desc.split("|")[0][5:] if desc.startswith("perp:") else None
    sub = lambda x: {"xyz:XYZ100": "Nasdaq 100", "xyz:SP500": "S&P 500", "xyz:CL": "Petróleo WTI", "xyz:GOLD": "Oro", "xyz:SILVER": "Plata"}.get(x, str(x).replace("xyz:", ""))
    if c.get("class") == "priceBinary" and c.get("underlying"):
        tp = _num(c.get("targetPrice"))
        return f"¿{c['underlying']} ≥ {_cifra(tp) if tp is not None else c.get('targetPrice', '?')} al vencer?"
    if nombre == "template:binaryPrice" and perp and _num(c.get("threshold")):
        return f"¿{sub(perp)} ≥ {_cifra(_num(c['threshold']))} al vencer?"
    if nombre == "template:priceTouch" and perp and _num(c.get("target")):
        return f"¿{sub(perp)} toca {_cifra(_num(c['target']))} antes de vencer?"
    if nombre == "template:binaryPriceExternal" and _num(c.get("threshold")):
        return f"¿{c.get('shortName') or c.get('instrument')} ≥ {_cifra(_num(c['threshold']))} al vencer?"
    if cq.get("class") == "priceBucket" and c.get("index") is not None:
        t = [x for x in (_num(v) for v in str(cq.get("priceThresholds") or "").split(",")) if x]
        k = int(c["index"])
        lo, hi = (t[k - 1] if k > 0 else None), (t[k] if k < len(t) else None)
        u = cq.get("underlying")
        return f"¿{u} < {_cifra(hi)} al vencer?" if lo is None else f"¿{u} ≥ {_cifra(lo)} al vencer?" if hi is None else f"¿{u} entre {_cifra(lo)} y {_cifra(hi)} al vencer?"
    if nombre.startswith("template:policyRate"):
        que = "mantiene" if "NoChange" in nombre else "baja" if "Decrease" in nombre else "sube"
        return f"¿La Fed {que} la tasa de interés ({cq.get('decisionLabel') or 'próxima decisión'})?"
    if nombre.startswith("template:companyIpo") and c.get("company"):
        return f"¿{c['company']} sale a bolsa…? ({nombre.replace('template:', '')})"
    if c.get("participant"):
        comp = cq.get("competition")
        return f"¿{c['participant']} gana{(' ' + comp) if comp else ''}?"
    nombre = re.sub(r"^template:", "", nombre).strip()
    return nombre or f"Mercado {o.get('outcome')}"


def describir(preds, info, entradas=None):
    """Lista de predicciones con nombre, lado, precio, valor y lo que pagaste.
    `info(cuerpo)` hace POST a https://api.hyperliquid.xyz/info; `entradas` = {"+N": precio
    promedio de compra} (de Hyperliquid: entryNtl / unidades)."""
    if not preds:
        return []
    try:
        meta = info({"type": "outcomeMeta"}) or {}
        mids = info({"type": "allMids"}) or {}
    except Exception:  # sin red: se muestran sin precio
        meta, mids = {}, {}
    if meta:
        _META.update(datos=meta, ts=time.time())
    mercados = {o.get("outcome"): o for o in meta.get("outcomes") or []}
    preguntas = {}
    for q in meta.get("questions") or []:
        for n in (q.get("namedOutcomes") or []) + [q.get("fallbackOutcome")]:
            preguntas[n] = q
    out = []
    for coin, cant in sorted(preds.items()):
        cod = int(PATRON.match(coin).group(1))
        oid, lado = divmod(cod, 10)
        o = mercados.get(oid) or {}
        lados = [s.get("name") for s in o.get("sideSpecs") or []]
        c = _campos(o.get("description"))
        precio = _num(mids.get(f"#{cod}"))
        costo = (entradas or {}).get(coin)
        escala = escala_de(o) if o else 1.0
        out.append({
            "coin": coin, "mercado": oid, "lado": lado,
            "lado_nombre": TRADUCE.get(lados[lado], lados[lado]) if lado < len(lados) else ("Sí" if lado == 0 else "No"),
            "pregunta": _pregunta(o, preguntas) if o else f"Mercado {oid}",
            "cantidad": cant, "precio": precio,
            "valor": round(cant * precio, 2) if precio is not None else None,
            "pago_si_acierta": round(cant, 2),
            "costo": round(costo, 5) if costo else None, "min_venta": precio_minimo_venta(costo, escala),
            "creador": creador(o) if o else None, "comision_doble": escala > 0 or None,
            "pnl_pct": round((precio / costo - 1) * 100, 2) if costo and precio is not None else None,
            "vence": _vence_ms(c.get("expiry")) or _vence_ms(_campos((preguntas.get(oid) or {}).get("description")).get("resolutionDeadline")),
            "quote": o.get("quoteToken") or "USDC",
            # Para que la IA compare el precio actual con el objetivo (mercados de precio).
            "subyacente": c.get("underlying") if c.get("class") == "priceBinary" else None,
            "objetivo": _num(c.get("targetPrice")) if c.get("class") == "priceBinary" else None,
        })
    return out


# ---------------------------------------------------------------- IA en cada ciclo (beta)
# La lista de mercados con el modelo y la «ventaja» la arma la nube
# (GET /runner/predicciones): así la app y el ciclo ven exactamente lo mismo.

def categoria(m):
    """cripto_hoy | cripto_mediano | bolsa | economia | otro."""
    return f"cripto_{m.get('plazo')}" if m.get("categoria") == "cripto" else m.get("categoria") or "otro"


# Modelos por ventana (volatilidad de 1, 3, 6, 12, 24 y 48 h): para entrar, al menos
# VENTANAS_MIN de ellos tienen que ver el lado más barato de lo que vale. Solo frena.
VENTANAS_MIN = 4


def ventanas_ok(ld):
    pv = ld.get("prob_ventanas") or {}
    if len(pv) < 4 or ld.get("ventanas_a_favor") is None:
        return True  # nube vieja o pocas velas: no se puede comprobar, decide el modelo principal
    return ld["ventanas_a_favor"] >= min(VENTANAS_MIN, len(pv))


# Puntos de ventaja extra que se exigen con turbulencia (la última hora se mueve
# mucho más que lo normal: el modelo es menos fiable).
EXTRA_TURBULENCIA = 4


def proponer(cfg, lista, posiciones, pendientes, libre, opinar, libro=None, glob=None, fb=None, excluir=None, aprender=None):
    """La IA revisa los mejores candidatos y tus posiciones. Devuelve
    (órdenes_nuevas, notas, resumen). Con `libro(cod, comprar)` cada candidato se
    comprueba contra el mejor precio REAL antes de gastar una consulta de IA, y la
    orden sale con ese precio como límite. `glob` es el mercado global de la nube:
    con turbulencia global (o del subyacente) se exige más ventaja.
    `opinar(candidatos, posiciones)` → ({coin: {accion, confianza, prob, razon}}, modelo, error).
    `fb` = resumen del feedback (feedback.resumir): si una categoría viene perdiendo o hay
    racha de pérdidas, pide más confianza y menos compras. Nunca afloja lo que pusiste.
    `excluir` = mercados donde hubo un giro: la IA no vuelve a entrar en ellos.
    `aprender` = {"reg": registro de aprendizaje, "aj": ajustes}: las probabilidades del
    modelo y de la IA se corrigen con lo que pasó de verdad (aprendizaje.calibrar) y se
    exige la ventaja extra donde viene perdiendo. En el resumen vuelven las decisiones
    del ciclo («decisiones») para anotarlas y medirlas al vencer."""
    reg, aj = (aprender or {}).get("reg"), (aprender or {}).get("aj")
    ventaja_min = float(cfg.get("pred_ventaja") or 8)
    monto, tope = float(cfg.get("pred_monto") or 11), float(cfg.get("pred_max_total") or 30)
    conf_min = float(cfg.get("pred_conf_min") if cfg.get("pred_conf_min") is not None else cfg.get("ia_conf_min") or 65)
    max_ciclo = int(cfg.get("pred_max_ciclo") or 1)
    if ((fb or {}).get("ajustes") or {}).get("max_ciclo"):
        max_ciclo = min(max_ciclo, int(fb["ajustes"]["max_ciclo"]))
    pmin, pmax = float(cfg.get("pred_prob_min") or 5) / 100, float(cfg.get("pred_prob_max") or 95) / 100
    horas_min = float(cfg.get("pred_horas_min") if cfg.get("pred_horas_min") is not None else 1)
    cats = set(cfg.get("pred_categorias") or ["cripto_hoy"])
    vender_ia = cfg.get("pred_vender_ia", True) is not False
    ganancia = float(cfg.get("pred_tomar_ganancia") or 0) / 100
    abiertos = {int(str(p["coin"]).lstrip("+")) // 10 for p in posiciones}
    pend = {(str(o.get("coin")), o.get("accion")) for o in pendientes if o.get("tipo") == "prediccion"}
    abiertos |= {int(c.lstrip("#")) // 10 for c, _ in pend if c.lstrip("#").isdigit()}
    abiertos |= set(excluir or ())
    por_mercado = {m["mercado"]: m for m in lista}
    notas, nuevas = [], []

    turb_global = bool((glob or {}).get("turbulencia"))
    exigir = lambda m: (ventaja_min + (EXTRA_TURBULENCIA if turb_global or m.get("regimen") == "turbulento" else 0)
                        + A.extra_para(aj, categoria(m)))
    # Probabilidad del modelo corregida con los resultados reales de este agente.
    pcal = lambda m, ld: round(100 * A.calibrar(ld["prob_modelo"] / 100, reg, categoria(m), "p_modelo"), 1) if reg else ld["prob_modelo"]
    elegibles, frenados, frenados_v = [], 0, 0
    for m in lista:
        if m["mercado"] in abiertos or categoria(m) not in cats or m.get("sin_ofertas") or (m.get("horas") or 0) < horas_min:
            continue
        for ld in m["lados"]:
            if ld.get("precio") is None or ld.get("prob_modelo") is None or not pmin <= ld["precio"] <= pmax:
                continue
            ld = {**ld, "prob_cal": pcal(m, ld)}
            # La ventaja de la nube, movida lo que el aprendizaje corrigió la probabilidad.
            ld["ventaja_cal"] = round((ld.get("ventaja") if ld.get("ventaja") is not None else ld["prob_modelo"] - ld["precio"] * 100)
                                      + ld["prob_cal"] - ld["prob_modelo"], 1)
            if ld["ventaja_cal"] >= ventaja_min:
                if ld["ventaja_cal"] < exigir(m):
                    frenados += 1
                elif not ventanas_ok(ld):
                    frenados_v += 1
                else:
                    elegibles.append((m, ld))
    elegibles.sort(key=lambda x: -x[1]["ventaja_cal"])
    cands, revisados_libro = [], 0
    for m, ld in elegibles:
        if len(cands) >= 3 or revisados_libro >= 8:
            break
        ask = None
        if libro:
            revisados_libro += 1
            mejor = libro(int(ld["coin"][1:]), True)
            ask = mejor and mejor.get("precio")
            # Sin vendedores, o el precio real ya se comió la ventaja: no vale la pena preguntar.
            if not ask or ask > ld["prob_cal"] / 100 - exigir(m) / 200 or ask > ld["precio"] + 0.05:
                continue
        cands.append({"coin": ld["coin"], "pregunta": m["pregunta"], "regla": m.get("regla"), "lado": ld["nombre"], "precio": ask or ld["precio"],
                      "prob_mercado_pct": round(ld["precio"] * 100, 1), "prob_modelo_pct": ld["prob_modelo"], "ventaja_pts": ld["ventaja_cal"],
                      **({"prob_modelo_corregida_pct": ld["prob_cal"]} if ld["prob_cal"] != ld["prob_modelo"] else {}),
                      "senales": m.get("senales"), "prob_por_ventana_pct": ld.get("prob_ventanas"), "_m": m, "_ld": ld,
                      "subyacente": m.get("nombre_sub") or m.get("subyacente"), "precio_actual": m.get("precio_actual"), "objetivo": m.get("objetivo"),
                      "distancia_pct": m.get("distancia_pct"), "horas_para_vencer": m.get("horas"), "cambio_1h_pct": m.get("cambio_1h_pct"),
                      "cambio_24h_pct": m.get("cambio_24h_pct"), "cambio_48h_pct": m.get("cambio_48h_pct"),
                      "mov_tipico_1h_24h_48h_pct": [m["mov"].get("h1"), m["mov"].get("h24"), m["mov"].get("h48")] if m.get("mov") else None,
                      "volatilidad": m.get("regimen"), "categoria": categoria(m)})

    revisar = []
    for p in posiciones:
        if p.get("precio") is None or (p.get("vence") and p["vence"] < time.time() * 1000) or int(p.get("cantidad") or 0) < 1:
            continue
        cod = int(str(p["coin"]).lstrip("+"))
        coin = f"#{cod}"
        minv = p.get("min_venta")
        # Asegurar ganancia: regla fija, sin IA (y nunca por debajo de lo que pagaste).
        if ganancia and p["precio"] >= ganancia and minv and p["precio"] >= minv and (coin, "VENDER") not in pend and len([o for o in nuevas if o["accion"] == "VENDER"]) < 1:
            nuevas.append({"tipo": "prediccion", "coin": coin, "accion": "VENDER", "unidades": int(p["cantidad"]), "limite": p["precio"],
                           "etiqueta": f"{p['pregunta']} · {p['lado_nombre']}", "razon": f"Asegurar ganancia: ya vale {p['precio'] * 100:.0f}% (tu regla: {ganancia * 100:.0f}%)",
                           "confianza": 100, "origen": "ia", "regla": True, "min_venta": minv})
            continue
        if not vender_ia:
            continue
        m = por_mercado.get(cod // 10)
        ld = m["lados"][cod % 10] if m and cod % 10 < len(m["lados"]) else {}
        revisar.append({"coin": coin, "pregunta": p["pregunta"], "regla": m and m.get("regla"), "lado": p["lado_nombre"], "unidades": p["cantidad"], "precio": p["precio"],
                        "prob_mercado_pct": round(p["precio"] * 100, 1), "prob_modelo_pct": ld.get("prob_modelo"),
                        "ventaja_pts": ld.get("ventaja"),
                        "horas_para_vencer": (m and m.get("horas")) or (round((p["vence"] - time.time() * 1000) / 3_600_000, 1) if p.get("vence") else None),
                        "precio_actual": m and m.get("precio_actual"), "objetivo": m and m.get("objetivo"),
                        "cambio_1h_pct": m and m.get("cambio_1h_pct"), "cambio_24h_pct": m and m.get("cambio_24h_pct"), "volatilidad": m and m.get("regimen"),
                        "pagaste": p.get("costo"), "vender_solo_desde": minv,
                        "puede_vender_sin_perdida": bool(minv and p["precio"] >= minv)})

    resumen = {"ts": int(time.time() * 1000), "revisados": len(lista), "elegibles": len(elegibles), "candidatos": len(cands), "posiciones": len(revisar),
               "categorias": sorted(cats), "modelo": None, "opiniones": [], "modo": cfg.get("pred_ia", "proponer"),
               "global": (glob or {}).get("resumen"), "turbulencia": turb_global}
    if frenados:
        notas.append(f"Predicciones: {frenados} lado{'s' if frenados != 1 else ''} con ventaja no alcanza{'n' if frenados != 1 else ''} lo que se exige con turbulencia (+{EXTRA_TURBULENCIA} puntos)")
    if frenados_v:
        notas.append(f"Predicciones: {frenados_v} lado{'s' if frenados_v != 1 else ''} con ventaja en el modelo principal, pero menos de {VENTANAS_MIN} de 6 ventanas (1 h a 48 h) la confirman: no entra")
    if elegibles and not cands and libro:
        notas.append(f"Predicciones: {len(elegibles)} mercados con ventaja en el modelo, pero en el libro real ya no la tienen (o no hay vendedores)")
    resumen["decisiones"] = []
    if not cands and not revisar:
        return nuevas, notas, resumen
    privados = [{k: v for k, v in c.items() if not k.startswith("_")} for c in cands]
    opin, modelo, error = opinar(privados, revisar)
    resumen["modelo"] = modelo
    if error:
        return nuevas, notas + [f"IA predicciones: {error}"], resumen
    for x in cands + revisar:
        o = opin.get(x["coin"])
        if o:
            resumen["opiniones"].append({"coin": x["coin"], "pregunta": x["pregunta"], "lado": x["lado"], **o})

    # Compras: como mucho `pred_max_ciclo`, con monto fijo y sin pasar tu tope total.
    expuesto = sum(p.get("valor") or 0 for p in posiciones) + sum(
        float(o.get("unidades") or 0) * float(o.get("limite") or 0) for o in pendientes if o.get("tipo") == "prediccion" and o.get("accion") == "COMPRAR")
    compras = 0
    for c in cands:
        o = opin.get(c["coin"]) or {}
        if compras >= max_ciclo or o.get("accion") != "COMPRAR" or o.get("confianza", 0) < conf_min:
            continue
        extra = FB.conf_extra(fb, c["categoria"])
        if extra and o["confianza"] < conf_min + extra:
            notas.append(f"Feedback: la IA quería comprar «{c['pregunta']} · {c['lado']}» con confianza {o['confianza']}, pero por los resultados recientes ahora se exige {conf_min + extra:g}")
            continue
        p_ia = A.calibrar(o["prob"] / 100, reg, c["categoria"], "p_ia") if o.get("prob") is not None and reg else (o["prob"] / 100 if o.get("prob") is not None else None)
        if p_ia is not None and p_ia < c["precio"] + 0.03:
            if reg and o.get("prob") is not None and o["prob"] / 100 >= c["precio"] + 0.03:
                notas.append(f"Aprendizaje: la IA daba {o['prob']:g}% a «{c['pregunta']} · {c['lado']}», pero corregido con sus resultados reales queda en {p_ia * 100:.0f}%: sin margen, no compro")
            continue  # la propia IA no ve margen suficiente
        # Con menos USDC libre (o cerca del tope) compra por lo que alcance, desde ~1 USDC.
        maximo = min(tope - expuesto, libre)
        u = unidades_para(monto, c["precio"], maximo) if maximo > 0 else None
        if u is None:
            motivo = f"pasaría tu tope de {tope:g} USDC" if tope - expuesto < libre else f"solo tienes {libre:.2f} USDC libres (mínimo ~{MIN_ORDEN:g} USDC a {c['precio']:g})"
            notas.append(f"Predicciones: la IA quería comprar «{c['pregunta']} · {c['lado']}» pero {motivo}")
            break
        nuevas.append({"tipo": "prediccion", "coin": c["coin"], "accion": "COMPRAR", "unidades": u,
                       "limite": c["precio"], "etiqueta": f"{c['pregunta']} · {c['lado']}", "razon": o.get("razon", ""),
                       "confianza": o["confianza"], "origen": "ia", "horas": c["horas_para_vencer"], "categoria": c["categoria"],
                       "ia": {"accion": "COMPRAR", "confianza": o["confianza"], "razon": o.get("razon", ""), "prob": o.get("prob"), "votos": o.get("votos")}})
        expuesto += u * c["precio"]
        libre -= u * c["precio"]
        compras += 1

    # Comprar más de una posición tuya si la señal es fuerte (cuenta en las compras del ciclo).
    for r in revisar:
        o = opin.get(r["coin"]) or {}
        if compras >= max_ciclo or o.get("accion") != "COMPRAR MÁS" or o.get("confianza", 0) < conf_min or (r["coin"], "COMPRAR") in pend:
            continue
        ask = None
        if libro:
            mejor = libro(int(r["coin"][1:]), True)
            ask = mejor and mejor.get("precio")
            if not ask or ask > r["precio"] + 0.05:
                continue
        precio = ask or r["precio"]
        if not pmin <= precio <= pmax or (o.get("prob") is not None and o["prob"] / 100 < precio + 0.03):
            continue
        # La IA no basta para comprar más: el modelo estadístico tiene que ver ventaja a ese
        # precio. Sin modelo (mercado fuera de la lista) tampoco se compra.
        pm = r.get("prob_modelo_pct")
        if pm is None or pm / 100 < precio + 0.03:
            notas.append(f"Predicciones: la IA quería comprar más de «{r['pregunta']} · {r['lado']}» a {precio:g}, pero "
                         + ("no hay modelo estadístico de ese mercado para comprobarlo" if pm is None else f"el modelo le da {pm:g}%: no hay ventaja"))
            continue
        maximo = min(tope - expuesto, libre)
        u = unidades_para(monto, precio, maximo) if maximo > 0 else None
        if u is None:
            notas.append(f"Predicciones: la IA quería comprar más de «{r['pregunta']} · {r['lado']}» pero {'pasaría tu tope' if tope - expuesto < libre else 'no hay USDC libres suficientes'}")
            break
        nuevas.append({"tipo": "prediccion", "coin": r["coin"], "accion": "COMPRAR", "unidades": u, "limite": precio,
                       "etiqueta": f"{r['pregunta']} · {r['lado']}", "razon": "Comprar más: " + o.get("razon", ""), "confianza": o["confianza"], "origen": "ia", "horas": r["horas_para_vencer"],
                       "ia": {"accion": "COMPRAR MÁS", "confianza": o["confianza"], "razon": o.get("razon", ""), "prob": o.get("prob")}})
        expuesto += u * precio
        libre -= u * precio
        compras += 1

    # Venta: si la IA ve tu lado sobrevalorado (máx. una por ciclo).
    if not any(o["accion"] == "VENDER" for o in nuevas):
        for r in revisar:
            o = opin.get(r["coin"]) or {}
            if o.get("accion") != "VENDER" or o.get("confianza", 0) < conf_min or (r["coin"], "VENDER") in pend:
                continue
            # Nunca con pérdida, por mínima que sea: si no se sabe lo que pagaste, tampoco.
            if not r["vender_solo_desde"]:
                notas.append(f"Predicciones: la IA quería vender «{r['pregunta']} · {r['lado']}», pero no sé a cuánto la compraste: no vendo para no arriesgar una pérdida")
                continue
            if r["precio"] < r["vender_solo_desde"]:
                notas.append(f"Predicciones: la IA quería vender «{r['pregunta']} · {r['lado']}» a {r['precio']:g}, pero la compraste a {r['pagaste']:g}: no vendo con pérdida (mantengo)")
                continue
            nuevas.append({"tipo": "prediccion", "coin": r["coin"], "accion": "VENDER", "unidades": int(r["unidades"]), "limite": max(r["precio"], r["vender_solo_desde"]),
                           "min_venta": r["vender_solo_desde"],
                           "etiqueta": f"{r['pregunta']} · {r['lado']}", "razon": o.get("razon", ""), "confianza": o["confianza"], "origen": "ia", "horas": r["horas_para_vencer"],
                           "ia": {"accion": "VENDER", "confianza": o["confianza"], "razon": o.get("razon", ""), "prob": o.get("prob")}})
            break
    # Lo decidido con cada candidato, para medirlo cuando venza (aprendizaje.anotar).
    compradas = {o["coin"] for o in nuevas if o["accion"] == "COMPRAR"}
    for c in cands:
        o, m, ld = opin.get(c["coin"]) or {}, c["_m"], c["_ld"]
        cod = int(c["coin"].lstrip("#"))
        resumen["decisiones"].append({
            "mercado": cod // 10, "lado": cod % 10, "coin": c["coin"], "etiqueta": f"{c['pregunta']} · {c['lado']}"[:90], "categoria": c["categoria"],
            "horas": c.get("horas_para_vencer"), "vence": m.get("vence"), "precio": c["precio"], "p_mercado": c["precio"],
            "p_modelo": round(ld["prob_modelo"] / 100, 4), "p_ia": round(o["prob"] / 100, 4) if o.get("prob") is not None else None,
            "conf": o.get("confianza"), "accion": "comprar" if c["coin"] in compradas else "pasar", "ia_accion": o.get("accion"),
            "senales": m.get("senales"), "modelos": [v.get("modelo") for v in (o.get("votos") or [])][:5] or ([modelo] if modelo else None)})
    return nuevas, notas, resumen


# ---------------------------------------------------------------- Protección por giro
# Excepción a «nunca vender con pérdida», solo en predicciones y solo si la activas
# (pred_giro): cuando el mercado se da vuelta contra tu lado, vende para no perderlo
# todo al vencer y, con «girar», usa lo cobrado para comprar el otro lado. Es una
# regla fija con números (sin IA), y se confirma con el modelo estadístico para no
# vender por un vaivén pasajero. Una sola vez por mercado: nunca va y vuelve.
GIRO_CAIDA = 0.10        # tu lado tiene que haber caído al menos 10 puntos desde lo que pagaste
GIRO_MODELO_MAX = 45     # y el modelo estadístico también tiene que darle tu % o menos (nunca más de 45 %)
GIRO_VENTAJA_OTRO = 0.03  # el otro lado solo se compra si el modelo le ve al menos 3 puntos sobre su precio


def giros(cfg, lista, posiciones, ya_girados, pendientes, ahora=None):
    """Posiciones cuyo lado se dio vuelta. Devuelve ([plan], notas); cada plan =
    {coin, unidades, etiqueta, mercado, precio, costo, prob_modelo, razon, otro?}.
    `ya_girados` = ids de mercado donde ya hubo un giro (no se repite)."""
    modo = cfg.get("pred_giro") or "off"
    if modo not in ("vender", "girar"):
        return [], []
    umbral = float(cfg.get("pred_giro_umbral") or 40) / 100
    ahora = ahora or time.time() * 1000
    por_mercado = {m["mercado"]: m for m in lista or []}
    vendiendo = {str(o.get("coin")) for o in pendientes or [] if o.get("tipo") == "prediccion" and o.get("accion") == "VENDER"}
    planes, notas = [], []
    for p in posiciones or []:
        if p.get("precio") is None or int(p.get("cantidad") or 0) < 1 or (p.get("vence") and p["vence"] < ahora):
            continue
        cod = int(str(p["coin"]).lstrip("+"))
        mid, lado = divmod(cod, 10)
        etiqueta = f"{p['pregunta']} · {p['lado_nombre']}"
        costo, precio = p.get("costo"), p["precio"]
        if precio > umbral:
            continue
        if mid in ya_girados:
            continue  # ya se giró una vez en este mercado: la posición nueva no se vuelve a girar
        if not costo:
            notas.append(f"Giro: «{etiqueta}» vale {precio:g}, pero no sé a cuánto la compraste: no la toco")
            continue
        if costo - precio < GIRO_CAIDA:
            continue  # la compraste ya barata: no es un giro, es la apuesta que elegiste
        if f"#{cod}" in vendiendo:
            continue
        m = por_mercado.get(mid)
        lados = (m or {}).get("lados") or []
        pm = (lados[lado] if lado < len(lados) else {}).get("prob_modelo")
        if pm is None:
            notas.append(f"Giro: «{etiqueta}» cayó a {precio:g} (pagaste {costo:g}), pero no hay modelo estadístico de ese mercado para confirmarlo: no vendo")
            continue
        if pm > min(GIRO_MODELO_MAX, umbral * 100):
            notas.append(f"Giro: «{etiqueta}» cayó a {precio:g} (pagaste {costo:g}), pero el modelo aún le da {pm:g}% (más que tu límite de {umbral * 100:g}%): puede ser un vaivén, espero")
            continue
        plan = {"coin": f"#{cod}", "unidades": int(p["cantidad"]), "etiqueta": etiqueta, "mercado": mid, "precio": precio, "costo": costo, "prob_modelo": pm,
                "horas": (m or {}).get("horas"),
                "razon": f"Giro: tu lado cayó de {costo:g} a {precio:g} (tu límite: {umbral * 100:g}%) y el modelo le da {pm:g}%. Vendo para no perderlo todo al vencer."}
        if modo == "girar":
            o = otro_lado(lista, plan["coin"], p["pregunta"])
            if o:
                plan["otro"] = o
        planes.append(plan)
    return planes, notas


def otro_lado(lista, coin, pregunta=None):
    """El lado contrario de `coin` («#N») en su mercado: {coin, etiqueta, prob_modelo} o None."""
    mid, lado = divmod(int(str(coin).lstrip("#+")), 10)
    m = next((m for m in lista or [] if m.get("mercado") == mid), None)
    lados = (m or {}).get("lados") or []
    if len(lados) != 2 or lado > 1:
        return None
    o = lados[1 - lado]
    return {"coin": o.get("coin"), "etiqueta": f"{pregunta or m.get('pregunta') or ''} · {o.get('nombre')}".strip(" ·"), "prob_modelo": o.get("prob_modelo")}
