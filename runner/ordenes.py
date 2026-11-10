"""Órdenes explícitas: las que propone la IA y las que crea o aprueba el usuario.

Viven en la nube (KV `ordenes`). Cada ciclo el runner recibe las aprobadas, las
ejecuta si se cumple su precio límite y devuelve qué pasó con cada una. También
convierte las señales fuertes de la IA en órdenes nuevas: «propuesta» (esperan
tu aprobación) o, en modo auto, ejecutadas en el mismo ciclo.
"""
import time

from exchanges import ErrorExchange
from predicciones import escala_comision, precio_minimo_venta

MAX_IA_CICLO = 2  # órdenes nuevas de la IA por ciclo

# Vender monedas spot sin pérdida: precio ≥ costo + este margen (comisión de venta ~0.1-0.4 %
# y diferencia entre el último precio y lo que de verdad paga el comprador).
MARGEN_SPOT = 0.005


def minimo_spot(costo):
    return costo * (1 + MARGEN_SPOT) if costo and costo > 0 else None


# Ventas que el candado frenó en este ciclo (van al reporte como «loss_sell_block»).
BLOQUEOS = []


def _bloqueo(que, motivo):
    BLOQUEOS.append({"ts": int(time.time() * 1000), "que": str(que)[:90], "motivo": str(motivo)[:200]})


def _cantidad_venta(o, saldos):
    pct = max(1.0, min(100.0, float(o.get("pct") or 100)))
    return saldos.get(o["simbolo"], 0.0) * pct / 100


MIN_SPOT_HL = 10.5  # Hyperliquid pide ~10 USDC por orden spot


def _pagar_con(ex, o, cfg, costo):
    """Los mercados de predicción se pagan en USDC. Si falta USDC y elegiste pagar
    con otra moneda (p. ej. HYPE), vende SOLO lo necesario (+2 %, mínimo ~10 USDC).
    Devuelve (operación_de_venta|None, nota|None, error|None)."""
    moneda = str(o.get("pagar_con") or (cfg or {}).get("pred_pagar_con") or "").upper()
    if not moneda or moneda == "USDC":
        return None, None, None
    saldos = ex.saldos()
    usdc = saldos.get("USDC", 0.0)
    if usdc >= costo:
        return None, None, None
    precio = (ex.precios([moneda]).get(moneda) or {}).get("precio")
    if not precio:
        return None, None, f"No hay precio de {moneda} para pagar con él"
    # Tampoco aquí se vende con pérdida: si esa moneda vale menos de lo que pagaste, no se usa.
    entrada = (getattr(ex, "entradas", None) or {}).get(moneda)
    minimo = minimo_spot(entrada)
    if minimo and precio < minimo:
        _bloqueo(f"Vender {moneda} para pagar una predicción", f"vale {precio:.4g} y la compraste a {entrada:.4g} (P/L {(precio / entrada - 1) * 100:+.2f} %)")
        return None, None, (f"No pagué con {moneda}: vale {precio:.4g} y la compraste a {entrada:.4g}; venderla sería con pérdida. "
                            "Pon USDC o espera. No se movió nada.")
    vender_usdc = max((costo - usdc) * 1.02 + 0.05, MIN_SPOT_HL)
    cant = vender_usdc / precio
    if saldos.get(moneda, 0.0) < cant:
        return None, None, (f"Faltan {costo - usdc:.2f} USDC y no tienes suficiente {moneda} para cubrirlos "
                            f"(necesito vender {cant:.4g}, tienes {saldos.get(moneda, 0.0):.4g}). No se movió nada.")
    try:
        # Límite: sin pérdida y como mucho 2 % por debajo del precio actual (no el 5 % de una orden a mercado).
        r = ex.vender(moneda, cant, max(minimo or 0, precio * 0.98))
    except ErrorExchange as e:
        return None, None, f"No pude vender {moneda} para pagar: {str(e)[:150]}. No se movió nada."
    op = {"ts": int(time.time()), "simbolo": moneda, "accion": "VENDER", "cantidad": r["cantidad"], "precio": r["precio"], "total": round(r["total"], 2),
          "motivo": f"Pagar predicción {o.get('etiqueta') or o.get('coin')}", "simulada": False, "pnl": None, "orden": o.get("id")}
    return op, f"Vendí {r['cantidad']:.4g} {moneda} ({r['total']:.2f} USDC) para pagarla", None


