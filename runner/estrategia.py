"""Reglas de trading de Kumo Bot (funciones puras, fáciles de probar).

Idea: el usuario define un reparto objetivo (% por moneda). El bot compra
cuando una moneda está por debajo de su objetivo y barata (RSI bajo), y vende
cuando está por encima de su objetivo o cara (RSI alto) y hay ganancia.
Por defecto nunca vende con pérdida.
"""

CONFIG_DEFECTO = {
    "pred_ia": "proponer",          # predicciones (beta): off | proponer | auto
    "pred_monto": 11.0,             # USDC por compra de la IA (mínimo ~1 en predicciones; con menos libre compra lo que alcance)
    "pred_max_total": 30.0,         # tope: valor en predicciones + compras pendientes
    "pred_ventaja": 8.0,            # puntos de ventaja del modelo para considerar un mercado
    "modo": "simulacion",          # simulacion | real
    "pausado": False,
    "quote": "USDT",
    "objetivo": {"BTC": 40, "ETH": 30},   # el resto queda en la moneda base
    "monto_min": 5.0,
    "monto_max": 25.0,
    "rsi_compra": 35,
    "rsi_venta": 68,
    "banda": 3.0,                  # puntos % de tolerancia sobre el objetivo
    "ganancia_min": 1.5,           # % mínimo sobre costo para vender
    "stop_perdida": 0.0,           # % (0 = desactivado)
    "nunca_vender_con_perdida": True,
    "ia": "veto",                  # off | veto | confirmar
    "ia_conf_min": 65,
    "ordenes_ia": "proponer",      # off | proponer (esperan tu OK) | auto (la IA ejecuta sola)
    "orden_horas": 24,             # las órdenes pendientes caducan a las N horas
    "max_ops_ciclo": 2,
    "marco": "1h",
    "saldo_simulado": 1000.0,
}


def rsi(cierres, periodo=14):
    if len(cierres) <= periodo:
        return None
    ganancias = perdidas = 0.0
    for i in range(1, periodo + 1):
        d = cierres[i] - cierres[i - 1]
        ganancias += max(d, 0)
        perdidas += max(-d, 0)
    g, p = ganancias / periodo, perdidas / periodo
    for i in range(periodo + 1, len(cierres)):
        d = cierres[i] - cierres[i - 1]
        g = (g * (periodo - 1) + max(d, 0)) / periodo
        p = (p * (periodo - 1) + max(-d, 0)) / periodo
    if p == 0:
        return 100.0
    return round(100 - 100 / (1 + g / p), 1)


def ema(valores, periodo):
    if not valores:
        return None
    k = 2 / (periodo + 1)
    e = valores[0]
    for v in valores[1:]:
        e = v * k + e * (1 - k)
    return e


def tendencia(cierres):
    if len(cierres) < 50:
        return "neutral"
    corta, larga = ema(cierres[-50:], 12), ema(cierres[-50:], 26)
    if corta > larga * 1.002:
        return "alcista"
    if corta < larga * 0.998:
        return "bajista"
    return "neutral"


def actualizar_costos(costos, saldos, precios):
    """Asigna costo inicial (precio actual) a monedas sin historial y limpia las vendidas."""
    nuevos = dict(costos)
    for sym, cant in saldos.items():
        if sym in precios and cant * precios[sym] >= 1 and sym not in nuevos:
            nuevos[sym] = {"costo": precios[sym], "estimado": True}
    for sym in list(nuevos):
        if saldos.get(sym, 0) * precios.get(sym, 0) < 0.5 and sym in precios:
            del nuevos[sym]
    return nuevos


def registrar_compra(costos, sym, cant_previa, cantidad, precio):
    prev = costos.get(sym, {}).get("costo", precio)
    total = cant_previa + cantidad
    costo = (cant_previa * prev + cantidad * precio) / total if total > 0 else precio
    # Si lo que ya tenías tenía costo estimado, el promedio también lo es.
    costos[sym] = {"costo": costo, "estimado": bool(cant_previa > 0 and costos.get(sym, {}).get("estimado"))}


def costos_del_exchange(costos, entradas):
    """Hyperliquid informa lo que pagaste por cada moneda (entryNtl): ese costo real
    reemplaza al que Kumo estimaba o calculaba."""
    nuevos = dict(costos)
    for sym, c in (entradas or {}).items():
        if not str(sym).startswith("+") and c and c > 0:
            nuevos[sym] = {"costo": c, "estimado": False, "fuente": "exchange"}
    return nuevos


def valorar(saldos, precios, quote):
    activos, total = [], saldos.get(quote, 0.0)
    for sym, cant in saldos.items():
        if sym == quote:
            continue
        p = precios.get(sym)
        if not p:
            continue
        total += cant * p
    for sym, cant in saldos.items():
        p = 1.0 if sym == quote else precios.get(sym)
        if not p:
            continue
        valor = cant * p
        if valor < 0.01:
            continue
        activos.append({"simbolo": sym, "cantidad": cant, "precio": p, "valor": round(valor, 2), "peso": round(100 * valor / total, 2) if total else 0})
    activos.sort(key=lambda a: -a["valor"])
    return round(total, 2), activos


