"""Feedback en movimiento (beta): qué decidió Kumo y cómo le fue.

En cada ciclo el runner anota las predicciones que tienes abiertas y, cuando una se
cierra (la vendiste o venció), guarda el resultado junto a la decisión que la abrió:
confianza, categoría y qué votó cada IA. Con eso arma un resumen de las últimas 24 h
y de 7 días que:

- se le pasa a las IAs en cada decisión («tu historial reciente»),
- se le pasa al supervisor de las 6 h, y
- puede hacer al ejecutor MÁS exigente (nunca menos): pide más confianza en una
  categoría que viene perdiendo, o en todo si hay una racha de pérdidas.

No gasta consultas de IA: son cuentas. El registro viaja dentro del estado del runner
que la nube ya guarda en cada ciclo (ninguna escritura extra).
"""
import time

MAX_CERRADAS = 60
DIAS = 30
HORA = 3_600_000
DIA = 24 * HORA

# Ajustes del ejecutor: solo con muestra suficiente y solo para pedir más.
MIN_MUESTRA = 4          # resultados en 7 días para juzgar una categoría
TASA_MALA = 0.4          # menos de 40 % ganadas…
EXTRA_CATEGORIA = 10     # …y en negativo: +10 de confianza en esa categoría
RACHA = 3                # 3 pérdidas seguidas en 24 h
EXTRA_RACHA = 10         # +10 de confianza en todo y 1 compra por ciclo


def _cod(coin):
    return int(str(coin).lstrip("+#"))


def _decision(coin, ordenes):
    """La compra ejecutada más reciente de ese lado (las órdenes vienen de la nube)."""
    for o in sorted(ordenes or [], key=lambda x: -(x.get("ts_mod") or x.get("ts") or 0)):
        if o.get("tipo") == "prediccion" and o.get("accion") == "COMPRAR" and o.get("estado") == "ejecutada" and str(o.get("coin")) == f"#{_cod(coin)}":
            ia = o.get("ia") or {}
            return {"origen": o.get("origen") or "tu", "confianza": o.get("confianza"), "categoria": o.get("categoria"),
                    "votos": [{"modelo": v.get("modelo"), "accion": v.get("accion")} for v in (ia.get("votos") or [])][:5] or None}
    return {"origen": "tu", "confianza": None, "categoria": None, "votos": None}


def _acierto(a, precio_final):
    """True/False si se puede saber quién ganó al vencer; None si no.
    Dos pistas: el último precio que vimos del lado (≥ 0,90 o ≤ 0,10 poco antes de
    vencer) y, en mercados «≥ objetivo», el precio del subyacente al vencer. Si se
    contradicen o quedan muy justas, no se cuenta."""
    por_precio = None
    if a.get("precio") is not None and a.get("vence") and a.get("visto", 0) >= a["vence"] - 45 * 60_000:
        por_precio = True if a["precio"] >= 0.9 else False if a["precio"] <= 0.1 else None
    por_vela = None
    if precio_final and a.get("subyacente") and a.get("objetivo") and a.get("vence"):
        try:
            fin = precio_final(a["subyacente"], a["vence"])
        except Exception:  # sin red: se decide con la otra pista
            fin = None
        if fin and abs(fin / a["objetivo"] - 1) >= 0.001:
            por_vela = (fin >= a["objetivo"]) == (a.get("lado") == "Sí")
    if por_precio is not None and por_vela is not None and por_precio != por_vela:
        return None
    return por_precio if por_precio is not None else por_vela