def _sigue_con_ventaja(ex, o, cod, cfg, mercados):
    """Compra de la IA: justo antes de enviarla se vuelve a mirar el mercado. Si ya
    venció, falta poco o el precio real ya no deja ventaja frente al modelo, no se
    compra (una propuesta aprobada horas después puede tener un precio viejo).
    Devuelve el motivo para no comprar, o None si sigue valiendo la pena."""
    if mercados is None:
        return "no pude leer los mercados ahora para comprobar que siga teniendo ventaja"
    m = next((x for x in mercados if x.get("mercado") == cod // 10), None)
    lados = (m or {}).get("lados") or []
    ld = lados[cod % 10] if cod % 10 < len(lados) else None
    if not m or not ld:
        return "ese mercado ya no está abierto"
    horas_min = float((cfg or {}).get("pred_horas_min") if (cfg or {}).get("pred_horas_min") is not None else 1)
    if m.get("horas") is not None and m["horas"] < horas_min:
        return f"vence en {m['horas']} h (tu mínimo es {horas_min:g} h)"
    if ld.get("prob_modelo") is None:
        return "el modelo ya no le calcula probabilidad"
    ventaja = float((cfg or {}).get("pred_ventaja") or 8)
    tope = ld["prob_modelo"] / 100 - ventaja / 200
    mejor = ex.mejor_precio_prediccion(cod, True) if hasattr(ex, "mejor_precio_prediccion") else None
    ask = (mejor or {}).get("precio")
    if not ask:
        return "no hay vendedores ahora"
    if ask > tope:
        return f"ya no tiene ventaja: el modelo da {ld['prob_modelo']:g}% y el más barato vende a {ask:g} (máx. {tope:.3f})"
    return None


def ejecutar_prediccion(ex, o, simulado, cfg=None, mercados=None):
    """Órdenes en mercados de predicción de Hyperliquid (solo en dinero real).
    Si en la respuesta viene «_op_pago», es la venta de la moneda con la que se pagó.
    `mercados` = lista de la nube (con el modelo), para revisar las compras de la IA."""
    upd = {"id": o["id"]}
    # Único punto de paso: en simulación solo se opera contra la cartera en papel, que no
    # tiene firma (es imposible que salga una orden real).
    papel = bool(getattr(ex, "papel", False))
    if simulado and not papel:
        return {**upd, "estado": "error", "error": "En simulación solo se opera en papel: no envié nada a Hyperliquid"}, None
    simulado = simulado or papel
    if not hasattr(ex, "orden_prediccion"):
        return {**upd, "estado": "error", "error": "Tu exchange no tiene mercados de predicción"}, None
    try:
        cod, unidades, lim = int(str(o["coin"]).lstrip("#")), int(o["unidades"]), float(o["limite"])
    except (KeyError, TypeError, ValueError):
        return {**upd, "estado": "error", "error": "Orden de predicción incompleta"}, None
    compra = o["accion"] == "COMPRAR"
    if compra and o.get("origen") == "ia":
        motivo = _sigue_con_ventaja(ex, o, cod, cfg, mercados)
        if motivo:
            return {**upd, "estado": "error", "error": f"No compré: {motivo}. No se movió nada."}, None
    if not compra and not o.get("permitir_perdida"):
        # Nunca vender con pérdida, por mínima que sea (con la comisión incluida). Una
        # orden IOC se llena a tu límite o mejor: basta con que el límite no pierda.
        costo = (getattr(ex, "entradas", None) or {}).get(f"+{cod}")
        # Mercados de creadores externos (out, skew…) cobran más comisión al vender.
        escala = escala_comision(ex._info_hl, cod // 10) if costo and hasattr(ex, "_info_hl") else 0.0
        minimo = precio_minimo_venta(costo, escala)
        if minimo is None and o.get("origen") == "ia":
            _bloqueo(o.get("etiqueta") or o["coin"], "costo desconocido")
            return {**upd, "estado": "error", "error": "No vendí: no sé a cuánto compraste esta predicción y la IA no vende si puede haber pérdida."}, None
        if minimo is not None and lim < minimo:
            _bloqueo(o.get("etiqueta") or o["coin"], f"límite {lim:g} bajo el mínimo sin pérdida {minimo:g} (costo {costo:.4g})")
            return {**upd, "estado": "error", "error": f"No vendí: la compraste a {costo:.4g} y vender a {lim:g} sería con pérdida (sin pérdida desde {minimo:g}, comisión incluida). "
                                                       "Si quieres vender igual, crea la venta marcando «vender aunque sea con pérdida».", "min_venta": minimo}, None
    op_pago, nota_pago = None, None
    if compra and (o.get("pagar_con") or (cfg or {}).get("pred_pagar_con")):
        # Antes de vender tu moneda, comprueba que la predicción se puede llenar a tu límite.
        mejor = ex.mejor_precio_prediccion(cod, True) if hasattr(ex, "mejor_precio_prediccion") else None
        if mejor is not None and mejor.get("precio") and mejor["precio"] > lim:
            return {**upd, "estado": "error", "error": f"No se llenaría: el más barato vende a {mejor['precio']:g} y tu límite es {lim:g}. No vendí nada.", "mejor": mejor}, None
        op_pago, nota_pago, err = _pagar_con(ex, o, cfg, unidades * lim)
        if err:
            return {**upd, "estado": "error", "error": err}, None
        if op_pago:
            upd["_op_pago"] = op_pago
    try:
        r = ex.orden_prediccion(cod, compra, unidades, lim)
    except ErrorExchange as e:
        return {**upd, "estado": "error", "error": str(e)[:200] + (f" ({nota_pago}: esos USDC quedan libres)" if nota_pago else "")}, None
    if r["cantidad"] <= 0:
        mejor = r.get("mejor")
        if mejor and mejor.get("precio"):
            pista = (f" El más barato vendía a {mejor['precio']:g} ({mejor['unidades']:g} u.): reintenta con límite {mejor['precio']:g} o más." if compra
                     else f" El mejor comprador pagaba {mejor['precio']:g} ({mejor['unidades']:g} u.): reintenta con límite {mejor['precio']:g} o menos.")
        else:
            pista = f" No había {'vendedores' if compra else 'compradores'} en el libro: prueba más tarde."
        movido = f" {nota_pago}: esos USDC quedan libres." if nota_pago else " No se movió nada."
        return {**upd, "estado": "error", "error": f"No se llenó: nadie {'vendía' if compra else 'compraba'} a {lim:g} o mejor.{movido}{pista}", "mejor": mejor}, None
    etiqueta = o.get("etiqueta") or o["coin"]
    op = {"ts": int(time.time()), "simbolo": etiqueta, "accion": o["accion"], "cantidad": r["cantidad"], "precio": r["precio"],
          "total": round(r["total"], 2), "motivo": f"Predicción {etiqueta}", "simulada": simulado, "pnl": None, "orden": o["id"], "prediccion": o["coin"]}
    nota = "; ".join(x for x in (f"Se llenaron {r['cantidad']:g} de {unidades}" if r["cantidad"] < unidades else None, nota_pago) if x) or None
    return {**upd, "estado": "ejecutada", "nota": nota, "resultado": {"precio": r["precio"], "cantidad": r["cantidad"], "total": round(r["total"], 2), "ts": int(time.time() * 1000)}}, op


def lista_para_intentar(o, precios):
    """¿Se puede intentar ya? Una orden spot con límite espera a que el precio lo cumpla."""
    if o.get("tipo") == "prediccion" or not o.get("limite"):
        return True
    p = precios.get(o.get("simbolo"))
    if not p:
        return True  # que ejecutar() explique que no hay precio
    return p <= o["limite"] if o.get("accion") == "COMPRAR" else p >= o["limite"]


def ejecutar(ex, o, saldos, precios, costos, cfg, simulado, mercados=None):
    """Intenta ejecutar una orden aprobada. Devuelve (actualizacion, operacion|None).
    Si aún no se cumple su condición, la orden sigue «aprobada» con una nota."""
    if o.get("tipo") == "prediccion":
        return ejecutar_prediccion(ex, o, simulado, cfg, mercados)
    sym, acc = o["simbolo"], o["accion"]
    upd = {"id": o["id"]}
    precio = precios.get(sym)
    if not precio:
        return {**upd, "estado": "error", "error": f"Sin precio para {sym} en tu exchange"}, None
    lim = o.get("limite")
    if lim and ((acc == "COMPRAR" and precio > lim) or (acc == "VENDER" and precio < lim)):
        return {**upd, "estado": "aprobada", "nota": f"Esperando precio {'≤' if acc == 'COMPRAR' else '≥'} {lim} (ahora {precio:.6g})"}, None
    minimo = float(cfg.get("monto_min") or 1)
    quote = cfg["quote"].upper()
    try:
        if acc == "COMPRAR":
            libre = saldos.get(quote, 0.0)
            monto = min(float(o.get("monto") or 0), libre)
            if monto < minimo:
                return {**upd, "estado": "aprobada", "nota": f"Saldo libre insuficiente: {libre:.2f} {quote} (mínimo {minimo:g})"}, None
            r = ex.comprar(sym, monto)
            from estrategia import registrar_compra
            registrar_compra(costos, sym, saldos.get(sym, 0.0), r["cantidad"], r["precio"])
        else:
            cant = _cantidad_venta(o, saldos)
            if cant * precio < minimo:
                return {**upd, "estado": "error", "error": f"No tienes suficiente {sym} para vender (mínimo {minimo:g} {quote})"}, None
            c = costos.get(sym) or {}
            costo, minimo = c.get("costo"), None
            if cfg.get("nunca_vender_con_perdida", True) and not o.get("permitir_perdida"):
                if o.get("origen") == "ia" and (not costo or c.get("estimado")):
                    _bloqueo(f"Vender {sym}", f"costo {'estimado' if costo else 'desconocido'}")
                    return {**upd, "estado": "error", "error": f"No vendí: no sé a cuánto compraste {sym} (costo {'estimado' if costo else 'desconocido'}) y la IA no vende si puede haber pérdida."}, None
                minimo = minimo_spot(costo)
                if minimo and precio < minimo:
                    _bloqueo(f"Vender {sym}", f"P/L {(precio / costo - 1) * 100:+.2f} % (mínimo +{MARGEN_SPOT * 100:g} %)")
                    return {**upd, "estado": "aprobada", "nota": f"Sería con pérdida o sin cubrir comisión ({(precio / costo - 1) * 100:+.1f}%; sin pérdida desde {minimo:.6g}): espero o marca «vender aunque pierda»"}, None
            r = ex.vender(sym, cant, minimo)
    except ErrorExchange as e:
        return {**upd, "estado": "error", "error": str(e)[:200]}, None
    costo = (costos.get(sym) or {}).get("costo")
    op = {"ts": int(time.time()), "simbolo": sym, "accion": acc, "cantidad": r["cantidad"], "precio": r["precio"],
          "total": round(r["total"], 2), "motivo": f"Orden {'de la IA' if o.get('origen') == 'ia' else 'tuya'}: {o.get('razon') or ''}".strip(": "),
          "simulada": simulado, "pnl": round((r["precio"] / costo - 1) * 100, 2) if acc == "VENDER" and costo else None,
          "orden": o["id"], "ia": o.get("ia")}
    return {**upd, "estado": "ejecutada", "resultado": {"precio": r["precio"], "cantidad": r["cantidad"], "total": round(r["total"], 2), "ts": int(time.time() * 1000)}}, op


def de_la_ia(cfg, opiniones, saldos, precios, pendientes, notas=None):
    """Señales COMPRAR/VENDER con confianza suficiente → órdenes nuevas (sin repetir
    las que ya están pendientes). En modo auto la IA solo compra monedas de tu reparto
    y sin pasar su objetivo: si no, cada ciclo podría volver a comprar lo mismo."""
    if cfg.get("ia") == "off" or cfg.get("ordenes_ia", "proponer") == "off":
        return []
    notas = notas if notas is not None else []
    auto = cfg.get("ordenes_ia") == "auto"
    from estrategia import valorar
    total, activos = valorar(saldos, precios, cfg["quote"].upper())
    pesos = {a["simbolo"]: a["peso"] for a in activos}
    umbral = float(cfg.get("ia_conf_min") or 65)
    quote = cfg["quote"].upper()
    mn, mx = float(cfg.get("monto_min") or 5), float(cfg.get("monto_max") or 25)
    ya = {(o["simbolo"], o["accion"]) for o in pendientes}
    nuevas = []
    for sym, o in sorted(opiniones.items(), key=lambda kv: -kv[1].get("confianza", 0)):
        acc, conf = o.get("accion"), float(o.get("confianza") or 0)
        if acc not in ("COMPRAR", "VENDER") or conf < umbral or (sym, acc) in ya or sym not in precios:
            continue
        orden = {"simbolo": sym, "accion": acc, "origen": "ia", "razon": o.get("razon", ""), "confianza": int(conf),
                 "ia": {"accion": acc, "confianza": int(conf), "razon": o.get("razon", "")}}
        if acc == "COMPRAR":
            if saldos.get(quote, 0.0) < mn:
                continue
            f = (conf - umbral) / max(1.0, 100 - umbral)
            orden["monto"] = round(mn + (mx - mn) * f, 2)
            if auto:
                obj = float((cfg.get("objetivo") or {}).get(sym, 0))
                tras = pesos.get(sym, 0.0) + (100 * orden["monto"] / total if total else 100)
                if obj <= 0 or tras > obj + float(cfg.get("banda") or 0):
                    notas.append(f"IA: quería comprar {sym}, pero {'no está en tu reparto' if obj <= 0 else f'pasaría su objetivo ({tras:.1f}% de {obj:g}%)'}: no compro sola")
                    continue
        else:
            if saldos.get(sym, 0.0) * precios[sym] < mn:
                continue
            orden["pct"] = 100 if conf >= 85 else 50
        nuevas.append(orden)
        if len(nuevas) >= MAX_IA_CICLO:
            break
    return nuevas