def proponer(cfg, saldos, precios, indicadores, costos):
    """Devuelve (propuestas, descartes) sin consultar a la IA."""
    quote = cfg["quote"]
    total, activos = valorar(saldos, precios, quote)
    pesos = {a["simbolo"]: a["peso"] for a in activos}
    libre = saldos.get(quote, 0.0)
    propuestas, notas = [], []
    if total <= 0:
        return [], ["Cartera vacía o sin precios"]
    simbolos = sorted(set(cfg["objetivo"]) | {s for s in costos if s != quote})
    for sym in simbolos:
        precio = precios.get(sym)
        ind = indicadores.get(sym) or {}
        r = ind.get("rsi")
        if not precio or r is None:
            notas.append(f"{sym}: sin datos de mercado")
            continue
        objetivo = float(cfg["objetivo"].get(sym, 0))
        peso = pesos.get(sym, 0.0)
        cant = saldos.get(sym, 0.0)
        costo = costos.get(sym, {}).get("costo")
        pnl = (precio / costo - 1) * 100 if costo else None
        base = {"simbolo": sym, "precio": precio, "rsi": r, "peso": peso, "objetivo": objetivo, "pnl": None if pnl is None else round(pnl, 2), "tendencia": ind.get("tendencia")}

        # --- Venta
        if cant * precio >= cfg["monto_min"]:
            stop = cfg["stop_perdida"] > 0 and pnl is not None and pnl <= -cfg["stop_perdida"] and not cfg["nunca_vender_con_perdida"]
            sobrepeso = peso > objetivo + cfg["banda"]
            caro = r >= cfg["rsi_venta"]
            # Sin pérdida «por mínima que sea»: la ganancia exigida cubre al menos la comisión
            # (0.5 %), y con costo estimado (no sé a cuánto la compraste) no se vende sola.
            estimado = bool(costos.get(sym, {}).get("estimado"))
            gan_min = max(float(cfg["ganancia_min"]), 0.5) if cfg["nunca_vender_con_perdida"] else float(cfg["ganancia_min"])
            con_ganancia = pnl is not None and pnl >= gan_min and not (estimado and cfg["nunca_vender_con_perdida"])
            if stop:
                propuestas.append({**base, "accion": "VENDER", "cantidad": cant, "motivo": f"Stop de pérdida ({pnl:.1f}%)", "stop": True})
                continue
            if (sobrepeso or caro) and con_ganancia:
                exceso = (peso - objetivo) / 100 * total if sobrepeso else cfg["monto_max"]
                monto = min(max(exceso, cfg["monto_min"]), cfg["monto_max"], cant * precio)
                motivo = ("sobre su objetivo" if sobrepeso else f"RSI alto {r}") + f", ganancia {pnl:.1f}%"
                propuestas.append({**base, "accion": "VENDER", "cantidad": monto / precio, "monto": round(monto, 2), "motivo": motivo})
                continue
            if (sobrepeso or caro) and not con_ganancia:
                if estimado and cfg["nunca_vender_con_perdida"] and pnl is not None and pnl >= gan_min:
                    notas.append(f"{sym}: vendería, pero no sé a cuánto la compraste (costo estimado): no vendo sola para no arriesgar una pérdida")
                else:
                    notas.append(f"{sym}: vendería, pero no hay ganancia suficiente ({'—' if pnl is None else f'{pnl:.1f}%'})")

        # --- Compra
        if objetivo > 0 and peso < objetivo - cfg["banda"] and r <= cfg["rsi_compra"]:
            falta = (objetivo - peso) / 100 * total
            monto = min(falta, cfg["monto_max"], libre)
            if monto >= cfg["monto_min"]:
                propuestas.append({**base, "accion": "COMPRAR", "monto": round(monto, 2), "motivo": f"bajo su objetivo ({peso:.1f}% de {objetivo:.0f}%) y RSI {r}"})
                libre -= monto
            else:
                notas.append(f"{sym}: quiere comprar pero no hay {cfg['quote']} libre suficiente")
    # Primero ventas (liberan saldo), luego compras con mayor déficit.
    propuestas.sort(key=lambda p: (p["accion"] != "VENDER", -(p["objetivo"] - p["peso"])))
    return propuestas, notas


def aplicar_ia(cfg, propuestas, opiniones):
    """opiniones: {SIMBOLO: {accion, confianza, razon}}. Devuelve (aprobadas, bloqueadas)."""
    if cfg["ia"] == "off":
        return propuestas, []
    aprobadas, bloqueadas = [], []
    for p in propuestas:
        o = opiniones.get(p["simbolo"]) or {}
        accion, conf = str(o.get("accion", "")).upper(), float(o.get("confianza", 0) or 0)
        p = {**p, "ia": o or None}
        if p.get("stop"):
            aprobadas.append(p)
            continue
        if cfg["ia"] == "confirmar":
            ok = accion == p["accion"] and conf >= cfg["ia_conf_min"]
        else:  # veto: la IA bloquea solo si opina lo contrario con confianza
            contraria = {"COMPRAR": "VENDER", "VENDER": "COMPRAR"}[p["accion"]]
            ok = not (accion in (contraria, "ESPERAR") and conf >= cfg["ia_conf_min"])
            if not o:
                ok = True
        (aprobadas if ok else bloqueadas).append(p)
    return aprobadas, bloqueadas