def actualizar(previo, preds, ordenes=None, ops=None, categorias=None, precio_final=None, ahora=None):
    """Devuelve el registro nuevo: {"abiertas": {coin: …}, "cerradas": [...], "ts"}.
    `preds` = posiciones de este ciclo; `ops` = operaciones ejecutadas en este ciclo;
    `categorias` = {mercado: categoría}; `precio_final(subyacente, ms)` → precio o None."""
    ahora = ahora or int(time.time() * 1000)
    previo = previo if isinstance(previo, dict) else {}
    abiertas = {k: dict(v) for k, v in (previo.get("abiertas") or {}).items()}
    cerradas = [c for c in (previo.get("cerradas") or []) if ahora - (c.get("ts") or 0) < DIAS * DIA]

    # Ventas de este ciclo: lo cobrado se suma a su posición; las de spot son un resultado.
    for op in ops or []:
        if op.get("accion") != "VENDER" or op.get("simulada"):
            continue
        if op.get("prediccion"):
            a = abiertas.get(f"+{_cod(op['prediccion'])}")
            if a:
                a["cobrado"] = round((a.get("cobrado") or 0) + float(op.get("total") or 0), 4)
                a["cantidad"] = max(0.0, (a.get("cantidad") or 0) - float(op.get("cantidad") or 0))
                a["vendio"] = True
        elif op.get("pnl") is not None:
            pct = float(op["pnl"])
            usdc = round(float(op.get("total") or 0) * pct / (100 + pct), 2) if pct > -100 else None
            cerradas.insert(0, {"ts": ahora, "tipo": "spot", "etiqueta": op.get("simbolo"), "categoria": "spot", "como": "vendida",
                                "resultado": "ganada" if pct > 0 else "perdida", "pnl": usdc, "pnl_pct": round(pct, 2), "origen": "ia" if op.get("orden") else "regla"})

    vistas = set()
    for p in preds or []:
        coin = f"+{_cod(p['coin'])}"
        cant, costo = float(p.get("cantidad") or 0), p.get("costo")
        if cant < 1:
            continue
        vistas.add(coin)
        a = abiertas.get(coin)
        if a is None:
            d = _decision(coin, ordenes)
            a = abiertas[coin] = {"ts": ahora, "etiqueta": f"{p.get('pregunta')} · {p.get('lado_nombre')}"[:90], "lado": p.get("lado_nombre"),
                                  "cantidad": 0.0, "invertido": 0.0 if costo else None, "cobrado": 0.0, **d}
        # Compraste más: lo invertido sube por la diferencia de costo total.
        if cant > (a.get("cantidad") or 0) and costo and a.get("invertido") is not None:
            a["invertido"] = round(a["invertido"] + costo * cant - (a.get("costo") or 0) * (a.get("cantidad") or 0), 4)
        a.update({"cantidad": cant, "costo": costo, "precio": p.get("precio"), "visto": ahora, "vence": p.get("vence"),
                  "subyacente": p.get("subyacente"), "objetivo": p.get("objetivo")})
        if not a.get("categoria"):
            a["categoria"] = (categorias or {}).get(_cod(coin) // 10) or a.get("categoria")

    for coin in [c for c in abiertas if c not in vistas]:
        a = abiertas[coin]
        vencio = bool(a.get("vence") and a["vence"] <= ahora)
        if not vencio and not a.get("vendio") and ahora - (a.get("visto") or ahora) < 2 * HORA:
            continue  # pudo faltar un dato en este ciclo: se espera antes de darla por cerrada
        del abiertas[coin]
        item = {"ts": ahora, "tipo": "prediccion", "etiqueta": a.get("etiqueta"), "categoria": a.get("categoria") or "otro", "origen": a.get("origen"),
                "confianza": a.get("confianza"), "votos": a.get("votos"), "como": "vendida" if a.get("vendio") and (not vencio or (a.get("cantidad") or 0) < 1) else "vencida" if vencio else "cerrada"}
        cobrado = a.get("cobrado") or 0
        if item["como"] == "vencida" and (a.get("cantidad") or 0) >= 1:
            gano = _acierto(a, precio_final)
            if gano is None:
                cerradas.insert(0, {**item, "resultado": "sin_confirmar", "pnl": None})
                continue
            cobrado += a["cantidad"] if gano else 0
        elif item["como"] == "cerrada":
            # Desapareció sin vencer y sin una venta de Kumo (la vendiste fuera): no se sabe a cuánto.
            cerradas.insert(0, {**item, "resultado": "sin_confirmar", "pnl": None})
            continue
        pnl = round(cobrado - a["invertido"], 2) if a.get("invertido") else None
        if pnl is None:
            resultado = "ganada" if item["como"] == "vencida" and cobrado > 0 else "perdida" if item["como"] == "vencida" else "sin_confirmar"
        else:
            resultado = "ganada" if pnl > 0 else "perdida" if pnl < 0 else "empate"
        cerradas.insert(0, {**item, "resultado": resultado, "pnl": pnl,
                            "pnl_pct": round(pnl / a["invertido"] * 100, 1) if pnl is not None else None})
    return {"ts": ahora, "abiertas": abiertas, "cerradas": cerradas[:MAX_CERRADAS]}


def _cuenta(items):
    g = sum(1 for c in items if c["resultado"] == "ganada")
    p = sum(1 for c in items if c["resultado"] == "perdida")
    return {"n": g + p, "ganadas": g, "perdidas": p, "pnl": round(sum(c.get("pnl") or 0 for c in items), 2)}


def _tramo(conf):
    return None if conf is None else "85+" if conf >= 85 else "70-84" if conf >= 70 else "<70"


def resumir(registro, ahora=None):
    """Cuentas de 24 h y 7 días, lecciones en palabras y ajustes para el ejecutor."""
    ahora = ahora or int(time.time() * 1000)
    todas = [c for c in (registro or {}).get("cerradas") or [] if c.get("resultado") in ("ganada", "perdida")]
    preds = [c for c in todas if c.get("tipo") == "prediccion"]
    d1 = [c for c in preds if ahora - c["ts"] < DIA]
    d7 = [c for c in preds if ahora - c["ts"] < 7 * DIA]
    por_cat, por_conf, por_modelo = {}, {}, {}
    for c in d7:
        por_cat.setdefault(c.get("categoria") or "otro", []).append(c)
        if c.get("origen") == "ia" and _tramo(c.get("confianza")):
            por_conf.setdefault(_tramo(c["confianza"]), []).append(c)
        for v in c.get("votos") or []:
            # Acertó quien votó comprar y se ganó, o quien votó no entrar y se perdió.
            m = por_modelo.setdefault(str(v.get("modelo")), {"votos": 0, "aciertos": 0})
            m["votos"] += 1
            m["aciertos"] += int((v.get("accion") in ("COMPRAR", "COMPRAR MÁS")) == (c["resultado"] == "ganada"))
    out = {"ts": ahora, "hoy": _cuenta(d1), "semana": _cuenta(d7), "spot_semana": _cuenta([c for c in todas if c.get("tipo") == "spot" and ahora - c["ts"] < 7 * DIA]),
           "categorias": {k: _cuenta(v) for k, v in por_cat.items()}, "confianza": {k: _cuenta(v) for k, v in por_conf.items()}, "modelos": por_modelo,
           "sin_confirmar": sum(1 for c in (registro or {}).get("cerradas") or [] if c.get("resultado") == "sin_confirmar" and ahora - c["ts"] < 7 * DIA),
           "abiertas": len((registro or {}).get("abiertas") or {})}

    lecciones, ajustes = [], {"conf_extra": {}, "conf_extra_todo": 0, "max_ciclo": None}
    for cat, c in sorted(out["categorias"].items()):
        if c["n"] >= MIN_MUESTRA and c["ganadas"] / c["n"] < TASA_MALA and c["pnl"] < 0:
            ajustes["conf_extra"][cat] = EXTRA_CATEGORIA
            lecciones.append(f"{cat}: {c['ganadas']} de {c['n']} ganadas en 7 días ({c['pnl']:+.2f} USDC). Se exige +{EXTRA_CATEGORIA} de confianza ahí.")
        elif c["n"] >= MIN_MUESTRA and c["ganadas"] / c["n"] >= 0.6 and c["pnl"] > 0:
            lecciones.append(f"{cat}: {c['ganadas']} de {c['n']} ganadas en 7 días ({c['pnl']:+.2f} USDC). Va bien.")
    racha = 0
    for c in d1:  # de la más reciente hacia atrás
        if c["resultado"] != "perdida":
            break
        racha += 1
    if racha >= RACHA:
        ajustes.update(conf_extra_todo=EXTRA_RACHA, max_ciclo=1)
        lecciones.append(f"Racha de {racha} pérdidas seguidas en 24 h. Se exige +{EXTRA_RACHA} de confianza en todo y 1 compra por ciclo.")
    alta, baja = out["confianza"].get("85+"), out["confianza"].get("<70")
    if alta and alta["n"] >= MIN_MUESTRA and alta["ganadas"] / alta["n"] < 0.5:
        lecciones.append(f"Con confianza de 85 o más solo ganó {alta['ganadas']} de {alta['n']}: la IA está siendo demasiado segura.")
    if baja and baja["n"] >= MIN_MUESTRA and baja["ganadas"] / baja["n"] < TASA_MALA:
        lecciones.append(f"Con confianza menor de 70 ganó {baja['ganadas']} de {baja['n']}: conviene subir la confianza mínima.")
    out.update(lecciones=lecciones, ajustes=ajustes)
    return out


def para_ia(resumen):
    """Texto corto para el pedido de la IA, o None si aún no hay resultados."""
    if not resumen or not resumen["semana"]["n"]:
        return None
    h, s = resumen["hoy"], resumen["semana"]
    t = (f"Resultados reales de este bot en predicciones: últimas 24 h {h['ganadas']} ganadas y {h['perdidas']} perdidas ({h['pnl']:+.2f} USDC); "
         f"7 días {s['ganadas']} ganadas y {s['perdidas']} perdidas ({s['pnl']:+.2f} USDC).")
    cats = [f"{k} {c['ganadas']}/{c['n']}" for k, c in sorted(resumen["categorias"].items()) if c["n"]]
    if cats:
        t += " Ganadas por categoría (7 días): " + ", ".join(cats) + "."
    conf = [f"{k}: {c['ganadas']}/{c['n']}" for k, c in sorted(resumen["confianza"].items()) if c["n"]]
    if conf:
        t += " Ganadas según la confianza que diste: " + ", ".join(conf) + "."
    if resumen["lecciones"]:
        t += " Lecciones: " + " ".join(resumen["lecciones"])
    return t + " Tenlo en cuenta: si un tipo de apuesta viene perdiendo, exige más antes de repetirla y ajusta tu confianza a lo que de verdad acertaste."


def conf_extra(resumen, categoria):
    """Puntos de confianza de más que pide el ejecutor para esa categoría."""
    a = (resumen or {}).get("ajustes") or {}
    return max(a.get("conf_extra_todo") or 0, (a.get("conf_extra") or {}).get(categoria) or 0)
